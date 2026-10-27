"""Strategy public boundary, legacy adapter regression, native local job and ordered bounded inference tests."""
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.rav.api import create_app
from src.rav.components.features.clip_b32 import ClipB32
from src.rav.components.objectives import text_match
from src.rav.core.registry import REGISTRY, discover
from src.rav.core.strategy import ContextParameters
from src.rav.core.types import FieldTable, Frame, Scores, Unit
from src.rav.errors import ServiceError
from src.rav.pipeline import prepare as p1
from src.rav.pipeline.propose import config_hash, propose, select_candidates
from src.rav.service import proposal, proposal_jobs
from src.rav.store.pilot_curation import PILOT_RECIPES, catalog, resolve_strategy_params

from .test_algorithm_v2 import v2  # noqa: F401
from .test_p5a import app as video_app  # noqa: F401


@pytest.fixture
def clip_v2(v2, monkeypatch):  # noqa: F811
    """v2 + prepared fake CLIP image field; text encoder is deterministic so no CLIP model loads."""
    rng = np.random.default_rng(1)
    base = rng.normal(size=512).astype(np.float32)
    for video in p1.catalog(v2.app.spec).values():
        ctx = p1.make_context([video], v2.app.spec, v2.app.fields)
        emb = (base + 0.05 * rng.normal(size=(len(ctx.units), 512))).astype(np.float32)   # every frame matches the query
        emb /= np.linalg.norm(emb, axis=1, keepdims=True)
        v2.app.fields.put(FieldTable(ClipB32().key, [u.id for u in ctx.units], emb, "vector"), (video.id, ctx._execution.spec_hash))
    def encode(texts, model, revision):
        return np.tile(base / np.linalg.norm(base), (len(texts), 1))
    monkeypatch.setattr(text_match, "encode_texts", encode)
    return v2


@pytest.mark.parametrize("name", [n for n in PILOT_RECIPES if next(r for r in catalog() if r["name"] == n)["config"] is not None])
def test_legacy_adapter_matches_old_propose(clip_v2, name):
    """Pilot worker now routes every legacy recipe through LegacyStrategy.run; picks must not change."""
    app, sid = clip_v2.app, clip_v2.session.id
    cfg = proposal.bind_recipe(app, sid, REGISTRY.recipe(name), clip_v2.model["id"])
    old_ctx = proposal.session_context(app, sid, cfg)
    ids = select_candidates(cfg, 7, old_ctx) if cfg.candidate_stage else None
    old = propose(cfg, 3, 7, old_ctx, ids)
    strategy = REGISTRY.strategy(name)
    entry = next(r for r in catalog() if r["name"] == name)
    params = strategy.Params.model_validate(resolve_strategy_params(entry, None, cfg.model_dump(mode="json")))
    new = strategy.run(proposal.session_context(app, sid, cfg), params, 3, 7, config=cfg).proposal
    assert new.config_hash == old.config_hash == config_hash(cfg)
    assert [(p.unit.id, p.combined, p.rank) for p in new.picks] == [(p.unit.id, p.combined, p.rank) for p in old.picks]
    assert old.picks


def test_native_strategy_local_job_freezes_params_and_bounds_yolo(clip_v2):
    app, sid = clip_v2.app, clip_v2.session.id
    params = {"texts": ["car"], "negative": [], "candidate_multiplier": 2}
    with pytest.raises(ServiceError):
        proposal_jobs.queue(app, sid, "context_uncertainty", clip_v2.config, 2, 5, strategy_params=params, model_id=clip_v2.model["id"])
    job = proposal_jobs.queue(app, sid, "context_uncertainty", None, 2, 5, strategy_params=params, model_id=clip_v2.model["id"])
    app.prepare_queue.get()()
    assert proposal_jobs.list_jobs(app, sid)[0]["state"] == "done", job
    rec = app.db.proposals(sid)[-1]
    assert rec["input"]["strategy_params"] == params and rec["input"]["model_id"] == clip_v2.model["id"]
    bounded = next(t for t in rec["strategy_trace"] if t["step"] == "bounded_candidates")
    assert bounded["count"] <= 4 and len(clip_v2.calls) == 2 * bounded["count"]   # original + flip, candidates only
    assert {p["unit_id"] for p in rec["picks"]} <= set(bounded["candidate_ids"]) and len(rec["picks"]) == 2
    explain = TestClient(create_app(app)).get(f"/api/v1/sessions/{sid}/proposals/{rec['n']}/explain")
    assert explain.status_code == 200 and explain.json()["recipe"] == "context_uncertainty", explain.text


def test_public_parameters_strict_bounds_and_unknowns():
    entry = next(r for r in catalog() if r["name"] == "context_uncertainty")
    for params in ({"candidate_multiplier": True}, {"candidate_multiplier": 4}, {"texts": [""]}, {"hidden": 2}):
        with pytest.raises(ServiceError):
            resolve_strategy_params(entry, params)
    with pytest.raises(ValidationError):
        ContextParameters(candidate_multiplier=2.5)
    assert resolve_strategy_params(entry, None)["candidate_multiplier"] == 2


def test_odd_local_job_without_model_preserves_frozen_trace(clip_v2, monkeypatch):
    """The optional model must not be required by local enqueue/run/persistence."""
    from src.rav.components.features import clip_b32

    vector = np.ones(512, np.float32) / np.sqrt(512)
    monkeypatch.setattr(clip_b32, "encode_texts", lambda texts: np.tile(vector, (len(texts), 1)))
    app, sid = clip_v2.app, clip_v2.session.id
    params = {"objects": ["truck"], "odd_conditions": ["at night"], "scenarios": [],
              "min_similarity": -1, "min_margin": -2}
    proposal_jobs.queue(app, sid, "odd_coverage", None, 2, 5, strategy_params=params)
    app.prepare_queue.get()()
    job = proposal_jobs.list_jobs(app, sid)[0]
    assert job["state"] == "done", job
    rec = app.db.proposals(sid)[-1]
    assert rec["input"]["model_id"] is None and rec["input"]["strategy_params"]["objects"] == ["truck"]
    assert rec["strategy_trace"][0]["branches"][0]["query"] == "a dashcam photo of truck, at night"
    assert rec["strategy_trace"][-1]["step"] == "odd_selection"
    assert not clip_v2.calls


def test_custom_strategy_schema_supports_required_and_typed_parameters():
    """A developer's native strategy is not limited to the sample strategy's text/int params."""
    from pydantic import Field

    from src.rav.core.strategy import Parameters, Strategy

    class CustomParams(Parameters):
        query: str = Field(min_length=2)
        threshold: float = Field(0.4, ge=0, le=1)
        enabled: bool = True
        weights: list[float] = Field(default_factory=lambda: [0.5], min_length=1)

    class CustomStrategy(Strategy):
        name = title = "custom_schema_review"
        Params = CustomParams

    metadata = CustomStrategy().metadata()
    assert "query" not in metadata["defaults"]
    entry = {"strategy": metadata}
    resolved = resolve_strategy_params(entry, {"query": "rain", "threshold": 0.8, "weights": [0.2, 0.8]})
    assert CustomParams.model_validate(resolved).enabled is True
    for invalid in ({}, {"query": "x"}, {"query": "rain", "threshold": 2},
                    {"query": "rain", "enabled": "yes"}, {"query": "rain", "weights": ["bad"]}):
        with pytest.raises(ServiceError):
            resolve_strategy_params(entry, invalid)


def test_strategy_discovery_is_safe_for_concurrent_first_requests(monkeypatch):
    """Studio startup requests catalog/session/media concurrently before the registry is warm."""
    import time
    from concurrent.futures import ThreadPoolExecutor

    from src.rav.core import registry

    monkeypatch.setattr(registry, "_discovered", False)
    monkeypatch.setattr(REGISTRY, "_strategies", {})
    original = registry.importlib.import_module

    def slow_import(name):
        time.sleep(0.03)
        return original(name)

    monkeypatch.setattr(registry.importlib, "import_module", slow_import)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: discover(), range(8)))
    assert "context_uncertainty" in REGISTRY.strategy_names()


def test_legacy_public_defaults_preserve_old_config_query():
    entry = next(r for r in catalog() if r["name"] == "text_diversity")
    config = {"objectives": [{"type": "text_match", "params": {"texts": ["rainy road"], "negative": ["sunny"]}}]}
    assert resolve_strategy_params(entry, None, config) == {"texts": ["rainy road"], "negative": ["sunny"]}


def test_context_filters_and_bounds_before_requested_yolo(monkeypatch):
    discover()
    units = [Unit(f"v:{i}", "v", float(i), float(i+1), "frame", Frame("v", i, float(i)), ()) for i in range(10)]
    ctx = SimpleNamespace(units=units, state=SimpleNamespace(kept={units[0].id}, dropped={units[1].id}),
                          proposals=[], evidence={}, feature_configs={}, _features={}, strategy_model_id="verified-model",
                          set_reference=lambda ref: None, fields_used=lambda: [])
    calls = []

    class Objective:
        def __init__(self, name):
            self.name = name

        def score(self, selected, ctx):
            ids = [u.id for u in selected]
            calls.append((self.name, ids))
            if self.name == "text_match":
                return Scores(self.name, ids, np.ones(len(ids)), np.array([u.t0 < 7 for u in selected]))
            return Scores(self.name, ids, np.ones(len(ids)), np.ones(len(ids), bool))

    class Selector:
        def pick(self, selected, values, mask, count, ctx):
            return SimpleNamespace(indices=np.flatnonzero(mask)[:count].tolist(), evidence={}, relaxed=False)

    original = REGISTRY.build
    def build(kind, config):
        if kind == "objective":
            return Objective(config["type"])
        if kind == "selector":
            return Selector()
        return original(kind, config)
    monkeypatch.setattr(REGISTRY, "build", build)
    result = REGISTRY.strategy("context_uncertainty").run(ctx, ContextParameters(candidate_multiplier=2), 2, 9)
    assert [name for name, _ in calls] == ["text_match", "novelty", "detection_instability"]
    assert calls[-1][1] == [u.id for u in units[2:6]]
    assert len(result.proposal.picks) == 2
    assert all(p.unit.id not in ctx.state.kept | ctx.state.dropped for p in result.proposal.picks)
    assert result.trace[1]["count"] == 4

    from src.rav.core.types import FieldKey
    fields = [FieldKey("emb.a", "a", "1", "params-a"), FieldKey("det.b", "b", "1", "params-b")]
    ctx.fields_used = lambda: fields
    first = REGISTRY.strategy("context_uncertainty").run(ctx, ContextParameters(), 2, 9).proposal.config_hash
    ctx.fields_used = lambda: list(reversed(fields))
    reordered = REGISTRY.strategy("context_uncertainty").run(ctx, ContextParameters(), 2, 9).proposal.config_hash
    assert first == reordered
    ctx.fields_used = lambda: [fields[0], FieldKey("det.b", "b", "1", "changed-params")]
    changed = REGISTRY.strategy("context_uncertainty").run(ctx, ContextParameters(), 2, 9).proposal.config_hash
    assert changed != first


def test_context_candidates_relax_spacing_on_near_duplicate_frames(monkeypatch):
    """Dashcam frames are near-duplicates in DINO: spacing relaxes (gate kept) instead of starving the batch."""
    discover()
    units = [Unit(f"v:{i}", "v", i * 0.25, i * 0.25, "frame", Frame("v", i, i * 0.25), ()) for i in range(40)]
    same = np.ones((40, 8), np.float32)
    ctx = SimpleNamespace(units=units, state=SimpleNamespace(kept=set(), dropped=set()), proposals=[], evidence={},
                          feature_configs={}, _features={}, strategy_model_id="m", set_reference=lambda ref: None,
                          fields_used=lambda: [], field=lambda name, us: SimpleNamespace(values=same[:len(us)]))
    gate = np.array([i % 4 != 0 for i in range(40)])   # CLIP gate rejects every 4th frame

    class Objective:
        def __init__(self, name):
            self.name = name

        def score(self, selected, ctx):
            ids = [u.id for u in selected]
            mask = gate[[int(i.split(":")[1]) for i in ids]] if self.name == "text_match" else np.ones(len(ids), bool)
            return Scores(self.name, ids, np.linspace(1, 0.5, len(ids)), mask)

    original = REGISTRY.build
    monkeypatch.setattr(REGISTRY, "build", lambda kind, cfg: Objective(cfg["type"]) if kind == "objective" else original(kind, cfg))
    result = REGISTRY.strategy("context_uncertainty").run(ctx, ContextParameters(candidate_multiplier=3), 4, 1)
    bounded = next(t for t in result.trace if t["step"] == "bounded_candidates")
    assert bounded["count"] == 12 and all(int(i.split(":")[1]) % 4 for i in bounded["candidate_ids"])
    assert len(result.proposal.picks) == 4 and result.proposal.relaxed
