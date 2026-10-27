"""Entry worker pilot: PostgreSQL/S3 → CPU/MEDIA (+ GPU nếu có Kaggle) dispatcher và supervisor.

Process chính chỉ chạy supervisor; mỗi queue một process `spawn` (`_run_queue`), chết thì khởi động lại có chờ tăng dần,
chết lặp thì thoát mã 3 cho Docker khởi động lại (CONTRACTS §21.20 F3).

Chạy `python -m src.rav.worker --mode pilot` với environment được inject;
`--mode operator-bootstrap` chỉ báo probe, không claim/recovery hoặc nạp ML.
GPU (DEP5a, §21.14): có `KAGGLE_API_TOKEN` → adapter cho mỗi slot GPU (DB `rav_pilot_resource_slot`) thuộc account của token,
dispatcher queue GPU và snapshot quota mỗi `RAV_PILOT_GPU_BUDGET_S` giây; thiếu token → không claim job GPU.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import multiprocessing
import os
import signal
import threading
import time
from pathlib import Path
from uuid import UUID, uuid4

from ..errors import ServiceError
from ..store.pilot_db import PilotDB
from ..store.pilot_runtime import PilotRuntime, WorkerContext
from ..store.pilot_storage import PilotStorage
from ..store.pilot_uploads import PilotUploads
from .dispatcher import PilotDispatcher
from .lanes import check_container_budget, configure_threads, cpu_lanes
from .supervisor import PilotSupervisor

log = logging.getLogger(__name__)


def _seconds(name: str, default: str) -> float:
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        value = 0
    if not math.isfinite(value) or not 0 < value <= 60:
        raise ServiceError("validation_error", f"{name} phải > 0 và ≤ 60 giây", 422)
    return value


def _workdir() -> Path:
    path = Path(os.environ.get("RAV_PILOT_WORKDIR", "output/pilot-worker")).resolve()
    roots = (Path("data").resolve(), Path("output").resolve())
    if not any(path.is_relative_to(root) and path != root for root in roots):
        raise ServiceError("validation_error", "RAV_PILOT_WORKDIR phải nằm trong data/ hoặc output/", 422)
    return path


def _context() -> WorkerContext:
    boot = os.environ.get("RAV_PILOT_HOST_BOOT_ID")
    if not boot:
        try:
            boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        except OSError:
            raise ServiceError("dependency_unavailable", "cần RAV_PILOT_HOST_BOOT_ID UUID của lần boot host", 503) from None
    try:
        return WorkerContext(worker_id=uuid4(), host_boot_id=UUID(boot))
    except ValueError:
        raise ServiceError("validation_error", "RAV_PILOT_HOST_BOOT_ID phải là UUID", 422) from None


def preflight() -> tuple[PilotDB, PilotStorage]:
    """Không lấy DATABASE_URL của API; không tự migrate hoặc mở tài nguyên cloud."""
    url = os.environ.get("RAV_PILOT_WORKER_DATABASE_URL", "")
    if not url:
        raise ServiceError("dependency_unavailable", "thiếu RAV_PILOT_WORKER_DATABASE_URL", 503)
    from .session_context import cache_cap
    cache_cap()   # trần cache sai → dừng sớm thay vì lỗi giữa job (§21.20 F4)
    db = PilotDB(url)
    db.ensure_schema()
    check_container_budget()
    lanes = cpu_lanes()
    if lanes:
        expected = {slot for lane in lanes.values() for slot in lane.slots}
        with db.backend() as cur:
            cur.execute("SELECT slot_key, kind FROM rav_pilot_resource_slot WHERE slot_key = ANY(%s)", (sorted(expected),))
            if {r["slot_key"] for r in cur.fetchall() if r["kind"] == "CPU"} != expected:
                raise ServiceError("policy_unconfigured", "CPU lane slots chưa provision", 503)
    return db, PilotStorage.from_env()


def gpu_adapters(db: PilotDB) -> dict:
    """slot_key → KaggleSlotAdapter cho slot GPU thuộc account của token; không token → {} (không claim GPU)."""
    token = os.environ.get("KAGGLE_API_TOKEN", "")
    if not token:
        return {}
    from ..pipeline.kaggle_gpu import KaggleGPUClient
    from .kaggle_adapter import KaggleSlotAdapter

    provider = KaggleGPUClient(api_token=token)
    account = provider.account()
    with db.backend() as cur:
        cur.execute("SELECT slot_key, account_or_host FROM rav_pilot_resource_slot WHERE kind = 'GPU' ORDER BY slot_key")
        slots = cur.fetchall()
    out = {}
    for s in slots:
        if s["account_or_host"] != account:
            log.warning("slot GPU %s thuộc account khác token; bỏ qua", s["slot_key"])
            continue
        def session_lookup(slot_key=s["slot_key"]):
            with db.backend() as cur:
                cur.execute("SELECT session_id,launch_state FROM rav_pilot_job_attempt WHERE process_group->>'gpu_slot_key'=%s "
                            "AND launch_state <> 'NONE' ORDER BY started_at DESC,id DESC LIMIT 1", (slot_key,))
                attempt = cur.fetchone()
                if attempt:
                    return attempt["session_id"]  # missing handle after INTENT remains UNKNOWN
                cur.execute("SELECT reconcile_evidence FROM rav_pilot_resource_slot WHERE slot_key=%s", (slot_key,))
                return (cur.fetchone()["reconcile_evidence"] or {}).get("verified_session_id")

        out[s["slot_key"]] = KaggleSlotAdapter(provider, account, s["slot_key"], session_lookup=session_lookup)
    return out


RESTART_MIN_S, RESTART_MAX_S = 1.0, 60.0   # chờ tăng dần trước khi khởi động lại process con (§21.20 F3)
CRASH_LIMIT, CRASH_WINDOW_S = 5, 600.0     # quá 5 lần chết trong 10 phút → process chính thoát cho Docker khởi động lại
CHILD_STOP_S = 60                          # chờ process con dừng hợp tác trước terminate (dispatcher tự os._exit theo stop_deadline)
CHILD_TERM_S = 5                           # SIGTERM có thể bị process con bỏ qua trên Linux
CHILD_KILL_S = 5
CRASH_EXIT_CODE = 3


class ChildCrashLoopError(RuntimeError):
    """Một queue chết lặp; process chính dừng mọi process con và thoát mã 3."""


class ChildShutdownError(RuntimeError):
    """Ít nhất một process con còn sống sau khi đã hết thời hạn dừng."""


def queue_handlers(queue: str, storage: PilotStorage, db: PilotDB) -> dict:
    """Handler theo queue: CPU/MEDIA dùng chung bộ handler worker; GPU theo adapter Kaggle của token."""
    workdir = _workdir()
    if queue == "GPU":
        from .gpu_handlers import make_gpu_handlers
        return make_gpu_handlers(storage, db, workdir, gpu_adapters(db), deployment=os.environ.get("RAV_PILOT_DEPLOYMENT", "pilot"))
    from .analysis_handlers import make_analysis_handlers
    from .curation_handlers import make_curation_handlers
    from .export_handlers import make_export_handlers
    from .handlers import make_handlers
    handlers = make_handlers(storage, db, workdir)
    handlers.update(make_curation_handlers(storage, db, workdir))   # PROPOSE trên queue CPU (DEP5b §21.15)
    handlers.update(make_analysis_handlers(storage, db, workdir))   # YOLO_BATCH, METRICS trên queue CPU (DEP5b2 §21.17)
    handlers.update(make_export_handlers(storage, db, workdir))     # EXPORT_BUILD, EXPORT_FINALIZE (§21.8)
    return handlers


def _run_queue(queue: str, stop, host_boot_id: str) -> None:
    """Process con của một queue: DB pool/collector riêng, cùng host boot với process chính, dừng theo stop event chung.
    Bỏ qua SIGINT/SIGTERM (process chính điều phối dừng) và tự dừng khi process cha mất."""
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, signal.SIG_IGN)
    logging.basicConfig(level=logging.INFO)
    lanes = cpu_lanes()
    if lanes and queue not in lanes and queue not in ("MEDIA", "GPU"):
        raise ServiceError("validation_error", "queue không thuộc CPU lanes đã cấu hình", 422)
    lane = lanes.get(queue)
    physical_queue = "CPU" if lane else queue
    if lanes:
        configure_threads(lane.threads if lane else 1)
    parent = os.getppid()
    db, storage = preflight()
    if queue == 'TEXT' and os.environ.get('RAV_INTERACTIVE_ENABLED') == '1':
        from .interactive import serve
        serve(db, storage, _workdir(), stop)
        return
    from ..telemetry import start_collector
    collector = start_collector(db, "gpu" if queue == "GPU" else "worker")
    kinds = lane.kinds if lane else ()
    if queue == 'YOLO' and os.environ.get('RAV_INTERACTIVE_ENABLED') == '1':
        kinds = tuple(dict.fromkeys((*kinds, 'PROPOSE')))
    options = {} if lane is None else {"kinds": kinds, "slot_keys": lane.slots, "rss_limit_bytes": lane.rss_bytes}
    if lanes and lane is None:
        aux = int(os.environ["RAV_PILOT_RUNTIME_AUX_RSS_BYTES"])
        options["rss_limit_bytes"] = aux // (3 if physical_queue == "MEDIA" else 2)
    dispatcher = PilotDispatcher(PilotRuntime(db), WorkerContext(worker_id=uuid4(), host_boot_id=UUID(host_boot_id)),
                                 queue=physical_queue, stop_event=stop, **options)
    for kind, handler in queue_handlers(physical_queue, storage, db).items():
        if lane is not None and kind not in kinds:
            continue
        dispatcher.register_handler(kind, handler)
    poll_s = _seconds("RAV_PILOT_WORKER_POLL_S", "1")
    log.info("process %s sẵn sàng (pid %s)", queue, os.getpid())
    try:
        while not stop.is_set():
            if os.getppid() != parent:   # process chính đã mất: không claim thêm
                break
            try:
                busy = dispatcher.run_once()
            except Exception:
                log.exception("dispatcher %s lỗi; thử lại", queue)
                busy = False
            if lanes:
                from .process import rss_bytes
                used = rss_bytes()
                if used is not None and used > options["rss_limit_bytes"]:
                    log.error("process %s vượt RSS budget cả khi idle; restart để nhả resident cache", queue)
                    os._exit(2)
            if not busy:
                stop.wait(poll_s)
    finally:
        if collector:
            collector.close()


class _Child:
    def __init__(self, queue: str):
        self.queue, self.process, self.next_start, self.deaths = queue, None, 0.0, []


def _write_process_file(path: Path, ctx: WorkerContext, children: list[_Child]) -> None:
    from .process import _token, identity
    record = {**identity(), "host_boot_id": str(ctx.host_boot_id), "expected": [c.queue for c in children], "children": {}}
    for c in children:
        if c.process is not None and c.process.pid:
            record["children"][c.queue] = {"pid": c.process.pid, "start_token": _token(c.process.pid)}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record), encoding="utf-8")
    for attempt in range(10):  # local-only: Windows file replace retry against concurrent readers
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.05)


def _supervise_children(children: list[_Child], process_ctx, child_stop, ctx: WorkerContext, process_file: Path) -> None:
    """Khởi động process con còn thiếu khi đến lượt; process con chết → chờ tăng dần rồi khởi động lại; chết lặp → ChildCrashLoopError."""
    now, changed = time.monotonic(), False
    for c in children:
        if c.process is not None and not c.process.is_alive():
            c.process.join(timeout=0)
            c.deaths = [t for t in c.deaths if now - t < CRASH_WINDOW_S] + [now]
            log.error("process %s đã thoát (mã %s), lần %d trong %d s", c.queue, c.process.exitcode, len(c.deaths), CRASH_WINDOW_S)
            if len(c.deaths) > CRASH_LIMIT:
                raise ChildCrashLoopError(f"process {c.queue} chết {len(c.deaths)} lần trong {CRASH_WINDOW_S:.0f} s")
            c.process, c.next_start = None, now + min(RESTART_MAX_S, RESTART_MIN_S * 2 ** (len(c.deaths) - 1))
            changed = True
        if c.process is None and now >= c.next_start:
            # `python -m` makes this module __main__; spawn needs its importable module identity.
            from .__main__ import _run_queue as target
            c.process = process_ctx.Process(target=target, args=(c.queue, child_stop, str(ctx.host_boot_id)),
                                            name=f"rav-{c.queue.lower()}-dispatcher", daemon=True)
            c.process.start()
            changed = True
    if changed:
        _write_process_file(process_file, ctx, children)


def _stop_children(children: list[_Child], child_stop, process_file: Path) -> None:
    """Dừng có hạn từng queue; giữ process record nếu còn child sống để supervisor đối soát."""
    child_stop.set()
    alive = []
    for c in children:
        process = c.process
        if process is None:
            continue
        process.join(timeout=CHILD_STOP_S)
        if process.is_alive():
            process.terminate()
            process.join(timeout=CHILD_TERM_S)
        if process.is_alive():
            process.kill()
            process.join(timeout=CHILD_KILL_S)
        if process.is_alive():
            alive.append(c.queue)
    if alive:
        raise ChildShutdownError(f"process con vẫn sống sau shutdown: {', '.join(alive)}")
    process_file.unlink(missing_ok=True)


def run(stop: threading.Event) -> None:
    """Process chính: supervisor/reconciliation + điều phối process con CPU, MEDIA (và GPU khi có token Kaggle)."""
    db, storage = preflight()
    from ..telemetry import start_collector
    collector = start_collector(db, 'worker')
    poll_s = _seconds("RAV_PILOT_WORKER_POLL_S", "1")
    reconcile_s = _seconds("RAV_PILOT_WORKER_RECONCILE_S", "10")
    ctx = _context()
    adapters = gpu_adapters(db)
    queues = (tuple(cpu_lanes()) or ("CPU",)) + ("MEDIA",) + (("GPU",) if adapters else ())
    supervisor = PilotSupervisor(PilotRuntime(db), ctx, PilotUploads(db, lambda: storage), gpu=adapters)
    budget_s = _seconds("RAV_PILOT_GPU_BUDGET_S", "60")
    process_ctx = multiprocessing.get_context("spawn")
    child_stop = process_ctx.Event()
    children = [_Child(q) for q in queues]
    process_file = _workdir() / "process.json"
    process_file.parent.mkdir(parents=True, exist_ok=True)   # process chính không còn tạo workdir qua make_handlers
    _write_process_file(process_file, ctx, children)
    log.info("worker pilot sẵn sàng: supervisor + process %s", "/".join(queues))
    next_reconcile = next_budget = 0.0
    try:
        while not stop.is_set():
            if cpu_lanes():
                from .process import rss_bytes
                used = rss_bytes()
                if used is not None and used > int(os.environ["RAV_PILOT_RUNTIME_AUX_RSS_BYTES"]) // 6:
                    raise ChildShutdownError("supervisor vượt RSS budget")
            _supervise_children(children, process_ctx, child_stop, ctx, process_file)
            if adapters and time.monotonic() >= next_budget:
                supervisor.refresh_gpu_budget()
                next_budget = time.monotonic() + budget_s
            if time.monotonic() >= next_reconcile:
                supervisor.run_reconciliation()
                next_reconcile = time.monotonic() + reconcile_s
            stop.wait(poll_s)
    finally:
        try:
            _stop_children(children, child_stop, process_file)
        finally:
            if collector:
                collector.close()



def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("pilot", "operator-bootstrap"), required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    if args.mode == "operator-bootstrap":
        blocked = []
        try:
            preflight()
        except ServiceError as exc:
            blocked.append(exc.code)
        print(json.dumps({"bootstrap_ready": True, "pilot_ready": False, "blocked": blocked,
                          "queues_started": False, "missing_handlers": ["DEP5_CURATION"]}))
        return 0
    stop = threading.Event()
    previous = {}
    def request_stop(signum, frame):
        stop.set()
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, request_stop)
        run(stop)
    except KeyboardInterrupt:
        stop.set()
    except ServiceError as exc:
        log.error("worker không sẵn sàng: %s: %s", exc.code, exc.message)
        return 2
    except ChildCrashLoopError as exc:
        log.error("%s; thoát để Docker khởi động lại container", exc)
        return CRASH_EXIT_CODE
    except ChildShutdownError as exc:
        log.error("%s", exc)
        # Normal interpreter exit joins daemon children without a timeout.
        os._exit(2)
    finally:
        stop.set()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
