"""P2 — `propose(recipe, batch_size, seed, ctx) -> Proposal` (architecture §4.4, CONTRACTS §6, §7).

candidates = pool − kept − dropped → objectives → combiner (`get_score_candidates`) → selector.
Thuần: không đọc DB, state lấy từ `ctx.state`; `seed` là nguồn ngẫu nhiên duy nhất → cùng input + seed → cùng output.
Component lấy qua registry theo tên trong recipe (không import `components/`).
"""

from __future__ import annotations

import hashlib
import json

import numpy as np

from ..core.context import Context
from ..core.registry import REGISTRY, discover
from ..core.types import ComponentConfig, Pick, Proposal, RecipeConfig, Scores, Unit


def get_score_candidates(recipe: RecipeConfig, ctx: Context, candidate_ids=None) -> tuple[list[Unit], list[Scores], np.ndarray, np.ndarray]:
    """candidates, điểm từng objective, combined, mask — chỉ lấy điểm, không chọn."""
    discover()
    ctx.set_reference(REGISTRY.build("reference", recipe.reference))
    if ctx.feature_configs != recipe.features:
        ctx.feature_configs = recipe.features
        ctx._features.clear()
    done = ctx.state.kept | ctx.state.dropped
    candidates = [u for u in ctx.units if u.id not in done]
    if candidate_ids is not None:
        by_id = {u.id: u for u in candidates}
        if len(set(candidate_ids)) != len(candidate_ids) or any(i not in by_id for i in candidate_ids):
            raise ValueError("Candidate snapshot không thuộc pool−kept−dropped")
        candidates = [by_id[i] for i in candidate_ids]
    scores = [REGISTRY.build("objective", o).score(candidates, ctx) for o in recipe.objectives]
    combined, mask = REGISTRY.build("combiner", recipe.combiner).combine(
        [(s, o.strength) for s, o in zip(scores, recipe.objectives)], [u.id for u in candidates])
    return candidates, scores, combined, mask


def propose(recipe: RecipeConfig, batch_size: int, seed: int, ctx: Context, candidate_ids=None, *, score_output=None) -> Proposal:
    ctx.rng = np.random.default_rng(seed)
    candidates, scores, combined, mask = get_score_candidates(recipe, ctx, candidate_ids)
    if score_output is not None:
        score_output.update({'unit_id': [u.id for u in candidates], 'combined': combined.tolist(),
                             **{s.name: s.values.tolist() for s in scores}})
    sel = REGISTRY.build("selector", recipe.selector).pick(candidates, combined, mask, batch_size, ctx)
    for uid, rows in sel.evidence.items():
        ctx.evidence.setdefault(uid, []).extend(rows)
    picks = [Pick(candidates[i], float(combined[i]), {s.name: float(s.values[i]) for s in scores}, rank)
             for rank, i in enumerate(sel.indices, 1)]
    n = len(ctx.proposals) + 1
    return Proposal(None, n, n, recipe.name, config_hash(recipe), picks, sel.relaxed,
                    sorted(ctx.fields_used(), key=lambda k: (k.name, k.producer, k.version, k.params_hash)))


def config_hash(recipe: RecipeConfig) -> str:
    """sha256 (16 hex) của recipe đã resolve: mỗi component [type, version, params điền mặc định], objective kèm strength."""
    discover()

    def resolve(kind: str, c: ComponentConfig) -> list:
        cls = REGISTRY.get(kind, c.type)
        return [c.type, cls.version, cls.Params.model_validate(c.params).model_dump(mode="json")]

    doc = {"reference": resolve("reference", recipe.reference),
           "objectives": [[*resolve("objective", o), o.strength] for o in recipe.objectives],
           "combiner": resolve("combiner", recipe.combiner),
           "selector": resolve("selector", recipe.selector)}
    if recipe.features:
        doc["features"] = {name: resolve("feature", c) for name, c in sorted(recipe.features.items())}
        for name, c in recipe.features.items():
            if REGISTRY.get("feature", c.type).provides != name:
                raise ValueError("Feature binding provides không đúng field")
    if recipe.candidate_stage is not None:
        s = recipe.candidate_stage
        doc["candidate_stage"] = {"limit": s.limit, "selector": resolve("selector", s.selector),
            "combiner": resolve("combiner", s.combiner),
            "objectives": [[*resolve("objective", o), o.strength] for o in s.objectives]}
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()[:16]


def select_candidates(recipe, seed, ctx):
    """Declarative cheap stage; uses existing interfaces, never starts a job or reads storage."""
    stage = recipe.candidate_stage
    cheap = recipe.model_copy(update={"objectives": stage.objectives, "combiner": stage.combiner,
                                      "selector": stage.selector, "candidate_stage": None})
    ctx.rng = np.random.default_rng(seed)
    units, _, combined, mask = get_score_candidates(cheap, ctx)
    selected = REGISTRY.build("selector", stage.selector).pick(units, combined, mask, stage.limit, ctx)
    return [units[i].id for i in selected.indices]
