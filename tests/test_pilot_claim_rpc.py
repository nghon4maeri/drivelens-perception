"""Claim RPC metadata/atomicity/compatibility using isolated PostgreSQL; no live services."""
import logging

import psycopg
import pytest

from src.rav.store.pilot_schema import apply_migrations, schema_problem
from src.rav.worker.dispatcher import PilotDispatcher
from tests.test_pilot_queue import actor, spec
from tests.test_pilot_queue import rt as queue_fixture  # noqa: F401


@pytest.fixture
def rt(request):
    return request.getfixturevalue("queue_fixture")


@pytest.mark.parametrize("rpc", [False, True])
def test_identity_policy_and_queue_time_are_claimed_atomically(rt, rpc):
    job = rt["api"].enqueue(actor(rt["a"]), spec())
    worker = rt["worker"]
    worker._claim_rpc = rpc
    identity = {"host": "isolated", "pid": 123, "start_token": "token"}
    lease = worker.claim(rt["ctx"], "CPU", process=identity)
    assert lease.job_id == job.id and lease.process_recorded
    assert lease.policy_config["lease_ms"] > 0 and lease.queue_ms >= 0
    assert rt["pg"].sql("SELECT process_group FROM rav_pilot_job_attempt WHERE id=%s", (lease.attempt_id,))[0]["process_group"] == identity
    assert "policy_config" not in lease.payload and "process_recorded" not in lease.payload


def test_warm_claim_uses_one_execute_and_dispatcher_skips_process_record(rt, monkeypatch):
    rt["api"].enqueue(actor(rt["a"]), spec())
    worker = rt["worker"]
    worker._claim_rpc = True
    messages = []

    class Capture(logging.Handler):
        def emit(self, record):
            messages.append(record.getMessage())

    capture = Capture()
    logger = logging.getLogger("rav.timing")
    logger.addHandler(capture)
    try:
        lease = worker.claim(rt["ctx"], "CPU", process={"pid": 123, "host": "test", "start_token": "x"})
    finally:
        logger.removeHandler(capture)
    assert sum('"stage": "db.execute"' in x for x in messages) == 1
    d = PilotDispatcher(worker, rt["ctx"])
    d.register_handler("EXPORT_BUILD", lambda *_: {"cleanup_done": True})
    monkeypatch.setattr(worker, "record_process", lambda *_: pytest.fail("identity should already be recorded"))
    assert d._run_lease(lease)
    row = rt["pg"].sql("SELECT usage_state,process_group FROM rav_pilot_job_attempt WHERE id=%s", (lease.attempt_id,))[0]
    assert row["usage_state"] == "FINAL" and row["process_group"]["handler_stopped"]


def test_claim_failure_rolls_back_attempt_identity_and_slot(rt):
    job = rt["api"].enqueue(actor(rt["a"]), spec())
    pg = rt["pg"]
    pg.sql("CREATE FUNCTION fail_claim_slot() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'test'; END $$")
    pg.sql("CREATE TRIGGER fail_claim_slot BEFORE UPDATE ON rav_pilot_resource_slot FOR EACH ROW EXECUTE FUNCTION fail_claim_slot()")
    try:
        with pytest.raises(psycopg.Error):
            rt["worker"].claim(rt["ctx"], "CPU", process={"pid": 1})
        assert pg.sql("SELECT state FROM rav_pilot_job WHERE id=%s", (job.id,))[0]["state"] == "QUEUED"
        assert pg.sql("SELECT count(*) AS n FROM rav_pilot_job_attempt")[0]["n"] == 0
        assert pg.sql("SELECT state FROM rav_pilot_resource_slot")[0]["state"] == "FREE"
    finally:
        pg.sql("DROP TRIGGER fail_claim_slot ON rav_pilot_resource_slot")
        pg.sql("DROP FUNCTION fail_claim_slot()")


def test_missing_claim_migration_is_ready_and_legacy_still_records_identity(rt):
    pg, worker = rt["pg"], rt["worker"]
    pg.sql("DROP FUNCTION public.rav_pilot_claim_local(jsonb,text,text[],text[],jsonb)")
    pg.sql("DELETE FROM rav_pilot_schema_migration WHERE version='012_pilot_claim.sql'")
    try:
        with worker.db.backend() as cur:
            assert schema_problem(cur) is None
        rt["api"].enqueue(actor(rt["a"]), spec())
        lease = worker.claim(rt["ctx"], "CPU", process={"pid": 1})
        assert lease.process_recorded and lease.policy_config and worker._claim_rpc is False
    finally:   # 021 định nghĩa lại claim (thêm MODEL_VERIFY): áp lại sau 012 để không để lại hàm cũ cho test sau
        pg.sql("DELETE FROM rav_pilot_schema_migration WHERE version='021_pilot_model_verify.sql'")
        apply_migrations(pg.admin_url)


def test_only_worker_executes_claim_rpc(rt):
    for role in ("rav_pilot_api", "authenticated", "anon"):
        assert not rt["pg"].sql("SELECT has_function_privilege(%s, 'public.rav_pilot_claim_local(jsonb,text,text[],text[],jsonb)', 'EXECUTE') AS allowed", (role,))[0]["allowed"]
