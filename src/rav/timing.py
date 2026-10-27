"""Safe JSON latency spans for pilot API/worker; stdout, no payloads or credentials.

Use timing(stage) and correlation(**IDs); durations are monotonic and nested.
Process CPU is diagnostic only (includes other threads), never a quota receipt.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import wraps

_ids: ContextVar[dict] = ContextVar("rav_timing_ids", default={})
log = logging.getLogger("rav.timing")
if not log.handlers:
    log.addHandler(logging.StreamHandler(sys.stdout))
log.setLevel(logging.INFO)
log.propagate = False
_allowed = {"request_id", "upload_id", "job_id", "attempt_id", "kind", "status", "bytes",
            "queue_ms", "cache_hit", "duplicate", "outcome", "error_type", "error_code"}


def emit(stage: str, **fields) -> None:
    safe = {k: str(v) if k.endswith("_id") else v for k, v in {**_ids.get(), **fields}.items()
            if k in _allowed and v is not None}
    log.info(json.dumps({"event": "rav_timing", "at": datetime.now(UTC).isoformat(),
                         "stage": stage, **safe}))


@contextmanager
def correlation(**ids):
    token = _ids.set({**_ids.get(), **{k: v for k, v in ids.items() if k in _allowed}})
    try:
        yield
    finally:
        _ids.reset(token)


@contextmanager
def timing(stage: str, **fields):
    wall, cpu = time.perf_counter(), time.process_time()
    outcome, error_type = "ok", None
    try:
        yield
    except BaseException as exc:
        outcome, error_type = "error", type(exc).__name__
        raise
    finally:
        # Numeric timings are generated here, not accepted from arbitrary payloads.
        safe = {k: str(v) if k.endswith("_id") else v for k, v in {**_ids.get(), **fields}.items()
                if k in _allowed and v is not None}
        log.info(json.dumps({"event": "rav_timing", "at": datetime.now(UTC).isoformat(),
                             "stage": stage, **safe, "outcome": outcome, "error_type": error_type,
                             "wall_ms": round((time.perf_counter()-wall)*1000, 3),
                             "process_cpu_ms": round((time.process_time()-cpu)*1000, 3)}))


def traced_handler(fn):
    @wraps(fn)
    def run(lease, stop):
        with correlation(job_id=lease.job_id, attempt_id=lease.attempt_id,
                         upload_id=lease.payload.get("upload_id"), kind=lease.kind), timing("worker.handler"):
            return fn(lease, stop)
    return run
