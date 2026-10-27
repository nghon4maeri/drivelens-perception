"""Additive bounded scores so low novelty never zeroes model-risk priority (§18)."""
import numpy as np

from ...core.registry import register
from .base import Combiner


@register("combiner")
class WeightedMean(Combiner):
    """Trung bình có trọng số strength≥0; mask AND; không dùng tích khiến một score 0 triệt hết tín hiệu khác."""

    name = "weighted_mean"
    version = "1"

    def combine(self, scores, unit_ids):
        values, mask, total = np.zeros(len(unit_ids)), np.ones(len(unit_ids), bool), 0.
        for s, weight in scores:
            if s.unit_ids != unit_ids or not np.isfinite(weight) or weight < 0:
                raise ValueError("Weighted mean cần IDs đúng và trọng số hữu hạn không âm")
            if not np.isfinite(s.values).all() or np.any(np.asarray(s.values) < 0) or np.any(np.asarray(s.values) > 1):
                raise ValueError("Weighted mean cần score hữu hạn trong [0,1]")
            values += np.asarray(s.values) * weight
            total += weight
            if s.mask is not None:
                mask &= s.mask
        if total <= 0:
            raise ValueError("Weighted mean cần tổng trọng số >0")
        return values / total, mask
