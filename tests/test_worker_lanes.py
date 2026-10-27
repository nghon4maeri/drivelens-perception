"""CPU lanes: validated resource partition, process ownership, RSS stop and model reuse."""
import copy
import json
import threading
from types import SimpleNamespace

import pytest

from src.rav.errors import ServiceError
from src.rav.store.pilot_runtime import QUEUE_KINDS
from src.rav.worker import __main__ as entry
from src.rav.worker.dispatcher import PilotDispatcher
from src.rav.worker.lanes import configure_threads, cpu_lanes


def lane_config():
    groups = {"TEXT": ["PROPOSE"], "YOLO": ["YOLO_BATCH"],
              "BULK": [k for k in QUEUE_KINDS["CPU"] if k not in ("PROPOSE", "YOLO_BATCH")]}
    return [{"name": name, "kinds": kinds, "slots": ["cpu-"+name.lower()], "threads": 1,
             "rss_bytes": 1536 << 20} for name, kinds in groups.items()]


def install(monkeypatch, rows=None):
    for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "RAV_DECODER_THREADS", "RAV_MODEL_THREADS"):
        monkeypatch.setenv(key, "3")
    monkeypatch.setenv("RAV_PILOT_CPU_LANES", json.dumps(lane_config() if rows is None else rows))
    monkeypatch.setenv("RAV_PILOT_RUNTIME_CPU_THREADS", "4")
    monkeypatch.setenv("RAV_PILOT_RUNTIME_RSS_BYTES", str(6 << 30))
    monkeypatch.setenv("RAV_PILOT_RUNTIME_AUX_RSS_BYTES", str(1536 << 20))


def test_partition_and_legacy(monkeypatch):
    monkeypatch.delenv("RAV_PILOT_CPU_LANES", raising=False)
    assert cpu_lanes() == {}
    install(monkeypatch)
    lanes = cpu_lanes()
    assert lanes["TEXT"].kinds == ("PROPOSE",)
    assert lanes["YOLO"].slots == ("cpu-yolo",)
    assert set(k for x in lanes.values() for k in x.kinds) == set(QUEUE_KINDS["CPU"])


@pytest.mark.parametrize("fault", ["duplicate_slot", "duplicate_kind", "over_threads", "over_rss", "missing_kind", "bool"])
def test_invalid_partition_fails_before_runtime(monkeypatch, fault):
    rows = copy.deepcopy(lane_config())
    if fault == "duplicate_slot":
        rows[1]["slots"] = rows[0]["slots"]
    elif fault == "duplicate_kind":
        rows[1]["kinds"] = rows[0]["kinds"]
    elif fault == "over_threads":
        rows[0]["threads"] = 2
    elif fault == "over_rss":
        rows[0]["rss_bytes"] = 4 << 30
    elif fault == "missing_kind":
        rows.pop()
    else:
        rows[0]["threads"] = True
    install(monkeypatch, rows)
    with pytest.raises(ServiceError):
        cpu_lanes()


@pytest.mark.parametrize('queue,interactive,expected', [
    ('TEXT', False, ['PROPOSE']), ('YOLO', False, ['YOLO_BATCH']), ('YOLO', True, ['PROPOSE', 'YOLO_BATCH']),
])
def test_child_filters_handlers_and_owns_thread_budget(monkeypatch, queue, interactive, expected):
    install(monkeypatch)
    monkeypatch.setenv('RAV_INTERACTIVE_ENABLED', '1' if interactive else '0')
    stop = threading.Event()
    seen = []
    monkeypatch.setattr(entry, "preflight", lambda: (object(), object()))
    monkeypatch.setattr(entry, "PilotRuntime", lambda db: db)
    monkeypatch.setattr(entry.signal, "signal", lambda *args: None)
    monkeypatch.setattr(entry, "queue_handlers", lambda *args: {"PROPOSE": 1, "YOLO_BATCH": 2})
    class Dispatcher:
        def __init__(self, runtime, ctx, **kwargs):
            seen.append(kwargs)
        def register_handler(self, kind, fn):
            seen.append(kind)
        def run_once(self):
            stop.set()
            return True
    monkeypatch.setattr(entry, "PilotDispatcher", Dispatcher)
    entry._run_queue(queue, stop, "00000000-0000-0000-0000-000000000001")
    assert seen[0]["queue"] == "CPU" and seen[0]["slot_keys"] == ('cpu-'+queue.lower(),)
    assert set(seen[0]['kinds']) == set(expected)
    assert seen[1:] == expected
    import os
    assert os.environ["OMP_NUM_THREADS"] == "1" and os.environ["RAV_DECODER_THREADS"] == "1"


def test_model_reuse_and_replaced_checkpoint_invalidate(monkeypatch, tmp_path):
    from src.rav.pipeline import batch_yolo as y
    p = tmp_path / "model.pt"
    p.write_bytes(b"checkpoint-a")
    calls = []
    def load(*args):
        result = object()
        calls.append(result)
        return result
    monkeypatch.setattr(y, "load_model", load)
    assert y._model(p, tmp_path) is y._model(p, tmp_path)
    p.write_bytes(b"checkpoint-b")
    assert y._model(p, tmp_path) is calls[-1] and len(calls) == 2


def test_corrupt_content_addressed_model_fails_before_load(monkeypatch, tmp_path):
    from src.rav.pipeline import batch_yolo as y
    p = tmp_path / (("a"*64)+".pt")
    p.write_bytes(b"corrupt-cache")
    monkeypatch.setattr(y, "load_model", lambda *args: pytest.fail("unverified cache loaded"))
    with pytest.raises(y.YoloError, match="SHA"):
        y._model(p, tmp_path)


def test_rss_stop_is_independent_of_db(monkeypatch):
    d = PilotDispatcher(None, None, rss_limit_bytes=10)
    stop = threading.Event()
    class Done:
        n = 0
        def wait(self, _):
            self.n += 1
            return self.n > 1
    monkeypatch.setattr("src.rav.worker.dispatcher.rss_bytes", lambda: 11)
    d._watch(SimpleNamespace(kind="PROPOSE"), {"worker_rss_bytes": 100, "run_deadline_ms_by_kind": {},
                                              "stop_deadline_ms": 10000}, stop, Done())
    assert stop.is_set() and d._resource_error["code"] == "resource_limit"


def test_threads_only_changes_child_environment(monkeypatch):
    for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "RAV_DECODER_THREADS", "RAV_MODEL_THREADS"):
        monkeypatch.setenv(key, "3")
    configure_threads(1)
    import os
    assert os.environ["MKL_NUM_THREADS"] == "1"


@pytest.mark.parametrize("cpu,memory,valid", [("300000 100000", str(6 << 30), False),
                                            ("400000 100000", str(4 << 30), False),
                                            ("400000 100000", str(6 << 30), True)])
def test_declared_budget_cannot_exceed_cgroup(monkeypatch, tmp_path, cpu, memory, valid):
    from pathlib import Path

    from src.rav.worker import lanes
    install(monkeypatch)
    (tmp_path / "cpu.max").write_text(cpu)
    (tmp_path / "memory.max").write_text(memory)
    monkeypatch.setattr(lanes, "Path", lambda name: tmp_path / Path(name).name)
    if valid:
        lanes.check_container_budget()
    else:
        with pytest.raises(ServiceError, match="cgroup"):
            lanes.check_container_budget()


def test_legacy_cpu_cannot_bypass_configured_lanes(monkeypatch):
    install(monkeypatch)
    monkeypatch.setattr(entry.signal, "signal", lambda *args: None)
    with pytest.raises(ServiceError, match="queue"):
        entry._run_queue("CPU", threading.Event(), "00000000-0000-0000-0000-000000000001")
