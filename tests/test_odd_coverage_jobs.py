"""Real PostgreSQL/moto job path: optional resource binding and frozen ODD evidence."""
import hashlib
import json
from uuid import uuid4

import numpy as np
import pytest

from src.rav.components.features import clip_b32
from src.rav.errors import ServiceError
from src.rav.store.pilot_curation import ProposeIn
from tests.test_pilot_curation import cu  # noqa: F401
from tests.test_pilot_media import actor

PARAMS = {"objects": ["truck"], "odd_conditions": ["rain", "night"], "scenarios": [],
          "min_similarity": -1, "min_margin": -2}


@pytest.mark.parametrize("with_model", [False, True])
def test_native_job_freezes_optional_model_and_reloads_batch_only_evidence(cu, monkeypatch, with_model):  # noqa: F811
    def texts(queries, **kwargs):
        x = np.zeros((len(queries), 512), np.float32)
        x[:, 0] = 1
        return x

    monkeypatch.setattr(clip_b32, "encode_texts", texts)
    model_id = None
    if with_model:
        from src.rav.pipeline import batch_yolo

        file = cu.tmp / "odd-model.pt"
        file.write_bytes(b"qa-model")
        sha = hashlib.sha256(file.read_bytes()).hexdigest()
        model_id = uuid4()
        key = cu.s3.system_model_key("yolo", sha, ".pt")
        cu.s3.put_file(key, file, sha, "application/octet-stream")
        cu.pg.sql("INSERT INTO rav_pilot_model_asset(id,core_model_id,name,raw_key,sha256,size_bytes,checkpoint_revision,"
                  "task,class_map,runtime_manifest,validation) VALUES (%s,%s,'QA',%s,%s,%s,'test','detect','{}','{}','INFERENCE_VERIFIED')",
                  (model_id, "ym-" + sha, key, sha, file.stat().st_size))
        monkeypatch.setattr(batch_yolo, "load_model", lambda *args: object())
        monkeypatch.setattr(batch_yolo, "detect", lambda *args: [{"box": [.1, .1, .4, .6], "label": "car", "confidence": .9}])
    inp = ProposeIn(recipe="odd_coverage", batch_size=3, expected_state_version=1, strategy_params=PARAMS,
                    model_id=model_id, seed=7, return_scores=True)
    job = cu.cur.request_proposal(actor(cu.a), cu.session.id, inp, "odd-1")
    again = cu.cur.request_proposal(actor(cu.a), cu.session.id, inp, "odd-1")
    assert job.id == again.id
    frozen = cu.pg.sql("SELECT payload FROM rav_pilot_job WHERE id=%s", (job.id,))[0]["payload"]
    assert frozen["config"] is None and frozen["strategy_params"]["objects"] == ["truck"]
    assert bool(frozen.get("model")) is with_model
    assert frozen['pool'][0]['video_metadata']['fps'] > 0
    if not with_model:
        # Regression: CLIP/DINO-only selection must never fetch/probe the raw video.
        monkeypatch.setattr('src.rav.worker.handlers._probe', lambda *a: pytest.fail('unnecessary raw probe'))
        download = cu.s3.download

        def feature_only(key, *args, **kwargs):
            assert key != frozen['pool'][0]['raw_key'], 'unnecessary raw S3 download'
            return download(key, *args, **kwargs)

        monkeypatch.setattr(cu.s3, 'download', feature_only)
    if with_model:
        assert frozen["model"]["sha256"] == sha
    assert cu.d.run_once()
    final = cu.d.runtime.get_job(actor(cu.a), job.id)
    assert final.state == "SUCCEEDED", final
    phases = [s['phase'] for s in final.progress['stages']]
    assert ('LOAD_RAW' in phases) is with_model
    assert ('PREPARE_METADATA' in phases) is not with_model
    assert 'LOAD_FEATURES' in phases and 'ODD_SELECT' in phases
    assert all(s['state'] == 'done' for s in final.progress['stages'])
    rows = cu.pg.sql("SELECT picks,evidence,frozen_input FROM rav_pilot_proposal WHERE session_id=%s", (cu.session.id,))
    assert len(rows) == 1
    row = rows[0]
    assert row["evidence"]["trace"][0]["step"] == "odd_gate"
    assert len(row["picks"]) > 0
    # Actual persisted read endpoint must preserve trace and project every score/evidence onto the batch.
    rec = cu.cur.get_proposal(actor(cu.a), cu.session.id, 1)
    assert rec.trace[0]["branches"][0]["query"] == "a dashcam photo of truck, rain"
    ids = {p["unit_id"] for p in rec.picks}
    assert set(rec.evidence) == ids and set(rec.scores["unit_id"]) == ids
    json.dumps(rec.model_dump(mode="json"), allow_nan=False)
    if with_model:
        assert any(t["step"] == "odd_uncertainty" for t in rec.trace)
    with pytest.raises(ServiceError):
        cu.cur.get_proposal(actor(cu.b), cu.session.id, 1)


def test_optional_model_is_still_authorized_and_verified(cu):  # noqa: F811
    with pytest.raises(ServiceError) as e:
        cu.cur.request_proposal(actor(cu.a), cu.session.id,
                                    ProposeIn(recipe="odd_coverage", strategy_params=PARAMS, batch_size=3,
                                              expected_state_version=1, model_id=uuid4()), "odd-bad-model")
    assert e.value.code == "model_not_found"


def test_proposal_phase_reset_is_durable_and_heartbeat_does_not_erase_it(cu):  # noqa: F811
    from src.rav.store.pilot_runtime import PhaseTiming, Progress

    job = cu.cur.request_proposal(actor(cu.a), cu.session.id,
        ProposeIn(recipe='odd_coverage', strategy_params=PARAMS, batch_size=3, expected_state_version=1), 'timings')
    runtime = cu.d.runtime
    lease = runtime.claim(cu.d.worker_ctx, 'CPU')
    assert lease and lease.job_id == job.id
    stage = PhaseTiming(phase='LOAD_FEATURES', started_at='2026-10-17T00:00:00Z', elapsed_ms=120,
                        state='done', done=10, total=10, cache_hit=True)
    runtime.update_progress(lease, Progress(sequence=1, phase='LOAD_FEATURES', done=10, total=10, stages=[stage]))
    runtime.update_progress(lease, Progress(sequence=2, phase='CLIP_TEXT_CACHE', done=0, total=2, stages=[stage]))
    runtime.update_progress(lease, Progress(sequence=3, phase='CLIP_TEXT_CACHE', done=1, total=2, stages=[stage]))
    with pytest.raises(ServiceError):
        runtime.update_progress(lease, Progress(sequence=4, phase='CLIP_TEXT_CACHE', done=0, total=2))
    runtime.heartbeat(lease, Progress(sequence=1, phase='working', done=0, total=0))
    reloaded = runtime.get_job(actor(cu.a), job.id)
    assert reloaded.phase == 'CLIP_TEXT_CACHE' and reloaded.progress['done'] == 1
    assert reloaded.progress['stages'] == [stage.model_dump()]


def test_context_cancellation_before_any_source_io(cu, monkeypatch):  # noqa: F811
    import threading

    from src.rav.worker.session_context import pool_context

    stop = threading.Event()
    stop.set()
    monkeypatch.setattr(cu.s3, 'download', lambda *a, **kw: pytest.fail('cancelled job fetched S3'))
    with pytest.raises(ServiceError) as e:
        pool_context(cu.s3, {}, cu.tmp / 'work', 1 << 20, stop=stop)
    assert e.value.code == 'cancel_requested'
