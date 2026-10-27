"""Check a credential-free combined image over loopback HTTP, without a browser.

Input: local container URL; output: success or nonzero exit on routing/config errors.
Run: python scripts/smoke_combined.py [--url http://127.0.0.1:18080].
This packaging check expects readiness 503 without DB configuration.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import urlopen


def fetch(url: str) -> tuple[int, str, str]:
    try:
        response = urlopen(url, timeout=3)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.headers.get_content_type(), response.read().decode("utf-8")


def smoke(url: str) -> None:
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ValueError("Use a loopback HTTP URL for an isolated container without DB env.")
    url = url.rstrip("/")
    deadline = time.monotonic() + 60
    while True:
        try:
            status, kind, body = fetch(url + "/health")
            if status == 200:
                break
        except (URLError, TimeoutError, ConnectionError):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError("Combined container did not become live within 60 seconds.")
        time.sleep(1)
    assert kind == "application/json" and json.loads(body)["mode"] == "pilot", "Invalid pilot health"
    for path in ("/login", "/app"):
        status, kind, html = fetch(url + path)
        assert status == 200 and kind == "text/html", f"SPA missing: {path}"
        assets = re.findall(r'(?:src|href)="(/assets/[^"<>]+)"', html)
        assert assets, f"No built assets in {path}"
        for asset in assets:
            code, media_type, content = fetch(url + asset)
            assert code == 200 and media_type != "text/html" and content, f"Asset missing: {asset}"
    status, kind, body = fetch(url + "/runtime-config.js")
    assert status == 200 and kind == "application/javascript", "Runtime config missing"
    prefix = "window.__RAV_PUBLIC_CONFIG__ = "
    assert body.startswith(prefix), "Invalid runtime config assignment"
    config = json.loads(body.removeprefix(prefix).strip().removesuffix(";"))
    assert set(config) == {"apiUrl", "supabaseUrl", "supabasePublishableKey"}, "Public config allowlist changed"
    status, kind, body = fetch(url + "/health/ready")
    assert status == 503 and kind == "application/json" and json.loads(body)["status"] == "not_ready", "False readiness"
    status, kind, body = fetch(url + "/api/v2/missing")
    assert status == 404 and kind == "application/json" and "error" in json.loads(body), "API fell back to SPA"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:18080")
    smoke(parser.parse_args().url)
    print("PASS: combined health, SPA/assets, public config, missing-DB readiness and API JSON")


if __name__ == "__main__":
    main()
