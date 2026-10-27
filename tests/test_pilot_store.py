"""Store pilot trên PostgreSQL thật (DEP2F #89): schema/migration, RLS A/B, trigger bất biến, RESTRICT, phiên, quyết định, snapshot.

Chạy: đặt `RAV_PILOT_TEST_DATABASE_URL` (xem tests/conftest.py) rồi `python -m pytest tests/test_pilot_store.py -q`.
"""

from __future__ import annotations

from uuid import uuid4

import psycopg
import pytest

from src.rav.actor import ActorContext
from src.rav.errors import ServiceError
from src.rav.store.pilot_db import (
    DEFAULT_SAMPLE_SPEC,
    CreateSessionInput,
    DecisionInput,
    PilotDB,
    SnapshotInput,
    members_hash,
)
from src.rav.store.pilot_schema import apply_migrations, schema_problem


def actor(uid) -> ActorContext:
    return ActorContext(user_id=uid)


def unit(core: str, t: float) -> str:
    return f"{core}:{t:.3f}-{t:.3f}"


@pytest.fixture
def world(pg):
    """A/B active, C disabled; A có video riêng, B có video riêng cùng core id, một video SYSTEM."""
    policy = pg.policy()
    a, b = pg.profile(policy_id=policy), pg.profile(policy_id=policy)
    c = pg.profile(policy_id=policy, active=False)
    return {"pg": pg, "db": PilotDB(pg.api_url), "a": a, "b": b, "c": c,
            "va": pg.source(a, "cam_front", n_units=12), "vb": pg.source(b, "cam_front", n_units=7),
            "sys": pg.source(None, "demo_commaai", n_units=84)}


def new_session(w, pool=None, target_k=3, key=None):
    inp = CreateSessionInput(name="phiên A", goal="xe ban ngày", target_k=target_k, pool_source_ids=pool or [w["va"]])
    return w["db"].create_session(actor(w["a"]), inp, key or str(uuid4()))


# ------------------------------------------------------------------------------
# Schema / migration
# ------------------------------------------------------------------------------
def test_migration_idempotent_and_checksum_guard(pg):
    assert apply_migrations(pg.admin_url) == []
    with psycopg.connect(pg.admin_url) as c:
        assert schema_problem(c.cursor()) is None
    pg.sql("UPDATE rav_pilot_schema_migration SET checksum = %s WHERE version = '002_pilot_runtime.sql'", ("0" * 64,))
    try:
        with psycopg.connect(pg.admin_url) as c:
            assert "lệch checksum" in schema_problem(c.cursor())
        with pytest.raises(RuntimeError):
            apply_migrations(pg.admin_url)
        with pytest.raises(ServiceError) as e:
            PilotDB(pg.api_url).ensure_schema()
        assert e.value.code == "schema_not_ready" and e.value.status == 503
    finally:
        from src.rav.store.pilot_schema import migration_checksums
        pg.sql("UPDATE rav_pilot_schema_migration SET checksum = %s WHERE version = '002_pilot_runtime.sql'",
               (migration_checksums()["002_pilot_runtime.sql"],))


def test_non_postgres_url_rejected():
    with pytest.raises(ServiceError) as e:
        PilotDB("sqlite:///x.db")
    assert e.value.code == "dependency_unavailable"


def test_no_cascade_or_set_null_foreign_keys(pg):
    rows = pg.sql("SELECT conrelid::regclass::text AS t, conname, confdeltype FROM pg_constraint "
                  "WHERE contype = 'f' AND conrelid::regclass::text LIKE 'rav_pilot_%'")
    assert rows and all(r["confdeltype"] == "r" for r in rows), [r for r in rows if r["confdeltype"] != "r"]


def test_rls_enabled_on_every_pilot_table(pg):
    rows = pg.sql("SELECT relname, relrowsecurity FROM pg_class WHERE relkind = 'r' AND relname LIKE 'rav_pilot_%'")
    assert rows and all(r["relrowsecurity"] for r in rows)


def test_spec_hash_parity_with_core():
    from src.rav.core.execution import spec_hash
    from src.rav.core.registry import REGISTRY, discover

    discover()
    d = REGISTRY.build("decoder", DEFAULT_SAMPLE_SPEC["decoder"])
    u = REGISTRY.build("unit", DEFAULT_SAMPLE_SPEC["units"])
    assert spec_hash(d, u) == DEFAULT_SAMPLE_SPEC["spec_hash"]


# ------------------------------------------------------------------------------
# RLS và quyền role
# ------------------------------------------------------------------------------
def test_rls_scopes_sources_per_user(world):
    db, a, b = world["db"], world["a"], world["b"]
    page_a = db.list_sources(actor(a))
    assert {s.id for s in page_a.items} == {world["va"], world["sys"]} and page_a.total == 2
    assert {s.id for s in db.list_sources(actor(b)).items} == {world["vb"], world["sys"]}
    with pytest.raises(ServiceError) as e:
        db.get_source(actor(a), world["vb"])
    assert e.value.status == 404


def test_authenticated_role_cannot_read_raw_key_or_write(world):
    db = world["db"]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.as_user(world["a"]) as cur:
            cur.execute("SELECT raw_key FROM rav_pilot_source_asset")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.as_user(world["a"]) as cur:
            cur.execute("INSERT INTO rav_pilot_session (id, owner_id, name, goal, target_k, spec, n_units) "
                        "VALUES (%s, %s, 'x', 'g', 1, '{}', 0)", (uuid4(), world["a"]))


def test_disabled_user_sees_nothing_through_rls(world):
    with world["db"].as_user(world["c"]) as cur:
        cur.execute("SELECT count(*) AS n FROM rav_pilot_source_asset")
        assert cur.fetchone()["n"] == 0


def test_api_role_cannot_alter_profile_security_or_create_sources(world):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with world["db"].backend() as cur:
            cur.execute("UPDATE rav_pilot_profile SET active = true WHERE user_id = %s", (world["c"],))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with world["db"].backend() as cur:
            cur.execute("UPDATE rav_pilot_profile SET policy_id = NULL WHERE user_id = %s", (world["a"],))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with world["db"].backend() as cur:
            cur.execute("INSERT INTO rav_pilot_source_asset (id, core_source_id, kind, scope, owner_id, protected, name, raw_key, "
                        "raw_sha256, size_bytes, availability) VALUES (%s, 'x', 'VIDEO', 'PRIVATE', %s, false, 'x', 'k', %s, 1, 'READY')",
                        (uuid4(), world["a"], "b" * 64))


def test_ensure_profile_bootstrap_and_concurrency(world, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    db, pg = world["db"], world["pg"]
    # 1. Không tự chọn policy enabled mới nhất nếu chưa đánh dấu signup_default
    pg.sql("UPDATE rav_pilot_quota_policy SET signup_default = false")
    p_other = pg.policy(signup_default=False, version="experimental-policy")
    u_missing = uuid4()
    with pytest.raises(ServiceError) as e:
        db.ensure_profile(u_missing, "Người mới")
    assert e.value.status == 503 and e.value.code == "policy_unconfigured"

    # 2. Hỗ trợ biến môi trường RAV_PILOT_SIGNUP_POLICY_VERSION tường minh
    monkeypatch.setenv("RAV_PILOT_SIGNUP_POLICY_VERSION", "experimental-policy")
    prof_env = db.ensure_profile(u_missing, "Người mới Env")
    assert prof_env["user_id"] == u_missing and prof_env["active"] is True
    assert prof_env["display_name"] == "Người mới Env" and prof_env["policy_id"] == p_other
    accts = {r["account_key"] for r in pg.sql(
        "SELECT account_key FROM rav_pilot_storage_account WHERE account_key = ANY(%s)",
        ([f"USER:{u_missing}", "GLOBAL"],))}
    assert accts == {f"USER:{u_missing}", "GLOBAL"}
    monkeypatch.delenv("RAV_PILOT_SIGNUP_POLICY_VERSION", raising=False)

    # 3. Dùng policy signup_default = true và kiểm tra truy cập đồng thời idempotent
    p_signup = pg.policy(signup_default=True, version="signup-default-v1")
    u_conc = uuid4()
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(lambda i: db.ensure_profile(u_conc, f"Tên {i}"), range(6)))
    assert all(r["user_id"] == u_conc and r["active"] is True and r["policy_id"] == p_signup for r in results)
    assert len({r["display_name"] for r in results}) == 1

    # 4. Không sửa profile đã tồn tại và không mở lại tài khoản bị khóa
    before_name = db.get_profile(u_conc)["display_name"]
    again = db.ensure_profile(u_conc, "Tên Khác Không Được Ghi Đè")
    assert again["display_name"] == before_name
    locked = db.ensure_profile(world["c"], "Thử mở khóa")
    assert locked["active"] is False



# ------------------------------------------------------------------------------
# Trigger bất biến / danh tính
# ------------------------------------------------------------------------------
def test_source_identity_immutable_and_never_deleted(world):
    pg = world["pg"]
    with pytest.raises(psycopg.errors.CheckViolation):
        pg.sql("UPDATE rav_pilot_source_asset SET raw_key = 'x' WHERE id = %s", (world["va"],))
    with pytest.raises(psycopg.errors.CheckViolation):
        pg.sql("DELETE FROM rav_pilot_source_asset WHERE id = %s", (world["va"],))
    pg.sql("UPDATE rav_pilot_source_asset SET name = 'đổi tên' WHERE id = %s", (world["va"],))


def test_system_source_requires_null_owner_and_protected(world):
    with pytest.raises(psycopg.errors.CheckViolation):
        world["pg"].sql("INSERT INTO rav_pilot_source_asset (id, core_source_id, kind, scope, owner_id, protected, name, raw_key, "
                        "raw_sha256, size_bytes, availability) VALUES (%s, 'x', 'VIDEO', 'SYSTEM', %s, true, 'x', 'k', %s, 1, 'READY')",
                        (uuid4(), world["a"], "b" * 64))


def test_session_source_trigger_blocks_foreign_private_source(world):
    s = new_session(world)
    with pytest.raises(psycopg.errors.CheckViolation):
        world["pg"].sql("INSERT INTO rav_pilot_session_source (session_id, owner_id, source_id, ord) VALUES (%s, %s, %s, 9)",
                        (s.id, world["a"], world["vb"]))


def test_decision_and_snapshot_rows_immutable(world):
    db, a = world["db"], world["a"]
    s = new_session(world)
    db.append_decisions(actor(a), s.id, DecisionInput(changes=[{"unit_id": unit("cam_front", 0), "action": "keep"}],
                                                      expected_state_version=1))
    snap = db.create_snapshot(actor(a), s.id, SnapshotInput(expected_state_version=2), "k1")
    for q in ("UPDATE rav_pilot_decision SET action = 'drop'", "DELETE FROM rav_pilot_decision",
              f"UPDATE rav_pilot_snapshot SET note = 'x' WHERE id = '{snap.id}'", "DELETE FROM rav_pilot_snapshot_member",
              "DELETE FROM rav_pilot_command_receipt"):
        with pytest.raises(psycopg.errors.CheckViolation):
            world["pg"].sql(q)


# ------------------------------------------------------------------------------
# Tạo phiên
# ------------------------------------------------------------------------------
def test_create_session_pins_pool_and_is_idempotent(world):
    db, a, pg = world["db"], world["a"], world["pg"]
    inp = CreateSessionInput(name="P", goal="g", target_k=5, pool_source_ids=[world["va"], world["sys"]])
    s = db.create_session(actor(a), inp, "same-key")
    assert s.state_version == 1 and s.n_units == 12 + 84 and s.pool_source_ids == [world["va"], world["sys"]]
    assert db.create_session(actor(a), inp, "same-key").id == s.id
    with pytest.raises(ServiceError) as e:
        db.create_session(actor(a), inp.model_copy(update={"goal": "khác"}), "same-key")
    assert e.value.code == "idempotency_conflict"
    pins = pg.sql("SELECT source_id, consumer_kind, input_role FROM rav_pilot_source_pin WHERE consumer_id = %s", (s.id,))
    assert {(p["source_id"], p["consumer_kind"], p["input_role"]) for p in pins} == {
        (world["va"], "SESSION", "POOL"), (world["sys"], "SESSION", "POOL")}
    spec = pg.sql("SELECT spec FROM rav_pilot_session WHERE id = %s", (s.id,))[0]["spec"]
    assert spec["pool"] == {"video_ids": ["cam_front", "demo_commaai"]}   # core id, không UUID (§21.2)
    assert db.get_session(actor(a), s.id) == s


def test_create_session_rejects_bad_pools(world):
    db, a, pg = world["db"], world["a"], world["pg"]
    cases = {
        "not_found": [world["vb"]],
        "duplicate_core_source_in_pool": [world["va"], pg.source(None, "cam_front", n_units=1)],
        "source_unavailable": [pg.source(a, "pending", availability="UNAVAILABLE")],
        "validation_error": [pg.source(a, "imgs", kind="IMAGE_SET")],
    }
    for code, pool in cases.items():
        with pytest.raises(ServiceError) as e:
            new_session(world, pool=pool)
        assert e.value.code == code, code
    with pytest.raises(ServiceError) as e:
        new_session(world, pool=[pg.source(a, "unverified", n_units=None)])
    assert e.value.code == "source_unavailable"
    with pytest.raises(ServiceError) as e:
        db.create_session(actor(world["c"]), CreateSessionInput(name="x", goal="g", target_k=1,
                                                                pool_source_ids=[world["sys"]]), "k")
    assert e.value.code == "account_disabled"


def test_other_user_cannot_see_or_touch_session(world):
    db = world["db"]
    s = new_session(world)
    with pytest.raises(ServiceError) as e:
        db.get_session(actor(world["b"]), s.id)
    assert e.value.status == 404
    assert db.list_sessions(actor(world["b"])).total == 0
    with pytest.raises(ServiceError) as e:
        db.append_decisions(actor(world["b"]), s.id, DecisionInput(changes=[{"unit_id": unit("cam_front", 0), "action": "keep"}],
                                                                   expected_state_version=1))
    assert e.value.status == 404


# ------------------------------------------------------------------------------
# Quyết định
# ------------------------------------------------------------------------------
def test_decisions_replay_cas_and_actor(world):
    db, a, pg = world["db"], world["a"], world["pg"]
    s = new_session(world)
    u0, u1 = unit("cam_front", 0), unit("cam_front", 0.25)
    st = db.append_decisions(actor(a), s.id, DecisionInput(changes=[{"unit_id": u0, "action": "keep"},
                                                                    {"unit_id": u1, "action": "drop"}], expected_state_version=1))
    assert (st.state_version, st.kept_ids, st.dropped_ids, st.decision_seq) == (2, [u0], [u1], 2)
    with pytest.raises(ServiceError) as e:
        db.append_decisions(actor(a), s.id, DecisionInput(changes=[{"unit_id": u0, "action": "drop"}], expected_state_version=1))
    assert e.value.code == "state_version_conflict"
    st = db.append_decisions(actor(a), s.id, DecisionInput(changes=[{"unit_id": u1, "action": "clear"}], expected_state_version=2))
    assert (st.state_version, st.dropped_ids) == (3, [])
    assert db.get_review_state(actor(a), s.id) == st
    actors = {r["actor"] for r in pg.sql("SELECT actor FROM rav_pilot_decision")}
    assert actors == {f"human:{a}"}


def test_clear_undecided_is_noop_without_row(world):
    db, a, pg = world["db"], world["a"], world["pg"]
    s = new_session(world)
    st = db.append_decisions(actor(a), s.id, DecisionInput(changes=[{"unit_id": unit("cam_front", 1), "action": "clear"}],
                                                           expected_state_version=1))
    assert st.state_version == 1 and pg.sql("SELECT count(*) AS n FROM rav_pilot_decision")[0]["n"] == 0


def test_decisions_reject_foreign_units_and_target_k_whole_batch(world):
    db, a, pg = world["db"], world["a"], world["pg"]
    s = new_session(world, target_k=1)
    with pytest.raises(ServiceError) as e:
        db.append_decisions(actor(a), s.id, DecisionInput(changes=[{"unit_id": unit("demo_commaai", 0), "action": "keep"}],
                                                          expected_state_version=1))
    assert e.value.code == "unit_not_in_pool"
    with pytest.raises(ServiceError) as e:
        db.append_decisions(actor(a), s.id, DecisionInput(changes=[{"unit_id": unit("cam_front", 0), "action": "keep"},
                                                                   {"unit_id": unit("cam_front", 1), "action": "keep"}],
                                                          expected_state_version=1))
    assert e.value.code == "target_k_reached"
    assert pg.sql("SELECT count(*) AS n FROM rav_pilot_decision")[0]["n"] == 0
    with pytest.raises(ServiceError) as e:
        db.append_decisions(actor(a), s.id, DecisionInput(changes=[{"unit_id": unit("cam_front", 0), "action": "keep",
                                                                    "proposal_n": 3}], expected_state_version=1))
    assert e.value.code == "validation_error"


def test_decision_input_forbids_identity_fields():
    with pytest.raises(ValueError):
        DecisionInput(changes=[{"unit_id": "x:0.000-0.000", "action": "keep", "actor": "human:admin"}], expected_state_version=1)
    with pytest.raises(ValueError):
        DecisionInput(changes=[{"unit_id": "x:0.000-0.000", "action": "keep", "extra": {"owner_id": "x"}}],
                      expected_state_version=1)


@pytest.mark.parametrize("bad", ["cam_front:0.1-0.1", "cam_front:0.000-0.250", "cam_front:0.100-0.100",
                                 "cam_front:3.000-3.000", "cam_front:00.000-00.000", "cam_front:garbage"])
def test_invalid_decision_rejects_whole_batch(world, bad):
    db, a, pg = world["db"], world["a"], world["pg"]
    session = new_session(world)
    with pytest.raises(ServiceError) as e:
        db.append_decisions(actor(a), session.id, DecisionInput(changes=[
            {"unit_id": unit("cam_front", 0), "action": "keep"}, {"unit_id": bad, "action": "clear"}],
            expected_state_version=1))
    assert e.value.code == "unit_not_in_source"
    assert db.get_review_state(actor(a), session.id).state_version == 1
    assert pg.sql("SELECT count(*) AS n FROM rav_pilot_decision WHERE session_id = %s", (session.id,))[0]["n"] == 0


def test_decisions_require_verified_internal_metadata(world):
    db, a, pg = world["db"], world["a"], world["pg"]
    session = new_session(world)
    pg.sql("UPDATE rav_pilot_source_asset SET metadata = '{\"n_units\":12}' WHERE id = %s", (world["va"],))
    with pytest.raises(ServiceError) as e:
        db.append_decisions(actor(a), session.id, DecisionInput(changes=[
            {"unit_id": unit("cam_front", 0), "action": "keep"}], expected_state_version=1))
    assert e.value.code == "source_unavailable"
    assert db.get_review_state(actor(a), session.id).state_version == 1


# ------------------------------------------------------------------------------
# Snapshot
# ------------------------------------------------------------------------------
def test_snapshot_server_computed_reused_and_pinned(world):
    from src.rav.service.snapshot import members_hash as local_members_hash

    db, a, pg = world["db"], world["a"], world["pg"]
    s = new_session(world, pool=[world["va"], world["sys"]], target_k=10)
    kept = [unit("demo_commaai", 0), unit("cam_front", 2), unit("cam_front", 0.5)]
    db.append_decisions(actor(a), s.id, DecisionInput(changes=[{"unit_id": u, "action": "keep"} for u in kept],
                                                      expected_state_version=1))
    snap = db.create_snapshot(actor(a), s.id, SnapshotInput(note="v1", expected_state_version=2), "snap-1")
    assert snap.members_hash == members_hash(kept) == local_members_hash(kept)
    assert (snap.n, snap.n_members, snap.decision_seq, snap.proposal_cutoff_n) == (1, 3, 3, 0)
    detail = db.get_snapshot(actor(a), snap.id)
    assert [m.unit_id for m in detail.members] == [unit("cam_front", 0.5), unit("cam_front", 2), unit("demo_commaai", 0)]
    assert detail.context["sample_spec"]["spec_hash"] == DEFAULT_SAMPLE_SPEC["spec_hash"]
    assert db.create_snapshot(actor(a), s.id, SnapshotInput(note="v1", expected_state_version=2), "snap-1") == snap
    again = db.create_snapshot(actor(a), s.id, SnapshotInput(note="khác", expected_state_version=2), "snap-2")
    assert again.id == snap.id   # cùng members/context → dùng lại snapshot cuối
    pins = pg.sql("SELECT source_id FROM rav_pilot_source_pin WHERE consumer_kind = 'SNAPSHOT' AND consumer_id = %s", (snap.id,))
    assert {p["source_id"] for p in pins} == {world["va"], world["sys"]}
    with pytest.raises(ServiceError) as e:
        db.get_snapshot(actor(world["b"]), snap.id)
    assert e.value.status == 404
    assert db.list_snapshots(actor(a), s.id).total == 1


def test_snapshot_rejects_empty_and_stale(world):
    db, a = world["db"], world["a"]
    s = new_session(world)
    with pytest.raises(ServiceError) as e:
        db.create_snapshot(actor(a), s.id, SnapshotInput(expected_state_version=1), "k")
    assert e.value.code == "snapshot_empty"
    db.append_decisions(actor(a), s.id, DecisionInput(changes=[{"unit_id": unit("cam_front", 0), "action": "keep"}],
                                                      expected_state_version=1))
    with pytest.raises(ServiceError) as e:
        db.create_snapshot(actor(a), s.id, SnapshotInput(expected_state_version=1), "k2")
    assert e.value.code == "state_version_conflict"


# ------------------------------------------------------------------------------
# Kho dữ liệu mẫu, trạng thái xuất bản & tái sử dụng artifact SYSTEM (Lát 2, §21.35)
# ------------------------------------------------------------------------------
def test_sample_catalog_published_and_unpublished_access_separation(world, monkeypatch):
    from src.rav.store.pilot_analysis import DetectIn, PilotAnalysis
    from src.rav.store.pilot_db import UpdateReferenceIn

    db, a, b, pg = world["db"], world["a"], world["b"], world["pg"]
    ana = PilotAnalysis(db)

    # Tạo 1 nguồn SYSTEM, 1 dataset version tham chiếu và 1 model SYSTEM đang xuất bản (published = true)
    sys_v = pg.source(None, "sample_city", n_units=8, name="Sample City", description="Video mẫu đô thị", published=True)
    ds_src = pg.source(None, "coco_ref", kind="IMAGE_SET", n_units=4, name="COCO Ref", published=True)
    ds_id, dver_id, mod_id = uuid4(), uuid4(), uuid4()
    pg.sql(
        "INSERT INTO rav_pilot_dataset (id, core_dataset_id, source_id, name, description) VALUES (%s, 'coco', %s, 'COCO', 'Ref')",
        (ds_id, ds_src),
    )
    pg.sql(
        "INSERT INTO rav_pilot_dataset_version (id, core_version_id, dataset_id, n, manifest, manifest_hash, annotation_format, classes) "
        "VALUES (%s, 'coco_v1', %s, 1, '{}', %s, 'yolo', '[\"car\"]')",
        (dver_id, ds_id, "f" * 64),
    )
    pg.sql(
        "INSERT INTO rav_pilot_model_asset (id, core_model_id, scope, protected, name, description, published, "
        "raw_key, sha256, size_bytes, task, class_map, validation, runtime_manifest, checkpoint_revision, provenance) "
        "VALUES (%s, 'yolo_sample', 'SYSTEM', true, 'YOLO Sample', 'Model mẫu', true, 'models/yolo.pt', %s, 2048, "
        "'detect', '{\"0\":\"car\"}', 'INFERENCE_VERIFIED', '{\"engine_version\":\"1\"}', 'r1', '{\"train_dataset\":\"COCO\"}')",
        (mod_id, "c" * 64),
    )
    pg.sql(
        "INSERT INTO rav_pilot_model_dataset_declaration (model_id, dataset_version_id, relation, declared_by) "
        "VALUES (%s, %s, 'TRAIN', %s)",
        (mod_id, dver_id, a),
    )

    # Trigger model cho phép sửa name, description, published nhưng chặn sửa sha256/raw_key
    pg.sql("UPDATE rav_pilot_model_asset SET name = 'YOLO Sample v1', description = 'Đã cập nhật' WHERE id = %s", (mod_id,))
    with pytest.raises(psycopg.errors.CheckViolation):
        pg.sql("UPDATE rav_pilot_model_asset SET sha256 = %s WHERE id = %s", ("d" * 64, mod_id))

    # User A tạo phiên gắn với sys_v và model mod_id khi cả hai đang published = true
    s_a = db.create_session(
        actor(a),
        CreateSessionInput(
            name="Phiên dùng mẫu",
            goal="g",
            target_k=4,
            pool_source_ids=[sys_v],
            reference={"dataset_version_ids": [dver_id], "model_id": mod_id},
        ),
        "sess-sample-a",
    )
    assert s_a.pool_source_ids == [sys_v]
    assert s_a.reference and s_a.reference.model_id == mod_id

    # Operator ẩn sys_v và mod_id (published = false)
    pg.sql("UPDATE rav_pilot_source_asset SET published = false WHERE id = %s", (sys_v,))
    pg.sql("UPDATE rav_pilot_model_asset SET published = false WHERE id = %s", (mod_id,))

    # 1. Kho / chọn mới: cả user A và user B đều không thấy tài nguyên đã ẩn trong danh sách mặc định
    assert sys_v not in {s.id for s in db.list_sources(actor(a)).items}
    assert sys_v not in {s.id for s in db.list_sources(actor(b)).items}
    assert mod_id not in {m.id for m in ana.list_models(actor(a))}
    assert mod_id not in {m.id for m in ana.list_models(actor(b))}

    # Người dùng thường gọi include_unpublished=True bị chặn 403
    with pytest.raises(ServiceError) as e:
        db.list_sources(actor(b), include_unpublished=True)
    assert e.value.status == 403
    with pytest.raises(ServiceError) as e:
        ana.list_models(actor(b), include_unpublished=True)
    assert e.value.status == 403

    # Admin (operations_read) xem được include_unpublished=True
    monkeypatch.setenv("RAV_PILOT_OPERATOR_IDS", str(a))
    assert sys_v in {s.id for s in db.list_sources(actor(a), include_unpublished=True).items}
    assert mod_id in {m.id for m in ana.list_models(actor(a), include_unpublished=True)}
    monkeypatch.delenv("RAV_PILOT_OPERATOR_IDS", raising=False)

    # 2. Không ai được tạo phiên mới hoặc gắn mới tài nguyên SYSTEM đang ẩn (published = false)
    with pytest.raises(ServiceError) as e_a:
        db.create_session(actor(a), CreateSessionInput(name="Mới", goal="g", target_k=2, pool_source_ids=[sys_v]), "new-a")
    assert e_a.value.status == 409 and e_a.value.code == "source_unavailable"
    with pytest.raises(ServiceError) as e_b:
        db.create_session(actor(b), CreateSessionInput(name="Mới", goal="g", target_k=2, pool_source_ids=[sys_v]), "new-b")
    assert e_b.value.status == 404 and e_b.value.code == "not_found"

    s_b = db.create_session(actor(b), CreateSessionInput(name="Phiên B", goal="g", target_k=2, pool_source_ids=[world["vb"]]), "sess-b-own")
    with pytest.raises(ServiceError) as e:
        db.update_session_reference(
            actor(b), s_b.id, UpdateReferenceIn(expected_state_version=1, reference={"dataset_version_ids": [dver_id], "model_id": mod_id})
        )
    assert e.value.status == 404 and e.value.code == "not_found"

    # 3. User B (chưa từng có phiên gắn sys_v / mod_id) đoán ID sẽ bị 404 cả ở adapter và RLS
    with pytest.raises(ServiceError) as e:
        db.get_source(actor(b), sys_v)
    assert e.value.status == 404
    with pytest.raises(ServiceError) as e:
        db.get_model(actor(b), mod_id)
    assert e.value.status == 404
    with db.as_user(b) as cur:
        cur.execute("SELECT id FROM rav_pilot_source_asset WHERE id = %s", (sys_v,))
        assert cur.fetchone() is None
        cur.execute("SELECT id FROM rav_pilot_model_asset WHERE id = %s", (mod_id,))
        assert cur.fetchone() is None

    # 4. User A (đã có phiên s_a gắn sys_v và mod_id) vẫn đọc được bình thường trong phiên cũ
    src_a = db.get_source(actor(a), sys_v)
    assert src_a.id == sys_v and src_a.published is False and src_a.description == "Video mẫu đô thị"
    mod_a = db.get_model(actor(a), mod_id)
    assert mod_a.id == mod_id and mod_a.published is False and mod_a.name == "YOLO Sample v1"
    ref_a = db.get_session_reference(actor(a), s_a.id)
    assert ref_a.model and ref_a.model.id == mod_id
    with db.as_user(a) as cur:
        cur.execute("SELECT id FROM rav_pilot_source_asset WHERE id = %s", (sys_v,))
        assert cur.fetchone()["id"] == sys_v
        cur.execute("SELECT id FROM rav_pilot_model_asset WHERE id = %s", (mod_id,))
        assert cur.fetchone()["id"] == mod_id
    # User A vẫn ghi quyết định trong phiên cũ bình thường
    st_a = db.append_decisions(actor(a), s_a.id, DecisionInput(changes=[{"unit_id": unit("sample_city", 0), "action": "keep"}],
                                                               expected_state_version=s_a.state_version))
    assert st_a.kept_ids == [unit("sample_city", 0)]

    # Ngược lại user B có proposal trong phiên của B nhưng cố gọi YOLO với mod_id ẩn → 404
    import json

    pg.sql(
        "INSERT INTO rav_pilot_proposal (id, session_id, owner_id, n, frozen_input, input_hash, picks, evidence, fields_used, state_version) "
        "VALUES (%s, %s, %s, 1, '{}', 'h1', %s, '{}', '[]', 1)",
        (uuid4(), s_b.id, b, json.dumps([{"unit_id": unit("cam_rear", 0), "source_id": str(world["vb"]), "rank": 1, "combined": 1.0, "breakdown": {}}])),
    )
    with pytest.raises(ServiceError) as e:
        ana.request_detections(actor(b), s_b.id, 1, DetectIn(model_id=mod_id), "det-b-unpub")
    assert e.value.status == 404


def test_two_users_share_system_source_artifacts_and_independent_decisions(world):
    import json

    from src.rav.store import pilot_frames
    from src.rav.store.pilot_curation import freeze_session_in_tx
    from src.rav.store.pilot_features import PilotFeatures, PreparationIn, input_hash
    from src.rav.store.pilot_runtime import PilotRuntime

    db, a, b, pg = world["db"], world["a"], world["b"], world["pg"]
    sys_id = world["sys"]
    src_row = pg.sql("SELECT * FROM rav_pilot_source_asset WHERE id = %s", (sys_id,))[0]

    # Operator chuẩn bị sẵn 1 lần: FRAME_SET + DINO + CLIP đều mang scope = 'SYSTEM', owner_id = NULL
    fset_id = uuid4()
    pg.sql(
        "INSERT INTO rav_pilot_frame_set (id, scope, owner_id, source_id, raw_sha256, sample_spec_hash, encoding_version, "
        "n_units, object_count, total_bytes, state) VALUES (%s, 'SYSTEM', NULL, %s, %s, %s, %s, 84, 168, 10000, 'READY')",
        (fset_id, sys_id, src_row["raw_sha256"], DEFAULT_SAMPLE_SPEC["spec_hash"], pilot_frames.ENCODING_VERSION),
    )
    art_ids = {}
    for feat in ("DINO", "CLIP"):
        aid = uuid4()
        art_ids[feat] = aid
        pg.sql(
            "INSERT INTO rav_pilot_artifact (id, owner_id, scope, source_id, kind, variant, input_hash, object_key, "
            "sha256, size_bytes, state, policy_version, manifest) "
            "VALUES (%s, NULL, 'SYSTEM', %s, 'FEATURE', %s, %s, %s, %s, 4096, 'READY', 'v1', %s)",
            (
                aid,
                sys_id,
                feat,
                input_hash(src_row, feat),
                f"system/features/{sys_id}/{feat}.npz",
                "e" * 64,
                json.dumps({"field_keys": {"DINO": ["emb.dinov2_s", "hash.phash"], "CLIP": ["emb.clip_b32"]}[feat]}),
            ),
        )

    # Cả hai tài khoản A và B đều thấy sẵn sàng (ready=True) và gọi prepare không sinh bất kỳ job nào
    feats_svc = PilotFeatures(db)
    for u in (a, b):
        s_dto = db.get_source(actor(u), sys_id)
        assert s_dto.frames.ready is True and s_dto.dino.ready is True and s_dto.clip.ready is True
        for feat_name in ("FRAMES", "DINO", "CLIP"):
            prep_dto, status = feats_svc.prepare(actor(u), sys_id, PreparationIn(feature=feat_name), f"prep-{u}-{feat_name}")
            assert status == 200 and prep_dto.ready is True and prep_dto.job is None
    assert pg.sql("SELECT count(*) AS n FROM rav_pilot_job")[0]["n"] == 0

    # Hai tài khoản cùng tạo phiên trên video SYSTEM, dùng lại cùng artifact và duyệt độc lập
    s1 = db.create_session(actor(a), CreateSessionInput(name="A-sys", goal="g", target_k=5, pool_source_ids=[sys_id]), "s-a-sys")
    s2 = db.create_session(actor(b), CreateSessionInput(name="B-sys", goal="g", target_k=5, pool_source_ids=[sys_id]), "s-b-sys")

    u0 = unit("demo_commaai", 0)
    db.append_decisions(actor(a), s1.id, DecisionInput(changes=[{"unit_id": u0, "action": "keep"}], expected_state_version=1))
    db.append_decisions(actor(b), s2.id, DecisionInput(changes=[{"unit_id": u0, "action": "drop"}], expected_state_version=1))

    assert db.get_review_state(actor(a), s1.id).kept_ids == [u0]
    assert db.get_review_state(actor(a), s1.id).dropped_ids == []
    assert db.get_review_state(actor(b), s2.id).kept_ids == []
    assert db.get_review_state(actor(b), s2.id).dropped_ids == [u0]

    rt = PilotRuntime(db)
    with db.backend() as cur:
        base_a, _ = freeze_session_in_tx(db, rt, cur, a, s1.id, 2, "PROPOSE", ("DINO", "CLIP"))
        base_b, _ = freeze_session_in_tx(db, rt, cur, b, s2.id, 2, "PROPOSE", ("DINO", "CLIP"))
    assert base_a["pool"][0]["features"]["DINO"]["artifact_id"] == str(art_ids["DINO"])
    assert base_b["pool"][0]["features"]["DINO"]["artifact_id"] == str(art_ids["DINO"])
    assert base_a["pool"][0]["features"]["CLIP"]["artifact_id"] == str(art_ids["CLIP"])
    assert base_b["pool"][0]["features"]["CLIP"]["artifact_id"] == str(art_ids["CLIP"])

