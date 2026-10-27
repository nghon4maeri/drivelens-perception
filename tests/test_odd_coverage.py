"""ODD union/gate, dynamic selection, bounded inference and public evidence regressions."""
import threading
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from src.rav.components.features import clip_b32
from src.rav.core.odd_coverage import ODDCoverage, ODDParameters, branches, gate, greedy
from src.rav.core.registry import REGISTRY, discover
from src.rav.core.types import Frame, Scores, Unit
from src.rav.errors import ServiceError
from src.rav.proposal_response import proposal_response
from src.rav.store.pilot_curation import catalog, resolve_strategy_params


def make_ctx(n=8):
    units = [Unit(f"v:{i * 2:.3f}-{i * 2:.3f}", "v", i * 2., i * 2., "frame", Frame("v", i, i * 2.), ()) for i in range(n)]
    ctx = SimpleNamespace(units=units, state=SimpleNamespace(kept=set(), dropped=set()), proposals=[], evidence={},
                          feature_configs={}, _features={}, fields_used=lambda: [], cancel_event=threading.Event())
    rng = np.random.default_rng(4)
    vectors = rng.normal(size=(n, 4)).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    clip = np.tile([1., 0., 0.], (n, 1))

    def field(name, selected):
        values = clip if name == "emb.clip_b32" else vectors
        return SimpleNamespace(values=values[[units.index(u) for u in selected]])

    ctx.field = field
    return ctx


def encode(texts, **kwargs):
    return np.array([[0., 1., 0.] if t == "contrast" else [1., 0., 0.] for t in texts])


def test_cross_product_union_and_independent_scenario():
    p = ODDParameters(objects=["truck", "car"], odd_conditions=["rain", "night"], scenarios=["pedestrian crossing"])
    b = branches(p)
    assert len(b) == 5
    assert b[0]["query"] == "a dashcam photo of truck, rain"
    assert b[-1]["query"] == "pedestrian crossing" and b[-1]["kind"] == "scenario"
    assert len(branches(ODDParameters(objects=["truck", "truck"], odd_conditions=[]))) == 1


@pytest.mark.parametrize("invalid", [
    {"odd_conditions": [], "objects": [], "scenarios": []}, {"objects": ["   "]},
    {"objects": ["x" * 201]}, {"objects": ["x"] * 9}, {"temperature": 0}, {"candidate_limit": 2.5},
])
def test_parameter_limits_apply_at_worker_and_lightweight_api(invalid):
    with pytest.raises(ValidationError):
        ODDParameters(**invalid)
    entry = next(r for r in catalog() if r["name"] == "odd_coverage")
    with pytest.raises(ServiceError):
        resolve_strategy_params(entry, invalid)


def test_gate_can_reject_entire_pool_and_does_not_compete_between_branches():
    p = ODDParameters(min_similarity=.2, min_margin=.05)
    images = np.array([[1., 0.], [0., 1.]])
    pos = np.array([[1., 0.], [1., 0.]])
    _, _, mask, scores = gate(images, pos, np.array([[0., 1.]]), p)
    assert mask.tolist() == [[True, True], [False, False]]
    assert scores[0, 0] == scores[0, 1]
    _, _, none, _ = gate(images, pos, pos[:1], p)
    assert not none.any()  # Best image is still rejected when contrast is equally strong.


def test_greedy_updates_distance_and_coverage_after_each_pick():
    ctx = make_ctx(3)
    emb = np.array([[0., 1., 0.], [0., .8, .6], [0., -1., 0.]])
    picks, _ = greedy(ctx.units, emb, np.ones((3, 1), bool), np.array([1., .99, .9]),
                      np.zeros(1), np.array([[1., 0., 0.]]), 2, ctx)
    assert picks == [0, 2]  # Static ranking would take0,1; both pass hard spacing.
    picks, _ = greedy(ctx.units[:2], emb[:2], np.eye(2, dtype=bool), np.array([1., .9]),
                      np.array([20, 0]), np.array([[1., 0., 0.]]), 1, ctx)
    assert picks == [1]  # Already represented branch yields to the missing branch.


def test_strategy_deduplicates_overlap_freezes_counts_and_excludes_reviewed(monkeypatch):
    monkeypatch.setattr(clip_b32, "encode_texts", encode)
    ctx = make_ctx()
    ctx.state.kept.add(ctx.units[0].id)
    ctx.state.dropped.add(ctx.units[1].id)
    p = ODDParameters(objects=["truck"], odd_conditions=["rain", "night"], contrast_texts=["contrast"])
    result = ODDCoverage().run(ctx, p, 3, 1)
    assert len(result.proposal.picks) == 3
    ids = [p.unit.id for p in result.proposal.picks]
    assert len(set(ids)) == 3 and not set(ids) & (ctx.state.kept | ctx.state.dropped)
    g, s = result.trace[0], result.trace[-1]
    assert g["eligible_counts"] == [6, 6] and g["kept_counts"] == [1, 1]
    assert g["count"] == 6 and s["selected_counts"] == [3, 3]
    assert all(len(ctx.evidence[uid]) >= 5 for uid in ids)
    assert not result.proposal.relaxed
    ctx.state.kept.update(ids)
    again = ODDCoverage().run(ctx, p, 2, 1)
    assert again.trace[0]["kept_counts"] == [4, 4]


def test_empty_pool_no_match_and_hard_duplicate_spacing_return_short_batch(monkeypatch):
    monkeypatch.setattr(clip_b32, "encode_texts", encode)
    for n in (0, 8):
        result = ODDCoverage().run(make_ctx(n), ODDParameters(contrast_texts=["contrast"], min_margin=2), 4, 1)
        assert not result.proposal.picks and result.trace[0]["count"] == 0
    ctx = make_ctx(5)
    base = ctx.field
    ctx.field = lambda name, selected: (SimpleNamespace(values=np.tile([1., 0.], (len(selected), 1)))
                                        if name == "emb.dinov2_s" else base(name, selected))
    result = ODDCoverage().run(ctx, ODDParameters(contrast_texts=["contrast"]), 4, 1)
    assert len(result.proposal.picks) == 1 and not result.proposal.relaxed


def test_bounded_round_robin_shortlist_and_requested_only_optional_uncertainty(monkeypatch):
    discover()
    monkeypatch.setattr(clip_b32, "encode_texts", encode)
    ctx = make_ctx(150)
    ctx.strategy_model_id = "frozen-sha-model"
    seen = []
    original = REGISTRY.build

    def build(kind, config):
        if kind == "objective" and config["type"] == "detection_instability":
            def score(units, context):
                seen.extend(u.id for u in units)
                assert context.feature_configs["det.yolo_consistency"].params["model_id"] == "frozen-sha-model"
                return Scores("detection_instability", [u.id for u in units], np.ones(len(units)), np.ones(len(units), bool))
            return SimpleNamespace(score=score)
        return original(kind, config)

    monkeypatch.setattr(REGISTRY, "build", build)
    result = ODDCoverage().run(ctx, ODDParameters(contrast_texts=["contrast"], candidate_limit=72), 4, 9)
    assert result.trace[0]["count"] == 150 and result.trace[0]["shortlist_count"] == 72
    assert 0 < len(seen) <= 12
    assert {p.unit.id for p in result.proposal.picks} <= set(seen)
    record = {"picks": [{"unit_id": p.unit.id} for p in result.proposal.picks], "input": {},
              "scores": result.scores, "evidence": ctx.evidence, "trace": result.trace}
    public = proposal_response(record)
    assert len(public["scores"]["unit_id"]) <= 4


def test_cancellation_stops_before_text_inference(monkeypatch):
    ctx = make_ctx()
    ctx.cancel_event.set()
    monkeypatch.setattr(clip_b32, "encode_texts", lambda texts: pytest.fail("must not run"))
    with pytest.raises(InterruptedError):
        ODDCoverage().run(ctx, ODDParameters(), 4, 1)


def test_shortlist_preserves_a_small_branch_instead_of_global_topk(monkeypatch):
    ctx = make_ctx(100)
    original_field = ctx.field
    clip = np.tile([1., 0., 0.], (100, 1))
    clip[-1] = [0., 1., 0.]
    ctx.field = lambda name, selected: (SimpleNamespace(values=clip[[ctx.units.index(u) for u in selected]])
                                       if name == "emb.clip_b32" else original_field(name, selected))
    monkeypatch.setattr(clip_b32, "encode_texts", lambda texts, **kwargs: np.array(
        [[0., 0., 1.] if t == "contrast" else [0., 1., 0.] if "bus" in t else [1., 0., 0.] for t in texts]))
    result = ODDCoverage().run(ctx, ODDParameters(objects=["car", "bus"], odd_conditions=[],
                                               contrast_texts=["contrast"], candidate_limit=72), 2, 7)
    assert result.trace[0]["eligible_counts"] == [99, 1]
    assert result.trace[0]["shortlist_count"] == 72
    assert ctx.units[-1].id in result.scores["unit_id"]
    # Coverage is a weighted gain, not a mandatory per-branch quota in the final batch.
