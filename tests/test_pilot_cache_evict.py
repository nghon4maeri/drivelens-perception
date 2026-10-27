"""Dọn cache đĩa worker theo trần (DEP7-Frames F4, CONTRACTS §21.20): LRU theo mtime, chừa file vừa dùng/mới dùng, `.part` cũ."""

import hashlib
import os

import pytest

from src.rav.errors import ServiceError
from src.rav.worker import session_context as sc

NOW = 1_000_000.0


def put(folder, name, size, age_s):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"x" * size)
    os.utime(path, (NOW - age_s, NOW - age_s))
    return path


def test_no_cap_keeps_cache_but_drops_stale_parts(monkeypatch, tmp_path):
    monkeypatch.delenv("RAV_PILOT_CACHE_MAX_BYTES", raising=False)
    big = put(tmp_path / "raw-cache", "a.video", 5000, 99_999)
    stale = put(tmp_path / "raw-cache", "a.123.part", 10, sc.PART_STALE_S + 1)
    fresh = put(tmp_path / "raw-cache", "b.456.part", 10, 60)
    assert sc.evict(tmp_path, now=NOW) == [stale]
    assert big.exists() and fresh.exists()


def test_cap_removes_oldest_first_across_cache_dirs(monkeypatch, tmp_path):
    monkeypatch.setenv("RAV_PILOT_CACHE_MAX_BYTES", "250")
    oldest = put(tmp_path / "model-cache", "m.pt", 100, 5000)
    older = put(tmp_path / "raw-cache", "r.video", 100, 4000)
    newer = put(tmp_path / "feature-cache", "f.npz", 100, 3000)
    recent = put(tmp_path / "raw-cache", "hot.video", 100, 30)          # dùng trong 10 phút: không dọn
    other = put(tmp_path / "workspace", "attempt.bin", 10_000, 99_999)  # ngoài thư mục cache: không đụng
    removed = sc.evict(tmp_path, now=NOW)
    assert removed == [oldest, older] and newer.exists() and recent.exists() and other.exists()


def test_keep_and_recent_files_survive_even_over_cap(monkeypatch, tmp_path):
    monkeypatch.setenv("RAV_PILOT_CACHE_MAX_BYTES", "1")
    keep = put(tmp_path / "raw-cache", "keep.video", 100, 99_999)
    recent = put(tmp_path / "raw-cache", "recent.video", 100, 10)
    old = put(tmp_path / "raw-cache", "old.video", 100, 99_998)
    assert sc.evict(tmp_path, keep=keep, now=NOW) == [old]
    assert keep.exists() and recent.exists()


@pytest.mark.parametrize("value", ["0", "-5", "abc"])
def test_invalid_cap_rejected(monkeypatch, value):
    monkeypatch.setenv("RAV_PILOT_CACHE_MAX_BYTES", value)
    with pytest.raises(ServiceError):
        sc.cache_cap()


class FakeStorage:
    def __init__(self, data: bytes):
        self.data, self.calls = data, 0

    def download(self, key, dest, max_bytes):
        self.calls += 1
        dest.write_bytes(self.data)
        return hashlib.sha256(self.data).hexdigest(), len(self.data)


def test_cached_hit_touches_and_miss_evicts(monkeypatch, tmp_path):
    monkeypatch.setenv("RAV_PILOT_CACHE_MAX_BYTES", "150")
    old = put(tmp_path / "raw-cache", "old.video", 100, 99_999)
    data = b"y" * 100
    sha = hashlib.sha256(data).hexdigest()
    storage = FakeStorage(data)
    path = sc.cached(storage, tmp_path / "raw-cache", "k", sha, 1000, ".video")
    assert path.read_bytes() == data and storage.calls == 1
    assert not old.exists()   # tải mới làm vượt trần → dọn bản cũ nhất, giữ bản vừa tải
    os.utime(path, (1, 1))
    assert sc.cached(storage, tmp_path / "raw-cache", "k", sha, 1000, ".video") == path and storage.calls == 1
    assert path.stat().st_mtime > 1   # hit cập nhật mtime (LRU)
    assert not list((tmp_path / "raw-cache").glob("*.part"))


def test_cached_rejects_wrong_sha_without_leaving_files(tmp_path):
    storage = FakeStorage(b"z" * 10)
    with pytest.raises(ServiceError):
        sc.cached(storage, tmp_path / "raw-cache", "k", "0" * 64, 1000, ".video")
    assert list((tmp_path / "raw-cache").iterdir()) == []
