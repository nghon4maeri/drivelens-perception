"""DEP5b2 YOLO trên đợt + phân tích (CONTRACTS §17, §21.17, #97): PostgreSQL thật + S3 moto + worker YOLO_BATCH/METRICS thật.

YOLO dùng checkpoint thật `data/models/yolo11n.pt` (thiếu → bỏ qua test YOLO, có lý do). Phân tích so với `service/metrics.metrics_record`
trên Context local dựng từ cùng đặc trưng/state (parity).
Chạy: đặt `RAV_PILOT_TEST_DATABASE_URL` (tests/conftest.py) rồi `python -m pytest tests/test_pilot_analysis.py -q`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from src.rav.errors import ServiceError
from src.rav.store.pilot_analysis import AnalysisIn, DetectIn, MetricIn, PilotAnalysis
from src.rav.store.pilot_db import DEFAULT_SAMPLE_SPEC, PilotDB
from src.rav.worker.analysis_handlers import make_analysis_handlers
from tests.test_pilot_curation import SPEC, _ask, _decide, cu  # noqa: F401  (fixture cu)
from tests.test_pilot_media import actor

ROOT = Path(__file__).resolve().parents[1]
YOLO = ROOT / "data/models/yolo11n.pt"


@pytest.fixture
def an(request):
    c = request.getfixturevalue("cu")   # fixture của test_pilot_curation (đề xuất trên worker)
    for kind, fn in make_analysis_handlers(c.s3, PilotDB(c.pg.worker_url), c.tmp / "work").items():
        c.d.register_handler(kind, fn)
    c.ana = PilotAnalysis(c.api)
    # These are functional evidence/analysis tests: cold ML imports can exceed
    # the shared toy quota of 10s CPU. Quota enforcement has dedicated tests.
    c.pg.bonus(c.a, c.pg.profile(policy_id=c.policy), 'CPU', 120_000)
    _ask(c, "diversity", key="base", batch=6, seed=3)
    assert c.d.run_once()
    return c


def _model(an, validation="INFERENCE_VERIFIED"):
    sha = hashlib.sha256(YOLO.read_bytes()).hexdigest()
    key = f"pilot-test/system/models/yolo/{sha}.pt"
    an.s3.put_file(key, YOLO, sha, "application/octet-stream")
    mid = uuid4()
    an.pg.sql("INSERT INTO rav_pilot_model_asset (id, core_model_id, name, raw_key, sha256, size_bytes, checkpoint_revision, task, class_map, "
              "runtime_manifest, validation) VALUES (%s, %s, 'YOLO11n COCO', %s, %s, %s, 'ultralytics-8.3.228:yolo11n.pt', 'detect', '{}', %s, %s)",
              (mid, f"ym-{sha}-{mid.hex[:4]}", key, sha, YOLO.stat().st_size,
               json.dumps({"engine_version": "8.3.228", "params": {"imgsz": 640}}), validation))
    return mid


@pytest.mark.skipif(not YOLO.exists(), reason="cần data/models/yolo11n.pt (quyết định #97) để chạy YOLO thật")
def test_yolo_on_proposal_publishes_evidence_once(an):
    mid = _model(an)
    assert [m.id for m in an.ana.list_models(actor(an.a))] == [mid]
    dto, status = an.ana.request_detections(actor(an.a), an.session.id, 1, DetectIn(model_id=mid), "y1")
    assert status == 202 and dto.job.kind == "YOLO_BATCH"
    assert an.ana.request_detections(actor(an.a), an.session.id, 1, DetectIn(model_id=mid), "y1")[0].job.id == dto.job.id
    assert an.d.run_once()
    job = an.pg.sql("SELECT state, error FROM rav_pilot_job WHERE id = %s", (dto.job.id,))[0]
    assert job["state"] == "SUCCEEDED", job["error"]
    ev = an.ana.get_detections(actor(an.a), an.session.id, 1, mid)
    picks = an.cur.get_proposal(actor(an.a), an.session.id, 1).picks
    assert [i["unit_id"] for i in ev["items"]] == [p["unit_id"] for p in picks] and ev["total_images"] == len(picks)
    assert ev["engine_version"] == "8.3.228" and all(0 <= b <= 1 for i in ev["items"] for d in i["detections"] for b in d["box"])
    again, status = an.ana.request_detections(actor(an.a), an.session.id, 1, DetectIn(model_id=mid), "y2")
    assert status == 200 and again.job is None and again.evidence["items"] == ev["items"]   # cùng model/runtime → không chạy lại
    with pytest.raises(ServiceError) as e:
        an.ana.get_detections(actor(an.b), an.session.id, 1, mid)
    assert e.value.status == 404
    # Proposal/state không đổi vì YOLO
    assert an.api.get_review_state(actor(an.a), an.session.id).state_version == 1


@pytest.mark.skipif(not YOLO.exists(), reason="cần data/models/yolo11n.pt")
def test_yolo_requires_verified_model_and_owner(an):
    bad = _model(an, "CONTAINER_ONLY")
    with pytest.raises(ServiceError) as e:
        an.ana.request_detections(actor(an.a), an.session.id, 1, DetectIn(model_id=bad), "y3")
    assert e.value.code == "model_not_verified"
    good = _model(an)
    with pytest.raises(ServiceError) as e:
        an.ana.request_detections(actor(an.b), an.session.id, 1, DetectIn(model_id=good), "y4")
    assert e.value.status == 404
    with pytest.raises(ServiceError) as e:
        an.ana.request_detections(actor(an.a), an.session.id, 9, DetectIn(model_id=good), "y5")
    assert e.value.status == 404


def _local_metrics(an, kept, dropped):
    """Kỳ vọng: cùng code `metrics_record` trên Context local dựng từ cùng đặc trưng + state + proposal."""
    from src.rav.core.types import ReviewState
    from src.rav.pipeline import prepare as p1
    from src.rav.service.metrics import metrics_record
    from src.rav.service.proposal import to_proposals
    from src.rav.store.field_store import FieldStore
    from src.rav.worker.handlers import _probe

    store = FieldStore(an.tmp / f"local-{uuid4().hex[:6]}")
    for t in an.tables:
        store.put(t, (an.core, DEFAULT_SAMPLE_SPEC["spec_hash"]))
    ctx = p1.make_context([_probe(an.path)], {"source": None, **SPEC}, store)
    ctx.state = ReviewState(set(kept), set(dropped))
    props = an.cur.list_proposals(actor(an.a), an.session.id)
    ctx.proposals = to_proposals("x", [{"n": p.n, "round": p.n, "input": p.input, "picks": p.picks, "relaxed": p.relaxed} for p in props], ctx.units)
    return metrics_record(ctx, None, "kept", 50)


def test_analysis_default_parity_views_and_staleness(an):
    ids = [p["unit_id"] for p in an.cur.get_proposal(actor(an.a), an.session.id, 1).picks]
    _decide(an, [(ids[0], "keep"), (ids[1], "keep"), (ids[2], "drop")])
    sv = an.api.get_review_state(actor(an.a), an.session.id).state_version
    req, status = an.ana.request_analysis(actor(an.a), an.session.id, AnalysisIn(expected_state_version=sv), "m1")
    assert status == 202 and req.job.kind == "METRICS"
    with pytest.raises(ServiceError) as e:
        an.ana.request_analysis(actor(an.a), an.session.id, AnalysisIn(expected_state_version=sv), "m2")
    assert e.value.code == "analysis_in_progress"
    with pytest.raises(ServiceError) as e:
        an.ana.get_analysis(actor(an.a), an.session.id)
    assert e.value.status == 404                       # GET không tự tính
    assert an.d.run_once()
    job = an.pg.sql("SELECT state, error FROM rav_pilot_job WHERE id = %s", (req.job.id,))[0]
    assert job["state"] == "SUCCEEDED", job["error"]
    got = an.ana.get_analysis(actor(an.a), an.session.id)
    assert got.current and got.decision_seq == 3 and got.proposal_count == 1
    want = _local_metrics(an, ids[:2], ids[2:3])
    res = {r["type"]: r["values"] for r in got.results["results"]}
    for r in want["results"]:
        if r["type"] == "review_time":
            continue
        for k, v in r["values"].items():
            assert res[r["type"]][k] == pytest.approx(v, abs=1e-6), (r["type"], k)
    assert got.results["progress"] == {"target_k": 50, "kept": 2, "dropped": 1, "proposals": 1}
    views = got.results["views"]
    assert set(views) == {"embedding_map", "timeline", "duplicate_pairs"}
    assert len(views["embedding_map"]["data"]["unit_id"]) == len(views["embedding_map"]["data"]["x"]) > 0
    assert np.isfinite(views["embedding_map"]["data"]["x"]).all()
    # Cùng input + ngữ cảnh → trả luôn, không job mới; quyết định mới → kết quả cũ không còn current.
    again, status = an.ana.request_analysis(actor(an.a), an.session.id, AnalysisIn(expected_state_version=sv), "m3")
    assert status == 200 and again.analysis.id == got.id
    _decide(an, [(ids[3], "keep")])
    assert not an.ana.get_analysis(actor(an.a), an.session.id).current
    replay, status = an.ana.request_analysis(actor(an.a), an.session.id, AnalysisIn(expected_state_version=sv), "m1")
    assert status == 200 and replay.analysis.id == got.id and not replay.analysis.current
    with pytest.raises(ServiceError):
        an.ana.get_analysis(actor(an.b), an.session.id)
    assert an.cur.job_result(actor(an.a), req.job.id)["analysis_id"] == str(got.id)


def test_analysis_custom_metric_and_errors(an):
    custom = AnalysisIn(expected_state_version=1, metrics=[MetricIn(type="nd_rate_at_k", params={"threshold": 8})], on=1)
    req, _ = an.ana.request_analysis(actor(an.a), an.session.id, custom, "c1")
    assert an.d.run_once()
    res = an.ana.get_analysis(actor(an.a), an.session.id, json.loads(json.dumps(an.pg.sql(
        "SELECT payload->>'input_hash' AS h FROM rav_pilot_job WHERE id = %s", (req.job.id,))[0]["h"])))
    assert [r["type"] for r in res.results["results"]] == ["nd_rate_at_k"] and res.results["on"] == 1 and res.results["views"] == {}
    bad = AnalysisIn(expected_state_version=1, metrics=[MetricIn(type="khong_co")])
    req2, _ = an.ana.request_analysis(actor(an.a), an.session.id, bad, "c2")
    assert an.d.run_once()
    row = an.pg.sql("SELECT state, error FROM rav_pilot_job WHERE id = %s", (req2.job.id,))[0]
    assert row["state"] == "FAILED" and row["error"]["code"] == "metric_not_found"
    with pytest.raises(ServiceError) as e:
        an.ana.request_analysis(actor(an.a), an.session.id, AnalysisIn(expected_state_version=1, on=5), "c3")
    assert e.value.status == 404
    with pytest.raises(ServiceError) as e:
        an.ana.request_analysis(actor(an.a), an.session.id, AnalysisIn(expected_state_version=7), "c4")
    assert e.value.code == "state_version_conflict"
