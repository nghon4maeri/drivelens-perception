"""Greedy dynamic diversity plus seeded exploration within the same validity/NMS gates (§18)."""
import numpy as np
from pydantic import Field

from ...core.registry import register
from ...core.types import SelectResult
from .base import Selector


@register("selector")
class ExploreDiversity(Selector):
    """75% quota theo combined×(0.5+0.5×distance tới batch); còn lại khám phá random seed, NMS/min_dist không nới."""

    name = "explore_diversity"
    version = "1"

    class Params(Selector.Params):
        field: str = "emb.dinov2_s"
        exploration: float = Field(.25, ge=0, le=1)
        nms_s: float = Field(1., ge=0)
        min_dist: float = Field(.1, ge=0, le=2)

    def pick(self, units, combined, mask, n, ctx):
        if not units or not np.any(mask):
            return SelectResult([])
        x = np.asarray(ctx.field(self.params.field, units).values, np.float64)
        x /= np.maximum(np.linalg.norm(x, axis=1, keepdims=True), 1e-12)
        available = set(np.flatnonzero(mask).tolist())
        took, evidence = [], {}
        preferred = n - round(n * self.params.exploration)
        while available and len(took) < n:
            gain = {}
            for i in available:
                u = units[i]
                if any(u.video_id is not None and u.video_id == units[j].video_id
                       and abs(u.t0 - units[j].t0) < self.params.nms_s for j in took):
                    continue
                distance = float(np.min(1 - x[i] @ x[took].T)) if took else 1.
                if took and distance < self.params.min_dist:
                    continue
                gain[i] = float(combined[i]) * (.5 + .5 * np.clip(distance / 2, 0, 1)) if took else float(combined[i])
            if not gain:
                break
            if len(took) < preferred:
                chosen = min(gain, key=lambda i: (-gain[i], units[i].order_key))
                method = "ưu tiên + đa dạng trong đợt"
            else:
                chosen = int(ctx.rng.choice(sorted(gain)))
                method = "khám phá có seed"
            took.append(chosen)
            available.remove(chosen)
            evidence[units[chosen].id] = [{"kind": "scalar", "label": "Selection gain", "value": gain[chosen], "method": method}]
        return SelectResult(took, evidence=evidence)
