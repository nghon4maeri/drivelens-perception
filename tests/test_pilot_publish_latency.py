"""Opt-in PROPOSE publish A/B with real local PG and artificial application-level waits.

RAV_PUBLISH_SIM_OUTPUT=output/...jsonl enables; RTT grid/runs through RAV_PUBLISH_SIM_RTT_MS/RAV_PUBLISH_SIM_RUNS.
Deterministic fake feature inputs isolate publication, not model/retrieval quality or actual WAN packet timing.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from scripts.benchmark_proposal_latency import DBWaitModel
from src.rav.store import pilot_db, pilot_runtime
from tests.test_pilot_curation import cu as curation_fixture  # noqa: F401
from tests.test_pilot_propose_publish import accounting, ready
from tests.test_pilot_propose_publish import cu as publication_fixture  # noqa: F401

RTTS = [int(x) for x in os.environ.get("RAV_PUBLISH_SIM_RTT_MS", "0,100,260").split(",")]
RUNS = int(os.environ.get("RAV_PUBLISH_SIM_RUNS", "3"))
TARGET = os.environ.get("RAV_PUBLISH_SIM_OUTPUT")
pytestmark = pytest.mark.skipif(not TARGET, reason="opt-in controlled latency benchmark, not normal regression")


@pytest.fixture
def cu(request):
    return request.getfixturevalue("publication_fixture")


@pytest.mark.parametrize("rtt_ms", RTTS)
@pytest.mark.parametrize("mode", ["legacy", "rpc"])
@pytest.mark.parametrize("run", range(RUNS))
def test_publication_wait_model(cu, monkeypatch, rtt_ms, mode, run):
    rt, lease, outputs, usage = ready(cu)
    # Give the emulator a long lease; it is modeling network waits, not testing lease expiration.
    cu.pg.sql("UPDATE rav_pilot_job_attempt SET lease_until=now()+interval '5 minutes' WHERE id=%s", (lease.attempt_id,))
    rt._propose_rpc = mode == "rpc"   # steady-state capability already known
    wait = DBWaitModel(rtt_ms)
    monkeypatch.setattr(pilot_db, "timing", wait.wrap(pilot_db.timing))
    monkeypatch.setattr(pilot_runtime, "timing", wait.wrap(pilot_runtime.timing))
    start = time.perf_counter()
    result = rt.publish(lease, outputs, usage)
    elapsed = (time.perf_counter() - start) * 1000
    assert result.job.state == "SUCCEEDED"
    if mode == "rpc":
        assert wait.counts["db.execute"] == 1
    snapshot = accounting(cu)
    assert len(snapshot["rav_pilot_proposal"]) == 1 and len(snapshot["rav_pilot_usage_receipt"]) == 2
    assert sum(r["actual_ms"] for r in snapshot["rav_pilot_usage_receipt"]) == usage.cpu_ms
    target = Path(TARGET)
    assert target.resolve().is_relative_to(Path("output").resolve())
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"mode": mode, "rtt_ms": rtt_ms, "run": run, "publish_ms": elapsed,
                            "sleep_ms": wait.sleep_ms, "wait_points": wait.points, "counts": wait.counts}) + "\n")
