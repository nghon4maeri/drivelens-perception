"""DEP5b đề xuất trên worker cho Studio v2 (CONTRACTS §21.15, #95): PostgreSQL thật + S3 moto + worker PROPOSE thật.

Đặc trưng giả có seed (artifact NPZ cho pilot, cùng mảng vào FieldStore cho đường core local) → parity picks/ID/thứ tự/điểm.
Chạy: đặt `RAV_PILOT_TEST_DATABASE_URL` (tests/conftest.py) rồi `python -m pytest tests/test_pilot_curation.py -q`.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest

from src.rav.components.features.clip_b32 import ClipB32
from src.rav.components.features.dinov2_s import DinoV2S
from src.rav.components.features.phash import PHash
from src.rav.core.registry import REGISTRY, discover
from src.rav.core.types import FieldTable, ReviewState
from src.rav.errors import ServiceError
from src.rav.pipeline import prepare as p1
from src.rav.pipeline.propose import propose
from src.rav.service.proposal import to_proposals
from src.rav.store.field_store import FieldStore
from src.rav.store.pilot_curation import PILOT_RECIPES, TRAINING_RECIPES, PilotCuration, ProposeIn, catalog
from src.rav.store.pilot_db import DEFAULT_SAMPLE_SPEC, CreateSessionInput, DecisionChange, DecisionInput, PilotDB
from src.rav.store.pilot_features import input_hash
from src.rav.store.pilot_runtime import PilotRuntime, WorkerContext
from src.rav.worker.curation_handlers import make_curation_handlers
from src.rav.worker.dispatcher import PilotDispatcher
from src.rav.worker.gpu_handlers import build_context, feature_npz
from tests.conftest import make_video
from tests.test_pilot_media import actor, seed_source

SPEC = {k: DEFAULT_SAMPLE_SPEC[k] for k in ("decoder", "units")}


def unit_rows(rng, n, dim):
    v = rng.normal(size=(n, dim)).astype(np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def seed_features(pg, s3, sid, core, path, owner, features=("DINO",), seed=0):
    """Artifact FEATURE READY như sau PREPARE/import (NPZ đúng ordered IDs của decoder); trả bảng để dựng đường local."""
    src = pg.sql("SELECT * FROM rav_pilot_source_asset WHERE id = %s", (sid,))[0]
    ids = [u.id for u in build_context(path, {"core_source_id": core, "spec": SPEC, "n_units": src["metadata"]["n_units"]}).units]
    rng, out = np.random.default_rng(seed), []
    for feature in features:
        if feature == "DINO":
            tables = (FieldTable(DinoV2S().key, ids, unit_rows(rng, len(ids), 384), "vector"),
                      FieldTable(PHash().key, ids, rng.integers(0, 2**63, len(ids)).astype(np.uint64), "scalar"))
        else:
            tables = (FieldTable(ClipB32().key, ids, unit_rows(rng, len(ids), 512), "vector"),)
        data, meta = feature_npz(feature, tables)
        sha, art = hashlib.sha256(data).hexdigest(), uuid4()
        key = s3.artifact_key(owner, art, sha) if owner else f"pilot-test/system/t/features/{core}/{feature}/{sha}.npz"
        tmp = path.with_suffix(f".{feature}.npz")
        tmp.write_bytes(data)
        s3.put_file(key, tmp, sha, "application/octet-stream")
        pg.sql("INSERT INTO rav_pilot_artifact (id, owner_id, scope, source_id, kind, variant, input_hash, object_key, sha256, "
               "size_bytes, state, manifest) VALUES (%s, %s, %s, %s, 'FEATURE', %s, %s, %s, %s, %s, 'READY', %s)",
               (art, owner, "PRIVATE" if owner else "SYSTEM", sid, feature, input_hash(src, feature), key, sha, len(data),
                json.dumps({**meta, "feature": feature})))
        out.extend(tables)
    return out


@pytest.fixture
def cu(pg, s3_storage, tmp_path, monkeypatch):
    # CLIP text encoder giả, tất định (worker và đường local cùng dùng) — không tải model.
    def encode(texts, model, revision):
        rows = [np.random.default_rng(int(hashlib.sha256(t.encode()).hexdigest()[:8], 16)).normal(size=512) for t in texts]
        v = np.asarray(rows, np.float32)
        return v / np.linalg.norm(v, axis=1, keepdims=True)
    monkeypatch.setattr("src.rav.components.objectives.text_match.encode_texts", encode)
    policy = pg.policy()
    a, b = pg.profile(policy_id=policy), pg.profile(policy_id=policy)
    pg.slot("cpu-1", "CPU")
    wdb = PilotDB(pg.worker_url)
    runtime = PilotRuntime(wdb)
    d = PilotDispatcher(runtime, WorkerContext(worker_id=uuid4(), host_boot_id=uuid4()), "CPU", heartbeat_s=0.2)
    for kind, fn in make_curation_handlers(s3_storage, wdb, tmp_path / "work").items():
        d.register_handler(kind, fn)
    path = make_video(tmp_path / "v.mp4", seconds=4, fps=10, size=(64, 48))
    sid, core, _ = seed_source(pg, s3_storage, path, a)
    tables = seed_features(pg, s3_storage, sid, core, path, a, ("DINO", "CLIP"))
    api = PilotDB(pg.api_url)
    session = api.create_session(actor(a), CreateSessionInput(name="s", goal="g", target_k=50, pool_source_ids=[sid]), "c1")
    return SimpleNamespace(pg=pg, s3=s3_storage, a=a, b=b, sid=sid, core=core, path=path, tables=tables, api=api, d=d,
                           cur=PilotCuration(api), session=session, tmp=tmp_path, policy=policy)


def _ask(cu, recipe="diversity", key="p1", batch=4, seed=7, **kw):
    sv = cu.api.get_review_state(actor(cu.a), cu.session.id).state_version
    return cu.cur.request_proposal(actor(cu.a), cu.session.id,
                                   ProposeIn(recipe=recipe, batch_size=batch, seed=seed, expected_state_version=sv, **kw), key)


def _local(cu, recipe, batch, seed, kept=(), dropped=(), history=()):
    """Đường core như `service/proposal.create_proposal`: Context trên pool + FieldStore + state + proposal cũ."""
    discover()
    from src.rav.worker.handlers import _probe
    store = FieldStore(cu.tmp / f"local-{uuid4().hex[:6]}")
    for t in cu.tables:
        store.put(t, (cu.core, DEFAULT_SAMPLE_SPEC["spec_hash"]))
    ctx = p1.make_context([_probe(cu.path)], {"source": None, **SPEC}, store)
    ctx.state = ReviewState(set(kept), set(dropped))
    ctx.proposals = to_proposals("x", list(history), ctx.units)
    return propose(REGISTRY.recipe(recipe), batch, seed, ctx)


def _decide(cu, changes):
    sv = cu.api.get_review_state(actor(cu.a), cu.session.id).state_version
    return cu.api.append_decisions(actor(cu.a), cu.session.id, DecisionInput(
        changes=[DecisionChange(unit_id=u, action=act) for u, act in changes], expected_state_version=sv))


@pytest.mark.parametrize("recipe", ["perception_instability", "context_uncertainty"])
def test_perception_job_without_training_publishes_evidence_and_reuses_cache(cu, monkeypatch, recipe):
    from src.rav.pipeline import batch_yolo
    checkpoint = cu.tmp / 'checkpoint.pt'
    checkpoint.write_bytes(b'isolated-test-checkpoint')
    sha = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    mid = uuid4()
    key = cu.s3.system_model_key('yolo', sha, '.pt')
    cu.s3.put_file(key, checkpoint, sha, 'application/octet-stream')
    cu.pg.sql("INSERT INTO rav_pilot_model_asset (id,core_model_id,name,raw_key,sha256,size_bytes,checkpoint_revision,"
              "task,class_map,runtime_manifest,validation) VALUES (%s,%s,'test detect',%s,%s,%s,'test',"
              "'detect','{}','{}','INFERENCE_VERIFIED')", (mid, 'ym-'+sha, key, sha, checkpoint.stat().st_size))
    calls = []
    monkeypatch.setattr(batch_yolo, 'load_model', lambda *args: object())
    def detect(*args):
        calls.append(1)
        return [{'box': [0.1, 0.1, 0.4, 0.6], 'label': 'car', 'confidence': .9}]
    monkeypatch.setattr(batch_yolo, 'detect', detect)
    if recipe == 'context_uncertainty':
        # Model inference is mocked, but run the actual native API → queue → strategy → publication path.
        clip = next(t for t in cu.tables if t.key.name == 'emb.clip_b32')
        from src.rav.components.objectives import text_match
        monkeypatch.setattr(text_match, 'encode_texts', lambda texts, *args: np.tile(clip.values[0], (len(texts), 1)))
    first = _ask(cu, recipe=recipe, model_id=mid)
    assert cu.d.run_once()
    got = cu.cur.get_proposal(actor(cu.a), cu.session.id, 1)
    assert got.picks and got.evidence
    assert any(e.get('matching') for rows in got.evidence.values() for e in rows)
    assert cu.cur.job_result(actor(cu.a), first.id)['proposal_n'] == 1
    inferred = len(calls)
    assert inferred > 0
    _ask(cu, recipe=recipe, model_id=mid, key='cached-second')
    assert cu.d.run_once()
    assert cu.cur.get_proposal(actor(cu.a), cu.session.id, 2).picks
    assert len(calls) == inferred


def test_recipe_catalog_matches_registry_and_pilot_list():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("cat", Path(__file__).resolve().parents[1] / "scripts/pilot_recipe_catalog.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert json.loads(json.dumps(mod.build())) == catalog()   # chạy lại scripts/pilot_recipe_catalog.py nếu recipe đổi
    assert [r["name"] for r in catalog()] == [*PILOT_RECIPES, *TRAINING_RECIPES]
    usable = [r for r in catalog() if r["available"]]
    assert [r["name"] for r in usable] == list(PILOT_RECIPES)
    assert not any(r["requires_training"] for r in usable)
    assert {r["name"] for r in usable if r["requires_model"]} == {"perception_instability", "context_uncertainty"}
    assert all(r["unavailable_reason"] == "missing_model_training_provenance" for r in catalog() if not r["available"])


@pytest.mark.parametrize("recipe", ["diversity", "uniform", "random", "text_diversity"])
def test_proposal_parity_with_local_core(cu, recipe):
    job = _ask(cu, recipe, seed=11)
    assert job.kind == "PROPOSE" and job.state == "QUEUED"
    assert cu.d.run_once()
    st = cu.pg.sql("SELECT state, error FROM rav_pilot_job")[0]
    assert st["state"] == "SUCCEEDED", st["error"]
    got = cu.cur.get_proposal(actor(cu.a), cu.session.id, 1)
    want = _local(cu, recipe, 4, 11)
    assert [p["unit_id"] for p in got.picks] == [p.unit.id for p in want.picks]
    assert [p["rank"] for p in got.picks] == [p.rank for p in want.picks]
    assert np.allclose([p["combined"] for p in got.picks], [p.combined for p in want.picks], atol=1e-6)
    assert all(p["source_id"] == str(cu.sid) for p in got.picks)
    assert got.input["recipe"] == recipe and got.input["config_hash"] == want.config_hash and got.input["decision_seq"] == 0
    assert got.explain and got.explain["n"] == 1 and got.explain["recipe"] == recipe
    # Proposal không đổi state_version; kết quả job trỏ đúng đợt.
    assert cu.api.get_review_state(actor(cu.a), cu.session.id).state_version == 1
    assert cu.cur.job_result(actor(cu.a), job.id) == {"kind": "PROPOSE", "session_id": str(cu.session.id), "proposal_n": 1}


def test_second_round_replays_decisions_and_history(cu):
    _ask(cu, key="r1")
    assert cu.d.run_once()
    first = cu.cur.get_proposal(actor(cu.a), cu.session.id, 1)
    ids = [p["unit_id"] for p in first.picks]
    _decide(cu, [(ids[0], "keep"), (ids[1], "drop"), (ids[2], "keep"), (ids[2], "clear")])
    _ask(cu, key="r2", seed=3, return_scores=True)
    assert cu.d.run_once()
    second = cu.cur.get_proposal(actor(cu.a), cu.session.id, 2)
    history = [{"n": 1, "round": 1, "input": first.input, "picks": first.picks, "relaxed": first.relaxed}]
    want = _local(cu, "diversity", 4, 3, kept=[ids[0]], dropped=[ids[1]], history=history)
    assert [p["unit_id"] for p in second.picks] == [p.unit.id for p in want.picks]
    assert sorted(second.input["kept_ids"]) == [ids[0]] and second.input["dropped_ids"] == [ids[1]]
    assert second.input["decision_seq"] == 4 and second.scores and len(second.scores["unit_id"]) == len(second.scores["combined"])
    assert second.scores['unit_id'] == [p['unit_id'] for p in second.picks]
    assert ids[0] not in second.scores["unit_id"] and second.explain["n_kept_at_proposal"] == 1
    listed = cu.cur.list_proposals(actor(cu.a), cu.session.id)
    assert [p.n for p in listed] == [1, 2] and listed[1].scores is None   # scores chỉ ở GET một đợt


def test_context_changed_fails_without_writing_and_charges_cpu(cu):
    job = _ask(cu)
    _decide(cu, [(f"{cu.core}:0.000-0.000", "keep")])   # keep/drop sau khi đóng băng
    assert cu.d.run_once()
    row = cu.pg.sql("SELECT state, error FROM rav_pilot_job")[0]
    assert row["state"] == "FAILED" and row["error"]["code"] == "proposal_context_changed"
    assert cu.pg.sql("SELECT count(*) AS n FROM rav_pilot_proposal")[0]["n"] == 0
    assert cu.pg.sql("SELECT count(*) AS n FROM rav_pilot_usage_receipt WHERE resource = 'CPU'")[0]["n"] == 1
    # Thử lại: đóng băng lại theo state hiện tại, cùng recipe/seed → thành công, ghi retry_of.
    new = cu.cur.retry(actor(cu.a), job.id, "rt1")
    assert cu.cur.retry(actor(cu.a), job.id, "rt1").id == new.id
    assert cu.d.run_once()
    assert cu.pg.sql("SELECT state, retry_of FROM rav_pilot_job WHERE id = %s", (new.id,))[0] == {"state": "SUCCEEDED",
                                                                                                 "retry_of": job.id}
    assert cu.cur.get_proposal(actor(cu.a), cu.session.id, 1).input["decision_seq"] == 1


def test_api_guards(cu):
    job = _ask(cu, key="g1")
    assert _ask(cu, key="g1").id == job.id                       # cùng key → cùng job
    with pytest.raises(ServiceError) as e:
        _ask(cu, key="g2")
    assert e.value.code == "proposal_in_progress"
    with pytest.raises(ServiceError) as e:
        cu.cur.request_proposal(actor(cu.a), cu.session.id, ProposeIn(recipe="diversity", expected_state_version=9), "g3")
    assert e.value.code == "state_version_conflict"
    with pytest.raises(ServiceError) as e:
        _ask(cu, recipe="model_guided", key="g4")
    assert e.value.code == "missing_model_training_provenance" and e.value.status == 422   # Hướng tới (#97)
    with pytest.raises(ServiceError) as e:
        _ask(cu, recipe="khong-co", key="g4b")
    assert e.value.code == "recipe_not_available"
    bad = dict(next(r for r in catalog() if r["name"] == "diversity")["config"])
    bad["selector"] = {"type": "random", "params": {}}
    with pytest.raises(ServiceError) as e:
        _ask(cu, key="g5", config=bad)
    assert e.value.code == "invalid_config"
    with pytest.raises(ServiceError) as e:                      # B không thấy phiên của A
        cu.cur.request_proposal(actor(cu.b), cu.session.id, ProposeIn(recipe="random", expected_state_version=1), "x")
    assert e.value.status == 404
    with pytest.raises(ServiceError):
        cu.cur.list_proposals(actor(cu.b), cu.session.id)
    with pytest.raises(ServiceError) as e:
        cu.cur.job_result(actor(cu.a), job.id)
    assert e.value.code == "job_not_succeeded"


def test_feature_not_ready_and_random_without_features(cu):
    path = make_video(cu.tmp / "w.mp4", seconds=2, fps=10, shift=60)
    sid2, core2, _ = seed_source(cu.pg, cu.s3, path, cu.a)
    s2 = cu.api.create_session(actor(cu.a), CreateSessionInput(name="s2", goal="g", target_k=5, pool_source_ids=[sid2]), "c2")
    with pytest.raises(ServiceError) as e:
        cu.cur.request_proposal(actor(cu.a), s2.id, ProposeIn(recipe="diversity", expected_state_version=1), "f1")
    assert e.value.code == "feature_not_ready" and e.value.details["missing"] == [{"source_id": str(sid2), "feature": "DINO"}]
    cu.cur.request_proposal(actor(cu.a), s2.id, ProposeIn(recipe="uniform", batch_size=3, expected_state_version=1), "f2")
    assert cu.d.run_once()
    got = cu.cur.get_proposal(actor(cu.a), s2.id, 1)
    # Chưa giữ frame nào → giải thích không cần DINO; không bịa so sánh với frame đã giữ.
    assert len(got.picks) == 3 and got.explain["nearest_kept"] == [] and not got.explain["field_used_by_recipe"]


def test_invalid_params_fail_on_worker(cu):
    cfg = json.loads(json.dumps(next(r for r in catalog() if r["name"] == "diversity")["config"]))
    cfg["selector"]["params"]["nms_s"] = "abc"                   # đúng loại component, sai tham số → worker kiểm
    _ask(cu, key="w1", config=cfg)
    assert cu.d.run_once()
    row = cu.pg.sql("SELECT state, error FROM rav_pilot_job")[0]
    assert row["state"] == "FAILED" and row["error"]["code"] == "invalid_config"


def test_a06_three_accounts_round_robin_and_decisions_stay_fast(cu, monkeypatch):
    """A06: 3 tài khoản cùng xếp job nặng; claim xoay vòng; keep/drop qua API vẫn nhanh khi worker đang chạy."""
    c = cu.pg.profile(policy_id=cu.policy)
    owners, sessions = [cu.a, cu.b, c], {}
    for i, o in enumerate(owners):
        sid, core, _ = (cu.sid, cu.core, None) if o == cu.a else seed_source(cu.pg, cu.s3, cu.path, o)
        if o != cu.a:
            seed_features(cu.pg, cu.s3, sid, core, cu.path, o, ("DINO",))
        for k in range(2):
            s = cu.api.create_session(actor(o), CreateSessionInput(name=f"{i}{k}", goal="g", target_k=9, pool_source_ids=[sid]),
                                      f"a06-{i}-{k}")
            sessions[(o, k)] = s
            cu.cur.request_proposal(actor(o), s.id, ProposeIn(recipe="diversity", batch_size=2, expected_state_version=1),
                                    f"a06p-{i}-{k}")
    slow_started = threading.Event()
    real_propose = __import__("src.rav.pipeline.propose", fromlist=["propose"]).propose

    def slow(*args, **kwargs):
        slow_started.set()
        time.sleep(0.6)
        return real_propose(*args, **kwargs)
    monkeypatch.setattr("src.rav.pipeline.propose.propose", slow)
    runner = threading.Thread(target=lambda: [cu.d.run_once() for _ in range(6)])
    runner.start()
    assert slow_started.wait(30)
    # Trong lúc worker đang chạy job nặng: quyết định trên phiên khác phản hồi nhanh.
    t0 = time.monotonic()
    other = sessions[(cu.a, 1)]
    cu.api.append_decisions(actor(cu.a), other.id, DecisionInput(
        changes=[DecisionChange(unit_id=f"{cu.core}:0.000-0.000", action="keep")], expected_state_version=1))
    assert time.monotonic() - t0 < 1.0
    runner.join(timeout=120)
    rows = cu.pg.sql("SELECT j.owner_id, a.created_at FROM rav_pilot_job_attempt a JOIN rav_pilot_job j ON j.id = a.job_id "
                     "ORDER BY a.created_at")
    order = [r["owner_id"] for r in rows]
    assert len(order) == 6 and set(order[:3]) == set(owners) and set(order[3:]) == set(owners)
