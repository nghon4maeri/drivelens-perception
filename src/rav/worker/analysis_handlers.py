"""Handler phân tích của worker pilot: `YOLO_BATCH`, `METRICS` (CONTRACTS §17, §21.17, #97), `MODEL_VERIFY` (§21.33).

- YOLO_BATCH: checkpoint model SYSTEM theo SHA (cache) → Context các video của picks → `pipeline/batch_yolo.predict_batch` (pixel đúng anchor,
  CPU, tham số cố định như local M2) → detections đúng thứ tự picks. Không đổi proposal/state/scoring.
- METRICS: Context như PROPOSE (đặc trưng chỉ từ artifact, history quyết định qua `keep_drop`) → `service/metrics.metrics_record`
  + dữ liệu view (`embedding_map`, `timeline`, `duplicate_pairs`) tham số mặc định; giá trị không hữu hạn → null.
- MODEL_VERIFY: tải checkpoint admin upload từ staging (giới hạn + SHA) → nạp/chạy thử như `scripts/pilot_import_model.py`
  → PUT canonical theo SHA → bản ghi model cho publish. Staging luôn bị xóa.
Chạy trong tiến trình worker (numpy, PyAV, torch/ultralytics cho YOLO).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from ..errors import ServiceError
from ..store.pilot_db import PilotDB
from ..store.pilot_runtime import AttemptLease
from ..store.pilot_storage import PilotStorage
from .curation_handlers import finite
from .session_context import build_context, cached, policy_of, pool_context


def jsonable(value: Any) -> Any:
    """numpy → kiểu JSON; không hữu hạn → null."""
    return finite(json.loads(json.dumps(value, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))))


def make_analysis_handlers(storage: PilotStorage, db: PilotDB, workdir: Path) -> dict[str, Any]:
    workdir.mkdir(parents=True, exist_ok=True)

    def yolo_batch(lease: AttemptLease, stop: threading.Event) -> dict:
        from ..pipeline import batch_yolo

        p = lease.payload
        m = p["model"]
        model_path = cached(storage, workdir / "model-cache", m["raw_key"], m["sha256"], m["size_bytes"], ".pt")
        ctx, _ = pool_context(storage, p, workdir, policy_of(db, lease.job_id)["max_video_bytes"])
        by_id = {u.id: u for u in ctx.units}
        if any(uid not in by_id for uid in p["unit_ids"]):
            raise ServiceError("result_invalid", "ảnh của đợt không thuộc nguồn đã đóng băng", 422)
        if stop.is_set():
            return {}
        try:
            items = batch_yolo.predict_batch(model_path, m["core_model_id"], [by_id[u] for u in p["unit_ids"]], ctx, workdir)
        except batch_yolo.YoloError as exc:
            raise ServiceError("unsupported_yolo_model", str(exc), 422) from None
        return {"detections": jsonable({"model_id": m["id"], "model_name": m["name"], "model_sha256": m["sha256"],
                                        "engine_version": batch_yolo.ENGINE_VERSION, "params": batch_yolo.PARAMS,
                                        "total_images": len(items), "items": items}), "cleanup_done": True}

    def metrics(lease: AttemptLease, stop: threading.Event) -> dict:
        from ..core.registry import REGISTRY
        from ..core.types import ComponentConfig
        from ..service.metrics import metrics_record

        p = lease.payload
        ctx = build_context(storage, db, p, workdir, policy_of(db, lease.job_id)["max_video_bytes"], history=True)
        if stop.is_set():
            return {}
        inp = p["input"]
        configs = [ComponentConfig.model_validate(x) for x in inp["metrics"]] if inp["metrics"] else None
        record = metrics_record(ctx, configs, inp["on"], p["target_k"])
        views: dict[str, Any] = {}
        for v in inp["views"]:
            try:
                spec = REGISTRY.build("view", v).build(ctx)
                views[v["type"]] = {"type": spec.type, "data": spec.data}
            except (KeyError, ValueError) as exc:   # vd pool chưa có pHash/DINO cho view này
                views[v["type"]] = {"type": v["type"], "error": str(exc.args[0] if exc.args else exc)[:300]}
        return {"analysis": jsonable({**record, "views": views}), "cleanup_done": True}

    def model_verify(lease: AttemptLease, stop: threading.Event) -> dict:
        from ..pipeline import batch_yolo
        from ..store.pilot_models import MAX_MODEL_BYTES, model_row

        p = lease.payload
        path = workdir / "model-verify" / f"{lease.attempt_id}.pt"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            sha, size = storage.download(p["staging_key"], path, MAX_MODEL_BYTES)
            if stop.is_set():
                return {}
            try:   # checkpoint tuỳ ý: lỗi pickle/thiếu lớp/khác task đều là model không dùng được
                info = batch_yolo.verify_checkpoint(path, workdir)
            except Exception as exc:
                raise ServiceError("unsupported_yolo_model", str(exc)[:300] or type(exc).__name__, 422) from None
            row = model_row(p["prefix"], sha, size, p["name"], p["filename"], p["train_dataset"], info, uploaded_by=p["uploaded_by"])
            head = storage.head(row["raw_key"])
            if head is None or head["ContentLength"] != size:
                storage.put_file(row["raw_key"], path, sha, "application/octet-stream")
        except ServiceError as exc:
            if exc.code in ("unsupported_yolo_model", "upload_invalid"):   # lỗi tạm (S3) giữ staging cho attempt sau
                storage.delete(p["staging_key"])
            raise
        finally:
            path.unlink(missing_ok=True)
        storage.delete(p["staging_key"])
        return {"model": row, "cleanup_done": True}

    return {"YOLO_BATCH": yolo_batch, "METRICS": metrics, "MODEL_VERIFY": model_verify}
