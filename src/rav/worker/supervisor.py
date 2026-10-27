"""Supervisor của worker pilot (runtime spec §7): thu hồi lease hết hạn, hết hạn hàng đợi, đối soát slot bị cách ly.

`run_reconciliation()` gọi `recover_expired_leases()` và (nếu có) dọn staging upload quá hạn; slot QUARANTINED chỉ được trả bằng `reconcile_slot()` kèm bằng chứng
process cũ đã dừng (host boot / process start token / group exit) — supervisor không tự đoán.
Slot GPU (§21.14) còn cần session Kaggle của slot đã kết thúc (`reconcile_launch` = ABSENT_VERIFIED); FOUND có handle → hủy rồi
xét lại vòng sau; FOUND không handle / UNKNOWN → giữ QUARANTINED. `refresh_gpu_budget()` ghi snapshot quota từ Kaggle SDK.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..store import pilot_gpu
from ..store.pilot_runtime import PilotRuntime, SlotEvidence, WorkerContext
from ..store.pilot_uploads import PilotUploads
from .process import stopped

log = logging.getLogger(__name__)


class PilotSupervisor:
    def __init__(self, runtime: PilotRuntime, worker_ctx: WorkerContext, uploads: PilotUploads | None = None,
                 gpu: dict[str, Any] | None = None):
        self.runtime, self.worker_ctx, self.uploads = runtime, worker_ctx, uploads
        self.gpu = gpu or {}   # slot_key → KaggleSlotAdapter

    def refresh_gpu_budget(self) -> list[Any]:
        """Một snapshot cho mỗi account Kaggle của các slot; lỗi đọc → không ghi (snapshot cũ sẽ hết tươi, job GPU chờ)."""
        written = []
        for account, adapter in {a.account: a for a in self.gpu.values()}.items():
            try:
                q = adapter.quota()
            except Exception:
                log.warning("không đọc được quota GPU của account %s", account)
                continue
            remaining = max(0, q["total_ms"] - q["used_ms"] - q["reserved_ms"])
            refresh = q.get("refresh_time")
            written.append(pilot_gpu.record_snapshot(
                self.runtime.db, provider_account=account, source="KAGGLE_SDK", remaining_ms=remaining, reset_at=refresh,
                evidence_ref=f"kaggle-sdk:quota:{int(time.time())}",
                evidence={"total_ms": q["total_ms"], "used_ms": q["used_ms"], "reserved_ms": q["reserved_ms"],
                          "refresh_time": refresh.isoformat() if refresh else None}))
        return written

    def _gpu_stopped(self, att: dict) -> bool:
        """Session Kaggle của attempt đã chắc chắn kết thúc (hoặc chưa từng tạo)."""
        if att["launch_state"] in ("NONE", "STOPPED"):
            return True
        adapter = self.gpu.get(att["slot_key"])
        if adapter is None:
            return False
        state = adapter.reconcile_launch(att["session_id"])
        if state.status == "ABSENT_VERIFIED":
            with self.runtime.db.backend() as cur:
                pilot_gpu.mark_stopped_in_tx(cur, att["id"], state.evidence or {})
                cur.execute("SELECT usage FROM rav_pilot_job_attempt WHERE id=%s", (att["id"],))
                att["usage"] = cur.fetchone()["usage"]
            return True
        if state.status == "FOUND" and att["session_id"]:
            try:
                adapter.provider.stop_session(att["session_id"])
            except Exception:
                log.warning("hủy session GPU của slot %s lỗi; thử lại vòng sau", att["slot_key"])
        return False

    def run_reconciliation(self) -> list[dict[str, Any]]:
        recovered = self.runtime.recover_expired_leases()
        with self.runtime.db.backend() as cur:
            cur.execute("SELECT s.slot_key,s.generation,s.kind AS slot_kind,a.* FROM rav_pilot_resource_slot s "
                        "JOIN rav_pilot_job_attempt a ON a.id=s.attempt_id WHERE s.state='QUARANTINED'")
            attempts = cur.fetchall()
        for att in attempts:
            proof = att["process_group"] or {}
            verified = proof.get("handler_stopped") is True or stopped(
                proof, str(att["host_boot_id"]), str(self.worker_ctx.host_boot_id))
            if not verified or not self._gpu_stopped(att):
                continue
            if att["slot_kind"] == "GPU" and self.uploads is not None:   # object trao đổi GPU theo tên cụ thể
                storage = self.uploads.storage()
                deleted = True
                for name in ("progress.json", "result.npz"):
                    try:
                        storage.delete(storage.job_key(att["job_id"], att["id"], name))
                    except Exception:
                        deleted = False
                        log.warning("chưa xóa được object GPU của attempt %s", att["id"])
                if not deleted:
                    continue
            if self.uploads is not None:
                from ..store.pilot_objects import cleanup_attempt
                if not cleanup_attempt(self.runtime.db, self.uploads.storage(), att["id"], process_stopped=True):
                    continue
            usage = att["usage"] or {}
            try:
                recovered.append(self.reconcile(att["slot_key"], att["generation"], SlotEvidence(
                    process_exit_verified=True, cpu_ms=usage.get("cpu_ms"), gpu_ms=usage.get("gpu_ms"),
                    proof={"handler_or_process_stopped": True})))
            except Exception:
                log.exception("đối soát slot %s sẽ thử lại", att["slot_key"])
        if self.uploads is not None:   # staging quá hạn + grace: xóa object, trả dung lượng (DEP4 #91)
            recovered += [{"upload_id": u, "action": "staging_swept"} for u in self.uploads.sweep_staging()]
        if recovered:
            log.info("supervisor thu hồi %d mục: %s", len(recovered), recovered)
        return recovered

    def reconcile(self, slot_key: str, generation: int, evidence: SlotEvidence) -> dict[str, Any]:
        return self.runtime.reconcile_slot(self.worker_ctx, slot_key, generation, evidence)
