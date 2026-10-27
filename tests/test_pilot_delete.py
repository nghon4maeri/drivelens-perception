"""DEP6a deletion: real PostgreSQL + moto, accounting, partial failure and safe retry."""
# ruff: noqa: F811

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.rav.errors import ServiceError
from src.rav.store import pilot_delete
from src.rav.store.pilot_db import PilotDB
from src.rav.store.pilot_delete import make_handler
from src.rav.store.pilot_runtime import PilotRuntime, Progress
from tests.test_pilot_uploads import account, actor, up, uploaded  # noqa: F401, F811


def setup_delete(up):
    _, ready = uploaded(up, up["video"].read_bytes(), "upload-delete")
    rt = PilotRuntime(PilotDB(up["pg"].api_url))
    source = up["pg"].sql("SELECT * FROM rav_pilot_source_asset WHERE id = %s", (ready.source_id,))[0]
    return rt, source


def test_heartbeat_completes_while_s3_delete_is_blocked(up, monkeypatch):
    rt, source = setup_delete(up)
    rt.request_source_delete(actor(up["a"]), source["id"], 1, "slow-delete")
    dispatcher = up["dispatch"]
    runtime = dispatcher.runtime
    lease = runtime.claim(dispatcher.worker_ctx, "CPU")
    entered, release = threading.Event(), threading.Event()
    original = up["s3"].delete

    def slow_delete(key):
        entered.set()
        assert release.wait(8), "test did not release S3"
        return original(key)

    monkeypatch.setattr(up["s3"], "delete", slow_delete)
    handler = make_handler(up["s3"], runtime.db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        deleting = pool.submit(handler, lease, threading.Event())
        try:
            assert entered.wait(5)
            heartbeat = pool.submit(runtime.heartbeat, lease, Progress(sequence=1, phase="working", done=0, total=0))
            heartbeat.result(timeout=1.5)  # must finish before S3 is released
        finally:
            release.set()
            deleting.result(timeout=10)


@pytest.mark.parametrize("change,code", [("cancel", "cancel_requested"), ("lease", "lease_lost")])
def test_delete_rechecks_after_s3_before_confirming(up, monkeypatch, change, code):
    rt, source = setup_delete(up)
    req = rt.request_source_delete(actor(up["a"]), source["id"], 1, f"delete-{change}")
    runtime = up["dispatch"].runtime
    lease = runtime.claim(up["dispatch"].worker_ctx, "CPU")
    entered, release = threading.Event(), threading.Event()
    original = up["s3"].head
    before = account(up["pg"], up["a"])
    monkeypatch.setattr(pilot_delete, "DELETE_CONCURRENCY", 1)

    def slow_head(key):
        entered.set()
        assert release.wait(8), "test did not release S3"
        return original(key)

    monkeypatch.setattr(up["s3"], "head", slow_head)
    handler = make_handler(up["s3"], runtime.db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        deleting = pool.submit(handler, lease, threading.Event())
        try:
            assert entered.wait(5)
            if change == "cancel":
                version = up["pg"].sql("SELECT state_version FROM rav_pilot_job WHERE id = %s", (req.job_id,))[0]["state_version"]
                changed = pool.submit(rt.cancel, actor(up["a"]), req.job_id, version)
            else:
                changed = pool.submit(up["pg"].sql, "UPDATE rav_pilot_job_attempt SET lease_until = now() - interval '1 second' WHERE id = %s", (lease.attempt_id,))
            changed.result(timeout=1.5)  # change must commit while S3 is still blocked
        finally:
            release.set()
        with pytest.raises(ServiceError) as exc:
            deleting.result(timeout=10)
    assert exc.value.code == code
    receipt = up["pg"].sql("SELECT receipt FROM rav_pilot_source_delete WHERE id = %s", (req.id,))[0]["receipt"]
    assert receipt["confirmed"] == []
    assert up["pg"].sql("SELECT availability FROM rav_pilot_source_asset WHERE id = %s", (source["id"],))[0]["availability"] == "DELETE_PENDING"
    assert account(up["pg"], up["a"]) == before


def test_delete_stop_during_s3_sends_no_further_calls(up, monkeypatch):
    rt, source = setup_delete(up)
    req = rt.request_source_delete(actor(up["a"]), source["id"], 1, "delete-stop")
    runtime = up["dispatch"].runtime
    lease = runtime.claim(up["dispatch"].worker_ctx, "CPU")
    entered, release, stop = threading.Event(), threading.Event(), threading.Event()
    original = up["s3"].delete
    deleted = []
    monkeypatch.setattr(pilot_delete, "DELETE_CONCURRENCY", 1)

    def slow_delete(key):
        deleted.append(key)
        entered.set()
        assert release.wait(8), "test did not release S3"
        return original(key)

    monkeypatch.setattr(up["s3"], "delete", slow_delete)
    monkeypatch.setattr(up["s3"], "head", lambda key: pytest.fail("HEAD sent after stop"))
    with ThreadPoolExecutor(max_workers=1) as pool:
        deleting = pool.submit(make_handler(up["s3"], runtime.db), lease, stop)
        try:
            assert entered.wait(5)
            stop.set()
        finally:
            release.set()
        assert deleting.result(timeout=10) == {"cleanup_done": True}
    assert len(deleted) == 1
    assert up["pg"].sql("SELECT receipt FROM rav_pilot_source_delete WHERE id = %s", (req.id,))[0]["receipt"]["confirmed"] == []


def test_delete_s3_tombstone_accounting_and_replay(up):
    rt, source = setup_delete(up)
    before = account(up["pg"], up["a"])
    req = rt.request_source_delete(actor(up["a"]), source["id"], 1, "delete")
    assert rt.request_source_delete(actor(up["a"]), source["id"], 1, "delete").id == req.id
    with pytest.raises(ServiceError) as exc:
        rt.get_source_delete(actor(up["b"]), req.id)
    assert exc.value.code == "not_found"
    with pytest.raises(ServiceError) as exc:
        rt.request_source_delete(actor(up["a"]), source["id"], 2, "concurrent")
    assert exc.value.code == "delete_in_progress"
    assert up["dispatch"].run_once()
    assert rt.get_source_delete(actor(up["a"]), req.id).state == "SUCCEEDED"
    assert up["s3"].head(source["raw_key"]) is None
    frames = up["pg"].sql("SELECT total_bytes, state FROM rav_pilot_frame_set WHERE source_id = %s", (source["id"],))[0]
    assert frames["state"] == "DELETED"   # ảnh lưu sẵn thuộc inventory xóa (§21.20)
    assert account(up["pg"], up["a"])["persisted_bytes"] == before["persisted_bytes"] - source["size_bytes"] - frames["total_bytes"] == 0
    assert up["pg"].sql("SELECT availability FROM rav_pilot_source_asset WHERE id = %s", (source["id"],))[0]["availability"] == "DELETED"
    assert rt.request_source_delete(actor(up["a"]), source["id"], 1, "delete").state == "SUCCEEDED"
    assert not up["dispatch"].run_once()


def test_delete_partial_failure_retry_requires_reconciled_attempt(up, monkeypatch):
    rt, source = setup_delete(up)
    req = rt.request_source_delete(actor(up["a"]), source["id"], 1, "delete")
    original = up["s3"].head
    def fail_head(key):
        raise ServiceError("storage_unavailable", "S3 lỗi", 503)
    monkeypatch.setattr(up["s3"], "head", fail_head)
    assert up["dispatch"].run_once()
    assert rt.get_source_delete(actor(up["a"]), req.id).state == "FAILED"
    frames = up["pg"].sql("SELECT total_bytes FROM rav_pilot_frame_set WHERE source_id = %s", (source["id"],))[0]["total_bytes"]
    assert account(up["pg"], up["a"])["persisted_bytes"] == source["size_bytes"] + frames   # lỗi giữa chừng: chưa trừ gì
    up["pg"].sql("UPDATE rav_pilot_job_attempt SET cleanup_done = false WHERE job_id = %s", (req.job_id,))
    with pytest.raises(ServiceError) as exc:
        rt.request_source_delete(actor(up["a"]), source["id"], 2, "retry")
    assert exc.value.code == "delete_reconcile_pending"
    up["pg"].sql("UPDATE rav_pilot_job_attempt SET cleanup_done = true WHERE job_id = %s", (req.job_id,))
    monkeypatch.setattr(up["s3"], "head", original)
    retry = rt.request_source_delete(actor(up["a"]), source["id"], 2, "retry")
    assert retry.job_id != req.job_id
    assert up["dispatch"].run_once()
    assert rt.get_source_delete(actor(up["a"]), retry.id).state == "SUCCEEDED"
    assert account(up["pg"], up["a"])["persisted_bytes"] == 0


def test_delete_includes_media_but_preserves_other_owner(up):
    from uuid import uuid4

    from src.rav.store.pilot_media import MediaRequestIn, PilotMedia
    from src.rav.store.pilot_runtime import WorkerContext
    from src.rav.worker.dispatcher import PilotDispatcher
    from src.rav.worker.handlers import make_handlers

    rt, source = setup_delete(up)
    up["pg"].slot("media-delete", "MEDIA")
    worker_db = PilotDB(up["pg"].worker_url)
    dispatch = PilotDispatcher(PilotRuntime(worker_db), WorkerContext(worker_id=uuid4(), host_boot_id=uuid4()), "MEDIA")
    for kind, fn in make_handlers(up["s3"], worker_db, up["tmp"] / "media").items():
        dispatch.register_handler(kind, fn)
    media = PilotMedia(PilotDB(up["pg"].api_url), lambda: up["s3"])
    req = media.request(actor(up["a"]), MediaRequestIn(source_id=source["id"],
                        unit_id=f"{source['core_source_id']}:0.000-0.000", variant="context"))
    assert dispatch.run_once()
    assert media.get(actor(up["a"]), req.id).state == "READY"
    art = up["pg"].sql("SELECT * FROM rav_pilot_artifact WHERE source_id = %s", (source["id"],))[0]
    other_up = dict(up, a=up["b"])
    _, other = uploaded(other_up, up["video"].read_bytes(), "other-upload")
    other_src = up["pg"].sql("SELECT * FROM rav_pilot_source_asset WHERE id = %s", (other.source_id,))[0]
    other_bytes = account(up["pg"], up["b"])["persisted_bytes"]
    delete = rt.request_source_delete(actor(up["a"]), source["id"], 1, "delete")
    assert media.get(actor(up["a"]), req.id).state == "FAILED"
    assert up["dispatch"].run_once()
    assert rt.get_source_delete(actor(up["a"]), delete.id).state == "SUCCEEDED"
    assert up["s3"].head(art["object_key"]) is None
    assert up["pg"].sql("SELECT state FROM rav_pilot_artifact WHERE id = %s", (art["id"],))[0]["state"] == "DELETED"
    assert account(up["pg"], up["a"])["persisted_bytes"] == 0
    assert up["s3"].head(other_src["raw_key"]) is not None
    assert account(up["pg"], up["b"])["persisted_bytes"] == other_bytes


def test_expired_lease_cannot_delete(up):
    import threading

    from src.rav.store.pilot_delete import make_handler

    rt, source = setup_delete(up)
    req = rt.request_source_delete(actor(up["a"]), source["id"], 1, "delete")
    dispatcher = up["dispatch"]
    lease = dispatcher.runtime.claim(dispatcher.worker_ctx, "CPU")
    assert lease.job_id == req.job_id
    up["pg"].sql("UPDATE rav_pilot_job_attempt SET lease_until = now() - interval '1 second' WHERE id = %s", (lease.attempt_id,))
    with pytest.raises(ServiceError) as exc:
        make_handler(up["s3"], PilotDB(up["pg"].worker_url))(lease, threading.Event())
    assert exc.value.code == "lease_lost"
    assert up["s3"].head(source["raw_key"]) is not None
