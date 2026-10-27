"""L7a — views (embedding_map, timeline, duplicate_pairs), metric keep_rate_by_recipe, get_view / get_metrics, route, CLI spotcheck (#50).

Dùng lại video PyAV + field giả của test_p5a (không chạy DINOv2).
"""

import json
from argparse import Namespace
from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from src.rav.api import create_app
from src.rav.cli.__main__ import spotcheck
from src.rav.commands import ServiceError
from src.rav.components.views.duplicate_pairs import hamming_matrix
from src.rav.core.registry import REGISTRY
from src.rav.core.types import Decision, Proposal, ReviewState

from .test_p5a import app, new_session, run, video_id  # noqa: F401  (fixture `app`)


def test_hamming_matrix():
    h = np.array([0, 0b1011, 0xFFFFFFFFFFFFFFFF], np.uint64)
    assert hamming_matrix(h).tolist() == [[0, 3, 64], [3, 0, 61], [64, 61, 0]]


def test_keep_rate_by_recipe_uses_last_decision_per_unit():
    now = datetime.now(UTC)

    def d(uid, action, n):
        return Decision("s", uid, action, "human:t", "g", n, now, None)

    history = [d("u1", "keep", 1), d("u2", "drop", 1), d("u3", "keep", 2), d("u3", "drop", 2),   # u3: cuối cùng là drop
               d("u4", "keep", 2), d("u4", "clear", None), d("u5", "keep", None)]               # u4 đã clear, u5 ngoài proposal
    ctx = SimpleNamespace(proposals=[Proposal("s", 1, 1, "diversity", "h", []), Proposal("s", 2, 2, "random", "h", [])],
                          state=ReviewState({"u1", "u5"}, {"u2", "u3"}, history))
    out = REGISTRY.build("metric", {"type": "keep_rate_by_recipe"}).evaluate([], ctx)
    assert out == {"keep_rate_by_recipe": 1 / 3, "recipe/diversity": 0.5, "recipe/random": 0.0}


def reviewed_session(app):  # noqa: F811
    s = new_session(app)
    p = run(app, "propose", session_id=s.id, recipe="diversity", batch_size=4)
    ids = [x.unit_id for x in p.picks]
    run(app, "update_state", session_id=s.id, changes=[{"unit_id": ids[0], "action": "keep", "proposal_n": 1},
                                                       {"unit_id": ids[1], "action": "keep", "proposal_n": 1},
                                                       {"unit_id": ids[2], "action": "drop", "proposal_n": 1}])
    p2 = run(app, "propose", session_id=s.id, recipe="random", batch_size=3, seed=0)
    run(app, "update_state", session_id=s.id, changes=[{"unit_id": p2.picks[0].unit_id, "action": "drop", "proposal_n": 2}])
    return s, ids, p2


def test_views_are_json_and_consistent(app):  # noqa: F811
    s, ids, p2 = reviewed_session(app)
    em = run(app, "get_view", session_id=s.id, name="embedding_map")
    json.dumps(em.model_dump())
    status = dict(zip(em.data["unit_id"], em.data["status"]))
    assert len(status) == 12 and status[ids[0]] == "kept" and status[ids[2]] == "dropped"
    assert sorted(u for u, st in status.items() if st == "picked") == sorted(x.unit_id for x in p2.picks[1:])  # proposal mới nhất
    first = run(app, "get_view", session_id=s.id, name="embedding_map", params={"proposal_n": 1}).data["status"]
    assert first.count("picked") == 1                                          # ids[3]: chưa quyết định

    tl = run(app, "get_view", session_id=s.id, name="timeline", params={"series": ["hash.phash"]})
    json.dumps(tl.model_dump())
    t, uid = tl.data["t"], tl.data["unit_id"]
    assert t == sorted(t) and len(tl.data["series"]["hash.phash"]) == 12
    assert sorted(uid[i] for i in tl.data["kept"]) == sorted(ids[:2]) and len(tl.data["picks"]) == 3

    dp = run(app, "get_view", session_id=s.id, name="duplicate_pairs", params={"per_distance": 2, "distances": list(range(65))})
    json.dumps(dp.model_dump())
    hist, pairs = dp.data["hist"], dp.data["pairs"]
    assert len(hist) == 65 and sum(hist) == 12 * 11 // 2
    assert all(sum(p["distance"] == d for p in pairs) == min(2, hist[d]) for d in range(65))
    again = run(app, "get_view", session_id=s.id, name="duplicate_pairs", params={"per_distance": 2, "distances": list(range(65))})
    assert again.data == dp.data                                              # seed cố định → tái lập
    kept = run(app, "get_view", session_id=s.id, name="duplicate_pairs", params={"on": "kept"}).data
    assert sum(kept["hist"]) == 1

    for name, params, code in (("nope", {}, "view_not_found"), ("timeline", {"typo": 1}, "invalid_params"),
                               ("timeline", {"series": ["emb.dinov2_s"]}, "invalid_params"),
                               ("embedding_map", {"proposal_n": 9}, "invalid_params")):
        with pytest.raises(ServiceError) as e:
            run(app, "get_view", session_id=s.id, name=name, params=params)
        assert e.value.code == code


def test_get_metrics_configurable(app):  # noqa: F811
    s, ids, p2 = reviewed_session(app)
    m = run(app, "get_metrics", session_id=s.id)
    assert m.progress.model_dump() == {"target_k": 5, "kept": 2, "dropped": 2, "proposals": 2} and m.on == "kept"
    assert [r.type for r in m.results] == ["nd_rate_at_k", "keep_rate_by_recipe", "review_time", "min_dist", "temporal_coverage",
                                           "cluster_coverage"]                   # mặc định #59
    indep = {r.type: r.values["independent"] for r in m.results}
    assert indep["min_dist"] == 0.0 and indep["review_time"] == indep["temporal_coverage"] == 1.0   # min_dist đọc DINOv2 như diversity
    keep = m.results[1].values
    assert keep["recipe/diversity"] == pytest.approx(2 / 3) and keep["recipe/random"] == 0.0 and keep["independent"] == 1.0
    assert m.results[0].values["independent"] == 1.0                          # diversity chọn bằng DINOv2, metric đo pHash

    two = run(app, "get_metrics", session_id=s.id, on=2, metrics=[{"type": "nd_rate_at_k", "params": {"threshold": 0}},
                                                                   {"type": "nd_rate_at_k", "params": {"threshold": 64}}])
    assert [r.values["nd_rate_at_k"] for r in two.results] == [0.0, 1.0] and two.on == 2   # cùng type khác params: không đè nhau
    for kw, code in (({"on": 9}, "proposal_not_found"), ({"metrics": [{"type": "nope"}]}, "metric_not_found"),
                     ({"metrics": [{"type": "nd_rate_at_k", "params": {"typo": 1}}]}, "invalid_params"),
                     ({"metrics": [{"type": "nd_rate_at_k", "params": {"field": "emb.dinov2_s"}}]}, "invalid_params")):
        with pytest.raises(ServiceError) as e:
            run(app, "get_metrics", session_id=s.id, **kw)
        assert e.value.code == code


def test_view_and_metrics_routes(app):  # noqa: F811
    s, _, _ = reviewed_session(app)
    client = TestClient(create_app(app), raise_server_exceptions=False)
    base = f"/api/v1/sessions/{s.id}"
    dp = client.get(f"{base}/views/duplicate_pairs", params={"distances": 8, "per_distance": 1}).json()
    assert dp["type"] == "duplicate_pairs" and all(p["distance"] == 8 for p in dp["data"]["pairs"])
    tl = client.get(f"{base}/views/timeline", params=[("series", "hash.phash"), ("series", "hash.phash")]).json()
    assert list(tl["data"]["series"]) == ["hash.phash"]
    assert client.get(f"{base}/metrics", params={"on": 1}).json()["on"] == 1
    body = {"metrics": [{"type": "keep_rate_by_recipe"}], "on": "kept"}
    assert client.post(f"{base}/metrics", json=body).json()["results"][0]["type"] == "keep_rate_by_recipe"
    bad = client.post(f"{base}/metrics", json={"metrics": [{"type": "nd_rate_at_k", "params": {"field": "emb.dinov2_s"}}]})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_params"      # không phải 500
    res = client.get(f"{base}/views/nope")
    assert res.status_code == 404 and res.json()["error"]["code"] == "view_not_found"


def test_cli_spotcheck_writes_grid(app, tmp_path):  # noqa: F811
    out = tmp_path / "grid.jpg"
    args = Namespace(video_id=video_id(app, "a.mp4"), distances=[20, 30, 40], per=2, on="pool", session=None, out=str(out))
    spotcheck(app, args)
    spotcheck(app, args)                                                      # lần hai dùng lại phiên spotcheck
    assert [x.name for x in run(app, "list_sessions").root] == [f"spotcheck:{args.video_id}"]
    assert Image.open(out).size[1] == 3 * (200 + 12)


def _old_duplicate_pairs(h, distances, per, seed):
    """Bản ma trận N×N trước đây — chuẩn để so."""
    ia, ib = np.triu_indices(len(h), k=1)
    dist = hamming_matrix(h)[ia, ib]
    rng = np.random.default_rng(seed)
    pairs = [(int(ia[k]), int(ib[k]), d) for d in distances
             for k in sorted(rng.choice(np.flatnonzero(dist == d), min(int((dist == d).sum()), per), replace=False))]
    return np.bincount(dist, minlength=65).tolist(), pairs


def test_duplicate_pairs_blockwise_matches_full_matrix_and_bounded_memory():
    """View tính theo khối trên pool: cùng histogram và cùng cặp mẫu (cùng seed) như bản N×N; RAM không theo N² (pool 12.5k → ~5 GB)."""
    import tracemalloc

    from src.rav.components.views.duplicate_pairs import DuplicatePairs

    from .test_p3 import hash_ctx
    rng = np.random.default_rng(1)
    base = rng.integers(0, 2**63, 1300, dtype=np.uint64)
    base[::7] = base[0] ^ rng.integers(0, 2**10, len(base[::7]), dtype=np.uint64)   # cụm gần trùng trải nhiều khối
    ctx, ids = hash_ctx(base.tolist())
    distances = [0, 2, 4, 6, 8, 10, 12, 14, 16, 30, 99]
    spec = DuplicatePairs(DuplicatePairs.Params(distances=distances, per_distance=3, seed=5)).build(ctx)
    hist, pairs = _old_duplicate_pairs(base, distances, 3, 5)
    assert spec.data["hist"] == hist and sum(hist) == 1300 * 1299 // 2
    assert [(ids.index(p["a"]), ids.index(p["b"]), p["distance"]) for p in spec.data["pairs"]] == pairs
    assert any(p["distance"] <= 8 for p in spec.data["pairs"])

    big, _ = hash_ctx(rng.integers(0, 2**63, 6000, dtype=np.uint64).tolist())
    tracemalloc.start()
    DuplicatePairs(DuplicatePairs.Params()).build(big)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert peak < 300 * 2**20                                      # bản N×N cũ cần ~2 GB ở N=6000
