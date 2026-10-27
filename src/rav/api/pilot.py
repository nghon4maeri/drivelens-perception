"""App FastAPI pilot: `/api/v2` + health, tùy chọn FE build (§21.18), không torch/numpy/pyav.

Chạy: `python -m uvicorn src.rav.api.pilot:app` với env `DATABASE_URL` (role `rav_pilot_api`), `SUPABASE_URL`, `SUPABASE_JWKS_URL`
(tùy chọn `SUPABASE_JWT_SECRET` cho HS256 legacy), `RAV_S3_BUCKET` + `RAV_PILOT_S3_PREFIX` (upload/media). Không fallback SQLite, không tự migrate (operator chạy `scripts/pilot_migrate.py`);
schema chưa đúng version → endpoint nghiệp vụ trả 503 `schema_not_ready`, `/health` vẫn sống.
Lỗi: `{"error": {"code", "message", "retryable", "request_id", ...details}}`; mọi response `Cache-Control: no-store`.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException

from ..errors import ServiceError
from ..store.pilot_db import PilotDB
from ..store.pilot_storage import PilotStorage
from ..timing import correlation, emit, timing
from . import routes_v2
from .auth import AuthConfig

log = logging.getLogger(__name__)


def auth_config_from_env() -> AuthConfig:
    base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    return AuthConfig(jwks_url=os.environ.get("SUPABASE_JWKS_URL") or (f"{base}/auth/v1/.well-known/jwks.json" if base else None),
                      jwt_secret=os.environ.get("SUPABASE_JWT_SECRET") or None,
                      expected_issuer=f"{base}/auth/v1" if base else None)


def create_pilot_app(database_url: str | None = None, auth_config: AuthConfig | None = None,
                     raw_jwks: dict | None = None, storage_factory: Callable[[], PilotStorage] | None = None) -> FastAPI:
    url = database_url if database_url is not None else os.environ.get("DATABASE_URL", "")
    auth_cfg = auth_config or auth_config_from_env()
    state = {"db": None, "collector": None}
    lock = threading.Lock()

    def get_db() -> PilotDB:
        """PilotDB đã kiểm schema; kiểm lại tới khi đạt (operator có thể migrate sau khi app chạy)."""
        if state["db"] is None:
            with lock:
                if state["db"] is None:
                    db = PilotDB(url)
                    db.ensure_schema()
                    state["db"] = db
                    from ..telemetry import start_collector
                    state['collector'] = start_collector(db, 'api')
        return state["db"]

    @asynccontextmanager
    async def lifespan(api):
        yield
        if state['collector']:
            state['collector'].close()

    api = FastAPI(title="DriveLens pilot API", version="2", lifespan=lifespan)
    origins = [x.strip().rstrip("/") for x in os.environ.get("RAV_FRONTEND_ORIGIN", "").split(",") if x.strip()]
    for origin in origins:
        parts = urlsplit(origin)
        if (parts.scheme not in ("https", "http") or not parts.hostname or "*" in parts.netloc
                or parts.username or parts.password or parts.path or parts.query or parts.fragment):
            raise ValueError("RAV_FRONTEND_ORIGIN cần origin HTTP(S) explicit, không wildcard/path/credential")
    if origins:
        api.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "PUT", "OPTIONS"],
                           allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "Upload-Offset"])
    api.include_router(routes_v2.router(get_db, auth_cfg, raw_jwks=raw_jwks,
                                        get_storage=storage_factory or PilotStorage.from_env))

    @api.middleware("http")
    async def request_id_and_no_store(request: Request, call_next):
        request.state.request_id = str(uuid4())
        with correlation(request_id=request.state.request_id), timing("api.request", status=None):
            response = await call_next(request)
            route = request.scope.get("route")
            emit("api.response", status=response.status_code, kind=getattr(route, "name", "unmatched"))
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @api.get("/health", tags=["system"])
    def health():
        return {"status": "ok", "service": "rav-api-v2", "version": "2.0.0", "mode": "pilot"}

    @api.get("/api/v2/health", tags=["system"])
    def v2_health():
        return {"status": "ok", "service": "rav-api-v2", "version": "2.0.0", "mode": "pilot"}

    @api.get("/health/ready", tags=["system"])
    def ready():
        try:
            get_db()
            return {"status": "ok", "checks": {"database": "ready", "schema": "ready"}}
        except ServiceError as e:
            return JSONResponse({"status": "not_ready", "checks": {"database": e.code}}, status_code=503)

    def envelope(request: Request, status: int, code: str, message: str, details: dict | None = None) -> JSONResponse:
        emit('api.error', status=status, error_code=code)
        body = {**(details or {}), "code": code, "message": message, "retryable": status >= 500 or status == 429,
                "request_id": getattr(request.state, "request_id", None)}
        return JSONResponse({"error": body}, status_code=status, headers={"Cache-Control": "private, no-store"})

    @api.exception_handler(ServiceError)
    async def service_error(request: Request, e: ServiceError):
        response = envelope(request, e.status, e.code, e.message, e.details)
        if e.status == 429:
            response.headers['Retry-After'] = str(e.details.get('retry_after', 1))
        return response

    @api.exception_handler(RequestValidationError)
    async def request_validation(request: Request, e: RequestValidationError):
        msg = "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors())
        return envelope(request, 422, "validation_error", msg)

    @api.exception_handler(ValidationError)
    async def validation(request: Request, e: ValidationError):
        return envelope(request, 422, "validation_error", "dữ liệu không hợp lệ")

    @api.exception_handler(HTTPException)
    async def http_error(request: Request, e: HTTPException):
        return envelope(request, e.status_code, "not_found" if e.status_code == 404 else "http_error", str(e.detail))

    @api.exception_handler(Exception)
    async def internal(request: Request, e: Exception):
        log.exception("lỗi không lường trước ở %s %s", request.method, request.url.path)
        return envelope(request, 500, "internal_error", "lỗi máy chủ")

    from .web_static import mount_web

    mount_web(api)
    return api


app = create_pilot_app()
