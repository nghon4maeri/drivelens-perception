"""Tách ảnh lưu sẵn theo lô cho một video đã xác minh (CONTRACTS §21.20, quyết định #119).

Input: video local (`VideoRef` từ `_probe`), lease attempt, giới hạn đóng băng trong payload (`pilot_frames.bounds`).
Output: dict `frames` cho hook publish (`frame_set_id`, `n_units`, `total_bytes`, `items` theo `(variant, frame_idx)`), hoặc None khi stop.
Mỗi unit trên lưới decoder đọc tuần tự bằng `read_exact` (index stream thật), encode FRAME + THUMBNAIL từ cùng RGB anchor.
Mỗi lô: file tạm trong workspace → một transaction giữ byte + INTENT → PUT/HEAD song song hữu hạn → một transaction WRITTEN → xóa file tạm.
RAM chỉ giữ một frame RGB; hủy/stop kiểm giữa các lô; lỗi để receipt cho cleanup của supervisor (không xóa prefix).
Dùng từ handler `VALIDATE_UPLOAD` (và `PREPARE_FRAMES` ở F2).
"""

from __future__ import annotations

import hashlib
import io
import shutil
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import UUID

from ..errors import ServiceError
from ..store import pilot_objects
from ..store.pilot_db import DEFAULT_SAMPLE_SPEC
from ..store.pilot_frames import ENCODING_VERSION, FRAME_ENCODING
from ..timing import emit, timing


def encode(rgb) -> list[tuple[str, bytes, int, int]]:
    """FRAME đủ phân giải + THUMBNAIL ≤ 320 từ cùng RGB exact (không resize từ JPEG); trả (variant, bytes, w, h)."""
    from PIL import Image

    img = Image.fromarray(rgb)
    out = []
    for variant in ("FRAME", "THUMBNAIL"):
        pic = img
        if variant == "THUMBNAIL":
            side = FRAME_ENCODING["thumbnail"]["max_side"]
            pic = img.copy()
            pic.thumbnail((side, side))
        buf = io.BytesIO()
        pic.save(buf, FRAME_ENCODING["format"], quality=FRAME_ENCODING["quality"])
        out.append((variant, buf.getvalue(), pic.width, pic.height))
    return out


def _no_space() -> ServiceError:
    return ServiceError("dependency_unavailable", "đĩa tạm của worker không đủ chỗ cho ảnh", 503)


def encode_batch(decoder, video, frames, out_dir: Path, key_of: Callable[[int, str, str], str],
                 *, max_bytes: int | None = None) -> list[dict[str, Any]]:
    """Đọc tuần tự `read_exact` + encode một lô unit vào file tạm; trả item (key/sha/size/kích thước/path) theo thứ tự lưới."""
    batch: list[dict[str, Any]] = []
    used = 0
    for f in frames:
        t = f"{f.t:.3f}"
        for variant, data, w, h in encode(decoder.read_exact(video, f)):
            if max_bytes is not None and used + len(data) > max_bytes:
                raise _no_space()
            used += len(data)
            sha = hashlib.sha256(data).hexdigest()
            path = out_dir / f"{f.idx}-{variant}.jpg"
            try:
                path.write_bytes(data)
            except OSError:
                raise _no_space() from None
            batch.append({"variant": variant, "frame_idx": f.idx, "unit_id": f"{video.id}:{t}-{t}",
                          "object_key": key_of(f.idx, variant, sha), "sha256": sha, "size_bytes": len(data),
                          "width": w, "height": h, "path": path})
    return batch


def materialize(storage, db, lease, video, frame_set_id: UUID, key_of: Callable[[int, str, str], str], bounds: dict,
                work: Path, stop: threading.Event, temp_used: int = 0,
                on_progress: Callable[[Any], None] | None = None) -> dict[str, Any] | None:
    """`key_of(frame_idx, variant, sha256)` dựng key S3 do server quy định; `temp_used` = byte tạm khác của attempt (vd staging)."""
    import time

    from ..components.decoders.pyav import PyAV
    from ..store.pilot_runtime import Progress

    if bounds.get("encoding_version") != ENCODING_VERSION:
        raise ServiceError("result_invalid", "payload yêu cầu encoding ảnh khác bản worker", 422)
    decoder = PyAV(DEFAULT_SAMPLE_SPEC["decoder"]["params"])
    frames = decoder.frames(video)
    total_units = len(frames)
    start_time = time.monotonic()
    if on_progress:
        on_progress(Progress(sequence=0, phase="EXTRACTING_FRAMES", done=0, total=total_units, elapsed_s=0.0))
    size, cap = bounds["frame_batch_size"], bounds["worker_temp_bytes"]
    out_dir = work / "frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    total, last_batch = 0, 0
    try:
        with ThreadPoolExecutor(max_workers=bounds["frame_put_concurrency"]) as pool:
            for n, start in enumerate(range(0, len(frames), size)):
                if stop.is_set():
                    return None
                if shutil.disk_usage(out_dir).free < last_batch:   # lô trước là ước lượng chỗ cần cho lô này
                    raise _no_space()
                with timing("worker.frames.decode_encode", units=len(frames[start:start + size])):
                    batch = encode_batch(decoder, video, frames[start:start + size], out_dir, key_of)
                last_batch = sum(i["size_bytes"] for i in batch)
                total += last_batch
                if total > bounds["max_frame_bytes"]:
                    raise ServiceError("upload_invalid", f"bộ ảnh vượt {bounds['max_frame_bytes']} byte", 422)
                if temp_used + last_batch > cap:
                    raise _no_space()
                with timing("worker.frames.intent_batch", objects=len(batch), bytes=last_batch):
                    pilot_objects.intent_batch(db, lease, [(i["object_key"], i["sha256"], i["size_bytes"]) for i in batch],
                                               f"frames:{lease.attempt_id}:{n}")
                with timing("worker.s3.frames_put_head", objects=len(batch), bytes=last_batch):
                    list(pool.map(lambda i: storage.put_file(i["object_key"], i["path"], i["sha256"], "image/jpeg"), batch))
                pilot_objects.written_batch(db, lease, [i["object_key"] for i in batch])
                for i in batch:
                    i.pop("path").unlink(missing_ok=True)
                items.extend(batch)
                if on_progress:
                    units_done = min(start + len(frames[start:start + size]), total_units)
                    elapsed = time.monotonic() - start_time
                    fps = round(units_done / max(elapsed, 0.001), 2)
                    remaining = max(0, total_units - units_done)
                    eta_s = round(remaining / max(fps, 0.1), 1) if units_done > 0 else None
                    on_progress(Progress(sequence=n + 1, phase="EXTRACTING_FRAMES", done=units_done,
                                         total=total_units, fps=fps, elapsed_s=round(elapsed, 1), eta_s=eta_s))
    finally:
        handle = PyAV._handles.pop(video.uri, None)   # đóng file để xóa được workspace (Windows giữ khóa file đang mở)
        if handle is not None:
            handle.container.close()
        shutil.rmtree(out_dir, ignore_errors=True)
    emit("worker.frames.done", units=len(frames), objects=len(items), bytes=total)
    return {"frame_set_id": str(frame_set_id), "encoding_version": ENCODING_VERSION,
            "sample_spec_hash": DEFAULT_SAMPLE_SPEC["spec_hash"], "n_units": len(frames), "total_bytes": total, "items": items}
