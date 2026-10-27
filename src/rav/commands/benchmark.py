"""`run_benchmark(recipes, K, seeds, metrics?, video_ids?)`: gọi pipeline P2 + P3 với state rỗng, không DB (CONTRACTS §6, §11.2; #42–#44).

Mỗi lượt recipe × seed × K dựng `Context` mới (state rỗng), chọn một lần K unit (`batch_size = K`),
rồi chấm (kiểm độc lập theo `Proposal.fields_used`, #44) bằng từng metric trong `metrics` (config `{"type", "params"}`, áp chung mọi recipe, không nằm trong recipe — #43).
CLI: `python -m src.rav.cli benchmark --k 200 --seeds 0 1 2 3 4 --metric nd_rate_at_k threshold=8`.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, Field

from ..core.context import Context
from ..core.registry import REGISTRY, discover
from ..core.types import ComponentConfig
from ..pipeline import prepare as p1
from ..pipeline.evaluate import evaluate
from ..pipeline.propose import propose
from ..service import App
from ..service.video import get_video
from .base import Command, command


class BenchmarkIn(BaseModel):
    recipes: list[str] | None = Field(None, description="None = mọi recipe có sẵn")
    K: list[int] = Field([200], description="số frame chọn mỗi lượt (batch_size = K)")
    seeds: list[int] = [0, 1, 2, 3, 4]
    video_ids: list[str] | None = Field(None, description="pool; None = mọi video trong catalog")
    metrics: list[ComponentConfig] = Field(default_factory=lambda: [ComponentConfig(type="nd_rate_at_k")],
                                           description="cách chấm, áp chung mọi recipe")


class BenchmarkRow(BaseModel):
    recipe: str
    seed: int
    K: int
    n: int                      # số unit thật sự chọn được (≤ K nếu pool nhỏ)
    relaxed: bool
    metrics: list[dict[str, float]]   # cùng thứ tự BenchmarkIn.metrics; khoá theo #42


class BenchmarkOut(BaseModel):
    pool_size: int
    rows: list[BenchmarkRow]


def benchmark(recipes: list[str], ks: list[int], seeds: list[int], metrics: list[ComponentConfig | dict],
              make_ctx: Callable[[], Context]) -> list[BenchmarkRow]:
    """Lõi của command, tách ra để test với ctx giả."""
    discover()
    rows = []
    for name in recipes:
        recipe = REGISTRY.recipe(name)
        for k in ks:
            for seed in seeds:
                ctx = make_ctx()
                prop = propose(recipe, k, seed, ctx)
                ids = [p.unit.id for p in prop.picks]
                rows.append(BenchmarkRow(recipe=name, seed=seed, K=k, n=len(ids), relaxed=prop.relaxed,
                                         metrics=evaluate(metrics, ids, prop.fields_used, ctx)))
    return rows


@command
class RunBenchmark(Command):
    name = "run_benchmark"
    description = "So recipe với baseline: mỗi recipe × seed × K chọn một lần K frame (state rỗng), chấm bằng các metric chọn qua config (mặc định ND-Rate@K pHash)."
    Input, Output = BenchmarkIn, BenchmarkOut

    def run(self, inp: BenchmarkIn, app: App) -> BenchmarkOut:
        catalog = p1.catalog(app.spec)
        videos = [get_video(app, v) for v in inp.video_ids] if inp.video_ids else list(catalog.values())

        def make_ctx() -> Context:
            return p1.make_context(videos, app.spec, app.fields)

        rows = benchmark(inp.recipes or REGISTRY.recipe_names(), inp.K, inp.seeds, inp.metrics, make_ctx)
        return BenchmarkOut(pool_size=len(make_ctx().units), rows=rows)
