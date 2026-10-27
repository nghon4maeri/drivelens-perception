"""Quota 4h pilot trên PostgreSQL thật (DEP2F #89): cửa sổ UTC cố định, base trước bonus FIFO, hoàn trả về cửa sổ gốc.

Chạy: đặt `RAV_PILOT_TEST_DATABASE_URL` (tests/conftest.py) rồi `python -m pytest tests/test_pilot_quota.py -q`.
"""

from __future__ import annotations

from datetime import UTC
from uuid import uuid4

import pytest

from src.rav.actor import ActorContext
from src.rav.errors import ServiceError
from src.rav.store.pilot_db import PilotDB
from src.rav.store.pilot_runtime import (
    EnqueueSpec,
    PilotRuntime,
    ReserveDelta,
    SlotEvidence,
    Usage,
    WorkerContext,
    policy_problem,
)
from tests.conftest import POLICY_CONFIG


@pytest.fixture
def q(pg):
    config = dict(POLICY_CONFIG, cpu_base_ms=1_500)
    uid = pg.profile(policy_id=pg.policy(config=config))
    operator = pg.profile()
    pg.slot("cpu-1")
    return {"pg": pg, "uid": uid, "operator": operator, "api": PilotRuntime(PilotDB(pg.api_url)),
            "worker": PilotRuntime(PilotDB(pg.worker_url)), "ctx": WorkerContext(worker_id=uuid4(), host_boot_id=uuid4())}


def enqueue(q, n=0):
    return q["api"].enqueue(ActorContext(user_id=q["uid"]), EnqueueSpec(kind="EXPORT_BUILD", idempotency_key=str(uuid4()),
                                                                        payload={"n": n}))


def test_window_is_fixed_4h_utc_bucket(q):
    dto = q["api"].read_quota(ActorContext(user_id=q["uid"]))
    w = q["pg"].sql("SELECT window_start, window_end FROM rav_pilot_quota_window WHERE resource = 'CPU'")[0]
    start = w["window_start"].astimezone(UTC)
    assert int(start.timestamp()) % 14_400 == 0 and (w["window_end"] - w["window_start"]).total_seconds() == 14_400
    assert dto.next_reset_at == w["window_end"] and dto.cpu.base_available == 1_500 and dto.cpu.held == 0


def test_base_then_bonus_fifo_and_refund_to_origin(q):
    pg = q["pg"]
    b1 = pg.bonus(q["uid"], q["operator"], "CPU", 300)
    b2 = pg.bonus(q["uid"], q["operator"], "CPU", 1_000)
    enqueue(q, 1)   # base 1000
    job = enqueue(q, 2)   # base 500 + bonus b1 300 + b2 200
    hold = pg.sql("SELECT id FROM rav_pilot_quota_hold WHERE job_id = %s", (job.id,))[0]["id"]
    parts = pg.sql("SELECT window_id, bonus_id, reserved_ms FROM rav_pilot_quota_hold_part WHERE hold_id = %s ORDER BY part_n",
                   (hold,))
    assert [(p["bonus_id"], p["reserved_ms"]) for p in parts] == [(None, 500), (b1, 300), (b2, 200)]
    dto = q["api"].read_quota(ActorContext(user_id=q["uid"]))
    assert (dto.cpu.base_available, dto.cpu.bonus_available) == (0, 800)
    with pytest.raises(ServiceError) as e:
        enqueue(q, 3)   # cần 1000, chỉ còn 800 bonus
    assert e.value.code == "quota_exceeded" and "next_reset_at" in e.value.details

    # Cửa sổ cũ "trôi về quá khứ" rồi job thứ hai mới kết thúc: phần dư trả về đúng cửa sổ gốc, không cộng vào cửa sổ mới.
    pg.sql("UPDATE rav_pilot_quota_window SET window_start = window_start - interval '4 hours', "
           "window_end = window_end - interval '4 hours' WHERE owner_id = %s", (q["uid"],))
    worker, ctx = q["worker"], q["ctx"]
    first = worker.claim(ctx, "CPU")
    worker.publish(first, {}, Usage(cpu_ms=100, gpu_ms=0, state="FINAL"))
    second = worker.claim(ctx, "CPU")
    assert second.job_id == job.id
    worker.publish(second, {}, Usage(cpu_ms=600, gpu_ms=0, state="FINAL"))
    old = pg.sql("SELECT held_ms, consumed_ms FROM rav_pilot_quota_window WHERE owner_id = %s AND window_end < now()",
                 (q["uid"],))[0]
    assert old["consumed_ms"] == 100 + 500 and old["held_ms"] == 0
    bonuses = {r["id"]: r for r in pg.sql("SELECT id, held_ms, consumed_ms FROM rav_pilot_quota_bonus")}
    assert (bonuses[b1]["consumed_ms"], bonuses[b2]["consumed_ms"]) == (100, 0)
    dto = q["api"].read_quota(ActorContext(user_id=q["uid"]))
    assert dto.cpu.base_available == 1_500   # cửa sổ mới tạo lười, đủ base, không nhận hoàn trả


def test_policy_validator_requires_measured_keys():
    assert policy_problem(POLICY_CONFIG) is None
    assert "cpu_base_ms" in policy_problem({k: v for k, v in POLICY_CONFIG.items() if k != "cpu_base_ms"})
    assert "heartbeat" in policy_problem(dict(POLICY_CONFIG, heartbeat_ms=POLICY_CONFIG["lease_ms"]))
    assert policy_problem(dict(POLICY_CONFIG, lease_ms=0))


def test_policy_rows_are_immutable(q):
    import psycopg

    pid = q["pg"].policy()
    with pytest.raises(psycopg.errors.CheckViolation):
        q["pg"].sql("UPDATE rav_pilot_quota_policy SET config = '{}' WHERE id = %s", (pid,))
    q["pg"].sql("UPDATE rav_pilot_quota_policy SET enabled = false WHERE id = %s", (pid,))


def test_extension_keeps_old_window_origin_and_uses_new_base_then_bonus(q):
    pg, worker = q["pg"], q["worker"]
    bonus = pg.bonus(q["uid"], q["operator"], "CPU", 500)
    job = enqueue(q)
    lease = worker.claim(q["ctx"], "CPU")
    pg.sql("UPDATE rav_pilot_quota_window SET window_start = window_start - interval '4 hours', "
           "window_end = window_end - interval '4 hours' WHERE owner_id = %s", (q["uid"],))
    worker.extend_hold(lease, ReserveDelta(cpu_ms=1_800))
    parts = pg.sql("SELECT p.*, w.window_end FROM rav_pilot_quota_hold_part p "
                   "LEFT JOIN rav_pilot_quota_window w ON w.id = p.window_id "
                   "JOIN rav_pilot_quota_hold h ON h.id = p.hold_id WHERE h.job_id = %s ORDER BY p.part_n", (job.id,))
    assert [p["reserved_ms"] for p in parts] == [1_000, 1_500, 300]
    assert parts[-1]["bonus_id"] == bonus and parts[0]["window_id"] != parts[1]["window_id"]
    worker.publish(lease, {}, Usage(cpu_ms=2_700, gpu_ms=0, state="FINAL"))
    h = pg.sql("SELECT consumed_ms, released_ms FROM rav_pilot_quota_hold WHERE job_id = %s", (job.id,))[0]
    assert (h["consumed_ms"], h["released_ms"]) == (2_700, 100)
    assert pg.sql("SELECT consumed_ms, held_ms FROM rav_pilot_quota_bonus WHERE id = %s", (bonus,))[0] == {
        "consumed_ms": 200, "held_ms": 0}


def test_actual_extends_automatically_and_replay_never_double_charges(q):
    job = enqueue(q)
    lease = q["worker"].claim(q["ctx"], "CPU")
    usage = Usage(cpu_ms=1_400, gpu_ms=0, state="FINAL")
    assert q["worker"].publish(lease, {}, usage).job.state == "SUCCEEDED"
    assert q["worker"].publish(lease, {}, usage).replayed
    h = q["pg"].sql("SELECT reserved_ms, consumed_ms FROM rav_pilot_quota_hold WHERE job_id = %s", (job.id,))[0]
    assert h == {"reserved_ms": 1_400, "consumed_ms": 1_400}


def test_pipeline_deferred_error_rolls_back_quota_and_can_reuse_connection(q):
    import io
    import json
    import logging

    import psycopg

    from src.rav.timing import log

    pg, worker = q["pg"], q["worker"]
    job = enqueue(q)
    lease = worker.claim(q["ctx"], "CPU")
    usage = Usage(cpu_ms=600, gpu_ms=0, state="FINAL")
    before = pg.sql("SELECT * FROM rav_pilot_quota_hold WHERE job_id=%s", (job.id,))
    pg.sql("CREATE FUNCTION test_reject_consumption() RETURNS trigger LANGUAGE plpgsql AS $$ "
           "BEGIN RAISE check_violation USING MESSAGE='injected'; END $$")
    pg.sql("CREATE TRIGGER test_reject_consumption BEFORE UPDATE OF consumed_ms ON rav_pilot_quota_window "
           "FOR EACH ROW EXECUTE FUNCTION test_reject_consumption()")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    log.addHandler(handler)
    try:
        with pytest.raises(psycopg.errors.CheckViolation):
            worker.publish(lease, {}, usage)
    finally:
        log.removeHandler(handler)
        pg.sql("DROP TRIGGER test_reject_consumption ON rav_pilot_quota_window")
        pg.sql("DROP FUNCTION test_reject_consumption()")
    # Earlier hold/part writes and later receipt/slot publication must all roll back.
    assert pg.sql("SELECT * FROM rav_pilot_quota_hold WHERE job_id=%s", (job.id,)) == before
    assert pg.sql("SELECT consumed_ms FROM rav_pilot_quota_hold_part")[0]["consumed_ms"] == 0
    assert pg.sql("SELECT count(*) AS n FROM rav_pilot_usage_receipt")[0]["n"] == 0
    assert pg.sql("SELECT state FROM rav_pilot_job WHERE id=%s", (job.id,))[0]["state"] == "RUNNING"
    spans = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert any(s["stage"] == "db.enqueue" for s in spans)
    assert any(s["stage"] == "db.pipeline" and s["outcome"] == "error" for s in spans)
    assert worker.publish(lease, {}, usage).job.state == "SUCCEEDED"
    assert worker.publish(lease, {}, usage).replayed


def test_running_budget_extends_before_next_chunk_and_rejects_exhaustion(q):
    job = enqueue(q)
    lease = q["worker"].claim(q["ctx"], "CPU")
    observed = Usage(cpu_ms=400, gpu_ms=0, state="OPEN")
    q["worker"].ensure_running_budget(lease, observed)
    q["worker"].ensure_running_budget(lease, observed)
    assert q["pg"].sql("SELECT reserved_ms FROM rav_pilot_quota_hold WHERE job_id = %s", (job.id,))[0]["reserved_ms"] == 1_400
    with pytest.raises(ServiceError) as exc:
        q["worker"].ensure_running_budget(lease, Usage(cpu_ms=600, gpu_ms=0, state="OPEN"))
    assert exc.value.code == "quota_exceeded"
    assert q["pg"].sql("SELECT reserved_ms FROM rav_pilot_quota_hold WHERE job_id = %s", (job.id,))[0]["reserved_ms"] == 1_400


def test_insufficient_actual_retains_usage_pins_and_blocks_admission(q):
    pg, worker = q["pg"], q["worker"]
    source = pg.source(q["uid"], "held")
    job = q["api"].enqueue(ActorContext(user_id=q["uid"]),
                           EnqueueSpec(kind="EXPORT_BUILD", idempotency_key=str(uuid4())), [source])
    lease = worker.claim(q["ctx"], "CPU")
    receipt = worker.publish(lease, {}, Usage(cpu_ms=1_600, gpu_ms=0, state="FINAL"))
    assert receipt.job.state == "WAITING_RESOURCE"
    att = pg.sql("SELECT usage_state, usage FROM rav_pilot_job_attempt WHERE id = %s", (lease.attempt_id,))[0]
    assert att["usage_state"] == "UNKNOWN" and att["usage"]["cpu_ms"] == 1_600
    assert not q["api"].read_quota(ActorContext(user_id=q["uid"])).admission["allowed"]
    assert pg.sql("SELECT released_at FROM rav_pilot_source_pin WHERE consumer_id = %s", (job.id,))[0]["released_at"] is None
    assert pg.sql("SELECT count(*) AS n FROM rav_pilot_usage_receipt")[0]["n"] == 0
    with pytest.raises(ServiceError) as exc:
        enqueue(q, 99)
    assert exc.value.code == "quota_reconcile_required"
    assert worker.reconcile_slot(q["ctx"], "cpu-1", lease.slot_generation,
                                 SlotEvidence(process_exit_verified=True))["state"] == "QUARANTINED"
    pg.bonus(q["uid"], q["operator"], "CPU", 100)
    assert worker.reconcile_slot(q["ctx"], "cpu-1", lease.slot_generation,
                                 SlotEvidence(process_exit_verified=True))["state"] == "FREE"
    assert pg.sql("SELECT actual_ms FROM rav_pilot_usage_receipt WHERE resource = 'CPU'")[0]["actual_ms"] == 1_600
    with pytest.raises(ServiceError):
        worker.reconcile_slot(q["ctx"], "cpu-1", lease.slot_generation, SlotEvidence(process_exit_verified=True))
    assert pg.sql("SELECT consumed_ms FROM rav_pilot_quota_hold WHERE job_id = %s AND resource = 'CPU'",
                  (job.id,))[0]["consumed_ms"] == 1_600
