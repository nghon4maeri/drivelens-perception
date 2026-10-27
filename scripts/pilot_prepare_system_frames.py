"""Chuẩn bị ảnh lưu sẵn cho nguồn SYSTEM (operator; CONTRACTS §21.20–21, quyết định #119, #121).

Input: Postgres pilot (role operator), S3 pilot (env `RAV_S3_BUCKET`/`RAV_PILOT_S3_PREFIX`, credential boto3 mặc định), policy version
để lấy giới hạn `storage_global_bytes` và bounds. Output: frame set SYSTEM READY + ảnh FRAME/THUMBNAIL trên S3; dung lượng chỉ tính `GLOBAL`.
Mặc định **dry-run**: liệt kê nguồn SYSTEM READY chưa có bộ ảnh cùng spec/encoding, số ảnh dự kiến và `inventory_hash`.
Ghi thật: `--apply --operator-confirm <inventory_hash> --policy-version <v>`:
- Mỗi nguồn giữ session advisory lock theo receipt key; kiểm policy enabled, nguồn SYSTEM READY, raw/spec/encoding, bounds/đĩa tạm;
- Encode local có giới hạn, tính inventory chính xác (key/hash/byte);
- Transaction trước PUT: ghi receipt STAGING bền (kèm process identity) và giữ `reserved_bytes` trên `GLOBAL`;
- PUT các ảnh lên S3 theo giới hạn concurrency và xác minh lại HEAD/SHA-256;
- Transaction publish: kiểm lại nguồn/policy/receipt, insert frame set + frames, chuyển reserved → persisted trên `GLOBAL`, receipt COMMITTED;
- Lỗi có xử lý: chờ mọi PUT kết thúc rồi cleanup DELETE+HEAD đúng inventory, chỉ nhả reserved khi đã xác nhận sạch;
- Nếu crash hoặc cleanup chưa xong: receipt giữ reservation, recovery yêu cầu chứng minh process cũ đã dừng (cùng host kiểm PID/start token, khác host qua `--recovery-proof`);
- Chạy lại khi nguồn đã có bộ READY hợp lệ là no-op, không PUT lại hoặc tính byte trùng.
- Dry-run liệt kê cả receipt cần phục hồi; --cleanup-only đối soát chúng độc lập policy/source hiện tại, không import mới.
Chạy:
  python scripts/pilot_prepare_system_frames.py --database-url postgresql://...
  python scripts/pilot_prepare_system_frames.py --database-url ... --apply --operator-confirm <hash> --policy-version <v>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.rav.store import pilot_frames, pilot_storage  # noqa: E402
from src.rav.store.pilot_db import DEFAULT_SAMPLE_SPEC, canonical_hash  # noqa: E402

NAMESPACE = uuid5(NAMESPACE_URL, "https://rav26.local/pilot-frames")
RAW_CAP = 8 << 30
def _seed_version(raw_key: str, prefix: str) -> str:
    head = f"{prefix}/system/"
    if not raw_key.startswith(head):
        raise ValueError(f"raw key nguồn SYSTEM ngoài namespace: {raw_key}")
    return raw_key[len(head):].split("/", 1)[0]


def build_plan(cur, prefix: str, *, cleanup_only: bool = False) -> dict:
    cur.execute("SELECT s.id, s.core_source_id, s.raw_key, s.raw_sha256, s.metadata FROM rav_pilot_source_asset s "
                "WHERE s.scope = 'SYSTEM' AND s.kind = 'VIDEO' AND s.availability = 'READY' AND NOT EXISTS ("
                "SELECT 1 FROM rav_pilot_frame_set f WHERE f.source_id = s.id AND f.raw_sha256 = s.raw_sha256 "
                "AND f.sample_spec_hash = %s AND f.encoding_version = %s AND f.state = 'READY') ORDER BY s.core_source_id, s.id",
                (DEFAULT_SAMPLE_SPEC["spec_hash"], pilot_frames.ENCODING_VERSION))
    sources = [{"source_id": str(r["id"]), "core_source_id": r["core_source_id"], "raw_key": r["raw_key"], "raw_sha256": r["raw_sha256"],
                "n_units": (r["metadata"] or {}).get("n_units"), "seed_version": _seed_version(r["raw_key"], prefix),
                "frame_set_id": str(uuid5(NAMESPACE, f"{r['id']}:{r['raw_sha256']}:{DEFAULT_SAMPLE_SPEC['spec_hash']}:"
                                                     f"{pilot_frames.ENCODING_VERSION}"))} for r in cur.fetchall()]
    if cleanup_only:
        sources = []
    cur.execute("SELECT id, idempotency_key, inventory_hash, mapping, counts FROM rav_pilot_import_receipt "
                "WHERE idempotency_key LIKE 'system-frames:%' AND state IN ('STAGING', 'FAILED') "
                "AND NOT coalesce((mapping->>'cleaned')::boolean, false) ORDER BY idempotency_key")
    recoveries = [{**r, "id": str(r["id"])} for r in cur.fetchall()
                  if all(i["object_key"].startswith(f"{prefix}/system/") for i in r["mapping"]["items"])]
    body = {"encoding_version": pilot_frames.ENCODING_VERSION, "spec_hash": DEFAULT_SAMPLE_SPEC["spec_hash"], "sources": sources,
            "recoveries": recoveries}
    return {**body, "objects": 2 * sum(s["n_units"] or 0 for s in sources), "inventory_hash": canonical_hash(body)}


def _materialize(storage, item: dict, work: Path, limits: dict, source: dict) -> dict:
    """Encode exact toàn bộ inventory local, còn nguyên file đến sau PUT/publish."""
    from src.rav.components.decoders.pyav import PyAV
    from src.rav.worker.frame_writer import encode_batch
    from src.rav.worker.handlers import _probe

    work.mkdir(parents=True, exist_ok=True)
    if source["size_bytes"] <= 0 or source["size_bytes"] > RAW_CAP:
        raise RuntimeError("raw ngoài giới hạn")
    if source["size_bytes"] >= limits["worker_temp_bytes"]:
        raise RuntimeError("raw vượt giới hạn temp policy")
    if shutil.disk_usage(work).free < source["size_bytes"] + limits["max_frame_bytes"]:
        raise RuntimeError("đĩa tạm không đủ cho raw và ảnh")
    raw = work / "raw.video"
    sha, size = storage.download(item["raw_key"], raw, source["size_bytes"])
    if sha != item["raw_sha256"] or size != source["size_bytes"]:
        raise RuntimeError(f"raw {item['core_source_id']} không khớp SHA-256 đã ghi")
    video = _probe(raw)
    decoder = PyAV(DEFAULT_SAMPLE_SPEC["decoder"]["params"])
    frames = decoder.frames(video)
    if video.id != item["core_source_id"] or len(frames) != item["n_units"] or not frames:
        raise RuntimeError(f"raw {item['core_source_id']} không khớp core ID/lưới unit")
    set_id = UUID(item["frame_set_id"])
    items = []
    try:
        total = 0
        for start in range(0, len(frames), limits["frame_batch_size"]):
            batch = encode_batch(decoder, video, frames[start:start + limits["frame_batch_size"]], work,
                                 lambda idx, v, h: storage.system_frame_key(
                                     item["seed_version"], item["core_source_id"], set_id, idx, v, h),
                                 max_bytes=min(limits["max_frame_bytes"] - total, limits["worker_temp_bytes"] - size - total))
            total += sum(i["size_bytes"] for i in batch)
            if total > limits["max_frame_bytes"] or total + size > limits["worker_temp_bytes"]:
                raise RuntimeError("inventory ảnh vượt giới hạn policy/temp")
            if shutil.disk_usage(work).free < limits["max_frame_bytes"] - total:
                raise RuntimeError("đĩa tạm không đủ cho inventory ảnh")
            items.extend(batch)
    finally:
        handle = PyAV._handles.pop(video.uri, None)
        if handle is not None:
            handle.container.close()
    return {"frame_set_id": str(set_id), "encoding_version": pilot_frames.ENCODING_VERSION,
            "sample_spec_hash": DEFAULT_SAMPLE_SPEC["spec_hash"], "n_units": len(frames),
            "total_bytes": sum(i["size_bytes"] for i in items), "items": items}


def _receipt_key(item: dict) -> str:
    return (f"system-frames:{item['source_id']}:{item['raw_sha256']}:"
            f"{DEFAULT_SAMPLE_SPEC['spec_hash']}:{pilot_frames.ENCODING_VERSION}")


def _actor() -> dict:
    process = psutil.Process(os.getpid())
    return {"host": socket.gethostname(), "pid": process.pid, "started": process.create_time(), "token": str(uuid4())}


def _stopped(mapping: dict, proof: str | None) -> bool:
    actor = mapping.get("actor") or {}
    if not actor.get("host") or not actor.get("pid"):
        return False
    if actor.get("host") == socket.gethostname():
        try:
            process = psutil.Process(actor["pid"])
            started = float(actor["started"])
            return abs(process.create_time() - started) > 0.05 or process.status() == psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return True
        except (psutil.AccessDenied, KeyError, ValueError):
            return False
    # Cross-host confirmation is an explicit operator attestation bound to this receipt and writer token.
    return bool(proof and proof == f"{mapping.get('receipt_key')}:{actor.get('host')}:{actor.get('pid')}:{actor.get('started')}:{actor.get('token')}")


def _cleanup(conn, storage, receipt: dict, *, proof: str | None = None, current: bool = False) -> None:
    from psycopg.types.json import Jsonb

    mapping = receipt["mapping"]
    if mapping.get("cleaned"):
        return
    if not current and not _stopped(mapping, proof):
        raise RuntimeError("process import trước còn sống hoặc chưa có bằng chứng đã dừng")
    with conn.transaction():
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM rav_pilot_frame_set WHERE id = %s AND state = 'READY'", (UUID(mapping["frame_set_id"]),))
        if cur.fetchone():
            raise RuntimeError("bộ ảnh đã READY; không cleanup object đã nhận")
    for obj in mapping["items"]:
        storage.delete(obj["object_key"])
        if storage.head(obj["object_key"]) is not None:
            raise RuntimeError("S3 chưa xác nhận xóa object")
    with conn.transaction():
        cur = conn.cursor()
        cur.execute("SELECT * FROM rav_pilot_import_receipt WHERE id = %s FOR UPDATE", (receipt["id"],))
        now = cur.fetchone()
        if not now:
            return
        cur.execute("SELECT 1 FROM rav_pilot_frame_set WHERE id = %s AND state = 'READY'", (UUID(mapping["frame_set_id"]),))
        if now["state"] == "COMMITTED" or cur.fetchone():
            raise RuntimeError("bộ ảnh đã được nhận; không nhả reservation")
        reserved = now["counts"].get("reserved_bytes", 0)
        if reserved > 0:
            cur.execute("UPDATE rav_pilot_storage_account SET reserved_bytes = reserved_bytes - %s WHERE account_key = 'GLOBAL'",
                        (reserved,))
        updated = {**now["mapping"], "cleaned": True}
        cur.execute("UPDATE rav_pilot_import_receipt SET state = 'FAILED', mapping = %s, counts = %s WHERE id = %s",
                    (Jsonb(updated), Jsonb({**now["counts"], "reserved_bytes": 0}), receipt["id"]))


def _verify_objects(storage, items: list[dict]) -> None:
    for obj in items:
        head = storage.head(obj["object_key"])
        if not head or head["ContentLength"] != obj["size_bytes"]:
            raise RuntimeError("S3 thiếu object hoặc sai kích thước")
        digest = hashlib.sha256()
        body = storage.client.get_object(Bucket=storage.bucket, Key=storage._own(obj["object_key"]))["Body"]
        with body as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                digest.update(chunk)
        if digest.hexdigest() != obj["sha256"]:
            raise RuntimeError("S3 object sai SHA-256")


def apply_plan(database_url: str, storage, plan: dict, policy_version: str | None, workdir: Path,
               recovery_proof: str | None = None) -> dict:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb

    done, skipped = [], []
    if (plan.get("spec_hash") != DEFAULT_SAMPLE_SPEC["spec_hash"]
            or plan.get("encoding_version") != pilot_frames.ENCODING_VERSION
            or plan.get("inventory_hash") != canonical_hash({k: v for k, v in plan.items() if k not in ("objects", "inventory_hash")})):
        raise RuntimeError("plan/spec/hash không hợp lệ")
    recovered = []
    for planned in plan.get("recoveries", []):
        key = planned["idempotency_key"]
        with psycopg.connect(database_url, autocommit=True, row_factory=dict_row) as conn:
            cur = conn.cursor()
            cur.execute("SELECT pg_try_advisory_lock(hashtext(%s)) AS locked", (key,))
            if not cur.fetchone()["locked"]:
                raise RuntimeError("import cùng nguồn đang chạy")
            try:
                with conn.transaction():
                    cur.execute("SELECT * FROM rav_pilot_import_receipt WHERE id = %s FOR UPDATE", (UUID(planned["id"]),))
                    old = cur.fetchone()
                    if (not old or old["state"] not in ("STAGING", "FAILED")
                            or any(old[k] != planned[k] for k in ("idempotency_key", "inventory_hash", "mapping", "counts"))):
                        raise RuntimeError("receipt đổi sau dry-run; cần xác nhận inventory mới")
                _cleanup(conn, storage, old, proof=recovery_proof)
                recovered.append(planned["id"])
            finally:
                cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (key,))
    for item in plan["sources"]:
        key = _receipt_key(item)
        work = workdir / item["frame_set_id"]
        with psycopg.connect(database_url, autocommit=True, row_factory=dict_row) as conn:
            cur = conn.cursor()
            cur.execute("SELECT pg_try_advisory_lock(hashtext(%s)) AS locked", (key,))
            if not cur.fetchone()["locked"]:
                raise RuntimeError("import cùng nguồn đang chạy")
            try:
                with conn.transaction():
                    cur.execute("SELECT config FROM rav_pilot_quota_policy WHERE version = %s AND enabled", (policy_version,))
                    policy = cur.fetchone()
                    if not policy:
                        raise RuntimeError(f"policy {policy_version} thiếu hoặc disabled")
                    limits = pilot_frames.bounds(policy["config"], "PREPARE_FRAMES")
                    cur.execute("SELECT * FROM rav_pilot_source_asset WHERE id = %s FOR UPDATE", (UUID(item["source_id"]),))
                    src = cur.fetchone()
                    if (not src or src["scope"] != "SYSTEM" or src["kind"] != "VIDEO" or src["availability"] != "READY"
                            or src["raw_sha256"] != item["raw_sha256"] or src["raw_key"] != item["raw_key"]
                            or src["core_source_id"] != item["core_source_id"] or src["metadata"].get("n_units") != item["n_units"]
                            or item["n_units"] is None or item["n_units"] <= 0
                            or _seed_version(src["raw_key"], storage.prefix) != item["seed_version"]):
                        raise RuntimeError("nguồn SYSTEM/raw/spec không còn hợp lệ")
                    if pilot_frames.ready_set_in_tx(cur, src["id"], src["raw_sha256"]):
                        skipped.append(item["source_id"])
                        continue
                    cur.execute("SELECT * FROM rav_pilot_import_receipt WHERE idempotency_key = %s FOR UPDATE", (key,))
                    old = cur.fetchone()
                if old:
                    if old["state"] == "COMMITTED":
                        raise RuntimeError("receipt COMMITTED nhưng thiếu bộ READY")
                    _cleanup(conn, storage, old, proof=recovery_proof)
                try:
                    frames = _materialize(storage, item, work, limits, src)
                    total = pilot_frames.validate_set(src, frames)
                    inventory = [{k: v for k, v in frame.items() if k != "path"} for frame in frames["items"]]
                    inventory_hash = canonical_hash(inventory)
                    actor = _actor()
                    mapping = {"receipt_key": key, "source_id": item["source_id"], "raw_sha256": item["raw_sha256"],
                               "spec_hash": DEFAULT_SAMPLE_SPEC["spec_hash"], "encoding_version": pilot_frames.ENCODING_VERSION,
                               "frame_set_id": item["frame_set_id"], "items": inventory, "actor": actor, "cleaned": False}
                    with conn.transaction():
                        cur.execute("SELECT config FROM rav_pilot_quota_policy WHERE version = %s AND enabled", (policy_version,))
                        if not cur.fetchone():
                            raise RuntimeError("policy đổi trước reservation")
                        cur.execute("INSERT INTO rav_pilot_storage_account (account_key, limit_bytes) VALUES ('GLOBAL', %s) "
                                    "ON CONFLICT (account_key) DO NOTHING", (policy["config"]["storage_global_bytes"],))
                        cur.execute("SELECT * FROM rav_pilot_storage_account WHERE account_key = 'GLOBAL' FOR UPDATE")
                        account = cur.fetchone()
                        if account["persisted_bytes"] + account["reserved_bytes"] + total > min(account["limit_bytes"], policy["config"]["storage_global_bytes"]):
                            raise RuntimeError("hết dung lượng GLOBAL")
                        cur.execute("UPDATE rav_pilot_storage_account SET reserved_bytes = reserved_bytes + %s WHERE account_key = 'GLOBAL'",
                                    (total,))
                        counts = {"objects": len(inventory), "bytes": total, "reserved_bytes": total}
                        if old:
                            cur.execute("UPDATE rav_pilot_import_receipt SET inventory_hash = %s, mapping = %s, counts = %s, hashes = %s, "
                                        "state = 'STAGING' WHERE id = %s", (inventory_hash, Jsonb(mapping), Jsonb(counts),
                                                                               Jsonb({"raw": item["raw_sha256"]}), old["id"]))
                            receipt_id = old["id"]
                        else:
                            receipt_id = uuid4()
                            cur.execute("INSERT INTO rav_pilot_import_receipt (id, idempotency_key, inventory_hash, mapping, counts, "
                                        "hashes, state) VALUES (%s, %s, %s, %s, %s, %s, 'STAGING')",
                                        (receipt_id, key, inventory_hash, Jsonb(mapping), Jsonb(counts),
                                         Jsonb({"raw": item["raw_sha256"]})))
                    receipt = {"id": receipt_id, "mapping": mapping, "counts": counts}
                    try:
                        with ThreadPoolExecutor(max_workers=limits["frame_put_concurrency"]) as pool:
                            list(pool.map(lambda f: storage.put_file(f["object_key"], f["path"], f["sha256"], "image/jpeg"),
                                          frames["items"]))
                        _verify_objects(storage, inventory)
                        with conn.transaction():
                            cur.execute("SELECT config FROM rav_pilot_quota_policy WHERE version = %s AND enabled", (policy_version,))
                            if not cur.fetchone():
                                raise RuntimeError("policy đổi trước publish")
                            cur.execute("SELECT * FROM rav_pilot_source_asset WHERE id = %s FOR UPDATE", (src["id"],))
                            current = cur.fetchone()
                            if (not current or current["scope"] != "SYSTEM" or current["availability"] != "READY"
                                    or current["raw_sha256"] != item["raw_sha256"] or current["raw_key"] != item["raw_key"]
                                    or pilot_frames.ready_set_in_tx(cur, src["id"], src["raw_sha256"])):
                                raise RuntimeError("nguồn đổi trước publish")
                            cur.execute("SELECT * FROM rav_pilot_import_receipt WHERE id = %s FOR UPDATE", (receipt_id,))
                            row = cur.fetchone()
                            if (row["state"] != "STAGING" or row["inventory_hash"] != inventory_hash
                                    or row["mapping"] != mapping or row["counts"]["reserved_bytes"] != total):
                                raise RuntimeError("receipt đổi trước publish")
                            pilot_frames.insert_set_in_tx(cur, current, {**frames, "items": inventory}, None, None)
                            cur.execute("UPDATE rav_pilot_storage_account SET reserved_bytes = reserved_bytes - %s, "
                                        "persisted_bytes = persisted_bytes + %s WHERE account_key = 'GLOBAL'", (total, total))
                            cur.execute("UPDATE rav_pilot_import_receipt SET state = 'COMMITTED', counts = %s WHERE id = %s",
                                        (Jsonb({**counts, "reserved_bytes": 0}), receipt_id))
                    except Exception:
                        # Executor joined before cleanup; exact keys remain in durable receipt until DELETE+HEAD completes.
                        _cleanup(conn, storage, receipt, current=True)
                        raise
                    done.append({"source_id": item["source_id"], "frame_set_id": item["frame_set_id"],
                                 "objects": len(inventory), "bytes": total})
                finally:
                    shutil.rmtree(work, ignore_errors=True)
            finally:
                cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (key,))
    return {"prepared": done, "skipped": skipped, **({"recovered": recovered} if recovered else {})}


def main(argv: list[str] | None = None) -> int:
    import psycopg
    from psycopg.rows import dict_row

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--database-url", default=os.environ.get("RAV_PILOT_MIGRATE_DATABASE_URL"))
    ap.add_argument("--prefix", default=os.environ.get("RAV_PILOT_S3_PREFIX"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--cleanup-only", action="store_true", help="chỉ đối soát receipt cũ; không import/PUT mới")
    ap.add_argument("--operator-confirm")
    ap.add_argument("--policy-version")
    ap.add_argument("--recovery-proof", help="cross-host: attestation receipt_key:host:pid:started:token sau khi xác minh process cũ đã dừng")
    ap.add_argument("--workdir", type=Path, default=ROOT / "output/pilot-frames")
    args = ap.parse_args(argv)
    if not args.database_url or not args.prefix:
        ap.error("cần --database-url và --prefix (hoặc env RAV_PILOT_MIGRATE_DATABASE_URL / RAV_PILOT_S3_PREFIX)")
    with psycopg.connect(args.database_url, row_factory=dict_row) as conn:
        plan = build_plan(conn.cursor(), args.prefix, cleanup_only=args.cleanup_only)
    args.workdir.mkdir(parents=True, exist_ok=True)
    (args.workdir / f"plan-{plan['inventory_hash'][:12]}.json").write_text(json.dumps(plan, indent=1), encoding="utf-8")
    if not args.apply:
        print(json.dumps({"dry_run": True, "sources": len(plan["sources"]), "objects": plan["objects"],
                          "recoveries": len(plan["recoveries"]),
                          "inventory_hash": plan["inventory_hash"]}, indent=1))
        return 0
    if args.operator_confirm != plan["inventory_hash"] or (not args.cleanup_only and not args.policy_version):
        ap.error("--apply cần --operator-confirm đúng inventory_hash; import mới cần --policy-version")
    result = apply_plan(args.database_url, pilot_storage.PilotStorage.from_env(), plan, args.policy_version, args.workdir,
                        args.recovery_proof)
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
