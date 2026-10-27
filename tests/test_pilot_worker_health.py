"""Liveness không nhận PID tái sử dụng, host/boot khác hoặc record thiếu/hỏng."""

import json
from types import SimpleNamespace

import pytest

from src.rav.worker import health


@pytest.mark.parametrize("change,token,expected", [
    ({}, "start", True), ({}, None, False), ({}, "reused", False),
    ({"host": "elsewhere"}, "start", False),
    ({"host_boot_id": "old"}, "start", False),
    ({"pid": True}, "start", False), ({"start_token": None}, None, False),
])
def test_process_identity(monkeypatch, tmp_path, change, token, expected):
    record = {"pid": 123, "start_token": "start", "host": "host", "host_boot_id": "boot", **change}
    (tmp_path / "process.json").write_text(json.dumps(record))
    monkeypatch.setattr(health, "_workdir", lambda: tmp_path)
    monkeypatch.setattr(health, "_context", lambda: SimpleNamespace(host_boot_id="boot"))
    monkeypatch.setattr(health.socket, "gethostname", lambda: "host")
    monkeypatch.setattr(health, "_token", lambda pid: token)
    assert health.alive() is expected


def test_absent_or_invalid_record(monkeypatch, tmp_path):
    monkeypatch.setattr(health, "_workdir", lambda: tmp_path)
    assert not health.alive()
    (tmp_path / "process.json").write_text("broken")
    assert not health.alive()


@pytest.mark.parametrize("children,expected", [
    ({"CPU": {"pid": 7, "start_token": "c"}, "MEDIA": {"pid": 8, "start_token": "m"}}, True),
    ({"CPU": {"pid": 7, "start_token": "c"}}, False),                                    # MEDIA đang chờ khởi động lại
    ({"CPU": {"pid": 7, "start_token": "c"}, "MEDIA": {"pid": 8, "start_token": "old"}}, False),   # PID bị tái sử dụng
])
def test_children_must_be_alive(monkeypatch, tmp_path, children, expected):
    """§21.20 F3: healthy chỉ khi process chính và mọi process con queue đang cần đều sống."""
    record = {"pid": 123, "start_token": "start", "host": "host", "host_boot_id": "boot", "expected": ["CPU", "MEDIA"],
              "children": children}
    (tmp_path / "process.json").write_text(json.dumps(record))
    monkeypatch.setattr(health, "_workdir", lambda: tmp_path)
    monkeypatch.setattr(health, "_context", lambda: SimpleNamespace(host_boot_id="boot"))
    monkeypatch.setattr(health.socket, "gethostname", lambda: "host")
    monkeypatch.setattr(health, "_token", {123: "start", 7: "c", 8: "m"}.get)
    assert health.alive() is expected
