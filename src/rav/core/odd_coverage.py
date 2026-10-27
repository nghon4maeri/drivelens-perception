"""ODD branch retrieval and dynamic batch coverage (CONTRACTS 21.37).

Input: prepared Context, validated queries and optional frozen detection model.
Output: Proposal, bounded branch trace and selected-frame evidence. No DB/HTTP I/O.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

import numpy as np
from pydantic import ConfigDict, Field, StringConstraints, model_validator

from .strategy import Parameters, Strategy, StrategyResult, identity
from .types import ComponentConfig, Pick, Proposal

Line = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200, pattern=r"\S")]


class ODDParameters(Parameters):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"anyOf": [
        {"required": [key], "properties": {key: {"minItems": 1}}}
        for key in ("odd_conditions", "objects", "scenarios")
    ]})
    odd_conditions: list[Line] = Field(default_factory=lambda: ["in daytime", "at night"], max_length=8)
    objects: list[Line] = Field(default_factory=lambda: ["cars", "trucks"], max_length=8)
    scenarios: list[Line] = Field(default_factory=list, max_length=8)
    contrast_texts: list[Line] = Field(default_factory=lambda: ["a dashcam photo of a road scene"], min_length=1, max_length=8)
    min_similarity: float = Field(.2, ge=-1, le=1)
    min_margin: float = Field(.01, ge=-2, le=2)
    temperature: float = Field(.05, ge=.005, le=1)
    candidate_limit: int = Field(512, ge=72, le=2048, strict=True)

    @model_validator(mode="after")
    def queries_required(self):
        if not (self.odd_conditions or self.objects or self.scenarios):
            raise ValueError("Nhập ít nhất một điều kiện ODD, đối tượng hoặc scenario.")
        return self


def branches(params: ODDParameters) -> list[dict]:
    """Deterministic cross product plus independent scenarios; duplicate prompts count once."""
    prompts = []
    for obj in params.objects or [""]:
        for odd in params.odd_conditions or [""]:
            if obj or odd:
                prompts.append(("object_odd", f"a dashcam photo of {obj}, {odd}" if obj and odd else
                                f"a dashcam photo of {obj}" if obj else f"a dashcam photo, {odd}"))
    prompts.extend(("scenario", text) for text in params.scenarios)
    unique = dict.fromkeys(text for _, text in prompts)
    return [{"id": f"b{i + 1}", "query": text, "kind": next(k for k, t in prompts if t == text)}
            for i, text in enumerate(unique)]


def _cancel(ctx):
    if getattr(ctx, "cancel_event", None) is not None and ctx.cancel_event.is_set():
        raise InterruptedError("ODD coverage cancelled")


def _progress(ctx, stage, done, total):
    callback = getattr(ctx, "_on_progress", None)
    if callable(callback):
        callback(stage, done, total)


def _normalize(x):
    x = np.asarray(x, np.float32)
    return x / np.maximum(np.linalg.norm(x, axis=-1, keepdims=True), 1e-12)


def gate(images, positive_text, contrast_text, params):
    """Independent branch gates; sigmoid is a ranking transform, not a calibrated probability."""
    positive = images @ positive_text.T
    margin = positive - (images @ contrast_text.T).max(axis=1, keepdims=True)
    mask = (positive >= params.min_similarity) & (margin >= params.min_margin)
    relevance = 1 / (1 + np.exp(-np.clip(margin / params.temperature, -60, 60)))
    return positive, margin, mask, relevance


def greedy(units, embeddings, membership, relevance, kept_counts, kept_embeddings, count, ctx,
           uncertainty=None, valid=None):
    """Recompute distance and branch gain after every pick; never relax hard spacing."""
    n = len(units)
    if not n:
        return [], []
    if len(kept_embeddings):
        distance = np.ones(n, np.float32)
        for start in range(0, len(kept_embeddings), 256):
            _cancel(ctx)
            distance = np.minimum(distance, ((1 - embeddings @ kept_embeddings[start:start + 256].T) / 2).min(axis=1))
    else:
        # Representative first pick; subsequent picks use distance to the growing batch.
        centroid = _normalize(embeddings.mean(axis=0))
        distance = (1 + embeddings @ centroid) / 2
    allowed = np.ones(n, bool) if valid is None else valid.copy()
    counts = kept_counts.copy()
    picked, details = [], []
    for _ in range(min(count, n)):
        _cancel(ctx)
        coverage = (membership / (1 + counts)).sum(axis=1) / np.maximum(membership.sum(axis=1), 1)
        base = .35 * relevance + .40 * np.clip(distance, 0, 1) + .25 * coverage
        gain = base if uncertainty is None else .8 * base + .2 * uncertainty
        candidates = np.flatnonzero(allowed)
        if not len(candidates):
            break
        i = min(candidates, key=lambda i: (-float(gain[i]), units[i].order_key))
        picked.append(int(i))
        details.append({"gain": float(gain[i]), "novelty": float(distance[i]), "coverage_gain": float(coverage[i])})
        counts += membership[i]
        d = np.clip((1 - embeddings @ embeddings[i]) / 2, 0, 1)
        distance = d if len(picked) == 1 and not len(kept_embeddings) else np.minimum(distance, d)
        allowed &= 2 * d >= .1
        allowed[i] = False
        u = units[i]
        if u.video_id is not None and u.t0 is not None:
            allowed &= np.array([v.video_id != u.video_id or v.t0 is None or abs(v.t0 - u.t0) >= 1 for v in units])
    return picked, details


class ODDCoverage(Strategy):
    """Tìm theo ODD × đối tượng và scenario; bổ sung coverage/đa dạng, tùy chọn đo thiếu ổn định."""
    name = "odd_coverage"
    title = "Tìm tình huống và bổ sung coverage"
    Params = ODDParameters
    needs = ("DINO", "CLIP")
    background = model_optional = True
    steps = ("CLIP lọc từng nhánh ODD × đối tượng và scenario độc lập",
             "Hợp ứng viên, giữ thông tin nhánh và giới hạn chi phí",
             "Chọn động theo coverage và DINO; tùy chọn YOLO trên shortlist")

    def run(self, ctx, params, batch_size, seed, *, config=None):
        from ..components.features.clip_b32 import encode_texts
        from .registry import REGISTRY

        if config is not None:
            raise ValueError("ODD strategy không nhận legacy config")
        _cancel(ctx)
        branch = branches(params)
        texts = encode_texts([b["query"] for b in branch] + params.contrast_texts,
                            progress=getattr(ctx, '_on_progress', None), stop=getattr(ctx, 'cancel_event', None))
        positive_text, contrast_text = texts[:len(branch)], texts[len(branch):]
        units = [u for u in ctx.units if u.id not in ctx.state.dropped]
        _progress(ctx, 'ODD_CLIP_GATE', 0, len(units))
        kept = np.array([u.id in ctx.state.kept for u in units], bool)
        clip = _normalize(ctx.field("emb.clip_b32", units).values) if units else np.empty((0, texts.shape[1]))
        eligible, kept_counts = np.zeros(len(branch), int), np.zeros(len(branch), int)
        rankings = [np.empty(0, int) for _ in branch]
        rankscores = [np.empty(0) for _ in branch]
        union_count = 0
        for start in range(0, len(units), 1024):
            _cancel(ctx)
            _, margin, mask, _ = gate(clip[start:start + 1024], positive_text, contrast_text, params)
            is_kept = kept[start:start + len(mask)]
            kept_counts += mask[is_kept].sum(axis=0)
            mask[is_kept] = False
            eligible += mask.sum(axis=0)
            union_count += int(mask.any(axis=1).sum())
            for j in range(len(branch)):
                indices = np.flatnonzero(mask[:, j])
                scores = np.concatenate([rankscores[j], margin[indices, j]])
                indices = np.concatenate([rankings[j], indices + start])
                order = np.lexsort((indices, -scores))[:params.candidate_limit]
                rankings[j], rankscores[j] = indices[order], scores[order]
            _progress(ctx, "ODD_CLIP_GATE", min(start + 1024, len(units)), len(units))
        # Round-robin prevents a populous branch from taking the entire shortlist budget.
        bounded, seen = [], set()
        for rank in range(max(map(len, rankings), default=0)):
            for ranking in rankings:
                if rank < len(ranking) and int(ranking[rank]) not in seen:
                    seen.add(int(ranking[rank]))
                    bounded.append(int(ranking[rank]))
                    if len(bounded) == params.candidate_limit:
                        break
            if len(bounded) == params.candidate_limit:
                break
        candidates = [units[i] for i in bounded]
        cos, margin, membership, relevances = gate(clip[bounded], positive_text, contrast_text, params)
        relevance = np.where(membership, relevances, 0).max(axis=1)
        embeddings = _normalize(ctx.field("emb.dinov2_s", candidates).values) if candidates else np.empty((0, 384))
        kept_units = [u for u in ctx.units if u.id in ctx.state.kept]
        kept_emb = _normalize(ctx.field("emb.dinov2_s", kept_units).values) if kept_units else np.empty((0, 384))
        trace = [{"step": "odd_gate", "pool_count": int((~kept).sum()), "count": union_count,
                  "shortlist_count": len(candidates), "branches": branch, "eligible_counts": eligible.tolist(),
                  "kept_counts": kept_counts.tolist(), "min_similarity": params.min_similarity,
                  "min_margin": params.min_margin, "contrast_texts": params.contrast_texts}]
        model_id = getattr(ctx, "strategy_model_id", None)
        uncertainty = valid = None
        if model_id and candidates:
            pre, _ = greedy(candidates, embeddings, membership, relevance, kept_counts, kept_emb,
                            min(96, 3 * batch_size), ctx)
            candidates = [candidates[i] for i in pre]
            cos, margin, membership, relevances = cos[pre], margin[pre], membership[pre], relevances[pre]
            embeddings, relevance = embeddings[pre], relevance[pre]
            ctx.feature_configs = {**ctx.feature_configs, "det.yolo_consistency": ComponentConfig(
                type="yolo_consistency", params={"model_id": model_id})}
            ctx._features.pop("det.yolo_consistency", None)
            measured = REGISTRY.build("objective", {"type": "detection_instability"}).score(candidates, ctx)
            uncertainty, valid = measured.values, measured.mask
            trace.append({"step": "odd_uncertainty", "count": len(candidates), "observed_count": int(valid.sum())})
        _progress(ctx, 'ODD_SELECT', 0, batch_size)
        selected, details = greedy(candidates, embeddings, membership, relevance, kept_counts, kept_emb,
                                   batch_size, ctx, uncertainty, valid)
        _progress(ctx, 'ODD_SELECT', len(selected), batch_size)
        picks, gains = [], np.zeros(len(candidates))
        selected_counts = np.zeros(len(branch), int)
        for rank, (i, detail) in enumerate(zip(selected, details), 1):
            u = candidates[i]
            matches = np.flatnonzero(membership[i])
            best = max(matches, key=lambda j: margin[i, j])
            selected_counts += membership[i]
            gains[i] = detail["gain"]
            ctx.evidence.setdefault(u.id, []).extend([
                {"kind": "scalar", "label": "Nhánh truy vấn khớp (CLIP)", "value": len(matches),
                 "matching": {"branch_ids": [branch[j]["id"] for j in matches]},
                 "explanation": " · ".join(branch[j]["query"] for j in matches[:3])},
                {"kind": "scalar", "label": "CLIP cosine", "value": float(cos[i, best]), "method": branch[best]["query"]},
                {"kind": "scalar", "label": "CLIP margin", "value": float(margin[i, best]),
                 "method": "Cosine nhánh trừ cosine đối chứng; không phải xác suất"},
                {"kind": "scalar", "label": "Độ bổ sung DINO lúc chọn", "value": detail["novelty"]},
                {"kind": "scalar", "label": "Lợi ích coverage lúc chọn", "value": detail["coverage_gain"]},
            ])
            breakdown = {"relevance": float(relevance[i]), "novelty": detail["novelty"], "coverage": detail["coverage_gain"]}
            if uncertainty is not None:
                breakdown["detection_instability"] = float(uncertainty[i])
            picks.append(Pick(u, detail["gain"], breakdown, rank))
        trace.append({"step": "odd_selection", "count": len(picks), "requested": batch_size,
                      "selected_counts": selected_counts.tolist(), "relaxed": False,
                      "note": "Coverage theo CLIP, chưa phải nhãn ODD xác nhận. Thiếu ứng viên hoặc khoảng cách: không ép đủ batch."})
        fields = sorted(ctx.fields_used(), key=lambda k: (k.name, k.producer, k.version, k.params_hash))
        proposal = Proposal(None, len(ctx.proposals) + 1, len(ctx.proposals) + 1, self.name,
                            identity(self, params, resources={"model_id": model_id, "fields": [asdict(k) for k in fields]}),
                            picks, False, fields)
        _progress(ctx, "ODD_SELECT", len(picks), batch_size)
        return StrategyResult(proposal, {"unit_id": [u.id for u in candidates], "combined": gains.tolist(),
                                        "relevance": relevance.tolist()}, trace)
