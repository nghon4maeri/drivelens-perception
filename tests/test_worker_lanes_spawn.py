"""Real three-process/three-slot concurrency probe on isolated PostgreSQL, no AI/S3 claims.

The probe intentionally finishes with a business error after a barrier: verifies simultaneous RUNNING,
per-lane ownership, final usage/cleanup and slot release, not PII/model correctness.
"""
import copy
import json
import multiprocessing
import os
import time
from pathlib import Path
from uuid import uuid4

from src.rav.actor import ActorContext
from src.rav.errors import ServiceError
from src.rav.store.pilot_db import PilotDB
from src.rav.store.pilot_runtime import EnqueueSpec, PilotRuntime
from tests.conftest import POLICY_CONFIG
from tests.test_worker_lanes import install, lane_config


def test_real_supervisor_split_preflight_health_restart(pg, monkeypatch, tmp_path):
    import psutil

    from src.rav.worker import __main__ as entry
    from src.rav.worker.health import alive
    install(monkeypatch)
    for row in lane_config():
        pg.slot(row["slots"][0])
    folder = tmp_path / "supervised"
    monkeypatch.setenv("RAV_PILOT_WORKER_DATABASE_URL", pg.worker_url)
    monkeypatch.setenv("RAV_PILOT_WORKDIR", str(folder))
    monkeypatch.setenv("RAV_PILOT_HOST_BOOT_ID", str(uuid4()))
    monkeypatch.setenv("RAV_S3_BUCKET", "isolated-lanes")
    monkeypatch.setenv("RAV_PILOT_S3_PREFIX", "test-lanes")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.delenv("KAGGLE_API_TOKEN", raising=False)
    monkeypatch.setenv("RAV_PILOT_WORKER_POLL_S", "0.1")
    monkeypatch.setenv("RAV_PILOT_WORKER_RECONCILE_S", "0.2")
    ctx = multiprocessing.get_context("spawn")
    stop = ctx.Event()
    parent = ctx.Process(target=entry.run, args=(stop,))
    def wait_for(condition):
        end = time.monotonic()+30
        while time.monotonic() < end:
            if condition():
                return
            assert parent.is_alive(), "split supervisor exited"
            time.sleep(0.1)
        raise AssertionError("split supervisor timeout")
    record = folder / "process.json"
    try:
        parent.start()
        wait_for(alive)
        before = json.loads(record.read_text())
        assert set(before["expected"]) == {"TEXT", "YOLO", "BULK", "MEDIA"}
        target = before["children"]["YOLO"]["pid"]
        psutil.Process(target).kill()
        wait_for(lambda: alive() and json.loads(record.read_text())["children"]["YOLO"]["pid"] != target)
        after = json.loads(record.read_text())
        assert all(before["children"][k] == after["children"][k] for k in ("TEXT", "BULK", "MEDIA"))
    finally:
        stop.set()
        if parent.pid:
            parent.join(15)
            if parent.is_alive():
                parent.kill()
                parent.join(5)
    assert parent.exitcode == 0 and not record.exists()


def _child(name, database_url, folder, boot, stop):
    from src.rav.worker import __main__ as entry

    def handler(lease, cancelled):
        root = Path(folder)
        (root / (name+".started")).write_text(str(os.getpid()))
        end = time.monotonic()+30
        while not (root / "release").exists() and not cancelled.is_set() and time.monotonic() < end:
            time.sleep(0.05)
        raise ServiceError("probe_completed", "isolated concurrency probe", 422)
    entry.preflight = lambda: (PilotDB(database_url), None)
    entry.queue_handlers = lambda *args: {k: handler for r in lane_config() for k in r["kinds"]}
    entry._run_queue(name, stop, boot)


def test_three_real_cpu_lanes_run_together_and_settle(pg, monkeypatch, tmp_path):
    install(monkeypatch)
    cfg = copy.deepcopy(POLICY_CONFIG)
    cfg["slot_inventory"]["CPU"] = [r["slots"][0] for r in lane_config()]
    policy = pg.policy(config=cfg)
    runtime = PilotRuntime(PilotDB(pg.api_url))
    ids = []
    for row, kind in zip(lane_config(), ("PROPOSE", "YOLO_BATCH", "EXPORT_BUILD"), strict=True):
        pg.slot(row["slots"][0])
        uid = pg.profile(policy_id=policy)
        ids.append(runtime.enqueue(ActorContext(user_id=uid), EnqueueSpec(kind=kind, idempotency_key=str(uuid4()),
                                                                        payload={"probe": str(uuid4())})).id)
    ctx = multiprocessing.get_context("spawn")
    stop, boot = ctx.Event(), str(uuid4())
    processes = [ctx.Process(target=_child, args=(r["name"], pg.worker_url, str(tmp_path), boot, stop)) for r in lane_config()]
    try:
        for p in processes:
            p.start()
        end = time.monotonic()+30
        while len(list(tmp_path.glob("*.started"))) < 3 and time.monotonic() < end:
            time.sleep(0.1)
        assert len(list(tmp_path.glob("*.started"))) == 3
        assert len({p.read_text() for p in tmp_path.glob("*.started")}) == 3
        assert all(j["state"] == "RUNNING" for j in pg.sql("SELECT state FROM rav_pilot_job WHERE id=ANY(%s)", (ids,)))
        (tmp_path / "release").touch()
        while time.monotonic() < end:
            jobs = pg.sql("SELECT state FROM rav_pilot_job WHERE id=ANY(%s)", (ids,))
            if all(j["state"] == "FAILED" for j in jobs):
                break
            time.sleep(0.1)
        assert all(j["state"] == "FAILED" for j in jobs)
        assert all(x["usage_state"] == "FINAL" and x["cleanup_done"] for x in pg.sql("SELECT usage_state,cleanup_done FROM rav_pilot_job_attempt"))
        assert all(x["state"] == "FREE" for x in pg.sql("SELECT state FROM rav_pilot_resource_slot"))
    finally:
        stop.set()
        for p in processes:
            if p.pid:
                p.join(8)
                if p.is_alive():
                    p.kill()
                    p.join(5)
        runtime.db.close()
