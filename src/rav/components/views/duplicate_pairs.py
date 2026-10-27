"""`duplicate_pairs`: cặp frame theo khoảng cách pHash, lấy mẫu theo từng mức để spot-check ngưỡng ND-Rate (CONTRACTS §9.10, #50)."""

from __future__ import annotations

from typing import Literal

import numpy as np
from pydantic import Field

from ...core.registry import register
from ...core.types import ViewSpec
from .base import View, proposal

POPCOUNT = np.array([bin(i).count("1") for i in range(256)], np.uint8)


BLOCK = 512


def hamming(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """(len(a), len(b)) khoảng cách Hamming giữa các hash 64-bit."""
    x = (a[:, None] ^ b[None, :]).view(np.uint8).reshape(len(a), len(b), 8)
    return POPCOUNT[x].sum(-1, dtype=np.uint8)


def hamming_matrix(h: np.ndarray) -> np.ndarray:
    """(N, N) khoảng cách Hamming giữa các hash 64-bit (chỉ cho N nhỏ)."""
    return hamming(h, h).astype(np.int64)


def _upper_blocks(h: np.ndarray):
    """Khoảng cách các cặp (i, j > i) theo khối hàng, đúng thứ tự `triu_indices`: (s, d, mask). RAM O(BLOCK·N) thay vì N×N
    (pool 12.5k unit → ~5 GB, vượt RSS worker)."""
    cols = np.arange(len(h))
    for s in range(0, len(h), BLOCK):
        d = hamming(h[s:s + BLOCK], h)
        yield s, d, cols[None, :] > np.arange(s, s + len(d))[:, None]


@register("view")
class DuplicatePairs(View):
    """Mọi cặp (a, b) trong tập `on` (pool / kept / picks của proposal `proposal_n`, mặc định mới nhất), khoảng cách = Hamming của field `field`
    (mặc định pHash). `hist[d]` = số cặp có khoảng cách đúng d (0..64). `pairs` = với mỗi d trong `distances`, tối đa `per_distance` cặp
    có khoảng cách đúng d, chọn ngẫu nhiên theo `seed` (tái lập). Người xem từng mức d để biết tới đâu thì còn là "gần trùng"."""

    name = "duplicate_pairs"
    version = "1"

    class Params(View.Params):
        field: str = "hash.phash"
        on: Literal["pool", "kept", "proposal"] = "pool"
        proposal_n: int | None = None
        distances: list[int] = Field(default_factory=lambda: [0, 2, 4, 6, 8, 10, 12, 14, 16])
        per_distance: int = Field(4, ge=1)
        seed: int = 0

    def build(self, ctx) -> ViewSpec:
        if self.params.on == "kept":
            units = [u for u in ctx.units if u.id in ctx.state.kept]
        elif self.params.on == "proposal":
            p = proposal(ctx, self.params.proposal_n)
            units = [pick.unit for pick in p.picks] if p else []
        else:
            units = list(ctx.units)
        hist = [0] * 65
        pairs = []
        if len(units) >= 2:
            h = np.asarray(ctx.field(self.params.field, units).values, dtype=np.uint64)
            counts = np.zeros(65, np.int64)
            for _, d, mask in _upper_blocks(h):          # lượt 1: histogram
                counts += np.bincount(d[mask], minlength=65)
            hist = counts.tolist()
            # lượt 2: chọn hạng (thứ tự triu) đúng như bản ma trận cũ rồi tìm cặp tương ứng — cùng seed, cùng kết quả
            rng = np.random.default_rng(self.params.seed)
            want = []
            for d in self.params.distances:
                n = hist[d] if 0 <= d <= 64 else 0
                want.append((d, sorted(rng.choice(n, min(n, self.params.per_distance), replace=False))))
            found: list[list] = [[] for _ in want]
            seen = np.zeros(65, np.int64)
            for s, block, mask in _upper_blocks(h):
                here = np.bincount(block[mask], minlength=65)
                for (d, ranks), out in zip(want, found):
                    lo = seen[d] if 0 <= d <= 64 else 0
                    local = [r - lo for r in ranks if lo <= r < lo + (here[d] if 0 <= d <= 64 else 0)]
                    if local:
                        rows, cols = np.nonzero(mask & (block == d))
                        out += [(s + int(rows[k]), int(cols[k])) for k in local]
                seen += here
            for (d, _), out in zip(want, found):
                for i, j in out:
                    a, b = units[i], units[j]
                    pairs.append({"a": a.id, "b": b.id, "distance": int(d),
                                  "a_frame": [a.anchor.video_id, a.anchor.idx], "b_frame": [b.anchor.video_id, b.anchor.idx]})
        return ViewSpec(self.name, {"hist": hist, "pairs": pairs})
