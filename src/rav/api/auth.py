"""Xác thực JWT Supabase và dựng ActorContext (CONTRACTS §21.1, §21.11; architecture §11.3).

1. Lấy Bearer token từ header `Authorization` (không đọc owner/actor/role từ body hay header khác).
2. Xác minh chữ ký bằng JWKS (khóa RSA hoặc EC); HS256 chỉ khi operator cấp riêng `SUPABASE_JWT_SECRET` (khóa JWT legacy).
3. Kiểm `exp`, issuer `<SUPABASE_URL>/auth/v1`, audience `authenticated`, `sub` là UUID.
4. Profile phải tồn tại và `active` (thiếu → 401 `unauthorized`, tắt → 401 `account_disabled`).
Lỗi xác thực luôn là 401 `unauthorized` với thông điệp an toàn; JWKS không tải được → 503 `dependency_unavailable`.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from typing import Any
from uuid import UUID, uuid4

import jwt
from pydantic import BaseModel, Field

from ..actor import ActorContext
from ..errors import ServiceError

__all__ = ["ActorContext", "AuthConfig", "extract_actor_context", "verify_jwt_token"]

log = logging.getLogger(__name__)
ASYMMETRIC = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512")


class AuthConfig(BaseModel):
    jwks_url: str | None = None
    jwt_secret: str | None = None          # chỉ khóa JWT HS256 legacy, không phải secret API key
    expected_issuer: str | None = None
    expected_audience: str | None = "authenticated"
    leeway_s: int = Field(default=30, ge=0)


class JWKSCache:
    """Cache khóa công khai theo `kid` với TTL; khóa lạ thì tải lại một lần (xoay khóa)."""

    def __init__(self, ttl_seconds: float = 3600.0):
        self.ttl = ttl_seconds
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at = 0.0

    def get(self, kid: str | None, jwks_url: str) -> jwt.PyJWK:
        if not self._keys or time.time() - self._fetched_at > self.ttl or kid not in self._keys:
            self.refresh(jwks_url)
        key = self._keys.get(kid)
        if key is None:
            raise ServiceError("unauthorized", "token ký bằng khóa không xác định", 401)
        return key

    def refresh(self, jwks_url: str) -> None:
        try:
            req = urllib.request.Request(jwks_url, headers={"User-Agent": "RAV26-Auth/1.0"})
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                self._keys = _parse_jwks(json.loads(resp.read().decode("utf-8")))
            self._fetched_at = time.time()
        except Exception as e:
            log.warning("không tải được JWKS: %s", type(e).__name__)
            if not self._keys:
                raise ServiceError("dependency_unavailable", "dịch vụ xác thực tạm thời không khả dụng", 503) from e


def _parse_jwks(data: dict) -> dict[str, jwt.PyJWK]:
    keys = {}
    for jwk in data.get("keys", []):
        if jwk.get("kid") and jwk.get("alg", "ES256") in ASYMMETRIC:
            try:
                keys[jwk["kid"]] = jwt.PyJWK(jwk)
            except jwt.PyJWTError:
                log.warning("bỏ qua JWK không đọc được kid=%s", jwk.get("kid"))
    return keys


_jwks_cache = JWKSCache()


def verify_jwt_token(token: str, config: AuthConfig, raw_jwks: dict | None = None) -> dict:
    """Trả claims đã xác minh; mọi lỗi token → 401 `unauthorized`."""
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as e:
        raise ServiceError("unauthorized", "token không đúng định dạng", 401) from e
    alg = header.get("alg")
    if alg in ASYMMETRIC:
        if raw_jwks is not None:
            key = _parse_jwks(raw_jwks).get(header.get("kid"))
            if key is None:
                raise ServiceError("unauthorized", "token ký bằng khóa không xác định", 401)
        elif config.jwks_url:
            key = _jwks_cache.get(header.get("kid"), config.jwks_url)
        else:
            raise ServiceError("dependency_unavailable", "thiếu cấu hình JWKS", 503)
        signing_key: Any = key.key
    elif alg == "HS256" and config.jwt_secret:
        signing_key = config.jwt_secret
    else:
        raise ServiceError("unauthorized", "thuật toán ký không được chấp nhận", 401)
    try:
        return jwt.decode(token, key=signing_key, algorithms=[alg], issuer=config.expected_issuer,
                          audience=config.expected_audience, leeway=config.leeway_s,
                          options={"require": ["exp", "sub"], "verify_iss": bool(config.expected_issuer),
                                   "verify_aud": config.expected_audience is not None})
    except jwt.ExpiredSignatureError as e:
        raise ServiceError("unauthorized", "token đã hết hạn", 401) from e
    except jwt.PyJWTError as e:
        raise ServiceError("unauthorized", "token không hợp lệ", 401) from e


def _extract_display_name_hint(claims: dict) -> str:
    """Lấy gợi ý tên hiển thị từ JWT metadata/email (chỉ hiển thị, không cấp quyền — §21.34)."""
    meta = claims.get("user_metadata")
    if isinstance(meta, dict):
        for key in ("display_name", "full_name", "name"):
            val = meta.get(key)
            if isinstance(val, str) and val.strip():
                clean = "".join(ch for ch in val if ch.isprintable()).strip()[:80]
                if clean:
                    return clean
    email = claims.get("email")
    if isinstance(email, str) and "@" in email:
        prefix = email.split("@", 1)[0].strip()
        if prefix:
            clean = "".join(ch for ch in prefix if ch.isprintable()).strip()[:80]
            if clean:
                return clean
    return ""


def extract_actor_context(authorization_header: str | None, config: AuthConfig, profile_store: Any,
                          raw_jwks: dict | None = None) -> ActorContext:
    """Pilot luôn bắt buộc token; ưu tiên `profile_store.ensure_profile(uuid, display_name)` (§21.34), fallback `get_profile`."""
    parts = (authorization_header or "").strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise ServiceError("unauthorized", "cần header Authorization: Bearer <token>", 401)
    claims = verify_jwt_token(parts[1], config, raw_jwks=raw_jwks)
    try:
        user_id = UUID(str(claims["sub"]))
    except ValueError:
        raise ServiceError("unauthorized", "token không định danh người dùng hợp lệ", 401) from None
    ensure = getattr(profile_store, "ensure_profile", None)
    if callable(ensure):
        profile = ensure(user_id, _extract_display_name_hint(claims))
    else:
        profile = profile_store.get_profile(user_id)
    if profile is None:
        raise ServiceError("unauthorized", "tài khoản chưa được cấp quyền dùng pilot", 401)
    if not profile["active"]:
        raise ServiceError("account_disabled", "tài khoản đã bị vô hiệu hóa", 401)
    return ActorContext(user_id=user_id, request_id=uuid4())

