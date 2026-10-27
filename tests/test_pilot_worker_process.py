"""Process proof and deadline regressions without real process termination."""
import socket
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

from src.rav.store.pilot_runtime import WorkerContext
from src.rav.worker import process
from src.rav.worker.dispatcher import PilotDispatcher


def test_process_proof_rejects_alive_foreign_and_unknown(monkeypatch):
    boot = str(uuid4())
    proof = {"pid": 123, "host": socket.gethostname(), "start_token": "same"}
    monkeypatch.setattr(process, "_token", lambda pid: "same")
    assert not process.stopped(proof, boot, boot)
    assert not process.stopped(dict(proof, host="other-host"), boot, boot)
    assert not process.stopped(proof, boot, str(uuid4()))
    assert not process.stopped(None, boot, boot)
    monkeypatch.setattr(process, "_token", lambda pid: None)
    assert process.stopped(proof, boot, boot)
    monkeypatch.setattr(process, "_token", lambda pid: "reused-pid")
    assert process.stopped(proof, boot, boot)


def test_current_process_has_token_and_is_alive():
    proof = process.identity()
    boot = str(uuid4())
    assert proof["start_token"]
    assert not process.stopped(proof, boot, boot)


def test_database_failure_stops_handler_and_watchdog_remains(monkeypatch):
    class BrokenRuntime:
        def heartbeat(self, *args):
            raise OSError("database offline")
    external, stop, lost, done = (threading.Event() for _ in range(4))
    dispatcher = PilotDispatcher(BrokenRuntime(), WorkerContext(worker_id=uuid4(), host_boot_id=uuid4()),
                                 stop_event=external)
    lease = SimpleNamespace(job_id=uuid4(), kind="VALIDATE_UPLOAD")
    dispatcher._heartbeat(lease, stop, lost, done, 0.001, time.process_time())
    assert stop.is_set() and lost.is_set()
    calls = []
    def exit_worker(code):
        calls.append(code)
        done.set()
    monkeypatch.setattr("src.rav.worker.dispatcher.os._exit", exit_worker)
    dispatcher._watch(lease, {"run_deadline_ms_by_kind": {}, "stop_deadline_ms": 1, "worker_rss_bytes": 4 << 30}, stop, done)
    assert calls == [2]


def test_shutdown_deadline_independent_of_blocked_database(monkeypatch):
    external, stop, done = (threading.Event() for _ in range(3))
    external.set()
    dispatcher = PilotDispatcher(None, WorkerContext(worker_id=uuid4(), host_boot_id=uuid4()), stop_event=external)
    calls = []
    def exit_worker(code):
        calls.append(code)
        done.set()
    monkeypatch.setattr("src.rav.worker.dispatcher.os._exit", exit_worker)
    dispatcher._watch(SimpleNamespace(job_id=uuid4(), kind="RENDER_MEDIA"),
                      {"run_deadline_ms_by_kind": {}, "stop_deadline_ms": 1, "worker_rss_bytes": 4 << 30}, stop, done)
    assert stop.is_set() and calls == [2]
