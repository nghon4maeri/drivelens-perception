"""Handler worker của DEP4: `VALIDATE_UPLOAD`, `RENDER_MEDIA` (CONTRACTS §21.7, §21.12; runtime spec §6).

Chạy trong tiến trình worker (profile nặng: PyAV/Pillow). Dùng đúng `local_folder._probe` và decoder `pyav` của core để
`core_source_id`, `n_units`, chỉ số frame khớp bản local (§21.2). Mỗi attempt một thư mục làm việc riêng, xóa khi xong;
raw nguồn cache theo SHA-256 (nội dung bất biến). Lỗi nghiệp vụ → `ServiceError` mã an toàn (dispatcher ghi FAILED).
`VALIDATE_UPLOAD` tách luôn ảnh lưu sẵn FRAME/THUMBNAIL theo lô (`frame_writer`, §21.20) trừ khi nguồn đích đã có bộ READY;
`PREPARE_FRAMES` tách ảnh cho nguồn cũ từ raw cache.
Dùng: `make_handlers(storage, db, workdir)` rồi `dispatcher.register_handler(kind, fn)`.
"""

from __future__ import annotations

import hashlib
import io
import shutil
import threading
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from ..errors import ServiceError
from ..store import pilot_frames, pilot_objects
from ..store.pilot_db import DEFAULT_SAMPLE_SPEC, PilotDB
from ..store.pilot_runtime import AttemptLease, PilotRuntime, Progress
from ..store.pilot_storage import PilotStorage
from ..timing import emit, timing, traced_handler
from .frame_writer import materialize
from .session_context import cached

RAW_CAP = 8 << 30   # trần tải raw cho media (raw đã được kiểm giới hạn lúc upload/import)
EXT = {"mp4": "mp4", "mov": "mp4", "matroska": "mkv", "webm": "webm", "avi": "avi"}


def _decoder():
    from ..components.decoders.pyav import PyAV

    return PyAV(DEFAULT_SAMPLE_SPEC["decoder"]["params"])


def _probe(path: Path):
    from ..components.sources.local_folder import _probe as probe

    stat = path.stat()
    try:
        return probe(str(path), stat.st_size, stat.st_mtime_ns)
    except Exception:
        raise ServiceError("upload_invalid", "không đọc được video (định dạng hoặc file hỏng)", 422) from None


def _ext(path: Path) -> str:
    import av

    with av.open(str(path)) as c:
        names = c.format.name.split(",")
    return next((EXT[n] for n in names if n in EXT), "mp4")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_handlers(storage: PilotStorage, db: PilotDB, workdir: Path) -> dict[str, Any]:
    workdir.mkdir(parents=True, exist_ok=True)
    cache = workdir / "raw-cache"
    cache.mkdir(exist_ok=True)
    runtime = PilotRuntime(db)

    @traced_handler
    def validate_upload(lease: AttemptLease, stop: threading.Event) -> dict:
        p, b = lease.payload, lease.payload["bounds"]
        work = workdir / str(lease.attempt_id)
        work.mkdir(parents=True, exist_ok=True)
        try:
            runtime.update_progress(lease, Progress(sequence=1, phase="DOWNLOADING", done=0, total=0))
            if p.get("staging_source") == "VPS_DISK":
                from ..store.pilot_vps_disk import VPSDiskUpload
                disk = VPSDiskUpload(UUID(p["upload_id"]))
                disk.seal(p["declared_bytes"])
                staging = disk.part_path
                if not staging.exists():
                    raise ServiceError("upload_invalid", "không tìm thấy file trên đĩa VPS", 422)
                h = hashlib.sha256()
                size = 0
                with staging.open("rb") as f:
                    while chunk := f.read(1024 * 1024):
                        h.update(chunk)
                        size += len(chunk)
                        if size > min(p["declared_bytes"], b["max_video_bytes"]):
                            raise ServiceError("upload_invalid", "file vượt giới hạn", 422)
                sha = h.hexdigest()
            else:
                staging = work / "staging.bin"
                with timing("worker.s3.download_hash", bytes=p["declared_bytes"]):
                    sha, size = storage.download(p["staging_key"], staging, min(p["declared_bytes"], b["max_video_bytes"]))
            emit("worker.downloaded", bytes=size)
            if stop.is_set():
                return {}
            runtime.update_progress(lease, Progress(sequence=2, phase="PROBING", done=0, total=0))
            with timing("worker.probe_core_hash"):
                video = _probe(staging)
            if video.id != sha[:16]:
                raise ServiceError("result_invalid", "id nội dung không khớp SHA-256", 422)
            meta = video.meta
            duration_ms = round(video.duration_s * 1000)
            problems = [m for ok, m in (
                (duration_ms <= b["max_duration_ms"], "video quá dài"),
                (meta["width"] <= b["max_width"] and meta["height"] <= b["max_height"], "độ phân giải vượt giới hạn"),
                (meta["width"] * meta["height"] * meta["n_frames"] <= b["max_decoded_pixels"], "tổng số pixel giải mã vượt giới hạn"),
            ) if not ok]
            if problems:
                raise ServiceError("upload_invalid", "; ".join(problems), 422)
            with timing("worker.unit_grid"):
                n_units = len(_decoder().frames(video))
            owner = UUID(p["owner_id"])
            target_scope = p.get("target_scope") or "PRIVATE"
            with db.backend() as cur:   # cùng bytes đã có trong kho đích → không ghi canonical thứ hai
                if target_scope == "SYSTEM":
                    cur.execute("SELECT id FROM rav_pilot_source_asset WHERE core_source_id = %s AND scope = 'SYSTEM' "
                                "AND availability = 'READY'", (video.id,))
                else:
                    cur.execute("SELECT id FROM rav_pilot_source_asset WHERE owner_id = %s AND core_source_id = %s AND scope = 'PRIVATE' "
                                "AND availability = 'READY'", (owner, video.id))
                existing = cur.fetchone()
                duplicate = existing is not None
                has_frames = duplicate and pilot_frames.ready_set_in_tx(cur, existing["id"], sha) is not None
            emit("worker.dedup", duplicate=duplicate)
            with timing("worker.container_format"):
                ext = _ext(staging)
            key = None
            if not duplicate:
                runtime.update_progress(lease, Progress(sequence=3, phase="SAVING_CANONICAL", done=0, total=0))
                key = (storage.system_raw_key("uploads", video.id, sha, ext) if target_scope == "SYSTEM"
                       else storage.private_raw_key(owner, UUID(p["reserved_source_id"]), sha, ext))
                pilot_objects.intent(db, lease, key, sha, size)
                with timing("worker.s3.canonical_put_head", bytes=size):
                    storage.put_file(key, staging, sha, f"video/{ext}")
                pilot_objects.written(db, lease, key)
            frames = None
            if not has_frames:   # ảnh lưu sẵn tách cùng job; nguồn chỉ READY khi đủ bộ (§21.20)
                set_id = uuid4()
                key_fn = ((lambda idx, variant, h: storage.system_frame_key("uploads", video.id, set_id, idx, variant, h))
                          if target_scope == "SYSTEM"
                          else (lambda idx, variant, h: storage.frame_key(owner, set_id, idx, variant, h)))
                with timing("worker.frames.materialize", units=n_units):
                    frames = materialize(storage, db, lease, video, set_id,
                                         key_fn,
                                         p["frames"], work, stop, temp_used=size,
                                         on_progress=lambda prog: runtime.update_progress(lease, prog))
                if frames is None:
                    return {}
            runtime.update_progress(lease, Progress(sequence=1000000, phase="FINALIZING", done=n_units, total=n_units))
            metadata = {"width": meta["width"], "height": meta["height"], "duration_ms": duration_ms,
                        "n_frames": meta["n_frames"], "n_units": n_units, "fps": video.fps}
            return {"upload": {"sha256": sha, "size_bytes": size, "canonical_key": key, "core_source_id": video.id,
                               "ext": ext, "metadata": metadata, "frames": frames}, "cleanup_done": True}
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _raw(p: dict) -> Path:   # cùng helper/thư mục với session_context: an toàn giữa process CPU và MEDIA (§21.20 F3)
        return cached(storage, cache, p["raw_key"], p["raw_sha256"], RAW_CAP, ".video")

    @traced_handler
    def render_media(lease: AttemptLease, stop: threading.Event) -> dict:
        from ..core.types import Frame

        p = lease.payload
        emit("worker.raw_cache", cache_hit=(cache / f"{p['raw_sha256']}.video").exists())
        with timing("worker.media.raw_download_hash"):
            raw = _raw(p)
        with timing("worker.media.probe_core_hash"):
            video = _probe(raw)
        if stop.is_set():
            return {}
        variant, side = p["variant"], p["max_side"]
        work = workdir / str(lease.attempt_id)
        work.mkdir(parents=True, exist_ok=True)
        if variant == "CONTEXT":
            data, content_type = _context_clip(raw, p["t"], p["context_s"], side or 640, work / "clip.mp4"), "video/mp4"
        else:   # chỉ cho job thumbnail/frame do API cũ enqueue lúc rollout; API mới phục vụ ảnh lưu sẵn (§21.20)
            from PIL import Image

            rgb = _decoder().read(video, Frame(video.id, p["idx"], p["idx"] / video.fps))
            img = Image.fromarray(rgb)
            if side:
                img.thumbnail((side, side))
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=85)
            data, content_type = buf.getvalue(), "image/jpeg"
        artifact_id, sha = uuid4(), _sha(data)
        try:
            out = work / "artifact.bin"
            out.write_bytes(data)
            key = storage.artifact_key(UUID(p["owner_id"]), artifact_id, sha)
            pilot_objects.intent(db, lease, key, sha, len(data))
            with timing("worker.s3.media_put_head", bytes=len(data)):
                storage.put_file(key, out, sha, content_type)
            pilot_objects.written(db, lease, key)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return {"artifact": {"id": str(artifact_id), "object_key": key, "sha256": sha, "size_bytes": len(data),
                             "content_type": content_type}, "cleanup_done": True}

    @traced_handler
    def prepare_frames(lease: AttemptLease, stop: threading.Event) -> dict:
        """Ảnh lưu sẵn cho nguồn cũ (§21.20): raw từ cache theo SHA, kiểm core ID/lưới unit khớp nguồn rồi tách ảnh theo lô."""
        p = lease.payload
        if p.get('sample_copy'):
            from .sample_copy import copy_sample
            return copy_sample(storage, db, lease, stop)
        with timing("worker.media.raw_download_hash"):
            raw = _raw(p)
        video = _probe(raw)
        if video.id != p["core_source_id"] or len(_decoder().frames(video)) != p["n_units"]:
            raise ServiceError("result_invalid", "raw không khớp nguồn đã xác minh", 422)
        if stop.is_set():
            return {}
        owner, set_id = UUID(p["owner_id"]), uuid4()
        target_scope = p.get("target_scope") or "PRIVATE"
        key_fn = ((lambda idx, variant, h: storage.system_frame_key("uploads", video.id, set_id, idx, variant, h))
                  if target_scope == "SYSTEM"
                  else (lambda idx, variant, h: storage.frame_key(owner, set_id, idx, variant, h)))
        work = workdir / str(lease.attempt_id)
        work.mkdir(parents=True, exist_ok=True)
        try:
            runtime.update_progress(lease, Progress(sequence=1, phase="PROBING", done=0, total=p["n_units"]))
            with timing("worker.frames.materialize", units=p["n_units"]):
                frames = materialize(storage, db, lease, video, set_id,
                                     key_fn,
                                     p["frames"], work, stop,
                                     on_progress=lambda prog: runtime.update_progress(lease, prog))
            if frames:
                runtime.update_progress(lease, Progress(sequence=1000000, phase="FINALIZING", done=p["n_units"], total=p["n_units"]))
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return {"frames": frames, "cleanup_done": True} if frames else {}

    from ..store.pilot_delete import make_handler
    return {"VALIDATE_UPLOAD": validate_upload, "RENDER_MEDIA": render_media, "PREPARE_FRAMES": prepare_frames,
            "DELETE_SOURCE": traced_handler(make_handler(storage, db))}


def _context_clip(path: Path, t: float, half: float, max_side: int, out: Path) -> bytes:
    """Clip ±`half` giây quanh t, H.264 yuv420p, cạnh dài ≤ max_side (chẵn), mp4 faststart (cần file thật để ghi lại moov)."""
    from fractions import Fraction

    import av

    with av.open(str(path)) as src:
        vs = src.streams.video[0]
        rate = max(1, min(30, round(float(vs.average_rate or 25))))
        w, h = vs.codec_context.width, vs.codec_context.height
        scale = min(1.0, max_side / max(w, h))
        ow, oh = max(2, int(w * scale) // 2 * 2), max(2, int(h * scale) // 2 * 2)
        start = max(0.0, t - half)
        end = t + half
        origin = float((vs.start_time or 0) * vs.time_base)
        src.seek(int((start + origin) / vs.time_base), stream=vs, backward=True)
        with av.open(str(out), "w", format="mp4", options={"movflags": "+faststart"}) as dst:
            os_ = dst.add_stream("libx264", rate=rate)
            os_.width, os_.height, os_.pix_fmt = ow, oh, "yuv420p"
            n = 0
            previous = None
            ft = 0.0
            source_end = 0.0
            for frame in src.decode(vs):
                if frame.pts is None:
                    raise ServiceError("result_invalid", "frame context thiếu timestamp", 422)
                ft = float(frame.pts * frame.time_base) - origin
                source_end = ft + (float(frame.duration * frame.time_base) if frame.duration else 1 / rate)
                # CFR output samples hold the last source frame until its next real timestamp.
                while previous is not None and start + n / rate < min(ft, end) - 1e-9:
                    img = previous.reformat(width=ow, height=oh, format="yuv420p")
                    img.pts, img.time_base = n, Fraction(1, rate)
                    n += 1
                    for pkt in os_.encode(img):
                        dst.mux(pkt)
                if ft >= end:
                    break
                previous = frame
            else:
                # Do not extend an EOF clip beyond the source's final frame duration.
                end = min(end, source_end)
            while previous is not None and start + n / rate < end - 1e-9:
                img = previous.reformat(width=ow, height=oh, format="yuv420p")
                img.pts, img.time_base = n, Fraction(1, rate)
                n += 1
                for pkt in os_.encode(img):
                    dst.mux(pkt)
            for pkt in os_.encode():
                dst.mux(pkt)
    if n == 0:
        raise ServiceError("result_invalid", "không có frame trong cửa sổ clip", 422)
    return out.read_bytes()
