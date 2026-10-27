"""Đặc trưng DINO/CLIP của pilot (CONTRACTS §21.14, #94): yêu cầu chuẩn bị, readiness, publish artifact FEATURE.

- `PilotFeatures.prepare` (API): artifact READY khớp `input_hash` → sẵn sàng (200); nguồn PRIVATE chưa có → enqueue
  `PREPARE_DINO|PREPARE_CLIP` (queue GPU, 202); nguồn SYSTEM thiếu → 409 `system_feature_missing` (operator import, không chạy job).
- `readiness_in_tx`: `SourceDTO.dino/clip = {ready, job_id}` từ artifact READY + job PREPARE đang hoạt động của owner.
- `publish_in_tx` (hook runtime, cùng transaction publish): kiểm manifest khớp payload đóng băng rồi tạo artifact FEATURE READY.
Chỉ DB, import nhẹ (API dùng được).
"""

from __future__ import annotations

import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from ..actor import ActorContext
from ..errors import ServiceError
from . import pilot_storage
from .pilot_db import DEFAULT_SAMPLE_SPEC, PilotDB, PrepState, _In, canonical_hash, require_user
from .pilot_runtime import ACTIVE, EnqueueSpec, JobDTO, PilotRuntime

Feature = Literal["DINO", "CLIP"]
FIELDS: dict[str, tuple[str, ...]] = {"DINO": ("emb.dinov2_s", "hash.phash"), "CLIP": ("emb.clip_b32",)}
KIND = {"DINO": "PREPARE_DINO", "CLIP": "PREPARE_CLIP"}
FEATURE_OF = {v: k for k, v in KIND.items()}


class PreparationIn(_In):
    feature: Feature | Literal["FRAMES"]   # FRAMES: ảnh lưu sẵn cho nguồn cũ (§21.20)


class PreparationDTO(BaseModel):
    source_id: UUID
    feature: Feature | Literal["FRAMES"]
    ready: bool
    job: JobDTO | None


def input_hash(src: dict, feature: str) -> str:
    """Danh tính đặc trưng: nội dung nguồn + field + spec unit (không phụ thuộc owner)."""
    return canonical_hash({"core_source_id": src["core_source_id"], "raw_sha256": src["raw_sha256"], "feature": feature,
                           "field_keys": list(FIELDS[feature]), "spec_hash": DEFAULT_SAMPLE_SPEC["spec_hash"]})


def payload(src: dict, feature: str, *, owner_id: UUID | None = None) -> dict:
    """Payload đóng băng lúc enqueue (§21.14); raw key do server dựng, không từ client."""
    return {"source_id": str(src["id"]), "owner_id": str(owner_id or src["owner_id"]), "target_scope": src["scope"],
            "core_source_id": src["core_source_id"], "raw_key": src["raw_key"], "raw_sha256": src["raw_sha256"],
            "resource_version": src["resource_version"], "feature": feature, "field_keys": list(FIELDS[feature]),
            "spec": {k: DEFAULT_SAMPLE_SPEC[k] for k in ("decoder", "units")}, "spec_hash": DEFAULT_SAMPLE_SPEC["spec_hash"],
            "n_units": (src["metadata"] or {}).get("n_units"), "fps": (src["metadata"] or {}).get("fps"),
            "input_hash": input_hash(src, feature)}


def readiness_in_tx(cur, uid: UUID, source_ids: list[UUID]) -> dict[UUID, dict[str, PrepState]]:
    out = {sid: {"DINO": PrepState(ready=False, job_id=None), "CLIP": PrepState(ready=False, job_id=None)} for sid in source_ids}
    if not source_ids:
        return out
    cur.execute("SELECT source_id, variant FROM rav_pilot_artifact WHERE kind = 'FEATURE' AND state = 'READY' "
                "AND source_id = ANY(%s) AND (owner_id = %s OR scope = 'SYSTEM')", (source_ids, uid))
    for r in cur.fetchall():
        if r["variant"] in FIELDS:
            out[r["source_id"]][r["variant"]] = PrepState(ready=True, job_id=None)
    cur.execute("SELECT resource_id, kind, id FROM rav_pilot_job "
                "WHERE (owner_id = %s OR payload ->> 'target_scope' = 'SYSTEM') AND kind = ANY(%s) AND state = ANY(%s) "
                "AND resource_id = ANY(%s) ORDER BY created_at", (uid, list(KIND.values()), list(ACTIVE), source_ids))
    for r in cur.fetchall():
        state = out[r["resource_id"]][FEATURE_OF[r["kind"]]]
        if not state.ready:
            state.job_id = r["id"]
    return out


class PilotFeatures:
    def __init__(self, db: PilotDB, runtime: PilotRuntime | None = None):
        self.db, self.runtime = db, runtime or PilotRuntime(db)

    def prepare(self, actor: ActorContext, source_id: UUID, inp: PreparationIn, idempotency_key: str) -> tuple[PreparationDTO, int]:
        uid = require_user(actor)
        with self.db.backend() as cur:
            profile = self.db.lock_profile(cur, uid)
            src = self.db.lock_sources(cur, uid, [source_id])[source_id]
            if src["availability"] != "READY":
                raise ServiceError("source_unavailable", f"nguồn đang {src['availability']}", 409)
            if src["kind"] != "VIDEO":
                raise ServiceError("validation_error", "chỉ chuẩn bị đặc trưng cho nguồn video", 422)
            if inp.feature == "FRAMES":
                return self._prepare_frames(cur, uid, profile, src, idempotency_key)
            ih = input_hash(src, inp.feature)
            cur.execute("SELECT 1 FROM rav_pilot_artifact WHERE kind = 'FEATURE' AND state = 'READY' AND source_id = %s "
                        "AND variant = %s AND input_hash = %s", (src["id"], inp.feature, ih))
            if cur.fetchone():
                return PreparationDTO(source_id=src["id"], feature=inp.feature, ready=True, job=None), 200
            if src["scope"] == "SYSTEM":
                from .pilot_operations import operations_read

                if not operations_read(uid):
                    raise ServiceError("system_feature_missing", "nguồn hệ thống chưa có đặc trưng; operator cần import", 409)
            job = self.runtime.enqueue_in_tx(cur, uid, EnqueueSpec(
                kind=KIND[inp.feature], resource_id=src["id"], idempotency_key=f"prepare:{idempotency_key}",
                payload=payload(src, inp.feature, owner_id=uid)), [src["id"]])
            return PreparationDTO(source_id=src["id"], feature=inp.feature, ready=False, job=job), 202

    def _prepare_frames(self, cur, uid: UUID, profile: dict, src: dict, idempotency_key: str) -> tuple[PreparationDTO, int]:
        """Ảnh lưu sẵn cho nguồn chưa có bộ READY: job `PREPARE_FRAMES` explicit (§21.20); cùng input đang chạy → trả job đó."""
        from . import pilot_frames

        if pilot_frames.ready_set_in_tx(cur, src["id"], src["raw_sha256"]):
            return PreparationDTO(source_id=src["id"], feature="FRAMES", ready=True, job=None), 200
        if src["scope"] == "SYSTEM":
            from .pilot_operations import operations_read

            if not operations_read(uid):
                raise ServiceError("system_frames_missing", "nguồn hệ thống chưa có ảnh lưu sẵn; operator cần chuẩn bị", 409)
        _, cfg = self.runtime._policy(cur, profile)
        job = self.runtime.enqueue_in_tx(cur, uid, EnqueueSpec(
            kind="PREPARE_FRAMES", resource_id=src["id"], idempotency_key=f"prepare:{idempotency_key}",
            payload=pilot_frames.prepare_payload(src, pilot_frames.bounds(cfg, "PREPARE_FRAMES"), owner_id=uid)), [src["id"]])
        return PreparationDTO(source_id=src["id"], feature="FRAMES", ready=False, job=job), 202


# ------------------------------------------------------------------------------
# Hook runtime (cùng transaction publish/finish)
# ------------------------------------------------------------------------------
def publish_in_tx(cur, job: dict, lease, outputs: dict) -> UUID:
    """outputs["feature"] = {id, object_key, sha256, size_bytes, manifest}; trả artifact id (job.result_ref)."""
    from psycopg import errors as pg_errors
    from psycopg.types.json import Jsonb

    p, f = job["payload"], outputs.get("feature") or {}
    m = f.get("manifest") or {}
    if (not re.fullmatch(r"[0-9a-f]{64}", str(f.get("sha256"))) or not isinstance(f.get("size_bytes"), int) or f["size_bytes"] <= 0
            or m.get("source_id") != p["source_id"] or m.get("core_source_id") != p["core_source_id"]
            or m.get("feature") != p["feature"] or m.get("input_hash") != p["input_hash"] or m.get("spec_hash") != p["spec_hash"]
            or m.get("n_units") != p["n_units"] or m.get("npz_sha256") != f["sha256"]
            or sorted(m.get("fields") or []) != sorted(p["field_keys"])):
        raise ServiceError("result_invalid", "manifest đặc trưng không khớp payload đóng băng", 422)
    cur.execute("SELECT availability, resource_version, scope FROM rav_pilot_source_asset WHERE id = %s FOR UPDATE",
                (UUID(p["source_id"]),))
    src = cur.fetchone()
    if not src or src["availability"] != "READY" or src["resource_version"] != p["resource_version"]:
        raise ServiceError("source_unavailable", "nguồn đã đổi trong lúc chuẩn bị đặc trưng", 409)
    target_scope = p.get("target_scope") or src["scope"] or "PRIVATE"
    art_owner = None if target_scope == "SYSTEM" else job["owner_id"]
    art_scope = "SYSTEM" if target_scope == "SYSTEM" else "PRIVATE"
    artifact_id = UUID(f["id"])
    try:
        with cur.connection.transaction():
            cur.execute("INSERT INTO rav_pilot_artifact (id, owner_id, scope, source_id, job_id, attempt_id, kind, variant, input_hash, "
                        "object_key, sha256, size_bytes, state, policy_version, manifest) "
                        "VALUES (%s, %s, %s, %s, %s, %s, 'FEATURE', %s, %s, %s, %s, %s, 'READY', %s, %s)",
                        (artifact_id, art_owner, art_scope, UUID(p["source_id"]), job["id"], lease.attempt_id, p["feature"],
                         p["input_hash"], f["object_key"], f["sha256"], f["size_bytes"], job["policy_version"], Jsonb(m)))
    except pg_errors.UniqueViolation:
        raise ServiceError("stale_input", "đặc trưng của nguồn đã sẵn sàng từ job khác", 409) from None
    from .pilot_objects import accept_in_tx
    accept_in_tx(cur, lease, f["object_key"], f["sha256"], f["size_bytes"])
    pilot_storage.release_job_holds(cur, job["id"], persisted=f["size_bytes"])
    return artifact_id


def finish_in_tx(cur, job: dict, state: str, error: dict | None) -> None:
    """Không có bản ghi phụ cần đóng: readiness đọc thẳng từ artifact/job."""
