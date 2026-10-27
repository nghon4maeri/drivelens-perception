"""DEP4F durable PUT cleanup: real PostgreSQL receipts and moto object deletion.

Run with RAV_PILOT_TEST_DATABASE_URL and pytest tests/test_pilot_objects.py.
"""

import hashlib
import threading
from uuid import uuid4

import pytest

from src.rav.actor import ActorContext
from src.rav.errors import ServiceError
from src.rav.store import pilot_objects, pilot_storage
from src.rav.store.pilot_db import PilotDB
from src.rav.store.pilot_runtime import SlotEvidence, Usage
from src.rav.store.pilot_uploads import UploadIn
from src.rav.worker.handlers import make_handlers
from src.rav.worker.process import identity
from src.rav.worker.supervisor import PilotSupervisor
from tests.test_pilot_queue import rt as rt
from tests.test_pilot_queue import spec
from tests.test_pilot_uploads import account, post
from tests.test_pilot_uploads import up as up


def receipt(pg):
    return pg.sql('SELECT * FROM rav_pilot_worker_object')[0]


def test_crash_after_put_before_written_keeps_intent_and_exact_cleanup(rt, s3_storage, tmp_path):
    job = rt['api'].enqueue(ActorContext(user_id=rt['a']), spec(resource=rt['va']), [rt['va']])
    lease = rt['worker'].claim(rt['ctx'], 'CPU')
    db = PilotDB(rt['pg'].worker_url)
    path = tmp_path / 'object.bin'
    path.write_bytes(b'output bytes')
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    key = s3_storage.artifact_key(rt['a'], uuid4(), sha)
    pilot_objects.intent(db, lease, key, sha, path.stat().st_size)
    s3_storage.put_file(key, path, sha, 'application/octet-stream')
    assert receipt(rt['pg'])['state'] == 'INTENT'
    assert not pilot_objects.cleanup_attempt(db, s3_storage, lease.attempt_id, process_stopped=False)
    assert s3_storage.head(key)
    with db.backend() as cur:
        pilot_storage.release_job_holds(cur, job.id)
    assert rt['pg'].sql('SELECT state FROM rav_pilot_storage_hold')[0]['state'] == 'OPEN'
    assert pilot_objects.cleanup_attempt(db, s3_storage, lease.attempt_id, process_stopped=True)
    assert s3_storage.head(key) is None
    assert receipt(rt['pg'])['state'] == 'DELETED'
    pilot_objects.written(db, lease, key)
    assert receipt(rt['pg'])['state'] == 'DELETED'
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation), db.backend() as cur:
        cur.execute("UPDATE rav_pilot_worker_object SET object_key='other-key'")
    with pytest.raises(psycopg.errors.CheckViolation), db.backend() as cur:
        cur.execute("UPDATE rav_pilot_worker_object SET state='WRITTEN'")
    assert pilot_objects.cleanup_attempt(db, s3_storage, lease.attempt_id, process_stopped=True)


def test_delete_failure_retains_hold_and_receipt(rt, s3_storage, tmp_path, monkeypatch):
    job = rt['api'].enqueue(ActorContext(user_id=rt['a']), spec(resource=rt['va']), [rt['va']])
    lease = rt['worker'].claim(rt['ctx'], 'CPU')
    db = PilotDB(rt['pg'].worker_url)
    path = tmp_path / 'object.bin'
    path.write_bytes(b'output')
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    key = s3_storage.artifact_key(rt['a'], uuid4(), sha)
    pilot_objects.intent(db, lease, key, sha, 6)
    s3_storage.put_file(key, path, sha, 'application/octet-stream')
    delete = s3_storage.delete
    def failed(_):
        raise ServiceError('dependency_unavailable', 'delete failed', 503)
    monkeypatch.setattr(s3_storage, 'delete', failed)
    assert not pilot_objects.cleanup_attempt(db, s3_storage, lease.attempt_id, process_stopped=True)
    assert receipt(rt['pg'])['state'] == 'DELETE_PENDING'
    with db.backend() as cur:
        pilot_storage.release_job_holds(cur, job.id)
    assert rt['pg'].sql('SELECT state FROM rav_pilot_storage_hold')[0]['state'] == 'OPEN'
    assert rt['pg'].sql('SELECT released_at FROM rav_pilot_source_pin')[0]['released_at'] is None
    monkeypatch.setattr(s3_storage, 'delete', delete)
    assert pilot_objects.cleanup_attempt(db, s3_storage, lease.attempt_id, process_stopped=True)


def test_accepted_object_is_never_deleted(rt, s3_storage, tmp_path):
    rt['api'].enqueue(ActorContext(user_id=rt['a']), spec())
    lease = rt['worker'].claim(rt['ctx'], 'CPU')
    db = PilotDB(rt['pg'].worker_url)
    path = tmp_path / 'object.bin'
    path.write_bytes(b'output')
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    key = s3_storage.artifact_key(rt['a'], uuid4(), sha)
    pilot_objects.intent(db, lease, key, sha, 6)
    s3_storage.put_file(key, path, sha, 'application/octet-stream')
    pilot_objects.written(db, lease, key)
    with db.backend() as cur:
        pilot_objects.accept_in_tx(cur, lease, key, sha, 6)
    assert pilot_objects.cleanup_attempt(db, s3_storage, lease.attempt_id, process_stopped=True)
    assert receipt(rt['pg'])['state'] == 'ACCEPTED' and s3_storage.head(key)


def test_upload_cancel_right_after_put_holds_canonical_until_cleanup(up, monkeypatch):
    data = up['video'].read_bytes()
    actor = ActorContext(user_id=up['a'])
    created = up['svc'].create(actor, UploadIn(filename='clip.mp4', declared_bytes=len(data)), 'cancel-put')
    post(created, data)
    job = up['svc'].complete(actor, created.id)
    runtime = up['dispatch'].runtime
    lease = runtime.claim(up['dispatch'].worker_ctx, 'CPU')
    runtime.record_process(lease, identity())
    stop = threading.Event()
    original = up['s3'].put_file
    def cancel_after_put(*args):
        original(*args)
        from src.rav.store.pilot_runtime import PilotRuntime
        api = PilotRuntime(PilotDB(up['pg'].api_url))
        current = api.get_job(actor, job.validation_job_id)
        api.cancel(actor, job.validation_job_id, current.state_version)
        stop.set()
    monkeypatch.setattr(up['s3'], 'put_file', cancel_after_put)
    handlers = make_handlers(up['s3'], PilotDB(up['pg'].worker_url), up['tmp'] / 'cancel-worker')
    handlers['VALIDATE_UPLOAD'](lease, stop)
    runtime.record_stopped(lease, Usage(cpu_ms=10, gpu_ms=0, state='FINAL'))
    runtime.finish(lease, Usage(cpu_ms=10, gpu_ms=0, state='FINAL'))
    assert account(up['pg'], up['a'])['reserved_bytes'] == 2 * len(data)
    key = receipt(up['pg'])['object_key']
    assert up['s3'].head(key)
    from src.rav.store.pilot_uploads import PilotUploads
    supervisor = PilotSupervisor(runtime, up['dispatch'].worker_ctx,
                                 PilotUploads(PilotDB(up['pg'].worker_url), lambda: up['s3']))
    assert supervisor.run_reconciliation()
    assert account(up['pg'], up['a'])['reserved_bytes'] == len(data)
    assert up['s3'].head(key) is None
    assert up['pg'].sql("SELECT state FROM rav_pilot_resource_slot")[0]['state'] == 'FREE'
    assert not supervisor.run_reconciliation()


def test_client_roles_cannot_read_receipt_keys(rt):
    import psycopg

    with psycopg.connect(rt['pg'].admin_url) as conn:
        conn.execute('SET ROLE authenticated')
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute('SELECT object_key FROM rav_pilot_worker_object')


def test_upload_dedup_publication_race_deletes_only_unaccepted_canonical(up):
    pg, data = up['pg'], up['video'].read_bytes()
    actor = ActorContext(user_id=up['a'])
    pg.slot('cpu-2')
    runtime = up['dispatch'].runtime
    handlers = make_handlers(up['s3'], PilotDB(pg.worker_url), up['tmp'] / 'race-worker')
    attempts = []
    for key in ('race-1', 'race-2'):
        created = up['svc'].create(actor, UploadIn(filename='clip.mp4', declared_bytes=len(data)), key)
        post(created, data)
        up['svc'].complete(actor, created.id)
        lease = runtime.claim(up['dispatch'].worker_ctx, 'CPU')
        assert lease
        outputs = handlers['VALIDATE_UPLOAD'](lease, threading.Event())
        attempts.append((created, lease, outputs))
    usage = Usage(cpu_ms=10, gpu_ms=0, state='FINAL')
    runtime.publish(attempts[1][1], attempts[1][2], usage)
    runtime.publish(attempts[0][1], attempts[0][2], usage)
    first_key = attempts[0][2]['upload']['canonical_key']
    second_key = attempts[1][2]['upload']['canonical_key']
    assert first_key != second_key
    winner, loser = attempts[1][2]['upload']['frames'], attempts[0][2]['upload']['frames']
    # Bộ ảnh thua (§21.20) giữ chỗ trên hold của job tới khi cleanup + đối soát slot; chỉ bộ thắng được tính persisted.
    assert account(pg, up['a']) == {'persisted_bytes': len(data) + winner['total_bytes'],
                                    'reserved_bytes': 3 * len(data) + loser['total_bytes']}
    db = PilotDB(pg.worker_url)
    assert pilot_objects.cleanup_attempt(db, up['s3'], attempts[0][1].attempt_id, process_stopped=True)
    assert account(pg, up['a']) == {'persisted_bytes': len(data) + winner['total_bytes'],
                                    'reserved_bytes': 2 * len(data) + loser['total_bytes']}
    assert up['s3'].head(first_key) is None and up['s3'].head(second_key)
    assert all(up['s3'].head(i['object_key']) is None for i in loser['items'])
    assert all(up['s3'].head(i['object_key']) for i in winner['items'])
    rows = pg.sql('SELECT source_id FROM rav_pilot_upload ORDER BY created_at')
    assert rows[0]['source_id'] == rows[1]['source_id']


def test_retry_reuses_canonical_key_without_old_cleanup_deleting_it(rt, s3_storage, tmp_path):
    rt['api'].enqueue(ActorContext(user_id=rt['a']), spec())
    runtime, pg = rt['worker'], rt['pg']
    first = runtime.claim(rt['ctx'], 'CPU')
    db = PilotDB(pg.worker_url)
    path = tmp_path / 'object.bin'
    path.write_bytes(b'output')
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    key = s3_storage.artifact_key(rt['a'], uuid4(), sha)
    pilot_objects.intent(db, first, key, sha, 6)
    s3_storage.put_file(key, path, sha, 'application/octet-stream')
    pg.sql("UPDATE rav_pilot_job_attempt SET lease_until=now()-interval '1 minute' WHERE id=%s", (first.attempt_id,))
    runtime.recover_expired_leases()
    assert pilot_objects.cleanup_attempt(db, s3_storage, first.attempt_id, process_stopped=True)
    runtime.reconcile_slot(rt['ctx'], first.slot_key, first.slot_generation,
                           SlotEvidence(process_exit_verified=True, cpu_ms=10, gpu_ms=0))
    second = runtime.claim(rt['ctx'], 'CPU')
    assert second.attempt_n == 2
    pilot_objects.intent(db, second, key, sha, 6)
    s3_storage.put_file(key, path, sha, 'application/octet-stream')
    assert pilot_objects.cleanup_attempt(db, s3_storage, first.attempt_id, process_stopped=True)
    assert s3_storage.head(key)
    assert [r['state'] for r in pg.sql('SELECT state FROM rav_pilot_worker_object ORDER BY created_at')] == ['DELETED', 'INTENT']


def test_two_supervisors_delete_a_receipt_once(rt, s3_storage, tmp_path, monkeypatch):
    rt['api'].enqueue(ActorContext(user_id=rt['a']), spec())
    lease = rt['worker'].claim(rt['ctx'], 'CPU')
    db = PilotDB(rt['pg'].worker_url)
    path = tmp_path / 'object.bin'
    path.write_bytes(b'output')
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    key = s3_storage.artifact_key(rt['a'], uuid4(), sha)
    pilot_objects.intent(db, lease, key, sha, 6)
    s3_storage.put_file(key, path, sha, 'application/octet-stream')
    started, release = threading.Event(), threading.Event()
    calls, errors = [], []
    delete = s3_storage.delete
    def delayed_delete(value):
        calls.append(value)
        started.set()
        assert release.wait(5)
        delete(value)
    monkeypatch.setattr(s3_storage, 'delete', delayed_delete)
    def cleanup():
        try:
            assert pilot_objects.cleanup_attempt(db, s3_storage, lease.attempt_id, process_stopped=True)
        except BaseException as exc:
            errors.append(exc)
    threads = [threading.Thread(target=cleanup) for _ in range(2)]
    threads[0].start()
    assert started.wait(5)
    threads[1].start()
    release.set()
    for thread in threads:
        thread.join(10)
    assert not errors and not any(t.is_alive() for t in threads)
    assert calls == [key]
