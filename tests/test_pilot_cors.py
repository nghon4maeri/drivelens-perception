"""Cross-origin frontend Docker → API: explicit origins and v2 headers, no DB or cloud required."""
import pytest
from fastapi.testclient import TestClient

from src.rav.api.pilot import create_pilot_app


def test_cors_allows_only_configured_frontend(monkeypatch):
    monkeypatch.setenv("RAV_FRONTEND_ORIGIN", "https://web.example/ , https://preview.example")
    client = TestClient(create_pilot_app(database_url=""))
    headers = {"Origin": "https://web.example", "Access-Control-Request-Method": "POST",
               "Access-Control-Request-Headers": "authorization,content-type,idempotency-key"}
    response = client.options("/api/v2/sessions", headers=headers)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://web.example"
    assert "access-control-allow-credentials" not in response.headers
    headers["Origin"] = "https://stranger.example"
    response = client.options("/api/v2/sessions", headers=headers)
    assert response.status_code == 400 and "access-control-allow-origin" not in response.headers


def test_cors_is_disabled_when_unconfigured(monkeypatch):
    monkeypatch.delenv("RAV_FRONTEND_ORIGIN", raising=False)
    response = TestClient(create_pilot_app(database_url="")).get("/health", headers={"Origin": "https://stranger.example"})
    assert response.status_code == 200 and "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("origin", ["*", "https://*.example", "https://web.example/path", "file://web.example", "https://user:password@web.example"])
def test_cors_rejects_wildcard_or_non_origin_config(monkeypatch, origin):
    monkeypatch.setenv("RAV_FRONTEND_ORIGIN", origin)
    with pytest.raises(ValueError, match="RAV_FRONTEND_ORIGIN"):
        create_pilot_app(database_url="")
