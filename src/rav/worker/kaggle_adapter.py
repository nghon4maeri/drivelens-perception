"""Vòng đời GPU Kaggle theo slot cho worker pilot (CONTRACTS §21.14, runtime spec §7.1, #94).

Mỗi slot GPU có một notebook private cố định `<account>/rav26-pilot-<slot_key>`. SDK không tìm session theo marker, nên
`reconcile_launch()` đọc log endpoint theo ID session tương tác (#113): stream đang chạy → FOUND, log lưu khi kết thúc →
ABSENT_VERIFIED, thiếu ID/lỗi API → UNKNOWN (không bao giờ đoán ABSENT). Provider (`KaggleGPUClient` hoặc bản giả trong test) cần:
`account()`, `ensure_named_kernel(slug)`, `session_status(slug)`, `create_session(slug, ...)`, `wait_session`, `stop_session`,
`quota_statistics()`. Credential chỉ ở worker.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

log = logging.getLogger(__name__)
RUNNING = {"QUEUED", "RUNNING", "CANCEL_REQUESTED"}
ENDED = {"COMPLETE", "ERROR", "CANCEL_ACKNOWLEDGED", "NEW_SCRIPT"}


class LaunchResolution(BaseModel):
    """Kết quả đối soát session GPU của slot trên provider."""
    model_config = ConfigDict(extra="forbid")

    status: Literal["FOUND", "ABSENT_VERIFIED", "UNKNOWN"]
    evidence: dict[str, Any] | None = None
    reason: str | None = None


def build_marker(deployment: str, job_id, attempt_id, fence: int) -> str:
    return f"pilot:{deployment}:{job_id}:{attempt_id}:{fence}"


class KaggleSlotAdapter:
    def __init__(self, provider: Any, account: str, slot_key: str, session_lookup=None):
        self.provider, self.account, self.slot_key = provider, account, slot_key
        self.session_lookup = session_lookup
        self.kernel = f"{account}/rav26-pilot-{slot_key}"

    def ensure_kernel(self) -> str:
        return self.provider.ensure_named_kernel(self.kernel)

    def reconcile_launch(self, session_id=None) -> LaunchResolution:
        try:
            if hasattr(self.provider, "interactive_session_status"):
                session_id = session_id or (self.session_lookup() if self.session_lookup else None)
                if not session_id:
                    return LaunchResolution(status="UNKNOWN", reason="interactive_session_id_unverified")
                status = self.provider.interactive_session_status(session_id)
            else:  # simulated providers expose their exact session state directly
                status = self.provider.session_status(self.kernel)
        except Exception as exc:   # không phân biệt được "không có" với "không đọc được"
            return LaunchResolution(status="UNKNOWN", reason=f"provider_status_error:{type(exc).__name__}")
        evidence = {"kernel": self.kernel, "session_id": str(session_id) if session_id else None,
                    "status": status, "observed_at": time.time()}
        if status in RUNNING:
            return LaunchResolution(status="FOUND", evidence=evidence)
        if status in ENDED:
            return LaunchResolution(status="ABSENT_VERIFIED", evidence=evidence)
        return LaunchResolution(status="UNKNOWN", evidence=evidence, reason="status_unrecognized")

    def create(self):
        return self.provider.create_session(self.kernel, accelerator="t4", enable_internet=True, wait=False)

    def stop_and_verify(self, session_id: str | int, deadline_s: float, poll_s: float = 3.0) -> bool:
        """Hủy session rồi chờ provider xác nhận đã kết thúc; HTTP stop được nhận chưa đủ (runtime spec §7.1)."""
        try:
            self.provider.stop_session(session_id)
        except Exception:
            log.warning("hủy session slot %s lỗi; sẽ kiểm trạng thái", self.slot_key)
        deadline = time.monotonic() + deadline_s
        while True:
            if self.reconcile_launch(session_id).status == "ABSENT_VERIFIED":
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(poll_s, max(0.0, deadline - time.monotonic())))

    def quota(self) -> dict:
        return self.provider.quota_statistics()
