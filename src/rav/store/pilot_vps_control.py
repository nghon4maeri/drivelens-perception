"""Render-to-Contabo HTTPS control, no filesystem access on Render.

Input is server-owned upload metadata; output is durable seal/cleanup proof.
Local mode is for worker/same-host tests only, never a deployment fallback.
"""
import os
import ssl
import time
from urllib.parse import urlsplit

from ..errors import ServiceError
from .pilot_vps_disk import VPSDiskUpload, create_vps_capability


def control(upload, action):
    base = os.environ.get("RAV_VPS_UPLOAD_URL", "").rstrip("/")
    if not base or os.environ.get("RAV_VPS_UPLOAD_LOCAL") == "1":
        if not base and os.environ.get("RAV_RUNTIME_MODE") == "deploy":
            raise ServiceError("dependency_unavailable", "thiếu RAV_VPS_UPLOAD_URL", 503)
        disk = VPSDiskUpload(upload["id"])
        if action == "seal":
            return disk.seal(upload["declared_bytes"])
        disk.cleanup()
        return {"closed": True}
    import httpx

    parts = urlsplit(base)
    if (parts.scheme != "https" or not parts.netloc or parts.username or parts.password
            or parts.path or parts.query or parts.fragment):
        raise ServiceError("dependency_unavailable", "VPS URL cần HTTPS origin", 503)
    tls = ssl.create_default_context()
    ca = os.environ.get("RAV_VPS_UPLOAD_CA_PEM") or os.environ.get("RAV_INTERACTIVE_CA_PEM")
    if ca:
        tls.load_verify_locations(cadata=ca.replace("\\n", "\n"))
    cap = create_vps_capability(upload["id"], upload["owner_id"], upload["declared_bytes"], time.time() + 60, purpose=action)
    try:
        with httpx.Client(verify=tls, timeout=20) as client:
            reply = client.post(f"{base}/api/v2/vps-uploads/{upload['id']}/control/{action}",
                                headers={"Authorization": "Bearer " + cap})
        body = reply.json()
    except (httpx.RequestError, ValueError):
        raise ServiceError("dependency_unavailable", "không kết nối được VPS upload", 503) from None
    if not reply.is_success:
        error = body.get("error", {})
        raise ServiceError(error.get("code", "dependency_unavailable"), error.get("message", "VPS upload lỗi"), reply.status_code)
    return body
