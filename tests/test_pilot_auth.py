"""Xác thực JWT pilot (DEP2F #89): JWKS EC/RSA, HS256 legacy chỉ khi cấu hình, issuer/audience/exp, profile bắt buộc.

Không cần Postgres (profile store giả). Chạy: `python -m pytest tests/test_pilot_auth.py -q`.
"""

from __future__ import annotations

import json
import time
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from src.rav.api.auth import AuthConfig, extract_actor_context, verify_jwt_token
from src.rav.errors import ServiceError

ISS = "https://test.supabase.co/auth/v1"


def keypair(kind: str):
    key = ec.generate_private_key(ec.SECP256R1()) if kind == "ec" else rsa.generate_private_key(65537, 2048)
    alg = "ES256" if kind == "ec" else "RS256"
    algo = jwt.algorithms.ECAlgorithm if kind == "ec" else jwt.algorithms.RSAAlgorithm
    jwk = json.loads(algo.to_jwk(key.public_key()))
    jwk.update(kid=f"kid-{kind}", alg=alg, use="sig")
    return key, alg, {"keys": [jwk]}


def token(key, alg, kid, sub, **claims) -> str:
    payload = {"sub": sub, "iss": ISS, "aud": "authenticated", "exp": int(time.time()) + 3600, "role": "authenticated", **claims}
    return jwt.encode(payload, key, algorithm=alg, headers={"kid": kid})


class Profiles:
    def __init__(self, rows):
        self.rows = rows

    def get_profile(self, uid):
        return self.rows.get(uid)


CFG = AuthConfig(expected_issuer=ISS)


@pytest.mark.parametrize("kind", ["ec", "rsa"])
def test_valid_jwks_token(kind):
    key, alg, jwks = keypair(kind)
    uid = uuid4()
    a = extract_actor_context(f"Bearer {token(key, alg, f'kid-{kind}', str(uid))}", CFG,
                              Profiles({uid: {"active": True}}), raw_jwks=jwks)
    assert a.user_id == uid and a.actor_string == f"human:{uid}"


def test_rejections_are_401_unauthorized():
    key, alg, jwks = keypair("ec")
    other, _, _ = keypair("ec")
    sub = str(uuid4())
    bad = {
        "expired": token(key, alg, "kid-ec", sub, exp=int(time.time()) - 120),
        "wrong_aud": token(key, alg, "kid-ec", sub, aud="anon"),
        "wrong_iss": token(key, alg, "kid-ec", sub, iss="https://evil/auth/v1"),
        "forged": token(other, alg, "kid-ec", sub),
        "unknown_kid": token(key, alg, "kid-nope", sub),
        "not_uuid": token(key, alg, "kid-ec", "admin"),
        "garbage": "abc.def",
        "none_alg": jwt.encode({"sub": sub, "iss": ISS, "aud": "authenticated", "exp": int(time.time()) + 60}, None,
                               algorithm="none"),
        "hs256_without_secret": jwt.encode({"sub": sub, "iss": ISS, "aud": "authenticated", "exp": int(time.time()) + 60},
                                           "x" * 40, algorithm="HS256"),
    }
    for name, tok in bad.items():
        with pytest.raises(ServiceError) as e:
            extract_actor_context(f"Bearer {tok}", CFG, Profiles({UUID(sub): {"active": True}}), raw_jwks=jwks)
        assert (e.value.status, e.value.code) == (401, "unauthorized"), name


def test_missing_header_and_profile_rules():
    key, alg, jwks = keypair("ec")
    uid = uuid4()
    tok = token(key, alg, "kid-ec", str(uid))
    for header in (None, "", "Basic abc", f"Bearer {tok} extra"):
        with pytest.raises(ServiceError) as e:
            extract_actor_context(header, CFG, Profiles({uid: {"active": True}}), raw_jwks=jwks)
        assert e.value.code == "unauthorized"
    with pytest.raises(ServiceError) as e:
        extract_actor_context(f"Bearer {tok}", CFG, Profiles({}), raw_jwks=jwks)
    assert e.value.code == "unauthorized"
    with pytest.raises(ServiceError) as e:
        extract_actor_context(f"Bearer {tok}", CFG, Profiles({uid: {"active": False}}), raw_jwks=jwks)
    assert (e.value.status, e.value.code) == (401, "account_disabled")

    class BootstrapProfiles:
        def __init__(self):
            self.seen = {}

        def ensure_profile(self, u, display_name=""):
            self.seen[u] = display_name
            return {"user_id": u, "active": True, "display_name": display_name}

    bp = BootstrapProfiles()
    tok_meta = token(key, alg, "kid-ec", str(uid), email="alice@example.com",
                     user_metadata={"display_name": "  Alice Nguyễn \x00 ", "role": "admin"})
    act = extract_actor_context(f"Bearer {tok_meta}", CFG, bp, raw_jwks=jwks)
    assert act.user_id == uid and bp.seen[uid] == "Alice Nguyễn"


def test_hs256_only_with_explicit_legacy_secret():
    secret = "legacy-jwt-secret-at-least-32-bytes!!"
    sub = str(uuid4())
    tok = jwt.encode({"sub": sub, "iss": ISS, "aud": "authenticated", "exp": int(time.time()) + 60}, secret, algorithm="HS256")
    assert verify_jwt_token(tok, AuthConfig(expected_issuer=ISS, jwt_secret=secret))["sub"] == sub


def test_missing_jwks_config_is_503():
    key, alg, _ = keypair("ec")
    with pytest.raises(ServiceError) as e:
        verify_jwt_token(token(key, alg, "kid-ec", str(uuid4())), AuthConfig(expected_issuer=ISS))
    assert e.value.status == 503
