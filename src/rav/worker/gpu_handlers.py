"""Handler GPU của worker pilot: `PREPARE_DINO` / `PREPARE_CLIP` (CONTRACTS §21.14, runtime spec §7.1, #94).

Luồng: tải raw canonical (giới hạn + SHA) → Context core (decoder/unit như local, đủ `n_units`) → manifest transport C1/C2
(`remote_s3_embed.build_manifest`; raw dùng key canonical, không upload lại) → đối soát slot (phải ABSENT_VERIFIED) → INTENT →
tạo session → ATTACHED → worker Kaggle tải raw qua URL GET, ghi progress/result qua URL PUT `jobs/<job>/<attempt>/` →
`validate_result` → dừng session và chờ xác minh → NPZ artifact (object receipt trước PUT) → outputs cho publish.
Launch/stop không xác minh được → `HandlerDetachedError` (lease hết hạn → slot QUARANTINED → supervisor đối soát), không đoán.
Chạy trong tiến trình worker (import nặng: PyAV, numpy, component DINO/CLIP).
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import shutil
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from ..errors import ServiceError
from ..store import pilot_gpu, pilot_objects
from ..store.pilot_db import PilotDB
from ..store.pilot_runtime import AttemptLease
from ..store.pilot_storage import PilotStorage
from .dispatcher import HandlerDetachedError
from .kaggle_adapter import KaggleSlotAdapter, build_marker

log = logging.getLogger(__name__)
RAW_EXT = (".mp4", ".mov", ".mkv", ".avi", ".webm")


class _TransportStorage:
    """Giao diện storage mà `build_manifest`/`prepare_assets` cần, trên namespace pilot.

    Raw video của job → key canonical đã có (không upload lại); file khác (asset model DINO) → content-addressed
    `system/models/<name>/<sha><ext>`, chỉ PUT khi chưa có."""

    def __init__(self, storage: PilotStorage, raw_path: Path, raw_key: str, raw_sha: str):
        self.storage, self.raw_path, self.raw_key, self.raw_sha = storage, raw_path.resolve(), raw_key, raw_sha

    def upload_raw(self, path: Path, prefix: str, expected_sha: str | None = None) -> dict:
        path = Path(path)
        if path.resolve() == self.raw_path:
            if expected_sha and expected_sha != self.raw_sha:
                raise ServiceError("result_invalid", "raw nguồn đổi khi dựng manifest", 422)
            return {"key": self.raw_key, "sha256": self.raw_sha, "size_bytes": path.stat().st_size}
        sha = _file_sha(path)
        suffix = path.suffix.lower() if path.suffix.lower() in (".zip", ".pth", ".pt", ".bin") else ""
        key = self.storage.system_model_key(prefix.rstrip("/").rsplit("/", 1)[-1], sha, suffix)
        head = self.storage.head(key)
        if head is None:
            self.storage.put_file(key, path, sha, "application/octet-stream")
        elif head["ContentLength"] != path.stat().st_size:
            raise ServiceError("result_invalid", "asset model trên S3 không khớp kích thước", 422)
        return {"key": key, "sha256": sha, "size_bytes": path.stat().st_size}


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _policy(db: PilotDB, job_id: UUID) -> dict:
    with db.backend() as cur:
        cur.execute("SELECT q.config FROM rav_pilot_job j JOIN rav_pilot_quota_policy q ON q.version = j.policy_version "
                    "WHERE j.id = %s", (job_id,))
        return cur.fetchone()["config"]


def build_context(raw: Path, payload: dict):
    """Context core trên một video: id nội dung + ordered unit IDs phải khớp payload (= local)."""
    from ..pipeline import prepare as p1
    from .handlers import _probe

    video = _probe(raw)
    if video.id != payload["core_source_id"]:
        raise ServiceError("result_invalid", "id nội dung nguồn không khớp", 422)
    ctx = p1.make_context([video], {"source": None, **payload["spec"]}, None)
    if len(ctx.units) != payload["n_units"]:
        raise ServiceError("result_invalid", "số unit của decoder không khớp nguồn", 422)
    return ctx


def feature_npz(feature: str, tables: tuple) -> tuple[bytes, dict]:
    """NPZ artifact FEATURE: `ids` + một mảng theo tên field (§21.14)."""
    import numpy as np

    ids = tables[0].unit_ids
    if any(t.unit_ids != ids for t in tables):
        raise ServiceError("result_invalid", "các field không cùng thứ tự unit", 422)
    arrays: dict[str, Any] = {"ids": np.asarray(ids, dtype=str)}
    for t in tables:
        arrays[t.key.name] = np.asarray(t.values)
    buf = io.BytesIO()
    np.savez(buf, **arrays)
    fields = {t.key.name: asdict(t.key) for t in tables}
    return buf.getvalue(), {"fields": sorted(fields), "field_keys": fields,
                            "ordered_ids_hash": hashlib.sha256("\n".join(ids).encode()).hexdigest()}


def make_gpu_handlers(storage: PilotStorage, db: PilotDB, workdir: Path, adapters: dict[str, KaggleSlotAdapter], *,
                      deployment: str, poll_s: float = 3.0, launch_timeout_s: float = 900.0) -> dict[str, Any]:
    workdir.mkdir(parents=True, exist_ok=True)

    def prepare(lease: AttemptLease, stop: threading.Event) -> dict:
        from ..components.features.clip_b32 import ClipB32
        from ..components.features.dinov2_s import DinoV2S
        from ..pipeline.remote_embed import validate_result
        from ..pipeline.remote_s3_embed import build_manifest, launch_remote, manifest_hash, valid_progress

        p = lease.payload
        adapter = adapters.get(lease.slot_key)
        if adapter is None:
            raise ServiceError("dependency_unavailable", f"worker chưa cấu hình Kaggle cho slot {lease.slot_key}", 503)
        cfg = _policy(db, lease.job_id)
        work = workdir / str(lease.attempt_id)
        work.mkdir(parents=True, exist_ok=True)
        progress_key = storage.job_key(lease.job_id, lease.attempt_id, "progress.json")
        result_key = storage.job_key(lease.job_id, lease.attempt_id, "result.npz")
        gpu_ms = 0
        try:
            ext = next((e for e in RAW_EXT if p["raw_key"].endswith(e)), ".mp4")
            raw = work / f"raw{ext}"
            sha, _ = storage.download(p["raw_key"], raw, cfg["max_video_bytes"])
            if sha != p["raw_sha256"]:
                raise ServiceError("result_invalid", "raw nguồn không khớp SHA-256 đã ghi", 422)
            ctx = build_context(raw, p)
            feature = DinoV2S() if p["feature"] == "DINO" else ClipB32()
            ts = _TransportStorage(storage, raw, p["raw_key"], sha)
            manifest = build_manifest(feature, ctx.units, ctx, ts)
            if p["feature"] == "DINO":
                from ..pipeline.dino_assets import prepare_assets
                manifest["assets"], runtime = prepare_assets(ts, work)
                manifest["runtime"].update(runtime)
            mhash = manifest_hash(manifest)
            ttl = max(60, min(604800, cfg["remote_url_ttl_ms"] // 1000))
            remote_id = str(lease.attempt_id)
            config = {"job_id": remote_id, "manifest": manifest, "manifest_hash": mhash,
                      "input_urls": {k: storage.presign_get(s["key"], ttl) for k, s in manifest["sources"].items()},
                      "progress_url": storage.presign_put(progress_key, ttl, "application/json"),
                      "result_url": storage.presign_put(result_key, ttl, "application/octet-stream")}
            if manifest.get("assets"):
                config["asset_urls"] = {k: storage.presign_get(s["key"], ttl) for k, s in manifest["assets"].items()}
            if stop.is_set():
                return {"gpu_ms": 0}

            # ---- launch (runtime spec §7.1): slot phải sạch, INTENT trước khi gọi provider ----
            adapter.ensure_kernel()
            state = adapter.reconcile_launch()
            if state.status != "ABSENT_VERIFIED":
                raise HandlerDetachedError(f"slot GPU {lease.slot_key} chưa xác minh trống ({state.status})")
            pilot_gpu.launch_intent(db, lease, build_marker(deployment, lease.job_id, lease.attempt_id, lease.fence),
                                    adapter.account)
            t0 = time.monotonic()
            try:
                session = adapter.create()
            except Exception:
                pilot_gpu.launch_uncertain(db, lease)
                raise HandlerDetachedError("tạo session GPU không xác định được kết quả",
                                      gpu_ms=math.ceil((time.monotonic() - t0) * 1000)) from None
            pilot_gpu.launch_attached(db, lease, str(session.session_id), session.operation_name)

            tables, cancelled, failure = None, False, None
            try:
                adapter.provider.wait_session(session, timeout=launch_timeout_s)
                launch_remote(session, f"/kaggle/working/rav-{remote_id}", config, work)
                job = {"id": remote_id, "payload": {"manifest": manifest, "manifest_hash": mhash}}
                while True:
                    progress = storage.read_json(progress_key)
                    if progress and valid_progress(progress, job):
                        job["payload"]["remote_progress"] = progress
                    status = job["payload"].get("remote_progress", {}).get("state")
                    if status == "error":
                        code = str(job["payload"]["remote_progress"].get("error_code", "compute_or_transfer"))[:40]
                        raise ServiceError("gpu_job_failed", f"worker GPU lỗi ({code})", 502)
                    if status == "done":
                        break
                    if stop.wait(poll_s):
                        cancelled = True
                        break
                if not cancelled:
                    result = work / "result.npz"
                    bound = len(ctx.units) * (manifest["dim"] * 4 + 8) + len(json.dumps(manifest).encode()) * 4 + (1 << 20)
                    sha_r, _ = storage.download(result_key, result, bound)
                    if sha_r != job["payload"]["remote_progress"]["result_sha256"]:
                        raise ServiceError("result_invalid", "SHA kết quả GPU không khớp progress", 422)
                    values = validate_result(result, manifest, expected_job_id=remote_id)
                    tables = _tables(feature, manifest, values)
            except Exception as exc:
                failure = exc
            finally:
                verified = adapter.stop_and_verify(session.session_id, cfg["stop_deadline_ms"] / 1000)
                gpu_ms = math.ceil((time.monotonic() - t0) * 1000)
                pilot_gpu.launch_stopped(db, lease, verified=verified)
            if not verified:
                raise HandlerDetachedError("chưa xác minh dừng session GPU", gpu_ms=gpu_ms)
            if failure is not None:
                if not isinstance(failure, ServiceError):
                    log.warning("job %s lỗi GPU: %s", lease.job_id, type(failure).__name__)
                    failure = ServiceError("gpu_job_failed", "transport/kết quả GPU không hợp lệ", 502)
                failure.gpu_ms = gpu_ms
                raise failure
            if cancelled:
                return {"gpu_ms": gpu_ms}

            # ---- artifact FEATURE: receipt trước PUT (§21.13) ----
            data, meta = feature_npz(p["feature"], tables)
            artifact_id, sha_a = uuid4(), hashlib.sha256(data).hexdigest()
            out = work / "feature.npz"
            out.write_bytes(data)
            target_scope = p.get("target_scope") or "PRIVATE"
            key = (storage.system_feature_key("uploads", p["core_source_id"], p["feature"], sha_a)
                   if target_scope == "SYSTEM"
                   else storage.artifact_key(UUID(p["owner_id"]), artifact_id, sha_a))
            pilot_objects.intent(db, lease, key, sha_a, len(data))
            storage.put_file(key, out, sha_a, "application/octet-stream")
            pilot_objects.written(db, lease, key)
            manifest_out = {"source_id": p["source_id"], "core_source_id": p["core_source_id"], "feature": p["feature"],
                            "input_hash": p["input_hash"], "spec_hash": p["spec_hash"], "n_units": p["n_units"],
                            "npz_sha256": sha_a, "runtime": manifest.get("runtime", {}), **meta}
            return {"feature": {"id": str(artifact_id), "object_key": key, "sha256": sha_a, "size_bytes": len(data),
                                "manifest": manifest_out}, "gpu_ms": gpu_ms, "cleanup_done": True}
        finally:
            deleted = True
            for k in (progress_key, result_key):   # tên cụ thể của attempt, không quét prefix
                try:
                    storage.delete(k)
                except ServiceError:
                    deleted = False
                    log.warning("chưa xóa được object trao đổi GPU của attempt %s", lease.attempt_id)
            shutil.rmtree(work, ignore_errors=True)
            if not deleted:
                raise HandlerDetachedError("chưa xóa được object trao đổi GPU; supervisor sẽ dọn lại", gpu_ms=gpu_ms)

    return {"PREPARE_DINO": prepare, "PREPARE_CLIP": prepare}


def _tables(feature, manifest: dict, values) -> tuple:
    from ..components.features.phash import PHash
    from ..core.types import FieldTable

    ids = manifest["sample_ids"]
    if manifest.get("embedding_family") == "dino":
        if manifest.get("phash_key") != asdict(PHash().key):
            raise ServiceError("result_invalid", "pHash producer đã đổi", 422)
        vectors, hashes = values
        return (FieldTable(feature.key, ids, vectors, "vector"), FieldTable(PHash().key, ids, hashes, "scalar"))
    return (FieldTable(feature.key, ids, values, "vector"),)
