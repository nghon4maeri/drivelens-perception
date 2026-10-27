"""Vòng kéo việc của worker pilot (CONTRACTS §21.5, §21.11; runtime spec §5.2).

`run_once()`: claim một job của queue → chạy handler theo `lease.kind` trong khi luồng phụ gửi heartbeat →
`publish` (thành công) hoặc `finish` (lỗi / bị hủy). Kind chưa có handler → job FAILED `handler_unavailable`, không publish giả.
CPU thực dùng đo bằng `time.process_time()` quanh handler (handler chạy trong tiến trình worker); GPU do handler GPU báo.
Handler: `fn(lease, should_stop: threading.Event) -> dict` (outputs; có thể kèm `gpu_ms`, `result_ref`, `cleanup_done`).
Exception của handler có thể mang `gpu_ms` (GPU đã chạy trước khi lỗi). `HandlerDetachedError`: handler bỏ attempt có chủ đích
(launch/stop GPU chưa xác minh, §21.14) → không finish, ngừng heartbeat để lease hết hạn → slot QUARANTINED cho supervisor đối soát.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from typing import Any
from uuid import UUID

from ..errors import ServiceError
from ..store.pilot_runtime import AttemptLease, PilotRuntime, Progress, Usage, WorkerContext
from ..timing import correlation, emit, timing
from .process import identity, rss_bytes

log = logging.getLogger(__name__)
Handler = Callable[[AttemptLease, threading.Event], dict[str, Any]]


class HandlerDetachedError(Exception):
    def __init__(self, message: str, gpu_ms: int = 0):
        super().__init__(message)
        self.gpu_ms = gpu_ms


class PilotDispatcher:
    def __init__(self, runtime: PilotRuntime, worker_ctx: WorkerContext, queue: str = "CPU", heartbeat_s: float | None = None,
                 stop_event: threading.Event | None = None, *, kinds: tuple[str, ...] | None = None,
                 slot_keys: tuple[str, ...] | None = None, rss_limit_bytes: int | None = None):
        self.runtime, self.worker_ctx, self.queue, self.heartbeat_s = runtime, worker_ctx, queue, heartbeat_s
        self._handlers: dict[str, Handler] = {}
        self.stop_event = stop_event or threading.Event()
        self.kinds, self.slot_keys, self.rss_limit_bytes = kinds, slot_keys, rss_limit_bytes
        self._resource_error = None

    def register_handler(self, kind: str, handler: Handler) -> None:
        self._handlers[kind] = handler

    def run_once(self) -> bool:
        """True nếu đã nhận và kết thúc một job (thành công hay không)."""
        if self.stop_event.is_set():
            return False
        filters = {}
        if self.kinds is not None:
            filters["kinds"] = self.kinds
        if self.slot_keys is not None:
            filters["slot_keys"] = self.slot_keys
        lease = self.runtime.claim(self.worker_ctx, queue=self.queue, process=identity(), **filters)
        if not lease:
            return False
        with correlation(job_id=lease.job_id, attempt_id=lease.attempt_id,
                         upload_id=lease.payload.get("upload_id"), kind=lease.kind), timing("worker.attempt"):
            return self._run_lease(lease)

    def _run_lease(self, lease: AttemptLease) -> bool:
        self._resource_error = None
        cfg, queue_ms = lease.policy_config, lease.queue_ms
        if cfg is None or queue_ms is None:   # compatibility for leases created before claim metadata
            with self.runtime.db.backend() as cur:
                cur.execute("SELECT q.config, extract(epoch FROM (a.started_at-j.created_at))*1000 AS queue_ms "
                            "FROM rav_pilot_job j JOIN rav_pilot_quota_policy q ON q.version=j.policy_version "
                            "JOIN rav_pilot_job_attempt a ON a.job_id=j.id WHERE j.id=%s AND a.id=%s",
                            (lease.job_id, lease.attempt_id))
                row = cur.fetchone()
                cfg, queue_ms = row["config"], float(row["queue_ms"])
        emit("worker.claimed", job_id=lease.job_id, attempt_id=lease.attempt_id,
             upload_id=lease.payload.get("upload_id"), kind=lease.kind, queue_ms=queue_ms)
        interval = self.heartbeat_s or cfg["heartbeat_ms"] / 1000
        handler = self._handlers.get(lease.kind)
        cpu0 = time.process_time()
        if not lease.process_recorded:
            self.runtime.record_process(lease, identity())
        if handler is None:
            self.runtime.finish(lease, self._usage(cpu0, 0), {"code": "handler_unavailable",
                                                             "message": f"worker chưa có handler cho {lease.kind}",
                                                             "retryable": False})
            return True
        stop = threading.Event()        # hủy hoặc mất lease → handler dừng hợp tác
        lost = threading.Event()
        done = threading.Event()
        beat = threading.Thread(target=self._heartbeat, args=(lease, stop, lost, done, interval, cpu0), daemon=True)
        watch = threading.Thread(target=self._watch, args=(lease, cfg, stop, done), daemon=True)
        watch.start()
        beat.start()
        try:
            with correlation(job_id=lease.job_id, attempt_id=lease.attempt_id,
                             upload_id=lease.payload.get("upload_id"), kind=lease.kind), timing("worker.run_handler"):
                outputs = handler(lease, stop)
        except Exception as exc:
            done.set()
            beat.join(timeout=interval + 1)
            gpu_ms = int(getattr(exc, "gpu_ms", 0) or 0)
            self.runtime.record_stopped(lease, self._usage(cpu0, gpu_ms))
            if isinstance(exc, HandlerDetachedError):
                log.warning("job %s tách khỏi attempt: %s", lease.job_id, exc)
                return True
            if not lost.is_set():
                if isinstance(exc, ServiceError):   # lỗi nghiệp vụ có mã an toàn (vd upload_invalid)
                    error = {"code": exc.code, "message": exc.message, "retryable": exc.status >= 500}
                else:
                    log.exception("job %s lỗi", lease.job_id)
                    error = {"code": "internal_error", "message": type(exc).__name__, "retryable": True}
                self.runtime.finish(lease, self._usage(cpu0, gpu_ms), error)
                self._cleanup_vps(lease)
            return True
        done.set()
        beat.join(timeout=interval + 1)
        self.runtime.record_stopped(lease, self._usage(cpu0, int(outputs.get("gpu_ms", 0))))
        if lost.is_set():               # lease mất: không publish; supervisor đối soát slot
            return True
        usage = self._usage(cpu0, int(outputs.get("gpu_ms", 0)))
        if stop.is_set():
            self.runtime.finish(lease, usage, self._resource_error)
        else:
            try:
                with correlation(job_id=lease.job_id, attempt_id=lease.attempt_id,
                                 upload_id=lease.payload.get("upload_id")), timing("worker.publish"):
                    self.runtime.publish(lease, outputs, usage)
            except ServiceError as e:
                if e.code == "lease_lost":   # supervisor đối soát; không ghi gì thêm
                    return True
                self.runtime.finish(lease, usage, None if e.code == "cancel_requested" else
                                    {"code": e.code, "message": e.message, "retryable": e.status >= 500})
        self._cleanup_vps(lease)
        return True

    def _cleanup_vps(self, lease):
        if lease.kind != "VALIDATE_UPLOAD" or lease.payload.get("staging_source") != "VPS_DISK":
            return
        try:
            from ..store.pilot_vps_disk import VPSDiskUpload
            with self.runtime.db.backend() as cur:
                cur.execute("SELECT state FROM rav_pilot_upload WHERE id=%s", (lease.payload["upload_id"],))
                row = cur.fetchone()
            if row and row["state"] in ("READY", "FAILED", "ABORTED"):
                VPSDiskUpload(UUID(lease.payload["upload_id"])).cleanup()
        except Exception:
            log.exception("VPS cleanup after commit failed; staging sweeper will retry")

    def _heartbeat(self, lease: AttemptLease, stop: threading.Event, lost: threading.Event, done: threading.Event,
                   interval: float, cpu0: float) -> None:
        seq = 0
        while not done.wait(interval):
            seq += 1
            try:
                lease = self.runtime.heartbeat(lease, Progress(sequence=seq, phase="working", done=0, total=0))
                self.runtime.ensure_running_budget(lease, self._usage(cpu0, 0))
            except Exception as e:
                stop.set()
                if not isinstance(e, ServiceError) or e.code not in ("cancel_requested", "quota_exceeded"):
                    lost.set()
                    log.warning("job %s mất heartbeat; dừng handler", lease.job_id)
                return

    def _watch(self, lease: AttemptLease, cfg: dict, stop: threading.Event, done: threading.Event) -> None:
        """Deadline độc lập với DB/S3: vẫn dừng worker khi I/O bị treo."""
        stopped_at = None
        started = time.monotonic()
        deadline_ms = cfg["run_deadline_ms_by_kind"].get(lease.kind)
        limit = min(cfg["worker_rss_bytes"], self.rss_limit_bytes or cfg["worker_rss_bytes"])
        while not done.wait(0.1):
            now = time.monotonic()
            used = rss_bytes()
            if used is not None and used > limit:
                self._resource_error = {"code": "resource_limit", "message": "worker vượt RSS budget", "retryable": False}
                stop.set()
            if self.stop_event.is_set() or (deadline_ms and (now-started)*1000 >= deadline_ms):
                stop.set()
            if stop.is_set():
                stopped_at = stopped_at or now
                if (now-stopped_at)*1000 >= cfg["stop_deadline_ms"]:
                    log.error("job %s không dừng đúng deadline; thoát worker để supervisor phục hồi", lease.job_id)
                    os._exit(2)

    @staticmethod
    def _usage(cpu0: float, gpu_ms: int) -> Usage:
        return Usage(cpu_ms=max(0, round((time.process_time() - cpu0) * 1000)), gpu_ms=gpu_ms, state="FINAL",
                     evidence={"meter": "process_time"})
