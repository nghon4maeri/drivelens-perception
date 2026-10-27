"""Admin upload model tham chiếu SYSTEM của pilot (CONTRACTS §21.33).

API: `create_upload` (admin → presigned POST vào staging, không ghi DB) → browser POST thẳng lên S3 → `complete`
(HEAD staging rồi enqueue `MODEL_VERIFY`) → worker tải + SHA + nạp/chạy thử → `publish_in_tx` INSERT `rav_pilot_model_asset`.
`model_row`/`insert_model_in_tx` dùng chung với `scripts/pilot_import_model.py` (cùng id/metadata cho cùng bytes).
Admin = user_id trong `RAV_PILOT_OPERATOR_IDS`. API không nạp/unpickle model.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..actor import ActorContext
from ..errors import ServiceError
from .pilot_db import PilotDB, require_user
from .pilot_operations import operations_read
from .pilot_runtime import EnqueueSpec, JobDTO, PilotRuntime
from .pilot_storage import PilotStorage

MAX_MODEL_BYTES = 1 << 30
UPLOAD_TTL_S = 3600
_NAME = re.compile(r"[^\w .()\-]+", re.UNICODE)


class ModelUploadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    declared_bytes: int = Field(gt=0, le=MAX_MODEL_BYTES)

    @field_validator("filename")
    @classmethod
    def _pt(cls, v: str) -> str:
        if not v.lower().endswith(".pt"):
            raise ValueError("chỉ nhận checkpoint .pt")
        return v


class ModelCompleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=200)
    train_dataset: str = Field(min_length=1, max_length=200)

    @field_validator("filename")
    @classmethod
    def _display_only(cls, v: str) -> str:   # chỉ ghi vào checkpoint_revision; key S3 không dùng tên file
        return _NAME.sub("_", v.replace("\\", "/").rsplit("/", 1)[-1]).strip() or "model.pt"


def models_write(user_id: UUID | None) -> bool:
    return user_id is not None and operations_read(user_id)


def staging_key(prefix: str, upload_id: UUID) -> str:
    return f"{prefix}/staging/system-models/{upload_id}/model.pt"


def model_row(prefix: str, sha: str, size: int, name: str, file: str, train_dataset: str, info: dict, **provenance) -> dict:
    """Bản ghi model SYSTEM từ kết quả `batch_yolo.verify_checkpoint`; id/key chỉ phụ thuộc SHA."""
    return {"id": str(uuid5(NAMESPACE_URL, f"rav-pilot-model:{sha}")), "core_model_id": f"ym-{sha}", "name": name, "sha256": sha,
            "size_bytes": size, "raw_key": f"{prefix}/system/models/yolo/{sha}.pt",
            "checkpoint_revision": f"ultralytics-{info['engine_version']}:{file}", "task": info["task"], "class_map": info["class_map"],
            "runtime_manifest": {"engine_version": info["engine_version"], "params": info["params"]},
            "license": {"note": "theo giấy phép của nhà cung cấp checkpoint; operator kiểm trước khi dùng"},
            "provenance": {"train_dataset": train_dataset, "file": file,
                           "note": "model tham chiếu hiển thị detection; không phải model train trên dataset của phiên", **provenance},
            "validation": "INFERENCE_VERIFIED"}


def insert_model_in_tx(cur, row: dict) -> bool:
    """INSERT idempotent theo id; trả True nếu vừa tạo. Cùng id mà khác SHA → lỗi (không ghi đè)."""
    from psycopg.types.json import Jsonb

    cur.execute("INSERT INTO rav_pilot_model_asset (id, core_model_id, scope, protected, name, raw_key, sha256, size_bytes, "
                "checkpoint_revision, task, class_map, runtime_manifest, license, provenance, validation) "
                "VALUES (%s, %s, 'SYSTEM', true, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                (row["id"], row["core_model_id"], row["name"], row["raw_key"], row["sha256"], row["size_bytes"],
                 row["checkpoint_revision"], row["task"], Jsonb(row["class_map"]), Jsonb(row["runtime_manifest"]),
                 Jsonb(row["license"]), Jsonb(row["provenance"]), row["validation"]))
    created = cur.rowcount == 1
    cur.execute("SELECT sha256 FROM rav_pilot_model_asset WHERE id = %s", (row["id"],))
    got = cur.fetchone()
    if (got["sha256"] if isinstance(got, dict) else got[0]) != row["sha256"]:
        raise ServiceError("result_invalid", "model cùng id đã có với nội dung khác", 422)
    return created


class PilotModels:
    def __init__(self, db: PilotDB, storage: Callable[[], PilotStorage]):
        self.db, self.storage, self.runtime = db, storage, PilotRuntime(db)

    @staticmethod
    def _admin(actor: ActorContext) -> UUID:
        uid = require_user(actor)
        if not models_write(uid):
            raise ServiceError("forbidden", "chỉ admin được upload mô hình tham chiếu", 403)
        return uid

    def create_upload(self, actor: ActorContext, inp: ModelUploadIn) -> dict:
        self._admin(actor)
        store = self.storage()
        upload_id = uuid4()
        return {"upload_id": upload_id,
                "transfer": store.presign_post(staging_key(store.prefix, upload_id), inp.declared_bytes, UPLOAD_TTL_S)}

    def complete(self, actor: ActorContext, upload_id: UUID, inp: ModelCompleteIn) -> JobDTO:
        uid = self._admin(actor)
        store = self.storage()
        key = staging_key(store.prefix, upload_id)
        head = store.head(key)
        if head is None:
            raise ServiceError("upload_invalid", "chưa thấy file trên vùng tải lên", 409)
        if not 0 < head["ContentLength"] <= MAX_MODEL_BYTES:
            raise ServiceError("upload_invalid", "file model rỗng hoặc vượt 1 GiB", 409)
        return self.runtime.enqueue(actor, EnqueueSpec(
            kind="MODEL_VERIFY", resource_id=upload_id, idempotency_key=f"model-upload:{upload_id}",
            payload={"upload_id": str(upload_id), "staging_key": key, "size_bytes": head["ContentLength"], "prefix": store.prefix,
                     "filename": inp.filename, "name": inp.name, "train_dataset": inp.train_dataset, "uploaded_by": str(uid)}))


# ------------------------------------------------------------------------------
# Hook worker (gọi từ PilotRuntime trong cùng transaction publish/kết thúc)
# ------------------------------------------------------------------------------
def publish_in_tx(cur, job: dict, lease, outputs: dict) -> None:
    """outputs["model"] = bản ghi `model_row` worker dựng từ bytes đã kiểm (result_ref chỉ trỏ artifact nên để trống)."""
    p, row = job["payload"], outputs["model"]
    sha = row.get("sha256", "")
    expected = model_row(p["prefix"], sha, p["size_bytes"], p["name"], p["filename"], p["train_dataset"],
                         {"engine_version": row.get("runtime_manifest", {}).get("engine_version", ""), "task": "detect",
                          "params": row.get("runtime_manifest", {}).get("params"), "class_map": row.get("class_map")},
                         uploaded_by=p["uploaded_by"])
    if not re.fullmatch(r"[0-9a-f]{64}", sha) or row != expected or not isinstance(row["class_map"], dict):
        raise ServiceError("result_invalid", "kết quả kiểm model không khớp payload", 422)
    insert_model_in_tx(cur, row)


def finish_in_tx(cur, job: dict, state: str, error: dict | None) -> None:
    """Không có trạng thái DB riêng; worker đã xóa staging trong finally."""
