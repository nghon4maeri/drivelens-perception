"""Tầng HTTP (entry). `app` = FastAPI, chạy bằng `python -m uvicorn src.rav.api:app` (cấu hình từ biến môi trường / `.env`).

Mỗi route chỉ gọi một command (commands/), không chứa logic. Endpoint: architecture §5.2; lỗi chuẩn §5.1:
mọi lỗi trả `{"error": {"code", "message"}}` — `ServiceError` theo mã của nó, sai dữ liệu 422 `validation_error`,
route không có 404 `not_found`, lỗi lạ 500 `internal_error` (không lộ chi tiết).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException

from ..errors import ServiceError

log = logging.getLogger(__name__)


def _error(status: int, code: str, message: str, details: dict | None = None) -> JSONResponse:
    return JSONResponse({"error": {**(details or {}), "code": code, "message": message}}, status_code=status)


def _validation_message(errors: list[dict]) -> str:
    return "; ".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in errors)


def create_app(config=None) -> FastAPI:
    """App local v1 (một người dùng). Pilot dùng `src.rav.api.pilot:app` (CONTRACTS §21.11); app này không mount `/api/v2`."""
    from ..commands import App
    from ..service.export_snapshot import cleanup_orphans
    from ..store.embedding_jobs import JobStoreError
    from ..store.s3 import CloudStorageError
    from . import media, routes, upload

    config = config or App.from_env()
    # Khởi tạo/migrate trước request thread: cached_property Python 3.12 không khoá lần đọc đầu.
    _ = config.db
    cleanup_orphans(config)
    @asynccontextmanager
    async def lifespan(api):
        from ..service.cloud_embedding import start_recovery
        start_recovery(config)
        try:
            yield
        finally:
            config.cloud_stop.set()

    api = FastAPI(title="DriveLens local API", version="1",lifespan=lifespan)
    for r in (upload.router(config), routes.router(config), media.router(config)):   # upload trước: POST /videos
        api.include_router(r)

    @api.get("/health", tags=["system"])
    async def health_check():
        return {
            "status": "ok",
            "service": "rav-api",
            "version": "1.0.0",
            "mode": getattr(config, "runtime_mode", "local"),
        }

    @api.get("/health/ready", tags=["system"])
    async def readiness_check():
        try:
            db_ready = config.db is not None
        except Exception:
            db_ready = False

        storage_ready = True
        if config.storage_backend == "s3":
            try:
                storage_ready = config.cloud_storage is not None
            except Exception:
                storage_ready = False

        status = "ok" if (db_ready and storage_ready) else "degraded"
        return {
            "status": status,
            "service": "rav-api",
            "checks": {
                "database": "ready" if db_ready else "error",
                "storage": config.storage_backend if storage_ready else "error",
            },
        }

    @api.exception_handler(ServiceError)
    async def service_error(request: Request, e: ServiceError):
        return _error(e.status, e.code, e.message, e.details)

    @api.exception_handler(CloudStorageError)
    async def storage_error(request: Request,e: CloudStorageError):
        return _error(503,'cloud_storage_unavailable',str(e))

    @api.exception_handler(JobStoreError)
    async def job_store_error(request: Request,e: JobStoreError):
        return _error(503,'job_database_unavailable',str(e))

    @api.exception_handler(ValidationError)
    async def validation_error(request: Request, e: ValidationError):
        return _error(422, "validation_error", _validation_message(e.errors(include_url=False)))

    @api.exception_handler(RequestValidationError)
    async def request_validation_error(request: Request, e: RequestValidationError):
        return _error(422, "validation_error", _validation_message(e.errors()))

    @api.exception_handler(HTTPException)
    async def http_error(request: Request, e: HTTPException):
        return _error(e.status_code, "not_found" if e.status_code == 404 else "http_error", str(e.detail))

    @api.exception_handler(Exception)
    async def internal_error(request: Request, e: Exception):
        log.exception("lỗi không lường trước ở %s %s", request.method, request.url.path)
        return _error(500, "internal_error", "lỗi máy chủ")

    return api


def __getattr__(name: str):
    """`src.rav.api:app` tạo lười (PEP 562): import module con như `api.pilot` không dựng app local, DB hay luồng recovery."""
    if name == "app":
        from dotenv import load_dotenv

        load_dotenv()
        globals()["app"] = created = create_app()
        return created
    raise AttributeError(name)
