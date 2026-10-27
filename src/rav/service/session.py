"""Tạo / mở phiên: pool (PoolFilter), goal, target_k; chạy P1 cho video trong pool chưa có cache (CONTRACTS §7, §13.3).

Phiên = một dòng DB: session spec (`source`, `decoder`, `units` lấy từ `app.spec`; `pool`; `feedback`; `goal`; `target_k`).
`pool.video_ids` được resolve lúc tạo (None → mọi video đang có) để pool không tự đổi khi catalog có thêm video; chỉ thêm video bằng
`add_videos_to_session` (#56). Video đang chuẩn bị nền → 409 `video_preparing`.
`make_context` dựng `Context` trên pool của phiên — dùng cho review_state (kiểm unit thuộc pool) và proposal.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from ..core.context import Context
from ..core.types import DatasetReference, PoolFilter, Unit
from ..pipeline import prepare as p1
from . import App, ServiceError, publication_guard
from . import dataset as dataset_svc
from . import reference_model as model_svc
from . import video as video_svc


def create_session(app: App, pool: PoolFilter, goal: str, target_k: int, name: str | None = None,
                   reference: DatasetReference | None = None) -> dict:
    reference = reference or DatasetReference()
    if reference.model_id:
        model_svc.verify_model(app, reference.model_id)
        if not reference.dataset_version_ids:
            raise ServiceError("model_training_reference_required", "Chọn dataset train/finetune của model tham chiếu này.")
    versions = [dataset_svc.get_version(app, v) for v in reference.dataset_version_ids]
    if len({v["dataset_id"] for v in versions}) != len(versions):
        raise ServiceError("duplicate_dataset_in_reference", "mỗi dataset chỉ được chọn một version")
    for version in versions:
        job = app.dataset_prepare_jobs.get(version["id"])
        if not dataset_svc.is_prepared(app, version) or job and job["state"] in ("queued", "running"):
            raise ServiceError("reference_not_prepared", "dataset tham chiếu chưa chuẩn bị xong", 409)
    catalog = p1.catalog(app.spec)
    video_ids = sorted(catalog) if pool.video_ids is None else list(dict.fromkeys(pool.video_ids))
    missing = [v for v in video_ids if v not in catalog]
    if missing:
        raise ServiceError("video_not_found", f"không có video {missing}", 404)
    if not video_ids:
        raise ServiceError("empty_pool", "pool không có video nào")
    _check_not_preparing(app, video_ids)
    spec = {**app.spec, "pool": {"video_ids": video_ids}, "feedback": {"type": "keep_drop"},
            "goal": goal, "target_k": target_k, "reference": reference.model_dump(exclude_none=True)}
    ctx = p1.prepare([catalog[v] for v in video_ids], spec, app.fields)   # P1 đọc cache nếu đã có
    row = {"id": uuid.uuid4().hex, "name": name, "created_at": datetime.now(UTC).isoformat(),
           "spec": spec, "n_units": len(ctx.units)}
    app.db.add_session(row)
    return row


def _check_not_preparing(app: App, video_ids: list[str]) -> None:
    busy = [v for v in video_ids if video_svc.is_preparing(app, v)]
    if busy:
        raise ServiceError("video_preparing", f"video đang tiền xử lý (P1 nền), chờ xong rồi thử lại: {busy}", 409)


@publication_guard
def add_videos_to_session(app: App, sid: str, video_ids: list[str]) -> dict:
    """Thêm video đã chuẩn bị vào pool của phiên (không bớt; #56). Trả dòng phiên đã cập nhật."""
    row = get_session(app, sid)
    catalog = p1.catalog(app.spec)
    missing = [v for v in video_ids if v not in catalog]
    if missing:
        raise ServiceError("video_not_found", f"không có video {missing}", 404)
    _check_not_preparing(app, video_ids)
    not_ready = [v for v in video_ids if not video_svc.is_prepared(app, catalog[v])]
    if not_ready:
        raise ServiceError("video_not_prepared", f"video chưa tiền xử lý, bấm Chuẩn bị trước: {not_ready}", 409)
    pool = list(dict.fromkeys([*row["spec"]["pool"]["video_ids"], *video_ids]))
    spec = {**row["spec"], "pool": {"video_ids": pool}}
    ctx = p1.prepare([catalog[v] for v in pool], spec, app.fields)   # đọc cache; PCA theo pool mới
    app.db.update_session_pool(sid, spec, len(ctx.units))
    return get_session(app, sid)


def get_session(app: App, sid: str) -> dict:
    row = app.db.get_session(sid)
    if row is None:
        raise ServiceError("session_not_found", f"không có phiên {sid!r}", 404)
    return row


def list_sessions(app: App) -> list[dict]:
    return app.db.list_sessions()


def latest_snapshot(app: App, row: dict) -> dict | None:
    from .snapshot import with_status

    snapshots = app.db.snapshots(row["id"])
    if not snapshots:
        return None
    current = with_status(app, snapshots[0], row)
    return {k: current[k] for k in ("id", "n", "is_current")}


def make_context(app: App, row: dict, *, include_reference=False, **kwargs) -> Context:
    """Context trên pool của phiên (state, proposals… truyền qua `kwargs`)."""
    spec = row["spec"]
    catalog = p1.catalog(spec)
    missing = [v for v in spec["pool"]["video_ids"] if v not in catalog]
    if missing:
        raise ServiceError("video_not_found", f"phiên {row['id']}: video {missing} không còn trong catalog", 404)
    ctx = p1.make_context([catalog[v] for v in spec["pool"]["video_ids"]], spec, app.fields, **kwargs)
    if include_reference:
        versions = spec.get("reference", {}).get("dataset_version_ids", [])
        if not versions:
            raise ServiceError("training_reference_required", "Recipe cần dataset train tham chiếu; tạo phiên mới và chọn dataset.")
        training = []
        for vid in versions:
            version = dataset_svc.get_version(app, vid)
            report = dataset_svc.verify(app, vid)
            if not report["ok"]:
                raise ServiceError("dataset_file_changed", "Nguồn dataset train thiếu/đổi; kiểm tra dataset trước khi đề xuất.", 409)
            if not dataset_svc.is_prepared(app, version):
                raise ServiceError("reference_not_prepared", "Dataset train chưa chuẩn bị xong.", 409)
            image_ctx = dataset_svc.pipeline.make_context(version, dataset_svc.all_members(app, vid),
                dataset_svc.pipeline.resolve_folder(app.datasets_dir, version["folder"]), app.fields)
            training.extend(image_ctx.units)
            ctx.images.update(image_ctx.images)
        ctx.reference_sets["training"] = training
    return ctx


def list_units(app: App, sid: str, video_id: str | None = None) -> list[Unit]:
    """Unit trong pool, thứ tự `(video_id, t0)`; `video_id` chỉ lọc hiển thị (CONTRACTS §13.1.6)."""
    units = make_context(app, get_session(app, sid)).units
    return [u for u in units if video_id is None or u.video_id == video_id]
