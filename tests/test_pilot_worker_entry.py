"""Worker entry tests: fail closed, queue wiring, bootstrap và signal shutdown; không cloud thật."""

import json
import os
import signal
import threading
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.rav.errors import ServiceError
from src.rav.worker import __main__ as entry


def test_worker_database_never_falls_back(monkeypatch):
    monkeypatch.delenv("RAV_PILOT_WORKER_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://api@localhost/api")
    with pytest.raises(ServiceError, match="RAV_PILOT_WORKER_DATABASE_URL"):
        entry.preflight()


def test_schema_failure_precedes_storage_or_claim(monkeypatch):
    monkeypatch.setenv("RAV_PILOT_WORKER_DATABASE_URL", "postgresql://worker@localhost/test")
    def failed():
        raise ServiceError("schema_not_ready", "schema", 503)
    monkeypatch.setattr(entry, "PilotDB", lambda url: SimpleNamespace(ensure_schema=failed))
    monkeypatch.setattr(entry.PilotStorage, "from_env", lambda: pytest.fail("storage before schema"))
    with pytest.raises(ServiceError, match="schema"):
        entry.run(threading.Event())


class FakeProcess:
    """Process giả: sống tới khi test cho chết; pid = process test để `_token` đọc được."""
    started: list = []

    def __init__(self, *, target, args, name, daemon):
        self.target, self.args, self.name, self.daemon = target, args, name, daemon
        self.pid, self.exitcode, self.alive, self.joined = os.getpid(), None, False, []
        FakeProcess.started.append(self)

    def start(self):
        self.alive = True

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):   # process con dừng hợp tác khi stop event chung đã bật
        if self.args[1].is_set():
            self.alive = False
        self.joined.append(timeout)

    def terminate(self):
        self.alive = False


class StubbornProcess(FakeProcess):
    """Process giả không dừng qua event hoặc terminate; kill có thể thành công hay thất bại."""

    def __init__(self, *, kill_works=True):
        self.alive, self.joined, self.terminated, self.killed = True, [], 0, 0
        self.kill_works = kill_works

    def join(self, timeout=None):
        self.joined.append(timeout)

    def terminate(self):
        self.terminated += 1

    def kill(self):
        self.killed += 1
        if self.kill_works:
            self.alive = False


def test_shutdown_cooperative_child_stays_bounded(tmp_path):
    child_stop = threading.Event()
    process = FakeProcess(target=None, args=(None, child_stop), name="cpu", daemon=True)
    process.start()
    child = entry._Child("CPU")
    child.process = process
    record = tmp_path / "process.json"
    record.write_text("present")

    entry._stop_children([child], child_stop, record)

    assert child_stop.is_set()
    assert process.joined == [entry.CHILD_STOP_S]
    assert not record.exists()


@pytest.mark.parametrize("kill_works", [True, False])
def test_shutdown_escalates_and_preserves_live_child_record(tmp_path, kill_works):
    child_stop = threading.Event()
    process = StubbornProcess(kill_works=kill_works)
    child = entry._Child("CPU")
    child.process = process
    record = tmp_path / "process.json"
    record.write_text("present")

    if kill_works:
        entry._stop_children([child], child_stop, record)
        assert not record.exists()
    else:
        with pytest.raises(entry.ChildShutdownError, match="CPU"):
            entry._stop_children([child], child_stop, record)
        assert record.read_text() == "present"

    assert child_stop.is_set()
    assert process.terminated == process.killed == 1
    assert process.joined == [entry.CHILD_STOP_S, entry.CHILD_TERM_S, entry.CHILD_KILL_S]


def test_shutdown_attempts_all_children_even_if_one_survives(tmp_path):
    child_stop = threading.Event()
    stuck = StubbornProcess(kill_works=False)
    other = StubbornProcess(kill_works=True)
    children = [entry._Child("CPU"), entry._Child("MEDIA")]
    children[0].process, children[1].process = stuck, other
    record = tmp_path / "process.json"
    record.write_text("present")

    with pytest.raises(entry.ChildShutdownError, match="CPU"):
        entry._stop_children(children, child_stop, record)

    assert other.killed == 1 and not other.is_alive()
    assert stuck.is_alive() and record.exists()


def test_main_reports_shutdown_failure(monkeypatch):
    class HardExit(BaseException):
        pass

    def fail(_stop):
        raise entry.ChildShutdownError("CPU vẫn sống")
    exits = []
    def hard_exit(code):
        exits.append(code)
        raise HardExit
    monkeypatch.setattr(entry, "run", fail)
    monkeypatch.setattr(entry.os, "_exit", hard_exit)
    monkeypatch.setattr(entry.signal, "signal", lambda *a: None)
    with pytest.raises(HardExit):
        entry.main(["--mode", "pilot"])
    assert exits == [2]


def fake_context(child_stop):
    def context(method):
        assert method == "spawn"
        return SimpleNamespace(Event=lambda: child_stop, Process=FakeProcess)
    return context


def test_main_process_only_supervises_and_spawns_queue_children(monkeypatch, tmp_path):
    """Process chính không chạy handler: CPU/MEDIA ở process con `_run_queue`, cùng host boot; dừng thì báo và chờ process con."""
    stop, child_stop, boot = threading.Event(), threading.Event(), uuid4()
    FakeProcess.started = []
    monkeypatch.setattr(entry, "preflight", lambda: (object(), object()))
    monkeypatch.setattr(entry, "_context", lambda: SimpleNamespace(host_boot_id=boot))
    monkeypatch.setattr(entry, "_workdir", lambda: tmp_path)
    monkeypatch.setattr(entry, "PilotRuntime", lambda db: db)
    monkeypatch.setattr(entry, "PilotUploads", lambda *args: object())
    monkeypatch.delenv("KAGGLE_API_TOKEN", raising=False)   # không token → không có process GPU (§21.14)
    monkeypatch.setattr(entry.multiprocessing, "get_context", fake_context(child_stop))
    monkeypatch.setattr(entry, "PilotDispatcher", lambda *a, **k: pytest.fail("process chính không được claim job"))
    seen = []

    def reconcile():
        record = json.loads((tmp_path / "process.json").read_text())
        seen.append(record)
        stop.set()
        return []
    monkeypatch.setattr(entry, "PilotSupervisor", lambda r, c, uploads, gpu: SimpleNamespace(run_reconciliation=reconcile))
    entry.run(stop)
    assert [(p.target, p.args) for p in FakeProcess.started] == [
        (entry._run_queue, ("CPU", child_stop, str(boot))), (entry._run_queue, ("MEDIA", child_stop, str(boot)))]
    assert seen[0]["expected"] == ["CPU", "MEDIA"] and set(seen[0]["children"]) == {"CPU", "MEDIA"}
    assert seen[0]["pid"] == os.getpid() and seen[0]["host_boot_id"] == str(boot)
    assert child_stop.is_set() and all(p.joined == [entry.CHILD_STOP_S] for p in FakeProcess.started)
    assert not (tmp_path / "process.json").exists()


def test_dead_child_restarts_with_backoff_then_crash_loop(monkeypatch, tmp_path):
    FakeProcess.started = []
    clock = [1000.0]
    monkeypatch.setattr(entry.time, "monotonic", lambda: clock[0])
    ctx = SimpleNamespace(host_boot_id=uuid4())
    child_stop = threading.Event()
    children = [entry._Child("CPU"), entry._Child("MEDIA")]
    process_ctx = SimpleNamespace(Process=FakeProcess)
    pf = tmp_path / "process.json"
    entry._supervise_children(children, process_ctx, child_stop, ctx, pf)
    cpu, media = children
    first_media = media.process
    waits = []
    for death in range(1, entry.CRASH_LIMIT + 1):
        cpu.process.alive, cpu.process.exitcode = False, -9
        entry._supervise_children(children, process_ctx, child_stop, ctx, pf)
        assert cpu.process is None and "CPU" not in json.loads(pf.read_text())["children"]
        waits.append(cpu.next_start - clock[0])
        clock[0] = cpu.next_start - 0.01
        entry._supervise_children(children, process_ctx, child_stop, ctx, pf)
        assert cpu.process is None   # chưa tới lượt
        clock[0] = cpu.next_start
        entry._supervise_children(children, process_ctx, child_stop, ctx, pf)
        assert cpu.process is not None and cpu.process.alive
    assert waits == [1.0, 2.0, 4.0, 8.0, 16.0]
    assert media.process is first_media and media.process.alive   # queue khác không bị khởi động lại
    cpu.process.alive = False
    with pytest.raises(entry.ChildCrashLoopError):
        entry._supervise_children(children, process_ctx, child_stop, ctx, pf)


def test_backoff_caps_and_old_deaths_expire(monkeypatch, tmp_path):
    FakeProcess.started = []
    clock = [0.0]
    monkeypatch.setattr(entry.time, "monotonic", lambda: clock[0])
    child = entry._Child("MEDIA")
    args = ([child], SimpleNamespace(Process=FakeProcess), threading.Event(), SimpleNamespace(host_boot_id=uuid4()),
            tmp_path / "process.json")
    entry._supervise_children(*args)
    for _ in range(20):   # mỗi lần chết cách nhau > cửa sổ 10 phút: không thành crash loop, chờ không vượt 60 s
        clock[0] += entry.CRASH_WINDOW_S + 1
        child.process.alive = False
        entry._supervise_children(*args)
        assert child.next_start - clock[0] == entry.RESTART_MIN_S
        clock[0] = child.next_start
        entry._supervise_children(*args)
    child.deaths = [clock[0]] * 4
    child.process.alive = False
    entry._supervise_children(*args)
    assert child.next_start - clock[0] == min(entry.RESTART_MAX_S, 2 ** 4)


def test_gpu_child_and_supervisor_failure_stops_children(monkeypatch, tmp_path):
    """Có token Kaggle → thêm process GPU; supervisor lỗi vẫn báo dừng và chờ mọi process con."""
    stop, child_stop = threading.Event(), threading.Event()
    FakeProcess.started = []
    monkeypatch.setattr(entry, "preflight", lambda: (object(), object()))
    monkeypatch.setattr(entry, "_context", lambda: SimpleNamespace(host_boot_id=uuid4()))
    monkeypatch.setattr(entry, "_workdir", lambda: tmp_path)
    monkeypatch.setattr(entry, "gpu_adapters", lambda db: {"gpu": object()})
    monkeypatch.setattr(entry, "PilotRuntime", lambda db: db)
    monkeypatch.setattr(entry, "PilotUploads", lambda *args: object())
    monkeypatch.setattr(entry.multiprocessing, "get_context", fake_context(child_stop))

    def fail():
        raise RuntimeError("supervisor failed")
    monkeypatch.setattr(entry, "PilotSupervisor", lambda *args, **kwargs: SimpleNamespace(refresh_gpu_budget=fail))
    with pytest.raises(RuntimeError, match="supervisor failed"):
        entry.run(stop)
    assert [p.args[0] for p in FakeProcess.started] == ["CPU", "MEDIA", "GPU"]
    assert child_stop.is_set() and all(p.joined for p in FakeProcess.started)


def test_run_queue_child_registers_handlers_with_parent_boot(monkeypatch):
    stop, boot = threading.Event(), uuid4()
    monkeypatch.setattr(entry, "preflight", lambda: (object(), object()))
    monkeypatch.setattr(entry, "PilotRuntime", lambda db: db)
    monkeypatch.setattr(entry, "queue_handlers", lambda q, s, d: {"VALIDATE_UPLOAD": 1, "RENDER_MEDIA": 2})
    monkeypatch.setattr(entry.signal, "signal", lambda *a: None)
    made = []

    class Dispatcher:
        def __init__(self, runtime, ctx, queue, stop_event):
            assert ctx.host_boot_id == boot and stop_event is stop
            self.queue, self.kinds = queue, []
            made.append(self)

        def register_handler(self, kind, fn):
            self.kinds.append(kind)

        def run_once(self):
            stop.set()
            return True
    monkeypatch.setattr(entry, "PilotDispatcher", Dispatcher)
    entry._run_queue("MEDIA", stop, str(boot))
    assert [(d.queue, d.kinds) for d in made] == [("MEDIA", ["VALIDATE_UPLOAD", "RENDER_MEDIA"])]


def test_crash_loop_exits_with_code_3(monkeypatch):
    def run(stop):
        raise entry.ChildCrashLoopError("CPU chết lặp")
    monkeypatch.setattr(entry, "run", run)
    monkeypatch.setattr(entry.signal, "signal", lambda *a: None)
    assert entry.main(["--mode", "pilot"]) == entry.CRASH_EXIT_CODE


def test_bootstrap_reports_blocked_without_running(monkeypatch, capsys):
    def failed():
        raise ServiceError("schema_not_ready", "schema", 503)
    monkeypatch.setattr(entry, "preflight", failed)
    monkeypatch.setattr(entry, "run", lambda stop: pytest.fail("bootstrap started worker"))
    assert entry.main(["--mode", "operator-bootstrap"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["bootstrap_ready"] and not result["pilot_ready"]
    assert result["blocked"] == ["schema_not_ready"] and not result["queues_started"]


def test_sigterm_sets_stop_and_restores_handlers(monkeypatch):
    installed = {}
    previous = object()
    def install(sig, fn):
        installed[sig] = fn
        return previous
    monkeypatch.setattr(entry.signal, "signal", install)
    def run(stop):
        installed[signal.SIGTERM](signal.SIGTERM, None)
        assert stop.is_set()
    monkeypatch.setattr(entry, "run", run)
    assert entry.main(["--mode", "pilot"]) == 0
    assert set(installed.values()) == {previous}


def test_workdir_and_poll_bounds(monkeypatch):
    monkeypatch.setenv("RAV_PILOT_WORKDIR", "../escape")
    with pytest.raises(ServiceError):
        entry._workdir()
    monkeypatch.setenv("RAV_PILOT_WORKDIR", "output/test-worker")
    assert entry._workdir().is_relative_to(Path("output").resolve())
    for value in ("nan", "0", "61", "bad"):
        monkeypatch.setenv("RAV_PILOT_WORKER_POLL_S", value)
        with pytest.raises(ServiceError):
            entry._seconds("RAV_PILOT_WORKER_POLL_S", "1")


def test_explicit_host_boot_uuid(monkeypatch):
    boot = uuid4()
    monkeypatch.setenv("RAV_PILOT_HOST_BOOT_ID", str(boot))
    assert entry._context().host_boot_id == boot
