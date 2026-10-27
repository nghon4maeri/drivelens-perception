"""Local latency probes/report and controlled DB-wait model for PROPOSE publication.

Input: local test PostgreSQL URL, optional live trace/simulation samples, cached CLIP checkpoint.
Output: JSON under data/output; no production writes, credentials, downloads or deployment.
Run after use_project_storage.ps1: python scripts/benchmark_proposal_latency.py --output output/proposal-latency/report.json.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def summary(values):
    rows = sorted(values)
    if not rows:
        return {"n": 0}
    return {"n": len(rows), "mean_ms": statistics.mean(rows), "p50_ms": statistics.median(rows),
            "p95_ms": rows[max(0, math.ceil(.95 * len(rows)) - 1)], "max_ms": max(rows)}


class DBWaitModel:
    """Add controlled waits at application synchronization points, not a wire-protocol emulator."""

    def __init__(self, rtt_ms):
        if rtt_ms < 0:
            raise ValueError("rtt_ms must be nonnegative")
        self.rtt_ms = rtt_ms
        self.points = 0
        self.sleep_ms = 0.0
        self.counts = {}

    def wait(self):
        start = time.perf_counter()
        time.sleep(self.rtt_ms / 1000)
        self.sleep_ms += (time.perf_counter() - start) * 1000
        self.points += 1

    def wrap(self, original):
        # local-only: one modeled RTT per synchronous execute/pipeline group, two per top-level transaction.
        @contextmanager
        def timing(stage, **kwargs):
            self.counts[stage] = self.counts.get(stage, 0) + 1
            if stage in ("db.execute", "db.pipeline", "db.transaction"):
                self.wait()
            with original(stage, **kwargs):
                yield
            if stage == "db.transaction":
                self.wait()
        return timing


def probe_db(url, n):
    import psycopg

    if urlsplit(url).hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("DB probe accepts local test PostgreSQL only")
    values = []
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("SELECT 1")   # connection/auth/TLS initialization is not part of a warm query
        for _ in range(n):
            start = time.perf_counter()
            conn.execute("SELECT 1").fetchone()
            values.append((time.perf_counter() - start) * 1000)
    return {"scope": "local warm SELECT 1, includes SQL/protocol/driver, not pure network RTT", **summary(values)}


def probe_clip(n):
    # Offline cached weights only; model costs vary by machine/thread count and query cache.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["OMP_NUM_THREADS"] = os.environ["MKL_NUM_THREADS"] = "1"
    start = time.perf_counter()
    import torch

    from src.rav.components.features.clip_b32 import MODEL, REVISION, encode_texts

    torch.set_num_threads(1)
    query = ["a dashcam photo of cars on a street"]
    v = encode_texts(query)
    cold_ms = (time.perf_counter() - start) * 1000
    warm, hits = [], []
    for i in range(n):
        start = time.perf_counter()
        encode_texts([f"a dashcam photo of cars at crossing {i}"])
        warm.append((time.perf_counter() - start) * 1000)
        start = time.perf_counter()
        encode_texts(query)
        hits.append((time.perf_counter() - start) * 1000)
    return {"model": MODEL, "revision": REVISION, "threads": 1, "batch": 1, "shape": list(v.shape),
            "cold_import_load_first_inference_ms": cold_ms, "warm_unique_query": summary(warm),
            "text_cache_hit": summary(hits), "download": False, "machine": "local CPU"}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--db-url", default=os.environ.get("RAV_PILOT_TEST_DATABASE_URL"))
    ap.add_argument("--samples", type=Path)
    ap.add_argument("--trace", type=Path)
    ap.add_argument("--job-times", type=Path)
    ap.add_argument("--calibration-dir", type=Path)
    ap.add_argument("--model", action="store_true")
    ap.add_argument("--runs", type=int, default=20)
    args = ap.parse_args()
    if args.runs < 1 or not any(args.output.resolve().is_relative_to(ROOT / d) for d in ("data", "output")):
        ap.error("runs positive; output under data/output")
    report = {"scope": "measured local probes and controlled waits; not cloud SLA", "simulation": {}}
    if args.db_url:
        report["db"] = probe_db(args.db_url, args.runs)
    if args.model:
        report["clip_text"] = probe_clip(args.runs)
    if args.samples:
        rows = [json.loads(line) for line in args.samples.read_text().splitlines() if line.strip()]
        for key in sorted({(r["mode"], r["rtt_ms"]) for r in rows}):
            group = [r for r in rows if (r["mode"], r["rtt_ms"]) == key]
            report["simulation"][f"{key[0]}_{key[1]}ms"] = {
                "publication": summary([r["publish_ms"] for r in group]),
                "logical_wait_points": [r["wait_points"] for r in group],
                "sql_sync": [r["counts"].get("db.execute", 0) for r in group],
                "assumption": "execute/pipeline/BEGIN/COMMIT waits; excludes TLS/savepoints/lock contention"}
    if args.trace:
        trace = json.loads(args.trace.read_text())
        report["observed_trace"] = {}
        for stage in ("worker.run_handler", "worker.publish"):
            report["observed_trace"][stage] = summary([r["wall_ms"] for r in trace if r["stage"] == stage])
        report["observed_trace"]["queue"] = summary([r["queue_ms"] for r in trace if r["stage"] == "worker.claimed"])
        report["observed_trace"]["note"] = "historical live samples, nested spans are not summed"
        if args.job_times and args.samples:
            from datetime import datetime

            starts = json.loads(args.job_times.read_text())
            candidates = [r["publish_ms"] for r in rows if r["mode"] == "rpc" and r["rtt_ms"] == 260]
            if candidates:
                publish_estimate = statistics.median(candidates)
                report["flow_estimate"] = []
                for event in trace:
                    jid = event.get("job_id")
                    if event["stage"] != "worker.attempt" or jid not in starts:
                        continue
                    old = next(r["wall_ms"] for r in trace if r.get("job_id") == jid and r["stage"] == "worker.publish")
                    total = (datetime.fromisoformat(event["at"]) - datetime.fromisoformat(starts[jid])).total_seconds() * 1000
                    end = next(datetime.fromisoformat(r["at"]) for r in trace
                               if r.get("job_id") == jid and r["stage"] == "worker.publish")
                    sync = sum(r.get("job_id") == jid and r["stage"] == "db.execute"
                               and 0 <= (end - datetime.fromisoformat(r["at"])).total_seconds() * 1000 <= old
                               for r in trace)
                    report["flow_estimate"].append({"job_id": jid, "observed_job_ms": total,
                        "estimated_rpc_job_ms": total - old + publish_estimate if sync == 22 else None,
                        "eligible_assumption": "normal reserved-sufficient path" if sync == 22 else "exceptional path; no projection",
                        "scope": "replace publish only; keeps observed queue/compute/other costs; excludes FE image/poll latency"})
    if args.calibration_dir:
        report["calibration"] = {}
        for name in ("vps-db-probe", "vps-clip-probe"):
            path = args.calibration_dir / (name + ".json")
            if path.is_file():
                report["calibration"][name] = json.loads(path.read_text())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
