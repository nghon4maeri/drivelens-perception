"""End-to-end local đúng luồng pilot cho ảnh lưu sẵn (DEP7-Frames F4, CONTRACTS §21.20) — không cloud.

API pilot thật qua HTTP (uvicorn, JWT ES256), worker thật (`run` + process con spawn CPU/MEDIA), S3 moto server, PostgreSQL thật.
Luồng: upload → ảnh lưu sẵn → lưới thumbnail/frame/clip (cold/warm) → cô lập A/B → nguồn cũ chuẩn bị ảnh với process CPU bị giết giữa job
→ nguồn SYSTEM qua CLI operator → xóa nguồn → kiểm receipt/dung lượng/slot. Ghi số đo vào `output/frames-bench/e2e-local-*.json`.
Chạy: đặt `RAV_PILOT_TEST_DATABASE_URL`; `RAV_FRAMES_E2E_RUNS=<n>` lặp phần upload/lưới/clip để lấy p50/p95; `RAV_BENCH_VIDEO=<mp4>` dùng video thật.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import signal
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import psutil
import pytest
import requests

from src.rav.worker import __main__ as entry
from tests.conftest import POLICY_CONFIG, make_video
from tests.test_pilot_api_v2 import ISS, JWK, auth
from tests.test_pilot_frames import expected_jpegs
from tests.test_pilot_media import seed_source, unit
from tests.test_pilot_worker_spawn import _children, _free_port, _wait


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, max(0, round(p / 100 * (len(xs) - 1))))], 1)


def summary(xs: list[float]) -> dict:
    return {"n": len(xs), "p50_ms": pct(xs, 50), "p95_ms": pct(xs, 95), "max_ms": round(max(xs), 1)} if xs else {"n": 0}


@pytest.fixture
def e2e(pg, monkeypatch, tmp_path):
    import boto3
    import uvicorn
    from moto.server import ThreadedMotoServer

    from src.rav.api.auth import AuthConfig
    from src.rav.api.pilot import create_pilot_app
    from src.rav.store.pilot_storage import PilotStorage

    s3_port, api_port = _free_port(), _free_port()
    moto = ThreadedMotoServer(ip_address="127.0.0.1", port=s3_port)
    moto.start()
    endpoint, bucket = f"http://127.0.0.1:{s3_port}", f"rav-e2e-{uuid4().hex[:8]}"
    env = {"RAV_PILOT_WORKER_DATABASE_URL": pg.worker_url, "RAV_PILOT_HOST_BOOT_ID": str(uuid4()),
           "RAV_PILOT_WORKDIR": f"output/pytest-tmp/e2e-{uuid4().hex[:8]}", "RAV_S3_BUCKET": bucket, "RAV_PILOT_S3_PREFIX": "pilot-test",
           "AWS_ENDPOINT_URL_S3": endpoint, "AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test", "AWS_DEFAULT_REGION": "us-east-1",
           "RAV_PILOT_WORKER_POLL_S": "0.2", "RAV_PILOT_WORKER_RECONCILE_S": "1"}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    for k in ("KAGGLE_API_TOKEN", "RAV_TRACE_ENABLED", "RAV_PILOT_CACHE_MAX_BYTES"):
        monkeypatch.delenv(k, raising=False)
    client = boto3.client("s3", endpoint_url=endpoint, region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test")
    client.create_bucket(Bucket=bucket)
    s3 = PilotStorage(client, bucket, "pilot-test")
    bounds = {"frame_batch_size": 32, "frame_put_concurrency": 8, "max_frame_bytes": 1 << 30}
    profiles = dict(POLICY_CONFIG["profiles_by_kind"], VALIDATE_UPLOAD={"cpu_ms": 600_000, "gpu_ms": 0},
                    PREPARE_FRAMES={"cpu_ms": 600_000, "gpu_ms": 0})
    config = dict(POLICY_CONFIG, cpu_base_ms=100_000_000, storage_user_bytes=4 << 30, storage_global_bytes=16 << 30, profiles_by_kind=profiles,
                  lease_ms=5_000, heartbeat_ms=1_000, max_media_requests=20, pending_per_user=20,
                  chunk_bounds_by_kind=dict(POLICY_CONFIG["chunk_bounds_by_kind"], VALIDATE_UPLOAD=bounds, PREPARE_FRAMES=bounds))
    split = os.environ.get("RAV_FRAMES_E2E_CPU_LANES") == "1"
    if os.environ.get('RAV_FRAMES_E2E_CPU_FREE') == '1':
        config['cpu_accounting_enabled'] = False
    if split:
        from tests.test_worker_lanes import install, lane_config
        install(monkeypatch)
        config["slot_inventory"] = {"CPU": [row["slots"][0] for row in lane_config()], "MEDIA": ["media-1"]}
        config["worker_rss_bytes"] = 2 << 30
    policy = pg.policy(config=config)
    a, b = pg.profile(policy_id=policy), pg.profile(policy_id=policy)
    for key in config["slot_inventory"]["CPU"]:
        pg.slot(key, "CPU")
    pg.slot("media-1", "MEDIA")
    app = create_pilot_app(pg.api_url, AuthConfig(expected_issuer=ISS), raw_jwks={"keys": [JWK]})
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=api_port, log_level="warning"))
    api_thread = threading.Thread(target=server.run, daemon=True)
    api_thread.start()
    _wait(lambda: server.started, 30, "API uvicorn")
    stop, errors = threading.Event(), []

    def worker():
        try:
            entry.run(stop)
        except BaseException as exc:   # noqa: BLE001
            errors.append(exc)
    wd = Path(env["RAV_PILOT_WORKDIR"])
    worker_thread, worker_process, worker_log, worker_spawned = None, None, None, None
    spawned_stop = None
    if split and os.name == "nt":
        # local-only: SIGTERM terminates a Windows Popen without joining lanes.
        # A spawned supervisor owns its RSS budget and receives a shared stop Event.
        ctx = multiprocessing.get_context("spawn")
        spawned_stop = ctx.Event()
        worker_spawned = ctx.Process(target=entry.run, args=(spawned_stop,))
        worker_spawned.start()
    elif split:
        # Keep the supervisor's measured RSS separate from pytest/API/Moto memory.
        worker_log = (tmp_path / "worker.log").open("w")
        worker_process = subprocess.Popen([sys.executable, "-m", "src.rav.worker", "--mode", "pilot"],
                                          stdout=worker_log, stderr=subprocess.STDOUT)
    else:
        worker_thread = threading.Thread(target=worker, daemon=True)
        worker_thread.start()
    try:
        expected = {"TEXT", "YOLO", "BULK", "MEDIA"} if split else {"CPU", "MEDIA"}
        _wait(lambda: set(_children(wd)) == expected, 120, "process con worker")
        policy_version = pg.sql("SELECT version FROM rav_pilot_quota_policy WHERE id = %s", (policy,))[0]["version"]
        yield {"pg": pg, "s3": s3, "a": a, "b": b, "base": f"http://127.0.0.1:{api_port}/api/v2", "wd": wd, "tmp": tmp_path,
               "policy_version": policy_version, "errors": errors}
    finally:
        stop.set()
        if worker_spawned:
            spawned_stop.set()
            worker_spawned.join(timeout=120)
            if worker_spawned.is_alive():
                worker_spawned.terminate()
                worker_spawned.join(timeout=10)
            if worker_spawned.is_alive():
                worker_spawned.kill()
                worker_spawned.join(timeout=10)
        if worker_process:
            if worker_process.poll() is None:
                worker_process.send_signal(signal.SIGTERM)
            try:
                worker_process.wait(timeout=120)
            except subprocess.TimeoutExpired:
                worker_process.kill()
                worker_process.wait(timeout=10)
            worker_log.close()
        if worker_thread:
            worker_thread.join(timeout=120)
        server.should_exit = True
        api_thread.join(timeout=30)
        moto.stop()
    assert (worker_thread is None or not worker_thread.is_alive()) and not errors, errors
    assert worker_process is None or worker_process.returncode == 0
    assert worker_spawned is None or worker_spawned.exitcode == 0


def keys(s3, prefix: str) -> set[str]:
    """Mọi key dưới prefix (list_objects_v2 trả tối đa 1000 mỗi trang)."""
    pages = s3.client.get_paginator("list_objects_v2").paginate(Bucket=s3.bucket, Prefix=prefix)
    return {o["Key"] for page in pages for o in page.get("Contents", [])}


class Api:
    def __init__(self, base: str, uid):
        self.base, self.uid = base, uid

    def call(self, method: str, path: str, body=None, key: str | None = None) -> requests.Response:
        headers = dict(auth(self.uid))
        if key:
            headers["Idempotency-Key"] = key
        return requests.request(method, self.base + path, json=body, headers=headers, timeout=60)

    def ok(self, method: str, path: str, body=None, key: str | None = None, status=(200, 201, 202)) -> dict:
        r = self.call(method, path, body, key)
        assert r.status_code in status, (path, r.status_code, r.text)
        return r.json()


def upload(api: Api, data: bytes, name: str) -> tuple[dict, float]:
    t0 = time.perf_counter()
    created = api.ok("POST", "/uploads", {"filename": name, "declared_bytes": len(data)}, key=f"up-{uuid4()}")
    t = created["transfer"]
    r = requests.post(t["url"], data=t["fields"], files={"file": ("video", data)}, timeout=120)
    assert r.status_code in (200, 204), r.text
    api.ok("POST", f"/uploads/{created['id']}/complete")
    done = _wait(lambda: (u := api.ok("GET", f"/uploads/{created['id']}"))["state"] in ("READY", "FAILED") and u, 600, "upload READY")
    assert done["state"] == "READY", done
    return done, (time.perf_counter() - t0) * 1000


def media(api: Api, source_id: str, unit_id: str, variant: str, max_side=None) -> tuple[dict, float]:
    """Như FE: POST → PENDING thì poll GET → tải bytes từ URL đã ký; trả (DTO cuối, ms tới khi có bytes)."""
    t0 = time.perf_counter()
    body = {"source_id": source_id, "unit_id": unit_id, "variant": variant} | ({"max_side": max_side} if max_side else {})
    m = api.ok("POST", "/media/requests", body)
    while m["state"] == "PENDING":
        time.sleep(0.2)
        m = api.ok("GET", f"/media/requests/{m['id']}")
    if m["state"] == "READY":
        m["bytes"] = requests.get(m["url"], timeout=60).content
    return m, (time.perf_counter() - t0) * 1000


def grid(api: Api, source_id: str, units: list[str]) -> tuple[list[dict], list[float], float]:
    """Lưới thumbnail như FE: tối đa 4 request media đồng thời."""
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=4) as pool:
        out = list(pool.map(lambda u: media(api, source_id, u, "thumbnail"), units))
    return [m for m, _ in out], [ms for _, ms in out], (time.perf_counter() - t0) * 1000


def test_pilot_frames_end_to_end_local(e2e):
    pg, s3, a, b = e2e["pg"], e2e["s3"], e2e["a"], e2e["b"]
    api_a, api_b = Api(e2e["base"], a), Api(e2e["base"], b)
    runs = int(os.environ.get("RAV_FRAMES_E2E_RUNS", "1"))
    bench_video = os.environ.get("RAV_BENCH_VIDEO")
    m = {"upload_ready_ms": [], "first_thumb_cold_ms": [], "thumb_cold_ms": [], "thumb_warm_ms": [], "grid_cold_ms": [], "grid_warm_ms": [],
         "frame_ms": [], "clip_cold_ms": [], "clip_warm_ms": []}
    sources = []
    for run in range(runs):
        path = Path(bench_video) if bench_video and run == 0 else make_video(e2e["tmp"] / f"up{run}.mp4", seconds=30, fps=10,
                                                                               size=(640, 360), shift=run * 7)
        done, ms = upload(api_a, path.read_bytes(), f"video-{run}.mp4")
        m["upload_ready_ms"].append(ms)
        src = api_a.ok("GET", f"/sources/{done['source_id']}")
        assert src["frames"] == {"ready": True, "job_id": None}
        _, frames, jpegs = expected_jpegs(path)
        units = [unit(src["core_source_id"], f) for f in frames[:48]]
        dtos, per, total = grid(api_a, src["id"], units)
        assert all(d["state"] == "READY" and d["job_id"] is None for d in dtos)
        assert all(d["bytes"] == jpegs[("THUMBNAIL", f.idx)] for d, f in zip(dtos, frames[:48]))
        m["first_thumb_cold_ms"].append(per[0])
        m["thumb_cold_ms"] += per
        m["grid_cold_ms"].append(total)
        _, per, total = grid(api_a, src["id"], units)
        m["thumb_warm_ms"] += per
        m["grid_warm_ms"].append(total)
        full, ms = media(api_a, src["id"], units[3], "frame")
        assert full["bytes"] == jpegs[("FRAME", frames[3].idx)]
        m["frame_ms"].append(ms)
        big, _ = media(api_a, src["id"], units[3], "thumbnail", max_side=640)
        assert big["bytes"] == jpegs[("FRAME", frames[3].idx)]   # max_side > 320 → ảnh đầy đủ
        clip, ms = media(api_a, src["id"], units[20], "context")
        assert clip["bytes"][4:8] == b"ftyp"
        m["clip_cold_ms"].append(ms)
        clip2, ms = media(api_a, src["id"], units[20], "context")
        assert clip2["id"] == clip["id"]
        m["clip_warm_ms"].append(ms)
        sources.append((src, units, dtos))
    assert pg.sql("SELECT count(*)::int AS n FROM rav_pilot_job WHERE kind = 'RENDER_MEDIA' AND payload->>'variant' <> 'CONTEXT'")[0]["n"] == 0

    # Cô lập: B không xin được media/nguồn của A, kể cả theo id ảnh.
    src, units, dtos = sources[0]   # cô lập + xóa trên nguồn của lượt đầu
    assert api_b.call("POST", "/media/requests", {"source_id": src["id"], "unit_id": units[0], "variant": "thumbnail"}).status_code == 404
    assert api_b.call("GET", f"/media/requests/{dtos[0]['id']}").status_code == 404

    # Nguồn cũ (trước DEP7-Frames): NOT_PREPARED → bấm chuẩn bị → giết process CPU giữa job → phục hồi và READY.
    legacy_path = make_video(e2e["tmp"] / "legacy.mp4", seconds=60, fps=10, size=(640, 360), shift=91)
    lsid, lcore, lframes = seed_source(pg, s3, legacy_path, a)
    lunit = unit(lcore, lframes[10])
    np_ = api_a.ok("POST", "/media/requests", {"source_id": str(lsid), "unit_id": lunit, "variant": "thumbnail"})
    assert (np_["state"], np_["id"], np_["job_id"]) == ("NOT_PREPARED", None, None)
    t0 = time.perf_counter()
    prep = api_a.ok("POST", f"/sources/{lsid}/preparations", {"feature": "FRAMES"}, key="prep-legacy", status=(202,))
    job_id = prep["job"]["id"]
    assert api_a.ok("GET", f"/sources/{lsid}")["frames"]["job_id"] == job_id
    assert api_a.ok("POST", "/media/requests", {"source_id": str(lsid), "unit_id": lunit, "variant": "thumbnail"})["job_id"] == job_id
    _wait(lambda: pg.sql("SELECT count(*)::int AS n FROM rav_pilot_worker_object WHERE job_id = %s", (job_id,))[0]["n"] > 0, 120,
          "job chuẩn bị bắt đầu ghi ảnh")
    killed = _children(e2e["wd"])["CPU"]["pid"]
    psutil.Process(killed).kill()   # process CPU chết giữa job: lease hết hạn → supervisor đối soát → attempt mới
    _wait(lambda: api_a.ok("GET", f"/sources/{lsid}")["frames"]["ready"], 300, "nguồn cũ có ảnh sau phục hồi")
    prepare_ms = (time.perf_counter() - t0) * 1000
    attempts = pg.sql("SELECT state, cleanup_done FROM rav_pilot_job_attempt WHERE job_id = %s ORDER BY started_at", (job_id,))
    assert len(attempts) >= 2 and pg.sql("SELECT state FROM rav_pilot_job WHERE id = %s", (job_id,))[0]["state"] == "SUCCEEDED"
    assert _children(e2e["wd"])["CPU"]["pid"] != killed
    _, _, ljpegs = expected_jpegs(legacy_path)
    lthumb, _ = media(api_a, str(lsid), lunit, "thumbnail")
    assert lthumb["bytes"] == ljpegs[("THUMBNAIL", lframes[10].idx)]
    _wait(lambda: pg.sql("SELECT count(*)::int AS n FROM rav_pilot_worker_object WHERE job_id = %s AND state IN ('INTENT', 'WRITTEN', "
                         "'DELETE_PENDING')", (job_id,))[0]["n"] == 0, 120, "dọn object của attempt bị giết")
    listed = keys(s3, f"pilot-test/private/{a}/frames/")
    in_db = {r["object_key"] for r in pg.sql("SELECT f.object_key FROM rav_pilot_frame f JOIN rav_pilot_frame_set s ON s.id = f.frame_set_id "
                                             "WHERE s.state = 'READY'")}
    assert listed == in_db   # không còn ảnh mồ côi của attempt bị giết

    # Nguồn SYSTEM: operator CLI dry-run/apply, người dùng B thấy ảnh, chỉ GLOBAL bị tính.
    import importlib.util
    spec = importlib.util.spec_from_file_location("cli", Path(__file__).parents[1] / "scripts" / "pilot_prepare_system_frames.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    sys_path = make_video(e2e["tmp"] / "sys.mp4", seconds=6, fps=10, shift=33)
    ssid, score, sframes = seed_source(pg, s3, sys_path, None)
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(pg.admin_url, row_factory=dict_row) as conn:
        plan = cli.build_plan(conn.cursor(), "pilot-test")
    assert [p["source_id"] for p in plan["sources"]] == [str(ssid)]
    user_before = pg.sql("SELECT persisted_bytes FROM rav_pilot_storage_account WHERE account_key = %s", (f"USER:{a}",))[0]["persisted_bytes"]
    cli.apply_plan(pg.admin_url, s3, plan, e2e["policy_version"], e2e["tmp"] / "cli")
    sthumb, _ = media(api_b, str(ssid), unit(score, sframes[2]), "thumbnail")
    assert sthumb["state"] == "READY" and sthumb["bytes"][:2] == b"\xff\xd8"
    assert pg.sql("SELECT persisted_bytes FROM rav_pilot_storage_account WHERE account_key = %s", (f"USER:{a}",))[0]["persisted_bytes"] == user_before

    # Xóa nguồn đã upload: ảnh lưu sẵn thuộc inventory, URL không còn cấp, dung lượng trừ một lần.
    fs = pg.sql("SELECT id, total_bytes FROM rav_pilot_frame_set WHERE source_id = %s", (src["id"],))[0]
    raw = pg.sql("SELECT size_bytes FROM rav_pilot_source_asset WHERE id = %s", (src["id"],))[0]["size_bytes"]
    clip_bytes = pg.sql("SELECT coalesce(sum(size_bytes), 0)::bigint AS n FROM rav_pilot_artifact WHERE source_id = %s AND state = 'READY'",
                        (src["id"],))[0]["n"]
    before = pg.sql("SELECT persisted_bytes FROM rav_pilot_storage_account WHERE account_key = %s", (f"USER:{a}",))[0]["persisted_bytes"]
    req = api_a.ok("POST", f"/sources/{src['id']}/delete-requests", {"expected_resource_version": src["resource_version"]}, key="del-1")
    _wait(lambda: api_a.ok("GET", f"/delete-requests/{req['id']}")["state"] in ("SUCCEEDED", "FAILED"), 300, "xóa nguồn")
    assert api_a.ok("GET", f"/delete-requests/{req['id']}")["state"] == "SUCCEEDED"
    assert pg.sql("SELECT state FROM rav_pilot_frame_set WHERE id = %s", (fs["id"],))[0]["state"] == "DELETED"
    assert not keys(s3, f"pilot-test/private/{a}/frames/{fs['id']}/")
    assert api_a.ok("GET", f"/media/requests/{dtos[0]['id']}")["state"] == "FAILED"
    after = pg.sql("SELECT persisted_bytes FROM rav_pilot_storage_account WHERE account_key = %s", (f"USER:{a}",))[0]["persisted_bytes"]
    assert before - after == raw + fs["total_bytes"] + clip_bytes

    # Trạng thái cuối: không receipt treo, slot rảnh, worker còn đủ process con.
    _wait(lambda: pg.sql("SELECT count(*)::int AS n FROM rav_pilot_worker_object WHERE state IN ('INTENT', 'WRITTEN', 'DELETE_PENDING')")[0]["n"] == 0,
          60, "không còn receipt treo")
    assert {r["state"] for r in pg.sql("SELECT state FROM rav_pilot_resource_slot")} == {"FREE"}
    assert set(_children(e2e["wd"])) == {"CPU", "MEDIA"}

    report = {"local_only": True, "note": "PostgreSQL local + moto server + worker spawn thật; không phải số cloud", "runs": runs,
              "video": bench_video or "synthetic 30 s 640x360 10 fps", "grid_units": len(units), "legacy_prepare_with_cpu_kill_ms": round(prepare_ms),
              **{k: summary(v) for k, v in m.items()}}
    out = Path("output/frames-bench")
    out.mkdir(parents=True, exist_ok=True)
    (out / f"e2e-local-{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    statistics.mean(m["thumb_warm_ms"])   # có số đo thật
