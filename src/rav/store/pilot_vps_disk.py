"""Dịch vụ lưu trữ và ghi nhận upload đĩa VPS (§21.30, VPS-Upload-Ingest #139).

Quản lý luồng ghi tuần tự file `.part` theo chunk 16 MiB, buffer stream 1 MiB,
cơ chế fsync bền vững và cập nhật atomic metadata `.meta`.
Tự động phục hồi sau sự cố/crash (truncate phần chưa được ack về durable offset).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from ..errors import ServiceError

DEFAULT_VPS_CHUNK_SIZE = 16 * 1024 * 1024   # 16 MiB
STREAM_BUFFER_SIZE = 1 * 1024 * 1024        # 1 MiB

_LOCKS: dict[UUID, threading.RLock] = {}
_LOCKS_MUTEX = threading.Lock()


def _get_upload_lock(upload_id: UUID) -> threading.RLock:
    with _LOCKS_MUTEX:
        if upload_id not in _LOCKS:
            _LOCKS[upload_id] = threading.RLock()
        return _LOCKS[upload_id]


def get_vps_upload_dir() -> Path:
    d = Path(os.environ.get("RAV_VPS_UPLOAD_DIR", "data/uploads"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_vps_upload_secret() -> bytes:
    sec = os.environ.get("RAV_VPS_UPLOAD_SECRET", "")
    if len(sec) < 32:
        raise ServiceError("dependency_unavailable", "cần RAV_VPS_UPLOAD_SECRET tối thiểu 32 ký tự", 503)
    return sec.encode("utf-8")


def create_vps_capability(upload_id: UUID, owner_id: UUID, declared_bytes: int, expires_at_epoch: float,
                          purpose: str = "upload") -> str:
    payload = {
        "uid": str(upload_id),
        "owner": str(owner_id),
        "bytes": declared_bytes,
        "exp": float(expires_at_epoch),
        "purpose": purpose,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    b64 = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    sig = hmac.new(get_vps_upload_secret(), b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{b64}.{sig}"


def verify_vps_capability(token: str, upload_id: UUID, purpose: str = "upload") -> dict:
    if not token or len(token) > 4096 or "." not in token:
        raise ServiceError("unauthorized", "Capability token không hợp lệ", 401)
    b64, sig = token.split(".", 1)
    expected_sig = hmac.new(get_vps_upload_secret(), b64.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        raise ServiceError("unauthorized", "Chữ ký capability token không hợp lệ", 401)
    # decode payload
    padded = b64 + "=" * (-len(b64) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except Exception:
        raise ServiceError("unauthorized", "Payload capability token hỏng", 401)
    if not isinstance(payload, dict):
        raise ServiceError("unauthorized", "Payload capability token hỏng", 401)
    if payload.get("uid") != str(upload_id) or payload.get("purpose") != purpose:
        raise ServiceError("forbidden", "Capability token không thuộc về upload này", 403)
    if (type(payload.get("bytes")) is not int or payload["bytes"] <= 0
            or not isinstance(payload.get("exp"), (int, float)) or not math.isfinite(payload["exp"])):
        raise ServiceError("unauthorized", "Payload capability token hỏng", 401)
    if time.time() > payload["exp"]:
        raise ServiceError("upload_invalid", "Capability token đã hết hạn", 409)
    return payload


class VPSDiskUpload:
    def __init__(self, upload_id: UUID, work_dir: Path | None = None):
        self.upload_id = upload_id
        self.dir = work_dir or get_vps_upload_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.part_path = self.dir / f"{upload_id}.part"
        self.meta_path = self.dir / f"{upload_id}.meta"
        self.meta_tmp = self.dir / f"{upload_id}.meta.tmp"
        self.closed_path = self.dir / f"{upload_id}.closed"

    @contextmanager
    def locked(self):
        """Serialize status/write/seal/cleanup across HTTP threads and worker processes."""
        with _get_upload_lock(self.upload_id), (self.dir / f"{self.upload_id}.lock").open("a+b") as handle:
            if os.name == "nt":
                import msvcrt
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def get_status(self, declared_bytes: int) -> dict:
        with self.locked():
            return self._status(declared_bytes)

    def _status(self, declared_bytes: int) -> dict:
        if self.closed_path.exists():
            raise ServiceError("upload_invalid", "upload đã đóng", 409)
        meta_offset = 0
        data = {}
        if self.meta_path.exists():
            try:
                data = json.loads(self.meta_path.read_text(encoding="utf-8"))
                meta_offset = int(data.get("offset", 0))
            except (ValueError, TypeError, AttributeError):
                raise ServiceError("upload_invalid", "metadata upload hỏng; giữ nguyên bytes để đối soát", 409) from None

        # Crash recovery & consistency check
        if self.part_path.exists():
            phys_size = self.part_path.stat().st_size
            if phys_size > meta_offset:
                # Truncate back to last durable ack offset
                with self.part_path.open("r+b") as f:
                    f.truncate(meta_offset)
                    f.flush()
                    os.fsync(f.fileno())
            elif phys_size < meta_offset:
                meta_offset = phys_size
                self._write_meta_atomic(meta_offset, declared_bytes)
        elif meta_offset > 0:
            meta_offset = 0
            self._write_meta_atomic(0, declared_bytes)

        return {
            "upload_id": str(self.upload_id),
            "offset": meta_offset,
            "declared_bytes": declared_bytes,
            "completed": meta_offset >= declared_bytes,
            "sealed": bool(data.get("sealed", False)) if self.meta_path.exists() else False,
        }

    def _write_meta_atomic(self, offset: int, declared_bytes: int, *, sealed: bool = False) -> None:
        data = {
            "upload_id": str(self.upload_id),
            "offset": offset,
            "declared_bytes": declared_bytes,
            "updated_at": time.time(),
            "sealed": sealed,
        }
        self.meta_tmp.write_text(json.dumps(data), encoding="utf-8")
        with self.meta_tmp.open("r+b") as f:
            f.flush()
            os.fsync(f.fileno())
        os.replace(self.meta_tmp, self.meta_path)
        if os.name != "nt":
            descriptor = os.open(self.dir, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def seal(self, declared_bytes: int) -> dict:
        with self.locked():
            status = self._status(declared_bytes)
            if status["offset"] != declared_bytes:
                raise ServiceError("upload_invalid", "chưa nhận đủ file trên đĩa VPS", 409)
            self._write_meta_atomic(declared_bytes, declared_bytes, sealed=True)
            return {**status, "sealed": True}

    def write_chunk(self, declared_bytes: int, offset: int, stream: BinaryIO | bytes) -> int:
        with self.locked():
            status = self._status(declared_bytes)
            if status["sealed"]:
                raise ServiceError("upload_invalid", "upload đã seal", 409)
            expected_offset = status["offset"]
            if offset != expected_offset:
                raise ServiceError(
                    "offset_mismatch",
                    f"Offset không khớp: mong đợi {expected_offset}, nhận được {offset}",
                    409,
                    {"expected_offset": expected_offset},
                )
            if expected_offset >= declared_bytes:
                return expected_offset

            mode = "r+b" if self.part_path.exists() else "w+b"
            with self.part_path.open(mode) as f:
                f.seek(offset)
                total_written = 0
                if isinstance(stream, (bytes, bytearray, memoryview)):
                    if len(stream) > DEFAULT_VPS_CHUNK_SIZE:
                        raise ServiceError("upload_invalid", "chunk vượt 16 MiB", 413)
                    if offset + len(stream) > declared_bytes:
                        raise ServiceError("upload_invalid", "Dữ liệu vượt quá declared_bytes", 409)
                    f.write(stream)
                    total_written = len(stream)
                else:
                    while True:
                        buf = stream.read(STREAM_BUFFER_SIZE)
                        if not buf:
                            break
                        if offset + total_written + len(buf) > declared_bytes:
                            f.truncate(offset)
                            f.flush()
                            os.fsync(f.fileno())
                            raise ServiceError("upload_invalid", "Dữ liệu vượt quá declared_bytes", 409)
                        f.write(buf)
                        total_written += len(buf)
                        if total_written > DEFAULT_VPS_CHUNK_SIZE:
                            f.truncate(offset)
                            raise ServiceError("upload_invalid", "chunk vượt 16 MiB", 413)

                f.flush()
                os.fsync(f.fileno())

            new_offset = offset + total_written
            self._write_meta_atomic(new_offset, declared_bytes)
            return new_offset

    def cleanup(self) -> None:
        with self.locked():
            self.closed_path.touch()
            self.part_path.unlink(missing_ok=True)
            self.meta_path.unlink(missing_ok=True)
            self.meta_tmp.unlink(missing_ok=True)
