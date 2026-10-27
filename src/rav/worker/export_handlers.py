"""Worker handlers cho tác vụ xuất dữ liệu: EXPORT_BUILD và EXPORT_FINALIZE (CONTRACTS §21.8).

EXPORT_BUILD:
- Tải/giải mã ảnh full-res cho từng member của snapshot.
- Phát hiện và che mờ PII (khuôn mặt và biển số xe).
- Sinh báo cáo tự chứa (HTML offline + JSON manifest).
- Chuẩn hóa canonical payload và tính payload_hash.
- Đẩy các bản preview lên S3 và xuất bản trạng thái NEEDS_CONFIRMATION.

EXPORT_FINALIZE:
- Đóng gói toàn bộ ảnh đã che mờ + báo cáo + manifest thành file ZIP bất biến.
- Đẩy file ZIP lên S3 canonical key.
- Xuất bản trạng thái READY.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
import zipfile
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from PIL import Image

from ..errors import ServiceError
from ..store import pilot_objects
from ..store.pilot_db import DEFAULT_SAMPLE_SPEC, PilotDB, canonical_hash
from ..store.pilot_export import read_pii_cache
from ..store.pilot_frames import ENCODING_VERSION
from ..store.pilot_runtime import AttemptLease, PilotRuntime, Progress
from ..store.pilot_storage import PilotStorage
from ..store.s3 import file_sha256 as _file_sha
from ..timing import emit, timing, traced_handler
from .pii import (
    PIIDetector,
    canonical_export_payload_bytes,
    generate_export_report_html,
    redact,
)


def pii_cache_hash(owner_id: UUID, member: dict, inputs: tuple[dict, dict], detector: PIIDetector) -> str:
    """Exact private pixel/anchor + model/config identity; no reuse across sources or processing modes."""
    sources, frames = inputs
    sid = UUID(member["source_id"])
    if sid not in sources or sources[sid]["core_source_id"] != member["core_source_id"]:
        raise ServiceError("result_invalid", "nguồn cache PII không hợp lệ", 422)
    frame = frames.get((sid, member["unit_id"])) or {}
    return canonical_hash({"owner_id": str(owner_id), "source_id": str(sid), "unit_id": member["unit_id"],
                           "raw_sha256": sources[sid]["raw_sha256"], "frame_sha256": frame.get("sha256"),
                           "spec_hash": DEFAULT_SAMPLE_SPEC["spec_hash"], "models": detector.model_manifest,
                           "config": detector.config})


def _verified_cache(storage: PilotStorage, cached: dict, path: Path, unit_id: str) -> dict | None:
    """Cache is optional: verify content before reusing it, recompute missing/corrupt objects from source."""
    meta = cached.get("manifest") or {}
    if meta.get("unit_id") != unit_id or not all(k in meta for k in ("width", "height", "frame_idx", "detections")):
        return None
    try:
        sha, size = storage.download(cached["object_key"], path, 100 << 20)
        if sha != cached["sha256"] or size != cached["size_bytes"]:
            return None
        with Image.open(path) as image:
            if image.size != (meta["width"], meta["height"]):
                return None
            image.verify()
        return meta
    except (ServiceError, OSError, ValueError):
        return None


class _RawFrames:
    """Fallback legacy: verify raw once, resolve exact sampled anchors, decode forward once per source."""

    def __init__(self, path: Path, core: str):
        from ..components.decoders.pyav import PyAV
        from .handlers import _probe

        video = _probe(path)
        if video.id != core:
            raise ServiceError("result_invalid", "nguồn export không khớp nội dung", 422)
        self.path = path
        self.indices = {f"{core}:{f.t:.3f}-{f.t:.3f}": f.idx for f in PyAV(DEFAULT_SAMPLE_SPEC["decoder"]["params"]).frames(video)}
        self.container = None
        self.iterator = iter(())
        self.last = -1

    def read(self, unit_id: str) -> tuple[Image.Image, int]:
        import av

        if unit_id not in self.indices:
            raise ServiceError("result_invalid", "unit export không thuộc lưới frame nguồn", 422)
        target = self.indices[unit_id]
        if self.container is None or target <= self.last:
            self.close()
            self.container = av.open(str(self.path))
            self.iterator = enumerate(self.container.decode(self.container.streams.video[0]))
        for idx, frame in self.iterator:
            self.last = idx
            if idx == target:
                return frame.to_image().convert("RGB"), idx
        raise ServiceError("result_invalid", "không giải mã được exact frame export", 422)

    def close(self):
        if self.container is not None:
            self.container.close()
            self.container = None


def _export_inputs(db: PilotDB, owner_id: UUID, members: list[dict]) -> tuple[dict, dict]:
    """Two metadata queries for selected members only, rather than round trips for every PII frame."""
    source_ids = list({UUID(m["source_id"]) for m in members})
    with db.backend() as cur:
        cur.execute("SELECT id, raw_key, raw_sha256, size_bytes, core_source_id FROM rav_pilot_source_asset "
                    "WHERE id = ANY(%s) AND (owner_id = %s OR scope = 'SYSTEM') AND availability = 'READY'",
                    (source_ids, owner_id))
        sources = {r["id"]: r for r in cur.fetchall()}
        cur.execute(
            "SELECT s.source_id, f.unit_id, f.object_key, f.frame_idx, f.sha256 FROM rav_pilot_frame f "
            "JOIN rav_pilot_frame_set s ON s.id = f.frame_set_id "
            "JOIN rav_pilot_source_asset src ON src.id = s.source_id "
            "WHERE s.source_id = ANY(%s) AND f.unit_id = ANY(%s) AND f.variant = 'FRAME' AND s.state = 'READY' "
            "AND s.raw_sha256 = src.raw_sha256 AND s.sample_spec_hash = %s AND s.encoding_version = %s "
            "AND (s.owner_id = %s OR s.scope = 'SYSTEM')",
            (source_ids, [m["unit_id"] for m in members], DEFAULT_SAMPLE_SPEC["spec_hash"], ENCODING_VERSION, owner_id),
        )
        frames = {(r["source_id"], r["unit_id"]): r for r in cur.fetchall()}
    return sources, frames


def _load_member_image(storage: PilotStorage, db: PilotDB, owner_id: UUID, member: dict[str, Any], work: Path,
                       raw_sources: dict, inputs: tuple[dict, dict] | None = None) -> tuple[Image.Image, int]:
    """Use the same verified FRAME as Studio; legacy raw fallback never interprets ord as frame index."""
    source_id, unit_id = UUID(member["source_id"]), member["unit_id"]
    sources, frames = inputs if inputs is not None else _export_inputs(db, owner_id, [member])
    src_row = sources.get(source_id)
    if not src_row or src_row["core_source_id"] != member["core_source_id"] or not unit_id.startswith(src_row["core_source_id"] + ":"):
        raise ServiceError("result_invalid", "nguồn hoặc unit export không hợp lệ", 422)
    row = frames.get((source_id, unit_id))
    if row and row.get("object_key"):
        dest = work / "member.jpg"
        try:
            sha, _ = storage.download(row["object_key"], dest, 100 << 20)
            if sha != row["sha256"]:
                raise ServiceError("result_invalid", "checksum frame export không khớp", 422)
            with Image.open(dest) as image:
                return image.convert("RGB"), row["frame_idx"]
        finally:
            dest.unlink(missing_ok=True)

    if source_id not in raw_sources:
        if not src_row.get("raw_key"):
            raise ServiceError("result_invalid", "thiếu ảnh và raw nguồn export", 422)
        video_dest = work / f"source_{source_id}.mp4"
        sha, _ = storage.download(src_row["raw_key"], video_dest, src_row["size_bytes"])
        if sha != src_row["raw_sha256"]:
            raise ServiceError("result_invalid", "checksum raw export không khớp", 422)
        raw_sources[source_id] = _RawFrames(video_dest, src_row["core_source_id"])
    return raw_sources[source_id].read(unit_id)


def make_export_handlers(storage: PilotStorage, db: PilotDB, workdir: Path) -> dict[str, Any]:
    runtime = PilotRuntime(db)

    def upload(lease, path, key, kind, variant, input_hash, *, source_id=None, manifest=None):
        sha = _file_sha(path)
        size = path.stat().st_size
        pilot_objects.intent_batch(db, lease, [(key, sha, size)], f"export:{lease.attempt_id}:{key}")
        storage.put_file(key, path, sha, "image/png" if kind == "EXPORT_PREVIEW" else
                         "application/zip" if kind == "EXPORT_BUNDLE" else
                         "text/html; charset=utf-8" if path.suffix == ".html" else "application/json")
        return {"id": str(uuid4()), "key": key, "sha256": sha, "size_bytes": size, "kind": kind,
                "variant": variant, "input_hash": input_hash, "source_id": source_id, "manifest": manifest or {}}

    @traced_handler
    def export_build(lease: AttemptLease, stop: threading.Event) -> dict[str, Any]:
        p = lease.payload
        detector = PIIDetector()
        detector.require_ready()   # fail-closed before any decode/upload (CONTRACTS §21.8 DEP6)
        export_id = UUID(p["export_id"])
        snapshot_id = UUID(p["snapshot_id"])
        decision_seq = p["decision_seq"]
        proposal_cutoff_n = p["proposal_cutoff_n"]
        members = p["members"]
        report_basis = p.get("report_basis", {})
        owner_id = UUID(p["owner_id"]) if p.get("owner_id") else None
        if owner_id is None:
            with db.backend() as cur:
                cur.execute("SELECT owner_id FROM rav_pilot_export WHERE id = %s", (export_id,))
                row = cur.fetchone()
                if row:
                    owner_id = row["owner_id"]
        if owner_id is None:
            raise ServiceError("validation_error", "không xác định được owner_id cho export", 422)

        work = workdir / str(lease.attempt_id)
        work.mkdir(parents=True, exist_ok=True)
        images_dir = work / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        raw_sources: dict = {}
        artifacts: list[dict] = []
        prefix = f"{storage.prefix}/private/{owner_id}/exports/{export_id}/{lease.attempt_id}/preview"
        try:
            runtime.update_progress(
                lease,
                Progress(sequence=1, phase="PREPARING_EXPORT", done=0, total=len(members)),
            )

            artifact_refs: list[dict[str, Any]] = []
            members_summary: list[dict[str, Any]] = []
            pii_evidence: list[dict[str, Any]] = []
            inputs = _export_inputs(db, owner_id, members)
            cache_hashes = {m["unit_id"]: pii_cache_hash(owner_id, m, inputs, detector) for m in members}
            caches = read_pii_cache(db, owner_id, list(cache_hashes.values()))
            started = time.monotonic()

            for i, member in enumerate(members):
                if stop.is_set():
                    return {}

                ord_idx = member.get("ord", i)
                unit_id = member["unit_id"]

                cache_hash = cache_hashes[unit_id]
                rel_path = f"images/{ord_idx:06d}.png"
                out_path = images_dir / f"{ord_idx:06d}.png"
                cached = caches.get(cache_hash)
                meta = _verified_cache(storage, cached, out_path, unit_id) if cached else None
                emit('worker.export.pii_cache', cache_hit=meta is not None)
                if meta is not None:
                    preview_key, output_sha = cached["object_key"], cached["sha256"]
                    width, height, frame_idx, regions = meta["width"], meta["height"], meta["frame_idx"], meta["detections"]
                else:
                    with timing("worker.export.frame_load"):
                        img, frame_idx = _load_member_image(storage, db, owner_id, member, work, raw_sources, inputs)
                    width, height = img.size
                    with timing("worker.export.pii"):
                        detections = detector.detect(img)
                        redacted_img, regions = redact(img, detections, detector.config)
                    redacted_img.save(out_path, format="PNG")
                    meta = {"unit_id": unit_id, "width": width, "height": height, "frame_idx": frame_idx,
                            "detections": regions, "config": detector.config, "models": detector.model_manifest,
                            "inference_size": list(getattr(detector, "last_inference_size", img.size))}
                    preview_key = f"{prefix}/{ord_idx:06d}.png"
                    with timing("worker.export.preview_put"):
                        art = upload(lease, out_path, preview_key, "EXPORT_PREVIEW", "PII_REDACTED", cache_hash,
                                     source_id=member["source_id"], manifest=meta)
                    artifacts.append(art)
                    output_sha = art["sha256"]

                artifact_refs.append({
                    "role": "member_image",
                    "frame_identity": "exact-source-v2",
                    "ordinal": ord_idx,
                    "unit_id": unit_id,
                    "key": preview_key,
                    "output_sha256": output_sha,
                    "output_relpath": rel_path,
                })
                members_summary.append({
                    "ordinal": ord_idx,
                    "core_unit_id": unit_id,
                    "source_id": member["source_id"],
                    "frame_idx": frame_idx,
                    "output_relpath": rel_path,
                    "output_sha256": output_sha,
                    "width": width,
                    "height": height,
                })
                pii_evidence.append({
                    "ordinal": ord_idx,
                    "status": "processed",
                    "detections": regions,
                    "model_id": detector.config["pipeline_version"],
                    "models": detector.model_manifest,
                    "config": detector.config,
                    "cache_hit": cached is not None and preview_key == cached["object_key"],
                    "inference_size": meta.get("inference_size"),
                })

                runtime.update_progress(
                    lease,
                    Progress(
                        sequence=i + 2,
                        phase="REDACTING_PII",
                        done=i + 1,
                        total=len(members),
                        elapsed_s=time.monotonic() - started,
                        eta_s=(time.monotonic() - started) / (i + 1) * (len(members) - i - 1),
                    ),
                )

            # 5. Sinh Canonical Payload & Báo cáo
            report_core = {
                "goal": report_basis.get("goal", ""),
                "requested": len(members),
                "returned": len(members),
                "decision_seq": decision_seq,
            }
            canonical_payload = {
                "format_version": 1,
                "export_id": str(export_id),
                "snapshot_id": str(snapshot_id),
                "decision_seq": decision_seq,
                "proposal_cutoff_n": proposal_cutoff_n,
                "members": members_summary,
                "pii_evidence": pii_evidence,
                "report_core": report_core,
            }
            payload_bytes = canonical_export_payload_bytes(canonical_payload)
            payload_hash = hashlib.sha256(payload_bytes).hexdigest()

            # Lưu canonical payload json lên S3
            payload_file = work / "payload.json"
            payload_file.write_bytes(payload_bytes)
            payload_key = f"{prefix}/payload.json"
            artifacts.append(upload(lease, payload_file, payload_key, "REPORT", "PII_PAYLOAD", payload_hash))
            artifact_refs.append({"role": "payload_json", "key": payload_key, "sha256": payload_hash})

            # Sinh report.html tự chứa
            report_html = generate_export_report_html(
                str(export_id),
                str(snapshot_id),
                payload_hash,
                len(members),
                members_summary,
                pii_evidence,
                report_core,
            )
            report_bytes = report_html.encode("utf-8")
            report_sha = hashlib.sha256(report_bytes).hexdigest()
            report_file = work / "report.html"
            report_file.write_bytes(report_bytes)
            report_key = f"{prefix}/report.html"
            artifacts.append(upload(lease, report_file, report_key, "REPORT", "PII_REPORT", payload_hash))
            artifact_refs.append({"role": "report_html", "key": report_key, "sha256": report_sha})

            # Sinh manifest.json
            manifest_dict = {
                "export_id": str(export_id),
                "snapshot_id": str(snapshot_id),
                "payload_hash": payload_hash,
                "n_members": len(members),
                "members": members_summary,
                "pii_models": detector.model_manifest,   # pinned files + SHA used for every image (§21.8 DEP6)
                "pii_evidence": pii_evidence,
            }
            manifest_bytes = json.dumps(manifest_dict, indent=2).encode("utf-8")
            manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
            manifest_file = work / "manifest.json"
            manifest_file.write_bytes(manifest_bytes)
            manifest_key = f"{prefix}/manifest.json"
            artifacts.append(upload(lease, manifest_file, manifest_key, "REPORT", "PII_MANIFEST", payload_hash))
            artifact_refs.append({"role": "manifest_json", "key": manifest_key, "sha256": manifest_sha})

            pilot_objects.written_batch(db, lease, [a["key"] for a in artifacts])
            return {
                "artifacts": artifacts,
                "payload_hash": payload_hash,
                "artifact_refs": artifact_refs,
                "n_members": len(members),
                "report_core_hash": report_sha,
                "manifest_hash": manifest_sha,
                "cleanup_done": True,
            }

        finally:
            for source in raw_sources.values():
                source.close()
            shutil.rmtree(work, ignore_errors=True)

    @traced_handler
    def export_finalize(lease: AttemptLease, stop: threading.Event) -> dict[str, Any]:
        p = lease.payload
        export_id = UUID(p["export_id"])
        review_receipt_id = UUID(p["review_receipt_id"])

        with db.backend() as cur:
            cur.execute("SELECT owner_id FROM rav_pilot_export WHERE id = %s", (export_id,))
            export_row = cur.fetchone()
            if not export_row:
                raise ServiceError("not_found", f"không tìm thấy export {export_id}", 404)
            owner_id = export_row["owner_id"]

            cur.execute("SELECT * FROM rav_pilot_export_review_receipt WHERE id = %s AND export_id=%s AND owner_id=%s",
                        (review_receipt_id, export_id, owner_id))
            receipt = cur.fetchone()
            if not receipt:
                raise ServiceError("not_found", f"không tìm thấy review receipt {review_receipt_id}", 404)
            cur.execute("SELECT 1 FROM rav_pilot_export_confirmation WHERE export_id=%s AND owner_id=%s "
                        "AND review_receipt_id=%s AND payload_hash=%s", (export_id, owner_id, review_receipt_id, p["payload_hash"]))
            if not cur.fetchone() or receipt["payload_hash"] != p["payload_hash"]:
                raise ServiceError("payload_mismatch", "gói dataset chưa được xác nhận đúng payload", 409)

        work = workdir / str(lease.attempt_id)
        work.mkdir(parents=True, exist_ok=True)
        zip_path = work / f"export_{str(export_id)[:8]}.zip"

        try:
            runtime.update_progress(
                lease,
                Progress(sequence=1, phase="PACKAGING_ZIP", done=0, total=1),
            )

            artifact_refs = receipt.get("artifact_refs") or []

            # Đóng gói zip file
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for ref in artifact_refs:
                    if stop.is_set():
                        return {}
                    key = ref["key"]
                    role = ref.get("role")
                    tmp_file = work / f"dl_{hashlib.sha256(key.encode()).hexdigest()[:10]}"
                    downloaded_sha, _ = storage.download(key, tmp_file, 100 << 20)
                    if downloaded_sha != ref.get("output_sha256", ref.get("sha256")):
                        raise ServiceError("result_invalid", "checksum ảnh/báo cáo đóng gói không khớp", 422)

                    if role == "member_image":
                        arcname = ref.get("output_relpath", f"images/{ref.get('ordinal', 0):06d}.png")
                        zf.write(tmp_file, arcname=arcname)
                    elif role == "report_html":
                        zf.write(tmp_file, arcname="report.html")
                    elif role == "manifest_json":
                        zf.write(tmp_file, arcname="manifest.json")
                    elif role == "payload_json":
                        zf.write(tmp_file, arcname="payload.json")
                    tmp_file.unlink(missing_ok=True)

            output_key = f"{storage.prefix}/private/{owner_id}/exports/{export_id}/{lease.attempt_id}/bundle.zip"
            art = upload(lease, zip_path, output_key, "EXPORT_BUNDLE", "ZIP", p["payload_hash"])
            pilot_objects.written_batch(db, lease, [output_key])

            return {
                "output_key": output_key,
                "output_size_bytes": art["size_bytes"],
                "output_sha256": art["sha256"],
                "artifacts": [art],
                "cleanup_done": True,
            }

        finally:
            shutil.rmtree(work, ignore_errors=True)

    return {
        "EXPORT_BUILD": export_build,
        "EXPORT_FINALIZE": export_finalize,
    }
