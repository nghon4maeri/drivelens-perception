"""Smoke spawn thật cho worker (DEP7-Frames F3, CONTRACTS §21.20): process chính supervisor + process con CPU/MEDIA.

PostgreSQL thật + moto server (S3 giả dùng chung giữa các process qua `AWS_ENDPOINT_URL_S3`). Kiểm: process con thật khởi động,
health đạt; giết process CPU → chỉ CPU được khởi động lại, MEDIA giữ nguyên; job CPU dài (upload tách ảnh) không chặn clip MEDIA;
dừng worker → mọi process con thoát. Chạy: đặt `RAV_PILOT_TEST_DATABASE_URL` rồi `python -m pytest tests/test_pilot_worker_spawn.py -q`.
"""

from __future__ import annotations

import json
import multiprocessing
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

import psutil
import pytest
import requests

from src.rav.actor import ActorContext
from src.rav.store.pilot_db import PilotDB
from src.rav.store.pilot_media import MediaRequestIn, PilotMedia
from src.rav.store.pilot_storage import PilotStorage
from src.rav.store.pilot_uploads import PilotUploads, UploadIn
from src.rav.worker import __main__ as entry
from src.rav.worker import health
from src.rav.worker.process import stopped
from tests.conftest import POLICY_CONFIG, make_video
from tests.test_pilot_media import seed_source, unit

_ERRORS: list[BaseException] = []


def _ignore_sigterm(ready):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    ready.set()
    while True:
        time.sleep(1)


@pytest.mark.skipif(sys.platform != "linux", reason="real SIGTERM ignore/kill escalation requires Linux")
def test_real_sigterm_ignoring_child_is_killed(monkeypatch, tmp_path):
    """Linux: SIGTERM bị bỏ qua, nên shutdown phải dùng SIGKILL và chờ có hạn."""
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Event()
    process = ctx.Process(target=_ignore_sigterm, args=(ready,))
    process.start()
    try:
        assert ready.wait(10)
        child = entry._Child("CPU")
        child.process = process
        record = tmp_path / "process.json"
        record.write_text("present")
        monkeypatch.setattr(entry, "CHILD_STOP_S", 0.2)
        monkeypatch.setattr(entry, "CHILD_TERM_S", 0.2)
        monkeypatch.setattr(entry, "CHILD_KILL_S", 5)

        entry._stop_children([child], ctx.Event(), record)

        assert process.exitcode == -signal.SIGKILL
        assert not process.is_alive() and not record.exists()
    finally:
        if process.is_alive():
            process.kill()
            process.join(timeout=5)


@pytest.mark.skipif(sys.platform != "linux", reason="multiprocessing SIGTERM/atexit behavior requires Linux")
def test_surviving_daemon_does_not_block_worker_exit(tmp_path):
    """A surviving daemon makes normal Python exit join forever; the worker must leave promptly."""
    record = tmp_path / "process.json"
    script = """
import multiprocessing
import signal
import sys
import time
from pathlib import Path
from src.rav.worker import __main__ as entry

def ignore_sigterm(ready):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    ready.set()
    while True:
        time.sleep(1)

def fail(stop):
    ctx = multiprocessing.get_context("fork")
    ready = ctx.Event()
    process = ctx.Process(target=ignore_sigterm, args=(ready,), daemon=True)
    process.start()
    record = Path(sys.argv[1])
    record.write_text(str(process.pid))
    assert ready.wait(5)
    process.kill = lambda: None  # simulate a child that remains alive after the kill deadline
    child = entry._Child("CPU")
    child.process = process
    entry.CHILD_STOP_S = entry.CHILD_TERM_S = entry.CHILD_KILL_S = 0.1
    entry._stop_children([child], ctx.Event(), record)

entry.run = fail
raise SystemExit(entry.main(["--mode", "pilot"]))
"""
    try:
        result = subprocess.run([sys.executable, "-c", script, str(record)], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
        assert result.returncode == 2
        assert record.exists()  # supervisor still needs PID evidence
    finally:
        if record.exists():
            try:
                signal_pid = int(record.read_text())
                psutil.Process(signal_pid).kill()
            except (ValueError, psutil.NoSuchProcess):
                pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(cond, timeout: float, what: str):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = cond()
        if value:
            return value
        time.sleep(0.2)
    pytest.fail(f"quá {timeout} s chờ {what}; lỗi process chính: {_ERRORS!r}")


@pytest.fixture
def stack(pg, monkeypatch):
    import boto3
    from moto.server import ThreadedMotoServer

    port = _free_port()
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=port)
    server.start()
    endpoint, bucket = f"http://127.0.0.1:{port}", f"rav-spawn-{uuid4().hex[:8]}"
    env = {"RAV_PILOT_WORKER_DATABASE_URL": pg.worker_url, "RAV_PILOT_HOST_BOOT_ID": str(uuid4()),
           "RAV_PILOT_WORKDIR": f"output/pytest-tmp/spawn-{uuid4().hex[:8]}", "RAV_S3_BUCKET": bucket,
           "RAV_PILOT_S3_PREFIX": "pilot-test", "AWS_ENDPOINT_URL_S3": endpoint, "AWS_ACCESS_KEY_ID": "test",
           "AWS_SECRET_ACCESS_KEY": "test", "AWS_DEFAULT_REGION": "us-east-1", "RAV_PILOT_WORKER_POLL_S": "0.2",
           "RAV_PILOT_WORKER_RECONCILE_S": "1"}
    for k, v in env.items():
        monkeypatch.setenv(k, v)   # process con spawn kế thừa environment
    monkeypatch.delenv("KAGGLE_API_TOKEN", raising=False)
    monkeypatch.delenv("RAV_TRACE_ENABLED", raising=False)
    client = boto3.client("s3", endpoint_url=endpoint, region_name="us-east-1", aws_access_key_id="test",
                          aws_secret_access_key="test")
    client.create_bucket(Bucket=bucket)
    storage = PilotStorage(client, bucket, "pilot-test")
    profiles = dict(POLICY_CONFIG["profiles_by_kind"], VALIDATE_UPLOAD={"cpu_ms": 600_000, "gpu_ms": 0})
    policy = pg.policy(config=dict(POLICY_CONFIG, cpu_base_ms=100_000_000, profiles_by_kind=profiles, storage_user_bytes=1 << 30))
    a = pg.profile(policy_id=policy)
    pg.slot("cpu-1", "CPU")
    pg.slot("media-1", "MEDIA")
    stop = threading.Event()
    errors = _ERRORS
    errors.clear()

    def main():
        try:
            entry.run(stop)
        except BaseException as exc:   # noqa: BLE001 - báo lỗi về thread test
            errors.append(exc)
    thread = threading.Thread(target=main, daemon=True)
    yield {"pg": pg, "s3": storage, "a": a, "stop": stop, "thread": thread, "errors": errors,
           "workdir": Path(env["RAV_PILOT_WORKDIR"])}
    stop.set()
    thread.join(timeout=120)
    server.stop()


def _children(workdir: Path) -> dict:
    try:
        return json.loads((workdir / "process.json").read_text(encoding="utf-8"))["children"]
    except (OSError, ValueError, KeyError):
        return {}


def test_real_spawn_children_restart_and_stop(stack):
    stack["thread"].start()
    wd = stack["workdir"]
    first = _wait(lambda: _children(wd) if set(_children(wd)) == {"CPU", "MEDIA"} else None, 120, "process con CPU/MEDIA")
    _wait(health.alive, 60, "health đạt")
    main_pid = json.loads((wd / "process.json").read_text())["pid"]
    assert main_pid not in {c["pid"] for c in first.values()} and len({c["pid"] for c in first.values()}) == 2
    psutil.Process(first["CPU"]["pid"]).kill()   # process con chết bất ngờ (SIGKILL / TerminateProcess)
    proof = {"pid": first["CPU"]["pid"], "start_token": first["CPU"]["start_token"], "host": socket.gethostname()}
    boot = str(entry._context().host_boot_id)
    _wait(lambda: stopped(proof, boot, boot), 30, "bằng chứng process CPU cũ đã dừng")
    def restarted():
        c = _children(wd)
        return c if "CPU" in c and c["CPU"]["pid"] != first["CPU"]["pid"] else None
    again = _wait(restarted, 60, "khởi động lại CPU")
    assert again["MEDIA"] == first["MEDIA"]   # queue khác không bị khởi động lại
    _wait(health.alive, 60, "health đạt lại")
    stack["stop"].set()
    stack["thread"].join(timeout=120)
    assert not stack["thread"].is_alive() and not stack["errors"]
    assert not any(psutil.pid_exists(c["pid"]) and psutil.Process(c["pid"]).is_running() for c in again.values()
                   if psutil.pid_exists(c["pid"]) and psutil.Process(c["pid"]).status() != psutil.STATUS_ZOMBIE)
    assert not (wd / "process.json").exists()


def test_long_cpu_job_does_not_block_media_clip(stack, tmp_path):
    """Upload tách ảnh (CPU) đang chạy thì clip context (MEDIA) vẫn xong — trước F3 hai queue chạy nối tiếp trong một vòng."""
    pg, a, s3 = stack["pg"], stack["a"], stack["s3"]
    actor = ActorContext(user_id=a)
    sid, core, frames = seed_source(pg, s3, make_video(tmp_path / "ctx.mp4", seconds=8, fps=10, size=(128, 96)), a)
    data = make_video(tmp_path / "long.mp4", seconds=120, fps=10, size=(640, 360)).read_bytes()
    uploads = PilotUploads(PilotDB(pg.api_url), lambda: s3)
    created = uploads.create(actor, UploadIn(filename="long.mp4", declared_bytes=len(data)), "long")
    r = requests.post(created.transfer.url, data=created.transfer.fields, files={"file": ("video", data)})
    assert r.status_code in (200, 204), r.text
    job_id = uploads.complete(actor, created.id).validation_job_id
    stack["thread"].start()

    def job_state():
        return pg.sql("SELECT state FROM rav_pilot_job WHERE id = %s", (job_id,))[0]["state"]
    _wait(lambda: job_state() == "RUNNING", 120, "job upload chạy")
    media = PilotMedia(PilotDB(pg.api_url), lambda: s3)
    t0 = time.monotonic()
    m = media.request(actor, MediaRequestIn(source_id=sid, unit_id=unit(core, frames[5]), variant="context"))
    ready = _wait(lambda: (d := media.get(actor, m.id)).state == "READY" and d, 120, "clip context")
    clip_s = time.monotonic() - t0
    cpu_state_when_clip_ready = job_state()
    assert requests.get(ready.url).content[4:8] == b"ftyp"
    assert cpu_state_when_clip_ready == "RUNNING", f"clip xong sau khi job CPU kết thúc ({clip_s:.1f} s)"
    _wait(lambda: uploads.get(actor, created.id).state == "READY", 300, "upload READY có ảnh")
    assert pg.sql("SELECT state FROM rav_pilot_frame_set")[0]["state"] == "READY"
    pids = {r["kind"]: r["process_group"]["pid"] for r in pg.sql(
        "SELECT j.kind, a.process_group FROM rav_pilot_job_attempt a JOIN rav_pilot_job j ON j.id = a.job_id")}
    main_pid = json.loads((stack["workdir"] / "process.json").read_text())["pid"]
    assert pids["VALIDATE_UPLOAD"] != pids["RENDER_MEDIA"] and main_pid not in pids.values()
    out = Path("output/frames-bench")
    out.mkdir(parents=True, exist_ok=True)
    (out / f"spawn-media-during-cpu-{uuid4().hex[:6]}.json").write_text(json.dumps(
        {"clip_ready_s": round(clip_s, 3), "cpu_state_when_clip_ready": cpu_state_when_clip_ready}))
