"""Soft novelty against training/kept with nearest training evidence, bounded matrix memory (§18)."""
import numpy as np

from ...core.registry import register
from ...core.types import Scores
from .base import Objective


@register("objective")
class ReferenceNovelty(Objective):
    """(1−max cosine DINO tới dataset train + kept)/2; thấp vẫn được chọn nếu tín hiệu khác cao. Không là training value."""

    name = "reference_novelty"
    version = "1"

    class Params(Objective.Params):
        field: str = "emb.dinov2_s"

    def score(self, units, ctx):
        if not units:
            return Scores(self.name, [], np.empty(0))
        refs = ctx.reference()
        training = ctx.reference_sets.get("training", [])
        if not training:
            raise ValueError("Thiếu dataset train tham chiếu")
        x = np.asarray(ctx.field(self.params.field, units).values, np.float32)
        r = np.asarray(ctx.field(self.params.field, refs).values, np.float32)
        if x.ndim != 2 or r.ndim != 2 or x.shape[1] != r.shape[1]:
            raise ValueError("Embedding pool/train khác dimension; chuẩn bị cùng producer")
        if any(not np.isfinite(v).all() or np.any(np.linalg.norm(v, axis=1) < 1e-12) for v in (x, r)):
            raise ValueError("Embedding pool/train không finite hoặc vector rỗng")
        x = x / np.maximum(np.linalg.norm(x, axis=1, keepdims=True), 1e-12)
        r = r / np.maximum(np.linalg.norm(r, axis=1, keepdims=True), 1e-12)
        train_ids = {u.id for u in training}
        train_rows = np.array([i for i, u in enumerate(refs) if u.id in train_ids])
        values = []
        for start in range(0, len(units), 64):
            sim = np.clip(x[start:start + 64] @ r.T, -1, 1)
            values.extend(np.clip((1 - sim.max(axis=1)) / 2, 0, 1))
            best = train_rows[sim[:, train_rows].argmax(axis=1)]
            for i, j in enumerate(best):
                ref = refs[j].anchor
                ctx.evidence[units[start + i].id] = [{"kind": "nearest", "label": "Ảnh train gần nhất",
                    "source_id": ref.source_id, "key": ref.key, "unit_id": refs[j].id,
                    "similarity": float(sim[i, j]), "field": self.params.field,
                    "image_url": f"/api/v1/datasets/versions/{ref.source_id}/images/{ref.key}?max_side=320"}]
        return Scores(self.name, [u.id for u in units], np.asarray(values))
