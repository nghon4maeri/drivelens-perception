"""Probe liveness worker Docker qua PID/start token, hostname và host boot.

Input: process.json trong RAV_PILOT_WORKDIR (process chính + process con theo queue); output: exit 0 alive, 1 unavailable.
Chạy `python -m src.rav.worker.health`. Không truy cập DB/S3 hoặc claim job.
"""

import json
import socket

from ..errors import ServiceError
from .__main__ import _context, _workdir
from .process import _token


def _running(proc: dict) -> bool:
    pid = proc.get("pid")
    return type(pid) is int and pid > 0 and bool(proc.get("start_token")) and _token(pid) == proc["start_token"]


def alive() -> bool:
    """Process chính và mọi process con queue đang cần (`expected`, §21.20 F3) đều sống đúng PID/start token."""
    try:
        record = json.loads((_workdir() / "process.json").read_text(encoding="utf-8"))
        children = record.get("children") or {}
        return (_running(record) and record.get("host") == socket.gethostname()
                and record.get("host_boot_id") == str(_context().host_boot_id)
                and all(q in children and _running(children[q]) for q in record.get("expected") or []))
    except (OSError, ValueError, AttributeError, TypeError, ServiceError):
        return False


if __name__ == "__main__":
    raise SystemExit(0 if alive() else 1)
