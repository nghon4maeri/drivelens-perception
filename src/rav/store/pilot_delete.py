"""DEP6a: fenced source deletion, S3 confirmation and atomic tombstone/storage receipt.

Worker handler receives AttemptLease; publishes through PilotRuntime hooks. No direct CLI.
Failures retain DELETE_PENDING so partially deleted sources never become readable again.
Inventory: raw + artifacts + stored frames (§21.20); S3 deletes run in fenced batches with bounded concurrency.
"""

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

from psycopg.types.json import Jsonb

from ..errors import ServiceError

DELETE_BATCH = 100       # key mỗi transaction fenced (§21.20)
DELETE_CONCURRENCY = 8   # DELETE + HEAD đồng thời trong một lô


def _source(cur, job):
    cur.execute("SELECT * FROM rav_pilot_source_asset WHERE id = %s FOR UPDATE", (job["resource_id"],))
    src = cur.fetchone()
    if (not src or src["owner_id"] != job["owner_id"] or src["scope"] != "PRIVATE" or src["protected"]
            or src["availability"] != "DELETE_PENDING" or src["resource_version"] != job["payload"]["source_version"]):
        raise ServiceError("stale_input", "nguồn không còn khớp yêu cầu xóa", 409)
    cur.execute("SELECT 1 FROM rav_pilot_source_pin WHERE source_id = %s AND released_at IS NULL LIMIT 1", (src["id"],))
    if cur.fetchone():
        raise ServiceError("resource_in_use", "nguồn còn tham chiếu", 409)
    return src


def _inventory(cur, src):
    cur.execute("SELECT id, object_key, size_bytes, state FROM rav_pilot_artifact "
                "WHERE source_id = %s AND owner_id = %s AND scope = 'PRIVATE' AND state <> 'DELETED' "
                "ORDER BY id FOR UPDATE", (src["id"], src["owner_id"]))
    arts = cur.fetchall()
    objects = ([{"key": src["raw_key"], "bytes": src["size_bytes"]}] if src["raw_key"] else [])
    objects += [{"key": a["object_key"], "bytes": a["size_bytes"] if a["state"] == "READY" else 0} for a in arts]
    # Ảnh lưu sẵn (§21.20): frame set chỉ tồn tại khi đã publish đủ bộ nên mọi byte ảnh đã persisted.
    cur.execute("SELECT id FROM rav_pilot_frame_set WHERE source_id = %s AND owner_id = %s AND scope = 'PRIVATE' AND state <> 'DELETED' "
                "ORDER BY id FOR UPDATE", (src["id"], src["owner_id"]))
    sets = [r["id"] for r in cur.fetchall()]
    if sets:
        cur.execute("SELECT object_key, size_bytes FROM rav_pilot_frame WHERE frame_set_id = ANY(%s) ORDER BY object_key", (sets,))
        objects += [{"key": f["object_key"], "bytes": f["size_bytes"]} for f in cur.fetchall()]
    return objects


def _accounted_bytes(cur, src) -> int:
    """Read producer snapshots in one query; legacy imports without producers were accounted."""
    cur.execute(
        "SELECT coalesce(sum(bytes), 0)::bigint AS n FROM ("
        "SELECT s.size_bytes AS bytes FROM rav_pilot_source_asset s "
        "LEFT JOIN rav_pilot_upload u ON u.reserved_source_id = s.id AND u.source_id = s.id "
        "LEFT JOIN rav_pilot_storage_hold h ON h.id = u.storage_hold_id "
        "WHERE s.id = %s AND s.raw_key IS NOT NULL AND coalesce(h.storage_accounting_enabled, true) "
        "UNION ALL "
        "SELECT a.size_bytes FROM rav_pilot_artifact a LEFT JOIN rav_pilot_job j ON j.id = a.job_id "
        "WHERE a.source_id = %s AND a.owner_id = %s AND a.scope = 'PRIVATE' AND a.state = 'READY' "
        "AND coalesce(j.storage_accounting_enabled, true) "
        "UNION ALL "
        "SELECT f.total_bytes FROM rav_pilot_frame_set f LEFT JOIN rav_pilot_job j ON j.id = f.job_id "
        "WHERE f.source_id = %s AND f.owner_id = %s AND f.scope = 'PRIVATE' AND f.state <> 'DELETED' "
        "AND coalesce(j.storage_accounting_enabled, true)) charged",
        (src["id"], src["id"], src["owner_id"], src["id"], src["owner_id"]),
    )
    return cur.fetchone()["n"]


def make_handler(storage, db):
    from .pilot_runtime import PilotRuntime

    def delete_source(lease, stop):
        # Persist the inventory before external I/O; next attempt reuses the same source version.
        with db.backend() as cur:
            job, _ = PilotRuntime._lock_attempt(cur, lease)
            if job["state"] != "RUNNING":
                raise ServiceError("cancel_requested", "yêu cầu xóa đã hủy", 409)
            src = _source(cur, job)
            objects = _inventory(cur, src)
            prefix = f"{storage.prefix}/private/{job['owner_id']}/"
            if any(not o["key"].startswith(prefix) or ".." in o["key"] for o in objects):
                raise ServiceError("result_invalid", "object xóa không thuộc owner", 422)
            cur.execute("UPDATE rav_pilot_source_delete SET state = 'RUNNING', "
                        "receipt = receipt || %s WHERE id = %s", (Jsonb({"objects": objects, "confirmed": []}),
                                                               UUID(job["payload"]["delete_request_id"])))
        def gone(key: str) -> bool:
            if stop.is_set():
                return False
            storage.delete(key)
            if stop.is_set():
                return False
            if storage.head(key) is not None:
                raise ServiceError("delete_unconfirmed", "chưa xác nhận object đã xóa", 503)
            return not stop.is_set()

        with ThreadPoolExecutor(max_workers=DELETE_CONCURRENCY) as pool:
            for start in range(0, len(objects), DELETE_BATCH):
                if stop.is_set():
                    return {"cleanup_done": True}
                batch = [o["key"] for o in objects[start:start + DELETE_BATCH]]
                # Mỗi lô một transaction: fence/hủy/nguồn/pin kiểm lại trước S3; confirmed ghi bền sau khi cả lô xác nhận vắng.
                with db.backend() as cur:
                    job, _ = PilotRuntime._lock_attempt(cur, lease)
                    if job["state"] != "RUNNING":
                        raise ServiceError("cancel_requested", "yêu cầu xóa đã hủy", 409)
                    src = _source(cur, job)
                    if _inventory(cur, src) != objects:
                        raise ServiceError("stale_input", "inventory nguồn đã thay đổi", 409)
                confirmed = list(pool.map(gone, batch))
                if stop.is_set() or not all(confirmed):
                    return {"cleanup_done": True}
                with db.backend() as cur:
                    job, _ = PilotRuntime._lock_attempt(cur, lease)
                    if job["state"] != "RUNNING":
                        raise ServiceError("cancel_requested", "yêu cầu xóa đã hủy", 409)
                    src = _source(cur, job)
                    if _inventory(cur, src) != objects:
                        raise ServiceError("stale_input", "inventory nguồn đã thay đổi", 409)
                    if stop.is_set():
                        return {"cleanup_done": True}
                    cur.execute("UPDATE rav_pilot_source_delete SET receipt = jsonb_set(receipt, '{confirmed}', "
                                "(receipt->'confirmed') || %s) WHERE id = %s", (Jsonb(batch),
                                                                            UUID(job["payload"]["delete_request_id"])))
        return {"cleanup_done": True}
    return delete_source


def publish_in_tx(cur, job, lease, outputs):
    src = _source(cur, job)
    did = UUID(job["payload"]["delete_request_id"])
    cur.execute("SELECT * FROM rav_pilot_source_delete WHERE id = %s AND owner_id = %s FOR UPDATE", (did, job["owner_id"]))
    req = cur.fetchone()
    receipt = req["receipt"]
    objects = _inventory(cur, src)
    if (req["state"] != "RUNNING" or receipt.get("objects") != objects
            or set(receipt.get("confirmed", [])) != {o["key"] for o in objects}):
        raise ServiceError("delete_unconfirmed", "chưa xác nhận toàn bộ object xóa", 409)
    size = sum(o["bytes"] for o in objects)
    accounted = _accounted_bytes(cur, src)
    if accounted:
        keys = ["GLOBAL", f"USER:{job['owner_id']}"]
        cur.execute("SELECT * FROM rav_pilot_storage_account WHERE account_key = ANY(%s) ORDER BY account_key FOR UPDATE", (keys,))
        accounts = cur.fetchall()
        if len(accounts) != 2 or any(a["persisted_bytes"] < accounted for a in accounts):
            raise ServiceError("storage_reconcile_required", "dung lượng nguồn cần đối soát", 409)
        cur.execute("UPDATE rav_pilot_storage_account SET persisted_bytes = persisted_bytes - %s WHERE account_key = ANY(%s)",
                    (accounted, keys))
    cur.execute("UPDATE rav_pilot_artifact SET state = 'DELETED' WHERE source_id = %s AND owner_id = %s "
                "AND scope = 'PRIVATE' AND state <> 'DELETED'", (src["id"], job["owner_id"]))
    cur.execute("UPDATE rav_pilot_frame_set SET state = 'DELETED', updated_at = now() WHERE source_id = %s AND owner_id = %s "
                "AND scope = 'PRIVATE' AND state <> 'DELETED'", (src["id"], job["owner_id"]))
    cur.execute("UPDATE rav_pilot_source_asset SET availability = 'DELETED', resource_version = resource_version + 1 WHERE id = %s",
                (src["id"],))
    cur.execute("UPDATE rav_pilot_source_delete SET state = 'SUCCEEDED', receipt = receipt || %s WHERE id = %s",
                (Jsonb({"released_bytes": size, "accounted_released_bytes": accounted}), did))
    return None  # result_ref references artifacts; delete receipt has its own GET route.


def finish_in_tx(cur, job, state, error):
    cur.execute("SELECT current_user AS role")
    if cur.fetchone()["role"] == "rav_pilot_api":
        return  # Queued cancellation: API owns job state; receipt remains worker-only.
    cur.execute("UPDATE rav_pilot_source_delete SET state = 'FAILED', receipt = receipt || %s "
                "WHERE id = %s AND state <> 'SUCCEEDED'", (Jsonb({"error": error or {
                    "code": "cancelled", "message": "yêu cầu xóa đã hủy", "retryable": True}}),
                    UUID(job["payload"]["delete_request_id"])))
