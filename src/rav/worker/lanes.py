"""Opt-in CPU process/slot assignments and budgets (CONTRACTS §21.22).

Input: RAV_PILOT_CPU_LANES JSON and explicit runtime budgets. Output: validated CPU lanes;
no DB writes, ML imports or production slot provisioning.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from ..errors import ServiceError
from ..store.pilot_runtime import QUEUE_KINDS


@dataclass(frozen=True)
class CPULane:
    name: str
    kinds: tuple[str, ...]
    slots: tuple[str, ...]
    threads: int
    rss_bytes: int


def _invalid() -> ServiceError:
    return ServiceError("validation_error", "CPU lanes/budgets không hợp lệ", 422)


def cpu_lanes() -> dict[str, CPULane]:
    raw = os.environ.get("RAV_PILOT_CPU_LANES", "").strip()
    if not raw:
        return {}
    try:
        rows = json.loads(raw)
        budgets = [int(os.environ[k]) for k in ("RAV_PILOT_RUNTIME_CPU_THREADS", "RAV_PILOT_RUNTIME_RSS_BYTES",
                                                "RAV_PILOT_RUNTIME_AUX_RSS_BYTES")]
        if not isinstance(rows, list) or not rows or any(n <= 0 for n in budgets):
            raise ValueError
        lanes, kinds, slots = {}, set(), set()
        for r in rows:
            if not isinstance(r, dict) or set(r) != {"name", "kinds", "slots", "threads", "rss_bytes"}:
                raise ValueError
            name = r["name"]
            if (not isinstance(name, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,31}", name)
                    or name in lanes or name in QUEUE_KINDS):
                raise ValueError
            for key in ("kinds", "slots"):
                values = r[key]
                if (not isinstance(values, list) or not values or any(not isinstance(v, str) or not v for v in values)
                        or len(set(values)) != len(values)):
                    raise ValueError
            if (not set(r["kinds"]).issubset(QUEUE_KINDS["CPU"]) or kinds.intersection(r["kinds"])
                    or slots.intersection(r["slots"]) or len(r["slots"]) != 1
                    or any(type(r[k]) is not int or r[k] <= 0 for k in ("threads", "rss_bytes"))):
                raise ValueError
            lanes[name] = CPULane(name, tuple(r["kinds"]), tuple(r["slots"]), r["threads"], r["rss_bytes"])
            kinds.update(r["kinds"])
            slots.update(r["slots"])
        if (kinds != set(QUEUE_KINDS["CPU"]) or sum(x.threads for x in lanes.values()) + 1 > budgets[0]
                or sum(x.rss_bytes for x in lanes.values()) + budgets[2] > budgets[1]):
            raise ValueError
        return lanes
    except (ValueError, TypeError, KeyError, OverflowError):
        raise _invalid() from None


def configure_threads(n: int) -> None:
    """Set before NumPy/Torch/decoder import; no heavyweight imports in parent/health."""
    for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "RAV_DECODER_THREADS", "RAV_MODEL_THREADS"):
        os.environ[key] = str(n)


def check_container_budget() -> None:
    """Fail an opt-in runtime whose explicit budget exceeds its Linux cgroup-v2 envelope."""
    if not cpu_lanes():
        return
    cpu, memory = Path("/sys/fs/cgroup/cpu.max"), Path("/sys/fs/cgroup/memory.max")
    if cpu.exists():
        quota, period = cpu.read_text().split()
        if quota != "max" and int(os.environ["RAV_PILOT_RUNTIME_CPU_THREADS"]) > int(quota)/int(period):
            raise ServiceError("policy_unconfigured", "CPU runtime budget vượt cgroup", 503)
    if memory.exists():
        cap = memory.read_text().strip()
        if cap != "max" and int(os.environ["RAV_PILOT_RUNTIME_RSS_BYTES"]) > int(cap):
            raise ServiceError("policy_unconfigured", "RSS runtime budget vượt cgroup", 503)
