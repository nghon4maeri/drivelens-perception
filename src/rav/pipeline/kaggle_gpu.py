"""Minimal official Kaggle SDK session + Jupyter transport for CLIP jobs.

Input: explicit token from repo .env, optional notebook slug; output: private worker and GPU session tools.
Only owns sessions it creates; signed URLs never appear in exceptions. No kaggle_extend_api runtime dependency.
"""
from __future__ import annotations

import base64
import json
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit


@dataclass
class GPUSession:
    session_id: int
    operation_name: str
    tools: object = None


class KaggleGPUClient:
    def __init__(self, *, api_token: str):
        from kagglesdk.kaggle_client import KaggleClient

        if not api_token:
            raise ValueError("Thiếu KAGGLE_API_TOKEN trong .env repo")
        sdk = KaggleClient(api_token=api_token)
        self._sdk, self._token = sdk, api_token
        self.kernels = sdk.kernels.kernels_api_client
        self.operations = sdk.common.operations_client

    def ensure_kernel(self, output_dir: Path, override: str = "") -> str:
        from kagglesdk.kernels.types.kernels_api_service import (
            ApiGetKernelRequest,
            ApiSaveKernelRequest,
            KernelExecutionType,
        )
        from kagglesdk.security.types.oauth_service import IntrospectTokenRequest

        req = IntrospectTokenRequest()
        req.token = self._token
        identity = self._sdk.security.oauth_client.introspect_token(req)
        if not identity.active or not identity.username:
            raise ValueError("Token .env không active hoặc không xác định được tài khoản Kaggle.")
        owner = identity.username
        if override:
            if override.split("/", 1)[0] != owner:
                raise ValueError("RAV_KAGGLE_KERNEL phải thuộc tài khoản của token .env.")
            return override
        path = output_dir / "clip-worker.json"
        if path.is_file():
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved.get("account") == owner and saved.get("slug", "").startswith(owner + "/"):
                return saved["slug"]
        title = "RAV26 CLIP Worker " + uuid.uuid4().hex[:12]
        save = ApiSaveKernelRequest()
        save.slug, save.new_title = owner + "/" + title.lower().replace(" ", "-"), title
        save.language, save.kernel_type, save.is_private = "python", "notebook", True
        save.enable_gpu, save.enable_internet = False, True
        save.kernel_execution_type = KernelExecutionType.QUICK_SAVE
        save.text = json.dumps({"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [
            {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
             "source": ["# Private worker for RAV26 CLIP jobs. No data or credentials stored here.\n"]}]})
        response = self.kernels.save_kernel(save)
        if response.error:
            raise ValueError("Token .env chưa tạo được notebook private riêng; kiểm quyền Kaggle.")
        # QUICK_SAVE may omit ref; verify the requested notebook through its metadata.
        read = ApiGetKernelRequest()
        read.user_name, read.kernel_slug = owner, save.slug.split("/", 1)[1]
        metadata = self.kernels.get_kernel(read).metadata
        if not metadata or metadata.ref != save.slug or not metadata.is_private:
            raise ValueError("Không xác minh được notebook private vừa tạo bằng token .env.")
        output_dir.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps({"account": owner, "slug": metadata.ref, "kernel_id": metadata.id,
                                    "created_by": "rav26", "private": True}), encoding="utf-8")
        temp.replace(path)
        return metadata.ref

    def account(self) -> str:
        from kagglesdk.security.types.oauth_service import IntrospectTokenRequest

        req = IntrospectTokenRequest()
        req.token = self._token
        identity = self._sdk.security.oauth_client.introspect_token(req)
        if not identity.active or not identity.username:
            raise ValueError("Token Kaggle không active hoặc không xác định được tài khoản.")
        return identity.username

    def ensure_named_kernel(self, slug: str) -> str:
        """Pilot (#94): notebook private cố định theo slot GPU; tạo nếu chưa có, không dùng file local."""
        from kagglesdk.kernels.types.kernels_api_service import (
            ApiGetKernelRequest,
            ApiSaveKernelRequest,
            KernelExecutionType,
        )

        owner, name = slug.split("/", 1)
        if owner != self.account():
            raise ValueError("Notebook slot GPU phải thuộc tài khoản của token.")
        read = ApiGetKernelRequest()
        read.user_name, read.kernel_slug = owner, name
        try:
            metadata = self.kernels.get_kernel(read).metadata
        except Exception:
            metadata = None
        if metadata and metadata.ref == slug:
            if not metadata.is_private:
                raise ValueError("Notebook slot GPU không private.")
            return slug
        save = ApiSaveKernelRequest()
        save.slug, save.new_title = slug, name
        save.language, save.kernel_type, save.is_private = "python", "notebook", True
        save.enable_gpu, save.enable_internet = False, True
        save.kernel_execution_type = KernelExecutionType.QUICK_SAVE
        save.text = json.dumps({"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [
            {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
             "source": ["# RAV26 pilot GPU slot. No data or credentials stored here.\n"]}]})
        if self.kernels.save_kernel(save).error:
            raise ValueError("Không tạo được notebook private cho slot GPU.")
        metadata = self.kernels.get_kernel(read).metadata
        if not metadata or metadata.ref != slug or not metadata.is_private:
            raise ValueError("Không xác minh được notebook private của slot GPU.")
        return slug

    def session_status(self, slug: str) -> str:
        """Trạng thái session mới nhất của notebook: QUEUED/RUNNING/COMPLETE/ERROR/CANCEL_*/NEW_SCRIPT."""
        from kagglesdk.kernels.types.kernels_api_service import ApiGetKernelSessionStatusRequest

        req = ApiGetKernelSessionStatusRequest()
        req.user_name, req.kernel_slug = slug.split("/", 1)
        return self.kernels.get_kernel_session_status(req).status.name

    def interactive_session_status(self, session_id: str | int) -> str:
        """Exact interactive session: live SSE vs persisted terminal logs (#113).

        SDK's JSON decoder rejects empty persisted logs; inspect HTTP headers without
        consuming logs (which may include signed URLs). Unknown responses never imply stop.
        """
        from kagglesdk.kernels.types.kernels_api_service import ApiGetKernelSessionLogsStreamRequest

        req = ApiGetKernelSessionLogsStreamRequest()
        req.kernel_session_id, req.wait_for_logs_url_seconds = int(session_id), 1
        client = self.kernels._client
        client._init_session()
        request = client._prepare_request("kernels.KernelsApiService", "GetKernelSessionLogsStream", req)
        settings = client._session.merge_environment_settings(request.url, {}, True, None, None)
        with client._session.send(request, timeout=10, **settings) as response:
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if response.status_code == 200 and content_type == "text/event-stream":
                return "RUNNING"
            if response.status_code == 200 and content_type == "application/json":
                body = response.raw.read(1_048_577)
                if len(body) > 1_048_576:
                    return "UNKNOWN"
                if not body or isinstance(json.loads(body), list):
                    return "COMPLETE"
                return "UNKNOWN"  # JSON error/envelope is not persisted session logs
            return "UNKNOWN"

    def quota_statistics(self) -> dict:
        """Quota GPU tuần của account (timedelta → ms)."""
        stats = self.kernels.get_accelerator_quota_statistics()
        gpu = stats.gpu_quota
        if gpu is None:
            raise RuntimeError("Kaggle không trả quota GPU")
        ms = lambda d: int(d.total_seconds() * 1000) if d is not None else 0   # noqa: E731
        return {"total_ms": ms(gpu.total_time_allowed), "used_ms": ms(gpu.time_used), "reserved_ms": ms(gpu.time_reserved),
                "refresh_time": stats.quota_refresh_time}

    def create_session(self, slug, *, accelerator="t4", enable_internet=True, wait=False):
        from kagglesdk.kernels.types.kernels_api_service import ApiCreateKernelSessionRequest

        if accelerator != "t4" or wait:
            raise ValueError("CLIP adapter uses asynchronous t4 sessions")
        req = ApiCreateKernelSessionRequest()
        req.slug, req.kernel_type, req.language = slug, "notebook", "python"
        req.machine_shape, req.enable_internet = "NvidiaTeslaT4", enable_internet
        data = self.kernels.create_kernel_session(req).to_dict()
        metadata = data.get("metadata") or {}
        session_id = metadata.get("kernelSessionId") or metadata.get("kernel_session_id")
        operation = data.get("name", "")
        if not session_id and operation.startswith("operations/create-kernel-session/"):
            session_id = operation.rsplit("/", 1)[-1]
        if not session_id or not operation:
            raise RuntimeError("Kaggle không trả session ID/operation để quản lý job")
        return GPUSession(int(session_id), operation)

    def wait_session(self, session, *, timeout):
        from kagglesdk.common.types.operations_service import GetOperationRequest

        req = GetOperationRequest()
        req.name = session.operation_name
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data = self.operations.get_operation(req).to_dict()
            except Exception as exc:
                if "404" not in str(exc):
                    raise RuntimeError("Kaggle không đọc được operation") from None
                data = {}
            if data.get("done"):
                response = data.get("response") or {}
                url = response.get("jupyterUrl") or response.get("jupyter_url")
                if data.get("error") or not url:
                    raise RuntimeError("Kaggle không khởi động được GPU notebook")
                session.tools = JupyterGPU(url, timeout)
                return session
            time.sleep(min(3, max(0, deadline - time.monotonic())))
        raise TimeoutError("Kaggle GPU boot timeout")

    def stop_session(self, session_id):
        from kagglesdk.kernels.types.kernels_api_service import ApiCancelKernelSessionRequest

        req = ApiCancelKernelSessionRequest()
        req.kernel_session_id = int(session_id)
        response = self.kernels.cancel_kernel_session(req)
        if response.error_message:
            raise RuntimeError("Không dừng được session GPU của job")


class JupyterGPU:
    def __init__(self, url: str, timeout: float):
        self._url = url.rstrip("/")
        self.timeout = timeout

    def _request(self, method, path, **kwargs):
        import requests

        response = requests.request(method, self._url + "/api/" + path, timeout=self.timeout, **kwargs)
        if not response.ok:
            raise RuntimeError(f"Jupyter GPU trả HTTP {response.status_code}")
        return response

    @staticmethod
    def _file_path(path):
        return "contents/" + quote(path.lstrip("/").removeprefix("kaggle/working/"), safe="/")

    def put_file(self, local, remote):
        data = base64.b64encode(Path(local).read_bytes()).decode("ascii")
        self._request("PUT", self._file_path(remote), json={"type": "file", "format": "base64", "content": data})

    def get_file(self, remote, local):
        data = self._request("GET", self._file_path(remote), params={"content": 1, "format": "base64"}).json()
        if data.get("format") != "base64" or not isinstance(data.get("content"), str):
            raise RuntimeError("Jupyter GPU không trả file nhị phân đầy đủ")
        # Jupyter wraps base64 output in lines; keep strict decoding after removing whitespace.
        Path(local).write_bytes(base64.b64decode("".join(data["content"].split()), validate=True))

    def execute(self, code, *, timeout):
        import websocket

        # New session's kernel only; never attaches to another existing user session.
        kernel = self._request("POST", "kernels", json={"name": "python3"}).json()["id"]
        parsed = urlsplit(self._url)
        wsurl = urlunsplit(("wss" if parsed.scheme == "https" else "ws", parsed.netloc,
                           parsed.path + f"/api/kernels/{kernel}/channels", "session_id=" + uuid.uuid4().hex, ""))
        message_id = uuid.uuid4().hex
        request = {"header": {"msg_id": message_id, "username": "rav", "session": uuid.uuid4().hex,
                              "date": datetime.now(UTC).isoformat(), "msg_type": "execute_request", "version": "5.3"},
                   "parent_header": {}, "metadata": {}, "channel": "shell", "buffers": [],
                   "content": {"code": code, "silent": False, "store_history": False, "user_expressions": {},
                               "allow_stdin": False, "stop_on_error": True}}
        ws = websocket.create_connection(wsurl, timeout=min(timeout, 60))
        reply, idle = None, False
        try:
            ws.send(json.dumps(request))
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                ws.settimeout(max(0.001, min(60, deadline - time.monotonic())))
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                if not raw:
                    break
                message = json.loads(raw)
                if (message.get("parent_header") or {}).get("msg_id") != message_id:
                    continue
                kind, content = (message.get("header") or {}).get("msg_type"), message.get("content") or {}
                if message.get("channel") == "shell" and kind == "execute_reply":
                    reply = content.get("status")
                if message.get("channel") == "iopub" and kind == "status" and content.get("execution_state") == "idle":
                    idle = True
                if reply is not None and idle:
                    break
        finally:
            ws.close()
        return {"status": "ok" if reply == "ok" and idle else "error"}
