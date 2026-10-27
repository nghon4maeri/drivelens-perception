"""Upload video trực tiếp lên S3 của pilot (CONTRACTS §21.7, §21.12; runtime spec §6.1).

API: `create` (giữ dung lượng 2 × declared + presigned POST vào staging) → client POST thẳng lên S3 → `complete`
(HEAD staging rồi enqueue `VALIDATE_UPLOAD` cùng transaction) → worker xác minh → `publish_in_tx` tạo nguồn PRIVATE READY.
`abort` hủy upload chưa xong; `sweep_staging` (supervisor) xóa staging sau hạn + grace và trả phần dung lượng staging.
Không decode/hash ở API; không nhận key/bucket từ client.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..actor import ActorContext
from ..errors import ServiceError
from ..timing import timing
from . import pilot_frames, pilot_objects, pilot_storage
from .pilot_db import PilotDB, canonical_hash, require_user
from .pilot_runtime import EnqueueSpec, PilotRuntime

_NAME = re.compile(r"[^\w .()\-]+", re.UNICODE)


class UploadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    declared_bytes: int = Field(gt=0)
    preferred_transport: Literal["VPS_DISK"] | None = None
    target_scope: Literal["PRIVATE", "SYSTEM"] = "PRIVATE"

    @field_validator("filename")
    @classmethod
    def _display_only(cls, v: str) -> str:   # chỉ để hiển thị; key S3 không dùng tên file
        return _NAME.sub("_", v.replace("\\", "/").rsplit("/", 1)[-1]).strip() or "video"


class TransferDTO(BaseModel):
    method: str
    url: str
    fields: dict[str, str]
    expires_at: str
    part_size: int | None = None
    part_count: int | None = None
    capability: str | None = None


class UploadDTO(BaseModel):
    id: UUID
    state: str
    validation_job_id: UUID | None
    source_id: UUID | None
    error: dict[str, Any] | None
    declared_bytes: int | None = None
    filename: str | None = None
    target_scope: Literal["PRIVATE", "SYSTEM"] = "PRIVATE"
    progress: dict[str, Any] | None = None


class UploadCreatedDTO(BaseModel):
    id: UUID
    state: str
    validation_job_id: UUID | None
    source_id: UUID | None
    error: dict[str, Any] | None
    declared_bytes: int | None = None
    filename: str | None = None
    target_scope: Literal["PRIVATE", "SYSTEM"] = "PRIVATE"
    transfer: TransferDTO


def _base_fields(row: dict) -> dict:
    return {"id": row["id"], "state": row["state"], "validation_job_id": row["validation_job_id"],
            "source_id": row["source_id"], "error": row["error"], "declared_bytes": row["declared_bytes"],
            "filename": row["filename_display"], "target_scope": row.get("target_scope") or "PRIVATE"}


def _dto(row: dict) -> UploadDTO:
    return UploadDTO(**_base_fields(row), progress=row.get("progress"))


class PilotUploads:
    def __init__(self, db: PilotDB, storage: Callable[[], pilot_storage.PilotStorage]):
        self.db, self.storage, self.runtime = db, storage, PilotRuntime(db)

    def create(self, actor: ActorContext, inp: UploadIn, idempotency_key: str) -> UploadCreatedDTO:
        uid = require_user(actor)
        if inp.target_scope == "SYSTEM":
            from .pilot_operations import operations_read

            if not operations_read(uid):
                raise ServiceError("forbidden", "chỉ operator được upload nguồn mẫu SYSTEM", 403)
        store = self.storage()   # thiếu cấu hình S3 → 503 trước khi giữ chỗ
        input_hash = canonical_hash(inp.model_dump())
        with self.db.backend() as cur:
            profile = self.db.lock_profile(cur, uid)
            _, cfg = self.runtime._policy(cur, profile)
            cur.execute("SELECT * FROM rav_pilot_upload WHERE owner_id = %s AND idempotency_key = %s", (uid, idempotency_key))
            if row := cur.fetchone():
                if row["input_hash"] != input_hash:
                    raise ServiceError("idempotency_conflict", "Idempotency-Key đã dùng cho upload khác", 409)
            else:
                if inp.declared_bytes > cfg["max_video_bytes"]:
                    raise ServiceError("validation_error", f"Video vượt giới hạn {cfg['max_video_bytes'] / (1 << 30):.2f} GiB", 422)
                if cfg.get("vps_upload_enabled") or inp.preferred_transport == "VPS_DISK":
                    from .pilot_vps_disk import get_vps_upload_secret
                    get_vps_upload_secret()
                    if os.environ.get("RAV_RUNTIME_MODE") == "deploy" and not os.environ.get("RAV_VPS_UPLOAD_URL"):
                        raise ServiceError("dependency_unavailable", "thiếu RAV_VPS_UPLOAD_URL", 503)
                    cur.execute("SELECT pg_advisory_xact_lock(%s)", (0x56505355504C,))
                    cur.execute("SELECT 1 FROM rav_pilot_upload WHERE transfer_method='VPS_DISK' "
                                "AND (state='VALIDATING' OR (state='CREATED' AND capability_expires_at > now())) LIMIT 1")
                    if cur.fetchone():
                        raise ServiceError("upload_busy", "VPS đang nhận hoặc xử lý một upload; thử lại sau", 429)
                upload_id = uuid4()
                hold = pilot_storage.reserve(cur, uid, cfg, 2 * inp.declared_bytes, f"upload:{upload_id}",
                                             upload_id=upload_id, scope=inp.target_scope)
                cur.execute("INSERT INTO rav_pilot_upload (id, owner_id, reserved_source_id, idempotency_key, declared_bytes, "
                            "filename_display, target_scope, state, staging_key, capability_expires_at, storage_hold_id, input_hash) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s, 'CREATED', %s, now() + %s * interval '1 millisecond', %s, %s) RETURNING *",
                            (upload_id, uid, uuid4(), idempotency_key, inp.declared_bytes, inp.filename, inp.target_scope,
                             store.staging_key(uid, upload_id), cfg["upload_ttl_ms"], hold, input_hash))
                row = cur.fetchone()
                if cfg.get("vps_upload_enabled") or inp.preferred_transport == "VPS_DISK":
                    cur.execute("UPDATE rav_pilot_upload SET transfer_method='VPS_DISK' WHERE id=%s RETURNING *", (upload_id,))
                    row = cur.fetchone()
                elif cfg.get('multipart_upload_enabled') and inp.declared_bytes >= pilot_storage.MULTIPART_THRESHOLD:
                    cur.execute("UPDATE rav_pilot_upload SET transfer_method='MULTIPART' WHERE id=%s RETURNING *", (upload_id,))
                    row = cur.fetchone()
            cur.execute("SELECT GREATEST(1, floor(extract(epoch FROM capability_expires_at - now())))::int AS s "
                        "FROM rav_pilot_upload WHERE id = %s", (row["id"],))
            ttl = cur.fetchone()["s"]
        if row["state"] != "CREATED" or ttl <= 1:
            raise ServiceError("upload_invalid", f"upload đang {row['state']} hoặc đã hết hạn tải lên", 409)
        if row.get('transfer_method') == 'VPS_DISK':
            from datetime import UTC, datetime, timedelta

            from .pilot_vps_disk import DEFAULT_VPS_CHUNK_SIZE, create_vps_capability
            exp_dt = datetime.now(UTC) + timedelta(seconds=ttl)
            cap = create_vps_capability(row["id"], uid, row["declared_bytes"], exp_dt.timestamp())
            base_url = os.environ.get("RAV_VPS_UPLOAD_URL", "").rstrip("/")
            transfer_url = f"{base_url}/api/v2/vps-uploads/{row['id']}" if base_url else f"/api/v2/vps-uploads/{row['id']}"
            transfer = {
                'method': 'VPS_DISK',
                'url': transfer_url,
                'fields': {'capability': cap},
                'expires_at': exp_dt.isoformat(),
                'part_size': DEFAULT_VPS_CHUNK_SIZE,
                'part_count': (row['declared_bytes'] + DEFAULT_VPS_CHUNK_SIZE - 1) // DEFAULT_VPS_CHUNK_SIZE,
                'capability': cap,
            }
        elif row.get('transfer_method') == 'MULTIPART':
            from datetime import UTC, datetime, timedelta

            from .pilot_multipart import layout
            chunk, count = layout(row['declared_bytes'])
            transfer = {'method': 'MULTIPART', 'url': '', 'fields': {}, 'expires_at': (datetime.now(UTC)+timedelta(seconds=ttl)).isoformat(),
                        'part_size': chunk, 'part_count': count}
        else:
            transfer = store.presign_post(row["staging_key"], row["declared_bytes"], ttl)
        return UploadCreatedDTO(**_base_fields(row), transfer=TransferDTO(**transfer))

    def get(self, actor: ActorContext, upload_id: UUID) -> UploadDTO:
        uid = require_user(actor)
        with self.db.backend() as cur:
            self.db.lock_profile(cur, uid)
            cur.execute("SELECT * FROM rav_pilot_upload WHERE id = %s AND owner_id = %s", (upload_id, uid))
            row = cur.fetchone()
            if not row:
                raise ServiceError("not_found", f"không tìm thấy upload {upload_id}", 404)
            self._attach_progress_in_tx(cur, row)
        return _dto(row)

    def list(self, actor: ActorContext, limit: int = 20) -> list[UploadDTO]:
        """Liệt kê upload đang hiệu lực của owner để khôi phục tiến trình sau reload (§21.35)."""
        uid = require_user(actor)
        lim = max(1, min(int(limit), 50))
        with self.db.backend() as cur:
            self.db.lock_profile(cur, uid)
            cur.execute(
                "SELECT * FROM rav_pilot_upload WHERE owner_id = %s AND ("
                "state = 'VALIDATING' "
                "OR (state = 'CREATED' AND capability_expires_at > now()) "
                "OR (state = 'FAILED' AND updated_at > now() - interval '2 hours')"
                ") ORDER BY created_at DESC, id DESC LIMIT %s",
                (uid, lim),
            )
            rows = cur.fetchall()
            for r in rows:
                self._attach_progress_in_tx(cur, r)
        return [_dto(r) for r in rows]

    @staticmethod
    def _attach_progress_in_tx(cur, row: dict) -> None:
        if row["state"] == "VALIDATING" and row.get("validation_job_id"):
            cur.execute("SELECT * FROM rav_pilot_job WHERE id = %s", (row["validation_job_id"],))
            if jrow := cur.fetchone():
                detail = jrow.get("progress_detail") or {}
                prog: dict[str, Any] = {
                    "phase": jrow["phase"],
                    "done": jrow["done"],
                    "total": jrow["total"],
                }
                for k in ("fps", "elapsed_s", "eta_s", "detail"):
                    if k in detail and detail[k] is not None:
                        prog[k] = detail[k]
                row["progress"] = prog

    def complete(self, actor: ActorContext, upload_id: UUID) -> UploadDTO:
        """HEAD staging (ngoài transaction) rồi enqueue VALIDATE_UPLOAD; gọi lại trả cùng job."""
        current = self.get(actor, upload_id)
        if current.state != "CREATED":
            if current.state == "ABORTED":
                raise ServiceError("upload_invalid", "upload đã hủy", 409)
            return current
        uid = actor.user_id
        with self.db.backend() as cur:
            cur.execute("SELECT * FROM rav_pilot_upload WHERE id = %s", (upload_id,))
            row = cur.fetchone()
        vps_part_path = None
        staging_source = "S3_STAGING"
        if row.get('transfer_method') == 'MULTIPART':
            from .pilot_multipart import PilotMultipart
            PilotMultipart(self.db, self.storage).assemble(actor, upload_id)
            with timing("api.s3.head"):
                head = self.storage().head(row["staging_key"])
            if head is None:
                raise ServiceError("upload_invalid", "chưa thấy file trên vùng tải lên", 409)
            if head["ContentLength"] != row["declared_bytes"]:
                raise ServiceError("upload_invalid", "file lớn hơn dung lượng đã khai báo", 409)
            observed_bytes = head["ContentLength"]
        elif row.get('transfer_method') == 'VPS_DISK':
            from .pilot_vps_control import control
            status = control(row, "seal")
            if status["offset"] != row["declared_bytes"]:
                raise ServiceError("upload_invalid", f"chưa nhận đủ file trên đĩa VPS ({status['offset']}/{row['declared_bytes']})", 409)
            observed_bytes = status["offset"]
            staging_source = "VPS_DISK"
        else:
            with timing("api.s3.head"):
                head = self.storage().head(row["staging_key"])
            if head is None:
                raise ServiceError("upload_invalid", "chưa thấy file trên vùng tải lên", 409)
            if head["ContentLength"] > row["declared_bytes"]:
                raise ServiceError("upload_invalid", "file lớn hơn dung lượng đã khai báo", 409)
            observed_bytes = head["ContentLength"]
        with self.db.backend() as cur:
            profile = self.db.lock_profile(cur, uid)
            _, cfg = self.runtime._policy(cur, profile)
            cur.execute("SELECT * FROM rav_pilot_upload WHERE id = %s AND owner_id = %s FOR UPDATE", (upload_id, uid))
            up = cur.fetchone()
            if up["state"] != "CREATED":
                return _dto(up)
            cur.execute("SELECT capability_expires_at > now() AS alive FROM rav_pilot_upload WHERE id=%s", (upload_id,))
            if not cur.fetchone()["alive"]:
                raise ServiceError("upload_invalid", "upload đã hết hạn tải lên", 409)
            bounds = {k: cfg[k] for k in ("max_video_bytes", "max_duration_ms", "max_width", "max_height", "max_decoded_pixels")}
            frames = pilot_frames.bounds(cfg, "VALIDATE_UPLOAD")   # ảnh lưu sẵn tách cùng job (§21.20)
            target_scope = up.get("target_scope") or "PRIVATE"
            job = self.runtime.enqueue_in_tx(cur, uid, EnqueueSpec(
                kind="VALIDATE_UPLOAD", resource_id=upload_id, idempotency_key=f"upload:{upload_id}",
                payload={"upload_id": str(upload_id), "owner_id": str(uid), "staging_key": up["staging_key"],
                         "declared_bytes": up["declared_bytes"], "reserved_source_id": str(up["reserved_source_id"]),
                         "filename": up["filename_display"], "target_scope": target_scope, "bounds": bounds, "frames": frames,
                         "staging_source": staging_source, "vps_part_path": vps_part_path}), [])
            cur.execute("UPDATE rav_pilot_storage_hold SET staging_observed_bytes = %s WHERE id = %s",
                        (observed_bytes, up["storage_hold_id"]))
            cur.execute("UPDATE rav_pilot_upload SET state = 'VALIDATING', validation_job_id = %s, vps_part_path = %s, updated_at = now() "
                        "WHERE id = %s RETURNING *", (job.id, vps_part_path, upload_id))
            return _dto(cur.fetchone())

    def abort(self, actor: ActorContext, upload_id: UUID) -> UploadDTO:
        from .pilot_multipart import upload_lock
        with upload_lock(self.db, upload_id) as conn:
            result = self._abort(actor, upload_id, conn)
            row = conn.execute('SELECT * FROM rav_pilot_upload WHERE id=%s', (upload_id,)).fetchone()
            if row.get('transfer_method') == 'MULTIPART':
                self.storage().abort_multipart_key(row['staging_key'])
            elif row.get('transfer_method') == 'VPS_DISK' and row['state'] == 'ABORTED':
                from .pilot_vps_control import control
                control(row, "cleanup")
            return result

    def _abort(self, actor: ActorContext, upload_id: UUID, conn) -> UploadDTO:
        uid = require_user(actor)
        with conn.transaction(), conn.cursor() as cur:
            self.db.lock_profile(cur, uid)
            cur.execute("SELECT * FROM rav_pilot_upload WHERE id = %s AND owner_id = %s FOR UPDATE", (upload_id, uid))
            up = cur.fetchone()
            if not up:
                raise ServiceError("not_found", f"không tìm thấy upload {upload_id}", 404)
            if up["state"] in ("READY", "FAILED"):
                raise ServiceError("upload_invalid", f"upload đã {up['state']}; muốn bỏ video thì xóa nguồn", 409)
            if up["state"] == "CREATED":
                _close(cur, up, "ABORTED", None)
            elif up["state"] == "VALIDATING":
                cur.execute("SELECT * FROM rav_pilot_job WHERE id = %s FOR UPDATE", (up["validation_job_id"],))
                job = cur.fetchone()
                if job["state"] in ("QUEUED", "WAITING_RESOURCE"):
                    cur.execute("SELECT 1 FROM rav_pilot_job_attempt WHERE job_id=%s AND usage_state='UNKNOWN' LIMIT 1",
                                (job["id"],))
                    if cur.fetchone():
                        from psycopg.types.json import Jsonb

                        cur.execute("UPDATE rav_pilot_job SET error=%s,state_version=state_version+1,updated_at=now() "
                                    "WHERE id=%s", (Jsonb({"code": "cancel_requested", "message": "đang chờ đối soát",
                                                          "retryable": False}), job["id"]))
                    else:
                        self.runtime._terminate(cur, job, "CANCELLED", None)   # hook đóng upload ABORTED
                elif job["state"] == "RUNNING":
                    cur.execute("UPDATE rav_pilot_job SET state = 'CANCEL_REQUESTED', state_version = state_version + 1, "
                                "phase = 'cancel_requested', updated_at = now() WHERE id = %s", (job["id"],))
            cur.execute("SELECT * FROM rav_pilot_upload WHERE id = %s", (upload_id,))
            return _dto(cur.fetchone())

    def sweep_staging(self) -> list[UUID]:
        """Xóa object staging đã quá hạn + grace (không còn POST lại được) và trả phần dung lượng staging."""
        with self.db.backend() as cur:
            cur.execute("SELECT u.id, u.owner_id, u.staging_key FROM rav_pilot_upload u JOIN rav_pilot_storage_hold h ON h.id = u.storage_hold_id "
                        "JOIN rav_pilot_profile p ON p.user_id = u.owner_id JOIN rav_pilot_quota_policy q ON q.id = p.policy_id "
                        "WHERE h.state = 'OPEN' AND u.state IN ('CREATED', 'READY', 'FAILED', 'ABORTED') AND u.capability_expires_at "
                        "+ (q.config ->> 'staging_grace_ms')::bigint * interval '1 millisecond' < now()")
            due = cur.fetchall()
        done = []
        from .pilot_multipart import upload_lock
        for d in due:
            with upload_lock(self.db, d["id"]) as conn:
                with conn.transaction(), conn.cursor() as cur:
                    cur.execute("SELECT user_id FROM rav_pilot_profile WHERE user_id=%s FOR UPDATE", (d["owner_id"],))
                    cur.execute("SELECT * FROM rav_pilot_upload WHERE id=%s FOR UPDATE", (d["id"],))
                    up = cur.fetchone()
                    if up["state"] not in ("CREATED", "READY", "FAILED", "ABORTED"):
                        continue
                    job_id = up["validation_job_id"]
                    if job_id and pilot_objects.pending_in_tx(cur, job_id=job_id):
                        continue
                    if up["state"] == "CREATED":
                        _close(cur, up, "ABORTED", {"code": "upload_expired", "message": "hết hạn tải lên", "retryable": False})
                if up.get('transfer_method') == 'MULTIPART':
                    self.storage().abort_multipart_key(d['staging_key'])
                elif up.get('transfer_method') == 'VPS_DISK':
                    from .pilot_vps_control import control
                    control(up, "cleanup")
                self.storage().delete(d["staging_key"])   # S3 ngoài transaction; DB chỉ ghi sau khi xóa xong
                if self.storage().head(d['staging_key']) is not None:
                    continue
                with conn.transaction(), conn.cursor() as cur:
                    cur.execute("SELECT user_id FROM rav_pilot_profile WHERE user_id=%s FOR UPDATE", (d["owner_id"],))
                    cur.execute("SELECT * FROM rav_pilot_upload WHERE id = %s FOR UPDATE", (d["id"],))
                    up = cur.fetchone()
                    if up["state"] in ("READY", "FAILED", "ABORTED") and (not up["validation_job_id"] or
                            not pilot_objects.pending_in_tx(cur, job_id=up["validation_job_id"])):
                        pilot_storage.settle(cur, up["storage_hold_id"], final=True)
                done.append(d["id"])
        return done


def _close(cur, up: dict, state: str, error: dict | None) -> None:
    """Upload lỗi/hủy: phần canonical chờ receipt cleanup; staging chờ hết capability."""
    from psycopg.types.json import Jsonb

    if not up["validation_job_id"] or not pilot_objects.pending_in_tx(cur, job_id=up["validation_job_id"]):
        pilot_objects.release_canonical_in_tx(cur, up)
    cur.execute("UPDATE rav_pilot_upload SET state = %s, error = %s, updated_at = now() WHERE id = %s",
                (state, Jsonb(error) if error else None, up["id"]))


# ------------------------------------------------------------------------------
# Hook worker (gọi từ PilotRuntime trong cùng transaction publish/kết thúc)
# ------------------------------------------------------------------------------
def publish_in_tx(cur, job: dict, lease, outputs: dict) -> None:
    """outputs["upload"] = {sha256, size_bytes, canonical_key|None, core_source_id, ext, metadata, frames|None}; `frames` là bộ ảnh
    đủ (§21.20), None khi nguồn đích đã có bộ READY lúc worker kiểm."""
    from psycopg import errors as pg_errors
    from psycopg.types.json import Jsonb

    v = outputs["upload"]
    upload_id = UUID(job["payload"]["upload_id"])
    cur.execute("SELECT * FROM rav_pilot_upload WHERE id = %s FOR UPDATE", (upload_id,))
    up = cur.fetchone()
    if up["state"] != "VALIDATING" or up["validation_job_id"] != job["id"]:
        raise ServiceError("stale_input", "upload không còn chờ xác minh bởi job này", 409)
    if not 0 < v["size_bytes"] <= up["declared_bytes"] or not re.fullmatch(r"[0-9a-f]{64}", v["sha256"]) \
            or v["core_source_id"] != v["sha256"][:16]:
        raise ServiceError("result_invalid", "kết quả xác minh upload không hợp lệ", 422)
    target_scope = up.get("target_scope") or "PRIVATE"

    def existing() -> UUID | None:
        if target_scope == "SYSTEM":
            cur.execute("SELECT id FROM rav_pilot_source_asset WHERE core_source_id = %s AND scope = 'SYSTEM' "
                        "AND availability = 'READY'", (v["core_source_id"],))
        else:
            cur.execute("SELECT id FROM rav_pilot_source_asset WHERE owner_id = %s AND core_source_id = %s AND scope = 'PRIVATE' "
                        "AND availability = 'READY'", (up["owner_id"], v["core_source_id"]))
        row = cur.fetchone()
        return row["id"] if row else None

    source_id = existing()
    if source_id is None:
        try:
            with cur.connection.transaction():   # savepoint: upload song song cùng bytes → UniqueViolation
                if target_scope == "SYSTEM":
                    cur.execute("INSERT INTO rav_pilot_source_asset (id, core_source_id, kind, scope, owner_id, protected, published, "
                                "name, raw_key, raw_sha256, size_bytes, provenance, metadata, availability) "
                                "VALUES (%s, %s, 'VIDEO', 'SYSTEM', NULL, true, false, %s, %s, %s, %s, %s, %s, 'READY')",
                                (up["reserved_source_id"], v["core_source_id"], up["filename_display"],
                                 v["canonical_key"], v["sha256"], v["size_bytes"],
                                 Jsonb({"upload_id": str(upload_id), "operator_id": str(up["owner_id"])}), Jsonb(v["metadata"])))
                else:
                    cur.execute("INSERT INTO rav_pilot_source_asset (id, core_source_id, kind, scope, owner_id, protected, name, raw_key, "
                                "raw_sha256, size_bytes, provenance, metadata, availability) "
                                "VALUES (%s, %s, 'VIDEO', 'PRIVATE', %s, false, %s, %s, %s, %s, %s, %s, 'READY')",
                                (up["reserved_source_id"], v["core_source_id"], up["owner_id"], up["filename_display"],
                                 v["canonical_key"], v["sha256"], v["size_bytes"], Jsonb({"upload_id": str(upload_id)}),
                                 Jsonb(v["metadata"])))
            source_id = up["reserved_source_id"]
            pilot_storage.settle(cur, up["storage_hold_id"], persist=v["size_bytes"],
                                 release=up["declared_bytes"] - v["size_bytes"])
        except pg_errors.UniqueViolation:
            source_id = existing()
            if source_id is None:   # bản cùng nội dung đang chờ xóa
                raise ServiceError("source_unavailable", "video cùng nội dung đang bị xóa; thử lại sau", 409) from None
    if source_id != up["reserved_source_id"]:
        # Canonical thừa giữ dung lượng tới khi receipt cleanup xác nhận xóa S3.
        if not pilot_objects.pending_in_tx(cur, job_id=job["id"]):
            pilot_objects.release_canonical_in_tx(cur, up)
    if v.get("frames"):   # bộ ảnh đủ của nguồn đích; nguồn đã có bộ READY thì bộ này chờ cleanup (§21.20)
        cur.execute("SELECT * FROM rav_pilot_source_asset WHERE id = %s", (source_id,))
        source = cur.fetchone()
        if source["raw_sha256"] != v["sha256"]:
            raise ServiceError("result_invalid", "bộ ảnh không thuộc nội dung nguồn", 422)
        pilot_frames.publish_set_in_tx(cur, job, lease, source, v["frames"])
    elif not pilot_frames.ready_set_in_tx(cur, source_id, v["sha256"]):
        raise ServiceError("result_invalid", "upload thiếu bộ ảnh lưu sẵn", 422)
    cur.execute("UPDATE rav_pilot_upload SET state = 'READY', source_id = %s, canonical_key = %s, verified_sha256 = %s, "
                "verified_bytes = %s, updated_at = now() WHERE id = %s",
                (source_id, v.get("canonical_key"), v["sha256"], v["size_bytes"], upload_id))


def finish_in_tx(cur, job: dict, state: str, error: dict | None) -> None:
    upload_id = UUID(job["payload"]["upload_id"])
    cur.execute("SELECT * FROM rav_pilot_upload WHERE id = %s FOR UPDATE", (upload_id,))
    up = cur.fetchone()
    if up and up["state"] in ("CREATED", "VALIDATING"):
        _close(cur, up, "ABORTED" if state == "CANCELLED" else "FAILED", error)
