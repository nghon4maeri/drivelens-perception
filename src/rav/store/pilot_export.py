"""Quản lý vòng đời Export và Owner Confirmation Gate (CONTRACTS §21.8).

Vòng đời: REQUESTED -> PROCESSING -> NEEDS_CONFIRMATION -> FINALIZING -> READY
Tích hợp:
- Bảng rav_pilot_export, rav_pilot_export_review_receipt, rav_pilot_export_confirmation
- Hook runtime publish_in_tx, finish_in_tx cho EXPORT_BUILD và EXPORT_FINALIZE
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from ..actor import ActorContext
from ..errors import ServiceError
from .pilot_db import Page, PilotDB, _In, _page_args, canonical_hash, require_user
from .pilot_runtime import EnqueueSpec, PilotRuntime
from .pilot_storage import PilotStorage

REVIEW_EXPIRY_HOURS = 24


class ExportDTO(BaseModel):
    id: UUID
    snapshot_id: UUID
    state: str
    state_version: int
    build_job_id: UUID | None = None
    finalize_job_id: UUID | None = None
    payload_hash: str | None = None
    review_receipt_id: UUID | None = None
    review_expires_at: str | None = None
    n_members: int
    output_size_bytes: int | None = None
    error: dict[str, Any] | None = None
    created_at: str
    updated_at: str


class ReviewMemberDTO(BaseModel):
    ordinal: int
    unit_id: str
    output_sha256: str
    output_relpath: str
    preview_url: str | None = None


class ReviewDTO(BaseModel):
    export_id: UUID
    review_receipt_id: UUID
    payload_hash: str
    review_expires_at: str
    n_members: int
    report_url: str | None = None
    members: list[ReviewMemberDTO] = Field(default_factory=list)
    source_identity_verified: bool = False


class ConfirmExportIn(_In):
    payload_hash: str
    review_receipt_id: UUID


class DownloadDTO(BaseModel):
    export_id: UUID
    payload_hash: str
    sha256: str
    size_bytes: int
    filename: str
    url: str
    expires_at: str


def _row_to_export_dto(row: dict[str, Any], n_members: int = 0) -> ExportDTO:
    return ExportDTO(
        id=row["id"],
        snapshot_id=row["snapshot_id"],
        state=row["state"],
        state_version=row["state_version"],
        build_job_id=row.get("build_job_id"),
        finalize_job_id=row.get("finalize_job_id"),
        payload_hash=row.get("payload_hash"),
        review_receipt_id=row.get("review_receipt_id"),
        review_expires_at=row["review_expires_at"].isoformat() if row.get("review_expires_at") else None,
        n_members=n_members or row.get("n_members", 0),
        output_size_bytes=row.get("output_size_bytes"),
        error=row.get("error"),
        created_at=row["created_at"].isoformat(),
        updated_at=row["updated_at"].isoformat(),
    )


def read_pii_cache(db: PilotDB, owner_id: UUID, hashes: list[str]) -> dict[str, dict]:
    """Private READY artifacts only; metadata for requested frame identities, never another owner's cache."""
    if not hashes:
        return {}
    with db.backend() as cur:
        cur.execute("SELECT DISTINCT ON (input_hash) * FROM rav_pilot_artifact "
                    "WHERE owner_id=%s AND scope='PRIVATE' AND kind='EXPORT_PREVIEW' AND variant='PII_REDACTED' "
                    "AND state='READY' AND input_hash=ANY(%s) ORDER BY input_hash,created_at DESC,id DESC", (owner_id, hashes))
        return {r["input_hash"]: r for r in cur.fetchall()}


def verified_frame_refs(refs: list[dict], n_members: int) -> bool:
    """Old ordinal-based exports cannot be confirmed/downloaded as if their frame identity was verified."""
    images = [r for r in refs if r.get("role") == "member_image"]
    return n_members > 0 and len(images) == n_members and all(r.get("frame_identity") == "exact-source-v2" for r in images)


def _publish_artifacts(cur, job, lease, artifacts: list[dict]) -> None:
    """Use existing fenced object receipts/accounting for previews, PII cache and durable dataset ZIP."""
    from psycopg.types.json import Jsonb

    from . import pilot_objects, pilot_storage

    for a in artifacts:
        pilot_objects.accept_in_tx(cur, lease, a["key"], a["sha256"], a["size_bytes"])
        cur.execute("INSERT INTO rav_pilot_artifact (id,owner_id,scope,source_id,job_id,attempt_id,kind,variant,input_hash,"
                    "object_key,sha256,size_bytes,state,policy_version,manifest) "
                    "VALUES (%s,%s,'PRIVATE',%s,%s,%s,%s,%s,%s,%s,%s,%s,'READY',%s,%s)",
                    (UUID(a["id"]), job["owner_id"], UUID(a["source_id"]) if a.get("source_id") else None,
                     job["id"], lease.attempt_id, a["kind"], a["variant"], a["input_hash"], a["key"], a["sha256"],
                     a["size_bytes"], job["policy_version"], Jsonb(a.get("manifest") or {})))
    pilot_storage.persist_job_bytes(cur, job["id"], sum(a["size_bytes"] for a in artifacts))


class PilotExport:
    def __init__(self, db: PilotDB, storage: PilotStorage | None = None):
        self.db = db
        self._storage = storage

    @property
    def storage(self) -> PilotStorage:
        if self._storage is None:
            self._storage = PilotStorage.from_env()
        return self._storage

    def request_export(self, actor: ActorContext, snapshot_id: UUID, idempotency_key: str | None = None) -> ExportDTO:
        uid = require_user(actor)
        export_id = uuid4()
        now = datetime.now(UTC)

        with self.db.backend() as cur:
            # 1. Kiểm tra snapshot hợp lệ và thuộc sở hữu
            cur.execute(
                "SELECT id, session_id, decision_seq, proposal_cutoff_n, members_hash, n_members, context, report_basis "
                "FROM rav_pilot_snapshot WHERE id = %s AND owner_id = %s",
                (snapshot_id, uid),
            )
            snap = cur.fetchone()
            if not snap:
                raise ServiceError("not_found", f"không tìm thấy snapshot {snapshot_id}", 404)

            # Idempotency check
            if idempotency_key:
                cur.execute(
                    "SELECT * FROM rav_pilot_export WHERE owner_id = %s AND idempotency_key = %s",
                    (uid, idempotency_key),
                )
                existing = cur.fetchone()
                if existing:
                    return _row_to_export_dto(existing, snap["n_members"])

            # Lấy danh sách snapshot members
            cur.execute(
                "SELECT ord, unit_id, source_id, core_source_id, source_kind, provenance "
                "FROM rav_pilot_snapshot_member WHERE snapshot_id = %s AND owner_id = %s ORDER BY ord ASC",
                (snapshot_id, uid),
            )
            members = cur.fetchall()
            if not members:
                raise ServiceError("validation_error", "snapshot không có members", 422)

            input_dict = {
                "snapshot_id": str(snapshot_id),
                "members_hash": snap["members_hash"],
                "decision_seq": snap["decision_seq"],
                "proposal_cutoff_n": snap["proposal_cutoff_n"],
            }
            input_h = canonical_hash(input_dict)

            # 2. Tạo bản ghi export REQUESTED
            cur.execute(
                "INSERT INTO rav_pilot_export (id, owner_id, snapshot_id, snapshot_decision_seq, "
                "snapshot_proposal_cutoff_n, input_hash, state, state_version, idempotency_key, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'REQUESTED', 1, %s, %s, %s) RETURNING *",
                (
                    export_id,
                    uid,
                    snapshot_id,
                    snap["decision_seq"],
                    snap["proposal_cutoff_n"],
                    input_h,
                    idempotency_key,
                    now,
                    now,
                ),
            )
            export_row = cur.fetchone()

        # 3. Enqueue job EXPORT_BUILD trên queue CPU
        runtime = PilotRuntime(self.db)
        members_data = [
            {
                "ord": m["ord"],
                "unit_id": m["unit_id"],
                "source_id": str(m["source_id"]),
                "core_source_id": m["core_source_id"],
                "source_kind": m["source_kind"],
                "provenance": m["provenance"],
            }
            for m in members
        ]
        build_job = runtime.enqueue(
            actor,
            EnqueueSpec(
                kind="EXPORT_BUILD",
                resource_id=export_id,
                payload={
                    "export_id": str(export_id),
                    "owner_id": str(uid),
                    "snapshot_id": str(snapshot_id),
                    "decision_seq": snap["decision_seq"],
                    "proposal_cutoff_n": snap["proposal_cutoff_n"],
                    "members": members_data,
                    "report_basis": snap["report_basis"],
                },
                idempotency_key=f"build:{export_id}",
            ),
        )

        with self.db.backend() as cur:
            cur.execute(
                "UPDATE rav_pilot_export SET build_job_id = %s WHERE id = %s",
                (build_job.id, export_id),
            )
            export_row["build_job_id"] = build_job.id

        return _row_to_export_dto(export_row, snap["n_members"])

    def get_export(self, actor: ActorContext, export_id: UUID) -> ExportDTO:
        uid = require_user(actor)
        with self.db.as_user(uid) as cur:
            cur.execute(
                "SELECT e.*, s.n_members FROM rav_pilot_export e "
                "JOIN rav_pilot_snapshot s ON s.id = e.snapshot_id "
                "WHERE e.id = %s",
                (export_id,),
            )
            row = cur.fetchone()
            if not row:
                raise ServiceError("not_found", f"không tìm thấy export {export_id}", 404)
            return _row_to_export_dto(row, row["n_members"])

    def list_exports(self, actor: ActorContext, session_id: UUID, cursor: str | None = None, limit: int = 20) -> Page:
        uid = require_user(actor)
        offset, limit = _page_args(cursor, limit)
        with self.db.as_user(uid) as cur:
            cur.execute("SELECT id FROM rav_pilot_session WHERE id = %s", (session_id,))
            if not cur.fetchone():
                raise ServiceError("not_found", "không tìm thấy phiên", 404)
            cur.execute("SELECT count(*) AS n FROM rav_pilot_export e JOIN rav_pilot_snapshot s ON s.id = e.snapshot_id "
                        "WHERE s.session_id = %s", (session_id,))
            total = cur.fetchone()["n"]
            cur.execute("SELECT e.*, s.n_members FROM rav_pilot_export e JOIN rav_pilot_snapshot s ON s.id = e.snapshot_id "
                        "WHERE s.session_id = %s ORDER BY e.created_at DESC, e.id DESC LIMIT %s OFFSET %s",
                        (session_id, limit, offset))
            items = [_row_to_export_dto(row, row["n_members"]) for row in cur.fetchall()]
        return Page(items=items, total=total, next_cursor=str(offset + limit) if offset + limit < total else None)

    def get_review(self, actor: ActorContext, export_id: UUID) -> ReviewDTO:
        uid = require_user(actor)
        with self.db.as_user(uid) as cur:
            cur.execute(
                "SELECT e.*, s.n_members FROM rav_pilot_export e "
                "JOIN rav_pilot_snapshot s ON s.id = e.snapshot_id "
                "WHERE e.id = %s",
                (export_id,),
            )
            export_row = cur.fetchone()
            if not export_row:
                raise ServiceError("not_found", f"không tìm thấy export {export_id}", 404)

            state = export_row["state"]
            if state not in ("NEEDS_CONFIRMATION", "FINALIZING", "READY"):
                raise ServiceError(
                    "export_not_reviewable",
                    f"export đang ở trạng thái {state}, chưa sẵn sàng để review",
                    409,
                )

            receipt_id = export_row.get("review_receipt_id")
            if not receipt_id:
                raise ServiceError("not_found", "chưa có receipt xem trước cho export", 404)

            cur.execute(
                "SELECT * FROM rav_pilot_export_review_receipt WHERE id = %s",
                (receipt_id,),
            )
            receipt = cur.fetchone()
            if not receipt:
                raise ServiceError("not_found", f"không tìm thấy review receipt {receipt_id}", 404)

        artifact_refs = receipt.get("artifact_refs") or []
        report_url = None
        members: list[ReviewMemberDTO] = []

        ttl_s = 3600
        for ref in artifact_refs:
            role = ref.get("role")
            key = ref.get("key")
            if role == "report_html" and key:
                report_url = self.storage.presign_get(key, ttl_s)
            elif role == "member_image":
                preview_url = self.storage.presign_get(key, ttl_s) if key else None
                members.append(
                    ReviewMemberDTO(
                        ordinal=ref.get("ordinal", 0),
                        unit_id=ref.get("unit_id", ""),
                        output_sha256=ref.get("output_sha256", ""),
                        output_relpath=ref.get("output_relpath", ""),
                        preview_url=preview_url,
                    )
                )

        members.sort(key=lambda m: m.ordinal)

        return ReviewDTO(
            export_id=export_id,
            review_receipt_id=receipt["id"],
            payload_hash=receipt["payload_hash"],
            review_expires_at=receipt["expires_at"].isoformat(),
            n_members=receipt["n_members"],
            report_url=report_url,
            members=members,
            source_identity_verified=verified_frame_refs(artifact_refs, receipt["n_members"]),
        )

    def confirm_export(
        self,
        actor: ActorContext,
        export_id: UUID,
        payload_hash: str,
        review_receipt_id: UUID,
        idempotency_key: str | None = None,
    ) -> ExportDTO:
        uid = require_user(actor)
        now = datetime.now(UTC)

        with self.db.backend() as cur:
            cur.execute(
                "SELECT e.*, s.n_members FROM rav_pilot_export e "
                "JOIN rav_pilot_snapshot s ON s.id = e.snapshot_id "
                "WHERE e.id = %s AND e.owner_id = %s FOR UPDATE",
                (export_id, uid),
            )
            export_row = cur.fetchone()
            if not export_row:
                raise ServiceError("not_found", f"không tìm thấy export {export_id}", 404)

            state = export_row["state"]
            cur.execute("SELECT artifact_refs FROM rav_pilot_export_review_receipt WHERE id=%s AND export_id=%s AND owner_id=%s",
                        (export_row.get("review_receipt_id"), export_id, uid))
            source_receipt = cur.fetchone()
            if source_receipt and not verified_frame_refs(source_receipt["artifact_refs"], export_row["n_members"]):
                raise ServiceError("export_source_unverified", "lần xuất cũ chưa xác minh đúng frame; tạo lần xuất mới từ snapshot", 409)
            if state == "FINALIZING" or state == "READY":
                return _row_to_export_dto(export_row, export_row["n_members"])
            if state != "NEEDS_CONFIRMATION":
                raise ServiceError(
                    "conflict",
                    f"export đang ở trạng thái {state}, không thể xác nhận",
                    409,
                )

            # Kiểm tra payload_hash và review_receipt_id
            if export_row["payload_hash"] != payload_hash:
                raise ServiceError("payload_mismatch", "payload_hash không khớp với bản đã review", 409)

            if export_row["review_receipt_id"] != review_receipt_id:
                raise ServiceError("validation_error", "review_receipt_id không hợp lệ", 422)

            if export_row["review_expires_at"] and export_row["review_expires_at"] < now:
                raise ServiceError("review_expired", "phiên review đã hết hạn; vui lòng tạo export mới", 409)

            # Ghi bản ghi confirmation
            confirm_id = uuid4()
            cur.execute(
                "INSERT INTO rav_pilot_export_confirmation (id, export_id, owner_id, payload_hash, "
                "review_receipt_id, actor_id, confirmed_at, idempotency_key) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (export_id, payload_hash) DO NOTHING",
                (confirm_id, export_id, uid, payload_hash, review_receipt_id, uid, now, idempotency_key),
            )

            # Cập nhật state sang FINALIZING
            cur.execute(
                "UPDATE rav_pilot_export SET state = 'FINALIZING', state_version = state_version + 1, "
                "updated_at = %s WHERE id = %s RETURNING *",
                (now, export_id),
            )
            export_row = cur.fetchone()

        # Enqueue EXPORT_FINALIZE
        runtime = PilotRuntime(self.db)
        finalize_job = runtime.enqueue(
            actor,
            EnqueueSpec(
                kind="EXPORT_FINALIZE",
                resource_id=export_id,
                payload={
                    "export_id": str(export_id),
                    "owner_id": str(uid),
                    "payload_hash": payload_hash,
                    "review_receipt_id": str(review_receipt_id),
                },
                idempotency_key=f"finalize:{export_id}",
            ),
        )

        with self.db.backend() as cur:
            cur.execute(
                "UPDATE rav_pilot_export SET finalize_job_id = %s WHERE id = %s",
                (finalize_job.id, export_id),
            )
            export_row["finalize_job_id"] = finalize_job.id

        return _row_to_export_dto(export_row, export_row.get("n_members", 0))

    def get_download(self, actor: ActorContext, export_id: UUID) -> DownloadDTO:
        uid = require_user(actor)
        with self.db.as_user(uid) as cur:
            cur.execute("SELECT * FROM rav_pilot_export WHERE id = %s", (export_id,))
            row = cur.fetchone()
            if not row:
                raise ServiceError("not_found", f"không tìm thấy export {export_id}", 404)

            state = row["state"]
            if state != "READY":
                raise ServiceError("export_not_ready", f"export chưa sẵn sàng tải (trạng thái {state})", 409)

            cur.execute("SELECT artifact_refs,n_members FROM rav_pilot_export_review_receipt WHERE id=%s", (row["review_receipt_id"],))
            source_receipt = cur.fetchone()
            if not source_receipt or not verified_frame_refs(source_receipt["artifact_refs"], source_receipt["n_members"]):
                raise ServiceError("export_source_unverified", "gói cũ chưa xác minh đúng frame; tạo lần xuất mới từ snapshot", 409)

            output_key = row.get("output_key")
            if not output_key:
                raise ServiceError("result_invalid", "export READY nhưng thiếu output_key", 500)

            ttl_s = 3600
            url = self.storage.presign_get(output_key, ttl_s)
            expires_at = (datetime.now(UTC) + timedelta(seconds=ttl_s)).isoformat()
            filename = f"export_{str(export_id)[:8]}.zip"

            return DownloadDTO(
                export_id=export_id,
                payload_hash=row["payload_hash"] or "",
                sha256=row["output_sha256"] or "",
                size_bytes=row["output_size_bytes"] or 0,
                filename=filename,
                url=url,
                expires_at=expires_at,
            )


# ------------------------------------------------------------------------------
# Runtime Hooks (gọi từ PilotRuntime trong transaction publish/finish)
# ------------------------------------------------------------------------------
def publish_in_tx(cur, job: dict[str, Any], lease, outputs: dict[str, Any]) -> None:
    from psycopg.types.json import Jsonb

    kind = job["kind"]
    p = job.get("payload") or {}
    if "export_id" not in p:
        return None

    export_id = UUID(p["export_id"])
    owner_id = job["owner_id"]
    now = datetime.now(UTC)

    _publish_artifacts(cur, job, lease, outputs.get("artifacts", []))

    if kind == "EXPORT_BUILD":
        if "payload_hash" not in outputs:
            return None
        payload_hash = outputs["payload_hash"]
        review_receipt_id = uuid4()
        review_expires_at = now + timedelta(hours=REVIEW_EXPIRY_HOURS)
        artifact_refs = outputs.get("artifact_refs", [])
        n_members = outputs.get("n_members", 0)

        # 1. Tạo review receipt
        cur.execute(
            "INSERT INTO rav_pilot_export_review_receipt (id, export_id, owner_id, payload_hash, "
            "report_core_hash, manifest_hash, artifact_refs, n_members, issued_at, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                review_receipt_id,
                export_id,
                owner_id,
                payload_hash,
                outputs.get("report_core_hash", payload_hash),
                outputs.get("manifest_hash", payload_hash),
                Jsonb(artifact_refs),
                n_members,
                now,
                review_expires_at,
            ),
        )

        # 2. Chuyển state export sang NEEDS_CONFIRMATION
        cur.execute(
            "UPDATE rav_pilot_export SET state = 'NEEDS_CONFIRMATION', state_version = state_version + 1, "
            "payload_hash = %s, review_receipt_id = %s, review_expires_at = %s, updated_at = %s "
            "WHERE id = %s",
            (payload_hash, review_receipt_id, review_expires_at, now, export_id),
        )
        return

    if kind == "EXPORT_FINALIZE":
        if "output_key" not in outputs:
            return None
        output_key = outputs["output_key"]
        output_size = outputs["output_size_bytes"]
        output_sha = outputs["output_sha256"]

        # Chuyển state export sang READY
        cur.execute(
            "UPDATE rav_pilot_export SET state = 'READY', state_version = state_version + 1, "
            "output_key = %s, output_size_bytes = %s, output_sha256 = %s, updated_at = %s "
            "WHERE id = %s",
            (output_key, output_size, output_sha, now, export_id),
        )
        return

    raise ServiceError("result_invalid", f"hook export không hỗ trợ kind {kind}", 500)


def finish_in_tx(cur, job: dict[str, Any], state: str, error: dict[str, Any] | None) -> None:
    from psycopg.types.json import Jsonb

    if state in ("FAILED", "CANCELLED"):
        p = job["payload"]
        if "export_id" in p:
            export_id = UUID(p["export_id"])
            now = datetime.now(UTC)
            cur.execute(
                "UPDATE rav_pilot_export SET state = %s, state_version = state_version + 1, "
                "error = %s, updated_at = %s WHERE id = %s AND state NOT IN ('READY', 'CANCELLED')",
                (state, Jsonb(error) if error else None, now, export_id),
            )
