"""Strategy orchestration independent of recipe configuration; shared Context owns resources.

Inputs are validated public parameters and an already provisioned Context. Outputs share
Proposal, evidence, score columns and a bounded execution trace; no storage/HTTP access.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .types import Pick, Proposal


class Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TextParameters(Parameters):
    texts: list[str] = Field(default_factory=lambda: ["a dashcam photo of cars on a street"], min_length=1, max_length=32)
    negative: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("texts", "negative")
    @classmethod
    def nonblank(cls, values):
        if any(not s.strip() or len(s) > 1000 for s in values):
            raise ValueError("text must be nonblank and at most 1000 characters")
        return [s.strip() for s in values]


class ContextParameters(TextParameters):
    candidate_multiplier: int = Field(2, ge=2, le=3, strict=True)


@dataclass
class StrategyResult:
    proposal: Proposal
    scores: dict = field(default_factory=dict)
    trace: list[dict] = field(default_factory=list)


class Strategy:
    """A complete sampling algorithm; subclasses may define any parameter model and steps."""
    name: str
    version = "1"
    title: str
    steps: tuple[str, ...] = ()
    Params = Parameters
    needs: tuple[str, ...] = ()
    background = False
    model = False
    model_optional = False
    reference_dataset = False

    def metadata(self):
        defaults = {name: value.get_default(call_default_factory=True) for name, value in self.Params.model_fields.items()
                    if not value.is_required()}
        inputs = {"model": self.model, "reference_dataset": self.reference_dataset}
        if self.model_optional:
            inputs["model_optional"] = True
        return {"name": self.name, "version": self.version, "title": self.title,
                "steps": list(self.steps), "inputs": inputs,
                "parameters_schema": self.Params.model_json_schema(), "defaults": self.Params.model_construct(**defaults).model_dump(mode="json")}

    def run(self, ctx, params, batch_size, seed, *, config=None):
        raise NotImplementedError


def identity(strategy, params, config=None, resources=None):
    from ..pipeline.propose import config_hash
    value = [strategy.name, strategy.version, params.model_dump(mode="json"), config_hash(config) if config else None, resources]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


class LegacyStrategy(Strategy):
    """Adapt existing recipes without changing their algorithms or component identities."""
    def __init__(self, recipe, registry):
        self.name = recipe.name
        self.title = recipe.name.replace("_", " ").capitalize()
        all_objectives = [*recipe.objectives, *(recipe.candidate_stage.objectives if recipe.candidate_stage else [])]
        self.Params = TextParameters if any(o.type == "text_match" for o in all_objectives) else Parameters
        self.reference_dataset = bool(getattr(registry.get("reference", recipe.reference.type), "needs_reference_sets", False))
        self.model = any(f.params.get("model_id") for f in recipe.features.values())
        self.background = recipe.candidate_stage is not None or self.reference_dataset
        self.needs = tuple(f for f, used in (("DINO", bool(recipe.objectives)), ("CLIP", self.Params is TextParameters)) if used)
        self.steps = (("Bound candidates",) if recipe.candidate_stage else ()) + ("Score candidates", "Select diverse batch")
        self.recipe = recipe

    def resolve_config(self, config, params):
        recipe = (config or self.recipe).model_copy(deep=True)
        for objective in [*recipe.objectives, *(recipe.candidate_stage.objectives if recipe.candidate_stage else [])]:
            if objective.type == "text_match":
                objective.params.update(params.model_dump(mode="json"))
        return recipe

    def run(self, ctx, params, batch_size, seed, *, config=None):
        from ..pipeline.propose import propose, select_candidates
        recipe = self.resolve_config(config, params)
        candidates = select_candidates(recipe, seed, ctx) if recipe.candidate_stage else None
        if recipe.candidate_stage and getattr(ctx, "_on_progress", None):
            ctx._on_progress("det.yolo_consistency", 0, len(candidates))
        scores = {}
        prop = propose(recipe, batch_size, seed, ctx, candidates, score_output=scores)
        scores.setdefault("unit_id", candidates if candidates is not None else [u.id for u in ctx.units if u.id not in ctx.state.kept | ctx.state.dropped])
        return StrategyResult(prop, scores, [{"step": "legacy_recipe", "candidate_ids": candidates or scores["unit_id"], "count": len(candidates if candidates is not None else scores.get("unit_id", []))}])


class ContextUncertainty(Strategy):
    """Filter CLIP context and DINO diversity before requested-only YOLO consistency."""
    name = "context_uncertainty"
    title = "Context and detection uncertainty"
    Params = ContextParameters
    needs = ("DINO", "CLIP")
    background = model = True
    steps = ("Filter context with CLIP", "Bound diverse candidates with DINO", "Measure YOLO original/flip consistency", "Select relevant uncertain diverse batch")

    def run(self, ctx, params, batch_size, seed, *, config=None):
        from .registry import REGISTRY
        from .types import ComponentConfig
        ctx.rng = np.random.default_rng(seed)
        ctx.set_reference(REGISTRY.build("reference", {"type": "session_kept"}))
        done = ctx.state.kept | ctx.state.dropped
        units = [u for u in ctx.units if u.id not in done]
        relevance = REGISTRY.build("objective", {"type": "text_match", "params": params.model_dump(exclude={"candidate_multiplier"})}).score(units, ctx)
        novelty = REGISTRY.build("objective", {"type": "novelty"}).score(units, ctx)
        combined = relevance.values * novelty.values
        # relax spacing like legacy candidate stages; masks (CLIP gate, observed YOLO) are never relaxed
        selector = REGISTRY.build("selector", {"type": "greedy_nms"})
        chosen = selector.pick(units, combined, relevance.mask, min(96, batch_size * params.candidate_multiplier), ctx)
        candidates = [units[i] for i in chosen.indices]
        trace = [{"step": "context_filter", "count": int(np.count_nonzero(relevance.mask)), "pool_count": len(units)},
                 {"step": "bounded_candidates", "count": len(candidates), "candidate_ids": [u.id for u in candidates]}]
        model_id = getattr(ctx, "strategy_model_id", None)
        if not model_id:
            raise ValueError("verified strategy model resource required")
        ctx.feature_configs = {**ctx.feature_configs, "det.yolo_consistency": ComponentConfig(type="yolo_consistency", params={"model_id": model_id})}
        ctx._features.pop("det.yolo_consistency", None)
        instability = REGISTRY.build("objective", {"type": "detection_instability"}).score(candidates, ctx)
        values = combined[chosen.indices] * instability.values
        selected = selector.pick(candidates, values, instability.mask, batch_size, ctx)
        for uid, rows in selected.evidence.items():
            ctx.evidence.setdefault(uid, []).extend(rows)
        picks = [Pick(candidates[i], float(values[i]), {"text_match": float(relevance.values[chosen.indices[i]]),
                    "novelty": float(novelty.values[chosen.indices[i]]), "detection_instability": float(instability.values[i])}, rank)
                 for rank, i in enumerate(selected.indices, 1)]
        used_fields = sorted(ctx.fields_used(), key=lambda k: (k.name, k.producer, k.version, k.params_hash))
        prop = Proposal(None, len(ctx.proposals)+1, len(ctx.proposals)+1, self.name, identity(self, params, resources={"model_id": model_id, "fields": [asdict(k) for k in used_fields]}), picks,
                        selected.relaxed, sorted(ctx.fields_used(), key=lambda k: (k.name, k.producer, k.version, k.params_hash)))
        trace.extend([{"step": "yolo_consistency", "count": len(candidates), "candidate_ids": [u.id for u in candidates]},
                      {"step": "final_batch", "count": len(picks), "candidate_ids": [p.unit.id for p in picks]}])
        return StrategyResult(prop, {"unit_id": [u.id for u in candidates], "combined": values.tolist(),
                                    "detection_instability": instability.values.tolist(), "text_match": relevance.values[chosen.indices].tolist(),
                                    "novelty": novelty.values[chosen.indices].tolist()}, trace)
