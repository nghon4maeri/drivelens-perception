"""Durable attempt PUT intents, acceptance and stopped-process S3 cleanup (§21.13).

Handlers commit intent before PUT; supervisor retries exact receipt keys after stop proof.
"""

from uuid import UUID, uuid4

from ..errors import ServiceError
from . import pilot_storage


def pending_in_tx(cur, *, job_id: UUID | None = None, attempt_id: UUID | None = None) -> bool:
    column, value = ('attempt_id', attempt_id) if attempt_id else ('job_id', job_id)
    cur.execute(f"SELECT 1 FROM rav_pilot_worker_object WHERE {column} = %s "
                "AND state NOT IN ('ACCEPTED', 'DELETED') LIMIT 1", (value,))
    return cur.fetchone() is not None


def intent(db, lease, key: str, sha: str, size: int) -> None:
    from .pilot_runtime import PilotRuntime

    with db.backend() as cur:
        job, _ = PilotRuntime._lock_attempt(cur, lease)
        if job['state'] == 'CANCEL_REQUESTED':
            raise ServiceError('cancelled', 'job đã yêu cầu hủy', 409)
        if job['kind'] != 'VALIDATE_UPLOAD':
            cur.execute("SELECT config FROM rav_pilot_quota_policy WHERE version = %s", (job['policy_version'],))
            policy = cur.fetchone()['config']
            cur.execute("SELECT coalesce(sum(reserved_bytes-persisted_bytes-released_bytes),0) AS n "
                        "FROM rav_pilot_storage_hold WHERE job_id=%s AND upload_id IS NULL AND state='OPEN'", (job['id'],))
            missing = size - cur.fetchone()['n']
            if missing > 0:
                scope = (job.get('payload') or {}).get('target_scope', 'PRIVATE')
                pilot_storage.reserve(cur, job['owner_id'], policy, missing, f'object:{key}', job_id=job['id'], scope=scope)
        cur.execute("INSERT INTO rav_pilot_worker_object (id,owner_id,job_id,attempt_id,object_key,sha256,size_bytes,state) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,'INTENT')",
                    (uuid4(), job['owner_id'], job['id'], lease.attempt_id, key, sha, size))


def intent_batch(db, lease, objects: list[tuple[str, str, int]], hold_key: str, *, reuse_reserved: bool = False) -> None:
    """Một transaction cho cả lô ảnh (§21.20): kiểm attempt/fence/cancel, giữ đúng tổng byte lô (USER + GLOBAL hoặc GLOBAL_ONLY), ghi INTENT từng object."""
    from .pilot_runtime import PilotRuntime

    with db.backend() as cur:
        job, _ = PilotRuntime._lock_attempt(cur, lease)
        if job['state'] == 'CANCEL_REQUESTED':
            raise ServiceError('cancelled', 'job đã yêu cầu hủy', 409)
        cur.execute("SELECT config FROM rav_pilot_quota_policy WHERE version = %s", (job['policy_version'],))
        scope = (job.get('payload') or {}).get('target_scope', 'PRIVATE')
        cfg = cur.fetchone()['config']
        amount = sum(o[2] for o in objects)
        if reuse_reserved:
            cur.execute("SELECT coalesce(sum(reserved_bytes-persisted_bytes-released_bytes),0) AS n "
                        "FROM rav_pilot_storage_hold WHERE job_id=%s AND state='OPEN'", (job['id'],))
            # Earlier INTENT objects also occupy the open reservation until atomic publication.
            free = cur.fetchone()['n']
            cur.execute("SELECT coalesce(sum(size_bytes),0) AS n FROM rav_pilot_worker_object WHERE attempt_id=%s "
                        "AND state IN ('INTENT','WRITTEN')", (lease.attempt_id,))
            amount = max(0, amount - max(0, free - cur.fetchone()['n']))
        if amount:
            pilot_storage.reserve(cur, job['owner_id'], cfg, amount, hold_key, job_id=job['id'], scope=scope)
        cur.executemany("INSERT INTO rav_pilot_worker_object (id,owner_id,job_id,attempt_id,object_key,sha256,size_bytes,state) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,'INTENT')",
                        [(uuid4(), job['owner_id'], job['id'], lease.attempt_id, k, sha, size) for k, sha, size in objects])


def written_batch(db, lease, keys: list[str]) -> None:
    with db.backend() as cur:
        cur.execute("UPDATE rav_pilot_worker_object o SET state='WRITTEN', updated_at=now() "
                    "FROM rav_pilot_job_attempt a WHERE a.id=o.attempt_id AND a.fence=%s AND a.worker_id=%s "
                    "AND o.attempt_id=%s AND o.object_key=ANY(%s) AND o.state='INTENT'",
                    (lease.fence, lease.worker_id, lease.attempt_id, keys))


def written(db, lease, key: str) -> None:
    with db.backend() as cur:
        cur.execute("UPDATE rav_pilot_worker_object o SET state='WRITTEN', updated_at=now() "
                    "FROM rav_pilot_job_attempt a WHERE a.id=o.attempt_id AND a.fence=%s AND a.worker_id=%s "
                    "AND o.attempt_id=%s AND o.object_key=%s AND o.state='INTENT'",
                    (lease.fence, lease.worker_id, lease.attempt_id, key))


def accept_in_tx(cur, lease, key: str, sha: str, size: int) -> None:
    cur.execute("UPDATE rav_pilot_worker_object SET state='ACCEPTED',updated_at=now() "
                "WHERE attempt_id=%s AND object_key=%s AND sha256=%s AND size_bytes=%s "
                "AND state IN ('INTENT','WRITTEN','ACCEPTED') RETURNING id", (lease.attempt_id, key, sha, size))
    if not cur.fetchone():
        raise ServiceError('result_invalid', 'object không có receipt của attempt', 422)


def accept_selected_in_tx(cur, job, lease) -> None:
    if job['kind'] == 'VALIDATE_UPLOAD':
        cur.execute("SELECT s.raw_key AS object_key,s.raw_sha256 AS sha256,s.size_bytes, "
                    "s.id=u.reserved_source_id AS new_source FROM rav_pilot_upload u "
                    "JOIN rav_pilot_source_asset s ON s.id=u.source_id WHERE u.validation_job_id=%s", (job['id'],))
    else:
        cur.execute("SELECT object_key,sha256,size_bytes FROM rav_pilot_artifact WHERE attempt_id=%s AND state='READY'",
                    (lease.attempt_id,))
    selected = cur.fetchall()
    if job['kind'] in ('VALIDATE_UPLOAD', 'PREPARE_FRAMES'):   # ảnh của frame set do attempt này publish (§21.20)
        from .pilot_frames import accept_in_tx as accept_frames
        accept_frames(cur, lease)
    for row in selected:
        cur.execute("SELECT 1 FROM rav_pilot_worker_object WHERE attempt_id=%s AND object_key=%s",
                    (lease.attempt_id, row['object_key']))
        if cur.fetchone() or row.get('new_source'):
            accept_in_tx(cur, lease, row['object_key'], row['sha256'], row['size_bytes'])


def cleanup_attempt(db, storage, attempt_id: UUID, *, process_stopped: bool) -> bool:
    if not process_stopped:
        return False
    from .pilot_multipart import upload_lock
    try:
        with upload_lock(db, attempt_id, wait_s=5) as conn:
            return _cleanup_locked(conn, storage, attempt_id)
    except ServiceError as exc:
        if exc.code == 'upload_busy':
            return False
        raise


def _cleanup_locked(conn, storage, attempt_id):
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("UPDATE rav_pilot_worker_object SET state='DELETE_PENDING',updated_at=now() WHERE attempt_id=%s "
                    "AND state IN ('INTENT','WRITTEN')", (attempt_id,))
        cur.execute("SELECT * FROM rav_pilot_worker_object WHERE attempt_id=%s AND state='DELETE_PENDING'", (attempt_id,))
        objects = cur.fetchall()
    from concurrent.futures import ThreadPoolExecutor

    def erase(obj):
        try:
            if obj['size_bytes'] >= pilot_storage.MULTIPART_THRESHOLD:
                storage.abort_multipart_key(obj['object_key'])
            storage.delete(obj['object_key'])
            if storage.head(obj['object_key']) is not None:
                return None
            return obj['id']
        except ServiceError:
            return None

    # DELETE_PENDING cannot be accepted by publish. Stopped proof precedes this barrier;
    # parallel exact-key I/O keeps profile locks out of network waits and bounds cleanup memory.
    with ThreadPoolExecutor(max_workers=4) as pool:
        for start in range(0, len(objects), 64):
            batch = objects[start:start+64]
            erased = [oid for oid in pool.map(erase, batch) if oid is not None]
            with conn.transaction(), conn.cursor() as cur:
                cur.execute('SELECT user_id FROM rav_pilot_profile WHERE user_id=%s FOR UPDATE', (batch[0]['owner_id'],))
                cur.execute("UPDATE rav_pilot_worker_object SET state='DELETED',updated_at=now() WHERE id=ANY(%s) AND state='DELETE_PENDING'", (erased,))
            if len(erased) != len(batch):
                return False
    with conn.transaction(), conn.cursor() as cur:
        clean = not pending_in_tx(cur, attempt_id=attempt_id)
        if clean and objects:
            cur.execute('SELECT user_id FROM rav_pilot_profile WHERE user_id=%s FOR UPDATE', (objects[0]['owner_id'],))
            cur.execute('SELECT * FROM rav_pilot_upload WHERE validation_job_id=%s FOR UPDATE', (objects[0]['job_id'],))
            up = cur.fetchone()
            if up and up['state'] in ('READY', 'FAILED', 'ABORTED'):
                release_canonical_in_tx(cur, up)
        return clean



def release_canonical_in_tx(cur, up) -> None:
    """Release canonical once; the other declared half stays reserved for staging."""
    if up['validation_job_id'] and pending_in_tx(cur, job_id=up['validation_job_id']):
        return
    cur.execute("SELECT * FROM rav_pilot_storage_hold WHERE id=%s FOR UPDATE", (up['storage_hold_id'],))
    hold = cur.fetchone()
    release = max(0, up['declared_bytes'] - hold['persisted_bytes'] - hold['released_bytes'])
    pilot_storage.settle(cur, hold['id'], release=release)
