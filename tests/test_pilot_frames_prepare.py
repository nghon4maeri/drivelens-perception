"""Chuẩn bị ảnh lưu sẵn cho nguồn cũ và SYSTEM (DEP7-Frames F2, CONTRACTS §21.20): Postgres thật + S3 moto + worker thật.

User: `POST /sources/{id}/preparations {feature: FRAMES}` → job `PREPARE_FRAMES` (dedup, ghim nguồn, không đổi resource_version).
Operator: `scripts/pilot_prepare_system_frames.py` dry-run/apply, dung lượng chỉ `GLOBAL`, chạy lại không tính hai lần.
Chạy: đặt `RAV_PILOT_TEST_DATABASE_URL` rồi `python -m pytest tests/test_pilot_frames_prepare.py -q`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
import requests

from src.rav.actor import ActorContext
from src.rav.errors import ServiceError
from src.rav.store.pilot_db import PilotDB
from src.rav.store.pilot_features import PilotFeatures, PreparationIn
from src.rav.store.pilot_media import MediaRequestIn, PilotMedia
from src.rav.store.pilot_runtime import PilotRuntime, WorkerContext
from src.rav.store.pilot_uploads import PilotUploads
from src.rav.worker.dispatcher import PilotDispatcher
from src.rav.worker.handlers import make_handlers
from src.rav.worker.supervisor import PilotSupervisor
from tests.conftest import POLICY_CONFIG, make_video
from tests.test_pilot_frames import expected_jpegs, frame_objects, receipts
from tests.test_pilot_media import seed_source, unit

FRAMES = PreparationIn(feature="FRAMES")


def actor(uid) -> ActorContext:
    return ActorContext(user_id=uid)


def accounts(pg) -> dict[str, int]:
    return {r["account_key"].split(":")[0]: r["persisted_bytes"] for r in pg.sql("SELECT * FROM rav_pilot_storage_account")}


@pytest.fixture
def fp(pg, s3_storage, tmp_path):
    def build(config=None):
        policy = pg.policy(config=config or POLICY_CONFIG)
        a, b = pg.profile(policy_id=policy), pg.profile(policy_id=policy)
        pg.slot("cpu-1")
        worker = PilotRuntime(PilotDB(pg.worker_url))
        d = PilotDispatcher(worker, WorkerContext(worker_id=uuid4(), host_boot_id=uuid4()), "CPU", heartbeat_s=0.2)
        for kind, fn in make_handlers(s3_storage, PilotDB(pg.worker_url), tmp_path / "work").items():
            d.register_handler(kind, fn)
        path = make_video(tmp_path / "v.mp4", seconds=4, fps=10)
        sid, core, frames = seed_source(pg, s3_storage, path, a)   # nguồn READY trước DEP7-Frames: chưa có bộ ảnh
        return {"pg": pg, "s3": s3_storage, "a": a, "b": b, "dispatch": d, "path": path, "sid": sid, "core": core, "frames": frames,
                "features": PilotFeatures(PilotDB(pg.api_url)), "media": PilotMedia(PilotDB(pg.api_url), lambda: s3_storage),
                "policy_version": pg.sql("SELECT version FROM rav_pilot_quota_policy WHERE id = %s", (policy,))[0]["version"],
                "supervisor": PilotSupervisor(worker, d.worker_ctx, PilotUploads(PilotDB(pg.worker_url), lambda: s3_storage)),
                "tmp": tmp_path}
    return build


def test_prepare_frames_for_existing_source(fp):
    u = fp()
    pg, a, sid = u["pg"], u["a"], u["sid"]
    req = MediaRequestIn(source_id=sid, unit_id=unit(u["core"], u["frames"][3]), variant="thumbnail")
    dto, status = u["features"].prepare(actor(a), sid, FRAMES, "k1")
    assert status == 202 and not dto.ready and dto.feature == "FRAMES" and dto.job.kind == "PREPARE_FRAMES"
    again, _ = u["features"].prepare(actor(a), sid, FRAMES, "k2")
    assert again.job.id == dto.job.id   # cùng nguồn/nội dung/spec/encoding đang chạy → một job
    assert PilotDB(pg.api_url).get_source(actor(a), sid).frames.job_id == dto.job.id
    pending = u["media"].request(actor(a), req)
    assert (pending.state, pending.job_id) == ("NOT_PREPARED", dto.job.id)
    with pytest.raises(ServiceError) as e:   # job ghim nguồn: không xóa giữa chừng
        PilotRuntime(PilotDB(pg.api_url)).request_source_delete(actor(a), sid, 1, "del")
    assert e.value.code == "resource_in_use"
    assert u["dispatch"].run_once()
    job = pg.sql("SELECT state FROM rav_pilot_job WHERE id = %s", (dto.job.id,))[0]
    assert job["state"] == "SUCCEEDED"
    fs = pg.sql("SELECT * FROM rav_pilot_frame_set")
    assert len(fs) == 1 and fs[0]["state"] == "READY" and fs[0]["job_id"] == dto.job.id and fs[0]["n_units"] == len(u["frames"])
    _, _, jpegs = expected_jpegs(u["path"])
    ready = u["media"].request(actor(a), req)
    assert ready.state == "READY" and requests.get(ready.url).content == jpegs[("THUMBNAIL", u["frames"][3].idx)]
    src = pg.sql("SELECT resource_version, availability FROM rav_pilot_source_asset WHERE id = %s", (sid,))[0]
    assert (src["resource_version"], src["availability"]) == (1, "READY")   # thêm ảnh hiển thị không làm phiên/feature stale
    assert accounts(pg) == {"USER": fs[0]["total_bytes"], "GLOBAL": fs[0]["total_bytes"]}
    assert receipts(pg) == {"ACCEPTED": 2 * len(u["frames"])}
    assert pg.sql("SELECT state FROM rav_pilot_resource_slot")[0]["state"] == "FREE"
    done, status = u["features"].prepare(actor(a), sid, FRAMES, "k3")
    assert (status, done.ready, done.job) == (200, True, None)


def test_prepare_frames_scope_rules(fp):
    u = fp()
    pg, a, b = u["pg"], u["a"], u["b"]
    with pytest.raises(ServiceError) as e:
        u["features"].prepare(actor(b), u["sid"], FRAMES, "k1")
    assert e.value.status == 404
    sys_sid, _, _ = seed_source(pg, u["s3"], make_video(u["tmp"] / "s.mp4", seconds=2, shift=50), None)
    with pytest.raises(ServiceError) as e:
        u["features"].prepare(actor(a), sys_sid, FRAMES, "k2")
    assert (e.value.code, e.value.status) == ("system_frames_missing", 409)
    assert pg.sql("SELECT count(*)::int AS n FROM rav_pilot_job")[0]["n"] == 0


def test_prepare_frames_requires_policy_bounds(fp):
    bounds = {k: v for k, v in POLICY_CONFIG["chunk_bounds_by_kind"].items() if k != "PREPARE_FRAMES"}
    u = fp(dict(POLICY_CONFIG, chunk_bounds_by_kind=bounds))
    with pytest.raises(ServiceError) as e:
        u["features"].prepare(actor(u["a"]), u["sid"], FRAMES, "k1")
    assert (e.value.code, e.value.status) == ("policy_unconfigured", 503)
    assert u["pg"].sql("SELECT count(*)::int AS n FROM rav_pilot_job")[0]["n"] == 0


def test_source_changed_during_prepare_publishes_nothing(fp, monkeypatch):
    from src.rav.store import pilot_objects

    u = fp()
    pg, a, s3 = u["pg"], u["a"], u["s3"]
    dto, _ = u["features"].prepare(actor(a), u["sid"], FRAMES, "k1")
    real = pilot_objects.written_batch

    def change_source(db, lease, keys):   # nguồn bị đổi trạng thái khi job đang chạy (sau claim)
        real(db, lease, keys)
        pg.sql("UPDATE rav_pilot_source_asset SET availability = 'DELETE_PENDING' WHERE id = %s", (u["sid"],))
    monkeypatch.setattr(pilot_objects, "written_batch", change_source)
    assert u["dispatch"].run_once()
    job = pg.sql("SELECT state, error FROM rav_pilot_job WHERE id = %s", (dto.job.id,))[0]
    assert job["state"] == "FAILED" and job["error"]["code"] == "source_unavailable"
    assert pg.sql("SELECT count(*)::int AS n FROM rav_pilot_frame_set")[0]["n"] == 0 and frame_objects(s3)
    assert u["supervisor"].run_reconciliation()   # ảnh không được nhận → xóa rồi mới trả hold
    assert frame_objects(s3) == [] and set(receipts(pg)) == {"DELETED"}
    assert pg.sql("SELECT coalesce(sum(persisted_bytes + reserved_bytes), 0)::bigint AS n FROM rav_pilot_storage_account")[0]["n"] == 0


def _cli():
    spec = importlib.util.spec_from_file_location("pilot_prepare_system_frames",
                                                  Path(__file__).parents[1] / "scripts" / "pilot_prepare_system_frames.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_operator_cli_prepares_system_frames_once(fp, capsys):
    import json

    import psycopg
    from psycopg.rows import dict_row

    u = fp()
    pg, b, s3 = u["pg"], u["b"], u["s3"]
    sys_path = make_video(u["tmp"] / "s.mp4", seconds=2, shift=50)
    sys_sid, sys_core, sys_frames = seed_source(pg, s3, sys_path, None)
    cli = _cli()
    assert cli.main(["--database-url", pg.admin_url, "--prefix", "pilot-test", "--workdir", str(u["tmp"] / "cli")]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["dry_run"] and dry["sources"] == 1 and dry["objects"] == 2 * len(sys_frames)
    assert pg.sql("SELECT count(*)::int AS n FROM rav_pilot_frame_set")[0]["n"] == 0   # dry-run không ghi
    with pytest.raises(SystemExit):
        cli.main(["--database-url", pg.admin_url, "--prefix", "pilot-test", "--apply", "--operator-confirm", "sai",
                  "--policy-version", u["policy_version"]])
    with psycopg.connect(pg.admin_url, row_factory=dict_row) as conn:
        plan = cli.build_plan(conn.cursor(), "pilot-test")
    assert plan["inventory_hash"] == dry["inventory_hash"]
    result = cli.apply_plan(pg.admin_url, s3, plan, u["policy_version"], u["tmp"] / "cli")
    assert [p["source_id"] for p in result["prepared"]] == [str(sys_sid)] and result["skipped"] == []
    fs = pg.sql("SELECT * FROM rav_pilot_frame_set")[0]
    assert (fs["scope"], fs["owner_id"], fs["state"], fs["job_id"]) == ("SYSTEM", None, "READY", None)
    keys = [r["object_key"] for r in pg.sql("SELECT object_key FROM rav_pilot_frame")]
    assert len(keys) == 2 * len(sys_frames) and all(k.startswith(f"pilot-test/system/t/frames/{sys_core}/{fs['id']}/") for k in keys)
    assert accounts(pg) == {"GLOBAL": fs["total_bytes"]}   # chỉ GLOBAL, không USER
    _, _, jpegs = expected_jpegs(sys_path)
    m = u["media"].request(actor(b), MediaRequestIn(source_id=sys_sid, unit_id=unit(sys_core, sys_frames[1]), variant="frame"))
    assert m.state == "READY" and requests.get(m.url).content == jpegs[("FRAME", sys_frames[1].idx)]
    with psycopg.connect(pg.admin_url, row_factory=dict_row) as conn:
        assert cli.build_plan(conn.cursor(), "pilot-test")["sources"] == []
    rerun = cli.apply_plan(pg.admin_url, s3, plan, u["policy_version"], u["tmp"] / "cli")   # chạy lại kế hoạch cũ: ghi đè đúng key
    assert rerun == {"prepared": [], "skipped": [str(sys_sid)]}
    assert accounts(pg) == {"GLOBAL": fs["total_bytes"]} and pg.sql("SELECT count(*)::int AS n FROM rav_pilot_frame_set")[0]["n"] == 1
    assert sorted(k for k in _system_frame_keys(s3)) == sorted(keys)


def _system_frame_keys(s3) -> list[str]:
    page = s3.client.list_objects_v2(Bucket=s3.bucket, Prefix=f"{s3.prefix}/system/")
    return [o["Key"] for o in page.get("Contents", []) if "/frames/" in o["Key"]]


@pytest.mark.parametrize("policy_state", ["missing", "disabled", "quota"])
def test_system_import_preflight_never_writes_images(fp, policy_state):
    """Regression: the old apply path wrote 16 S3 images before rejecting policy/quota."""
    import psycopg
    from psycopg.rows import dict_row

    u = fp()
    pg, s3 = u["pg"], u["s3"]
    sid, _, frames = seed_source(pg, s3, make_video(u["tmp"] / "sys.mp4", seconds=2), None)
    assert len(frames) == 8
    cli = _cli()
    with psycopg.connect(pg.admin_url, row_factory=dict_row) as conn:
        plan = cli.build_plan(conn.cursor(), "pilot-test")
    version = u["policy_version"]
    if policy_state == "missing":
        version = "does-not-exist"
    elif policy_state == "disabled":
        pg.sql("UPDATE rav_pilot_quota_policy SET enabled = false WHERE version = %s", (version,))
    else:
        cfg = dict(POLICY_CONFIG, storage_global_bytes=1)
        new_pol = pg.policy(config=cfg)
        version = pg.sql("SELECT version FROM rav_pilot_quota_policy WHERE id = %s", (new_pol,))[0]["version"]
    with pytest.raises(Exception):
        cli.apply_plan(pg.admin_url, s3, plan, version, u["tmp"] / "cli")
    assert _system_frame_keys(s3) == []
    assert pg.sql("SELECT count(*)::int AS n FROM rav_pilot_frame_set")[0]["n"] == 0
    assert pg.sql("SELECT coalesce(sum(persisted_bytes + reserved_bytes), 0)::bigint AS n "
                  "FROM rav_pilot_storage_account")[0]["n"] == 0
    assert pg.sql("SELECT availability FROM rav_pilot_source_asset WHERE id = %s", (sid,))[0]["availability"] == "READY"


def test_system_import_put_failure_cleans_up_and_allows_retry(fp, monkeypatch):
    """PUT thất bại sau 1 object: cleanup DELETE+HEAD dọn sạch, không partial READY, retry thành công."""
    import psycopg
    from psycopg.rows import dict_row

    u = fp()
    pg, s3 = u["pg"], u["s3"]
    sid, _, frames = seed_source(pg, s3, make_video(u["tmp"] / "sys.mp4", seconds=2), None)
    cli = _cli()
    with psycopg.connect(pg.admin_url, row_factory=dict_row) as conn:
        plan = cli.build_plan(conn.cursor(), "pilot-test")

    real_put = s3.put_file
    put_count = 0

    def failing_put(key, path, sha256, content_type):
        nonlocal put_count
        put_count += 1
        if put_count > 1:
            raise RuntimeError("S3 put network error")
        return real_put(key, path, sha256, content_type)

    monkeypatch.setattr(s3, "put_file", failing_put)
    with pytest.raises(RuntimeError, match="S3 put network error"):
        cli.apply_plan(pg.admin_url, s3, plan, u["policy_version"], u["tmp"] / "cli")

    # Đã dọn sạch: không object trên S3, không frame set, reserved/persisted về 0
    assert _system_frame_keys(s3) == []
    assert pg.sql("SELECT count(*)::int AS n FROM rav_pilot_frame_set")[0]["n"] == 0
    assert pg.sql("SELECT coalesce(sum(persisted_bytes + reserved_bytes), 0)::bigint AS n "
                  "FROM rav_pilot_storage_account")[0]["n"] == 0
    receipt = pg.sql("SELECT * FROM rav_pilot_import_receipt")[0]
    assert receipt["state"] == "FAILED" and receipt["mapping"]["cleaned"] is True
    assert receipt["counts"]["reserved_bytes"] == 0

    # Retry trong cùng process (unpatch put): thành công
    monkeypatch.setattr(s3, "put_file", real_put)
    res = cli.apply_plan(pg.admin_url, s3, plan, u["policy_version"], u["tmp"] / "cli")
    assert [p["source_id"] for p in res["prepared"]] == [str(sid)]
    fs = pg.sql("SELECT * FROM rav_pilot_frame_set")[0]
    assert fs["state"] == "READY"
    assert len(_system_frame_keys(s3)) == 2 * len(frames)
    assert accounts(pg) == {"GLOBAL": fs["total_bytes"]}
    receipt2 = pg.sql("SELECT * FROM rav_pilot_import_receipt WHERE id = %s", (receipt["id"],))[0]
    assert receipt2["state"] == "COMMITTED" and receipt2["counts"]["reserved_bytes"] == 0


def test_system_import_db_publish_failure_cleans_up(fp, monkeypatch):
    """DB publish thất bại: cleanup dọn sạch mọi object đã PUT và nhả reservation."""
    import psycopg
    from psycopg.rows import dict_row

    from src.rav.store import pilot_frames

    u = fp()
    pg, s3 = u["pg"], u["s3"]
    seed_source(pg, s3, make_video(u["tmp"] / "sys.mp4", seconds=2), None)
    cli = _cli()
    with psycopg.connect(pg.admin_url, row_factory=dict_row) as conn:
        plan = cli.build_plan(conn.cursor(), "pilot-test")

    def failing_insert(*args, **kwargs):
        raise RuntimeError("DB publish constraint error")

    monkeypatch.setattr(pilot_frames, "insert_set_in_tx", failing_insert)
    with pytest.raises(RuntimeError, match="DB publish constraint error"):
        cli.apply_plan(pg.admin_url, s3, plan, u["policy_version"], u["tmp"] / "cli")

    assert _system_frame_keys(s3) == []
    assert pg.sql("SELECT count(*)::int AS n FROM rav_pilot_frame_set")[0]["n"] == 0
    assert pg.sql("SELECT coalesce(sum(persisted_bytes + reserved_bytes), 0)::bigint AS n "
                  "FROM rav_pilot_storage_account")[0]["n"] == 0
    receipt = pg.sql("SELECT * FROM rav_pilot_import_receipt")[0]
    assert receipt["state"] == "FAILED" and receipt["mapping"]["cleaned"] is True


def test_system_import_cleanup_failure_retains_receipt_and_reservation(fp, monkeypatch):
    """Cleanup DELETE thất bại: giữ nguyên receipt STAGING và reservation để đối soát/recovery."""
    import psycopg
    from psycopg.rows import dict_row

    u = fp()
    pg, s3 = u["pg"], u["s3"]
    seed_source(pg, s3, make_video(u["tmp"] / "sys.mp4", seconds=2), None)
    cli = _cli()
    with psycopg.connect(pg.admin_url, row_factory=dict_row) as conn:
        plan = cli.build_plan(conn.cursor(), "pilot-test")

    real_put = s3.put_file
    put_count = 0

    def failing_put(key, path, sha256, content_type):
        nonlocal put_count
        put_count += 1
        if put_count > 1:
            raise RuntimeError("S3 put error")
        return real_put(key, path, sha256, content_type)

    def failing_delete(key):
        raise RuntimeError("S3 delete service unavailable")

    monkeypatch.setattr(s3, "put_file", failing_put)
    monkeypatch.setattr(s3, "delete", failing_delete)
    with pytest.raises(RuntimeError, match="S3 delete service unavailable"):
        cli.apply_plan(pg.admin_url, s3, plan, u["policy_version"], u["tmp"] / "cli")

    # Vì cleanup lỗi, object chưa bị xóa và reservation vẫn được giữ trên DB
    assert len(_system_frame_keys(s3)) == 1
    assert pg.sql("SELECT count(*)::int AS n FROM rav_pilot_frame_set")[0]["n"] == 0
    account = pg.sql("SELECT * FROM rav_pilot_storage_account WHERE account_key = 'GLOBAL'")[0]
    assert account["reserved_bytes"] > 0
    receipt = pg.sql("SELECT * FROM rav_pilot_import_receipt")[0]
    assert receipt["state"] == "STAGING" and not receipt["mapping"].get("cleaned")
    assert receipt["counts"]["reserved_bytes"] == account["reserved_bytes"]

    # Khi process cũ vẫn còn sống: lần chạy tiếp theo bị chặn
    monkeypatch.undo()
    with pytest.raises(RuntimeError, match="process import trước còn sống"):
        cli.apply_plan(pg.admin_url, s3, plan, u["policy_version"], u["tmp"] / "cli")

    # Giả lập process cũ đã thoát (PID không còn tồn tại): recovery tự dọn dẹp và hoàn tất
    crashed_mapping = dict(receipt["mapping"])
    crashed_mapping["actor"] = dict(crashed_mapping["actor"], pid=999999)
    from psycopg.types.json import Jsonb
    pg.sql("UPDATE rav_pilot_import_receipt SET mapping = %s WHERE id = %s", (Jsonb(crashed_mapping), receipt["id"]))

    res = cli.apply_plan(pg.admin_url, s3, plan, u["policy_version"], u["tmp"] / "cli")
    assert len(res["prepared"]) == 1
    assert pg.sql(
        "SELECT reserved_bytes FROM rav_pilot_storage_account WHERE account_key = 'GLOBAL'")[0]["reserved_bytes"] == 0
    fs = pg.sql("SELECT * FROM rav_pilot_frame_set")[0]
    assert accounts(pg) == {"GLOBAL": fs["total_bytes"]}


def test_system_import_crash_recovery_same_host_and_cross_host(fp):
    """Crash recovery: cùng host kiểm PID/token, khác host cần attestation proof."""
    import os
    import socket
    from uuid import uuid4

    import psutil
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb

    u = fp()
    pg, s3 = u["pg"], u["s3"]
    sid, _, frames = seed_source(pg, s3, make_video(u["tmp"] / "sys.mp4", seconds=2), None)
    cli = _cli()
    with psycopg.connect(pg.admin_url, row_factory=dict_row) as conn:
        plan = cli.build_plan(conn.cursor(), "pilot-test")

    src_item = plan["sources"][0]
    key = cli._receipt_key(src_item)
    proc = psutil.Process(os.getpid())

    # Nhánh 1: Cùng host, process còn sống (chính process này) → bị chặn, kể cả khi có proof
    live_actor = {"host": socket.gethostname(), "pid": proc.pid, "started": proc.create_time(), "token": str(uuid4())}
    mapping_live = {"receipt_key": key, "source_id": src_item["source_id"], "raw_sha256": src_item["raw_sha256"],
                    "frame_set_id": src_item["frame_set_id"], "items": [], "actor": live_actor, "cleaned": False}
    pg.sql("INSERT INTO rav_pilot_import_receipt (id, idempotency_key, inventory_hash, mapping, counts, hashes, state) "
           "VALUES (%s, %s, %s, %s, %s, %s, 'STAGING')",
           (uuid4(), key, "hash1", Jsonb(mapping_live), Jsonb({"reserved_bytes": 0}), Jsonb({}), ))

    proof_spoof = f"{key}:{live_actor['host']}:{live_actor['pid']}:{live_actor['started']}:{live_actor['token']}"
    with pytest.raises(RuntimeError, match="process import trước còn sống"):
        cli.apply_plan(pg.admin_url, s3, plan, u["policy_version"], u["tmp"] / "cli", recovery_proof=proof_spoof)

    # Nhánh 2: Khác host, không có proof → bị chặn
    remote_actor = {"host": "other-node-42", "pid": 99999, "started": 123456789.0, "token": "remote-tok-1"}
    mapping_remote = {**mapping_live, "actor": remote_actor}
    pg.sql("UPDATE rav_pilot_import_receipt SET mapping = %s WHERE idempotency_key = %s", (Jsonb(mapping_remote), key))

    with pytest.raises(RuntimeError, match="chưa có bằng chứng đã dừng"):
        cli.apply_plan(pg.admin_url, s3, plan, u["policy_version"], u["tmp"] / "cli")

    # Nhánh 3: Khác host, proof sai → bị chặn
    with pytest.raises(RuntimeError, match="chưa có bằng chứng đã dừng"):
        cli.apply_plan(pg.admin_url, s3, plan, u["policy_version"], u["tmp"] / "cli", recovery_proof="sai-proof")

    # Nhánh 4: Khác host, proof đúng attestation → thành công
    valid_proof = f"{key}:{remote_actor['host']}:{remote_actor['pid']}:{remote_actor['started']}:{remote_actor['token']}"
    res = cli.apply_plan(pg.admin_url, s3, plan, u["policy_version"], u["tmp"] / "cli", recovery_proof=valid_proof)
    assert len(res["prepared"]) == 1
    fs = pg.sql("SELECT * FROM rav_pilot_frame_set")[0]
    assert fs["state"] == "READY"


def test_system_import_concurrent_lock_and_source_changed_mid_flight(fp, monkeypatch):
    """Hai importer cùng nguồn bị chặn bởi advisory lock; nguồn đổi trạng thái giữa chừng không publish."""
    import psycopg
    from psycopg.rows import dict_row

    u = fp()
    pg, s3 = u["pg"], u["s3"]
    sid, _, _ = seed_source(pg, s3, make_video(u["tmp"] / "sys.mp4", seconds=2), None)
    cli = _cli()
    with psycopg.connect(pg.admin_url, row_factory=dict_row) as conn:
        plan = cli.build_plan(conn.cursor(), "pilot-test")

    key = cli._receipt_key(plan["sources"][0])

    # Nhánh 1: Importer khác đang giữ session lock
    with psycopg.connect(pg.admin_url, autocommit=True) as lock_conn:
        lock_cur = lock_conn.cursor()
        lock_cur.execute("SELECT pg_try_advisory_lock(hashtext(%s)) AS l", (key,))
        assert lock_cur.fetchone()[0] is True
        with pytest.raises(RuntimeError, match="import cùng nguồn đang chạy"):
            cli.apply_plan(pg.admin_url, s3, plan, u["policy_version"], u["tmp"] / "cli")
        lock_cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (key,))

    # Nhánh 2: Nguồn bị đổi giữa chừng (sau khi encode/trước publish)
    real_put = s3.put_file

    def change_source_on_put(k, p, h, ct):
        pg.sql("UPDATE rav_pilot_source_asset SET availability = 'DELETE_PENDING' WHERE id = %s", (sid,))
        return real_put(k, p, h, ct)

    monkeypatch.setattr(s3, "put_file", change_source_on_put)
    with pytest.raises(RuntimeError, match="nguồn đổi trước publish"):
        cli.apply_plan(pg.admin_url, s3, plan, u["policy_version"], u["tmp"] / "cli")

    assert _system_frame_keys(s3) == []
    assert pg.sql("SELECT count(*)::int AS n FROM rav_pilot_frame_set")[0]["n"] == 0
    assert pg.sql("SELECT coalesce(sum(persisted_bytes + reserved_bytes), 0)::bigint AS n "
                  "FROM rav_pilot_storage_account")[0]["n"] == 0


@pytest.mark.parametrize("changed", ["unavailable", "delete_pending", "disabled", "missing"])
def test_system_cleanup_only_recovers_when_new_import_is_forbidden(fp, monkeypatch, capsys, changed):
    import json

    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb

    u = fp()
    pg, s3 = u["pg"], u["s3"]
    sid, _, _ = seed_source(pg, s3, make_video(u["tmp"] / "sys.mp4", seconds=2), None)
    cli = _cli()
    with psycopg.connect(pg.admin_url, row_factory=dict_row) as conn:
        plan = cli.build_plan(conn.cursor(), s3.prefix)
    real_delete = s3.delete

    def fail_delete(key):
        raise RuntimeError("cleanup unavailable")

    def fail_publish(*args, **kwargs):
        raise RuntimeError("publish unavailable")

    monkeypatch.setattr(s3, "delete", fail_delete)
    monkeypatch.setattr(cli.pilot_frames, "insert_set_in_tx", fail_publish)
    with pytest.raises(RuntimeError, match="cleanup unavailable"):
        cli.apply_plan(pg.admin_url, s3, plan, u["policy_version"], u["tmp"] / "cli")
    receipt = pg.sql("SELECT * FROM rav_pilot_import_receipt")[0]
    assert receipt["counts"]["reserved_bytes"] > 0 and _system_frame_keys(s3)
    mapping = {**receipt["mapping"], "actor": {**receipt["mapping"]["actor"], "pid": 999999}}
    pg.sql("UPDATE rav_pilot_import_receipt SET mapping = %s WHERE id = %s", (Jsonb(mapping), receipt["id"]))
    if changed == "unavailable":
        pg.sql("UPDATE rav_pilot_source_asset SET availability = 'UNAVAILABLE' WHERE id = %s", (sid,))
    elif changed == "delete_pending":
        pg.sql("UPDATE rav_pilot_source_asset SET availability = 'DELETE_PENDING' WHERE id = %s", (sid,))
    elif changed == "disabled":
        pg.sql("UPDATE rav_pilot_quota_policy SET enabled = false WHERE version = %s", (u["policy_version"],))
    monkeypatch.setattr(s3, "delete", real_delete)
    monkeypatch.setattr(cli.pilot_storage.PilotStorage, "from_env", lambda: s3)
    if changed == "missing":
        with psycopg.connect(pg.admin_url, row_factory=dict_row) as conn:
            stale_plan = cli.build_plan(conn.cursor(), s3.prefix, cleanup_only=True)
        mapping["actor"]["token"] = "changed-after-dry-run"
        pg.sql("UPDATE rav_pilot_import_receipt SET mapping = %s WHERE id = %s", (Jsonb(mapping), receipt["id"]))
        with pytest.raises(RuntimeError, match="receipt đổi sau dry-run"):
            cli.apply_plan(pg.admin_url, s3, stale_plan, None, u["tmp"] / "cli")
        assert _system_frame_keys(s3)
        assert pg.sql("SELECT reserved_bytes FROM rav_pilot_storage_account WHERE account_key = 'GLOBAL'")[0]["reserved_bytes"] > 0
    argv = ["--database-url", pg.admin_url, "--prefix", s3.prefix, "--cleanup-only", "--workdir", str(u["tmp"] / "cli")]
    assert cli.main(argv) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["recoveries"] == 1 and dry["sources"] == 0
    # No policy argument: recovery must not depend on a policy allowing new imports.
    assert cli.main(argv + ["--apply", "--operator-confirm", dry["inventory_hash"]]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {"prepared": [], "skipped": [], "recovered": [str(receipt["id"])]}
    assert not _system_frame_keys(s3)
    assert pg.sql("SELECT reserved_bytes, persisted_bytes FROM rav_pilot_storage_account WHERE account_key = 'GLOBAL'") == [
        {"reserved_bytes": 0, "persisted_bytes": 0}]
    assert not pg.sql("SELECT * FROM rav_pilot_frame_set")
    with psycopg.connect(pg.admin_url, row_factory=dict_row) as conn:
        assert not cli.build_plan(conn.cursor(), s3.prefix, cleanup_only=True)["recoveries"]


def test_system_import_checks_raw_temp_budget_before_download(tmp_path, monkeypatch):
    from unittest.mock import Mock

    cli = _cli()
    storage = Mock()
    limits = {"worker_temp_bytes": 1024, "max_frame_bytes": 1024}
    with pytest.raises(RuntimeError, match="raw vượt giới hạn temp"):
        cli._materialize(storage, {}, tmp_path, limits, {"size_bytes": 2048})
    storage.download.assert_not_called()


def test_encode_batch_checks_budget_before_writing_variant(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from src.rav.worker import frame_writer

    monkeypatch.setattr(frame_writer, "encode", lambda rgb: [("FRAME", b"123456", 1, 1), ("THUMBNAIL", b"123456", 1, 1)])
    decoder = SimpleNamespace(read_exact=lambda video, frame: None)
    with pytest.raises(ServiceError, match="đĩa tạm"):
        frame_writer.encode_batch(decoder, SimpleNamespace(id="v"), [SimpleNamespace(t=0, idx=0)], tmp_path,
                                  lambda *args: "unused", max_bytes=10)
    assert sum(p.stat().st_size for p in tmp_path.glob("*.jpg")) == 6
    assert not (tmp_path / "0-THUMBNAIL.jpg").exists()
