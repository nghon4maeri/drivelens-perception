"""Dấu vết process worker để supervisor kiểm tra thoát, không cần thư viện ML.

Input PID/host/start token; output bằng chứng hoặc None khi không thể kiểm chứng.
"""
from __future__ import annotations

import ctypes
import os
import socket
from pathlib import Path


def _token(pid: int) -> str | None:
    if os.name != "nt":
        try:
            return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
        except FileNotFoundError:
            return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.c_void_p] * 4
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return None
        raise OSError("không kiểm tra được process")
    try:
        times = [ctypes.c_uint64() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            raise OSError("không đọc được process start token")
        return str(times[0].value)
    finally:
        kernel.CloseHandle(handle)


def identity() -> dict:
    pid = os.getpid()
    return {"pid": pid, "host": socket.gethostname(), "start_token": _token(pid)}


def rss_bytes() -> int | None:
    """Resident process memory, including warm model/cache; no ML or psutil dependency."""
    if os.name != "nt":
        try:
            for line in Path("/proc/self/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
        except (OSError, ValueError):
            return None
        return None
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ("peak_rss", "rss", "peak_paged", "paged", "peak_nonpaged", "nonpaged",
                                                "pagefile", "peak_pagefile")]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]
    if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return counters.rss
    return None


def stopped(process: dict | None, old_boot: str, boot: str) -> bool:
    if not process or process.get("host") != socket.gethostname() or old_boot != boot:
        return False
    if not process.get("start_token") or not isinstance(process.get("pid"), int):
        return False
    try:
        return _token(process["pid"]) != process["start_token"]
    except OSError:
        return False
