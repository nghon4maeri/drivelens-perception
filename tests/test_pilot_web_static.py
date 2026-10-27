"""Check combined hosting routing, static path isolation and public config allowlist."""

import json

import pytest
from fastapi.testclient import TestClient

from src.rav.api.pilot import create_pilot_app


def test_combined_routes_and_public_config(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>IrishCoffee T026</html>", encoding="utf-8")
    (tmp_path / "assets" / "app.js").write_text("console.log('pilot')", encoding="utf-8")
    (tmp_path / "assets" / "frame.webp").write_bytes(b"RIFF\0\0\0\0WEBP")
    (tmp_path / "private.txt").write_text("must not leak", encoding="utf-8")
    monkeypatch.setenv("RAV_WEB_DIST", str(tmp_path))
    monkeypatch.setenv("VITE_API_URL", "")
    monkeypatch.setenv("VITE_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("VITE_SUPABASE_PUBLISHABLE_KEY", 'public"key')
    monkeypatch.setenv("DATABASE_URL", "private-database-secret")
    with TestClient(create_pilot_app()) as client:
        for route in ("/", "/landing", "/login", "/app", "/IrishCoffee-T026"):
            assert "IrishCoffee T026" in client.get(route).text
        assert client.head("/login").status_code == 200
        assert client.get("/assets/app.js").status_code == 200
        assert client.get("/assets/frame.webp").headers["content-type"] == "image/webp"
        for route in ("/private.txt", "/api/v2/missing", "/health/missing", "/assets/missing.js",
                      "/assets/%2e%2e/private.txt"):
            response = client.get(route)
            assert response.status_code == 404
            assert "must not leak" not in response.text
            assert "IrishCoffee T026" not in response.text
        assert client.get("/health").json()["status"] == "ok"
        # No DB fixture: business routes retain their dependency error, never SPA fallback.
        assert client.get("/api/v2/me").status_code == 503
        assert "error" in client.get("/api/v2/me").json()
        response = client.get("/runtime-config.js")
        config = json.loads(response.text.removeprefix("window.__RAV_PUBLIC_CONFIG__ = ").strip().removesuffix(";"))
        assert config == {"apiUrl": "", "supabaseUrl": "https://example.supabase.co",
                          "supabasePublishableKey": 'public"key'}
        assert "private-database-secret" not in response.text
        assert "no-store" in response.headers["cache-control"]


def test_api_only_and_incomplete_web_build(monkeypatch, tmp_path):
    monkeypatch.delenv("RAV_WEB_DIST", raising=False)
    with TestClient(create_pilot_app()) as client:
        assert client.get("/").status_code == 404
    monkeypatch.setenv("RAV_WEB_DIST", str(tmp_path))
    with pytest.raises(RuntimeError, match="complete frontend build"):
        create_pilot_app()
