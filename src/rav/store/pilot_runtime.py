"""Hàng đợi bền + quota 4h + yêu cầu xóa nguồn của pilot (CONTRACTS §21.5–21.6, §21.9, §21.11; runtime spec §3–5).

- Enqueue một transaction: khóa profile → nguồn (UUID tăng dần) → cửa sổ quota/bonus → job; giữ hold base trước rồi bonus FIFO,
  ghim nguồn (pin JOB), idempotent theo (owner, kind, Idempotency-Key). Thiếu/sai policy → 503 `policy_unconfigured` (không số mặc định).
- Worker: `claim` (slot + job `FOR UPDATE SKIP LOCKED`, owner round-robin qua `rav_pilot_dispatch_cursor`) → `heartbeat` → `publish`/`finish`.
  Mọi lệnh worker kiểm worker/fence/slot generation/lease còn hạn. Hold tích lũy consumed, kết thúc thì nhả phần dư về đúng cửa sổ gốc.
- Lease hết hạn: attempt LOST, usage UNKNOWN, slot QUARANTINED cho tới `reconcile_slot` có bằng chứng process đã dừng.
Không có ML import; chỉ DB. Chạy bởi API (role `rav_pilot_api`) và worker (role `rav_pilot_worker`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from ..actor import ActorContext
from ..errors import ServiceError
from ..timing import timing
from . import pilot_storage
from .pilot_db import PilotDB, canonical_hash, require_user

JobKind = Literal["VALIDATE_UPLOAD", "RENDER_MEDIA", "PREPARE_DINO", "PREPARE_CLIP", "PROPOSE", "YOLO_BATCH", "METRICS",
                  "EXPORT_BUILD", "EXPORT_FINALIZE", "DELETE_SOURCE", "PREPARE_FRAMES", "MODEL_VERIFY"]
QUEUE_KINDS = {"GPU": ("PREPARE_DINO", "PREPARE_CLIP"), "MEDIA": ("RENDER_MEDIA",),
               "CPU": ("VALIDATE_UPLOAD", "PROPOSE", "YOLO_BATCH", "METRICS", "EXPORT_BUILD", "EXPORT_FINALIZE", "DELETE_SOURCE",
                       "PREPARE_FRAMES", "MODEL_VERIFY")}
ACTIVE = ("QUEUED", "WAITING_RESOURCE", "RUNNING", "CANCEL_REQUESTED")
GPU_WAIT = ("gpu_budget_unverified", "gpu_budget_exhausted")   # job GPU chờ ngân sách, claim xét lại (§21.14)
TERMINAL = ("SUCCEEDED", "FAILED", "CANCELLED")
MAX_ATTEMPTS = 2
WINDOW_S = 14_400
# Khóa bắt buộc của policy (runtime spec §3.2); giá trị lấy từ đo DEP1, không có mặc định.
POLICY_INT_KEYS = ("cpu_base_ms", "gpu_base_ms", "pending_per_user", "heavy_active_per_user", "storage_user_bytes",
                   "storage_global_bytes", "max_video_bytes", "max_duration_ms", "max_width", "max_height", "max_decoded_pixels",
                   "max_zip_bytes", "max_media_requests", "lease_ms", "heartbeat_ms", "queue_deadline_ms", "stop_deadline_ms",
                   "cleanup_deadline_ms", "user_get_ttl_ms", "upload_ttl_ms", "remote_url_ttl_ms", "staging_grace_ms",
                   "gpu_snapshot_fresh_ms", "cost_snapshot_fresh_ms", "gpu_headroom_ms", "worker_rss_bytes", "worker_temp_bytes")
POLICY_MAP_KEYS = ("slot_inventory", "run_deadline_ms_by_kind", "profiles_by_kind", "chunk_bounds_by_kind")


def _hook(kind: str):
    """Phần publish/kết thúc riêng theo kind, chạy trong cùng transaction (import lười để tránh vòng import)."""
    if kind == "VALIDATE_UPLOAD":
        from . import pilot_uploads
        return pilot_uploads
    if kind == "RENDER_MEDIA":
        from . import pilot_media
        return pilot_media
    if kind == "DELETE_SOURCE":
        from . import pilot_delete
        return pilot_delete
    if kind in ("PREPARE_DINO", "PREPARE_CLIP"):
        from . import pilot_features
        return pilot_features
    if kind == "PREPARE_FRAMES":
        from . import pilot_frames
        return pilot_frames
    if kind == "PROPOSE":
        from . import pilot_curation
        return pilot_curation
    if kind in ("YOLO_BATCH", "METRICS"):
        from . import pilot_analysis
        return pilot_analysis
    if kind in ("EXPORT_BUILD", "EXPORT_FINALIZE"):
        from . import pilot_export
        return pilot_export
    if kind == "MODEL_VERIFY":
        from . import pilot_models
        return pilot_models
    return None


def queue_of(kind: str) -> str:
    return next(q for q, kinds in QUEUE_KINDS.items() if kind in kinds)


def policy_problem(config: Any) -> str | None:
    """None nếu config đủ khóa và hợp lệ; ngược lại mô tả (→ policy_unconfigured)."""
    if not isinstance(config, dict):
        return "config không phải object"
    for k in POLICY_INT_KEYS:
        v = config.get(k)
        if k == 'cpu_base_ms' and config.get('cpu_accounting_enabled', True) is False and v == 0 and not isinstance(v, bool):
            continue
        if k in ('storage_user_bytes', 'storage_global_bytes') and config.get('storage_accounting_enabled', True) is False and v == 0 and not isinstance(v, bool):
            continue
        if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
            return f"thiếu hoặc sai {k}"
    if not isinstance(config.get('cpu_accounting_enabled', True), bool):
        return 'cpu_accounting_enabled phải boolean'
    if not isinstance(config.get('storage_accounting_enabled', True), bool):
        return 'storage_accounting_enabled phải boolean'
    for k in POLICY_MAP_KEYS:
        if not isinstance(config.get(k), dict) or not config[k]:
            return f"thiếu {k}"
    fa = config.get("forecast_admission_usd")
    if not isinstance(fa, int | float) or isinstance(fa, bool) or fa <= 0:
        return "thiếu hoặc sai forecast_admission_usd"
    if config["heartbeat_ms"] >= config["lease_ms"]:
        return "heartbeat_ms phải nhỏ hơn lease_ms"
    return None


# ------------------------------------------------------------------------------
# DTO (runtime spec §4)
# ------------------------------------------------------------------------------
class WorkerContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    worker_id: UUID
    host_boot_id: UUID


class EnqueueSpec(BaseModel):
    """Do server dựng (không nhận thẳng từ HTTP body); policy/deadline thuộc server."""
    model_config = ConfigDict(extra="forbid")

    kind: JobKind
    resource_id: UUID | None = None
    idempotency_key: str = Field(min_length=1, max_length=200)
    payload_version: Literal["rav.runtime.job.v1"] = "rav.runtime.job.v1"
    payload: dict[str, Any] = Field(default_factory=dict)
    expected_resource_version: int | None = None


class PhaseTiming(BaseModel):
    phase: str = Field(pattern=r'^[A-Z_]{1,40}$')
    started_at: str
    elapsed_ms: float = Field(ge=0)
    state: Literal['running', 'done', 'error']
    done: int = Field(ge=0)
    total: int = Field(ge=0)
    cache_hit: bool | None = None


class Progress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=0)
    phase: str
    done: int = Field(ge=0)
    total: int = Field(ge=0)
    fps: float | None = None
    elapsed_s: float | None = None
    eta_s: float | None = None
    detail: str | None = None
    stages: list[PhaseTiming] | None = Field(None, max_length=24)


class JobDTO(BaseModel):
    id: UUID
    kind: str
    state: str
    state_version: int
    phase: str
    progress: dict[str, Any]
    attempt_n: int
    waiting_reason: str | None
    error: dict[str, Any] | None
    resource_id: UUID | None
    result_ref: UUID | None
    accepted_state_version: int | None = None


class AttemptLease(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    kind: str
    attempt_id: UUID
    attempt_n: int
    fence: int
    worker_id: UUID
    slot_key: str
    slot_generation: int
    lease_until: datetime
    payload: dict[str, Any]
    policy_config: dict[str, Any] | None = None   # internal claim metadata, not part of the frozen job payload
    queue_ms: float | None = None
    process_recorded: bool = False


class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cpu_ms: int | None = Field(default=None, ge=0)
    gpu_ms: int | None = Field(default=None, ge=0)
    state: Literal["OPEN", "UNKNOWN", "FINAL"]
    evidence_hash: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)


class ReserveDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cpu_ms: int = Field(default=0, ge=0)
    gpu_ms: int = Field(default=0, ge=0)
    storage_bytes: int = Field(default=0, ge=0)


class PublicationReceipt(BaseModel):
    id: UUID
    replayed: bool
    job: JobDTO


class SlotEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    process_exit_verified: bool
    cpu_ms: int | None = Field(default=None, ge=0)
    gpu_ms: int | None = Field(default=None, ge=0)
    proof: dict[str, Any] = Field(default_factory=dict)


class QuotaAmounts(BaseModel):
    enabled: bool = True
    base_available: int
    bonus_available: int
    held: int


class QuotaDTO(BaseModel):
    server_time: datetime
    next_reset_at: datetime
    cpu: QuotaAmounts
    gpu: QuotaAmounts
    admission: dict[str, Any]


class DeleteReceipt(BaseModel):
    id: UUID
    source_id: UUID
    state: str
    job_id: UUID | None
    error: dict[str, Any] | None


def _job_dto(row: dict) -> JobDTO:
    detail = row.get("progress_detail") or {}
    prog: dict[str, Any] = {"done": row["done"], "total": row["total"]}
    for k in ("fps", "elapsed_s", "eta_s", "detail", "stages"):
        if k in detail and detail[k] is not None:
            prog[k] = detail[k]
    return JobDTO(id=row["id"], kind=row["kind"], state=row["state"], state_version=row["state_version"], phase=row["phase"],
                  progress=prog, attempt_n=row["attempt_n"],
                  waiting_reason=row["waiting_reason"], error=row["error"], resource_id=row["resource_id"],
                  result_ref=row["result_ref"], accepted_state_version=(row.get("payload") or {}).get("state_version") if row["kind"] == "PROPOSE" else None)


class PilotRuntime:
    def __init__(self, db: PilotDB):
        self.db = db
        self._propose_rpc: bool | None = None
        self._claim_rpc: bool | None = None
        self._has_progress_detail: bool | None = None

    # --------------------------------------------------------------------------
    # Policy và cửa sổ quota
    # --------------------------------------------------------------------------
    @staticmethod
    def _policy(cur, profile: dict) -> tuple[str, dict]:
        if profile.get("policy_id") is None:
            raise ServiceError("policy_unconfigured", "tài khoản chưa được gán quota policy", 503)
        cur.execute("SELECT version, config, enabled FROM rav_pilot_quota_policy WHERE id = %s", (profile["policy_id"],))
        row = cur.fetchone()
        if not row or not row["enabled"]:
            raise ServiceError("policy_unconfigured", "quota policy chưa bật", 503)
        if problem := policy_problem(row["config"]):
            raise ServiceError("policy_unconfigured", f"quota policy chưa đủ: {problem}", 503)
        return row["version"], row["config"]

    @staticmethod
    def _window(cur, owner: UUID, resource: str, base_ms: int, lock: bool) -> dict:
        """Cửa sổ UTC cố định 4 giờ theo giờ DB (§21.6); tạo lười, không cộng dồn base."""
        cur.execute("INSERT INTO rav_pilot_quota_window (id, owner_id, resource, window_start, window_end, base_ms) "
                    "SELECT %s, %s, %s, ws, ws + interval '4 hours', %s "
                    "FROM (SELECT to_timestamp(floor(extract(epoch FROM now()) / %s) * %s) AS ws) w "
                    "ON CONFLICT (owner_id, resource, window_start) DO NOTHING",
                    (uuid4(), owner, resource, base_ms, WINDOW_S, WINDOW_S))
        cur.execute("SELECT * FROM rav_pilot_quota_window WHERE owner_id = %s AND resource = %s "
                    "AND window_start = to_timestamp(floor(extract(epoch FROM now()) / %s) * %s)"
                    + (" FOR UPDATE" if lock else ""), (owner, resource, WINDOW_S, WINDOW_S))
        return cur.fetchone()

    def read_quota(self, actor: ActorContext) -> QuotaDTO:
        uid = require_user(actor)
        with self.db.backend() as cur:
            profile = self.db.lock_profile(cur, uid)
            _, cfg = self._policy(cur, profile)
            amounts = {}
            for res in ("CPU", "GPU"):
                if res == 'CPU' and cfg.get('cpu_accounting_enabled', True) is False:
                    amounts[res] = QuotaAmounts(base_available=0, bonus_available=0, held=0, enabled=False)
                    continue
                w = self._window(cur, uid, res, cfg[f"{res.lower()}_base_ms"], lock=False)
                cur.execute("SELECT coalesce(sum(granted_ms - held_ms - consumed_ms), 0) AS b FROM rav_pilot_quota_bonus "
                            "WHERE owner_id = %s AND resource = %s", (uid, res))
                amounts[res] = QuotaAmounts(base_available=w["base_ms"] - w["held_ms"] - w["consumed_ms"],
                                            bonus_available=int(cur.fetchone()["b"]), held=w["held_ms"])
                end = w["window_end"]
            cur.execute("SELECT now() AS t")
            now = cur.fetchone()["t"]
            cur.execute("SELECT 1 FROM rav_pilot_job j JOIN rav_pilot_job_attempt a ON a.job_id = j.id "
                        "WHERE j.owner_id = %s AND a.usage_state = 'UNKNOWN' LIMIT 1", (uid,))
            blocked = cur.fetchone() is not None
        return QuotaDTO(server_time=now, next_reset_at=end, cpu=amounts["CPU"], gpu=amounts["GPU"],
                        admission={"allowed": not blocked, "reason": "quota_reconcile_required" if blocked else None})

    # --------------------------------------------------------------------------
    # Enqueue (một transaction)
    # --------------------------------------------------------------------------
    def enqueue(self, actor: ActorContext, spec: EnqueueSpec, source_ids: list[UUID] | None = None) -> JobDTO:
        """`source_ids` do caller phía server giải ra từ resource (không lấy từ body)."""
        uid = require_user(actor)
        with self.db.backend() as cur:
            return self.enqueue_in_tx(cur, uid, spec, source_ids or [])

    def enqueue_in_tx(self, cur, uid: UUID, spec: EnqueueSpec, source_ids: list[UUID]) -> JobDTO:
        from psycopg import errors as pg_errors
        from psycopg.types.json import Jsonb

        profile = self.db.lock_profile(cur, uid)
        sources = self.db.lock_sources(cur, uid, source_ids)
        input_hash = canonical_hash({"kind": spec.kind, "resource_id": str(spec.resource_id) if spec.resource_id else None,
                                     "payload_version": spec.payload_version, "payload": spec.payload,
                                     "sources": sorted([str(s), sources[s]["resource_version"]] for s in sources)})
        cur.execute("SELECT * FROM rav_pilot_job WHERE owner_id = %s AND kind = %s AND idempotency_key = %s",
                    (uid, spec.kind, spec.idempotency_key))
        if old := cur.fetchone():
            if old["input_hash"] != input_hash:
                raise ServiceError("idempotency_conflict", "Idempotency-Key đã dùng cho đầu vào khác", 409)
            return _job_dto(old)
        for s in sources.values():
            wanted = "DELETE_PENDING" if spec.kind == "DELETE_SOURCE" else "READY"
            if s["availability"] != wanted:
                raise ServiceError("source_unavailable", f"nguồn {s['id']} đang {s['availability']}", 409)
        if spec.expected_resource_version is not None and spec.resource_id in sources \
                and sources[spec.resource_id]["resource_version"] != spec.expected_resource_version:
            raise ServiceError("stale_input", "resource_version đã đổi", 409)
        policy_version, cfg = self._policy(cur, profile)
        cur.execute("SELECT 1 FROM rav_pilot_job j JOIN rav_pilot_job_attempt a ON a.job_id = j.id "
                    "WHERE j.owner_id = %s AND a.usage_state = 'UNKNOWN' LIMIT 1", (uid,))
        if cur.fetchone():
            raise ServiceError("quota_reconcile_required", "usage chưa tất toán; chờ đối soát", 429)
        media = spec.kind == "RENDER_MEDIA"   # media có giới hạn riêng, không chiếm chỗ job nặng (CONTRACTS §21.12)
        cur.execute("SELECT count(*) AS n FROM rav_pilot_job WHERE owner_id = %s AND state = ANY(%s) AND "
                    + ("kind = 'RENDER_MEDIA'" if media else "kind <> 'RENDER_MEDIA'"), (uid, list(ACTIVE)))
        limit = cfg["max_media_requests"] if media else cfg["pending_per_user"]
        if cur.fetchone()["n"] >= limit:
            raise ServiceError("quota_exceeded", f"đã có {limit} job {'media ' if media else ''}đang chờ/chạy", 429)
        est = cfg["profiles_by_kind"].get(spec.kind)
        if not isinstance(est, dict) or not all(isinstance(est.get(k), int) and est[k] >= 0 for k in ("cpu_ms", "gpu_ms")):
            raise ServiceError("policy_unconfigured", f"policy thiếu ước lượng cho {spec.kind}", 503)
        job_id = uuid4()
        cur.execute("SELECT now() + %s * interval '1 millisecond' AS d", (cfg["queue_deadline_ms"],))
        deadline = cur.fetchone()["d"]
        try:
            with cur.connection.transaction():   # savepoint: trùng job đang chạy cùng input → trả job đó
                cpu_free = cfg.get('cpu_accounting_enabled', True) is False
                storage_free = cfg.get('storage_accounting_enabled', True) is False
                extra_cols = ""
                extra_vals = ""
                if cpu_free:
                    extra_cols += ", cpu_accounting_enabled"
                    extra_vals += ", false"
                if storage_free:
                    extra_cols += ", storage_accounting_enabled"
                    extra_vals += ", false"
                cur.execute("INSERT INTO rav_pilot_job (id, owner_id, kind, resource_id, input_hash, payload_version, payload, "
                            f"idempotency_key, policy_version, state, queue_deadline{extra_cols}) "
                            f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'QUEUED', %s{extra_vals})",
                            (job_id, uid, spec.kind, spec.resource_id, input_hash, spec.payload_version, Jsonb(spec.payload),
                             spec.idempotency_key, policy_version, deadline))
        except pg_errors.UniqueViolation:
            cur.execute("SELECT * FROM rav_pilot_job WHERE owner_id = %s AND kind = %s AND resource_id IS NOT DISTINCT FROM %s "
                        "AND input_hash = %s AND state = ANY(%s)", (uid, spec.kind, spec.resource_id, input_hash, list(ACTIVE)))
            return _job_dto(cur.fetchone())
        for res in ("CPU", "GPU"):
            if res == 'CPU' and cfg.get('cpu_accounting_enabled', True) is False:
                continue
            self._reserve(cur, uid, job_id, res, est[f"{res.lower()}_ms"], cfg[f"{res.lower()}_base_ms"])
        if isinstance(est.get("output_bytes"), int) and est["output_bytes"] > 0:
            scope = (spec.payload or {}).get("target_scope", "PRIVATE")
            pilot_storage.reserve(cur, uid, cfg, est["output_bytes"], f"job:{job_id}:output", job_id=job_id, scope=scope)
        if spec.kind != "DELETE_SOURCE":   # job xóa không tự ghim nguồn của chính nó (runtime spec §5.1)
            for s in sources.values():
                self.db.pin(cur, s["id"], "JOB", job_id, "INPUT", s["resource_version"])
        cur.execute("SELECT * FROM rav_pilot_job WHERE id = %s", (job_id,))
        return _job_dto(cur.fetchone())

    def _reserve(self, cur, uid: UUID, job_id: UUID, res: str, need: int, base_ms: int) -> None:
        if need <= 0:
            return
        w = self._window(cur, uid, res, base_ms, lock=True)
        cur.execute("SELECT * FROM rav_pilot_quota_bonus WHERE owner_id = %s AND resource = %s "
                    "AND granted_ms > held_ms + consumed_ms ORDER BY created_at, id FOR UPDATE", (uid, res))
        bonuses = cur.fetchall()
        base_left = w["base_ms"] - w["held_ms"] - w["consumed_ms"]
        if need > base_left + sum(b["granted_ms"] - b["held_ms"] - b["consumed_ms"] for b in bonuses):
            raise ServiceError("quota_exceeded", f"hết quota {res} trong cửa sổ 4 giờ", 429,
                               {"next_reset_at": w["window_end"].isoformat()})
        cur.execute("SELECT id FROM rav_pilot_quota_hold WHERE job_id = %s AND resource = %s AND state = 'OPEN' FOR UPDATE",
                    (job_id, res))
        existing = cur.fetchone()
        hold_id = existing["id"] if existing else uuid4()
        if existing:
            cur.execute("UPDATE rav_pilot_quota_hold SET reserved_ms = reserved_ms + %s WHERE id = %s", (need, hold_id))
        else:
            cur.execute("INSERT INTO rav_pilot_quota_hold (id, job_id, resource, reserved_ms) VALUES (%s, %s, %s, %s)",
                        (hold_id, job_id, res, need))
        cur.execute("SELECT coalesce(max(part_n), 0) AS n FROM rav_pilot_quota_hold_part WHERE hold_id = %s", (hold_id,))
        first_part = cur.fetchone()["n"] + 1
        parts, left = [], need
        if (take := min(base_left, left)) > 0:
            parts.append(("window_id", w["id"], take))
            cur.execute("UPDATE rav_pilot_quota_window SET held_ms = held_ms + %s WHERE id = %s", (take, w["id"]))
            left -= take
        for b in bonuses:
            if left <= 0:
                break
            take = min(b["granted_ms"] - b["held_ms"] - b["consumed_ms"], left)
            parts.append(("bonus_id", b["id"], take))
            cur.execute("UPDATE rav_pilot_quota_bonus SET held_ms = held_ms + %s WHERE id = %s", (take, b["id"]))
            left -= take
        for n, (col, ref, amount) in enumerate(parts, start=first_part):
            cur.execute(f"INSERT INTO rav_pilot_quota_hold_part (hold_id, part_n, {col}, reserved_ms) VALUES (%s, %s, %s, %s)",
                        (hold_id, n, ref, amount))

    # --------------------------------------------------------------------------
    # Tất toán hold (consumed tích lũy, phần dư trả đúng nguồn gốc)
    # --------------------------------------------------------------------------
    @staticmethod
    def _consume(cur, job_id: UUID, res: str, ms: int) -> None:
        if ms <= 0:
            return
        cur.execute("SELECT * FROM rav_pilot_quota_hold WHERE job_id = %s AND resource = %s AND state = 'OPEN' FOR UPDATE",
                    (job_id, res))
        hold = cur.fetchone()
        if not hold or ms > hold["reserved_ms"] - hold["consumed_ms"] - hold["released_ms"]:
            raise ServiceError("quota_exceeded", "actual chưa được giữ đủ quota", 429)
        take = ms
        with timing("db.pipeline"), cur.connection.pipeline():
            cur.execute("UPDATE rav_pilot_quota_hold SET consumed_ms = consumed_ms + %s WHERE id = %s", (take, hold["id"]))
            cur.execute("SELECT * FROM rav_pilot_quota_hold_part WHERE hold_id = %s ORDER BY part_n FOR UPDATE", (hold["id"],))
            parts = cur.fetchall()
        with timing("db.pipeline"), cur.connection.pipeline():
            for p in parts:
                if take <= 0:
                    break
                amount = min(take, p["reserved_ms"] - p["consumed_ms"] - p["released_ms"])
                if amount <= 0:
                    continue
                cur.execute("UPDATE rav_pilot_quota_hold_part SET consumed_ms = consumed_ms + %s WHERE hold_id = %s AND part_n = %s",
                            (amount, p["hold_id"], p["part_n"]))
                table, ref = ("rav_pilot_quota_window", p["window_id"]) if p["window_id"] else ("rav_pilot_quota_bonus", p["bonus_id"])
                cur.execute(f"UPDATE {table} SET held_ms = held_ms - %s, consumed_ms = consumed_ms + %s WHERE id = %s",
                            (amount, amount, ref))
                take -= amount

    @staticmethod
    def _release_rest(cur, job_id: UUID) -> None:
        cur.execute("SELECT * FROM rav_pilot_quota_hold WHERE job_id = %s AND state = 'OPEN' FOR UPDATE", (job_id,))
        for hold in cur.fetchall():
            cur.execute("SELECT * FROM rav_pilot_quota_hold_part WHERE hold_id = %s ORDER BY part_n FOR UPDATE", (hold["id"],))
            parts = cur.fetchall()
            with timing("db.pipeline"), cur.connection.pipeline():
                for p in parts:
                    rest = p["reserved_ms"] - p["consumed_ms"] - p["released_ms"]
                    if rest <= 0:
                        continue
                    cur.execute("UPDATE rav_pilot_quota_hold_part SET released_ms = released_ms + %s WHERE hold_id = %s AND part_n = %s",
                                (rest, p["hold_id"], p["part_n"]))
                    table, ref = ("rav_pilot_quota_window", p["window_id"]) if p["window_id"] else ("rav_pilot_quota_bonus", p["bonus_id"])
                    cur.execute(f"UPDATE {table} SET held_ms = held_ms - %s WHERE id = %s", (rest, ref))
                cur.execute("UPDATE rav_pilot_quota_hold SET released_ms = reserved_ms - consumed_ms, state = 'FINAL' WHERE id = %s",
                            (hold["id"],))

    @staticmethod
    def _release_job_pins(cur, job_id: UUID) -> None:
        cur.execute("UPDATE rav_pilot_source_pin SET released_at = now() "
                    "WHERE consumer_kind = 'JOB' AND consumer_id = %s AND released_at IS NULL", (job_id,))

    # --------------------------------------------------------------------------
    # Worker: claim / heartbeat / publish / finish
    # --------------------------------------------------------------------------
    def claim(self, worker: WorkerContext, queue: str, *, kinds: tuple[str, ...] | None = None,
              slot_keys: tuple[str, ...] | None = None, process: dict | None = None) -> AttemptLease | None:
        from psycopg.types.json import Jsonb

        if queue not in QUEUE_KINDS:
            raise ServiceError("validation_error", f"queue không hợp lệ: {queue}", 422)
        if kinds is not None and (not kinds or not set(kinds).issubset(QUEUE_KINDS[queue])):
            raise ServiceError("validation_error", "kind allowlist không thuộc queue", 422)
        if slot_keys is not None and (not slot_keys or any(not isinstance(k, str) or not k for k in slot_keys)):
            raise ServiceError("validation_error", "slot allowlist không hợp lệ", 422)
        kinds = list(QUEUE_KINDS[queue] if kinds is None else kinds)
        # Job GPU chờ ngân sách vẫn được xét lại mỗi lần claim (§21.14).
        waiting = list(GPU_WAIT) if queue == "GPU" else []
        claimable = "(state = 'QUEUED' OR (state = 'WAITING_RESOURCE' AND waiting_reason = ANY(%s)))"
        claim_args = [waiting]
        if queue != "MEDIA":
            claimable += (" AND (SELECT (q.config->>'heavy_active_per_user')::integer FROM rav_pilot_quota_policy q "
                          "WHERE q.version=rav_pilot_job.policy_version) > "
                          "(SELECT count(*) FROM rav_pilot_resource_slot s JOIN rav_pilot_job_attempt a2 ON a2.id=s.attempt_id "
                          "JOIN rav_pilot_job j2 ON j2.id=a2.job_id WHERE j2.owner_id=rav_pilot_job.owner_id "
                          "AND s.kind <> 'MEDIA' AND s.state <> 'FREE')")
        if slot_keys is not None:
            claimable += (" AND EXISTS (SELECT 1 FROM rav_pilot_quota_policy q WHERE q.version=rav_pilot_job.policy_version "
                          "AND coalesce(q.config->'slot_inventory'->%s, '[]'::jsonb) ?| %s::text[])")
            claim_args.extend((queue, list(slot_keys)))
        with self.db.backend() as cur:
            if queue != "GPU":
                if self._claim_rpc is None:
                    cur.execute("SELECT coalesce(has_function_privilege(current_user, "
                                "to_regprocedure('public.rav_pilot_claim_local(jsonb,text,text[],text[],jsonb)'), "
                                "'EXECUTE'),false) AS available")
                    self._claim_rpc = cur.fetchone()["available"]
                if self._claim_rpc:
                    cur.execute("SELECT public.rav_pilot_claim_local(%s,%s,%s,%s,%s) AS claimed",
                                (Jsonb(worker.model_dump(mode="json")), queue, kinds,
                                 list(slot_keys) if slot_keys is not None else None,
                                 Jsonb(process) if process is not None else None))
                    result = cur.fetchone()["claimed"]
                    if not result.get("fallback"):
                        return AttemptLease(**result["lease"]) if result["lease"] else None
            cur.execute("INSERT INTO rav_pilot_dispatch_cursor (queue) VALUES (%s) ON CONFLICT DO NOTHING", (queue,))
            cur.execute("SELECT last_owner_id FROM rav_pilot_dispatch_cursor WHERE queue = %s FOR UPDATE", (queue,))
            last = cur.fetchone()["last_owner_id"]
            cur.execute(f"SELECT DISTINCT owner_id FROM rav_pilot_job WHERE {claimable} AND kind = ANY(%s) "
                        "AND queue_deadline > now() ORDER BY owner_id", [*claim_args, kinds])
            owners = [r["owner_id"] for r in cur.fetchall()]
            if last is not None:   # round-robin: owner kế sau owner vừa được phục vụ, rồi quay vòng
                owners = [o for o in owners if o > last] + [o for o in owners if o <= last]
            job = None
            for owner in owners:
                cur.execute("SELECT user_id FROM rav_pilot_profile WHERE user_id = %s FOR UPDATE SKIP LOCKED", (owner,))
                if not cur.fetchone():
                    continue
                cur.execute(f"SELECT * FROM rav_pilot_job WHERE owner_id = %s AND {claimable} AND kind = ANY(%s) "
                            "AND queue_deadline > now() AND NOT EXISTS (SELECT 1 FROM rav_pilot_job_attempt a "
                            "WHERE a.job_id = rav_pilot_job.id AND a.usage_state = 'UNKNOWN') "
                            "ORDER BY created_at, id LIMIT 1 FOR UPDATE SKIP LOCKED", [owner, *claim_args, kinds])
                if job := cur.fetchone():
                    break
            if not job:
                return None
            cur.execute("SELECT config FROM rav_pilot_quota_policy WHERE version = %s", (job["policy_version"],))
            cfg = cur.fetchone()["config"]
            if slot_keys is None:
                cur.execute("SELECT * FROM rav_pilot_resource_slot WHERE kind = %s AND state = 'FREE' "
                            "ORDER BY slot_key LIMIT 1 FOR UPDATE SKIP LOCKED", (queue,))
            else:
                allowed = sorted(set(slot_keys).intersection(cfg["slot_inventory"].get(queue, [])))
                cur.execute("SELECT * FROM rav_pilot_resource_slot WHERE kind = %s AND state = 'FREE' "
                            "AND slot_key = ANY(%s) ORDER BY slot_key LIMIT 1 FOR UPDATE SKIP LOCKED", (queue, allowed))
            slot = cur.fetchone()
            if not slot:
                return None
            lease_ms = cfg["lease_ms"]
            snap = None
            if queue == "GPU":   # ngân sách GPU toàn cục của account slot (§21.14)
                from . import pilot_gpu
                snap, reason = pilot_gpu.admit_in_tx(cur, job, slot, cfg)
                if snap is None:
                    if job["state"] != "WAITING_RESOURCE" or job["waiting_reason"] != reason:
                        cur.execute("UPDATE rav_pilot_job SET state = 'WAITING_RESOURCE', waiting_reason = %s, phase = %s, "
                                    "state_version = state_version + 1, updated_at = now() WHERE id = %s",
                                    (reason, reason, job["id"]))
                    return None
            cur.execute("SELECT s.id, s.availability FROM rav_pilot_source_pin p JOIN rav_pilot_source_asset s ON s.id = p.source_id "
                        "WHERE p.consumer_kind = 'JOB' AND p.consumer_id = %s AND p.released_at IS NULL", (job["id"],))
            if any(r["availability"] != "READY" for r in cur.fetchall()):
                self._terminate(cur, job, "FAILED", {"code": "source_unavailable", "message": "nguồn không còn sẵn sàng",
                                                     "retryable": False})
                return None
            cur.execute("SELECT coalesce(max(fence), 0) + 1 AS f FROM rav_pilot_job_attempt WHERE job_id = %s", (job["id"],))
            fence = cur.fetchone()["f"]
            attempt_id, attempt_n = uuid4(), job["attempt_n"] + 1
            cur.execute("INSERT INTO rav_pilot_job_attempt (id, job_id, attempt_n, fence, worker_id, host_boot_id, state, "
                        "lease_until, started_at, process_group) VALUES (%s, %s, %s, %s, %s, %s, 'WORKING', "
                        "now() + %s * interval '1 millisecond', now(), %s) RETURNING lease_until, started_at",
                        (attempt_id, job["id"], attempt_n, fence, worker.worker_id, worker.host_boot_id, lease_ms,
                         Jsonb(process or {})))
            started = cur.fetchone()
            lease_until = started["lease_until"]
            if snap is not None:
                pilot_gpu.open_hold_in_tx(cur, attempt_id, snap, cfg["profiles_by_kind"][job["kind"]]["gpu_ms"])
            cur.execute("UPDATE rav_pilot_job SET state = 'RUNNING', state_version = state_version + 1, attempt_n = %s, "
                        "phase = 'claimed', waiting_reason = NULL, updated_at = now() WHERE id = %s", (attempt_n, job["id"]))
            cur.execute("UPDATE rav_pilot_resource_slot SET state = 'HELD', attempt_id = %s, generation = generation + 1, "
                        "lease_until = %s WHERE slot_key = %s RETURNING generation", (attempt_id, lease_until, slot["slot_key"]))
            generation = cur.fetchone()["generation"]
            cur.execute("UPDATE rav_pilot_dispatch_cursor SET last_owner_id = %s, revision = revision + 1 WHERE queue = %s",
                        (job["owner_id"], queue))
            return AttemptLease(job_id=job["id"], kind=job["kind"], attempt_id=attempt_id, attempt_n=attempt_n, fence=fence,
                                worker_id=worker.worker_id, slot_key=slot["slot_key"], slot_generation=generation,
                                lease_until=lease_until, payload=job["payload"], policy_config=cfg,
                                queue_ms=(started["started_at"]-job["created_at"]).total_seconds()*1000,
                                process_recorded=process is not None)

    @staticmethod
    def _lock_attempt(cur, lease: AttemptLease) -> tuple[dict, dict]:
        """Khóa profile → job → attempt → slot; lease mất → 409 lease_lost."""
        cur.execute("SELECT owner_id FROM rav_pilot_job WHERE id = %s", (lease.job_id,))
        owner = cur.fetchone()
        if owner:
            PilotDB.lock_profile(cur, owner["owner_id"])
        cur.execute("SELECT * FROM rav_pilot_job WHERE id = %s FOR UPDATE", (lease.job_id,))
        job = cur.fetchone()
        cur.execute("SELECT *, lease_until > now() AS alive FROM rav_pilot_job_attempt WHERE id = %s FOR UPDATE",
                    (lease.attempt_id,))
        att = cur.fetchone()
        cur.execute("SELECT * FROM rav_pilot_resource_slot WHERE slot_key = %s FOR UPDATE", (lease.slot_key,))
        slot = cur.fetchone()
        if (not job or not att or att["job_id"] != lease.job_id or att["fence"] != lease.fence
                or att["worker_id"] != lease.worker_id or att["state"] not in ("CLAIMED", "WORKING") or not att["alive"]
                or not slot or slot["attempt_id"] != lease.attempt_id or slot["generation"] != lease.slot_generation):
            raise ServiceError("lease_lost", "lease không còn hiệu lực", 409)
        return job, att

    def heartbeat(self, lease: AttemptLease, progress: Progress) -> AttemptLease:
        with self.db.backend() as cur:
            job, _ = self._lock_attempt(cur, lease)
            is_liveness_ping = (progress.done == 0 and progress.total == 0 and progress.phase in ("working", "running"))
            if not is_liveness_ping:
                if progress.done > progress.total or progress.done < job["done"]:
                    raise ServiceError("validation_error", "tiến độ không được giảm hoặc vượt total", 422)
            cur.execute("SELECT config FROM rav_pilot_quota_policy WHERE version = %s", (job["policy_version"],))
            lease_ms = cur.fetchone()["config"]["lease_ms"]
            cur.execute("UPDATE rav_pilot_job_attempt SET lease_until = now() + %s * interval '1 millisecond', updated_at = now() "
                        "WHERE id = %s RETURNING lease_until", (lease_ms, lease.attempt_id))
            lease_until = cur.fetchone()["lease_until"]
            cur.execute("UPDATE rav_pilot_resource_slot SET lease_until = %s WHERE slot_key = %s", (lease_until, lease.slot_key))
            if not is_liveness_ping:
                self._update_job_progress(cur, lease.job_id, progress)
            cancel_requested = job["state"] == "CANCEL_REQUESTED"
        if cancel_requested:   # lease đã gia hạn và commit; worker dừng hợp tác rồi gọi finish()
            raise ServiceError("cancel_requested", "người dùng đã yêu cầu hủy job", 409)
        return lease.model_copy(update={"lease_until": lease_until})

    def _update_job_progress(self, cur, job_id: UUID, progress: Progress) -> None:
        if self._has_progress_detail is None:
            cur.execute("SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'rav_pilot_job' AND column_name = 'progress_detail'")
            self._has_progress_detail = cur.fetchone() is not None
        if self._has_progress_detail:
            import json
            detail = {k: v for k, v in {"fps": progress.fps, "elapsed_s": progress.elapsed_s,
                                       "eta_s": progress.eta_s, "detail": progress.detail,
                                       "stages": [s.model_dump() for s in progress.stages] if progress.stages is not None else None}.items() if v is not None}
            cur.execute("UPDATE rav_pilot_job SET phase = %s, done = %s, total = %s, progress_detail = %s, updated_at = now() WHERE id = %s",
                        (progress.phase, progress.done, progress.total, json.dumps(detail), job_id))
        else:
            cur.execute("UPDATE rav_pilot_job SET phase = %s, done = %s, total = %s, updated_at = now() WHERE id = %s",
                        (progress.phase, progress.done, progress.total, job_id))

    def update_progress(self, lease: AttemptLease, progress: Progress) -> None:
        """Handler worker chủ động cập nhật tiến độ chi tiết."""
        with self.db.backend() as cur:
            job, _ = self._lock_attempt(cur, lease)
            phase_reset = job['kind'] == 'PROPOSE' and progress.phase != job['phase']
            if progress.done > progress.total or (progress.done < job["done"] and not phase_reset):
                raise ServiceError("validation_error", "tiến độ không được giảm hoặc vượt total", 422)
            self._update_job_progress(cur, lease.job_id, progress)

    def publish(self, lease: AttemptLease, outputs: dict[str, Any], usage: Usage) -> PublicationReceipt:
        """Atomic publication; staged PROPOSE RPC preserves the complete legacy fallback."""
        if usage.state != "FINAL" or usage.cpu_ms is None or usage.gpu_ms is None:
            raise ServiceError("validation_error", "publish cần usage FINAL đủ cpu_ms/gpu_ms", 422)
        with self.db.backend() as cur:
            if lease.kind == "PROPOSE" and (lease.policy_config or {}).get('cpu_accounting_enabled', True):
                if self._propose_rpc is None:
                    cur.execute("SELECT coalesce(has_function_privilege(current_user, "
                                "to_regprocedure('public.rav_pilot_publish_propose(jsonb,jsonb,jsonb,jsonb)'), "
                                "'EXECUTE'),false) AS available")
                    self._propose_rpc = cur.fetchone()["available"]
                if self._propose_rpc:
                    receipt = self._publish_propose(cur, lease, outputs, usage)
                    if receipt is not None:
                        return receipt
            return self._publish_in_tx(cur, lease, outputs, usage)

    @staticmethod
    def _publish_propose(cur, lease: AttemptLease, outputs: dict, usage: Usage) -> PublicationReceipt | None:
        from psycopg.types.json import Jsonb

        from .pilot_curation import prepare_publication

        try:
            prepared = prepare_publication(lease.payload, outputs)
        except ServiceError as exc:
            prepared = {"error": exc.code}   # replay precedes validation inside the DB
        cur.execute("SELECT public.rav_pilot_publish_propose(%s,%s,%s,%s) AS receipt",
                    (Jsonb(lease.model_dump(mode="json")), Jsonb(outputs),
                     Jsonb(usage.model_dump(mode="json")), Jsonb(prepared)))
        result = cur.fetchone()["receipt"]
        if result.get("fallback"):
            return None
        if code := result.get("error"):
            status = 401 if code in ("unauthorized", "account_disabled") else 422 if code == "result_invalid" else 409
            raise ServiceError(code, {
                "lease_lost": "lease không còn hiệu lực",
                "cancel_requested": "job đã được yêu cầu hủy; gọi finish()",
                "proposal_context_changed": "phiên đã đổi trong lúc đề xuất chạy; tải lại rồi đề xuất lại",
                "result_invalid": "kết quả đề xuất không khớp payload đóng băng",
                "account_disabled": "tài khoản đã bị vô hiệu hóa",
                "unauthorized": "tài khoản chưa được cấp quyền dùng pilot",
            }.get(code, code), status)
        return PublicationReceipt(id=lease.attempt_id, replayed=result["replayed"], job=_job_dto(result["job"]))

    def _publish_in_tx(self, cur, lease: AttemptLease, outputs: dict, usage: Usage) -> PublicationReceipt:
        """Complete legacy publication, also used for exceptional RPC paths under the same locks."""
        cur.execute("SELECT j.*, a.state AS att_state FROM rav_pilot_job j JOIN rav_pilot_job_attempt a ON a.job_id = j.id "
                    "WHERE a.id = %s AND a.fence = %s AND a.worker_id = %s", (lease.attempt_id, lease.fence, lease.worker_id))
        prior = cur.fetchone()
        if prior and prior["att_state"] == "CLOSED" and prior["state"] == "SUCCEEDED":
            return PublicationReceipt(id=lease.attempt_id, replayed=True, job=_job_dto(prior))
        job, _ = self._lock_attempt(cur, lease)
        if job["state"] == "CANCEL_REQUESTED":
            raise ServiceError("cancel_requested", "job đã được yêu cầu hủy; gọi finish()", 409)
        if job["state"] != "RUNNING":
            raise ServiceError("lease_lost", f"job đang {job['state']}, không publish thành công", 409)
        if not self._settle(cur, lease, usage, job):
            cur.execute("SELECT * FROM rav_pilot_job WHERE id = %s", (lease.job_id,))
            return PublicationReceipt(id=lease.attempt_id, replayed=False, job=_job_dto(cur.fetchone()))
        hook = _hook(job["kind"])
        result_ref = hook.publish_in_tx(cur, job, lease, outputs) if hook else None
        from . import pilot_objects
        pilot_objects.accept_selected_in_tx(cur, job, lease)
        pending = self._has_pending_objects(cur, lease.attempt_id)
        if not pending:
            self._release_rest(cur, lease.job_id)
            self._release_job_pins(cur, lease.job_id)
            pilot_storage.release_job_holds(cur, lease.job_id)
        cur.execute("UPDATE rav_pilot_job SET state = 'SUCCEEDED', state_version = state_version + 1, phase = 'published', "
                    "done = total, result_ref = %s, updated_at = now() WHERE id = %s RETURNING *",
                    (result_ref, lease.job_id))
        job = cur.fetchone()
        self._close_attempt(cur, lease, bool(outputs.get("cleanup_done", True)) and not pending)
        return PublicationReceipt(id=lease.attempt_id, replayed=False, job=_job_dto(job))

    def finish(self, lease: AttemptLease, usage: Usage, error: dict[str, Any] | None = None) -> JobDTO:
        """Kết thúc không thành công: job CANCEL_REQUESTED → CANCELLED, còn lại → FAILED với `error {code,message,retryable}`."""
        if usage.state != "FINAL" or usage.cpu_ms is None or usage.gpu_ms is None:
            raise ServiceError("validation_error", "finish cần usage FINAL đủ cpu_ms/gpu_ms", 422)
        with self.db.backend() as cur:
            job, _ = self._lock_attempt(cur, lease)
            if not self._settle(cur, lease, usage, job):
                cur.execute("SELECT * FROM rav_pilot_job WHERE id = %s", (lease.job_id,))
                return _job_dto(cur.fetchone())
            target = "CANCELLED" if job["state"] == "CANCEL_REQUESTED" else "FAILED"
            err = None if target == "CANCELLED" else (error or {"code": "internal_error", "message": "lỗi worker",
                                                               "retryable": True})
            self._terminate(cur, job, target, err, release_pins=True)
            self._close_attempt(cur, lease, not self._has_pending_objects(cur, lease.attempt_id))
            cur.execute("SELECT * FROM rav_pilot_job WHERE id = %s", (lease.job_id,))
            return _job_dto(cur.fetchone())

    def extend_hold(self, lease: AttemptLease, extra: ReserveDelta) -> dict[str, Any]:
        with self.db.backend() as cur:
            job, _ = self._lock_attempt(cur, lease)
            cur.execute("SELECT config FROM rav_pilot_quota_policy WHERE version = %s", (job["policy_version"],))
            cfg = cur.fetchone()["config"]
            for res, amount in (("CPU", extra.cpu_ms), ("GPU", extra.gpu_ms)):
                if res == 'CPU' and not job.get('cpu_accounting_enabled', True):
                    continue
                self._reserve(cur, job["owner_id"], job["id"], res, amount, cfg[f"{res.lower()}_base_ms"])
            if extra.storage_bytes:
                scope = (job.get("payload") or {}).get("target_scope", "PRIVATE")
                pilot_storage.reserve(cur, job["owner_id"], cfg, extra.storage_bytes,
                                      f"attempt:{lease.attempt_id}:extend:{uuid4()}", job_id=job["id"], scope=scope)
            return {"job_id": job["id"], "cpu_ms": extra.cpu_ms, "gpu_ms": extra.gpu_ms,
                    "storage_bytes": extra.storage_bytes}

    def ensure_running_budget(self, lease: AttemptLease, usage: Usage) -> None:
        """Giữ đủ actual đã quan sát và estimate chunk kế trước khi worker tiếp tục."""
        if (lease.policy_config or {}).get('cpu_accounting_enabled', True) is False and usage.gpu_ms == 0 and lease.kind not in QUEUE_KINDS['GPU']:
            return   # CPU telemetry has no quota; heartbeat/fence/storage guards remain independent
        with self.db.backend() as cur:
            job, _ = self._lock_attempt(cur, lease)
            cur.execute("SELECT config FROM rav_pilot_quota_policy WHERE version = %s", (job["policy_version"],))
            cfg = cur.fetchone()["config"]
            estimate = cfg["profiles_by_kind"][job["kind"]]
            for res, actual in (("CPU", usage.cpu_ms), ("GPU", usage.gpu_ms)):
                if res == 'CPU' and not job.get('cpu_accounting_enabled', True):
                    continue
                if actual is None:
                    raise ServiceError("validation_error", "cần usage đủ cpu_ms/gpu_ms", 422)
                cur.execute("SELECT coalesce(sum(reserved_ms-consumed_ms-released_ms),0) AS r "
                            "FROM rav_pilot_quota_hold WHERE job_id = %s AND resource = %s AND state = 'OPEN'",
                            (job["id"], res))
                deficit = actual + estimate[f"{res.lower()}_ms"] - int(cur.fetchone()["r"])
                self._reserve(cur, job["owner_id"], job["id"], res, deficit, cfg[f"{res.lower()}_base_ms"])

    def _settle(self, cur, lease: AttemptLease, usage: Usage, job: dict | None = None) -> bool:
        from psycopg.types.json import Jsonb

        if job is None:
            cur.execute("SELECT * FROM rav_pilot_job WHERE id=%s", (lease.job_id,))
            job = cur.fetchone()
        resources = (("CPU", usage.cpu_ms), ("GPU", usage.gpu_ms))
        if not job.get('cpu_accounting_enabled', True):
            resources = (("GPU", usage.gpu_ms),) if job['kind'] in QUEUE_KINDS['GPU'] or usage.gpu_ms else ()
            if not resources:
                cur.execute('UPDATE rav_pilot_job_attempt SET usage=%s WHERE id=%s', (Jsonb(usage.model_dump()), lease.attempt_id))
                return True
        cfg = None
        cur.execute("SELECT r.resource, EXISTS(SELECT 1 FROM rav_pilot_usage_receipt u WHERE u.attempt_id=%s AND u.resource=r.resource) AS settled, "
                    "coalesce((SELECT sum(reserved_ms-consumed_ms-released_ms) FROM rav_pilot_quota_hold h "
                    "WHERE h.job_id=%s AND h.resource=r.resource AND h.state='OPEN'),0) AS reserved "
                    "FROM (VALUES ('CPU'), ('GPU')) r(resource)", (lease.attempt_id, lease.job_id))
        basis = {r["resource"]: r for r in cur.fetchall()}
        try:
            with cur.connection.transaction():
                for res, actual in resources:
                    if basis[res]["settled"]:
                        continue
                    deficit = actual - int(basis[res]["reserved"])
                    if deficit > 0:
                        if cfg is None:
                            cur.execute("SELECT config FROM rav_pilot_quota_policy WHERE version=%s", (job["policy_version"],))
                            cfg = cur.fetchone()["config"]
                        self._reserve(cur, job["owner_id"], job["id"], res, deficit, cfg[f"{res.lower()}_base_ms"])
        except ServiceError as exc:
            if exc.code != "quota_exceeded":
                raise
            cur.execute("UPDATE rav_pilot_job_attempt SET state = 'LOST', usage_state = 'UNKNOWN', usage = %s, "
                        "updated_at = now() WHERE id = %s", (Jsonb(usage.model_dump()), lease.attempt_id))
            cur.execute("UPDATE rav_pilot_resource_slot SET state = 'QUARANTINED' WHERE attempt_id = %s", (lease.attempt_id,))
            cur.execute("UPDATE rav_pilot_job SET state = 'WAITING_RESOURCE', waiting_reason = 'quota_reconcile', "
                        "phase = 'quota_reconcile', state_version = state_version + 1, updated_at = now() WHERE id = %s",
                        (lease.job_id,))
            if job["state"] == "CANCEL_REQUESTED":
                cur.execute("UPDATE rav_pilot_job SET error = %s WHERE id = %s",
                            (Jsonb({"code": "cancel_requested", "message": "đang chờ đối soát", "retryable": False}), lease.job_id))
            return False
        for res, actual in resources:
            if basis[res]["settled"]:
                continue
            self._consume(cur, lease.job_id, res, actual)
            cur.execute("INSERT INTO rav_pilot_usage_receipt (id, job_id, attempt_id, resource, settlement_key, actual_ms, evidence) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (settlement_key) DO NOTHING",
                        (uuid4(), lease.job_id, lease.attempt_id, res, f"{lease.attempt_id}:{res}", actual,
                         Jsonb({"hash": usage.evidence_hash, **usage.evidence})))
        cur.execute("UPDATE rav_pilot_job_attempt SET usage = %s WHERE id = %s", (Jsonb(usage.model_dump()), lease.attempt_id))
        from .pilot_gpu import settle_hold_in_tx
        settle_hold_in_tx(cur, lease.attempt_id, usage.gpu_ms)
        return True

    @staticmethod
    def _close_attempt(cur, lease: AttemptLease, cleanup_done: bool) -> None:
        with timing("db.pipeline"), cur.connection.pipeline():
            cur.execute("UPDATE rav_pilot_job_attempt SET state = 'CLOSED', finished_at = now(), cleanup_done = %s, "
                        "usage_state = 'FINAL', updated_at = now() WHERE id = %s", (cleanup_done, lease.attempt_id))
            if cleanup_done:
                cur.execute("UPDATE rav_pilot_resource_slot SET state = 'FREE', attempt_id = NULL, lease_until = NULL WHERE slot_key = %s",
                            (lease.slot_key,))
            else:
                cur.execute("UPDATE rav_pilot_resource_slot SET state = 'QUARANTINED' WHERE slot_key = %s", (lease.slot_key,))

    @staticmethod
    def _has_pending_objects(cur, attempt_id: UUID) -> bool:
        from .pilot_objects import pending_in_tx
        return pending_in_tx(cur, attempt_id=attempt_id)

    def record_process(self, lease: AttemptLease, process: dict) -> None:
        from psycopg.types.json import Jsonb
        with self.db.backend() as cur:
            self._lock_attempt(cur, lease)
            cur.execute("UPDATE rav_pilot_job_attempt SET process_group = %s WHERE id = %s",
                        (Jsonb(process), lease.attempt_id))

    def record_stopped(self, lease: AttemptLease, usage: Usage) -> None:
        """Handler đã thoát; ghi usage ngay cả khi lease hết hạn, không công bố kết quả."""
        from psycopg.types.json import Jsonb
        with self.db.backend() as cur:
            cur.execute("UPDATE rav_pilot_job_attempt SET process_group = process_group || %s, usage = %s "
                        "WHERE id = %s AND worker_id = %s AND fence = %s AND usage_state <> 'FINAL'",
                        (Jsonb({"handler_stopped": True}), Jsonb(usage.model_dump()),
                         lease.attempt_id, lease.worker_id, lease.fence))

    def _terminate(self, cur, job: dict, state: str, error: dict | None, release_pins: bool = True) -> None:
        from psycopg.types.json import Jsonb

        from .pilot_objects import pending_in_tx
        if not pending_in_tx(cur, job_id=job["id"]):
            self._release_rest(cur, job["id"])
            if release_pins:
                self._release_job_pins(cur, job["id"])
            pilot_storage.release_job_holds(cur, job["id"])
        if hook := _hook(job["kind"]):
            hook.finish_in_tx(cur, job, state, error)
        cur.execute("UPDATE rav_pilot_job SET state = %s, state_version = state_version + 1, phase = %s, error = %s, "
                    "updated_at = now() WHERE id = %s", (state, state.lower(), Jsonb(error) if error else None, job["id"]))

    # --------------------------------------------------------------------------
    # Người dùng: cancel / xem job
    # --------------------------------------------------------------------------
    def cancel(self, actor: ActorContext, job_id: UUID, expected_version: int) -> JobDTO:
        from psycopg.types.json import Jsonb

        from .pilot_operations import operations_read

        uid = require_user(actor)
        is_op = operations_read(uid)
        with self.db.backend() as cur:
            self.db.lock_profile(cur, uid)
            cur.execute("SELECT * FROM rav_pilot_job WHERE id = %s AND (owner_id = %s OR (%s AND payload ->> 'target_scope' = 'SYSTEM')) FOR UPDATE",
                        (job_id, uid, is_op))
            job = cur.fetchone()
            if not job:
                raise ServiceError("not_found", f"không tìm thấy job {job_id}", 404)
            if job["owner_id"] != uid:
                self.db.lock_profile(cur, job["owner_id"])
            if job["state_version"] != expected_version:
                raise ServiceError("state_version_conflict", f"job state_version hiện là {job['state_version']}", 409)
            if job["state"] in ("QUEUED", "WAITING_RESOURCE"):
                cur.execute("SELECT 1 FROM rav_pilot_job_attempt WHERE job_id = %s AND usage_state = 'UNKNOWN' LIMIT 1",
                            (job_id,))
                if cur.fetchone():
                    cur.execute("UPDATE rav_pilot_job SET error = %s, state_version = state_version + 1, updated_at = now() "
                                "WHERE id = %s", (Jsonb({"code": "cancel_requested", "message": "đang chờ đối soát",
                                                         "retryable": False}), job_id))
                else:
                    self._terminate(cur, job, "CANCELLED", None)
            elif job["state"] == "RUNNING":
                cur.execute("UPDATE rav_pilot_job SET state = 'CANCEL_REQUESTED', state_version = state_version + 1, "
                            "phase = 'cancel_requested', updated_at = now() WHERE id = %s", (job_id,))
            cur.execute("SELECT * FROM rav_pilot_job WHERE id = %s", (job_id,))
            return _job_dto(cur.fetchone())

    def get_job(self, actor: ActorContext, job_id: UUID) -> JobDTO:
        from .pilot_operations import operations_read

        uid = require_user(actor)
        is_op = operations_read(uid)
        with self.db.backend() as cur:
            self.db.read_profile(cur, uid)
            cur.execute("SELECT * FROM rav_pilot_job WHERE id = %s AND (owner_id = %s OR (%s AND payload ->> 'target_scope' = 'SYSTEM'))",
                        (job_id, uid, is_op))
            row = cur.fetchone()
        if not row:
            raise ServiceError("not_found", f"không tìm thấy job {job_id}", 404)
        return _job_dto(row)

    def list_jobs(self, actor: ActorContext, state: str | None = None, cursor: str | None = None, limit: int = 50, kind: str | None = None, resource_id: UUID | None = None):
        from .pilot_db import Page, _page_args
        from .pilot_operations import operations_read

        uid = require_user(actor)
        is_op = operations_read(uid)
        offset, limit = _page_args(cursor, limit)
        where, args = "(owner_id = %s OR (%s AND payload ->> 'target_scope' = 'SYSTEM'))", [uid, is_op]
        if state:
            where += " AND state = %s"
            args.append(state)
        if kind:
            where += " AND kind = %s"
            args.append(kind)
        if resource_id is not None:
            where += " AND resource_id = %s"
            args.append(resource_id)
        with self.db.backend() as cur:
            self.db.read_profile(cur, uid)
            cur.execute(f"SELECT count(*) AS n FROM rav_pilot_job WHERE {where}", args)
            total = cur.fetchone()["n"]
            cur.execute(f"SELECT * FROM rav_pilot_job WHERE {where} ORDER BY created_at DESC, id LIMIT %s OFFSET %s",
                        [*args, limit, offset])
            items = [_job_dto(r) for r in cur.fetchall()]
        return Page(items=items, total=total, next_cursor=str(offset + limit) if offset + limit < total else None)

    # --------------------------------------------------------------------------
    # Supervisor: lease hết hạn, queue deadline, đối soát slot
    # --------------------------------------------------------------------------
    def recover_expired_leases(self) -> list[dict[str, Any]]:
        """Attempt hết lease → LOST + usage UNKNOWN + slot QUARANTINED; job WAITING_RESOURCE tới đối soát.
        Hold giữ nguyên (usage chưa biết) tới `reconcile_slot`. Job QUEUED quá queue_deadline → FAILED, nhả hold/pin."""
        from psycopg.types.json import Jsonb

        out = []
        with self.db.backend() as cur:
            cur.execute("SELECT a.id, a.job_id, j.owner_id FROM rav_pilot_job_attempt a "
                        "JOIN rav_pilot_job j ON j.id = a.job_id WHERE a.state IN ('CLAIMED', 'WORKING') "
                        "AND a.lease_until < now() ORDER BY a.id")
            for a in cur.fetchall():
                cur.execute("SELECT user_id FROM rav_pilot_profile WHERE user_id = %s FOR UPDATE SKIP LOCKED", (a["owner_id"],))
                if not cur.fetchone():
                    continue
                cur.execute("SELECT * FROM rav_pilot_job WHERE id = %s FOR UPDATE", (a["job_id"],))
                job = cur.fetchone()
                cur.execute("SELECT state, lease_until < now() AS expired FROM rav_pilot_job_attempt WHERE id = %s FOR UPDATE",
                            (a["id"],))
                current = cur.fetchone()
                if current["state"] not in ("CLAIMED", "WORKING") or not current["expired"]:
                    continue
                cur.execute("UPDATE rav_pilot_job_attempt SET state = 'LOST', usage_state = 'UNKNOWN', updated_at = now() "
                            "WHERE id = %s", (a["id"],))
                cur.execute("UPDATE rav_pilot_resource_slot SET state = 'QUARANTINED' WHERE attempt_id = %s", (a["id"],))
                action, new_state = "quarantined", "WAITING_RESOURCE"
                err = {"code": "cancel_requested", "message": "đang chờ xác nhận dừng", "retryable": False} \
                    if job["state"] == "CANCEL_REQUESTED" else None
                cur.execute("UPDATE rav_pilot_job SET state = %s, state_version = state_version + 1, phase = %s, "
                            "waiting_reason = %s, error = %s, updated_at = now() WHERE id = %s",
                            (new_state, action, "lease_lost",
                             Jsonb(err) if err else None, job["id"]))
                out.append({"attempt_id": a["id"], "job_id": job["id"], "action": action})
            cur.execute("SELECT * FROM rav_pilot_job WHERE (state = 'QUEUED' OR (state = 'WAITING_RESOURCE' "
                        "AND waiting_reason = ANY(%s))) AND queue_deadline < now() ORDER BY id FOR UPDATE SKIP LOCKED",
                        (list(GPU_WAIT),))
            for job in cur.fetchall():
                self._terminate(cur, job, "FAILED", {"code": "queue_deadline_exceeded", "message": "quá hạn chờ trong hàng đợi",
                                                     "retryable": True})
                out.append({"attempt_id": None, "job_id": job["id"], "action": "expired"})
        return out

    def reconcile_slot(self, worker: WorkerContext, slot_key: str, generation: int, evidence: SlotEvidence) -> dict[str, Any]:
        """Slot QUARANTINED chỉ FREE khi có bằng chứng process cũ đã dừng; usage chưa biết thì tính trọn phần đã giữ."""
        from psycopg.types.json import Jsonb

        with self.db.backend() as cur:
            cur.execute("SELECT j.id, j.owner_id FROM rav_pilot_resource_slot s "
                        "JOIN rav_pilot_job_attempt a ON a.id = s.attempt_id JOIN rav_pilot_job j ON j.id = a.job_id "
                        "WHERE s.slot_key = %s", (slot_key,))
            identity = cur.fetchone()
            if identity:
                self.db.lock_profile(cur, identity["owner_id"])
                cur.execute("SELECT id FROM rav_pilot_job WHERE id = %s FOR UPDATE", (identity["id"],))
            cur.execute("SELECT * FROM rav_pilot_resource_slot WHERE slot_key = %s FOR UPDATE", (slot_key,))
            slot = cur.fetchone()
            if not slot or slot["state"] != "QUARANTINED" or slot["generation"] != generation:
                raise ServiceError("lease_lost", "slot không ở trạng thái cần đối soát", 409)
            if not evidence.process_exit_verified:
                cur.execute("UPDATE rav_pilot_resource_slot SET reconcile_evidence = %s WHERE slot_key = %s",
                            (Jsonb(evidence.model_dump()), slot_key))
                return {"slot_key": slot_key, "state": "QUARANTINED"}
            cur.execute("SELECT * FROM rav_pilot_job_attempt WHERE id = %s FOR UPDATE", (slot["attempt_id"],))
            att = cur.fetchone()
            cur.execute("SELECT * FROM rav_pilot_job WHERE id = %s FOR UPDATE", (att["job_id"],))
            job = cur.fetchone()
            values = {}
            for res, actual in (("CPU", evidence.cpu_ms), ("GPU", evidence.gpu_ms)):
                measured = (att["usage"] or {}).get(f"{res.lower()}_ms")
                if measured is not None:
                    actual = measured if actual is None else max(actual, measured)
                if actual is None:
                    cur.execute("SELECT coalesce(sum(reserved_ms-consumed_ms-released_ms),0) AS r "
                                "FROM rav_pilot_quota_hold WHERE job_id = %s AND resource = %s AND state = 'OPEN'",
                                (job["id"], res))
                    actual = int(cur.fetchone()["r"])
                values[f"{res.lower()}_ms"] = actual
            lease = AttemptLease(job_id=job["id"], kind=job["kind"], attempt_id=att["id"], attempt_n=att["attempt_n"],
                                 fence=att["fence"], worker_id=att["worker_id"], slot_key=slot_key,
                                 slot_generation=generation, lease_until=att["lease_until"], payload=job["payload"])
            if not self._settle(cur, lease, Usage(**values, state="FINAL",
                                                evidence={"reconciled_by": str(worker.worker_id), **evidence.proof})):
                return {"slot_key": slot_key, "state": "QUARANTINED"}
            if self._has_pending_objects(cur, att["id"]):
                return {"slot_key": slot_key, "state": "QUARANTINED"}
            if job["state"] in TERMINAL:
                self._release_rest(cur, job["id"])
                self._release_job_pins(cur, job["id"])
                pilot_storage.release_job_holds(cur, job["id"])
            elif (job["error"] or {}).get("code") == "cancel_requested":
                self._terminate(cur, job, "CANCELLED", None)
            elif job["attempt_n"] >= MAX_ATTEMPTS or job["waiting_reason"] == "quota_reconcile":
                self._terminate(cur, job, "FAILED", {"code": "lease_lost", "message": "attempt cần thực hiện lại",
                                                     "retryable": True})
            else:
                cur.execute("SELECT config FROM rav_pilot_quota_policy WHERE version = %s", (job["policy_version"],))
                cfg = cur.fetchone()["config"]
                try:
                    with cur.connection.transaction():
                        for res in ("CPU", "GPU"):
                            if res == 'CPU' and not job.get('cpu_accounting_enabled', True):
                                continue
                            cur.execute("SELECT coalesce(sum(reserved_ms-consumed_ms-released_ms),0) AS r "
                                        "FROM rav_pilot_quota_hold WHERE job_id = %s AND resource = %s AND state = 'OPEN'",
                                        (job["id"], res))
                            extra = cfg["profiles_by_kind"][job["kind"]][f"{res.lower()}_ms"] - int(cur.fetchone()["r"])
                            self._reserve(cur, job["owner_id"], job["id"], res, extra, cfg[f"{res.lower()}_base_ms"])
                    cur.execute("UPDATE rav_pilot_job SET state = 'QUEUED', waiting_reason = NULL, phase = 'requeued', "
                                "state_version = state_version + 1, updated_at = now() WHERE id = %s", (job["id"],))
                except ServiceError as exc:
                    if exc.code != "quota_exceeded":
                        raise
                    self._terminate(cur, job, "FAILED", {"code": "quota_exceeded", "message": "không đủ quota chạy lại",
                                                         "retryable": True})
            cur.execute("UPDATE rav_pilot_job_attempt SET usage_state = 'FINAL', cleanup_done = true, updated_at = now() "
                        "WHERE id = %s", (att["id"],))
            cur.execute("UPDATE rav_pilot_resource_slot SET state = 'FREE', attempt_id = NULL, lease_until = NULL, "
                        "reconcile_evidence = coalesce(reconcile_evidence, '{}'::jsonb) || %s WHERE slot_key = %s",
                        (Jsonb(evidence.model_dump()), slot_key))
            return {"slot_key": slot_key, "state": "FREE"}

    # --------------------------------------------------------------------------
    # Xóa nguồn (RESTRICT, tombstone) — §21.9
    # --------------------------------------------------------------------------
    def request_source_delete(self, actor: ActorContext, source_id: UUID, expected_resource_version: int,
                              idempotency_key: str) -> DeleteReceipt:
        from psycopg.types.json import Jsonb

        uid = require_user(actor)
        with self.db.backend() as cur:
            self.db.lock_profile(cur, uid)
            src = self.db.lock_sources(cur, uid, [source_id])[source_id]
            cur.execute("SELECT * FROM rav_pilot_source_delete WHERE owner_id = %s AND idempotency_key = %s",
                        (uid, idempotency_key))
            if old := cur.fetchone():
                if old["source_id"] != source_id or old["expected_resource_version"] != expected_resource_version:
                    raise ServiceError("idempotency_conflict", "Idempotency-Key đã dùng cho yêu cầu xóa khác", 409)
                return _delete_dto(old, cur)
            if src["scope"] == "SYSTEM" or src["protected"]:
                raise ServiceError("forbidden", "nguồn hệ thống/được bảo vệ không xóa được", 403)
            if src["resource_version"] != expected_resource_version:
                raise ServiceError("stale_input", f"resource_version hiện là {src['resource_version']}", 409)
            if src["availability"] == "DELETE_PENDING":
                cur.execute("SELECT j.id, j.state FROM rav_pilot_job j WHERE j.kind = 'DELETE_SOURCE' AND j.resource_id = %s "
                            "ORDER BY j.created_at DESC LIMIT 1", (source_id,))
                previous = cur.fetchone()
                if not previous or previous["state"] not in ("FAILED", "CANCELLED"):
                    raise ServiceError("delete_in_progress", "yêu cầu xóa đang được xử lý", 409)
                cur.execute("SELECT 1 FROM rav_pilot_job_attempt a WHERE a.job_id = %s AND "
                            "(a.usage_state <> 'FINAL' OR NOT a.cleanup_done OR EXISTS "
                            "(SELECT 1 FROM rav_pilot_resource_slot s WHERE s.attempt_id = a.id)) LIMIT 1", (previous["id"],))
                unfinished = cur.fetchone() is not None
                cur.execute("SELECT 1 FROM rav_pilot_quota_hold WHERE job_id = %s "
                            "AND reserved_ms <> consumed_ms + released_ms LIMIT 1", (previous["id"],))
                unfinished = unfinished or cur.fetchone() is not None
                cur.execute("SELECT 1 FROM rav_pilot_storage_hold WHERE job_id = %s AND state <> 'FINAL' LIMIT 1",
                            (previous["id"],))
                unfinished = unfinished or cur.fetchone() is not None
                cur.execute("SELECT 1 FROM rav_pilot_worker_object WHERE job_id = %s "
                            "AND state IN ('INTENT', 'WRITTEN', 'DELETE_PENDING') LIMIT 1", (previous["id"],))
                if unfinished or cur.fetchone():
                    raise ServiceError("delete_reconcile_pending", "job xóa trước đang chờ đối soát quota/slot", 409)
            elif src["availability"] not in ("READY", "UNAVAILABLE"):
                raise ServiceError("source_unavailable", f"nguồn đang {src['availability']}", 409)
            cur.execute("SELECT consumer_kind, consumer_id FROM rav_pilot_source_pin WHERE source_id = %s AND released_at IS NULL "
                        "ORDER BY consumer_kind, consumer_id", (source_id,))
            pins = cur.fetchall()
            if pins:
                raise ServiceError("resource_in_use", "nguồn đang được phiên/snapshot/job tham chiếu", 409,
                                   {"dependencies": [{"kind": p["consumer_kind"], "id": str(p["consumer_id"])} for p in pins]})
            cur.execute("UPDATE rav_pilot_source_asset SET availability = 'DELETE_PENDING', resource_version = resource_version + 1 "
                        "WHERE id = %s RETURNING resource_version", (source_id,))
            version = cur.fetchone()["resource_version"]
            delete_id = uuid4()
            job = self.enqueue_in_tx(cur, uid, EnqueueSpec(kind="DELETE_SOURCE", resource_id=source_id,
                                                           idempotency_key=f"delete:{delete_id}",
                                                           payload={"source_id": str(source_id), "source_version": version,
                                                                    "delete_request_id": str(delete_id)}), [source_id])
            cur.execute("INSERT INTO rav_pilot_source_delete (id, source_id, owner_id, idempotency_key, expected_resource_version, "
                        "state, receipt) VALUES (%s, %s, %s, %s, %s, 'REQUESTED', %s) RETURNING *",
                        (delete_id, source_id, uid, idempotency_key, expected_resource_version, Jsonb({"job_id": str(job.id)})))
            return _delete_dto(cur.fetchone(), cur)

    def get_source_delete(self, actor: ActorContext, delete_id: UUID) -> DeleteReceipt:
        uid = require_user(actor)
        with self.db.backend() as cur:
            self.db.lock_profile(cur, uid)
            cur.execute("SELECT * FROM rav_pilot_source_delete WHERE id = %s AND owner_id = %s", (delete_id, uid))
            row = cur.fetchone()
            if not row:
                raise ServiceError("not_found", f"không tìm thấy yêu cầu xóa {delete_id}", 404)
            return _delete_dto(row, cur)


def _delete_dto(row: dict, cur) -> DeleteReceipt:
    job_id = (row["receipt"] or {}).get("job_id")
    state, error = row["state"], (row["receipt"] or {}).get("error")
    if job_id and state in ("REQUESTED", "RUNNING"):
        cur.execute("SELECT state, error FROM rav_pilot_job WHERE id = %s", (UUID(job_id),))
        job = cur.fetchone()
        if job and job["state"] in ("FAILED", "CANCELLED"):
            state, error = "FAILED", job["error"] or {"code": "cancelled", "message": "yêu cầu xóa đã hủy", "retryable": True}
    return DeleteReceipt(id=row["id"], source_id=row["source_id"], state=state,
                         job_id=UUID(job_id) if job_id else None, error=error)
