"""Test kiem tra cac thanh phan bootstrap va ha tang nhe (DEP1).

Muc dich:
    Xac minh cac endpoint /health va /api/v2/health hoat dong chinh xac,
    kiem tra tinh nhe (khong ro ri module ML vao Web API),
    kiem tra tinh hop le cua Dockerfile, .dockerignore va cac dependency profile.

Cach chay:
    . .\\scripts\\use_project_storage.ps1
    python -m pytest tests/test_pilot_bootstrap.py -q
"""

import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from src.rav.api import create_app
from src.rav.api.pilot import create_pilot_app
from src.rav.service import App

ROOT = Path(__file__).resolve().parent.parent


def test_local_health_and_no_v2_routes():
    client = TestClient(create_app(App.from_env()))
    data = client.get("/health").json()
    assert (data["status"], data["service"], data["version"]) == ("ok", "rav-api", "1.0.0")
    ready = client.get("/health/ready").json()
    assert ready["status"] in ("ok", "degraded") and "database" in ready["checks"]
    assert client.get("/api/v2/health").status_code == 404   # app local không mount v2 (CONTRACTS §21.11)


def test_pilot_health_without_database():
    client = TestClient(create_pilot_app(""))
    for path in ("/health", "/api/v2/health"):
        data = client.get(path).json()
        assert (data["status"], data["service"], data["version"], data["mode"]) == ("ok", "rav-api-v2", "2.0.0", "pilot")
    assert client.get("/health/ready").status_code == 503


def test_pilot_api_import_is_lightweight_in_fresh_process():
    """Gate runtime spec §7.2: process mới import app pilot không kéo torch/numpy/pyav/cv2."""
    code = ("import sys, src.rav.api.pilot; "
            "bad = [m for m in ('torch','torchvision','ultralytics','transformers','cv2','numpy','av','PIL','sklearn') "
            "if m in sys.modules]; print(','.join(bad))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True,
                         env={"PATH": "", "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""), "PYTHONUTF8": "1"})
    assert out.stdout.strip() == "", f"thư viện nặng rò vào API pilot: {out.stdout}"


def test_dockerfile_and_profiles_structure():
    root = Path(__file__).resolve().parent.parent

    # Dockerfile
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM python:3.12-slim" in dockerfile
    assert "FROM base AS api" in dockerfile
    assert "FROM base AS worker" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "/health" in dockerfile
    assert "src.rav.api.pilot:app" in dockerfile
    assert '"src.rav.worker", "--mode", "pilot"' in dockerfile
    assert "VIRTUAL_ENV=/opt/venv" in dockerfile
    assert "COPY --from=worker-builder /opt/venv /opt/venv" in dockerfile
    assert "COPY --from=api-builder /opt/venv /opt/venv" in dockerfile
    assert "/root/.local" not in dockerfile and " --user" not in dockerfile

    # .dockerignore
    dockerignore = (root / ".dockerignore").read_text(encoding="utf-8")
    assert "data/" in dockerignore
    assert "output/" in dockerignore
    assert ".env" in dockerignore

    # requirements-api.txt khong duoc chua torch hoac ultralytics
    req_api = (root / "requirements-api.txt").read_text(encoding="utf-8")
    assert "torch" not in req_api
    assert "ultralytics" not in req_api
    assert "fastapi" in req_api

    # requirements-worker.txt phai co torch va ultralytics
    req_worker = (root / "requirements-worker.txt").read_text(encoding="utf-8")
    assert "torch" in req_worker
    assert "ultralytics" in req_worker
