"""Phiên mẫu SYSTEM pilot (DEP4 #90–#91): import theo inventory, đọc chỉ đọc, tạo phiên riêng trống.

Chạy: đặt `RAV_PILOT_TEST_DATABASE_URL` (tests/conftest.py) rồi `python -m pytest tests/test_pilot_templates.py -q`.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from src.rav.actor import ActorContext
from src.rav.errors import ServiceError
from src.rav.store.pilot_db import PilotDB, canonical_hash
from src.rav.store.pilot_runtime import PilotRuntime
from src.rav.store.pilot_templates import ForkInput, PilotTemplates, apply_plan
from tests.conftest import make_video

ROOT = Path(__file__).resolve().parents[1]


def load_script():
    spec = importlib.util.spec_from_file_location("pilot_import_demo", ROOT / "scripts/pilot_import_demo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def actor(uid) -> ActorContext:
    return ActorContext(user_id=uid)


def legacy_sqlite(path: Path, core: str) -> None:
    """SQLite cùng tên cột với output/rav.sqlite local: 1 phiên, 2 đề xuất, 3 quyết định, 1 snapshot."""
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE session (id TEXT, name TEXT, created_at TEXT, spec TEXT, n_units INT);
        CREATE TABLE proposal (session_id TEXT, n INT, round INT, created_at TEXT, snapshot TEXT, picks TEXT, relaxed INT);
        CREATE TABLE decision (seq INTEGER PRIMARY KEY, session_id TEXT, unit_id TEXT, action TEXT, actor TEXT, goal TEXT,
                               proposal_n INT, decided_at TEXT, shown_at TEXT, reason TEXT, confidence REAL, extra TEXT);
        CREATE TABLE snapshot (id TEXT, session_id TEXT, n INT, created_at TEXT, created_by TEXT, note TEXT, decision_seq INT,
                               members_hash TEXT, n_members INT, context TEXT, context_hash TEXT);
    """)
    spec = {"pool": {"video_ids": [core]}, "goal": "Giữ frame rõ", "target_k": 5, "reference": {}}
    c.execute("INSERT INTO session VALUES ('s1', 'x', '2026-10-01T00:00:00', ?, 20)", (json.dumps(spec),))
    u = [f"{core}:{t:.3f}-{t:.3f}" for t in (0.0, 0.5, 1.0)]
    for n, picks in ((1, u[:2]), (2, u[2:])):
        c.execute("INSERT INTO proposal VALUES ('s1', ?, ?, ?, ?, ?, 0)",
                  (n, n, f"2026-10-01T00:0{n}:00", json.dumps({"config": {"name": "diversity"}, "batch_size": 2, "seed": 1,
                                                                "kept_ids": ["leak?"]}), json.dumps([{"unit_id": x} for x in picks])))
    for seq, (uid, action, pn, t) in enumerate([(u[0], "keep", 1, "01:30"), (u[1], "drop", 1, "01:40"), (u[2], "keep", 2, "02:30")], 1):
        c.execute("INSERT INTO decision VALUES (?, 's1', ?, ?, 'human:local', 'g', ?, ?, NULL, 'lý do', NULL, '{}')",
                  (seq, uid, action, pn, f"2026-10-01T00:{t}"))
    c.execute("INSERT INTO snapshot VALUES ('x', 's1', 1, '2026-10-01T00:03:00', 'ai:claude-opus-5-5', NULL, 3, ?, 2, '{}', 'h')",
              ("a" * 64,))
    c.commit()
    c.close()


@pytest.fixture
def tpl(pg, tmp_path):
    videos = tmp_path / "videos"
    videos.mkdir()
    path = make_video(videos / "v.mp4", seconds=2)
    core = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    legacy_sqlite(tmp_path / "rav.sqlite", core)
    inventory = {"seed_version": "t1", "templates": [{"template_key": "demo-t", "legacy_session_id": "s1", "name": "Mẫu thử"}]}
    plan, _ = load_script().build_plan(inventory, tmp_path / "rav.sqlite", videos, "pilot-test")
    policy = pg.policy()
    a, b = pg.profile(policy_id=policy), pg.profile(policy_id=policy)
    return {"pg": pg, "plan": plan, "hash": canonical_hash(plan), "a": a, "b": b, "core": core,
            "svc": PilotTemplates(PilotDB(pg.api_url))}


def apply(pg, plan, h):
    with psycopg.connect(pg.admin_url, row_factory=dict_row) as conn, conn.transaction():
        return apply_plan(conn.cursor(), plan, h)


def test_plan_is_deterministic_filtered_and_pathless(tpl, tmp_path):
    plan = tpl["plan"]
    assert canonical_hash(plan) == tpl["hash"]
    text = json.dumps(plan, ensure_ascii=False)
    assert str(tmp_path) not in text and "leak?" not in text   # không path máy, không payload thô (kept_ids)
    t = plan["templates"][0]
    assert [e["kind"] for e in t["events"]] == ["PROPOSAL", "DECISION", "DECISION", "PROPOSAL", "DECISION", "SNAPSHOT"]
    assert plan["sources"][0]["core_source_id"] == tpl["core"] and plan["sources"][0]["metadata"]["n_units"] > 0
    assert plan["sources"][0]["raw_key"].startswith("pilot-test/system/t1/sources/")


def test_apply_is_idempotent_and_guarded(tpl):
    pg = tpl["pg"]
    assert apply(pg, tpl["plan"], tpl["hash"]) == {"replayed": False, "sources": 1, "templates": 1, "events": 6}
    assert apply(pg, tpl["plan"], tpl["hash"])["replayed"] is True
    src = pg.sql("SELECT scope, owner_id, protected, availability FROM rav_pilot_source_asset")[0]
    assert (src["scope"], src["owner_id"], src["protected"], src["availability"]) == ("SYSTEM", None, True, "READY")
    assert pg.sql("SELECT availability FROM rav_pilot_demo_template_status")[0]["availability"] == "VERIFIED"
    other = json.loads(json.dumps(tpl["plan"]))
    other["templates"][0]["name"] = "đổi"
    with pytest.raises(ServiceError) as e:
        apply(pg, other, canonical_hash(other))
    assert e.value.code == "idempotency_conflict"
    with pytest.raises(psycopg.errors.CheckViolation):
        pg.sql("UPDATE rav_pilot_demo_template_event SET ord = 99")


def test_templates_readable_by_every_active_account(tpl):
    apply(tpl["pg"], tpl["plan"], tpl["hash"])
    svc = tpl["svc"]
    for uid in (tpl["a"], tpl["b"]):
        [t] = svc.list(actor(uid))
        assert (t.name, t.availability, t.goal, t.target_k) == ("Mẫu thử", "VERIFIED", "Giữ frame rõ", 5)
        assert t.counts.model_dump() == {"proposals": 2, "decisions": 3, "snapshots": 1}
        detail = svc.get(actor(uid), t.id)
        assert len(detail.history) == 6 and set(detail.history[0].payload) == {"n", "recipe", "batch_size", "picks", "created_at"}
    disabled = tpl["pg"].profile(active=False)
    with pytest.raises(ServiceError) as e:
        svc.list(actor(disabled))
    assert e.value.code == "account_disabled"


def test_fork_creates_empty_private_session(tpl):
    pg, svc, a, b = tpl["pg"], tpl["svc"], tpl["a"], tpl["b"]
    apply(pg, tpl["plan"], tpl["hash"])
    [t] = svc.list(actor(a))
    before = pg.sql("SELECT count(*) AS n, string_agg(payload_hash, ',' ORDER BY ord) AS h FROM rav_pilot_demo_template_event")[0]
    r = svc.fork(actor(a), t.id, ForkInput(), "f1")
    s = r.session
    assert (r.replayed, r.counts.model_dump()) == (False, {"proposals": 0, "decisions": 0, "snapshots": 0})
    assert (s.state_version, s.started_from_template_id, s.goal, s.target_k, s.name) == (1, t.id, "Giữ frame rõ", 5, "Mẫu thử")
    assert s.pool_source_ids == t.source_ids
    for table in ("rav_pilot_decision", "rav_pilot_proposal", "rav_pilot_snapshot", "rav_pilot_job"):
        assert pg.sql(f"SELECT count(*) AS n FROM {table}")[0]["n"] == 0, table
    state = PilotDB(pg.api_url).get_review_state(actor(a), s.id)
    assert (state.kept_ids, state.dropped_ids, state.decision_seq) == ([], [], 0)
    again = svc.fork(actor(a), t.id, ForkInput(), "f1")
    assert again.replayed and again.session.id == s.id
    other = svc.fork(actor(a), t.id, ForkInput(name="phiên 2"), "f2")
    assert other.session.id != s.id and other.session.name == "phiên 2"
    rb = svc.fork(actor(b), t.id, ForkInput(), "f1")
    assert rb.session.id not in (s.id, other.session.id)
    with pytest.raises(ServiceError):
        PilotDB(pg.api_url).get_session(actor(b), s.id)
    assert pg.sql("SELECT count(*) AS n, string_agg(payload_hash, ',' ORDER BY ord) AS h FROM rav_pilot_demo_template_event")[0] == before
    pins = pg.sql("SELECT consumer_kind, input_role FROM rav_pilot_source_pin WHERE consumer_id = %s", (s.id,))
    assert pins == [{"consumer_kind": "SESSION", "input_role": "POOL"}]


def test_fork_blocked_template_and_system_source_protected(tpl):
    pg, svc, a = tpl["pg"], tpl["svc"], tpl["a"]
    apply(pg, tpl["plan"], tpl["hash"])
    [t] = svc.list(actor(a))
    pg.sql("UPDATE rav_pilot_demo_template_status SET availability = 'BLOCKED', reason_code = 'test'")
    with pytest.raises(ServiceError) as e:
        svc.fork(actor(a), t.id, ForkInput(), "f")
    assert e.value.code == "template_unavailable"
    with pytest.raises(ServiceError) as e:
        PilotRuntime(PilotDB(pg.api_url)).request_source_delete(actor(a), t.source_ids[0], 1, "d")
    assert e.value.status == 403
    with pytest.raises(ServiceError) as e:
        svc.get(actor(a), uuid4())
    assert e.value.status == 404


def test_system_features_import_validated_and_readable(tpl, tmp_path):
    """DEP5a §21.14: đặc trưng SYSTEM do operator import từ FieldStore local; kiểm IDs/L2; mọi tài khoản thấy ready."""
    import numpy as np

    from src.rav.components.features.clip_b32 import ClipB32
    from src.rav.components.features.dinov2_s import DinoV2S
    from src.rav.components.features.phash import PHash
    from src.rav.core.types import FieldTable
    from src.rav.store.field_store import FieldStore
    from src.rav.store.pilot_db import DEFAULT_SAMPLE_SPEC

    script, plan = load_script(), json.loads(json.dumps(tpl["plan"]))
    files = {tpl["core"]: tmp_path / "videos" / "v.mp4"}
    n = plan["sources"][0]["metadata"]["n_units"]
    from src.rav.worker.gpu_handlers import build_context
    spec = {k: DEFAULT_SAMPLE_SPEC[k] for k in ("decoder", "units")}
    ids = [u.id for u in build_context(files[tpl["core"]], {"core_source_id": tpl["core"], "spec": spec, "n_units": n}).units]
    store, part = FieldStore(tmp_path / "fields"), (tpl["core"], DEFAULT_SAMPLE_SPEC["spec_hash"])

    def unit_rows(dim):
        v = np.random.default_rng(0).normal(size=(n, dim)).astype(np.float32)
        return v / np.linalg.norm(v, axis=1, keepdims=True)
    store.put(FieldTable(DinoV2S().key, ids, unit_rows(384), "vector"), part)
    store.put(FieldTable(PHash().key, ids, np.arange(n, dtype=np.uint64), "scalar"), part)
    # CLIP thiếu → bỏ qua (không tự tính); chỉ DINO vào kế hoạch.
    plan["features"], blobs = script.feature_entries(plan["sources"], files, tmp_path / "fields", plan["seed_version"], "pilot-test")
    assert [f["feature"] for f in plan["features"]] == ["DINO"] and len(blobs) == 1
    f = plan["features"][0]
    assert f["object_key"].startswith("pilot-test/system/t1/features/") and f["manifest"]["fields"] == ["emb.dinov2_s", "hash.phash"]
    h = canonical_hash(plan)
    assert apply(tpl["pg"], plan, h)["features"] == 1
    assert apply(tpl["pg"], plan, h)["replayed"]
    art = tpl["pg"].sql("SELECT * FROM rav_pilot_artifact")[0]
    assert art["scope"] == "SYSTEM" and art["owner_id"] is None and art["state"] == "READY"
    api = PilotDB(tpl["pg"].api_url)
    for who in (tpl["a"], tpl["b"]):
        src = api.get_source(actor(who), plan["sources"][0]["id"])
        assert src.dino.ready and not src.clip.ready
    # Vector không chuẩn L2 hoặc IDs lệch decoder → operator bị chặn, không vào kế hoạch.
    store.put(FieldTable(ClipB32().key, ids, np.ones((n, 512), np.float32), "vector"), part)
    with pytest.raises(SystemExit, match="L2"):
        script.feature_entries(plan["sources"], files, tmp_path / "fields", "t1", "pilot-test")
    store.put(FieldTable(ClipB32().key, ids[::-1], unit_rows(512), "vector"), part)
    with pytest.raises(SystemExit, match="ordered unit IDs"):
        script.feature_entries(plan["sources"], files, tmp_path / "fields", "t1", "pilot-test")
