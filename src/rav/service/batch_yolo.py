"""Resolve a proposal's picks and store/reuse complete YOLO batch results (CONTRACTS §17).

Commands call get_result/run. Pipeline does inference; service controls source/model integrity, lock and atomic JSON cache.
No changes to session reference, proposal, review state, scoring or FieldStore.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from hashlib import sha256

from ..pipeline import batch_yolo as pipeline
from ..pipeline.remote_embed import source_hashes
from . import App, ServiceError
from .reference_model import verify_model
from .session import get_session, make_context


def _sources(units, ctx):
    try:
        return source_hashes(units, ctx)
    except (ValueError, OSError):
        raise ServiceError("yolo_source_changed", "Nguồn ảnh/video bị thiếu hoặc đã đổi; không dùng kết quả YOLO cũ.", 409) from None


def _resolve(app, sid, n, model_id):
    session = get_session(app, sid)
    proposal = next((p for p in app.db.proposals(sid) if p["n"] == n), None)
    if proposal is None:
        raise ServiceError("proposal_not_found", "Không có đợt đề xuất này trong phiên.", 404)
    ids = [p["unit_id"] for p in proposal["picks"]]
    if not ids or len(ids) > 256:
        raise ServiceError("invalid_yolo_batch", "YOLO cần 1–256 ảnh trong đợt; giảm batch_size nếu đợt quá lớn.")
    model = verify_model(app, model_id)
    ctx = make_context(app, session)
    by_id = {u.id: u for u in ctx.units}
    if any(uid not in by_id for uid in ids):
        raise ServiceError("unit_not_found", "Ảnh của đợt không còn trong pool phiên.", 404)
    units = [by_id[uid] for uid in ids]
    fingerprint = {"session_id": sid, "proposal_n": n, "model_sha256": model["sha256"], "unit_ids": ids,
                   "sample_spec_hash": ctx._execution.spec_hash, "source_hashes": _sources(units, ctx),
                   "engine_version": pipeline.ENGINE_VERSION, "implementation": 1, "params": pipeline.PARAMS}
    key = sha256(json.dumps(fingerprint, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    path = app.output_dir / "yolo_batches" / (key + ".json")
    return model, ctx, units, fingerprint, path


def _cached(path, fingerprint):
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        result = value["result"]
        if (value["fingerprint"] != fingerprint or [r["unit_id"] for r in result["items"]] != fingerprint["unit_ids"]
                or result["total_images"] != len(fingerprint["unit_ids"])):
            raise ValueError()
        return {**result, "cached": True}
    except (ValueError, KeyError, TypeError, OSError):
        raise ServiceError("yolo_cache_invalid", "Cache YOLO không hợp lệ; kiểm thư mục output/yolo_batches.", 409) from None


def get_result(app: App, sid: str, n: int, model_id: str) -> dict | None:
    _, _, _, fingerprint, path = _resolve(app, sid, n, model_id)
    return _cached(path, fingerprint)


def run(app: App, sid: str, n: int, model_id: str) -> dict:
    # local-only: one API/output; don't load several checkpoints at once on the low-RAM host (M2).
    if not app.batch_yolo_lock.acquire(blocking=False):
        raise ServiceError("yolo_busy", "YOLO đang chạy một đợt khác; chờ xong rồi thử lại.", 409)
    try:
        model, ctx, units, fingerprint, path = _resolve(app, sid, n, model_id)
        cached = _cached(path, fingerprint)
        if cached is not None:
            return cached
        try:
            items = pipeline.predict_batch(app.models_dir / (model_id + ".pt"), model_id, units, ctx, app.output_dir)
        except pipeline.YoloError as exc:
            raise ServiceError("unsupported_yolo_model", str(exc)) from None
        except Exception:
            raise ServiceError("yolo_inference_failed", "Không nạp/chạy được YOLO detection; kiểm checkpoint và dependencies rồi thử lại.", 422) from None
        verify_model(app, model_id)
        if _sources(units, ctx) != fingerprint["source_hashes"]:
            raise ServiceError("yolo_source_changed", "Nguồn ảnh/video đổi trong khi chạy; kết quả không được lưu.", 409)
        if [r["unit_id"] for r in items] != fingerprint["unit_ids"]:
            raise ServiceError("yolo_result_invalid", "Kết quả YOLO không khớp ảnh của đợt.", 500)
        result = {"session_id": sid, "proposal_n": n, "model_id": model_id, "model_name": model["name"],
                  "model_sha256": model["sha256"], "engine_version": pipeline.ENGINE_VERSION,
                  "params": pipeline.PARAMS, "created_at": datetime.now(UTC).isoformat(),
                  "total_images": len(units), "items": items, "cached": False}
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
        try:
            temp.write_text(json.dumps({"fingerprint": fingerprint, "result": result}, allow_nan=False), encoding="utf-8")
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
        return result
    finally:
        app.batch_yolo_lock.release()
