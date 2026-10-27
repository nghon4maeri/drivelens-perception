"""Dev/CI gates: fail before skipped pilot coverage, preserve failures and catch broken packaging.

Run: python -m pytest tests/test_dev_checks.py -q; no DB, models or Docker required.
"""

from http.client import RemoteDisconnected
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from scripts import check_dev, smoke_combined
from src.rav.api.pilot import create_pilot_app


def test_missing_pg_fails_before_any_backend_check(monkeypatch):
    monkeypatch.delenv("RAV_PILOT_TEST_DATABASE_URL", raising=False)
    monkeypatch.setattr(check_dev.subprocess, "run", lambda *a, **k: pytest.fail("Ran without test DB"))
    with pytest.raises(SystemExit) as error:
        check_dev.main(["--scope", "backend"])
    assert error.value.code == 2


def test_first_command_failure_stops_checks_without_logging_db_url(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("RAV_PILOT_TEST_DATABASE_URL", "private-test-url")
    monkeypatch.setattr(check_dev, "ROOT", tmp_path)
    calls = []

    def fail(command, **kwargs):
        calls.append(command)
        assert kwargs["env"]["TMPDIR"] == str(tmp_path / "output/tmp")
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(check_dev.subprocess, "run", fail)
    assert check_dev.main(["--scope", "backend"]) == 7
    assert len(calls) == 1 and "private-test-url" not in capsys.readouterr().out


def test_frontend_build_modes_override_inherited_pilot_env(monkeypatch, tmp_path):
    monkeypatch.setattr(check_dev, "ROOT", tmp_path)
    monkeypatch.setattr(check_dev.shutil, "which", lambda name: "npm")
    monkeypatch.setenv("VITE_RAV_MODE", "pilot")
    modes = []

    def run(command, **kwargs):
        if command[-2:] == ["run", "build"]:
            modes.append(kwargs["env"]["VITE_RAV_MODE"])
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(check_dev.subprocess, "run", run)
    assert check_dev.main(["--scope", "frontend"]) == 0
    assert modes == ["local", "pilot"]


def test_linux_cannot_hide_hook_failure(monkeypatch):
    monkeypatch.setattr(check_dev.sys, "platform", "linux")
    with pytest.raises(SystemExit) as error:
        check_dev.main(["--ignore-known-windows-hook"])
    assert error.value.code == 2


@pytest.fixture
def combined(monkeypatch, tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/app.js").write_text("console.log('pilot')", encoding="utf-8")
    (tmp_path / "index.html").write_text('<html><script src="/assets/app.js"></script></html>', encoding="utf-8")
    monkeypatch.setenv("RAV_WEB_DIST", str(tmp_path))
    with TestClient(create_pilot_app(database_url="")) as client:
        def fetch(url):
            response = client.get(url.removeprefix("http://localhost"))
            return response.status_code, response.headers["content-type"].split(";")[0], response.text
        monkeypatch.setattr(smoke_combined, "fetch", fetch)
        yield fetch


def test_real_combined_app_passes_without_db_or_browser(combined):
    smoke_combined.smoke("http://localhost")


def test_smoke_waits_for_connection_closed_during_boot(combined, monkeypatch):
    calls = []

    def fetch(url):
        calls.append(url)
        if len(calls) == 1:
            raise RemoteDisconnected("Container is still booting")
        return combined(url)

    monkeypatch.setattr(smoke_combined, "fetch", fetch)
    monkeypatch.setattr(smoke_combined.time, "sleep", lambda seconds: None)
    smoke_combined.smoke("http://localhost")
    assert calls[:2] == ["http://localhost/health"] * 2


@pytest.mark.parametrize("path,replacement,message", [
    ("/health/ready", (200, "application/json", '{"status":"ok"}'), "False readiness"),
    ("/api/v2/missing", (404, "text/html", "<html>SPA</html>"), "API fell back"),
    ("/assets/app.js", (404, "application/json", "{}"), "Asset missing"),
    ("/runtime-config.js", (200, "application/javascript", 'window.__RAV_PUBLIC_CONFIG__ = {"secret":"bad"};'), "allowlist"),
])
def test_smoke_rejects_broken_image(combined, monkeypatch, path, replacement, message):
    monkeypatch.setattr(smoke_combined, "fetch", lambda url: replacement if url.endswith(path) else combined(url))
    with pytest.raises(AssertionError, match=message):
        smoke_combined.smoke("http://localhost")


def test_smoke_rejects_production_target():
    with pytest.raises(ValueError, match="loopback"):
        smoke_combined.smoke("https://c4-app-026.onrender.com")
