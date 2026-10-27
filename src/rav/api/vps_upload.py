"""Contabo upload data/control endpoints: bounded chunk spool, durable locked append.

Browser uses upload capability for GET/PUT; Render uses short-lived purpose-bound
capabilities for seal/cleanup. Mount at /api/v2 on the Contabo HTTPS listener.
"""
from __future__ import annotations

import tempfile
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request
from starlette.concurrency import run_in_threadpool

from ..errors import ServiceError
from ..store.pilot_vps_disk import DEFAULT_VPS_CHUNK_SIZE, STREAM_BUFFER_SIZE, VPSDiskUpload, verify_vps_capability

router = APIRouter(prefix="/vps-uploads", tags=["vps-upload"])


def _token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise ServiceError("unauthorized", "Thiếu Authorization capability header", 401)
    return authorization[7:].strip()


@router.get("/{upload_id}")
def get_vps_upload_status(upload_id: UUID, authorization: str | None = Header(None)):
    cap = verify_vps_capability(_token(authorization), upload_id)
    return VPSDiskUpload(upload_id).get_status(cap["bytes"])


@router.put("/{upload_id}/chunks")
async def write_vps_upload_chunk(upload_id: UUID, request: Request,
                                 authorization: str | None = Header(None),
                                 upload_offset: int | None = Header(None, alias="Upload-Offset"),
                                 offset: int | None = Query(None)):
    cap = verify_vps_capability(_token(authorization), upload_id)
    target = upload_offset if upload_offset is not None else offset
    if target is None or target < 0:
        raise ServiceError("validation_error", "Thiếu hoặc sai Upload-Offset header", 422)
    vps = VPSDiskUpload(upload_id)
    # Do not mutate shared .part across await points: concurrent GET/retry may recover it.
    with tempfile.TemporaryFile(dir=vps.dir) as spool:
        count = 0
        async for block in request.stream():
            count += len(block)
            if count > DEFAULT_VPS_CHUNK_SIZE or target + count > cap["bytes"]:
                raise ServiceError("upload_invalid", "chunk vượt giới hạn", 413)
            for start in range(0, len(block), STREAM_BUFFER_SIZE):
                spool.write(memoryview(block)[start:start + STREAM_BUFFER_SIZE])
        if count == 0:
            raise ServiceError("upload_invalid", "chunk rỗng", 422)
        spool.seek(0)
        new_offset = await run_in_threadpool(vps.write_chunk, cap["bytes"], target, spool)
    return {"status": "ok", "offset": new_offset}


@router.post("/{upload_id}/control/{action}")
def control_vps_upload(upload_id: UUID, action: str, authorization: str | None = Header(None)):
    if action not in ("seal", "cleanup"):
        raise ServiceError("not_found", "không có thao tác này", 404)
    cap = verify_vps_capability(_token(authorization), upload_id, purpose=action)
    vps = VPSDiskUpload(upload_id)
    if action == "seal":
        return vps.seal(cap["bytes"])
    vps.cleanup()
    return {"closed": True}
