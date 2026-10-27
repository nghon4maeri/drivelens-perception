"""V2 boundaries: reference exclusion, binding/subset cache, bounded engine, durable jobs and replay.

Uses real video/image decoding; only the YOLO engine is mocked. Run pytest tests/test_algorithm_v2.py.
"""
import json
import queue
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from src.rav.api import create_app
from src.rav.components.features.yolo_consistency import instability
from src.rav.core.component import Feature
from src.rav.core.registry import REGISTRY, Registry
from src.rav.core.types import ComponentConfig, FieldTable
from src.rav.pipeline import batch_yolo
from src.rav.pipeline.propose import config_hash
from src.rav.service import ServiceError, dataset, proposal, proposal_jobs, reference_model, review_state
from src.rav.service.session import make_context

from .test_datasets import image
from .test_p5a import app as video_app  # noqa: F401
from .test_p5a import run, video_id
from .test_reference_model import checkpoint


@pytest.fixture
def v2(video_app, tmp_path, monkeypatch):  # noqa: F811
    app = video_app
    app.datasets_dir, app.models_dir = tmp_path / "dataset", tmp_path / "models"
    image(app.datasets_dir / "train", "a.png")
    result = dataset.import_dataset(app, "train", "Train", None, "none", None)
    vid = result["version"]["id"]
    member = dataset.all_members(app, vid)[0]
    ctx = make_context(app, {"id": "", "spec": app.spec | {"pool": {"video_ids": [video_id(app, "a.mp4")]}}})
    vector = ctx.field("emb.dinov2_s", ctx.units[:1]).values
    from src.rav.core.execution import image_spec_hash
    for name, values, kind in (("emb.dinov2_s", vector, "vector"), ("hash.phash", np.array([0], np.uint64), "scalar")):
        app.fields.put(FieldTable(REGISTRY.feature_for(name)().key, [member["sample_id"]], values, kind), (vid, image_spec_hash()))
    path = tmp_path / "input.pt"
    path.write_bytes(checkpoint())
    model = reference_model.upload_model(app, "test.pt", str(path))["model"]
    session = run(app, "create_session", pool={"video_ids": [video_id(app, "a.mp4")]}, goal="cars", target_k=20,
                  reference={"dataset_version_ids": [vid], "model_id": model["id"]})
    calls = []
    def detect(engine, frame, model_id):
        assert frame.mode == "RGB" and frame.size == (64, 48)
        calls.append(np.asarray(frame))
        return [{"label": "car", "score": .7, "box": [.2, .2, .8, .8], "source": "yolo:" + model_id}]
    monkeypatch.setattr(batch_yolo, "load_model", lambda path, output: object())
    monkeypatch.setattr(batch_yolo, "detect", detect)
    app.prepare_queue = queue.Queue()
    config = REGISTRY.recipe("model_guided").model_copy(deep=True)
    config.candidate_stage.limit = 5
    for sel in (config.selector, config.candidate_stage.selector):
        sel.params = {"nms_s": 0, "min_dist": 0}
    return SimpleNamespace(app=app, session=session, config=config, calls=calls, version=vid, model=model, member=member)


def enqueue(v2):
    return proposal_jobs.queue(v2.app, v2.session.id, "model_guided", v2.config, 4, 123)


def test_reference_is_not_pool_and_nearest_evidence(v2):
    cfg = proposal.bind_recipe(v2.app, v2.session.id, v2.config)
    ctx = proposal.session_context(v2.app, v2.session.id, cfg)
    assert len(ctx.units) == 12 and len(ctx.reference_sets["training"]) == 1
    assert not set(u.id for u in ctx.units) & set(u.id for u in ctx.reference_sets["training"])
    p = run(v2.app, "propose", session_id=v2.session.id, recipe="reference_diversity", batch_size=3, seed=0)
    assert p.evidence and p.input.execution["reference_version_ids"] == [v2.version]
    assert all(p.evidence[i.unit_id][0]["source_id"] == v2.version for i in p.picks)
    assert v2.calls == []


def test_job_exact_subset_cache_and_scores_replay_after_review(v2):
    app, sid = v2.app, v2.session.id
    before = app.db.decisions(sid)
    first = enqueue(v2)
    assert enqueue(v2)["id"] == first["id"] and app.prepare_queue.qsize() == 1
    app.prepare_queue.get()()
    job = proposal_jobs.list_jobs(app, sid)[0]
    assert job["state"] == "done", job
    assert len(v2.calls) == 10  # 5 anchors × original/flip; pool has 12 anchors
    rec = app.db.proposals(sid)[0]
    ids = rec["input"]["execution"]["candidate_ids"]
    assert len(ids) == 5 and {p["unit_id"] for p in rec["picks"]} <= set(ids)
    assert rec["input"]["config"]["features"]["det.yolo_consistency"]["params"]["model_id"] == v2.model["id"]
    scores = proposal.scores(app, sid, rec)
    read = TestClient(create_app(app)).get(f"/api/v1/sessions/{sid}/proposals/1?return_scores=true")
    from src.rav.proposal_response import batch_scores
    assert read.status_code == 200 and read.json()["scores"] == batch_scores(scores, rec['picks'])
    assert read.json()["evidence"] == rec["evidence"] and "_evidence" not in read.json()["input"]
    explain = TestClient(create_app(app)).get(f"/api/v1/sessions/{sid}/proposals/1/explain").json()
    components = [(c["role"], c["type"]) for c in explain["components"]]
    assert len(components) == len(set(components))
    assert scores["unit_id"] == ids and len(v2.calls) == 10 and app.db.decisions(sid) == before
    for pick in rec["picks"]:
        index = ids.index(pick["unit_id"])
        assert scores["combined"][index] == pytest.approx(pick["combined"])
        assert {e["kind"] for e in rec["evidence"][pick["unit_id"]]} == {"nearest", "scalar", "detections"}
    review_state.update_state(app, sid, [{"unit_id": rec["picks"][0]["unit_id"], "action": "keep"}])
    assert proposal.scores(app, sid, rec) == scores and len(v2.calls) == 10
    with pytest.raises(ServiceError, match="job nền"):
        run(app, "propose", session_id=sid, recipe="model_guided", config=v2.config, batch_size=4)
    next((app.output_dir / "fields" / "requested").rglob("*.json")).unlink()
    read = TestClient(create_app(app)).get(f"/api/v1/sessions/{sid}/proposals/1?return_scores=true")
    assert read.status_code == 409 and read.json()["error"]["code"] == "proposal_cache_invalid"
    assert len(v2.calls) == 10


def test_context_change_during_job_rejects_publication_and_retry(v2, monkeypatch):
    original = batch_yolo.detect
    changed = False
    def detect(*args):
        nonlocal changed
        if not changed:
            changed = True
            ctx = make_context(v2.app, v2.app.db.get_session(v2.session.id))
            review_state.update_state(v2.app, v2.session.id, [{"unit_id": ctx.units[0].id, "action": "drop"}])
        return original(*args)
    monkeypatch.setattr(batch_yolo, "detect", detect)
    enqueue(v2)
    v2.app.prepare_queue.get()()
    job = proposal_jobs.list_jobs(v2.app, v2.session.id)[0]
    assert job["state"] == "error" and "quyết định" in job["error"]
    assert v2.app.db.proposals(v2.session.id) == []
    retry = enqueue(v2)
    v2.app.prepare_queue.get()()
    assert proposal_jobs.list_jobs(v2.app, v2.session.id)[-1]["id"] == retry["id"]
    assert proposal_jobs.list_jobs(v2.app, v2.session.id)[-1]["state"] == "done"


def test_failure_restart_and_http_job_read_never_runs_engine(v2, monkeypatch):
    client = TestClient(create_app(v2.app))
    url = f"/api/v1/sessions/{v2.session.id}/proposal-jobs"
    assert client.get(url).json() == []
    job = client.post(url, json={"recipe": "model_guided", "config": v2.config.model_dump(), "batch_size": 4}).json()
    assert job["state"] == "queued" and not v2.calls
    del v2.app.proposal_jobs
    assert client.get(url).json()[0]["state"] == "error" and not v2.calls
    # Simulate a fresh worker queue, then a third-party model failure with sensitive-looking text.
    v2.app.prepare_queue = queue.Queue()
    monkeypatch.setattr(batch_yolo, "load_model", lambda *_: (_ for _ in ()).throw(ValueError("secret path/token")))
    enqueue(v2)
    v2.app.prepare_queue.get()()
    failed = client.get(url).json()[-1]
    assert failed["state"] == "error" and "secret" not in failed["error"]
    assert not v2.app.db.proposals(v2.session.id)


def test_requested_cache_binding_isolation_and_raw_integrity(v2):
    ctx = make_context(v2.app, v2.app.db.get_session(v2.session.id))
    registry = Registry()
    calls = []
    @registry.register("feature")
    class Counted(Feature):
        """Testing runtime binding/subset, without a model dependency."""
        name, version, provides = "counted", "1", "counted.value"
        per_sample, cache_scope = True, "requested"
        class Params(Feature.Params):
            multiplier: int = 1
        def compute(self, units, ctx):
            calls.append([u.id for u in units])
            return FieldTable(self.key, [u.id for u in units], np.full(len(units), self.params.multiplier), "scalar")
    ctx._registry = registry
    units = ctx.units[:3]
    ctx.feature_configs = {"counted.value": ComponentConfig(type="counted", params={"multiplier": 2})}
    assert list(ctx.field("counted.value", units).values) == [2, 2, 2]
    ctx.field("counted.value", units)
    assert calls == [[u.id for u in units]]
    ctx.field("counted.value", units[:1])
    assert len(calls) == 2 and calls[-1] == [units[0].id]
    ctx.feature_configs["counted.value"].params["multiplier"] = 3
    ctx._features.clear()
    assert list(ctx.field("counted.value", units).values) == [3, 3, 3] and len(calls) == 3
    path = next(iter(ctx.videos.values())).uri
    from pathlib import Path
    Path(path).write_bytes(b"changed")
    with pytest.raises(ValueError, match="Raw source"):
        ctx.field("counted.value", units)
    assert len(calls) == 3


def test_checkpoint_and_dataset_change_never_publish_partial(v2, monkeypatch):
    original = batch_yolo.detect
    path = v2.app.models_dir / (v2.model["id"] + ".pt")
    def detect(*args):
        path.write_bytes(b"changed checkpoint")
        return original(*args)
    monkeypatch.setattr(batch_yolo, "detect", detect)
    enqueue(v2)
    v2.app.prepare_queue.get()()
    assert proposal_jobs.list_jobs(v2.app, v2.session.id)[0]["state"] == "error"
    assert not list((v2.app.output_dir / "fields" / "requested").rglob("*.json"))
    assert not v2.app.db.proposals(v2.session.id)


def test_missing_clip_cache_never_runs_image_encoder(v2, monkeypatch):
    from src.rav.components.features import clip_b32
    from src.rav.components.objectives import text_match
    calls = []
    monkeypatch.setattr(clip_b32, "encode_images", lambda *a: calls.append(a))
    monkeypatch.setattr(text_match, "encode_texts", lambda *a: np.ones((1, 512), np.float32) / np.sqrt(512))
    config = REGISTRY.recipe("text_model_guided").model_copy(deep=True)
    proposal_jobs.queue(v2.app, v2.session.id, config.name, config, 4, 0)
    v2.app.prepare_queue.get()()
    job = proposal_jobs.list_jobs(v2.app, v2.session.id)[0]
    assert job["state"] == "error" and "emb.clip_b32" in job["error"]
    assert not calls and not v2.calls and not v2.app.db.proposals(v2.session.id)


def test_weighted_and_seeded_selection_contract(v2):
    from src.rav.components.combiners.weighted_mean import WeightedMean
    from src.rav.components.selectors.explore_diversity import ExploreDiversity
    from src.rav.core.types import Scores
    ids = ["a", "b"]
    scores = [(Scores("novelty", ids, np.array([0., 1.])), .6), (Scores("risk", ids, np.array([1., 0.])), .4)]
    combined, mask = WeightedMean().combine(scores, ids)
    assert list(combined) == pytest.approx([.4, .6]) and mask.all()
    with pytest.raises(ValueError):
        WeightedMean().combine([(Scores("bad", ids, np.array([np.nan, 0.])), 1.)], ids)
    ctx = make_context(v2.app, v2.app.db.get_session(v2.session.id))
    sel = ExploreDiversity({"min_dist": 0, "nms_s": 0})
    def pick():
        ctx.rng = np.random.default_rng(9)
        return sel.pick(ctx.units, np.arange(12) / 12, np.ones(12, bool), 8, ctx)
    a, b = pick(), pick()
    assert a.indices == b.indices and len(set(a.indices)) == 8
    assert sum("khám phá" in e[0]["method"] for e in a.evidence.values()) == 2
    strict = ExploreDiversity({"min_dist": 2, "nms_s": 100})
    assert len(strict.pick(ctx.units, np.ones(12), np.ones(12, bool), 8, ctx).indices) == 1


def test_consistency_cases_and_legacy_hash():
    d = {"box": [.1, .1, .5, .5], "label": "car"}
    assert instability([], []) == (0., False)
    assert instability([d], [d]) == (0., True)
    assert instability([d], []) == (1., True)
    assert instability([d], [dict(d, label="person")]) == (1., True)
    legacy = REGISTRY.recipe("diversity")
    # Optional new defaults must not perturb the existing hash recipe representation.
    import hashlib

    from src.rav.pipeline.propose import discover
    discover()
    def resolved(kind, c):
        cls = REGISTRY.get(kind, c.type)
        return [c.type, cls.version, cls.Params.model_validate(c.params).model_dump(mode="json")]
    doc = {"reference": resolved("reference", legacy.reference),
           "objectives": [[*resolved("objective", o), o.strength] for o in legacy.objectives],
           "combiner": resolved("combiner", legacy.combiner), "selector": resolved("selector", legacy.selector)}
    assert config_hash(legacy) == hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()[:16]


def test_missing_reference_and_feature_mismatch(v2):
    from .test_p5a import new_session
    session = new_session(v2.app)
    for recipe in ("reference_diversity", "model_guided"):
        with pytest.raises(ServiceError, match="tham chiếu"):
            proposal_jobs.queue(v2.app, session.id, recipe, None, 4, 0)
    cfg = v2.config.model_copy(deep=True)
    cfg.features["emb.dinov2_s"] = ComponentConfig(type="yolo_consistency")
    with pytest.raises(ValueError, match="provides"):
        config_hash(cfg)
