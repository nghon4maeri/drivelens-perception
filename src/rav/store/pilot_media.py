"""Media riêng tư của pilot (CONTRACTS §21.7, §21.12; runtime spec §6.2).

`request`: kiểm quyền nguồn + unit thuộc lưới decoder. `thumbnail`/`frame` (§21.20): lookup ảnh lưu sẵn của frame set READY →
presigned GET, chưa có → `NOT_PREPARED` (không job, không decode). `context` (mp4 H.264 ±3 s, ≤ 640 px): cache `(owner, input_hash)`;
READY → presigned GET ngắn hạn, chưa có → job `RENDER_MEDIA` (trả PENDING). `get` chỉ đọc/ký URL (id media cache hoặc id ảnh).
Worker publish clip qua `publish_in_tx`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from ..actor import ActorContext
from ..errors import ServiceError
from . import pilot_frames, pilot_storage
from .pilot_db import DEFAULT_SAMPLE_SPEC, PilotDB, canonical_hash, require_user
from .pilot_runtime import ACTIVE, EnqueueSpec, PilotRuntime
from .pilot_units import frame_index

CONTEXT_S = 3.0
DEFAULT_SIDE = {"THUMBNAIL": 320, "FRAME": None, "CONTEXT": 640}


class MediaRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: UUID
    unit_id: str = Field(min_length=1, max_length=300)
    variant: Literal["thumbnail", "frame", "context"]
    max_side: int | None = Field(default=None, ge=64, le=4096)


class MediaDTO(BaseModel):
    id: UUID | None   # None khi NOT_PREPARED (không có gì để poll)
    state: str
    job_id: UUID | None
    url: str | None
    expires_at: str | None
    sha256: str | None
    error: dict[str, Any] | None


class MediaBatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[MediaRequestIn] = Field(min_length=1, max_length=200)


class PilotMedia:
    def __init__(self, db: PilotDB, storage: Callable[[], pilot_storage.PilotStorage]):
        self.db, self.storage, self.runtime = db, storage, PilotRuntime(db)

    def request(self, actor: ActorContext, inp: MediaRequestIn) -> MediaDTO:
        from psycopg.types.json import Jsonb

        uid = require_user(actor)
        variant = inp.variant.upper()
        if variant != "CONTEXT":
            return self.request_batch(actor, [inp])[0]
        with self.db.backend() as cur:
            profile = self.db.lock_profile(cur, uid)
            src = self.db.lock_sources(cur, uid, [inp.source_id])[inp.source_id]
            if src["availability"] != "READY" or src["kind"] != "VIDEO":
                raise ServiceError("source_unavailable", f"nguồn đang {src['availability']}", 409)
            idx, t = frame_index(inp.unit_id, src["core_source_id"], src["metadata"] or {})
            params = {"variant": variant, "max_side": inp.max_side or DEFAULT_SIDE[variant], "idx": idx, "t": round(t, 6),
                      "context_s": CONTEXT_S if variant == "CONTEXT" else None, "decoder": DEFAULT_SAMPLE_SPEC["decoder"]}
            input_hash = canonical_hash({"owner": str(uid), "source": str(src["id"]), "version": src["resource_version"],
                                         "raw_sha256": src["raw_sha256"], "unit_id": inp.unit_id, "params": params})
            cur.execute("SELECT * FROM rav_pilot_media WHERE owner_id = %s AND input_hash = %s FOR UPDATE", (uid, input_hash))
            media = cur.fetchone()
            if media and media["state"] != "FAILED":
                return self._dto(cur, media, profile)
            media_id = media["id"] if media else uuid4()
            attempt = (media["params"].get("attempt", 0) + 1) if media else 1
            if media is None:
                cur.execute("INSERT INTO rav_pilot_media (id, owner_id, source_id, unit_id, variant, params, input_hash, state) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s, 'PENDING')",
                            (media_id, uid, src["id"], inp.unit_id, variant, Jsonb({**params, "attempt": 1}), input_hash))
            job = self.runtime.enqueue_in_tx(cur, uid, EnqueueSpec(
                kind="RENDER_MEDIA", resource_id=src["id"], idempotency_key=f"media:{media_id}:{attempt}",
                payload={"media_id": str(media_id), "owner_id": str(uid), "source_id": str(src["id"]), "raw_key": src["raw_key"],
                         "raw_sha256": src["raw_sha256"], "fps": src["metadata"]["fps"], "n_frames": src["metadata"]["n_frames"],
                         **params}), [src["id"]])
            cur.execute("UPDATE rav_pilot_media SET state = 'PENDING', job_id = %s, error = NULL, params = %s, updated_at = now() "
                        "WHERE id = %s RETURNING *", (job.id, Jsonb({**params, "attempt": attempt}), media_id))
            return self._dto(cur, cur.fetchone(), profile)

    def get(self, actor: ActorContext, media_id: UUID) -> MediaDTO:
        uid = require_user(actor)
        with self.db.backend() as cur:
            profile = self.db.read_profile(cur, uid)
            cur.execute("SELECT * FROM rav_pilot_media WHERE id = %s AND owner_id = %s", (media_id, uid))
            media = cur.fetchone()
            if media:
                return self._dto(cur, media, profile)
            frame = pilot_frames.frame_by_id_in_tx(cur, uid, media_id)
            if not frame:
                raise ServiceError("not_found", f"không tìm thấy media {media_id}", 404)
            dto = MediaDTO(id=frame["id"], state="READY", job_id=None, url=None, expires_at=None, sha256=None, error=None)
            if frame["set_state"] != "READY" or frame["availability"] != "READY":
                return dto.model_copy(update={"state": "FAILED", "error": _UNAVAILABLE})
            return self._signed(cur, dto, frame["object_key"], frame["sha256"], profile)

    def request_batch(self, actor: ActorContext, items: list[MediaRequestIn]) -> list[MediaDTO]:
        """Read/sign stored images in order, bounded batch, no object keys in response (§21.23)."""
        uid = require_user(actor)
        if not items or len(items) > 200 or any(i.variant == "context" for i in items):
            raise ServiceError("validation_error", "batch chỉ gồm 1–200 thumbnail/frame", 422)
        with self.db.backend() as cur:
            profile = self.db.read_profile(cur, uid)
            _, cfg = self.runtime._policy(cur, profile)
            if len(items) > cfg["max_media_requests"]:
                raise ServiceError("validation_error", "batch vượt max_media_requests", 422,
                                   {"max_items": cfg["max_media_requests"]})
            ids = sorted({i.source_id for i in items})
            cur.execute("SELECT * FROM rav_pilot_source_asset WHERE id=ANY(%s)", (ids,))
            sources = {s["id"]: s for s in cur.fetchall()}
            wanted = []
            for inp in items:
                src = sources.get(inp.source_id)
                if src is None or not self.db.can_read_source_in_tx(cur, uid, src):
                    raise ServiceError("not_found", "không tìm thấy nguồn", 404)
                if src["availability"] != "READY" or src["kind"] != "VIDEO":
                    raise ServiceError("source_unavailable", "nguồn không sẵn sàng", 409)
                idx, _ = frame_index(inp.unit_id, src["core_source_id"], src["metadata"] or {})
                wanted.append((src["id"], pilot_frames.stored_variant(inp.variant, inp.max_side), idx))
            cur.execute("SELECT s.source_id, f.variant, f.frame_idx, f.id, f.object_key, f.sha256 FROM rav_pilot_frame_set s "
                        "JOIN rav_pilot_source_asset src ON src.id=s.source_id JOIN rav_pilot_frame f ON f.frame_set_id=s.id "
                        "JOIN unnest(%s::uuid[], %s::text[], %s::int[]) w(source_id,variant,frame_idx) "
                        "ON w.source_id=s.source_id AND w.variant=f.variant AND w.frame_idx=f.frame_idx "
                        f"WHERE {pilot_frames._ready_set_sql()} AND src.availability='READY'",
                        ([w[0] for w in wanted], [w[1] for w in wanted], [w[2] for w in wanted],
                         DEFAULT_SAMPLE_SPEC["spec_hash"], pilot_frames.ENCODING_VERSION))
            frames = {(f["source_id"], f["variant"], f["frame_idx"]): f for f in cur.fetchall()}
            jobs = {}
            missing = sorted({key[0] for key in wanted if key not in frames})
            if missing:
                cur.execute("SELECT resource_id, id FROM rav_pilot_job WHERE owner_id=%s AND kind='PREPARE_FRAMES' "
                            "AND resource_id=ANY(%s) AND state=ANY(%s) ORDER BY created_at", (uid, missing, list(ACTIVE)))
                for job in cur.fetchall():
                    jobs.setdefault(job["resource_id"], job["id"])
            ttl = max(1, cfg["user_get_ttl_ms"] // 1000)
            cur.execute("SELECT now() + %s * interval '1 second' AS e", (ttl,))
            expires = cur.fetchone()["e"].isoformat()
            out = []
            storage = self.storage() if frames else None
            for key in wanted:
                frame = frames.get(key)
                out.append(MediaDTO(id=frame["id"] if frame else None, state="READY" if frame else "NOT_PREPARED",
                                    job_id=None if frame else jobs.get(key[0]), url=storage.presign_get(frame["object_key"], ttl) if frame else None,
                                    expires_at=expires if frame else None, sha256=frame["sha256"] if frame else None, error=None))
            return out

    def _dto(self, cur, media: dict, profile: dict) -> MediaDTO:
        dto = MediaDTO(id=media["id"], state=media["state"], job_id=media["job_id"] if media["state"] == "PENDING" else None,
                       url=None, expires_at=None, sha256=None, error=media["error"])
        if media["state"] != "READY":
            return dto
        cur.execute("SELECT a.object_key, a.sha256, a.state, s.availability FROM rav_pilot_artifact a "
                    "JOIN rav_pilot_source_asset s ON s.id = a.source_id WHERE a.id = %s", (media["artifact_id"],))
        art = cur.fetchone()
        if art["state"] != "READY" or art["availability"] != "READY":   # nguồn chờ xóa/đã xóa: không cấp URL mới
            return dto.model_copy(update={"state": "FAILED", "error": _UNAVAILABLE})
        return self._signed(cur, dto, art["object_key"], art["sha256"], profile)

    def _signed(self, cur, dto: MediaDTO, key: str, sha: str, profile: dict) -> MediaDTO:
        _, cfg = self.runtime._policy(cur, profile)
        ttl = max(1, cfg["user_get_ttl_ms"] // 1000)
        cur.execute("SELECT (now() + %s * interval '1 second') AS e", (ttl,))
        expires = cur.fetchone()["e"].isoformat()
        return dto.model_copy(update={"url": self.storage().presign_get(key, ttl), "expires_at": expires, "sha256": sha})


_UNAVAILABLE = {"code": "source_unavailable", "message": "nguồn không còn sẵn sàng", "retryable": False}


# ------------------------------------------------------------------------------
# Hook worker
# ------------------------------------------------------------------------------
def publish_in_tx(cur, job: dict, lease, outputs: dict) -> UUID:
    """outputs["artifact"] = {id, object_key, sha256, size_bytes, content_type}; trả artifact id (job.result_ref)."""
    from psycopg.types.json import Jsonb

    a = outputs["artifact"]
    cur.execute("SELECT * FROM rav_pilot_media WHERE id = %s FOR UPDATE", (UUID(job["payload"]["media_id"]),))
    media = cur.fetchone()
    if media["state"] != "PENDING" or media["job_id"] != job["id"]:
        raise ServiceError("stale_input", "media không còn chờ job này", 409)
    if not re.fullmatch(r"[0-9a-f]{64}", a["sha256"]) or a["size_bytes"] <= 0:
        raise ServiceError("result_invalid", "artifact media không hợp lệ", 422)
    artifact_id = UUID(a["id"])
    cur.execute("INSERT INTO rav_pilot_artifact (id, owner_id, scope, source_id, job_id, attempt_id, kind, variant, input_hash, "
                "object_key, sha256, size_bytes, state, policy_version, manifest) "
                "VALUES (%s, %s, 'PRIVATE', %s, %s, %s, 'MEDIA', %s, %s, %s, %s, %s, 'READY', %s, %s)",
                (artifact_id, job["owner_id"], media["source_id"], job["id"], lease.attempt_id, media["variant"], media["input_hash"],
                 a["object_key"], a["sha256"], a["size_bytes"], job["policy_version"],
                 Jsonb({"content_type": a["content_type"], "params": media["params"]})))
    from .pilot_objects import accept_in_tx
    accept_in_tx(cur, lease, a["object_key"], a["sha256"], a["size_bytes"])
    pilot_storage.release_job_holds(cur, job["id"], persisted=a["size_bytes"])
    cur.execute("UPDATE rav_pilot_media SET state = 'READY', artifact_id = %s, updated_at = now() WHERE id = %s",
                (artifact_id, media["id"]))
    return artifact_id


def finish_in_tx(cur, job: dict, state: str, error: dict | None) -> None:
    from psycopg.types.json import Jsonb

    cur.execute("UPDATE rav_pilot_media SET state = 'FAILED', error = %s, updated_at = now() "
                "WHERE id = %s AND job_id = %s AND state = 'PENDING'",
                (Jsonb(error or {"code": "cancelled", "message": "job media đã hủy", "retryable": True}),
                 UUID(job["payload"]["media_id"]), job["id"]))
