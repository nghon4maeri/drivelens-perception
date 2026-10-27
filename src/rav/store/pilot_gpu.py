"""Ngân sách GPU Kaggle toàn cục + trạng thái launch session của attempt (CONTRACTS §21.14, runtime spec §3.2/§7.1, #94).

- Snapshot quota (bất biến) do worker đọc Kaggle SDK hoặc operator ghi; claim GPU chỉ khi snapshot của account còn tươi và đủ ngân sách:
  `remaining − usage GPU sau baseline − gpu_hold OPEN − gpu_headroom_ms ≥ ước lượng`.
- `gpu_hold` mở lúc claim (cùng transaction), chốt FINAL khi settle attempt.
- Launch: NONE → INTENT → ATTACHED | LAUNCH_UNCERTAIN; ATTACHED → STOPPED | STOP_PENDING; supervisor chốt STOPPED khi provider xác minh không còn session.
Chỉ DB, không import ML/SDK; gọi từ runtime (claim/settle) và worker GPU.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from ..errors import ServiceError

LAUNCH_OPEN = ("INTENT", "LAUNCH_UNCERTAIN", "ATTACHED", "STOP_PENDING")


def record_snapshot(db, *, provider_account: str, source: str, remaining_ms: int, reset_at: datetime | None,
                    evidence_ref: str, evidence: dict[str, Any], operator_id: UUID | None = None,
                    external_session_active: bool = False) -> UUID:
    """Ghi snapshot quota; baseline = seq usage receipt lớn nhất tại thời điểm ghi (usage sau đó trừ thêm)."""
    from psycopg.types.json import Jsonb

    if remaining_ms < 0:
        raise ServiceError("validation_error", "remaining_ms phải ≥ 0", 422)
    sid = uuid4()
    with db.backend() as cur:
        cur.execute("SELECT coalesce(max(seq), 0) AS s FROM rav_pilot_usage_receipt")
        baseline = cur.fetchone()["s"]
        cur.execute("INSERT INTO rav_pilot_gpu_budget_snapshot (id, provider_account, source, observed_at, remaining_ms, reset_at, "
                    "usage_baseline_seq, external_session_active, operator_id, evidence_ref, evidence) "
                    "VALUES (%s, %s, %s, now(), %s, %s, %s, %s, %s, %s, %s)",
                    (sid, provider_account, source, remaining_ms, reset_at, baseline, external_session_active, operator_id,
                     evidence_ref, Jsonb(evidence)))
    return sid


def admit_in_tx(cur, job: dict, slot: dict, cfg: dict) -> tuple[dict | None, str | None]:
    """(snapshot, None) nếu đủ ngân sách cho job trên account của slot; (None, waiting_reason) nếu không."""
    account = slot["account_or_host"]
    cur.execute("SELECT * FROM rav_pilot_gpu_budget_snapshot WHERE provider_account = %s "
                "AND observed_at >= now() - %s * interval '1 millisecond' ORDER BY observed_at DESC, id LIMIT 1",
                (account, cfg["gpu_snapshot_fresh_ms"]))
    snap = cur.fetchone()
    if not snap or snap["external_session_active"]:
        return None, "gpu_budget_unverified"
    cur.execute("SELECT coalesce(sum(r.actual_ms), 0) AS used FROM rav_pilot_usage_receipt r "
                "JOIN rav_pilot_gpu_hold h ON h.attempt_id = r.attempt_id "
                "WHERE r.resource = 'GPU' AND r.seq > %s AND h.provider_account = %s", (snap["usage_baseline_seq"], account))
    used = int(cur.fetchone()["used"])
    cur.execute("SELECT coalesce(sum(reserved_ms - consumed_ms - released_ms), 0) AS held FROM rav_pilot_gpu_hold "
                "WHERE provider_account = %s AND state = 'OPEN'", (account,))
    held = int(cur.fetchone()["held"])
    need = cfg["profiles_by_kind"][job["kind"]]["gpu_ms"]
    if snap["remaining_ms"] - used - held - cfg["gpu_headroom_ms"] < need:
        return None, "gpu_budget_exhausted"
    return snap, None


def open_hold_in_tx(cur, attempt_id: UUID, snap: dict, reserved_ms: int) -> None:
    cur.execute("INSERT INTO rav_pilot_gpu_hold (id, attempt_id, snapshot_id, provider_account, reserved_ms) "
                "VALUES (%s, %s, %s, %s, %s)", (uuid4(), attempt_id, snap["id"], snap["provider_account"], reserved_ms))


def settle_hold_in_tx(cur, attempt_id: UUID, gpu_ms: int) -> None:
    """Chốt hold của attempt: consumed = GPU actual (có thể vượt reserved), phần dư trả về ngân sách."""
    cur.execute("SELECT * FROM rav_pilot_gpu_hold WHERE attempt_id = %s FOR UPDATE", (attempt_id,))
    hold = cur.fetchone()
    if not hold or hold["state"] == "FINAL":
        return
    cur.execute("UPDATE rav_pilot_gpu_hold SET consumed_ms = %s, released_ms = %s, state = 'FINAL', updated_at = now() "
                "WHERE id = %s", (gpu_ms, max(0, hold["reserved_ms"] - gpu_ms), hold["id"]))


# ------------------------------------------------------------------------------
# Trạng thái launch của attempt (worker GPU / supervisor)
# ------------------------------------------------------------------------------
def _set_launch(db, lease, frm: tuple[str, ...], to: str, *, alive: bool, **cols: Any) -> None:
    sets = ", ".join(f"{k} = %s" for k in cols)
    clock = (", process_group = coalesce(process_group, '{}'::jsonb) || "
             "jsonb_build_object('gpu_started_at', clock_timestamp(), 'gpu_slot_key', %s::text)") if to == "INTENT" else ""
    with db.backend() as cur:
        if alive:
            from .pilot_runtime import PilotRuntime
            PilotRuntime._lock_attempt(cur, lease)
        cur.execute(f"UPDATE rav_pilot_job_attempt SET launch_state = %s{', ' + sets if sets else ''}{clock}, updated_at = now() "
                    "WHERE id = %s AND fence = %s AND worker_id = %s AND launch_state = ANY(%s) RETURNING id",
                    (to, *cols.values(), *((lease.slot_key,) if to == "INTENT" else ()),
                     lease.attempt_id, lease.fence, lease.worker_id, list(frm)))
        if not cur.fetchone():
            raise ServiceError("lease_lost", f"attempt không ở trạng thái launch {frm}", 409)


def launch_intent(db, lease, marker: str, provider_account: str) -> None:
    """Ghi trước khi gọi provider tạo session (lease phải còn hiệu lực)."""
    _set_launch(db, lease, ("NONE",), "INTENT", alive=True, launch_marker=marker, provider_account=provider_account)


def launch_attached(db, lease, session_id: str, operation_name: str) -> None:
    """Handle bất biến: ghi ngay sau khi provider trả session (kể cả lease vừa hết hạn)."""
    _set_launch(db, lease, ("INTENT",), "ATTACHED", alive=False, session_id=session_id, operation_name=operation_name)


def launch_uncertain(db, lease) -> None:
    _set_launch(db, lease, ("INTENT",), "LAUNCH_UNCERTAIN", alive=False)


def launch_stopped(db, lease, *, verified: bool) -> None:
    if verified:
        _set_launch(db, lease, ("ATTACHED", "STOP_PENDING"), "STOPPED", alive=False, session_stop_verified=True)
    else:
        _set_launch(db, lease, ("ATTACHED",), "STOP_PENDING", alive=False)


def mark_stopped_in_tx(cur, attempt_id: UUID, evidence: dict[str, Any]) -> None:
    """Supervisor: provider xác minh không còn session của slot → STOPPED."""
    from psycopg.types.json import Jsonb

    cur.execute("UPDATE rav_pilot_job_attempt SET launch_state = 'STOPPED', session_stop_verified = true, "
                "usage = CASE WHEN process_group ? 'gpu_started_at' THEN "
                "coalesce(usage, '{}'::jsonb) || jsonb_build_object('gpu_ms', greatest("
                "coalesce((usage->>'gpu_ms')::bigint, 0), ceil(extract(epoch FROM "
                "(clock_timestamp() - (process_group->>'gpu_started_at')::timestamptz)) * 1000)::bigint)) "
                "ELSE usage END, "
                "process_group = coalesce(process_group, '{}'::jsonb) || %s, updated_at = now() "
                "WHERE id = %s AND launch_state = ANY(%s)", (Jsonb({"gpu_stop": evidence}), attempt_id, list(LAUNCH_OPEN)))
