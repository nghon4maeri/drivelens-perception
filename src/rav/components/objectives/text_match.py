"""CLIP cosine text gate/ranking (CONTRACTS §15); explicit query config, not inferred session goal."""
from __future__ import annotations

import numpy as np
from pydantic import Field, field_validator

from ...core.registry import register
from ...core.types import Scores
from ..features.clip_b32 import ClipB32, encode_texts, normalize
from .base import Objective


@register("objective")
class TextMatch(Objective):
    """Max cosine to positive texts >= min_similarity, and > max negative + margin. Score=(cos+1)/2.
    Similarity is not probability. Explicit texts in proposal config; does not infer/translate session goal.
    """

    name = "text_match"
    version = "1"

    class Params(Objective.Params):
        field: str = "emb.clip_b32"
        texts: list[str] = Field(default_factory=lambda: ["a dashcam photo of cars on a street"], min_length=1, max_length=32)
        negative: list[str] = Field(default_factory=list, max_length=32)
        min_similarity: float = Field(0.2, ge=-1, le=1, description="Ngưỡng cosine heuristic, chưa hiệu chỉnh; không phải xác suất")
        negative_margin: float = Field(0.0, ge=0, le=2)

        @field_validator("field")
        @classmethod
        def clip_field(cls, value):
            if value != "emb.clip_b32":
                raise ValueError("text_match cần emb.clip_b32, không trộn DINO/text CLIP")
            return value

        @field_validator("texts", "negative")
        @classmethod
        def nonblank(cls, texts):
            if any(not s.strip() for s in texts):
                raise ValueError("Câu CLIP không được rỗng")
            return [s.strip() for s in texts]

    def score(self, units, ctx) -> Scores:
        ids = [u.id for u in units]
        if not units:
            return Scores(self.name, ids, np.empty(0), np.empty(0, bool))
        p = self.params
        # Component contract: image field via Context, text helper uses the identical pinned checkpoint.
        emb = normalize(ctx.field(p.field, units).values)
        feature = ClipB32()
        text = encode_texts(p.texts + p.negative, feature.params.model, feature.params.revision)
        cos = emb @ text.T
        positive = cos[:, :len(p.texts)].max(axis=1)
        mask = positive >= p.min_similarity
        if p.negative:
            mask &= positive > cos[:, len(p.texts):].max(axis=1) + p.negative_margin
        return Scores(self.name, ids, np.clip((positive + 1) / 2, 0, 1), mask)
