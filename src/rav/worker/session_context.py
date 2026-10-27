"""Dựng Context core của một phiên pilot từ payload đã đóng băng (CONTRACTS §21.15, §21.17) — dùng chung PROPOSE / METRICS / YOLO_BATCH.

raw canonical theo SHA (cache trong workdir), hoặc metadata đã đóng băng cho ODD không detector (§21.40),
→ Context như local (decoder/unit, IDs đủ `n_units`) → DINO/CLIP/pHash **chỉ** từ artifact FEATURE
(cache-only: thiếu → lỗi, không tự tính); field dẫn xuất (vd `proj.pca2`) được tính và giữ trong bộ nhớ của job → state = replay quyết định
`seq ≤ decision_seq` (tùy chọn kèm history `Decision` qua feedback `keep_drop` cho metric thời gian duyệt) → `ctx.proposals` = proposal `n ≤ proposal_count`.
`cached`/`evict`: cache file theo SHA dùng chung giữa process worker, dọn LRU theo trần `RAV_PILOT_CACHE_MAX_BYTES` (§21.20 F3/F4).
"""

from __future__ import annotations

import math
import os
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from ..errors import ServiceError
from ..store.pilot_db import PilotDB, replay
from ..store.pilot_storage import PilotStorage

ARTIFACT_FIELDS = {"emb.dinov2_s": "vector", "emb.clip_b32": "vector", "hash.phash": "scalar"}
_cache_lock = threading.Lock()


class ArtifactFieldStore:
    """Field từ artifact (đã kiểm) + field dẫn xuất tính trong job; không ghi ra đĩa. Field artifact được bảo vệ bằng `cache_only_fields`."""

    def __init__(self):
        self._tables: dict[tuple, Any] = {}

    @staticmethod
    def _k(key, part) -> tuple:
        return (key.name, key.producer, key.version, key.params_hash, part)

    def add(self, table, part) -> None:
        self._tables[self._k(table.key, part)] = table

    def get(self, key, part):
        return self._tables.get(self._k(key, part))

    def put(self, table, part) -> None:
        if table.key.name in ARTIFACT_FIELDS:
            raise ServiceError("feature_not_ready", f"thiếu đặc trưng {table.key.name}; worker không tự tính", 409)
        self.add(table, part)


def cached(storage: PilotStorage, folder: Path, key: str, sha: str, max_bytes: int, suffix: str, *, stop=None) -> Path:
    """Tải object theo SHA vào cache cục bộ dùng chung giữa job và giữa các process worker (§21.20 F3); SHA lệch → lỗi, không giữ file.
    Lock chỉ trong process; giữa process: `.part` riêng + đổi tên nguyên tử, nội dung theo SHA nên bản nào thắng cũng đúng (chấp nhận tải trùng)."""
    check_cancel(stop)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sha}{suffix}"
    with _cache_lock:
        check_cancel(stop)
        if path.exists():
            _touch(path)   # LRU theo mtime: bản đang được dùng không bị dọn trước
            return path
        tmp = folder / f"{sha}.{uuid4().hex}.part"
        try:
            got, _ = storage.download(key, tmp, max_bytes, **({'stop': stop} if stop is not None else {}))
            if got != sha:
                raise ServiceError("result_invalid", "object không khớp SHA-256 đã ghi", 422)
            try:
                tmp.replace(path)
            except OSError:   # Windows: process khác đã đặt bản cùng SHA và đang mở → dùng bản đó
                if not path.exists():
                    raise
        finally:
            tmp.unlink(missing_ok=True)
        _touch(path)
        evict(folder.parent, keep=path)
    return path


CACHE_DIRS = ("raw-cache", "model-cache", "feature-cache", "inference-cache")
CACHE_MIN_AGE_S = 600        # file dùng trong 10 phút gần nhất không bị dọn (job khác có thể sắp mở)
PART_STALE_S = 24 * 3600     # `.part` bỏ dở quá 24 giờ


def _touch(path: Path) -> None:
    try:
        os.utime(path)
    except OSError:
        pass


def cache_cap() -> int | None:
    """`RAV_PILOT_CACHE_MAX_BYTES` (số nguyên > 0); không đặt → không dọn (§21.20 F4)."""
    raw = os.environ.get("RAV_PILOT_CACHE_MAX_BYTES", "").strip()
    if not raw:
        return None
    try:
        cap = int(raw)
    except ValueError:
        cap = 0
    if cap <= 0:
        raise ServiceError("validation_error", "RAV_PILOT_CACHE_MAX_BYTES phải là số nguyên > 0", 422)
    return cap


def evict(workdir: Path, keep: Path | None = None, now: float | None = None) -> list[Path]:
    """Dọn cache theo SHA dưới trần: xóa file cũ nhất theo mtime tới khi tổng ≤ trần; chừa `keep` và file mới dùng; trả file đã xóa."""
    cap, now = cache_cap(), time.time() if now is None else now
    files, removed = [], []
    for name in CACHE_DIRS:
        folder = workdir / name
        if not folder.is_dir():
            continue
        for f in folder.iterdir():
            try:
                st = f.stat()
            except OSError:
                continue
            if f.suffix == ".part":
                if now - st.st_mtime > PART_STALE_S:
                    try:
                        f.unlink()
                        removed.append(f)
                    except OSError:
                        pass
                continue
            if f.is_file():
                files.append((st.st_mtime, st.st_size, f))
    if cap is None:
        return removed
    total = sum(size for _, size, _ in files)
    for mtime, size, f in sorted(files, key=lambda x: x[0]):
        if total <= cap:
            break
        if f == keep or now - mtime < CACHE_MIN_AGE_S:
            continue
        try:
            f.unlink()
        except OSError:   # Windows: process khác đang mở
            continue
        total -= size
        removed.append(f)
    return removed


def policy_of(db: PilotDB, job_id: UUID) -> dict:
    with db.backend() as cur:
        cur.execute("SELECT q.config FROM rav_pilot_job j JOIN rav_pilot_quota_policy q ON q.version = j.policy_version "
                    "WHERE j.id = %s", (job_id,))
        return cur.fetchone()["config"]


def check_cancel(stop):
    if stop is not None and stop.is_set():
        raise ServiceError('cancel_requested', 'đã yêu cầu dừng đề xuất', 409)


def pool_context(storage: PilotStorage, p: dict, workdir: Path, max_video_bytes: int, *, stop=None, progress=None):
    """Context trên các video của `p["pool"]` (decoder/unit như local); chưa nạp đặc trưng/state. Trả (ctx, store)."""
    from ..core.registry import discover
    from ..core.types import VideoRef
    from ..pipeline import prepare as p1
    from .handlers import _probe

    discover()
    check_cancel(stop)
    metadata_only = (p.get('recipe') == 'odd_coverage' and not p.get('model') and
                     all((s.get('video_metadata') or {}).get('fps') and
                         (s.get('video_metadata') or {}).get('n_frames') for s in p['pool']))
    phase = 'PREPARE_METADATA' if metadata_only else 'LOAD_RAW'
    if progress:
        progress(phase, 0, len(p['pool']))
    videos = []
    for s in p["pool"]:
        check_cancel(stop)
        if metadata_only:
            meta = s['video_metadata']
            fps, n = float(meta['fps']), int(meta['n_frames'])
            if not (math.isfinite(fps) and fps > 0 and n > 0):
                raise ServiceError('result_invalid', 'metadata video không hợp lệ', 422)
            video = VideoRef(s['core_source_id'], f"prepared://{s['core_source_id']}", fps, n / fps, meta)
        else:
            raw = cached(storage, workdir / "raw-cache", s["raw_key"], s["raw_sha256"], max_video_bytes, ".video", stop=stop)
            check_cancel(stop)
            video = _probe(raw)
        if video.id != s["core_source_id"]:
            raise ServiceError("result_invalid", "id nội dung nguồn không khớp", 422)
        videos.append(video)
        if progress:
            progress(phase, len(videos), len(p['pool']))
    store = ArtifactFieldStore()
    ctx = p1.make_context(videos, {"source": None, **p["spec"]}, store)
    if ctx._execution.spec_hash != p["spec_hash"] or len(ctx.units) != sum(s["n_units"] for s in p["pool"]):
        raise ServiceError("result_invalid", "unit của decoder không khớp spec đã đóng băng", 422)
    return ctx, store


def build_context(storage: PilotStorage, db: PilotDB, p: dict, workdir: Path, max_video_bytes: int, *, history: bool = False,
                  stop=None, progress=None):
    import numpy as np

    from ..core.registry import REGISTRY
    from ..core.types import Decision, FieldTable, ReviewState
    from ..service.proposal import to_proposals

    ctx, store = pool_context(storage, p, workdir, max_video_bytes, stop=stop, progress=progress)
    ctx.cancel_event = stop
    if progress:
        progress('LOAD_FEATURES', 0, sum(len(s['features']) for s in p['pool']))
    loaded, cache_hit = 0, True
    by_source: dict[str, list[str]] = {}
    for u in ctx.units:
        by_source.setdefault(u.video_id, []).append(u.id)
    for s in p["pool"]:
        for feature, art in s["features"].items():
            check_cancel(stop)
            cache_hit = cache_hit and (workdir / 'feature-cache' / f"{art['sha256']}.npz").exists()
            path = cached(storage, workdir / "feature-cache", art["object_key"], art["sha256"], art["size_bytes"], ".npz", stop=stop)
            with np.load(path, allow_pickle=False) as z:
                if list(z["ids"]) != by_source[s["core_source_id"]]:
                    raise ServiceError("feature_not_ready", f"đặc trưng {feature} của {s['source_id']} lệch unit", 409)
                for name, key_doc in art["field_keys"].items():
                    key = REGISTRY.feature_for(name)().key
                    if asdict(key) != key_doc:   # component đã đổi phiên bản → đặc trưng cũ không dùng được
                        raise ServiceError("feature_not_ready", f"{name} của {s['source_id']} tạo bởi phiên bản khác", 409)
                    store.add(FieldTable(key, list(z["ids"]), np.asarray(z[name]), ARTIFACT_FIELDS.get(name, "vector")),
                              (s["core_source_id"], p["spec_hash"]))
            loaded += 1
            if progress:
                progress('LOAD_FEATURES', loaded, sum(len(s['features']) for s in p['pool']), cache_hit=cache_hit)
    ctx.cache_only_fields |= set(ARTIFACT_FIELDS)
    sid = UUID(p["session_id"])
    check_cancel(stop)
    if progress:
        progress('LOAD_REVIEW_STATE', 0, 0)
    with db.backend() as cur:
        cur.execute("SELECT * FROM rav_pilot_decision WHERE session_id = %s AND seq <= %s ORDER BY seq", (sid, p["decision_seq"]))
        rows = cur.fetchall()
        cur.execute("SELECT n, frozen_input, picks, evidence FROM rav_pilot_proposal WHERE session_id = %s AND n <= %s ORDER BY n",
                    (sid, p["proposal_count"]))
        records = [{"n": r["n"], "round": r["n"], "input": r["frozen_input"], "picks": r["picks"],
                    "relaxed": bool((r["evidence"] or {}).get("relaxed"))} for r in cur.fetchall()]
    if len(records) != p["proposal_count"]:
        raise ServiceError("proposal_context_changed", "lịch sử đề xuất khác lúc đóng băng", 409)
    if history:   # như service/review_state: feedback keep_drop áp log quyết định (có history cho metric thời gian duyệt)
        decisions = [Decision(p["session_id"], r["unit_id"], r["action"], r["actor"], r["goal"], r["proposal_n"], r["decided_at"],
                              r["shown_at"], r["reason"], r["confidence"], r["extra"] or {}) for r in rows]
        ctx.state = REGISTRY.build("feedback", {"type": "keep_drop"}).apply(decisions, ReviewState())
    else:
        kept, dropped = replay(rows)
        ctx.state = ReviewState(kept, dropped)
    ctx.proposals = to_proposals(p["session_id"], records, ctx.units)
    return ctx
