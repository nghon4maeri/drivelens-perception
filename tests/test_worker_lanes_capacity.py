"""Opt-in real CPU model/worker mixed load in isolated PG/Moto; never production credentials.

RAV_RUN_LANE_CAPACITY=1 with public data/models/yolo11n.pt and pinned offline CLIP cache enables.
Features are seeded deterministic artifacts; CLIP text and YOLO inference are real, no semantic-quality claim.
"""
import copy
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import psutil
import psycopg
import pytest

from src.rav.store.pilot_analysis import DetectIn, PilotAnalysis
from src.rav.store.pilot_curation import catalog
from src.rav.store.pilot_db import CreateSessionInput, PilotDB
from tests.conftest import make_video
from tests.test_pilot_analysis import YOLO, _model
from tests.test_pilot_curation import seed_features
from tests.test_pilot_frames_e2e import Api, media, upload
from tests.test_pilot_frames_e2e import e2e as frames_fixture  # noqa: F401
from tests.test_pilot_media import actor, seed_frames, seed_source, unit
from tests.test_pilot_worker_spawn import _children, _wait

pytestmark = pytest.mark.skipif(os.environ.get("RAV_RUN_LANE_CAPACITY") != "1" or not YOLO.exists(),
                               reason="opt-in isolated VPS capacity test with public model caches")


@pytest.fixture(autouse=True)
def split(monkeypatch):
    monkeypatch.setenv("RAV_FRAMES_E2E_CPU_LANES", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")


@pytest.fixture
def stack(request):
    return request.getfixturevalue("frames_fixture")


def test_models_and_bulk_overlap_with_media(stack):
    pg, s3, a, b = stack["pg"], stack["s3"], stack["a"], stack["b"]
    db = PilotDB(pg.api_url)
    ana = PilotAnalysis(db)
    path = make_video(stack["tmp"] / "capacity-source.mp4", seconds=21, fps=20, size=(640, 360))
    pools, sessions = [], []
    for owner in (a, b):
        sid, core, frames = seed_source(pg, s3, path, owner)
        seed_frames(pg, s3, sid, owner, path, frames)
        seed_features(pg, s3, sid, core, path, owner, ("DINO", "CLIP"))
        sessions.append(db.create_session(actor(owner), CreateSessionInput(name="isolated capacity", goal="test", target_k=100,
                                                                          pool_source_ids=[sid]), str(owner)))
        pools.append((sid, core, frames))
    mid = _model(SimpleNamespace(pg=pg, s3=s3))
    c = pg.profile(policy_id=pg.sql("SELECT policy_id FROM rav_pilot_profile WHERE user_id=%s", (a,))[0]["policy_id"])
    api_a, api_b, api_c = (Api(stack["base"], uid) for uid in (a, b, c))
    peaks = {name: 0 for name in ("TEXT", "YOLO", "BULK", "MEDIA")}
    max_total = 0
    stop_monitor = threading.Event()
    monitor_errors = []
    peak_cpu_cores = 0.0

    def monitor():
        nonlocal max_total, peak_cpu_cores
        previous, last_time = {}, time.monotonic()
        while not stop_monitor.wait(.1):
            try:
                children = _children(stack["wd"])
                total = psutil.Process().memory_info().rss
                cpu_delta = 0.0
                now = time.monotonic()
                for name, child in children.items():
                    proc = psutil.Process(child["pid"])
                    rss = proc.memory_info().rss
                    peaks[name] = max(peaks.get(name, 0), rss)
                    total += rss
                    times = proc.cpu_times()
                    cpu = times.user+times.system
                    cpu_delta += max(0, cpu-previous.get(child["pid"], cpu))
                    previous[child["pid"]] = cpu
                peak_cpu_cores = max(peak_cpu_cores, cpu_delta/max(.001, now-last_time))
                last_time = now
                max_total = max(max_total, total)
            except psutil.NoSuchProcess:
                continue
            except Exception as exc:
                monitor_errors.append(type(exc).__name__)
    sampler = threading.Thread(target=monitor, daemon=True)
    sampler.start()
    records = []

    def done(api, job):
        return _wait(lambda: (r := api.ok("GET", f"/jobs/{job}"))["state"] in ("SUCCEEDED", "FAILED") and r,
                     180, "capacity job terminal")

    cfg = copy.deepcopy(next(r["config"] for r in catalog() if r["name"] == "text_diversity"))
    try:
        for run in range(3):
            # New B proposal ensures each YOLO round is an actual job, not evidence-cache replay.
            base = api_b.ok("POST", f"/sessions/{sessions[1].id}/proposals",
                            {"recipe": "random", "batch_size": 16, "seed": run, "expected_state_version": 1}, key=f"base-{run}")
            assert done(api_b, base["id"])["state"] == "SUCCEEDED"
            for obj in cfg["objectives"]:
                if obj["type"] == "text_match":
                    obj.setdefault("params", {}).update(texts=[f"cars on a road near crossing {run}"], min_similarity=-1)
            bulk = make_video(stack["tmp"] / f"bulk-{run}.mp4", seconds=90, fps=20, size=(640, 360), shift=run+10)
            with ThreadPoolExecutor(1) as pool:
                # Queue the three jobs before releasing dispatch; all handlers remain real.
                with psycopg.connect(pg.admin_url) as gate:
                    gate.execute("SELECT queue FROM rav_pilot_dispatch_cursor WHERE queue='CPU' FOR UPDATE")
                    future = pool.submit(upload, api_c, bulk.read_bytes(), f"bulk-{run}.mp4")
                    started = time.perf_counter()
                    text = api_a.ok("POST", f"/sessions/{sessions[0].id}/proposals",
                                   {"recipe": "text_diversity", "config": cfg, "batch_size": 16, "seed": run,
                                    "expected_state_version": 1}, key=f"text-{run}")
                    yolo, status = ana.request_detections(actor(b), sessions[1].id, run+1, DetectIn(model_id=mid), f"yolo-{run}")
                    assert status == 202
                    _wait(lambda: pg.sql("SELECT count(*) AS n FROM rav_pilot_job WHERE owner_id=%s AND kind='VALIDATE_UPLOAD' AND state='QUEUED'", (c,))[0]["n"] == 1,
                          30, "bulk queued before dispatch release")
                overlap = False
                deadline = time.monotonic()+150
                while time.monotonic()<deadline:
                    states = pg.sql("SELECT kind,state FROM rav_pilot_job WHERE state='RUNNING'")
                    kinds = {r["kind"] for r in states}
                    if {"PROPOSE", "YOLO_BATCH", "VALIDATE_UPLOAD"} <= kinds:
                        overlap = True
                        break
                    if run > 0 and not any(r["kind"] == "PROPOSE" for r in states):
                        text_state = api_a.ok("GET", f"/jobs/{text['id']}")["state"]
                        if text_state in ("SUCCEEDED", "FAILED"):
                            break
                    time.sleep(.01)
                if run == 0:
                    assert overlap, "three real kinds did not overlap during cold load"
                sid, core, frames = pools[0]
                items = [{"source_id": str(sid), "unit_id": unit(core, f), "variant": "thumbnail"} for f in frames[:16]]
                t = time.perf_counter()
                batch = api_a.ok("POST", "/media/batch", {"items": items})
                assert len(batch) == 16 and all(m["state"] == "READY" for m in batch)
                batch_ms = (time.perf_counter()-t)*1000
                clip, clip_ms = media(api_a, str(sid), unit(core, frames[run*4]), "context")
                assert clip["state"] == "READY" and clip_ms < 10_000
                text_done = done(api_a, text["id"])
                text_ms = (time.perf_counter()-started)*1000
                yolo_done = done(api_b, yolo.job.id)
                uploaded, bulk_ms = future.result(timeout=180)
                assert text_done["state"] == yolo_done["state"] == "SUCCEEDED", (text_done, yolo_done)
                assert uploaded["state"] == "READY"
            records.append({"run": run, "cold": run == 0, "text_until_observed_ms": text_ms,
                            "bulk_ms": bulk_ms, "batch16_ms": batch_ms, "context_ms": clip_ms, "three_real_kinds_overlap": overlap})
        assert all(r["state"] == "FREE" for r in pg.sql("SELECT state FROM rav_pilot_resource_slot"))
        result = {"scope": "isolated PG/Moto + real model CLIP text/YOLO, synthetic videos/seeded embeddings; no PII/SLA/quality claim",
                  "runs": records, "peak_rss_bytes_by_lane": peaks, "peak_test_parent_and_children_rss_bytes": max_total,
                  "peak_cpu_cores": peak_cpu_cores, "children": list(_children(stack["wd"]))}
        assert not monitor_errors, monitor_errors
        target = Path("output/claim-lanes/capacity.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, indent=2))
    finally:
        stop_monitor.set()
        sampler.join(2)
        db.close()
