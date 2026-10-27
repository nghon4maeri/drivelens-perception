"""Serve the pilot React build alongside API v2 when RAV_WEB_DIST is configured.

Input: trusted build directory and public VITE settings; output: SPA/assets routes.
No model, Node process, credentials or arbitrary filesystem paths are exposed.
"""

from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles


def mount_web(api: FastAPI) -> None:
    directory = os.environ.get("RAV_WEB_DIST")
    if not directory:
        return
    root = Path(directory).resolve()
    if not (root / "index.html").is_file() or not (root / "assets").is_dir():
        raise RuntimeError("RAV_WEB_DIST requires a complete frontend build")

    mimetypes.add_type("image/webp", ".webp")   # Python 3.12 slim không có sẵn; landing dùng ảnh WebP
    api.mount("/assets", StaticFiles(directory=root / "assets"), name="web-assets")

    @api.get("/runtime-config.js", include_in_schema=False)
    def runtime_config():
        config = {
            "apiUrl": os.environ.get("VITE_API_URL", ""),
            "supabaseUrl": os.environ.get("VITE_SUPABASE_URL", ""),
            "supabasePublishableKey": os.environ.get("VITE_SUPABASE_PUBLISHABLE_KEY")
            or os.environ.get("VITE_SUPABASE_ANON_KEY", ""),
        }
        return Response("window.__RAV_PUBLIC_CONFIG__ = " + json.dumps(config) + ";\n",
                        media_type="application/javascript")

    def index():
        return FileResponse(root / "index.html")

    for path in ("/", "/landing", "/login", "/app", "/IrishCoffee-T026"):
        api.add_api_route(path, index, methods=["GET", "HEAD"], include_in_schema=False)
