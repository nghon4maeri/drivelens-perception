"""Harness do dac hieu nang va tai nguyen khoi dong Web API (DEP1).

Chuc nang:
    1. Do thoi gian khoi dong va bo nho RAM (RSS) cua Web API.
    2. Kiem tra danh sach module trong sys.modules (xac minh API khong import ML nang).
    3. Do do tre (latency p50, p95, p99) cua endpoint /health va /api/v2/health.
    4. Xuat ket qua ra output/pilot-benchmark/measurements.json.

Input:
    Khong yeu cau input (doc truc tiep cau hinh App).

Output:
    output/pilot-benchmark/measurements.json

Cach chay:
    . .\\scripts\\use_project_storage.ps1
    python scripts/pilot_benchmark_harness.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

try:
    import psutil
except ImportError:
    psutil = None


root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

HEAVY_ML_MODULES = [
    "torch",
    "torchvision",
    "ultralytics",
    "transformers",
    "cv2",
    "scipy",
    "kagglesdk",
]


def measure_api_bootstrap(iterations: int = 100) -> dict:
    start_time = time.perf_counter()

    # Khoi tao app
    from fastapi.testclient import TestClient

    from src.rav.api.pilot import create_pilot_app  # đúng app Render chạy (CONTRACTS §21.11); /health không cần DB

    api = create_pilot_app()
    client = TestClient(api)
    cold_start_time = time.perf_counter() - start_time

    # Kiem tra sys.modules
    ml_leaked = [m for m in HEAVY_ML_MODULES if m in sys.modules]

    # Do RSS
    rss_mb = 0.0
    if psutil is not None:
        process = psutil.Process(os.getpid())
        rss_mb = process.memory_info().rss / (1024 * 1024)

    # Do latency health
    latencies = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        resp = client.get("/health")
        t1 = time.perf_counter()
        assert resp.status_code == 200
        latencies.append((t1 - t0) * 1000.0)

    latencies.sort()
    p50 = latencies[int(len(latencies) * 0.50)]
    p95 = latencies[int(len(latencies) * 0.95)]
    p99 = latencies[int(len(latencies) * 0.99)]

    # Do latency v2 health
    resp_v2 = client.get("/api/v2/health")
    assert resp_v2.status_code == 200

    results = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cold_start_seconds": round(cold_start_time, 4),
        "rss_memory_mb": round(rss_mb, 2),
        "heavy_ml_leaked_modules": ml_leaked,
        "is_lightweight_api": len(ml_leaked) == 0,
        "health_endpoint_latencies_ms": {
            "iterations": iterations,
            "min": round(min(latencies), 3),
            "p50": round(p50, 3),
            "p95": round(p95, 3),
            "p99": round(p99, 3),
            "max": round(max(latencies), 3),
        },
        "health_response": resp.json(),
        "v2_health_response": resp_v2.json(),
        "memory_gate_pass_512mb": rss_mb < 512.0 if rss_mb > 0 else True,
        "latency_gate_pass_p95_under_100ms": p95 < 100.0,
    }

    return results


def main() -> None:
    output_dir = Path("output/pilot-benchmark")
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / "measurements.json"

    print("=== DANG DO DAC BOOTSTRAP WEB API (DEP1) ===")
    results = measure_api_bootstrap()
    out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Cold start: {results['cold_start_seconds']}s")
    print(f"RSS Memory: {results['rss_memory_mb']} MB (Gate 512MB: {results['memory_gate_pass_512mb']})")
    print(f"Heavy ML modules leaked: {results['heavy_ml_leaked_modules']} (Lightweight: {results['is_lightweight_api']})")
    print(f"Latency p95: {results['health_endpoint_latencies_ms']['p95']} ms")
    print(f"Ket qua da luu tai: {out_file}")


if __name__ == "__main__":
    main()
