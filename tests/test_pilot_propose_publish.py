"""PROPOSE RPC atomicity/accounting/compatibility on isolated PostgreSQL; no live dependencies.

Run with RAV_PILOT_TEST_DATABASE_URL and pytest; curation fixture supplies deterministic feature artifacts.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest

from src.rav.errors import ServiceError
from src.rav.store.pilot_runtime import Usage
from src.rav.store.pilot_schema import schema_problem
from tests.conftest import POLICY_CONFIG
from tests.test_pilot_curation import _ask, _decide
from tests.test_pilot_curation import cu as curation_fixture  # noqa: F401
from tests.test_pilot_media import actor


@pytest.fixture
def cu(request):
    return request.getfixturevalue("curation_fixture")


def ready(cu):
    job = _ask(cu)
    rt = cu.d.runtime
    lease = rt.claim(cu.d.worker_ctx, "CPU")
    assert lease and lease.job_id == job.id
    outputs = cu.d._handlers["PROPOSE"](lease, threading.Event())
    return rt, lease, outputs, Usage(cpu_ms=100, gpu_ms=0, state="FINAL", evidence_hash="test")


def accounting(cu):
    return {t: cu.pg.sql(f"SELECT * FROM {t} ORDER BY 1") for t in (
        "rav_pilot_quota_hold", "rav_pilot_quota_hold_part", "rav_pilot_quota_window",
        "rav_pilot_quota_bonus", "rav_pilot_usage_receipt", "rav_pilot_proposal")}


def test_rpc_replay_and_concurrent_response_loss(cu):
    rt, lease, outputs, usage = ready(cu)
    rt._propose_rpc = True
    gate = threading.Barrier(2)

    def publish():
        gate.wait(timeout=10)
        return rt.publish(lease, outputs, usage)

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: publish(), range(2)))
    assert sorted(r.replayed for r in results) == [False, True]
    before = accounting(cu)
    # Invalid output on a replay must not replace the already committed result.
    assert rt.publish(lease, {}, usage).replayed
    assert accounting(cu) == before
    assert len(before["rav_pilot_proposal"]) == 1
    assert len(before["rav_pilot_usage_receipt"]) == 2
    assert {r["state"] for r in before["rav_pilot_quota_hold"]} == {"FINAL"}
    assert cu.pg.sql("SELECT state FROM rav_pilot_resource_slot WHERE slot_key=%s", (lease.slot_key,))[0]["state"] == "FREE"


@pytest.mark.parametrize("fault", ["fence", "worker", "generation", "expired", "cancel", "context", "result", "disabled"])
def test_rpc_rejection_is_atomic(cu, fault):
    rt, lease, outputs, usage = ready(cu)
    if fault == "fence":
        lease = lease.model_copy(update={"fence": lease.fence + 1})
    elif fault == "worker":
        lease = lease.model_copy(update={"worker_id": uuid4()})
    elif fault == "generation":
        lease = lease.model_copy(update={"slot_generation": lease.slot_generation + 1})
    elif fault == "expired":
        cu.pg.sql("UPDATE rav_pilot_job_attempt SET lease_until=now()-interval '1 second' WHERE id=%s", (lease.attempt_id,))
    elif fault == "cancel":
        rt.cancel(actor(cu.a), lease.job_id, rt.get_job(actor(cu.a), lease.job_id).state_version)
    elif fault == "context":
        _decide(cu, [(f"{cu.core}:0.000-0.000", "keep")])
    elif fault == "result":
        outputs["proposal"]["picks"][0]["unit_id"] = "wrong:0.000-0.000"
    else:
        cu.pg.sql("UPDATE rav_pilot_profile SET active=false WHERE user_id=%s", (cu.a,))
    before = accounting(cu)
    expected = {"cancel": "cancel_requested", "context": "proposal_context_changed", "result": "result_invalid",
                "disabled": "account_disabled"}.get(fault, "lease_lost")
    with pytest.raises(ServiceError) as exc:
        rt.publish(lease, outputs, usage)
    assert exc.value.code == expected
    assert accounting(cu) == before


def test_rpc_bonus_fifo_and_refund(cu):
    rt = cu.d.runtime
    with rt.db.backend() as cur:
        window = rt._window(cur, cu.a, "CPU", POLICY_CONFIG["cpu_base_ms"], lock=True)
        cur.execute("UPDATE rav_pilot_quota_window SET consumed_ms=base_ms-100 WHERE id=%s", (window["id"],))
    cu.pg.bonus(cu.a, cu.b, "CPU", 20_000)   # operator-only fixture supplies adjustment provenance
    rt, lease, outputs, usage = ready(cu)
    rt.publish(lease, outputs, usage.model_copy(update={"cpu_ms": 150}))
    bonus = cu.pg.sql("SELECT held_ms,consumed_ms FROM rav_pilot_quota_bonus")[0]
    assert bonus == {"held_ms": 0, "consumed_ms": 50}
    window = cu.pg.sql("SELECT base_ms,held_ms,consumed_ms FROM rav_pilot_quota_window WHERE resource='CPU'")[0]
    assert window["held_ms"] == 0 and window["consumed_ms"] == window["base_ms"]


def test_deficit_uses_complete_legacy_settlement(cu, monkeypatch):
    rt, lease, outputs, usage = ready(cu)
    reserved = cu.pg.sql("SELECT reserved_ms FROM rav_pilot_quota_hold WHERE job_id=%s AND resource='CPU'", (lease.job_id,))[0]["reserved_ms"]
    called = []
    original = rt._publish_in_tx

    def legacy(*args):
        called.append(True)
        return original(*args)

    monkeypatch.setattr(rt, "_publish_in_tx", legacy)
    result = rt.publish(lease, outputs, usage.model_copy(update={"cpu_ms": reserved + 1}))
    assert called and result.job.state == "SUCCEEDED"
    assert cu.pg.sql("SELECT actual_ms FROM rav_pilot_usage_receipt WHERE resource='CPU'")[0]["actual_ms"] == reserved + 1


def test_constraint_failure_rolls_back_consumption(cu):
    rt, lease, outputs, usage = ready(cu)
    before = accounting(cu)
    # Real database failure after quota mutations, during proposal INSERT.
    cu.pg.sql("CREATE FUNCTION fail_rpc_proposal() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
              "RAISE EXCEPTION 'injected failure'; END $$")
    cu.pg.sql("CREATE TRIGGER fail_rpc_proposal BEFORE INSERT ON rav_pilot_proposal "
              "FOR EACH ROW EXECUTE FUNCTION fail_rpc_proposal()")
    try:
        with pytest.raises(psycopg.Error):
            rt.publish(lease, outputs, usage)
        assert accounting(cu) == before
    finally:
        cu.pg.sql("DROP TRIGGER fail_rpc_proposal ON rav_pilot_proposal")
        cu.pg.sql("DROP FUNCTION fail_rpc_proposal()")


def test_staged_migration_absent_keeps_legacy_and_ready(cu):
    rt, lease, outputs, usage = ready(cu)
    cu.pg.sql("DROP FUNCTION public.rav_pilot_publish_propose(jsonb,jsonb,jsonb,jsonb)")
    try:
        cu.pg.sql("DELETE FROM rav_pilot_schema_migration WHERE version='011_pilot_propose_publish.sql'")
        with rt.db.backend() as cur:
            assert schema_problem(cur) is None
        assert rt.publish(lease, outputs, usage).job.state == "SUCCEEDED"
        assert rt._propose_rpc is False
    finally:
        from src.rav.store.pilot_schema import apply_migrations
        apply_migrations(cu.pg.admin_url)


def test_api_and_user_cannot_execute_rpc(cu):
    for role in ("rav_pilot_api", "authenticated", "anon"):
        assert not cu.pg.sql("SELECT has_function_privilege(%s, "
                             "'public.rav_pilot_publish_propose(jsonb,jsonb,jsonb,jsonb)','EXECUTE') AS allowed", (role,))[0]["allowed"]
    assert cu.pg.sql("SELECT has_function_privilege('rav_pilot_worker', "
                     "'public.rav_pilot_publish_propose(jsonb,jsonb,jsonb,jsonb)','EXECUTE') AS allowed")[0]["allowed"]
