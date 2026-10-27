"""Explicit CLIP prepare worker/cache readiness for videos; same queue as P1, remote by default.

Input video_id through queue_prepare_clip command. Output progress in VideoDTO.clip_prepare; no CPU fallback.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime

import numpy as np
from dotenv import dotenv_values

from ..components.features.clip_b32 import ClipB32
from ..pipeline import prepare as p1
from ..pipeline.remote_embed import KaggleJobError, remote_compute, source_hashes
from . import App
from .video import get_video


def prepared(app: App, video) -> bool:
    ctx = p1.make_context([video], app.spec, app.fields)
    table = ctx._execution.peek(ClipB32(), ctx.units, ctx)
    if table is None or table.unit_ids != [u.id for u in ctx.units]:
        return False
    values = np.asarray(table.values)
    return (values.shape == (len(ctx.units), 512) and values.dtype == np.float32
            and np.isfinite(values).all() and np.allclose(np.linalg.norm(values, axis=1), 1, atol=1e-4))


def queue_prepare(app: App, video_id: str) -> None:
    if app.storage_backend == 's3':
        from .cloud_embedding import queue
        queue(app,video_id)
        return
    video = get_video(app, video_id)
    # local-only: single API/worker and in-memory status, LOCAL_SINGLE_USER_LIMITS S9–S10.
    with app.clip_job_lock:
        job = app.clip_prepare_jobs.get(video_id)
        if job and job["state"] in ("queued", "running") or prepared(app, video):
            return
        app.clip_prepare_jobs[video_id] = {"state": "queued", "stage": "CLIP", "done": 0, "total": 0,
                                          "started_at": None, "error": None}
        app.prepare_queue.put(lambda: _run(app, video_id))


def _run(app: App, video_id: str) -> None:
    job = app.clip_prepare_jobs[video_id]
    job.update(state="running", started_at=datetime.now(UTC).isoformat())

    def progress(field, done, total):
        job.update(stage=field, done=done, total=total)

    try:
        video = get_video(app, video_id)
        ctx = p1.make_context([video], app.spec, app.fields, on_progress=progress)
        if prepared(app, video):
            job.update(state="done", stage="CLIP", done=len(ctx.units), total=len(ctx.units))
            return
        feature = ClipB32()
        backend = os.environ.get("RAV_CLIP_IMAGE_BACKEND", "kaggle")
        hashes = source_hashes(ctx.units, ctx)
        ctx._start_progress(feature.provides, len(ctx.units))
        if backend == "kaggle":
            job.update(stage="Trích frame → Kaggle GPU")
            table = remote_compute(feature, ctx.units, ctx, kernel=dotenv_values(".env").get("RAV_KAGGLE_KERNEL", ""),
                                   output_dir=app.output_dir, max_payload_bytes=int(os.environ.get("RAV_CLIP_MAX_PAYLOAD_MB", "256")) << 20,
                                   timeout=float(os.environ.get("RAV_CLIP_TIMEOUT_S", "900")),
                                   on_stage=lambda stage: job.update(stage="Kaggle: " + stage, done=0, total=0))
        elif backend == "local":
            table = feature.compute(ctx.units, ctx)
        else:
            raise ValueError("RAV_CLIP_IMAGE_BACKEND phải là kaggle hoặc local (chọn rõ, không fallback).")
        if source_hashes(ctx.units, ctx) != hashes:
            raise ValueError("Nguồn đã đổi trong lúc chuẩn bị CLIP; không ghi cache.")
        if table.key != feature.key or table.unit_ids != [u.id for u in ctx.units]:
            raise ValueError("CLIP key/IDs không khớp")
        values = np.asarray(table.values)
        if (values.shape != (len(ctx.units), 512) or values.dtype != np.float32 or not np.isfinite(values).all()
                or not np.allclose(np.linalg.norm(values, axis=1), 1, atol=1e-4)):
            raise ValueError("CLIP shape/dtype/finite/L2 không khớp")
        ctx._execution.store.put(table, (video_id, ctx._execution.spec_hash))
        ctx._end_progress()
        job.update(state="done", stage="CLIP sẵn sàng", done=len(ctx.units), total=len(ctx.units))
    except Exception as exc:
        # Only our ValueError messages are safe; library errors can expose credentials/URLs.
        message = str(exc) if isinstance(exc, (ValueError, KaggleJobError)) else "Chuẩn bị CLIP lỗi; kiểm model/notebook/quota/kết nối rồi thử lại."
        job.update(state="error", error=message)
