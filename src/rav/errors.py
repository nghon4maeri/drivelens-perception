"""Lỗi nghiệp vụ dùng chung cho local v1 và pilot v2 (mã + HTTP status + chi tiết an toàn).

Tách khỏi `service/__init__.py` để app pilot import nhẹ (không kéo pipeline/numpy, CONTRACTS §21.11); `service` re-export cùng class.
"""

from __future__ import annotations


class ServiceError(Exception):
    def __init__(self, code: str, message: str, status: int = 422, details: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status
        self.details = details or {}
