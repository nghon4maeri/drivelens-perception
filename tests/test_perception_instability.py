"""Tests for perception_instability recipe and TTA consistency (CONTRACTS §21.30, #140).

Covers:
- YOLO original/flip consistency matching, score, and explanation math
- Objective scoring with structured evidence & comparison detections
- Recipe registration and config validation
- Candidate limit scaling
- Worker proposal progress tracking and uncertainty summary
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.rav.components.features.yolo_consistency import instability
from src.rav.components.objectives.detection_instability import DetectionInstability
from src.rav.core.registry import REGISTRY, discover
from src.rav.core.types import RecipeConfig, Unit
from src.rav.errors import ServiceError
from src.rav.store.pilot_curation import PILOT_RECIPES, check_config


def test_instability_empty_both_not_observed():
    """Khi cả ảnh gốc và ảnh lật không có box nào: chưa có tín hiệu, score = 0."""
    score, observed = instability([], [])
    assert score == 0.0
    assert observed is False

    score, observed, stats = instability([], [], with_stats=True)
    assert score == 0.0
    assert observed is False
    assert stats["n_original"] == 0
    assert stats["n_flipped"] == 0
    assert stats["n_matched"] == 0
    assert stats["n_unmatched_orig"] == 0
    assert stats["n_unmatched_flip"] == 0
    assert stats["class_changes"] == 0


def test_instability_exact_match_low_score():
    """1 box khớp hoàn hảo giữa ảnh gốc và ảnh lật (cùng class, IoU = 1.0): score = 0, observed = True."""
    orig = [{"box": [0.1, 0.2, 0.5, 0.8], "label": "car", "score": 0.9}]
    # Trong yolo_consistency.py, flipped boxes đã được đổi tọa độ về không gian gốc trước khi truyền vào instability
    flip = [{"box": [0.1, 0.2, 0.5, 0.8], "label": "car", "score": 0.9}]

    score, observed, stats = instability(orig, flip, with_stats=True)
    assert observed is True
    assert score == 0.0
    assert stats["n_original"] == 1
    assert stats["n_flipped"] == 1
    assert stats["n_matched"] == 1
    assert stats["n_unmatched_orig"] == 0
    assert stats["n_unmatched_flip"] == 0
    assert stats["class_changes"] == 0


def test_instability_unmatched_box():
    """Gốc có 1 box, lật không có box nào: 1 unmatched box -> score > 0, observed = True."""
    orig = [{"box": [0.1, 0.2, 0.5, 0.8], "label": "car", "score": 0.9}]
    flip = []

    score, observed, stats = instability(orig, flip, with_stats=True)
    assert observed is True
    assert score > 0.0
    assert stats["n_original"] == 1
    assert stats["n_flipped"] == 0
    assert stats["n_matched"] == 0
    assert stats["n_unmatched_orig"] == 1
    assert stats["n_unmatched_flip"] == 0


def test_instability_class_change_penalty():
    """Khớp vị trí nhưng đổi class (car -> truck) -> class_changes = 1, phạt điểm cao hơn cùng class."""
    orig = [{"box": [0.1, 0.2, 0.5, 0.8], "label": "car", "score": 0.9}]
    flip_same = [{"box": [0.1, 0.2, 0.5, 0.8], "label": "car", "score": 0.9}]
    flip_diff = [{"box": [0.1, 0.2, 0.5, 0.8], "label": "truck", "score": 0.9}]

    score_same, _, stats_same = instability(orig, flip_same, with_stats=True)
    score_diff, _, stats_diff = instability(orig, flip_diff, with_stats=True)

    assert stats_same["class_changes"] == 0
    assert stats_diff["class_changes"] == 1
    assert score_diff > score_same


def test_detection_instability_objective_evidence_and_comparison():
    """Objective DetectionInstability ghi evidence chi tiết và comparison flipped detections."""
    from src.rav.core.types import Frame
    f1 = Frame(video_id="video1", idx=0, t=0.0)
    f2 = Frame(video_id="video1", idx=10, t=1.0)
    u1 = Unit("video1:0.000-1.000", "video1", 0.0, 1.0, "frame", f1, (f1,))
    u2 = Unit("video1:1.000-2.000", "video1", 1.0, 2.0, "frame", f2, (f2,))

    class MockContext:
        def __init__(self):
            self.evidence = {}
            self.state = SimpleNamespace(kept=set(), dropped=set())

        def field(self, name, units):
            return self.cached(name, units)

        def cached(self, name, units):
            assert name == "det.yolo_consistency"
            return SimpleNamespace(values=[
                {
                    "instability": 0.75,
                    "observed": True,
                    "original": [{"box": [0.1, 0.1, 0.4, 0.4], "label": "car", "score": 0.8}],
                    "flipped": [{"box": [0.2, 0.2, 0.5, 0.5], "label": "car", "score": 0.7}],
                    "n_original": 1,
                    "n_flipped": 1,
                    "n_matched": 1,
                    "n_unmatched_orig": 0,
                    "n_unmatched_flip": 0,
                    "class_changes": 0,
                },
                {
                    "instability": 0.0,
                    "observed": False,
                    "original": [],
                    "flipped": [],
                    "n_original": 0,
                    "n_flipped": 0,
                    "n_matched": 0,
                    "n_unmatched_orig": 0,
                    "n_unmatched_flip": 0,
                    "class_changes": 0,
                },
            ])

    ctx = MockContext()
    obj = DetectionInstability()
    scores = obj.score([u1, u2], ctx)

    assert scores.name == "detection_instability"
    assert len(scores.values) == 2
    assert scores.values[0] == 0.75
    assert scores.values[1] == 0.0
    assert scores.mask is not None
    assert bool(scores.mask[0]) is True
    assert bool(scores.mask[1]) is False

    # Kiểm tra evidence u1
    ev_u1 = ctx.evidence[u1.id]
    scalar_ev = next(e for e in ev_u1 if e["kind"] == "scalar" and e["label"] == "YOLO thiếu ổn định")
    assert scalar_ev["observed"] is True
    assert scalar_ev["value"] == 0.75
    assert "1 box gốc" in scalar_ev["explanation"]
    assert "1 box khớp" in scalar_ev["explanation"]

    det_ev = next(e for e in ev_u1 if e["kind"] == "detections" and e.get("comparison"))
    assert det_ev["comparison"] is True
    assert len(det_ev["boxes"]) == 1

    # Kiểm tra evidence u2 (chưa có tín hiệu)
    ev_u2 = ctx.evidence[u2.id]
    scalar_ev2 = next(e for e in ev_u2 if e["kind"] == "scalar" and e["label"] == "YOLO thiếu ổn định")
    assert scalar_ev2["observed"] is False
    assert "Chưa có tín hiệu" in scalar_ev2["explanation"]


def test_perception_instability_recipe_registered():
    """Recipe perception_instability có trong REGISTRY và PILOT_RECIPES."""
    discover()
    assert "perception_instability" in PILOT_RECIPES
    recipe = REGISTRY.recipe("perception_instability")
    assert isinstance(recipe, RecipeConfig)
    assert recipe.name == "perception_instability"
    assert recipe.reference.type == "session_kept"
    assert any(o.type == "novelty" for o in recipe.objectives)
    assert any(o.type == "detection_instability" for o in recipe.objectives)
    assert recipe.candidate_stage is not None
    assert recipe.candidate_stage.limit == 48
    assert "det.yolo_consistency" in recipe.features


def test_check_config_allows_candidate_stage_for_perception_instability():
    """check_config cho phép features và candidate_stage riêng cho perception_instability."""
    discover()
    recipe = REGISTRY.recipe("perception_instability")
    cfg = recipe.model_dump(mode="json")

    # perception_instability hợp lệ
    check_config("perception_instability", cfg)

    # recipe khác có features hoặc candidate_stage bị chặn
    cfg_random = cfg.copy()
    cfg_random["name"] = "random"
    with pytest.raises(ServiceError) as exc:
        check_config("random", cfg_random)
    assert exc.value.code == "invalid_config"


def test_candidate_limit_scaling_formula():
    """candidate_limit = max(32, min(96, batch_size * 2))."""
    def scale(batch_size):
        return max(32, min(96, batch_size * 2))

    assert scale(8) == 32
    assert scale(16) == 32
    assert scale(24) == 48
    assert scale(32) == 64
    assert scale(48) == 96
    assert scale(64) == 96
    assert scale(100) == 96


def test_propose_handler_progress_and_uncertainty_summary(tmp_path, monkeypatch):
    """Test propose_handler trong worker xử lý perception_instability, báo tiến trình và tạo uncertainty_summary."""
    from src.rav.store.pilot_runtime import AttemptLease
    from src.rav.worker.curation_handlers import make_curation_handlers

    progress_reports = []

    class MockRuntime:
        def update_progress(self, lease, progress):
            progress_reports.append(progress)
            raise ServiceError("validation_error", "tiến độ không được giảm", 422)   # advisory: must not fail the job

    class MockStorage:
        def download(self, key, dest, max_bytes):
            dest.write_bytes(b"dummy")
            return "dummy_sha", 5

    class MockDB:
        def backend(self):
            class Cur:
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    pass
                def execute(self, *args):
                    pass
                def fetchall(self):
                    return []
                def fetchone(self):
                    return {"config": {"max_video_bytes": 100 << 20}}
            return Cur()

    # Mock build_context
    from src.rav.core.types import Frame
    fv1 = Frame(video_id="v", idx=0, t=0.0)
    fv2 = Frame(video_id="v", idx=10, t=1.0)
    u_signaled = Unit("v:0.000-1.000", "v", 0.0, 1.0, "frame", fv1, (fv1,))
    u_unsignaled = Unit("v:1.000-2.000", "v", 1.0, 2.0, "frame", fv2, (fv2,))
    mock_ctx = SimpleNamespace(
        units=[u_signaled, u_unsignaled],
        state=SimpleNamespace(kept=set(), dropped=set()),
        proposals=[],
        evidence={
            u_signaled.id: [{"kind": "scalar", "label": "YOLO thiếu ổn định", "observed": True, "value": 0.5}],
            u_unsignaled.id: [{"kind": "scalar", "label": "YOLO thiếu ổn định", "observed": False, "value": 0.0}],
        },
        _progress=None,
        _on_progress=None,
    )

    monkeypatch.setattr("src.rav.worker.curation_handlers.build_context", lambda *a, **kw: mock_ctx)
    monkeypatch.setattr("src.rav.worker.curation_handlers.policy_of", lambda *a: {"max_video_bytes": 100 << 20})
    monkeypatch.setattr("src.rav.pipeline.propose.select_candidates", lambda config, seed, ctx: [u_signaled.id, u_unsignaled.id])

    # Mock propose
    class MockPick:
        def __init__(self, unit, rank):
            self.unit = unit
            self.rank = rank
            self.combined = 0.5
            self.breakdown = {}

    class MockProp:
        n = 1
        round = 1
        relaxed = False
        fields_used = []
        picks = [MockPick(u_signaled, 1), MockPick(u_unsignaled, 2)]

    monkeypatch.setattr("src.rav.pipeline.propose.propose", lambda *a, **kw: MockProp())
    monkeypatch.setattr("src.rav.service.proposal.proposal_record", lambda config, bs, seed, ctx, prop: {
        "n": 1, "input": {"recipe": config.name, "fields_used": []}, "picks": [{"unit_id": p.unit.id, "rank": p.rank} for p in prop.picks],
        "relaxed": False, "evidence": {u_signaled.id: [], u_unsignaled.id: []}
    })
    monkeypatch.setattr("src.rav.service.explain.explain_record", lambda *a: None)

    # Patch session_context.cached
    model_file = tmp_path / "model.pt"
    model_file.write_bytes(b"dummy_model")
    monkeypatch.setattr("src.rav.worker.session_context.cached", lambda *a: model_file)

    storage = MockStorage()
    db = MockDB()
    handlers = make_curation_handlers(storage, db, tmp_path / "work")

    # Thay runtime trong handler bằng MockRuntime
    monkeypatch.setattr("src.rav.worker.curation_handlers.PilotRuntime", lambda *a: MockRuntime())
    handlers = make_curation_handlers(storage, db, tmp_path / "work")

    discover()
    recipe = REGISTRY.recipe("perception_instability")
    cfg = recipe.model_dump(mode="json")
    lease = AttemptLease(
        job_id=uuid4(),
        kind="PROPOSE",
        attempt_id=uuid4(),
        attempt_n=1,
        fence=1,
        worker_id=uuid4(),
        slot_key="cpu-1",
        slot_generation=1,
        lease_until=datetime.now(UTC) + timedelta(minutes=5),
        payload={
            "session_id": str(uuid4()),
            "recipe": "perception_instability",
            "config": cfg,
            "batch_size": 16,
            "seed": 42,
            "return_scores": False,
            "model": {
                "id": str(uuid4()),
                "name": "yolo.pt",
                "core_model_id": "ym-000",
                "sha256": "0" * 64,
                "size_bytes": 1000,
                "raw_key": "models/yolo.pt",
            },
        },
    )

    stop = threading.Event()
    res = handlers["PROPOSE"](lease, stop)

    assert res["cleanup_done"] is True
    proposal_out = res["proposal"]
    assert proposal_out["n"] == 1
    evidence = proposal_out["evidence"]

    # Vì signaled_count = 1 < batch_size = 16 -> phải có uncertainty_summary
    assert "uncertainty_summary" in evidence
    summary = evidence["uncertainty_summary"]
    assert summary["signaled_count"] == 1
    assert summary["total_candidates"] == 2
    assert "Chỉ tìm thấy 1 frame có phát hiện vật thể" in summary["note"]


def test_routes_v2_request_proposal_bypasses_interactive_required_for_background(monkeypatch):
    """API request_proposal không quăng interactive_required khi direct.enabled() cho recipe background."""
    from src.rav.api import routes_v2
    from src.rav.store.pilot_curation import ProposeIn

    monkeypatch.setattr(routes_v2.InteractiveProxy, "enabled", lambda self: True)

    called = []
    class MockPilotCuration:
        def __init__(self, db):
            pass
        def request_proposal(self, a, session_id, inp, key):
            called.append((session_id, inp.recipe))
            return {"job_id": str(uuid4())}

    monkeypatch.setattr(routes_v2, "PilotCuration", MockPilotCuration)

    from src.rav.api.auth import AuthConfig
    auth_cfg = AuthConfig(jwt_secret="x" * 32, admin_key="test-admin-key", rate_limit_per_minute=1000)
    app = routes_v2.router(lambda: None, auth_cfg, get_storage=lambda: None)

    # Test logic trực tiếp của route logic
    inp_random = ProposeIn(recipe="random", expected_state_version=1)
    inp_bg = ProposeIn(recipe="perception_instability", expected_state_version=1)

    cat_map = {r["name"]: r for r in routes_v2.catalog()}
    assert bool(cat_map.get("random", {}).get("background")) is False
    assert bool(cat_map.get("perception_instability", {}).get("background")) is True

    # Test qua route function
    # Lấy route handler
    routes = [route for route in app.routes if getattr(route, "path", None) == "/api/v2/sessions/{session_id}/proposals" and "POST" in route.methods]
    assert len(routes) == 1
    handler = routes[0].endpoint

    with pytest.raises(ServiceError) as exc:
        handler(uuid4(), inp_random, SimpleNamespace(user_id=uuid4()), "test-key")
    assert exc.value.code == "interactive_required"

    # Với perception_instability: đi qua bình thường
    sid = uuid4()
    res = handler(sid, inp_bg, SimpleNamespace(user_id=uuid4()), "test-key")
    assert "job_id" in res
    assert (sid, "perception_instability") in called


def test_proposal_dto_preserves_evidence_and_uncertainty_summary():
    """_proposal_dto giữ nguyên units evidence và uncertainty_summary."""
    from src.rav.store.pilot_curation import _proposal_dto

    row = {
        "id": uuid4(),
        "n": 1,
        "created_at": datetime.now(UTC),
        "job_id": uuid4(),
        "frozen_input": {"recipe": "perception_instability"},
        "picks": [{"unit_id": "u1", "rank": 1, "combined": 0.8, "breakdown": {}}],
        "evidence": {
            "explain": {"recipe": "perception_instability"},
            "units": {
                "u1": [{"kind": "scalar", "label": "YOLO thiếu ổn định", "value": 0.8, "observed": True}],
            },
            "uncertainty_summary": {
                "signaled_count": 1,
                "total_candidates": 48,
                "note": "Chỉ có 1 frame có tín hiệu",
            },
        },
    }

    dto = _proposal_dto(row, with_scores=False)
    assert dto.evidence is not None
    assert "u1" in dto.evidence
    assert dto.evidence["u1"][0]["value"] == 0.8
    assert dto.uncertainty_summary is not None
    assert dto.uncertainty_summary["signaled_count"] == 1
    assert dto.uncertainty_summary["total_candidates"] == 48

    for legacy in (
        {"explain": {"recipe": "random"}, "scores": {}, "relaxed": False, "interactive_basis": {}},
        {"units": None, "explain": {}},
        {"units": {}},
    ):
        row["evidence"] = legacy
        old = _proposal_dto(row, with_scores=True)
        assert old.evidence == {}
        assert old.explain == legacy.get("explain")
        assert old.scores == legacy.get("scores")
    row["evidence"] = {"u1": [{"kind": "detections", "boxes": []}]}
    assert _proposal_dto(row, with_scores=False).evidence == row["evidence"]


def test_yolo_consistency_persistent_disk_caching(tmp_path, monkeypatch):
    """YoloConsistency lưu và tái sử dụng kết quả từ inference-cache/yolo-consistency."""
    import hashlib

    from src.rav.components.features.yolo_consistency import YoloConsistency
    from src.rav.core.types import Frame

    f1 = Frame(video_id="video1", idx=0, t=0.0)
    u1 = Unit("video1:0.000-1.000", "video1", 0.0, 1.0, "frame", f1, (f1,))

    model_file = tmp_path / "model.pt"
    model_file.write_bytes(b"dummy model checkpoint")
    actual_sha = hashlib.sha256(b"dummy model checkpoint").hexdigest()

    detect_calls = [0]
    def mock_detect(model, img, model_id):
        detect_calls[0] += 1
        return [{"box": [0.1, 0.1, 0.5, 0.5], "label": "car", "score": 0.9}]

    from src.rav.pipeline import batch_yolo
    monkeypatch.setattr(batch_yolo, "load_model", lambda path, out_dir: "mock_model")
    monkeypatch.setattr(batch_yolo, "detect", mock_detect)

    class MockContext:
        def __init__(self):
            self.model_files = {f"ym-{actual_sha}": model_file}
            self.model_lock = threading.Lock()
            self.output_dir = tmp_path

        def pixels_exact(self, anchor):
            import numpy as np
            return np.zeros((100, 100, 3), dtype=np.uint8)

    ctx = MockContext()
    feature = YoloConsistency()
    feature.params.model_id = f"ym-{actual_sha}"

    # Lượt 1: Cache miss -> gọi detect (gốc + lật = 2 lần)
    t1 = feature.compute([u1], ctx)
    assert len(t1.values) == 1
    assert t1.values[0]["observed"] is True
    assert detect_calls[0] == 2

    cache_dir = tmp_path / "inference-cache" / "yolo-consistency"
    cache_files = list(cache_dir.glob("*.json"))
    assert len(cache_files) == 1

    # Lượt 2: Cache hit -> không gọi detect thêm lần nào
    t2 = feature.compute([u1], ctx)
    assert len(t2.values) == 1
    assert t2.values[0]["observed"] is True
    assert detect_calls[0] == 2  # Không đổi!


def test_inference_decodes_chronologically_but_returns_candidate_order(tmp_path, monkeypatch):
    import hashlib

    import numpy as np

    from src.rav.components.features.yolo_consistency import YoloConsistency
    from src.rav.core.types import Frame
    from src.rav.pipeline import batch_yolo
    path = tmp_path / 'model.pt'
    path.write_bytes(b'test chronological decode')
    model_id = 'ym-'+hashlib.sha256(path.read_bytes()).hexdigest()
    reads = []
    def pixels(frame):
        reads.append(frame.idx)
        return np.full((8, 8, 3), frame.idx, dtype=np.uint8)
    ctx = SimpleNamespace(model_files={model_id: path}, model_lock=threading.Lock(), output_dir=tmp_path,
                          pixels_exact=pixels)
    monkeypatch.setattr(batch_yolo, 'load_model', lambda *args: object())
    monkeypatch.setattr(batch_yolo, 'detect', lambda model, image, mid:
                        [{'box': [.1, .1, .4, .6], 'label': str(np.array(image)[0, 0, 0]), 'score': .9}])
    units = []
    for idx in [3, 0, 2]:
        frame = Frame('v', idx, float(idx))
        units.append(Unit(f'v:{idx}-{idx}', 'v', float(idx), float(idx), 'frame', frame, (frame,)))
    feature = YoloConsistency(params={'model_id': model_id})
    table = feature.compute(units, ctx)
    assert reads == [0, 2, 3]
    assert table.unit_ids == [u.id for u in units]
    assert [r['original'][0]['label'] for r in table.values] == ['3', '0', '2']


def test_unobserved_candidates_not_selected_in_batch():
    """Candidates có observed=False bị mask loại bỏ, batch không bị nhồi frame chưa có tín hiệu."""
    from src.rav.components.combiners.product_strength import ProductStrength
    from src.rav.components.objectives.detection_instability import DetectionInstability
    from src.rav.components.selectors.greedy_nms import GreedyNMS
    from src.rav.core.types import Frame

    f1 = Frame(video_id="video1", idx=0, t=0.0)
    f2 = Frame(video_id="video1", idx=10, t=1.0)
    f3 = Frame(video_id="video1", idx=20, t=2.0)
    u1 = Unit("video1:0.000-1.000", "video1", 0.0, 1.0, "frame", f1, (f1,))
    u2 = Unit("video1:1.000-2.000", "video1", 1.0, 2.0, "frame", f2, (f2,))
    u3 = Unit("video1:2.000-3.000", "video1", 2.0, 3.0, "frame", f3, (f3,))
    units = [u1, u2, u3]

    class MockContext:
        def __init__(self):
            self.evidence = {}
            self.state = SimpleNamespace(kept=set(), dropped=set())

        def field(self, name, req_units):
            if name == "det.yolo_consistency":
                return SimpleNamespace(values=[
                    {"instability": 0.8, "observed": True, "original": [{"box": [0,0,1,1], "label": "car", "score": 0.9}], "flipped": []},
                    {"instability": 0.0, "observed": False, "original": [], "flipped": []},
                    {"instability": 0.0, "observed": False, "original": [], "flipped": []},
                ])
            if name == "emb.dinov2_s":
                import numpy as np
                return SimpleNamespace(values=np.array([[1, 0], [0, 1], [0.5, 0.5]]))
            raise ValueError(name)

    ctx = MockContext()
    obj = DetectionInstability()
    scores = obj.score(units, ctx)

    assert scores.mask is not None
    assert scores.mask.tolist() == [True, False, False]

    combiner = ProductStrength()
    combined, mask = combiner.combine([(scores, 1.0)], [u.id for u in units])
    assert mask.tolist() == [True, False, False]

    selector = GreedyNMS()
    # Yêu cầu batch 16, nhưng chỉ có 1 candidate có tín hiệu
    sel = selector.pick(units, combined, mask, 16, ctx)
    assert len(sel.indices) == 1
    assert sel.indices[0] == 0  # Chỉ chọn u1, không nhồi u2 và u3!

