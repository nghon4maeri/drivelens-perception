"""`list_videos`, `upload_video`, `prepare_video`, `queue_prepare`, `get_frame`, `get_video_file` (CONTRACTS §11.2, quyết định #35, #37, #49, #56)."""

from __future__ import annotations

import mimetypes
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field, RootModel

from ..service import App, clip_prepare
from ..service import video as svc
from .base import Command, command


class PrepareStatus(BaseModel):
    """Job P1 nền (#56): stage = field đang tính ("frames" lúc đọc video), done / total = số unit."""

    state: str            # queued | running | done | error
    stage: str
    done: int
    total: int
    started_at: str | None = None
    error: str | None = None
    job_id: str | None = None
    result_key: str | None = None


class VideoDTO(BaseModel):
    """architecture §5.3."""

    id: str
    name: str
    duration_s: float
    n_sessions: int   # số phiên có video trong pool
    prepared: bool
    media_url: str    # file video để tua (route media, #49)
    thumb_url: str    # ảnh frame giữa video, cạnh dài 320 px (thẻ ở màn Kho video, #57)
    width: int = 0    # thông tin file (#59)
    height: int = 0
    fps: float = 0.0
    size_bytes: int = 0
    added_at: str | None = None   # thời điểm file vào kho (mtime)
    prepare: PrepareStatus | None = None   # DINO+pHash: cloud durable; local RAM; None khi chưa có job
    clip_prepared: bool = False
    clip_prepare: PrepareStatus | None = None


def _dto(app: App, video, prepared: bool, n_sessions: int) -> VideoDTO:
    if app.storage_backend == 's3':
        from ..service.cloud_embedding import get_status
        get_status(app,video.id)
        get_status(app,video.id,feature='dino')
    return VideoDTO(id=video.id, name=video.meta.get("name", video.id), duration_s=video.duration_s,
                    n_sessions=n_sessions, prepared=prepared, media_url=f"/media/videos/{video.id}",
                    thumb_url=f"/api/v1/videos/{video.id}/frames/{int(video.duration_s * video.fps / 2)}?max_side=320",
                    prepare=svc.prepare_status(app, video.id), width=video.meta.get("width", 0), height=video.meta.get("height", 0),
                    clip_prepared=clip_prepare.prepared(app, video), clip_prepare=app.clip_prepare_jobs.get(video.id),
                    fps=video.fps, size_bytes=video.meta.get("size_bytes", 0),
                    added_at=datetime.fromtimestamp(video.meta["mtime_ns"] / 1e9, UTC).isoformat() if "mtime_ns" in video.meta else None)


class Empty(BaseModel):
    pass


class VideoList(RootModel[list[VideoDTO]]):
    pass


@command
class ListVideos(Command):
    name = "list_videos"
    description = "Liệt kê video trong catalog, kèm đã chạy P1 (tách frame + feature) chưa."
    Input, Output = Empty, VideoList

    def run(self, inp: Empty, app: App) -> VideoList:
        counts = svc.n_sessions(app)
        return VideoList([_dto(app, v, ok, counts[v.id]) for v, ok in svc.list_videos(app)])


class UploadIn(BaseModel):
    filename: str
    path: str = Field(description="file tạm trên máy chủ (API ghi multipart ra đây)")


@command
class UploadVideo(Command):
    name = "upload_video"
    description = "Lưu video vào catalog/S3; không chạy embedding. Cùng nội dung trả video cũ."
    Input, Output = UploadIn, VideoDTO
    mutates = needs_confirm = True

    def run(self, inp: UploadIn, app: App) -> VideoDTO:
        video = svc.upload_video(app, Path(inp.path), inp.filename)   # cùng nội dung → video cũ, có thể đã có phiên
        return _dto(app, video, svc.is_prepared(app, video), svc.n_sessions(app)[video.id])


class VideoIdIn(BaseModel):
    video_id: str


class PrepareOut(BaseModel):
    video_id: str
    n_units: int


@command
class PrepareVideo(Command):
    name = "prepare_video"
    description = "Chạy P1 cho một video: tách frame, tính và cache embedding / pHash / PCA. Chậm lần đầu, lần sau đọc cache."
    Input, Output = VideoIdIn, PrepareOut
    mutates = True

    def run(self, inp: VideoIdIn, app: App) -> PrepareOut:
        return PrepareOut(video_id=inp.video_id, n_units=svc.prepare_video(app, inp.video_id))


@command
class QueuePrepare(Command):
    name = "queue_prepare"
    description = "Chạy DINO+pHash khi yêu cầu: S3 dùng Kaggle, local dùng P1; tiến độ ở VideoDTO.prepare."
    Input, Output = VideoIdIn, VideoDTO
    mutates = True

    def run(self, inp: VideoIdIn, app: App) -> VideoDTO:
        video = svc.get_video(app, inp.video_id)
        svc.queue_prepare(app, video)
        return _dto(app, video, svc.is_prepared(app, video), svc.n_sessions(app)[video.id])


@command
class QueuePrepareClip(Command):
    name = "queue_prepare_clip"
    description = "Chuẩn bị CLIP ảnh nền (Kaggle mặc định), cache đúng checkpoint/revision; độc lập với P1 cũ."
    Input, Output = VideoIdIn, VideoDTO
    mutates = True

    def run(self, inp: VideoIdIn, app: App) -> VideoDTO:
        video = svc.get_video(app, inp.video_id)
        clip_prepare.queue_prepare(app, inp.video_id)
        return _dto(app, video, svc.is_prepared(app, video), svc.n_sessions(app)[video.id])


class FrameIn(BaseModel):
    video_id: str
    idx: int = Field(ge=0, description="chỉ số frame gốc trong video (Frame.idx)")
    max_side: int | None = Field(None, gt=0, description="thumbnail: cạnh dài tối đa (px)")


class FrameOut(BaseModel):
    media_type: str = "image/jpeg"
    data: bytes


@command
class GetFrame(Command):
    name = "get_frame"
    description = "Ảnh JPEG của một frame (thumbnail nếu có max_side), decode từ video theo yêu cầu."
    Input, Output = FrameIn, FrameOut

    def run(self, inp: FrameIn, app: App) -> FrameOut:
        return FrameOut(data=svc.frame_jpeg(app, inp.video_id, inp.idx, inp.max_side))


class VideoFileOut(BaseModel):
    path: str
    media_type: str


@command
class GetVideoFile(Command):
    name = "get_video_file"
    description = "Đường dẫn file video trên máy chủ (cho route media tua video); client chỉ thấy URL /media/videos/{video_id}."
    Input, Output = VideoIdIn, VideoFileOut

    def run(self, inp: VideoIdIn, app: App) -> VideoFileOut:
        path = Path(svc.get_video(app, inp.video_id).uri)
        return VideoFileOut(path=str(path), media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream")
