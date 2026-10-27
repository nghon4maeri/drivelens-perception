"""Video trong catalog: liệt kê, upload, chạy P1, lấy ảnh một frame (CONTRACTS §11.2, architecture §5.1).

Upload là cửa nhận dữ liệu từ ngoài: giới hạn dung lượng, `video_id` = hash nội dung (tải lại cùng file không tạo bản trùng),
mở thử bằng decoder. Upload chỉ lưu; P1 chạy khi gọi `queue_prepare` (#76).
"""

from __future__ import annotations

import io
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from ..core.types import VideoRef
from ..pipeline import prepare as p1
from . import App, ServiceError


def list_videos(app: App) -> list[tuple[VideoRef, bool]]:
    """(video, đã chạy P1 chưa)."""
    return [(v, is_prepared(app, v)) for v in p1.catalog(app.spec).values()]


def n_sessions(app: App) -> Counter:
    """video_id → số phiên có video đó trong pool."""
    return Counter(v for row in app.db.list_sessions() for v in row["spec"]["pool"]["video_ids"])


def is_prepared(app: App, video: VideoRef) -> bool:
    return p1.is_prepared(video, app.spec, app.fields)


def get_video(app: App, video_id: str) -> VideoRef:
    try:
        return p1.catalog(app.spec)[video_id]
    except KeyError:
        raise ServiceError("video_not_found", f"không có video {video_id!r}", 404) from None


def prepare_video(app: App, video_id: str) -> int:
    """Chạy P1 cho một video; trả số unit."""
    if app.storage_backend == 's3':
        raise ServiceError('prepare_requires_job','Video cloud cần gọi queue_prepare để chạy DINO trên Kaggle.',409)
    return len(p1.prepare([get_video(app, video_id)], app.spec, app.fields).units)


def prepare_status(app: App, video_id: str) -> dict | None:
    """Trạng thái job P1 nền của video (None nếu chưa từng xếp job trong lần chạy BE này)."""
    return app.prepare_jobs.get(video_id)


def is_preparing(app: App, video_id: str) -> bool:
    job = app.prepare_jobs.get(video_id)
    return job is not None and job["state"] in ("queued", "running")


def queue_prepare(app: App, video: VideoRef) -> None:
    """Xếp P1 nền cho video (bỏ qua nếu đã chuẩn bị hoặc đang có job)."""
    if app.storage_backend == 's3':
        from .cloud_embedding import queue
        queue(app,video.id,feature='dino')
        return
    if is_preparing(app, video.id) or is_prepared(app, video):
        return
    app.prepare_jobs[video.id] = {"state": "queued", "stage": "", "done": 0, "total": 0, "started_at": None, "error": None}
    app.prepare_queue.put(lambda: _run_prepare(app, video))


def _run_prepare(app: App, video: VideoRef) -> None:
    job = app.prepare_jobs[video.id]
    job.update(state="running", stage="frames", started_at=datetime.now(UTC).isoformat())

    def on_progress(field: str, done: int, total: int) -> None:
        job.update(stage=field, done=done, total=total)

    try:
        p1.prepare([video], app.spec, app.fields, on_progress=on_progress)
        job.update(state="done")
    except Exception as e:   # job nền: không có request để trả lỗi → ghi vào trạng thái cho UI
        job.update(state="error", error=f"{type(e).__name__}: {e}")


def upload_video(app: App, src: Path, filename: str) -> VideoRef:
    # local-only: mọi video chung một thư mục, chỉ kiểm tra cơ bản (LOCAL_SINGLE_USER_LIMITS #6)
    size = src.stat().st_size
    if size > app.max_upload_bytes:
        raise ServiceError("file_too_large", f"file {size >> 20} MB vượt giới hạn {app.max_upload_bytes >> 20} MB", 400)
    name = Path(filename).name or "video.mp4"
    before = p1.catalog(app.spec)
    dest = app.videos_dir / name
    if dest.exists():
        dest = app.videos_dir / f"{src.stat().st_mtime_ns}_{name}"
    app.videos_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    video = next((v for v in p1.iter_videos(app.spec) if Path(v.uri) == dest.resolve()), None)   # catalog theo id che mất bản trùng
    if video is not None and video.id in before:
        dest.unlink()
        if app.cloud_storage:
            app.cloud_storage.upload_raw(Path(before[video.id].uri), 'videos')
        return before[video.id]
    try:
        if video is None:
            raise ValueError("định dạng không hỗ trợ")
        p1.read_frame(video, 0, app.spec)
    except Exception as e:
        dest.unlink()
        raise ServiceError("invalid_video", f"{name}: không mở được bằng decoder ({e})", 400) from e
    try:
        if app.cloud_storage:
            ref = app.cloud_storage.upload_raw(dest, 'videos')
            app.cloud_storage.put_json(f"videos/{ref['sha256']}.json", {**ref, 'video_id':video.id, 'meta':video.meta})
    except Exception:
        # read_frame opened a shared PyAV handle; release it before rollback on Windows.
        from ..components.decoders.pyav import PyAV
        with PyAV._open_lock:
            handle = PyAV._handles.pop(video.uri,None)
        if handle:
            with handle.lock:
                handle.container.close()
        dest.unlink(missing_ok=True)
        raise
    return video


def frame_jpeg(app: App, video_id: str, idx: int, max_side: int | None = None) -> bytes:
    video = get_video(app, video_id)
    try:
        image = Image.fromarray(p1.read_frame(video, idx, app.spec))
    except IndexError:
        raise ServiceError("frame_not_found", f"{video_id}: không có frame {idx}", 404) from None
    if max_side:
        image.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=85)
    return buf.getvalue()
