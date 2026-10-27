"""L4 — P3: metric `nd_rate_at_k` (pHash dựng tay), kiểm độc lập, `evaluate` qua registry, benchmark trên pool 2 video.

Không chạy DINOv2 / pHash thật: execution giả trả embedding + hash dựng sẵn.
"""

import numpy as np

from src.rav.commands import COMMANDS
from src.rav.commands.benchmark import benchmark
from src.rav.core.context import Context
from src.rav.core.registry import REGISTRY
from src.rav.core.types import FieldKey, FieldTable, Frame, Unit, unit_id
from src.rav.pipeline.evaluate import evaluate
from src.rav.pipeline.propose import propose

from .test_p2 import make_pool

METRIC = {"type": "nd_rate_at_k"}


class FakeExecution:
    """Field dựng sẵn theo tên: `hash.phash` (uint64), `emb.dinov2_s` (vector)."""

    def __init__(self, fields):
        self.fields = fields

    def run(self, feature, units, ctx):
        values = np.stack([self.fields[feature.provides][u.id] for u in units])
        return FieldTable(feature.key, [u.id for u in units], values, "vector" if values.ndim == 2 else "scalar")


def hash_ctx(hashes, videos=None):
    videos = videos or ["a"] * len(hashes)
    units = [Unit(unit_id(v, i, i), v, i, i, "frame", Frame(v, i, i), (Frame(v, i, i),)) for i, v in enumerate(videos)]
    fields = {"hash.phash": {u.id: np.uint64(h) for u, h in zip(units, hashes)}}
    return Context(units, execution=FakeExecution(fields)), [u.id for u in units]


def test_nd_rate_hand_built_hashes():
    ctx, ids = hash_ctx([0b0, 0b1, 0xFFFF])                     # 0 và 1 cách 1 bit; 0xFFFF cách ≥ 15
    out = evaluate([METRIC], ids, [], ctx)[0]
    assert out["nd_rate_at_k"] == 2 / 3 and out["independent"] == 1.0 and out["by_video/a"] == 2 / 3


def test_nd_rate_threshold_edges_and_no_dup():
    ctx, ids = hash_ctx([0, 0xFF, 0x1FF << 20])                   # 8 bit: trùng (≤ 8, mặc định); 9 bit: không
    assert evaluate([METRIC], ids[:2], [], ctx)[0]["nd_rate_at_k"] == 1.0
    assert evaluate([METRIC], [ids[0], ids[2]], [], ctx)[0]["nd_rate_at_k"] == 0.0
    assert evaluate([{"type": "nd_rate_at_k", "params": {"threshold": 9}}], [ids[0], ids[2]], [], ctx)[0]["nd_rate_at_k"] == 1.0
    big = np.uint64(1 << 63) | np.uint64(0b11)                  # bit cao nhất không làm hỏng popcount
    ctx, ids = hash_ctx([int(big), int(big) ^ 1])
    assert evaluate([METRIC], ids, [], ctx)[0]["nd_rate_at_k"] == 1.0


def test_nd_rate_empty_set_is_zero():
    ctx, _ = hash_ctx([1, 2])
    assert evaluate([METRIC], [], [], ctx)[0] == {"nd_rate_at_k": 0.0, "independent": 1.0}


def test_nd_rate_by_video_counts_cross_video_duplicates():
    ctx, ids = hash_ctx([0, 1, 0xFFFF00, 0xFFFFFFFF << 30], videos=["a", "a", "b", "b"])
    out = evaluate([METRIC], ids, [], ctx)[0]
    assert out["nd_rate_at_k"] == 0.5 and out["by_video/a"] == 1.0 and out["by_video/b"] == 0.0
    ctx, ids = hash_ctx([0, 0xFFFF00, 1], videos=["a", "b", "b"])  # cặp trùng nằm ở hai video
    out = evaluate([METRIC], ids, [], ctx)[0]
    assert out["by_video/a"] == 1.0 and out["by_video/b"] == 0.5


def test_independence_follows_proposal_fields_used_not_ctx():
    ctx, ids = hash_ctx([0, 1, 0xFFFF])
    phash_key = FieldKey("hash.phash", "phash", "1", "x")
    assert evaluate([METRIC], ids, [phash_key], ctx)[0]["independent"] == 0.0     # recipe đã chọn bằng pHash
    ctx.field("hash.phash")                                      # ai đó đọc pHash qua ctx: không ảnh hưởng (#44)
    emb_key = FieldKey("emb.dinov2_s", "dinov2_s", "1", "x")
    assert evaluate([METRIC], ids, [emb_key], ctx)[0]["independent"] == 1.0


def test_second_metric_on_same_field_still_independent():
    ctx, ids = hash_ctx([0, 1, 0xFFFF])
    a, b = evaluate([METRIC, {"type": "nd_rate_at_k", "params": {"threshold": 16}}], ids, [], ctx)
    assert a["independent"] == b["independent"] == 1.0 and (a["nd_rate_at_k"], b["nd_rate_at_k"]) == (2 / 3, 1.0)


def test_proposal_records_fields_used_by_recipe():
    make = pool_ctx_factory()
    div = propose(REGISTRY.recipe("diversity"), 5, 0, make())
    assert [k.name for k in div.fields_used] == ["emb.dinov2_s"]
    assert propose(REGISTRY.recipe("random"), 5, 0, make()).fields_used == []


def pool_ctx_factory():
    units, emb = make_pool()                                     # 2 video "a" (32 unit), "b" (20 unit)
    rng = np.random.default_rng(1)
    scene_hash = {}
    phash = {}
    for u in units:                                              # cùng cảnh (2 s) → hash gần nhau
        base = scene_hash.setdefault((u.video_id, int(u.t0 // 2)), int(rng.integers(0, 2**63)))
        phash[u.id] = np.uint64(base ^ (1 << int(rng.integers(0, 3))))
    fields = {"emb.dinov2_s": {k: v.astype(np.float32) for k, v in emb.items()}, "hash.phash": phash}
    return lambda: Context(units, execution=FakeExecution(fields))


def test_benchmark_recipes_seeds_k_on_two_video_pool():
    metrics = [METRIC, {"type": "nd_rate_at_k", "params": {"threshold": 64}}]   # cùng type, khác params: không đè nhau
    rows = benchmark(["diversity", "random", "uniform"], [5, 100], [0, 1], metrics, pool_ctx_factory())
    assert [(r.recipe, r.K, r.seed) for r in rows][:4] == [("diversity", 5, 0), ("diversity", 5, 1),
                                                           ("diversity", 100, 0), ("diversity", 100, 1)]
    assert len(rows) == 3 * 2 * 2
    for r in rows:
        assert r.n == min(r.K, 52)
        m, loose = r.metrics
        assert 0.0 <= m["nd_rate_at_k"] <= 1.0 and m["independent"] == 1.0
        assert {k for k in m if k.startswith("by_video/")} == {"by_video/a", "by_video/b"}
        assert loose["nd_rate_at_k"] == (1.0 if r.n > 1 else 0.0)   # ngưỡng 64 bit: mọi cặp đều "gần trùng"
    div = [r for r in rows if r.recipe == "diversity" and r.K == 5]
    assert div[0].metrics == div[1].metrics                     # không có ngẫu nhiên → tất định
    k5 = {r.recipe: r.metrics[0]["nd_rate_at_k"] for r in rows if r.K == 5 and r.seed == 0}
    assert k5["diversity"] == 0.0                                # 5 cảnh khác nhau → không cặp nào gần trùng


def test_run_benchmark_command_registered():
    cmd = COMMANDS["run_benchmark"]
    inp = cmd.Input()
    assert inp.K == [200] and [m.type for m in inp.metrics] == ["nd_rate_at_k"] and not cmd.mutates
    assert cmd.Input(metrics=[{"type": "nd_rate_at_k", "params": {"threshold": 8}}]).metrics[0].params == {"threshold": 8}


def test_pairwise_metrics_blockwise_match_full_matrix_and_bounded_memory():
    """min_dist / nd_rate tính theo khối: khớp công thức ma trận N×N cũ (N không chia hết khối, có hash trùng) và RAM không theo N²."""
    import tracemalloc
    rng = np.random.default_rng(0)
    n = 1300
    hashes = rng.integers(0, 2**63, n, dtype=np.uint64)
    hashes[100:110] = hashes[0] ^ np.uint64(0b111)                 # vài unit gần trùng, nằm khác khối
    emb = rng.normal(size=(n, 16))
    units = [Unit(unit_id(v, i, i), v, i, i, "frame", Frame(v, i, i), (Frame(v, i, i),)) for i, v in enumerate(["a", "b"] * (n // 2))]
    ids = [u.id for u in units]
    ctx = Context(units, execution=FakeExecution({"hash.phash": dict(zip(ids, hashes)), "emb.dinov2_s": dict(zip(ids, emb))}))
    nd, md = evaluate([METRIC, {"type": "min_dist"}], ids, [], ctx)

    bits = np.unpackbits((hashes[:, None] ^ hashes[None, :]).view(np.uint8).reshape(n, n, 8), axis=-1).sum(-1)
    np.fill_diagonal(bits, 65)
    dup = (bits <= 8).any(1)
    assert nd["nd_rate_at_k"] == float(dup.mean()) and nd["by_video/a"] == float(dup[::2].mean())
    x = emb / np.linalg.norm(emb, axis=1, keepdims=True)
    sim = x @ x.T
    np.fill_diagonal(sim, -np.inf)
    d = 1.0 - sim.max(axis=1)
    assert np.isclose(md["min_dist"], d.mean()) and np.isclose(md["min_dist/min"], d.min())

    big, big_ids = hash_ctx(rng.integers(0, 2**63, 6000, dtype=np.uint64).tolist())
    tracemalloc.start()
    evaluate([METRIC], big_ids, [], big)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert peak < 300 * 2**20                                      # bản N×N cũ cần ~2,9 GB ở N=6000
