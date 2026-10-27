"""Hàng đợi bền pilot trên PostgreSQL thật (DEP2F #89): enqueue/claim/heartbeat/publish/finish, race, lease hết hạn, xóa nguồn.

Chạy: đặt `RAV_PILOT_TEST_DATABASE_URL` (tests/conftest.py) rồi `python -m pytest tests/test_pilot_queue.py -q`.
"""

from __future__ import annotations

import threading
from uuid import UUID, uuid4

import psycopg
import pytest

from src.rav.actor import ActorContext
from src.rav.errors import ServiceError
from src.rav.store.pilot_db import CreateSessionInput, PilotDB
from src.rav.store.pilot_runtime import EnqueueSpec, PilotRuntime, Progress, SlotEvidence, Usage, WorkerContext
from src.rav.worker.dispatcher import PilotDispatcher

FINAL = Usage(cpu_ms=400, gpu_ms=0, state="FINAL")


def test_lane_claim_filters_kind_and_uses_only_policy_slot(rt):
    pg, api, worker, a, ctx = rt["pg"], rt["api"], rt["worker"], rt["a"], rt["ctx"]
    # Independent slot, explicitly provisioned in this policy (no production mutation).
    import copy

    from tests.conftest import POLICY_CONFIG
    cfg = copy.deepcopy(POLICY_CONFIG)
    cfg["slot_inventory"]["CPU"] = ["cpu-1", "cpu-extra"]
    pid = pg.policy(config=cfg)
    pg.sql("UPDATE rav_pilot_profile SET policy_id=%s WHERE user_id=%s", (pid, a))
    pg.slot("cpu-extra")
    first = api.enqueue(actor(a), spec(kind="EXPORT_BUILD"))
    second = api.enqueue(actor(a), spec(kind="EXPORT_FINALIZE"))
    l1 = worker.claim(ctx, "CPU", kinds=("EXPORT_FINALIZE",), slot_keys=("cpu-extra",))
    assert l1.job_id == second.id and l1.slot_key == "cpu-extra"
    assert worker.claim(ctx, "CPU", kinds=("EXPORT_BUILD",), slot_keys=("cpu-extra",)) is None
    l2 = worker.claim(ctx, "CPU", kinds=("EXPORT_BUILD",), slot_keys=("cpu-1",))
    assert l2.job_id == first.id
    worker.publish(l1, {}, FINAL)
    worker.publish(l2, {}, FINAL)
    assert all(s["state"] == "FREE" for s in pg.sql("SELECT state FROM rav_pilot_resource_slot"))


def test_lane_cannot_claim_unconfigured_policy_slot_or_other_queue(rt):
    rt["pg"].slot("cpu-not-in-policy")
    rt["api"].enqueue(actor(rt["a"]), spec())
    assert rt["worker"].claim(rt["ctx"], "CPU", kinds=("EXPORT_BUILD",),
                              slot_keys=("cpu-not-in-policy",)) is None
    with pytest.raises(ServiceError):
        rt["worker"].claim(rt["ctx"], "CPU", kinds=("PREPARE_DINO",), slot_keys=("cpu-1",))


def test_lane_skips_owner_with_incompatible_frozen_policy(rt):
    import copy

    from tests.conftest import POLICY_CONFIG
    pg, worker, api = rt["pg"], rt["worker"], rt["api"]
    old = api.enqueue(actor(rt["a"]), spec(key="old"))
    cfg = copy.deepcopy(POLICY_CONFIG)
    cfg["slot_inventory"]["CPU"] = ["cpu-new"]
    policy = pg.policy(config=cfg)
    # Fix owner order to exercise old owner first, rather than depend on UUID luck.
    uid = UUID(int=(1 << 128)-1)
    pg.profile(user_id=uid, policy_id=policy)
    pg.slot("cpu-new")
    new = api.enqueue(actor(uid), spec(key="new"))
    lease = worker.claim(rt["ctx"], "CPU", kinds=("EXPORT_BUILD",), slot_keys=("cpu-new",))
    assert lease.job_id == new.id
    assert api.get_job(actor(rt["a"]), old.id).state == "QUEUED"
    worker.publish(lease, {}, FINAL)


def test_parallel_slots_respect_owner_limit_and_quarantine(rt):
    import copy

    from tests.conftest import POLICY_CONFIG
    pg, worker, api = rt["pg"], rt["worker"], rt["api"]
    cfg = copy.deepcopy(POLICY_CONFIG)
    cfg["slot_inventory"]["CPU"] = ["cpu-1", "cpu-extra"]
    cfg["heavy_active_per_user"] = 1
    policy = pg.policy(config=cfg)
    pg.sql("UPDATE rav_pilot_profile SET policy_id=%s WHERE user_id=ANY(%s)", (policy, [rt["a"], rt["b"]]))
    pg.slot("cpu-extra")
    first = api.enqueue(actor(rt["a"]), spec(key="a1"))
    api.enqueue(actor(rt["a"]), spec(key="a2", n=2))
    other = api.enqueue(actor(rt["b"]), spec(kind="EXPORT_FINALIZE", key="b1"))
    filters = {"slot_keys": ("cpu-1", "cpu-extra")}
    lease = worker.claim(rt["ctx"], "CPU", kinds=("EXPORT_BUILD",), **filters)
    assert lease.job_id == first.id
    assert worker.claim(rt["ctx"], "CPU", kinds=("EXPORT_BUILD",), **filters) is None
    pg.sql("UPDATE rav_pilot_resource_slot SET state='QUARANTINED' WHERE slot_key=%s", (lease.slot_key,))
    pg.sql("UPDATE rav_pilot_job SET state='WAITING_RESOURCE',waiting_reason='quota_reconcile' WHERE id=%s", (first.id,))
    assert worker.claim(rt["ctx"], "CPU", kinds=("EXPORT_BUILD",), **filters) is None
    other_lease = worker.claim(rt["ctx"], "CPU", kinds=("EXPORT_FINALIZE",), **filters)
    assert other_lease.job_id == other.id
    worker.publish(other_lease, {}, FINAL)


def actor(uid) -> ActorContext:
    return ActorContext(user_id=uid)


@pytest.fixture
def rt(pg):
    policy = pg.policy()
    a, b = pg.profile(policy_id=policy), pg.profile(policy_id=policy)
    w = {"pg": pg, "a": a, "b": b, "policy": policy, "api": PilotRuntime(PilotDB(pg.api_url)),
         "worker": PilotRuntime(PilotDB(pg.worker_url)), "ctx": WorkerContext(worker_id=uuid4(), host_boot_id=uuid4()),
         "va": pg.source(a, "cam_a"), "vb": pg.source(b, "cam_b")}
    pg.slot("cpu-1")
    return w


def spec(kind="EXPORT_BUILD", key=None, resource=None, **payload) -> EnqueueSpec:
    # Kind CPU chưa có hook publish (EXPORT_* → DEP6): kiểm cơ chế hàng đợi với payload giả; PROPOSE/YOLO_BATCH/METRICS có hook thật.
    return EnqueueSpec(kind=kind, resource_id=resource, idempotency_key=key or str(uuid4()), payload=payload or {"n": 1})


def holds(pg, job_id):
    return {r["resource"]: r for r in pg.sql("SELECT * FROM rav_pilot_quota_hold WHERE job_id = %s", (job_id,))}


# ------------------------------------------------------------------------------
# Enqueue
# ------------------------------------------------------------------------------
def test_enqueue_reserves_pins_and_replays(rt):
    api, a, pg = rt["api"], rt["a"], rt["pg"]
    job = api.enqueue(actor(a), spec(key="k1", resource=rt["va"]), [rt["va"]])
    assert (job.state, job.state_version, job.attempt_n) == ("QUEUED", 1, 0)
    assert holds(pg, job.id)["CPU"]["reserved_ms"] == 1_000
    pin = pg.sql("SELECT * FROM rav_pilot_source_pin WHERE consumer_id = %s", (job.id,))[0]
    assert (pin["consumer_kind"], pin["input_role"], pin["released_at"]) == ("JOB", "INPUT", None)
    assert api.enqueue(actor(a), spec(key="k1", resource=rt["va"]), [rt["va"]]).id == job.id
    with pytest.raises(ServiceError) as e:
        api.enqueue(actor(a), spec(key="k1", resource=rt["va"], n=2), [rt["va"]])
    assert e.value.code == "idempotency_conflict"
    window = pg.sql("SELECT held_ms FROM rav_pilot_quota_window WHERE owner_id = %s AND resource = 'CPU'", (a,))
    assert window[0]["held_ms"] == 1_000


def test_enqueue_without_valid_policy_is_503(rt):
    pg = rt["pg"]
    cases = {"no_policy": None, "disabled": pg.policy(enabled=False), "incomplete": pg.policy(config={"cpu_base_ms": 1})}
    for name, policy in cases.items():
        uid = pg.profile(policy_id=policy)
        with pytest.raises(ServiceError) as e:
            rt["api"].enqueue(actor(uid), spec())
        assert (e.value.code, e.value.status) == ("policy_unconfigured", 503), name


def test_enqueue_rejects_foreign_or_unready_source_and_pending_limit(rt):
    api, a, pg = rt["api"], rt["a"], rt["pg"]
    with pytest.raises(ServiceError) as e:
        api.enqueue(actor(a), spec(resource=rt["vb"]), [rt["vb"]])
    assert e.value.status == 404
    pending = pg.source(a, "pending", availability="UNAVAILABLE")
    with pytest.raises(ServiceError) as e:
        api.enqueue(actor(a), spec(resource=pending), [pending])
    assert e.value.code == "source_unavailable"
    for i in range(5):
        api.enqueue(actor(a), spec(n=i))
    with pytest.raises(ServiceError) as e:
        api.enqueue(actor(a), spec(n=99))
    assert (e.value.code, e.value.status) == ("quota_exceeded", 429)


def test_same_active_input_under_new_key_returns_existing_job(rt):
    api, a = rt["api"], rt["a"]
    j1 = api.enqueue(actor(a), spec(key="x1", resource=rt["va"], n=7), [rt["va"]])
    j2 = api.enqueue(actor(a), spec(key="x2", resource=rt["va"], n=7), [rt["va"]])
    assert j1.id == j2.id


def test_concurrent_enqueue_cannot_overspend_quota(rt):
    """Hai luồng thật cùng giữ quota cuối cùng: đúng một thành công (khóa profile/window trên Postgres)."""
    pg = rt["pg"]
    config = dict(__import__("tests.conftest", fromlist=["POLICY_CONFIG"]).POLICY_CONFIG, cpu_base_ms=1_500)
    uid = pg.profile(policy_id=pg.policy(config=config))
    api = rt["api"]
    results, barrier = [], threading.Barrier(2)

    def go(i):
        barrier.wait()
        try:
            results.append(api.enqueue(actor(uid), spec(n=i)).id)
        except ServiceError as e:
            results.append(e.code)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(map(str, results))[-1] == "quota_exceeded" and sum(r == "quota_exceeded" for r in results) == 1
    w = pg.sql("SELECT held_ms, consumed_ms, base_ms FROM rav_pilot_quota_window WHERE owner_id = %s AND resource = 'CPU'", (uid,))[0]
    assert w["held_ms"] == 1_000 <= w["base_ms"]


# ------------------------------------------------------------------------------
# Claim / heartbeat / publish / finish
# ------------------------------------------------------------------------------
def test_claim_publish_settles_quota_pins_and_slot(rt):
    api, worker, a, pg, ctx = rt["api"], rt["worker"], rt["a"], rt["pg"], rt["ctx"]
    job = api.enqueue(actor(a), spec(resource=rt["va"]), [rt["va"]])
    assert worker.claim(ctx, "GPU") is None   # không slot GPU
    lease = worker.claim(ctx, "CPU")
    assert lease and lease.kind == "EXPORT_BUILD" and lease.fence == 1
    assert worker.claim(ctx, "CPU") is None   # slot duy nhất đang HELD
    lease = worker.heartbeat(lease, Progress(sequence=1, phase="scoring", done=3, total=10))
    receipt = worker.publish(lease, {"cleanup_done": True}, FINAL)
    assert receipt.job.state == "SUCCEEDED" and not receipt.replayed
    h = holds(pg, job.id)["CPU"]
    assert (h["state"], h["consumed_ms"], h["released_ms"]) == ("FINAL", 400, 600)
    w = pg.sql("SELECT held_ms, consumed_ms FROM rav_pilot_quota_window WHERE owner_id = %s AND resource = 'CPU'", (a,))[0]
    assert (w["held_ms"], w["consumed_ms"]) == (0, 400)
    assert pg.sql("SELECT state FROM rav_pilot_resource_slot")[0]["state"] == "FREE"
    assert pg.sql("SELECT released_at FROM rav_pilot_source_pin WHERE consumer_id = %s", (job.id,))[0]["released_at"]
    again = worker.publish(lease, {"cleanup_done": True}, FINAL)
    assert again.replayed and pg.sql("SELECT count(*) AS n FROM rav_pilot_usage_receipt")[0]["n"] == 2


def test_stale_fence_and_progress_regression_rejected(rt):
    api, worker, a, ctx = rt["api"], rt["worker"], rt["a"], rt["ctx"]
    api.enqueue(actor(a), spec())
    lease = worker.claim(ctx, "CPU")
    worker.heartbeat(lease, Progress(sequence=1, phase="p", done=5, total=10))
    with pytest.raises(ServiceError) as e:
        worker.heartbeat(lease, Progress(sequence=2, phase="p", done=2, total=10))
    assert e.value.code == "validation_error"
    with pytest.raises(ServiceError) as e:
        worker.heartbeat(lease.model_copy(update={"fence": 99}), Progress(sequence=3, phase="p", done=6, total=10))
    assert e.value.code == "lease_lost"
    with pytest.raises(ServiceError) as e:
        worker.publish(lease.model_copy(update={"worker_id": uuid4()}), {}, FINAL)
    assert e.value.code == "lease_lost"


def test_cancel_queued_releases_and_running_is_cooperative(rt):
    api, worker, a, pg, ctx = rt["api"], rt["worker"], rt["a"], rt["pg"], rt["ctx"]
    queued = api.enqueue(actor(a), spec(n=1))
    cancelled = api.cancel(actor(a), queued.id, expected_version=1)
    assert cancelled.state == "CANCELLED" and holds(pg, queued.id)["CPU"]["released_ms"] == 1_000
    with pytest.raises(ServiceError) as e:
        api.cancel(actor(a), queued.id, expected_version=1)
    assert e.value.code == "state_version_conflict"
    job = api.enqueue(actor(a), spec(n=2))
    lease = worker.claim(ctx, "CPU")
    assert api.cancel(actor(a), job.id, expected_version=2).state == "CANCEL_REQUESTED"
    with pytest.raises(ServiceError) as e:
        worker.heartbeat(lease, Progress(sequence=1, phase="p", done=0, total=1))
    assert e.value.code == "cancel_requested"
    with pytest.raises(ServiceError) as e:
        worker.publish(lease, {}, FINAL)
    assert e.value.code == "cancel_requested"
    assert worker.finish(lease, FINAL).state == "CANCELLED"
    with pytest.raises(ServiceError) as e:
        api.cancel(actor(rt["b"]), job.id, expected_version=3)
    assert e.value.status == 404


def test_round_robin_between_owners(rt):
    api, worker, a, b, ctx = rt["api"], rt["worker"], rt["a"], rt["b"], rt["ctx"]
    for i in range(2):
        api.enqueue(actor(a), spec(n=i))
    api.enqueue(actor(b), spec(n=9))
    owners = []
    for _ in range(3):
        lease = worker.claim(ctx, "CPU")
        owners.append(rt["pg"].sql("SELECT owner_id FROM rav_pilot_job WHERE id = %s", (lease.job_id,))[0]["owner_id"])
        worker.publish(lease, {}, FINAL)
    assert owners[0] != owners[1] and owners.count(a) == 2


def test_concurrent_claims_single_slot(rt):
    api, worker, a = rt["api"], rt["worker"], rt["a"]
    for i in range(3):
        api.enqueue(actor(a), spec(n=i))
    got, barrier = [], threading.Barrier(3)

    def go():
        barrier.wait()
        got.append(worker.claim(WorkerContext(worker_id=uuid4(), host_boot_id=uuid4()), "CPU"))

    threads = [threading.Thread(target=go) for _ in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(g is not None for g in got) == 1


# ------------------------------------------------------------------------------
# Lease hết hạn, đối soát, queue deadline
# ------------------------------------------------------------------------------
def expire(pg, lease):
    pg.sql("UPDATE rav_pilot_job_attempt SET lease_until = now() - interval '1 second' WHERE id = %s", (lease.attempt_id,))


def test_expired_lease_quarantines_slot_until_evidence(rt):
    api, worker, a, pg, ctx = rt["api"], rt["worker"], rt["a"], rt["pg"], rt["ctx"]
    job = api.enqueue(actor(a), spec())
    lease = worker.claim(ctx, "CPU")
    expire(pg, lease)
    with pytest.raises(ServiceError) as e:
        worker.publish(lease, {}, FINAL)
    assert e.value.code == "lease_lost"
    assert worker.recover_expired_leases() == [{"attempt_id": lease.attempt_id, "job_id": job.id, "action": "quarantined"}]
    assert api.get_job(actor(a), job.id).state == "WAITING_RESOURCE"
    slot = pg.sql("SELECT state, generation FROM rav_pilot_resource_slot")[0]
    assert slot["state"] == "QUARANTINED" and holds(pg, job.id)["CPU"]["state"] == "OPEN"
    assert worker.claim(ctx, "CPU") is None
    assert worker.reconcile_slot(ctx, "cpu-1", slot["generation"], SlotEvidence(process_exit_verified=False))["state"] == "QUARANTINED"
    assert worker.reconcile_slot(ctx, "cpu-1", slot["generation"],
                                 SlotEvidence(process_exit_verified=True, cpu_ms=300, gpu_ms=0))["state"] == "FREE"
    lease2 = worker.claim(ctx, "CPU")
    assert lease2.attempt_n == 2 and lease2.fence == 2
    worker.publish(lease2, {}, FINAL)
    h = holds(pg, job.id)["CPU"]
    assert (h["consumed_ms"], h["released_ms"]) == (700, 600)   # retry giữ đủ 1000 sau actual lượt mất


def test_lease_lost_twice_fails_job_and_reconcile_settles(rt):
    api, worker, a, pg, ctx = rt["api"], rt["worker"], rt["a"], rt["pg"], rt["ctx"]
    job = api.enqueue(actor(a), spec())
    for _ in range(2):
        lease = worker.claim(ctx, "CPU")
        expire(pg, lease)
        worker.recover_expired_leases()
        gen = pg.sql("SELECT generation FROM rav_pilot_resource_slot")[0]["generation"]
        worker.reconcile_slot(ctx, "cpu-1", gen, SlotEvidence(process_exit_verified=True))   # không biết usage
    row = pg.sql("SELECT state, error FROM rav_pilot_job WHERE id = %s", (job.id,))[0]
    assert row["state"] == "FAILED" and row["error"]["code"] == "lease_lost"
    h = holds(pg, job.id)["CPU"]
    assert h["state"] == "FINAL" and h["consumed_ms"] == 2_000   # mỗi attempt unknown giữ trọn estimate


def test_queue_deadline_expires_job(rt):
    api, worker, a, pg = rt["api"], rt["worker"], rt["a"], rt["pg"]
    job = api.enqueue(actor(a), spec())
    pg.sql("UPDATE rav_pilot_job SET queue_deadline = now() - interval '1 second' WHERE id = %s", (job.id,))
    assert worker.recover_expired_leases()[0]["action"] == "expired"
    assert holds(pg, job.id)["CPU"]["released_ms"] == 1_000


def test_second_free_slot_cannot_claim_unreconciled_job(rt):
    pg, worker, ctx = rt["pg"], rt["worker"], rt["ctx"]
    pg.slot("cpu-2")
    job = rt["api"].enqueue(actor(rt["a"]), spec())
    lease = worker.claim(ctx, "CPU")
    expire(pg, lease)
    worker.recover_expired_leases()
    assert worker.claim(ctx, "CPU") is None
    assert rt["api"].get_job(actor(rt["a"]), job.id).state == "WAITING_RESOURCE"
    worker.reconcile_slot(ctx, lease.slot_key, lease.slot_generation,
                          SlotEvidence(process_exit_verified=True, cpu_ms=900, gpu_ms=0))
    retry = worker.claim(ctx, "CPU")
    assert retry.attempt_n == 2
    worker.publish(retry, {}, Usage(cpu_ms=800, gpu_ms=0, state="FINAL"))
    assert holds(pg, job.id)["CPU"]["consumed_ms"] == 1_700


def test_cancel_unreconciled_job_retains_hold_and_pin_until_stop(rt):
    pg, api, worker, ctx = rt["pg"], rt["api"], rt["worker"], rt["ctx"]
    job = api.enqueue(actor(rt["a"]), spec(resource=rt["va"]), [rt["va"]])
    lease = worker.claim(ctx, "CPU")
    expire(pg, lease)
    worker.recover_expired_leases()
    waiting = api.get_job(actor(rt["a"]), job.id)
    cancelled = api.cancel(actor(rt["a"]), job.id, waiting.state_version)
    assert cancelled.state == "WAITING_RESOURCE"
    assert holds(pg, job.id)["CPU"]["released_ms"] == 0
    assert pg.sql("SELECT released_at FROM rav_pilot_source_pin WHERE consumer_id = %s", (job.id,))[0]["released_at"] is None
    worker.reconcile_slot(ctx, lease.slot_key, lease.slot_generation,
                          SlotEvidence(process_exit_verified=True, cpu_ms=500, gpu_ms=0))
    assert api.get_job(actor(rt["a"]), job.id).state == "CANCELLED"
    assert holds(pg, job.id)["CPU"]["released_ms"] == 500


def test_worker_role_cannot_grant_itself_quota(rt):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with rt["worker"].db.backend() as cur:
            cur.execute("INSERT INTO rav_pilot_quota_bonus (id, owner_id, resource, granted_ms, operator_id, reason, adjustment_key) "
                        "VALUES (%s, %s, 'CPU', 1000000, %s, 'x', 'x')", (uuid4(), rt["a"], rt["a"]))


# ------------------------------------------------------------------------------
# Dispatcher
# ------------------------------------------------------------------------------
def test_dispatcher_never_fakes_success(rt):
    api, worker, a, pg, ctx = rt["api"], rt["worker"], rt["a"], rt["pg"], rt["ctx"]
    job = api.enqueue(actor(a), spec(kind="EXPORT_BUILD"))
    d = PilotDispatcher(worker, ctx, "CPU", heartbeat_s=0.05)
    assert d.run_once() is True
    row = pg.sql("SELECT state, error FROM rav_pilot_job WHERE id = %s", (job.id,))[0]
    assert row["state"] == "FAILED" and row["error"]["code"] == "handler_unavailable"
    assert d.run_once() is False


def test_dispatcher_default_heartbeat_uses_policy_before_lease_expires(rt):
    from tests.conftest import POLICY_CONFIG

    pg = rt["pg"]
    config = dict(POLICY_CONFIG, lease_ms=1_000, heartbeat_ms=100)
    uid = pg.profile(policy_id=pg.policy(config=config))
    job = rt["api"].enqueue(actor(uid), spec())
    dispatcher = PilotDispatcher(rt["worker"], rt["ctx"], "CPU")

    def longer_than_lease(lease, stop):
        assert not stop.wait(1.3)
        return {"cleanup_done": True}

    dispatcher.register_handler("EXPORT_BUILD", longer_than_lease)
    assert dispatcher.run_once()
    row = pg.sql("SELECT state, attempt_n FROM rav_pilot_job WHERE id = %s", (job.id,))[0]
    assert row == {"state": "SUCCEEDED", "attempt_n": 1}


def test_dispatcher_success_error_and_cancel(rt):
    api, worker, a, pg, ctx = rt["api"], rt["worker"], rt["a"], rt["pg"], rt["ctx"]
    d = PilotDispatcher(worker, ctx, "CPU", heartbeat_s=0.05)
    d.register_handler("EXPORT_BUILD", lambda lease, stop: {"cleanup_done": True})

    def boom(lease, stop):
        raise RuntimeError("x")

    d.register_handler("EXPORT_FINALIZE", boom)
    ok, bad = api.enqueue(actor(a), spec(n=1)), api.enqueue(actor(a), spec(kind="EXPORT_FINALIZE"))
    d.run_once()
    d.run_once()
    states = {r["id"]: r for r in pg.sql("SELECT id, state, error FROM rav_pilot_job")}
    assert states[ok.id]["state"] == "SUCCEEDED"
    assert states[bad.id]["state"] == "FAILED" and states[bad.id]["error"]["code"] == "internal_error"

    started = threading.Event()

    def slow(lease, stop):
        started.set()
        stop.wait(5)
        return {}

    d.register_handler("YOLO_BATCH", slow)
    job = api.enqueue(actor(a), spec(kind="YOLO_BATCH"))
    t = threading.Thread(target=d.run_once)
    t.start()
    started.wait(5)
    api.cancel(actor(a), job.id, expected_version=2)
    t.join(10)
    assert pg.sql("SELECT state FROM rav_pilot_job WHERE id = %s", (job.id,))[0]["state"] == "CANCELLED"
    assert pg.sql("SELECT state FROM rav_pilot_resource_slot")[0]["state"] == "FREE"


# ------------------------------------------------------------------------------
# Xóa nguồn (RESTRICT, tombstone)
# ------------------------------------------------------------------------------
def test_delete_restrict_system_and_tombstone(rt):
    api, a, pg = rt["api"], rt["a"], rt["pg"]
    system = pg.source(None, "demo")
    with pytest.raises(ServiceError) as e:
        api.request_source_delete(actor(a), system, 1, "d0")
    assert (e.value.code, e.value.status) == ("forbidden", 403)
    with pytest.raises(ServiceError) as e:
        api.request_source_delete(actor(a), rt["vb"], 1, "d1")
    assert e.value.status == 404
    db = PilotDB(pg.api_url)
    s = db.create_session(actor(a), CreateSessionInput(name="s", goal="g", target_k=1, pool_source_ids=[rt["va"]]), "s1")
    with pytest.raises(ServiceError) as e:
        api.request_source_delete(actor(a), rt["va"], 1, "d2")
    assert e.value.code == "resource_in_use" and e.value.details["dependencies"] == [{"kind": "SESSION", "id": str(s.id)}]
    assert pg.sql("SELECT availability FROM rav_pilot_source_asset WHERE id = %s", (rt["va"],))[0]["availability"] == "READY"

    lone = pg.source(a, "lone")
    with pytest.raises(ServiceError) as e:
        api.request_source_delete(actor(a), lone, 7, "d3")
    assert e.value.code == "stale_input"
    receipt = api.request_source_delete(actor(a), lone, 1, "d4")
    assert receipt.state == "REQUESTED" and receipt.job_id
    src = pg.sql("SELECT availability, resource_version FROM rav_pilot_source_asset WHERE id = %s", (lone,))[0]
    assert (src["availability"], src["resource_version"]) == ("DELETE_PENDING", 2)
    job = pg.sql("SELECT kind, state FROM rav_pilot_job WHERE id = %s", (receipt.job_id,))[0]
    assert (job["kind"], job["state"]) == ("DELETE_SOURCE", "QUEUED")
    assert pg.sql("SELECT count(*) AS n FROM rav_pilot_source_pin WHERE consumer_id = %s", (receipt.job_id,))[0]["n"] == 0
    assert api.request_source_delete(actor(a), lone, 1, "d4") == receipt
    assert api.get_source_delete(actor(a), receipt.id) == receipt
    with pytest.raises(ServiceError):
        api.get_source_delete(actor(rt["b"]), receipt.id)


def test_delete_without_policy_rolls_back_tombstone(rt):
    pg = rt["pg"]
    uid = pg.profile(policy_id=None)
    src = pg.source(uid, "x")
    with pytest.raises(ServiceError) as e:
        rt["api"].request_source_delete(actor(uid), src, 1, "d")
    assert e.value.code == "policy_unconfigured"
    assert pg.sql("SELECT availability FROM rav_pilot_source_asset WHERE id = %s", (src,))[0]["availability"] == "READY"
    assert pg.sql("SELECT count(*) AS n FROM rav_pilot_source_delete")[0]["n"] == 0
