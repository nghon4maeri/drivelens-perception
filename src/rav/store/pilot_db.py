"""Adapter PostgreSQL cho catalog/phiên/quyết định/snapshot của pilot (CONTRACTS §21, §21.11; identity spec §5–8).

- Chỉ PostgreSQL (không SQLite fallback). Đọc dữ liệu người dùng qua `as_user()` = `SET LOCAL ROLE authenticated` + claims → RLS.
- Ghi qua `backend()` (role `rav_pilot_api`): kiểm owner/scope tường minh từ `ActorContext` đã xác thực, khóa `FOR UPDATE`
  theo thứ tự profile → sources (UUID tăng dần) → phiên → receipt/pin; mọi thứ của một lệnh nằm trong một transaction.
- Review state = replay log `rav_pilot_decision` (giống feedback `keep_drop`); snapshot do server tính
  `members_hash`/`context_hash` (cùng công thức `service/snapshot.py`).
Input/Output: DTO Pydantic dưới đây; lỗi `ServiceError` theo §21.4. Không nhận owner/actor từ input.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..actor import ActorContext
from ..errors import ServiceError
from ..timing import timing
from .pilot_schema import schema_problem

# Cách tách frame mặc định của pilot = DEFAULT_SPEC local (pyav 4 fps + per_frame); spec_hash kiểm parity trong test.
DEFAULT_SAMPLE_SPEC = {"decoder": {"type": "pyav", "params": {"fps": 4}}, "units": {"type": "per_frame"},
                       "spec_hash": "78ce646cb2586489"}
MEMBERS_PREFIX = "rav-collection/1\n"   # = service/snapshot.py
METADATA_KEYS = ("width", "height", "duration_ms", "n_frames", "n_units")
MAX_PAGE = 100
_UNIT_T0 = re.compile(r":(-?\d+(?:\.\d+)?)-")


def canonical_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def members_hash(unit_ids) -> str:
    return hashlib.sha256((MEMBERS_PREFIX + "\n".join(sorted(unit_ids))).encode()).hexdigest()


def context_hash(context: dict) -> str:
    doc = {k: v for k, v in context.items() if k != "app"}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def replay(decisions) -> tuple[set[str], set[str]]:
    """keep/drop/clear theo thứ tự seq (feedback `keep_drop`)."""
    kept: set[str] = set()
    dropped: set[str] = set()
    for d in decisions:
        u = d["unit_id"]
        kept.discard(u)
        dropped.discard(u)
        if d["action"] == "keep":
            kept.add(u)
        elif d["action"] == "drop":
            dropped.add(u)
    return kept, dropped


def require_user(actor: ActorContext) -> UUID:
    if actor.user_id is None:
        raise ServiceError("unauthorized", "pilot cần người dùng đã xác thực", 401)
    return actor.user_id


# ------------------------------------------------------------------------------
# DTO (identity spec §8.1)
# ------------------------------------------------------------------------------
class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Page(BaseModel):
    items: list[Any]
    total: int
    next_cursor: str | None


class PrepState(BaseModel):
    ready: bool
    job_id: UUID | None


class SourceDTO(BaseModel):
    id: UUID
    core_source_id: str   # unit ID dạng `core:t0-t1` (§21.16)
    kind: str
    scope: str
    protected: bool
    name: str
    description: str = ""
    published: bool = True
    availability: str
    resource_version: int
    metadata: dict[str, int | None]
    dino: PrepState
    clip: PrepState
    frames: PrepState   # ảnh thumbnail/frame lưu sẵn (§21.20)


class ReferenceIn(_In):
    dataset_version_ids: list[UUID] = Field(default_factory=list)
    model_id: UUID | None = None


class DatasetVersionDTO(BaseModel):
    id: UUID
    core_version_id: str
    n: int
    annotation_format: str
    classes: list[str]
    manifest_hash: str
    import_report: dict[str, Any]
    created_at: datetime


class DatasetDTO(BaseModel):
    id: UUID
    core_dataset_id: str
    source_id: UUID
    name: str
    description: str
    created_at: datetime
    versions: list[DatasetVersionDTO] = Field(default_factory=list)


class ModelDeclarationDTO(BaseModel):
    model_id: UUID
    dataset_version_id: UUID
    relation: str
    declared_by: UUID
    evidence: dict[str, Any]
    created_at: datetime


class ModelDetailDTO(BaseModel):
    id: UUID
    core_model_id: str
    scope: str
    protected: bool
    name: str
    description: str = ""
    published: bool = True
    sha256: str
    size_bytes: int
    checkpoint_revision: str
    task: str
    class_map: dict[str, Any]
    runtime_manifest: dict[str, Any]
    license: dict[str, Any]
    provenance: dict[str, Any]
    validation: str
    created_at: datetime
    declarations: list[ModelDeclarationDTO] = Field(default_factory=list)


class SessionReferenceDTO(BaseModel):
    session_id: UUID
    dataset_versions: list[DatasetVersionDTO]
    model: ModelDetailDTO | None


class UpdateReferenceIn(_In):
    reference: ReferenceIn | None = None
    expected_state_version: int = Field(ge=1)


class CreateSessionInput(_In):
    name: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=2000)
    target_k: int = Field(gt=0, le=100_000)
    pool_source_ids: list[UUID] = Field(min_length=1, max_length=50)
    reference: ReferenceIn | None = None


class SessionDTO(BaseModel):
    id: UUID
    name: str
    goal: str
    target_k: int
    pool_source_ids: list[UUID]
    reference: ReferenceIn | None
    state_version: int
    n_units: int
    n_kept: int = 0       # replay quyết định (§21.16, sidebar)
    n_dropped: int = 0
    created_at: datetime
    started_from_template_id: UUID | None


class DecisionChange(_In):
    unit_id: str = Field(min_length=1, max_length=300)
    action: Literal["keep", "drop", "clear"]
    proposal_n: int | None = Field(default=None, ge=1)
    shown_at: datetime | None = None
    reason: str | None = Field(default=None, max_length=1000)
    confidence: float | None = Field(default=None, ge=0, le=1)
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("extra")
    @classmethod
    def _no_identity(cls, v: dict) -> dict:
        bad = {"actor", "owner_id", "admin", "role"} & set(v)
        if bad:
            raise ValueError(f"extra không được chứa {sorted(bad)}")
        return v


class DecisionInput(_In):
    changes: list[DecisionChange] = Field(min_length=1, max_length=500)
    expected_state_version: int = Field(ge=1)


class ReviewStateDTO(BaseModel):
    session_id: UUID
    state_version: int
    decision_seq: int
    kept_ids: list[str]
    dropped_ids: list[str]


class SnapshotInput(_In):
    note: str | None = Field(default=None, max_length=500)
    expected_state_version: int = Field(ge=1)


class SnapshotDTO(BaseModel):
    id: UUID
    session_id: UUID
    n: int
    n_members: int
    decision_seq: int
    proposal_cutoff_n: int
    members_hash: str
    context_hash: str
    created_at: datetime
    note: str | None


class SnapshotMemberDTO(BaseModel):
    ord: int
    unit_id: str
    source_id: UUID
    core_source_id: str
    source_kind: str


class SnapshotDetailDTO(SnapshotDTO):
    context: dict
    members: list[SnapshotMemberDTO]


# ------------------------------------------------------------------------------
# PilotDB
# ------------------------------------------------------------------------------
class PilotDB:
    """Kết nối PostgreSQL pilot. `url` phải là `postgresql://…` của role `rav_pilot_api` (API) hoặc `rav_pilot_worker` (worker)."""

    def __init__(self, url: str):
        if not url or not url.startswith(("postgresql://", "postgres://")):
            raise ServiceError("dependency_unavailable", "pilot cần DATABASE_URL PostgreSQL (không fallback SQLite)", 503)
        self.url = url
        self._pool = None
        self._pool_lock = threading.Lock()

    @contextmanager
    def _connection(self):
        import psycopg
        from psycopg.rows import dict_row

        class TimedCursor(psycopg.Cursor):
            def execute(self, query, params=None, **kwargs):
                # Pipeline execute only queues SQL; its enclosing span includes sync.
                stage = "db.enqueue" if self.connection.info.pipeline_status else "db.execute"
                with timing(stage):
                    return super().execute(query, params, **kwargs)

        from psycopg_pool import ConnectionPool, PoolClosed, PoolTimeout

        if self._pool is None:
            with self._pool_lock:
                if self._pool is None:
                    pool = ConnectionPool(self.url, min_size=0, max_size=4, timeout=10, open=True,
                                          kwargs={"row_factory": dict_row, "connect_timeout": 10, "autocommit": True, "cursor_factory": TimedCursor})
                    weakref.finalize(self, pool.close)
                    self._pool = pool
        try:
            with timing("db.checkout"):
                conn = self._pool.getconn()
            try:
                yield conn
            finally:
                self._pool.putconn(conn)
        except (psycopg.OperationalError, PoolTimeout, PoolClosed) as e:
            raise ServiceError("dependency_unavailable", "không kết nối được PostgreSQL", 503) from e

    def close(self) -> None:
        """Đóng pool khi caller kết thúc; GC/process exit cũng đóng instance không còn dùng."""
        if self._pool is not None:
            self._pool.close()

    @contextmanager
    def backend(self) -> Iterator[Any]:
        """Transaction role backend: caller tự kiểm quyền và khóa theo thứ tự §21.3."""
        with timing("db.transaction"), self._connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    yield cur

    @contextmanager
    def as_user(self, user_id: UUID) -> Iterator[Any]:
        """Transaction đọc với RLS của người dùng (role authenticated + claims đã xác minh)."""
        with timing("db.transaction"), self._connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SET LOCAL ROLE authenticated")
                    cur.execute("SELECT set_config('request.jwt.claims', %s, true)",
                                (json.dumps({"sub": str(user_id), "role": "authenticated"}),))
                    yield cur

    def ensure_schema(self) -> None:
        with self.backend() as cur:
            problem = schema_problem(cur)
        if problem:
            raise ServiceError("schema_not_ready", f"schema pilot chưa sẵn sàng: {problem}", 503)

    # --------------------------------------------------------------------------
    # Khóa dùng chung (thứ tự §21.3)
    # --------------------------------------------------------------------------
    @staticmethod
    def lock_profile(cur, user_id: UUID) -> dict:
        cur.execute("SELECT user_id, active, authz_version, policy_id FROM rav_pilot_profile WHERE user_id = %s FOR UPDATE",
                    (user_id,))
        row = cur.fetchone()
        if not row:
            raise ServiceError("unauthorized", "tài khoản chưa được cấp quyền dùng pilot", 401)
        if not row["active"]:
            raise ServiceError("account_disabled", "tài khoản đã bị vô hiệu hóa", 401)
        return row

    @staticmethod
    def read_profile(cur, user_id: UUID) -> dict:
        """Authorize from committed snapshot without exclusive lock (§21.23)."""
        cur.execute("SELECT user_id, active, authz_version, policy_id FROM rav_pilot_profile WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        if not row:
            raise ServiceError("unauthorized", "tài khoản chưa được cấp quyền dùng pilot", 401)
        if not row["active"]:
            raise ServiceError("account_disabled", "tài khoản đã bị vô hiệu hóa", 401)
        return row

    @staticmethod
    def can_read_source_in_tx(cur, user_id: UUID, src: dict) -> bool:
        """Kiểm quyền đọc nguồn ở đường backend() (§21.35): PRIVATE theo owner; SYSTEM cần published=true, admin, hoặc đã gắn phiên/snapshot của user."""
        if src["scope"] == "PRIVATE":
            return src["owner_id"] == user_id
        if src.get("published", True):
            return True
        from .pilot_operations import operations_read

        if operations_read(user_id):
            return True
        cur.execute(
            "SELECT 1 FROM rav_pilot_session_source WHERE owner_id = %s AND source_id = %s "
            "UNION ALL "
            "SELECT 1 FROM rav_pilot_snapshot_member WHERE owner_id = %s AND source_id = %s "
            "UNION ALL "
            "SELECT 1 FROM rav_pilot_session_reference sr "
            "JOIN rav_pilot_dataset_version dv ON dv.id = sr.dataset_version_id "
            "JOIN rav_pilot_dataset d ON d.id = dv.dataset_id "
            "WHERE sr.owner_id = %s AND d.source_id = %s "
            "LIMIT 1",
            (user_id, src["id"], user_id, src["id"], user_id, src["id"]),
        )
        return cur.fetchone() is not None

    @staticmethod
    def can_read_model_in_tx(cur, user_id: UUID, model: dict) -> bool:
        """Kiểm quyền đọc model SYSTEM ở đường backend() (§21.35): published=true, admin, hoặc đã gắn phiên/proposal của user."""
        if model.get("published", True):
            return True
        from .pilot_operations import operations_read

        if operations_read(user_id):
            return True
        mid_str = str(model["id"])
        core_id = model["core_model_id"]
        cur.execute(
            "SELECT 1 FROM rav_pilot_session_reference WHERE owner_id = %s AND model_id = %s "
            "UNION ALL "
            "SELECT 1 FROM rav_pilot_proposal WHERE owner_id = %s "
            "AND (frozen_input -> 'model' ->> 'id' = %s OR frozen_input -> 'model' ->> 'core_model_id' = %s) "
            "UNION ALL "
            "SELECT 1 FROM rav_pilot_session WHERE owner_id = %s "
            "AND (spec -> 'initial_strategy' -> 'model' ->> 'id' = %s "
            "     OR spec -> 'initial_strategy' -> 'model' ->> 'core_model_id' = %s "
            "     OR spec -> 'reference' ->> 'model_id' = %s) "
            "LIMIT 1",
            (user_id, model["id"], user_id, mid_str, core_id, user_id, mid_str, core_id, core_id),
        )
        return cur.fetchone() is not None

    @staticmethod
    def lock_sources(cur, user_id: UUID, source_ids: list[UUID], *, require_published_system: bool = False) -> dict[UUID, dict]:
        """Khóa nguồn theo UUID tăng dần; ngoài phạm vi hoặc SYSTEM ẩn chưa được cấp quyền qua phiên → 404; SYSTEM ẩn khi tạo phiên mới → 409."""
        ids = sorted(set(source_ids))
        cur.execute("SELECT * FROM rav_pilot_source_asset WHERE id = ANY(%s) ORDER BY id FOR UPDATE", (ids,))
        rows = {r["id"]: r for r in cur.fetchall()}
        for sid in ids:
            r = rows.get(sid)
            if r is None or not PilotDB.can_read_source_in_tx(cur, user_id, r):
                raise ServiceError("not_found", f"không tìm thấy nguồn {sid}", 404)
            if require_published_system and r["scope"] == "SYSTEM" and not r.get("published", True):
                raise ServiceError("source_unavailable", f"nguồn mẫu {sid} đang ẩn, không thể tạo phiên mới", 409)
        return rows

    @staticmethod
    def lock_own_session(cur, user_id: UUID, session_id: UUID) -> dict:
        cur.execute("SELECT * FROM rav_pilot_session WHERE id = %s AND owner_id = %s FOR UPDATE", (session_id, user_id))
        row = cur.fetchone()
        if not row:
            raise ServiceError("not_found", f"không tìm thấy phiên {session_id}", 404)
        return row

    @staticmethod
    def receipt(cur, user_id: UUID, action: str, key: str, input_hash: str) -> dict | None:
        """Receipt idempotent: cùng key + input → safe_output cũ; khác input → 409."""
        cur.execute("SELECT input_hash, safe_output FROM rav_pilot_command_receipt "
                    "WHERE owner_id = %s AND action = %s AND idempotency_key = %s", (user_id, action, key))
        row = cur.fetchone()
        if row and row["input_hash"] != input_hash:
            raise ServiceError("idempotency_conflict", "Idempotency-Key đã dùng cho đầu vào khác", 409)
        return row["safe_output"] if row else None

    @staticmethod
    def write_receipt(cur, user_id: UUID, action: str, key: str, input_hash: str, output: BaseModel) -> None:
        from psycopg.types.json import Jsonb

        cur.execute("INSERT INTO rav_pilot_command_receipt (id, owner_id, action, idempotency_key, input_hash, safe_output) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (uuid4(), user_id, action, key, input_hash, Jsonb(output.model_dump(mode="json"))))

    @staticmethod
    def pin(cur, source_id: UUID, consumer_kind: str, consumer_id: UUID, role: str, version: int) -> None:
        cur.execute("INSERT INTO rav_pilot_source_pin (id, source_id, consumer_kind, consumer_id, input_role, source_version) "
                    "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (uuid4(), source_id, consumer_kind, consumer_id, role, version))

    # --------------------------------------------------------------------------
    # Profile (auth dùng — role backend)
    # --------------------------------------------------------------------------
    def get_profile(self, user_id: UUID) -> dict | None:
        with self.backend() as cur:
            cur.execute("SELECT user_id, active, display_name, authz_version, policy_id FROM rav_pilot_profile WHERE user_id = %s",
                        (user_id,))
            return cur.fetchone()

    def ensure_profile(self, user_id: UUID, display_name: str = "") -> dict:
        """Tạo hồ sơ + tài khoản dung lượng idempotent trong lần xác thực đầu tiên (CONTRACTS §21.34)."""
        existing = self.get_profile(user_id)
        if existing is not None:
            return existing
        from .pilot_runtime import policy_problem

        clean_name = "".join(ch for ch in (display_name or "") if ch.isprintable()).strip()[:80] or f"user-{user_id.hex[:6]}"
        env_version = os.environ.get("RAV_PILOT_SIGNUP_POLICY_VERSION", "").strip()
        with self.backend() as cur:
            cur.execute("SELECT user_id, active, display_name, authz_version, policy_id FROM rav_pilot_profile WHERE user_id = %s",
                        (user_id,))
            row = cur.fetchone()
            if row is not None:
                return row
            if env_version:
                cur.execute("SELECT id, version, config, enabled FROM rav_pilot_quota_policy WHERE version = %s",
                            (env_version,))
            else:
                cur.execute("SELECT id, version, config, enabled FROM rav_pilot_quota_policy "
                            "WHERE signup_default = true AND enabled = true LIMIT 1")
            pol = cur.fetchone()
            if not pol or not pol["enabled"] or policy_problem(pol["config"]) is not None:
                raise ServiceError("policy_unconfigured",
                                   "chưa có cấu hình quota/chính sách đăng ký hợp lệ từ operator", 503)
            cfg = pol["config"]
            cur.execute("INSERT INTO rav_pilot_profile (user_id, active, display_name, policy_id) "
                        "VALUES (%s, true, %s, %s) ON CONFLICT (user_id) DO NOTHING",
                        (user_id, clean_name, pol["id"]))
            cur.execute("INSERT INTO rav_pilot_storage_account (account_key, limit_bytes) "
                        "VALUES (%s, %s), ('GLOBAL', %s) ON CONFLICT (account_key) DO NOTHING",
                        (f"USER:{user_id}", int(cfg.get("storage_user_bytes", 0)), int(cfg.get("storage_global_bytes", 0))))
            cur.execute("SELECT user_id, active, display_name, authz_version, policy_id FROM rav_pilot_profile WHERE user_id = %s",
                        (user_id,))
            row = cur.fetchone()
            if row is None:
                raise ServiceError("unauthorized", "tài khoản chưa được cấp quyền dùng pilot", 401)
            return row

    # --------------------------------------------------------------------------
    # Nguồn (đọc qua RLS)
    # --------------------------------------------------------------------------
    _SOURCE_COLS = "id, core_source_id, kind, scope, protected, name, description, published, availability, resource_version, metadata, owner_id"

    @staticmethod
    def _source_dto(row: dict, prep: dict[str, PrepState] | None = None) -> SourceDTO:
        # Readiness DINO/CLIP = artifact FEATURE READY do worker/operator publish (§21.14), không suy từ file.
        meta = row.get("metadata") or {}
        prep = prep or {}
        return SourceDTO(id=row["id"], core_source_id=row["core_source_id"], kind=row["kind"], scope=row["scope"],
                         protected=row["protected"], name=row["name"], description=row.get("description") or "",
                         published=bool(row.get("published", True)), availability=row["availability"],
                         resource_version=row["resource_version"], metadata={k: meta.get(k) for k in METADATA_KEYS},
                         dino=prep.get("DINO") or PrepState(ready=False, job_id=None),
                         clip=prep.get("CLIP") or PrepState(ready=False, job_id=None),
                         frames=prep.get("FRAMES") or PrepState(ready=False, job_id=None))

    def _readiness(self, uid: UUID, source_ids: list[UUID]) -> dict[UUID, dict[str, PrepState]]:
        """Artifact/job không mở cho `authenticated`; đọc bằng backend, lọc theo owner tường minh."""
        from . import pilot_frames
        from .pilot_features import readiness_in_tx
        with self.backend() as cur:
            out = readiness_in_tx(cur, uid, source_ids)
            for sid, state in pilot_frames.readiness_in_tx(cur, uid, source_ids).items():
                out[sid]["FRAMES"] = state
            return out

    def list_sources(self, actor: ActorContext, cursor: str | None = None, limit: int = 50, kind: str | None = None,
                     include_unpublished: bool = False) -> Page:
        uid = require_user(actor)
        offset, limit = _page_args(cursor, limit)
        if include_unpublished:
            from .pilot_operations import operations_read

            if not operations_read(uid):
                raise ServiceError("forbidden", "chỉ operator được xem danh mục tài nguyên ẩn", 403)
            where, args = "availability <> 'DELETED' AND (owner_id = %s OR scope = 'SYSTEM')", [uid]
            if kind:
                where += " AND kind = %s"
                args.append(kind)
            with self.backend() as cur:
                self.read_profile(cur, uid)
                cur.execute(f"SELECT count(*) AS n FROM rav_pilot_source_asset WHERE {where}", args)
                total = cur.fetchone()["n"]
                cur.execute(f"SELECT {self._SOURCE_COLS} FROM rav_pilot_source_asset WHERE {where} "
                            "ORDER BY created_at, id LIMIT %s OFFSET %s", [*args, limit, offset])
                rows = cur.fetchall()
        else:
            where, args = "availability <> 'DELETED' AND (scope = 'PRIVATE' OR published = true)", []
            if kind:
                where += " AND kind = %s"
                args.append(kind)
            with self.as_user(uid) as cur:
                cur.execute(f"SELECT count(*) AS n FROM rav_pilot_source_asset WHERE {where}", args)
                total = cur.fetchone()["n"]
                cur.execute(f"SELECT {self._SOURCE_COLS} FROM rav_pilot_source_asset WHERE {where} "
                            "ORDER BY created_at, id LIMIT %s OFFSET %s", [*args, limit, offset])
                rows = cur.fetchall()
        prep = self._readiness(uid, [r["id"] for r in rows])
        items = [self._source_dto(r, prep.get(r["id"])) for r in rows]
        return Page(items=items, total=total, next_cursor=str(offset + limit) if offset + limit < total else None)

    def get_source(self, actor: ActorContext, source_id: UUID) -> SourceDTO:
        uid = require_user(actor)
        with self.as_user(uid) as cur:
            cur.execute(f"SELECT {self._SOURCE_COLS} FROM rav_pilot_source_asset WHERE id = %s", (source_id,))
            row = cur.fetchone()
        if not row:
            from .pilot_operations import operations_read

            if operations_read(uid):
                with self.backend() as cur:
                    self.read_profile(cur, uid)
                    cur.execute(f"SELECT {self._SOURCE_COLS} FROM rav_pilot_source_asset WHERE id = %s", (source_id,))
                    row = cur.fetchone()
        if not row:
            raise ServiceError("not_found", f"không tìm thấy nguồn {source_id}", 404)
        return self._source_dto(row, self._readiness(uid, [row["id"]]).get(row["id"]))

    # --------------------------------------------------------------------------
    # Phiên
    # --------------------------------------------------------------------------
    def create_session(self, actor: ActorContext, inp: CreateSessionInput, idempotency_key: str,
                       started_from_template_id: UUID | None = None) -> SessionDTO:

        uid = require_user(actor)
        input_hash = canonical_hash({"input": inp.model_dump(mode="json"),
                                     "template": str(started_from_template_id) if started_from_template_id else None})
        if len(set(inp.pool_source_ids)) != len(inp.pool_source_ids):
            raise ServiceError("validation_error", "pool_source_ids bị trùng", 422)
        with self.backend() as cur:
            self.lock_profile(cur, uid)
            sources = self.lock_sources(cur, uid, inp.pool_source_ids, require_published_system=True)
            if (old := self.receipt(cur, uid, "CREATE_SESSION", idempotency_key, input_hash)) is not None:
                return SessionDTO(**old)
            dto = self.create_session_in_tx(cur, uid, inp, sources, started_from_template_id)
            self.write_receipt(cur, uid, "CREATE_SESSION", idempotency_key, input_hash, dto)
            return dto

    def create_session_in_tx(self, cur, uid: UUID, inp: CreateSessionInput, sources: dict[UUID, dict],
                             started_from_template_id: UUID | None) -> SessionDTO:
        """Lõi tạo phiên (caller đã khóa profile + nguồn): kiểm pool, tạo phiên, nguồn, tham chiếu, pin. Không ghi receipt."""
        from psycopg.types.json import Jsonb

        pool = [sources[s] for s in inp.pool_source_ids]
        core_ids = [s["core_source_id"] for s in pool]
        if len(set(core_ids)) != len(core_ids):
            raise ServiceError("duplicate_core_source_in_pool", "pool có hai nguồn cùng core_source_id", 422)
        for s in pool:
            if s["kind"] != "VIDEO":
                raise ServiceError("validation_error", f"nguồn {s['id']} không phải video", 422)
            if s["availability"] != "READY":
                raise ServiceError("source_unavailable", f"nguồn {s['id']} đang {s['availability']}", 409)
            if s["scope"] == "SYSTEM" and not s.get("published", True):
                raise ServiceError("source_unavailable", f"nguồn mẫu {s['id']} đang ẩn, không thể tạo phiên mới", 409)
            if not isinstance((s["metadata"] or {}).get("n_units"), int):
                raise ServiceError("source_unavailable", f"nguồn {s['id']} chưa được worker xác minh", 409)
        ref_spec, ref_rows = self._resolve_reference(cur, inp.reference, uid=uid, require_published=True)
        sid = uuid4()
        spec = {"format": 1, "pool": {"video_ids": core_ids}, "reference": ref_spec, "goal": inp.goal,
                "target_k": inp.target_k, "feedback": {"type": "keep_drop"},
                "decoder": DEFAULT_SAMPLE_SPEC["decoder"], "units": DEFAULT_SAMPLE_SPEC["units"]}
        n_units = sum(s["metadata"]["n_units"] for s in pool)
        cur.execute("INSERT INTO rav_pilot_session (id, owner_id, name, goal, target_k, spec, n_units, started_from_template_id) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING created_at",
                    (sid, uid, inp.name, inp.goal, inp.target_k, Jsonb(spec), n_units, started_from_template_id))
        created_at = cur.fetchone()["created_at"]
        for ord_, s in enumerate(pool):
            cur.execute("INSERT INTO rav_pilot_session_source (session_id, owner_id, source_id, ord) VALUES (%s, %s, %s, %s)",
                        (sid, uid, s["id"], ord_))
            self.pin(cur, s["id"], "SESSION", sid, "POOL", s["resource_version"])
        for version_id, model_id, ref_source in ref_rows:
            cur.execute("INSERT INTO rav_pilot_session_reference (session_id, owner_id, dataset_version_id, model_id) "
                        "VALUES (%s, %s, %s, %s)", (sid, uid, version_id, model_id))
            self.pin(cur, ref_source["id"], "REFERENCE", sid, "REFERENCE", ref_source["resource_version"])
        return SessionDTO(id=sid, name=inp.name, goal=inp.goal, target_k=inp.target_k, pool_source_ids=inp.pool_source_ids,
                          reference=inp.reference, state_version=1, n_units=n_units, created_at=created_at,
                          started_from_template_id=started_from_template_id)

    @staticmethod
    def _resolve_reference(cur, ref: ReferenceIn | None, uid: UUID | None = None, *,
                           require_published: bool = True) -> tuple[dict, list[tuple[UUID, UUID | None, dict]]]:
        """Dataset version/model SYSTEM có thật; model phải có khai báo TRAIN/FINETUNE với một version trong tham chiếu."""
        if ref is None or (not ref.dataset_version_ids and ref.model_id is None):
            return {"dataset_version_ids": [], "model_id": None}, []
        if not ref.dataset_version_ids:
            raise ServiceError("reference_invalid", "model tham chiếu cần dataset version đi kèm", 422)
        cur.execute("SELECT v.id, v.core_version_id, v.dataset_id, s.id AS source_id, s.resource_version, s.availability, "
                    "s.scope, s.owner_id, s.published "
                    "FROM rav_pilot_dataset_version v JOIN rav_pilot_dataset d ON d.id = v.dataset_id "
                    "JOIN rav_pilot_source_asset s ON s.id = d.source_id WHERE v.id = ANY(%s)",
                    (list(ref.dataset_version_ids),))
        rows = {r["id"]: r for r in cur.fetchall()}
        missing = [str(v) for v in ref.dataset_version_ids if v not in rows]
        if missing:
            raise ServiceError("not_found", f"không tìm thấy dataset version {missing}", 404)
        for v in ref.dataset_version_ids:
            r = rows[v]
            src_info = {"id": r["source_id"], "scope": r["scope"], "owner_id": r["owner_id"], "published": r.get("published", True)}
            if uid is not None and not PilotDB.can_read_source_in_tx(cur, uid, src_info):
                raise ServiceError("not_found", f"không tìm thấy dataset version {[str(v)]}", 404)
            if require_published and r["scope"] == "SYSTEM" and not r.get("published", True):
                raise ServiceError("source_unavailable", "dataset tham chiếu đang ẩn, không thể gắn mới", 409)
        if len({rows[v]["dataset_id"] for v in ref.dataset_version_ids}) != len(ref.dataset_version_ids):
            raise ServiceError("reference_invalid", "mỗi dataset chỉ chọn một version", 422)
        if any(r["availability"] != "READY" for r in rows.values()):
            raise ServiceError("source_unavailable", "dataset tham chiếu không sẵn sàng", 409)
        core_model = None
        if ref.model_id is not None:
            cur.execute("SELECT id, core_model_id, published FROM rav_pilot_model_asset WHERE id = %s", (ref.model_id,))
            m = cur.fetchone()
            if not m or (uid is not None and not PilotDB.can_read_model_in_tx(cur, uid, m)):
                raise ServiceError("not_found", f"không tìm thấy model {ref.model_id}", 404)
            if require_published and not m.get("published", True):
                raise ServiceError("model_unavailable", "model tham chiếu đang ẩn, không thể gắn mới", 409)
            cur.execute("SELECT 1 FROM rav_pilot_model_dataset_declaration WHERE model_id = %s AND dataset_version_id = ANY(%s)",
                        (ref.model_id, list(ref.dataset_version_ids)))
            if not cur.fetchone():
                raise ServiceError("reference_invalid", "model không có khai báo train/finetune với dataset tham chiếu", 422)
            core_model = m["core_model_id"]
        spec = {"dataset_version_ids": [rows[v]["core_version_id"] for v in ref.dataset_version_ids], "model_id": core_model}
        return spec, [(v, ref.model_id, {"id": rows[v]["source_id"], "resource_version": rows[v]["resource_version"]})
                      for v in ref.dataset_version_ids]

    @staticmethod
    def _session_dto(cur, row: dict) -> SessionDTO:
        cur.execute("SELECT source_id FROM rav_pilot_session_source WHERE session_id = %s ORDER BY ord", (row["id"],))
        pool = [r["source_id"] for r in cur.fetchall()]
        cur.execute("SELECT dataset_version_id, model_id FROM rav_pilot_session_reference WHERE session_id = %s "
                    "ORDER BY dataset_version_id", (row["id"],))
        refs = cur.fetchall()
        reference = ReferenceIn(dataset_version_ids=[r["dataset_version_id"] for r in refs],
                                model_id=next((r["model_id"] for r in refs if r["model_id"]), None)) if refs else None
        cur.execute("SELECT unit_id, action FROM rav_pilot_decision WHERE session_id = %s ORDER BY seq", (row["id"],))
        kept, dropped = replay(cur.fetchall())
        return SessionDTO(id=row["id"], name=row["name"], goal=row["goal"], target_k=row["target_k"], pool_source_ids=pool,
                          reference=reference, state_version=row["state_version"], n_units=row["n_units"],
                          n_kept=len(kept), n_dropped=len(dropped),
                          created_at=row["created_at"], started_from_template_id=row["started_from_template_id"])

    def get_session(self, actor: ActorContext, session_id: UUID) -> SessionDTO:
        uid = require_user(actor)
        with self.as_user(uid) as cur:
            cur.execute("SELECT * FROM rav_pilot_session WHERE id = %s", (session_id,))
            row = cur.fetchone()
            if not row:
                raise ServiceError("not_found", f"không tìm thấy phiên {session_id}", 404)
            return self._session_dto(cur, row)

    def list_sessions(self, actor: ActorContext, cursor: str | None = None, limit: int = 50) -> Page:
        uid = require_user(actor)
        offset, limit = _page_args(cursor, limit)
        with self.as_user(uid) as cur:
            cur.execute("SELECT count(*) AS n FROM rav_pilot_session")
            total = cur.fetchone()["n"]
            cur.execute("SELECT * FROM rav_pilot_session ORDER BY created_at DESC, id LIMIT %s OFFSET %s", (limit, offset))
            items = [self._session_dto(cur, r) for r in cur.fetchall()]
        return Page(items=items, total=total, next_cursor=str(offset + limit) if offset + limit < total else None)

    # --------------------------------------------------------------------------
    # Catalog Dataset & Model tham chiếu, Binding phiên (§21.6, §21.7, §21.17)
    # --------------------------------------------------------------------------
    def list_datasets(self, actor: ActorContext) -> list[DatasetDTO]:
        require_user(actor)
        with self.backend() as cur:
            cur.execute("SELECT d.* FROM rav_pilot_dataset d JOIN rav_pilot_source_asset s ON s.id = d.source_id "
                        "WHERE s.published = true ORDER BY d.created_at, d.id")
            d_rows = cur.fetchall()
            if not d_rows:
                return []
            d_ids = [r["id"] for r in d_rows]
            cur.execute("SELECT * FROM rav_pilot_dataset_version WHERE dataset_id = ANY(%s) ORDER BY n ASC", (d_ids,))
            v_rows = cur.fetchall()
            versions_by_dataset: dict[UUID, list[DatasetVersionDTO]] = {}
            for v in v_rows:
                versions_by_dataset.setdefault(v["dataset_id"], []).append(self._row_to_version_dto(v))
            return [
                DatasetDTO(
                    id=d["id"],
                    core_dataset_id=d["core_dataset_id"],
                    source_id=d["source_id"],
                    name=d["name"],
                    description=d["description"],
                    created_at=d["created_at"],
                    versions=versions_by_dataset.get(d["id"], []),
                )
                for d in d_rows
            ]

    def get_dataset(self, actor: ActorContext, dataset_id: UUID) -> DatasetDTO:
        uid = require_user(actor)
        with self.backend() as cur:
            cur.execute("SELECT d.*, s.scope, s.owner_id, s.published FROM rav_pilot_dataset d "
                        "JOIN rav_pilot_source_asset s ON s.id = d.source_id WHERE d.id = %s", (dataset_id,))
            d = cur.fetchone()
            if not d or not self.can_read_source_in_tx(
                cur, uid, {"id": d["source_id"], "scope": d["scope"], "owner_id": d["owner_id"], "published": d.get("published", True)}
            ):
                raise ServiceError("not_found", f"không tìm thấy dataset {dataset_id}", 404)
            cur.execute("SELECT * FROM rav_pilot_dataset_version WHERE dataset_id = %s ORDER BY n ASC", (dataset_id,))
            v_rows = cur.fetchall()
            return DatasetDTO(
                id=d["id"],
                core_dataset_id=d["core_dataset_id"],
                source_id=d["source_id"],
                name=d["name"],
                description=d["description"],
                created_at=d["created_at"],
                versions=[self._row_to_version_dto(v) for v in v_rows],
            )

    def get_model(self, actor: ActorContext, model_id: UUID) -> ModelDetailDTO:
        uid = require_user(actor)
        with self.backend() as cur:
            return self._get_model_detail(cur, model_id, uid=uid)

    @staticmethod
    def _row_to_version_dto(r: dict[str, Any]) -> DatasetVersionDTO:
        return DatasetVersionDTO(
            id=r["id"],
            core_version_id=r["core_version_id"],
            n=r["n"],
            annotation_format=r["annotation_format"],
            classes=r["classes"] or [],
            manifest_hash=r["manifest_hash"],
            import_report=r["import_report"] or {},
            created_at=r["created_at"],
        )

    @classmethod
    def _get_model_detail(cls, cur, model_id: UUID, uid: UUID | None = None) -> ModelDetailDTO:
        cur.execute("SELECT * FROM rav_pilot_model_asset WHERE id = %s", (model_id,))
        m = cur.fetchone()
        if not m or (uid is not None and not cls.can_read_model_in_tx(cur, uid, m)):
            raise ServiceError("not_found", f"không tìm thấy model {model_id}", 404)
        cur.execute("SELECT * FROM rav_pilot_model_dataset_declaration WHERE model_id = %s", (model_id,))
        dec_rows = cur.fetchall()
        decs = [
            ModelDeclarationDTO(
                model_id=r["model_id"],
                dataset_version_id=r["dataset_version_id"],
                relation=r["relation"],
                declared_by=r["declared_by"],
                evidence=r["evidence"] or {},
                created_at=r["created_at"],
            )
            for r in dec_rows
        ]
        return ModelDetailDTO(
            id=m["id"],
            core_model_id=m["core_model_id"],
            scope=m["scope"],
            protected=m["protected"],
            name=m["name"],
            description=m.get("description") or "",
            published=bool(m.get("published", True)),
            sha256=m["sha256"],
            size_bytes=m["size_bytes"],
            checkpoint_revision=m["checkpoint_revision"],
            task=m["task"],
            class_map=m["class_map"] or {},
            runtime_manifest=m["runtime_manifest"] or {},
            license=m["license"] or {},
            provenance=m["provenance"] or {},
            validation=m["validation"],
            created_at=m["created_at"],
            declarations=decs,
        )

    def get_session_reference(self, actor: ActorContext, session_id: UUID) -> SessionReferenceDTO:
        uid = require_user(actor)
        with self.backend() as cur:
            cur.execute("SELECT id FROM rav_pilot_session WHERE id = %s AND owner_id = %s", (session_id, uid))
            if not cur.fetchone():
                raise ServiceError("not_found", f"không tìm thấy phiên {session_id}", 404)
            cur.execute("SELECT dataset_version_id, model_id FROM rav_pilot_session_reference WHERE session_id = %s "
                        "ORDER BY dataset_version_id", (session_id,))
            refs = cur.fetchall()
            if not refs:
                return SessionReferenceDTO(session_id=session_id, dataset_versions=[], model=None)

            version_ids = [r["dataset_version_id"] for r in refs]
            cur.execute("SELECT * FROM rav_pilot_dataset_version WHERE id = ANY(%s) ORDER BY n", (version_ids,))
            v_rows = cur.fetchall()
            v_dtos = [self._row_to_version_dto(r) for r in v_rows]

            model_id = next((r["model_id"] for r in refs if r["model_id"]), None)
            m_dto = self._get_model_detail(cur, model_id, uid=uid) if model_id else None
            return SessionReferenceDTO(session_id=session_id, dataset_versions=v_dtos, model=m_dto)

    def update_session_reference(self, actor: ActorContext, session_id: UUID, inp: UpdateReferenceIn) -> SessionDTO:
        uid = require_user(actor)
        with self.backend() as cur:
            self.lock_profile(cur, uid)
            cur.execute("SELECT * FROM rav_pilot_session WHERE id = %s AND owner_id = %s FOR UPDATE",
                        (session_id, uid))
            sess = cur.fetchone()
            if not sess:
                raise ServiceError("not_found", f"không tìm thấy phiên {session_id}", 404)
            if sess["state_version"] != inp.expected_state_version:
                raise ServiceError("state_version_conflict",
                                   f"state_version hiện tại ({sess['state_version']}) khác expected ({inp.expected_state_version})", 409)

            _, ref_rows = self._resolve_reference(cur, inp.reference, uid=uid, require_published=True)

            # 1. Giải phóng pins cũ của REFERENCE cho session này
            cur.execute("UPDATE rav_pilot_source_pin SET released_at = now() "
                        "WHERE consumer_kind = 'REFERENCE' AND consumer_id = %s AND released_at IS NULL", (session_id,))

            # 2. Xóa các rav_pilot_session_reference cũ
            cur.execute("DELETE FROM rav_pilot_session_reference WHERE session_id = %s AND owner_id = %s",
                        (session_id, uid))

            # 3. Ghi rav_pilot_session_reference mới và đặt pin mới
            for version_id, model_id, ref_source in ref_rows:
                cur.execute("INSERT INTO rav_pilot_session_reference (session_id, owner_id, dataset_version_id, model_id) "
                            "VALUES (%s, %s, %s, %s)", (session_id, uid, version_id, model_id))
                self.pin(cur, ref_source["id"], "REFERENCE", session_id, "REFERENCE", ref_source["resource_version"])

            # 4. Tăng state_version của session
            cur.execute("UPDATE rav_pilot_session SET state_version = state_version + 1 "
                        "WHERE id = %s RETURNING *", (session_id,))
            updated_sess = cur.fetchone()
            return self._session_dto(cur, updated_sess)

    # --------------------------------------------------------------------------
    # Quyết định (append-only) và review state
    # --------------------------------------------------------------------------
    @staticmethod
    def _decisions(cur, session_id: UUID) -> list[dict]:
        cur.execute("SELECT seq, unit_id, action, proposal_n FROM rav_pilot_decision WHERE session_id = %s ORDER BY seq",
                    (session_id,))
        return cur.fetchall()

    @staticmethod
    def _state_dto(session_id: UUID, state_version: int, decisions: list[dict]) -> ReviewStateDTO:
        kept, dropped = replay(decisions)
        return ReviewStateDTO(session_id=session_id, state_version=state_version,
                              decision_seq=decisions[-1]["seq"] if decisions else 0,
                              kept_ids=sorted(kept), dropped_ids=sorted(dropped))

    def get_review_state(self, actor: ActorContext, session_id: UUID) -> ReviewStateDTO:
        uid = require_user(actor)
        with self.as_user(uid) as cur:
            cur.execute("SELECT state_version FROM rav_pilot_session WHERE id = %s", (session_id,))
            row = cur.fetchone()
            if not row:
                raise ServiceError("not_found", f"không tìm thấy phiên {session_id}", 404)
            return self._state_dto(session_id, row["state_version"], self._decisions(cur, session_id))

    @staticmethod
    def _pool_core_ids(cur, session_id: UUID) -> list[dict]:
        cur.execute("SELECT s.id, s.core_source_id, s.kind, s.metadata, ss.ord FROM rav_pilot_session_source ss "
                    "JOIN rav_pilot_source_asset s ON s.id = ss.source_id WHERE ss.session_id = %s ORDER BY ss.ord",
                    (session_id,))
        return cur.fetchall()

    def append_decisions(self, actor: ActorContext, session_id: UUID, inp: DecisionInput) -> ReviewStateDTO:
        """Append batch atomically; shared with proposal submission."""
        with self.backend() as cur:
            return self.append_decisions_in_tx(cur, actor, session_id, inp)

    def append_decisions_in_tx(self, cur, actor: ActorContext, session_id: UUID, inp: DecisionInput,
                               *, coalesce: bool = False) -> ReviewStateDTO:
        """Caller owns transaction; CAS, ordered batch INSERT, no partial decisions on enqueue failure."""
        from psycopg import errors as pg_errors
        from psycopg.types.json import Jsonb

        from .pilot_units import frame_index

        uid = require_user(actor)
        self.lock_profile(cur, uid)
        session = self.lock_own_session(cur, uid, session_id)
        if session["state_version"] != inp.expected_state_version:
            raise ServiceError("state_version_conflict",
                               f"state_version hiện là {session['state_version']}, client gửi {inp.expected_state_version}", 409)
        pool = {p["core_source_id"]: p for p in self._pool_core_ids(cur, session_id)}
        decisions = self._decisions(cur, session_id)
        kept, dropped = replay(decisions)
        to_insert = []
        changes = list({c.unit_id: c for c in inp.changes}.values()) if coalesce else inp.changes
        for c in changes:
            if c.unit_id.rsplit(":", 1)[0] not in pool:
                raise ServiceError("unit_not_in_pool", f"unit {c.unit_id} không thuộc pool của phiên", 422)
            source = pool[c.unit_id.rsplit(":", 1)[0]]
            frame_index(c.unit_id, source["core_source_id"], source["metadata"] or {})
            if coalesce and ((c.action == "keep" and c.unit_id in kept) or (c.action == "drop" and c.unit_id in dropped)):
                continue
            if c.action == "clear" and c.unit_id not in kept | dropped:
                continue   # clear unit chưa có quyết định: không ghi (giống keep_drop local)
            kept.discard(c.unit_id)
            dropped.discard(c.unit_id)
            (kept if c.action == "keep" else dropped if c.action == "drop" else set()).add(c.unit_id)
            to_insert.append(c)
        if len(kept) > session["target_k"]:
            raise ServiceError("target_k_reached", f"vượt target_k={session['target_k']} (sẽ giữ {len(kept)})", 422)
        if not to_insert:
            return self._state_dto(session_id, session["state_version"], decisions)
        try:
            with timing("db.pipeline"):
                cur.executemany("INSERT INTO rav_pilot_decision (session_id, owner_id, unit_id, action, actor, goal, proposal_n, "
                                "shown_at, reason, confidence, extra) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                                [(session_id, uid, c.unit_id, c.action, actor.actor_string, session["goal"], c.proposal_n,
                                  c.shown_at, c.reason, c.confidence, Jsonb(c.extra)) for c in to_insert])
        except pg_errors.ForeignKeyViolation as e:
            raise ServiceError("validation_error", "proposal_n không tồn tại trong phiên", 422) from e
        new_version = session["state_version"] + 1
        cur.execute("UPDATE rav_pilot_session SET state_version = %s WHERE id = %s", (new_version, session_id))
        return self._state_dto(session_id, new_version, self._decisions(cur, session_id))

    # --------------------------------------------------------------------------
    # Snapshot (server tự tính; bất biến)
    # --------------------------------------------------------------------------
    def create_snapshot(self, actor: ActorContext, session_id: UUID, inp: SnapshotInput, idempotency_key: str) -> SnapshotDTO:
        from psycopg.types.json import Jsonb

        uid = require_user(actor)
        input_hash = canonical_hash({"session_id": str(session_id), "input": inp.model_dump(mode="json")})
        with self.backend() as cur:
            self.lock_profile(cur, uid)
            pool_rows = self._pool_core_ids(cur, session_id) if self._own_session_exists(cur, uid, session_id) else []
            self.lock_sources(cur, uid, [p["id"] for p in pool_rows])
            session = self.lock_own_session(cur, uid, session_id)
            if (old := self.receipt(cur, uid, "CREATE_SNAPSHOT", idempotency_key, input_hash)) is not None:
                return SnapshotDTO(**old)
            if session["state_version"] != inp.expected_state_version:
                raise ServiceError("state_version_conflict",
                                   f"state_version hiện là {session['state_version']}, client gửi {inp.expected_state_version}", 409)
            decisions = self._decisions(cur, session_id)
            kept, _ = replay(decisions)
            if not kept:
                raise ServiceError("snapshot_empty", "phiên chưa giữ frame nào", 422)
            spec = session["spec"]
            context = {"format": 1,
                       "session": {"goal": spec["goal"], "target_k": spec["target_k"], "pool": spec["pool"],
                                   "reference": spec.get("reference", {"dataset_version_ids": []})},
                       "sample_spec": {"decoder": spec["decoder"], "units": spec["units"],
                                       "spec_hash": DEFAULT_SAMPLE_SPEC["spec_hash"]}}
            m_hash, c_hash = members_hash(kept), context_hash(context)
            cur.execute("SELECT * FROM rav_pilot_snapshot WHERE session_id = %s ORDER BY n DESC LIMIT 1", (session_id,))
            last = cur.fetchone()
            if last and last["members_hash"] == m_hash and last["context_hash"] == c_hash:
                dto = _snapshot_dto(last)
                self.write_receipt(cur, uid, "CREATE_SNAPSHOT", idempotency_key, input_hash, dto)
                return dto
            cur.execute("SELECT coalesce(max(n), 0) AS n FROM rav_pilot_proposal WHERE session_id = %s", (session_id,))
            cutoff = cur.fetchone()["n"]
            by_core = {p["core_source_id"]: p for p in pool_rows}
            last_keep = {d["unit_id"]: d for d in decisions if d["action"] == "keep"}

            def order(u: str):
                p = by_core[u.rsplit(":", 1)[0]]
                m = _UNIT_T0.search(u)
                return (p["ord"], float(m.group(1)) if m else 0.0, u)

            members = sorted(kept, key=order)
            snap_id = uuid4()
            n = (last["n"] if last else 0) + 1
            cur.execute("INSERT INTO rav_pilot_snapshot (id, session_id, owner_id, n, created_by, note, decision_seq, "
                        "proposal_cutoff_n, members_hash, n_members, context, context_hash) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
                        (snap_id, session_id, uid, n, uid, inp.note, decisions[-1]["seq"], cutoff, m_hash, len(members),
                         Jsonb(context), c_hash))
            snap = cur.fetchone()
            for i, u in enumerate(members):
                p = by_core[u.rsplit(":", 1)[0]]
                d = last_keep[u]
                cur.execute("INSERT INTO rav_pilot_snapshot_member (snapshot_id, owner_id, ord, unit_id, source_id, core_source_id, "
                            "source_kind, provenance) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                            (snap_id, uid, i, u, p["id"], p["core_source_id"], "video",
                             Jsonb({"decision_seq": d["seq"], "proposal_n": d["proposal_n"]})))
            for p in {by_core[u.rsplit(":", 1)[0]]["id"] for u in members}:
                cur.execute("SELECT resource_version FROM rav_pilot_source_asset WHERE id = %s", (p,))
                self.pin(cur, p, "SNAPSHOT", snap_id, "INPUT", cur.fetchone()["resource_version"])
            dto = _snapshot_dto(snap)
            self.write_receipt(cur, uid, "CREATE_SNAPSHOT", idempotency_key, input_hash, dto)
            return dto

    @staticmethod
    def _own_session_exists(cur, uid: UUID, session_id: UUID) -> bool:
        cur.execute("SELECT 1 FROM rav_pilot_session WHERE id = %s AND owner_id = %s", (session_id, uid))
        return cur.fetchone() is not None

    def get_snapshot(self, actor: ActorContext, snapshot_id: UUID) -> SnapshotDetailDTO:
        uid = require_user(actor)
        with self.as_user(uid) as cur:
            cur.execute("SELECT * FROM rav_pilot_snapshot WHERE id = %s", (snapshot_id,))
            row = cur.fetchone()
            if not row:
                raise ServiceError("not_found", f"không tìm thấy snapshot {snapshot_id}", 404)
            cur.execute("SELECT ord, unit_id, source_id, core_source_id, source_kind FROM rav_pilot_snapshot_member "
                        "WHERE snapshot_id = %s ORDER BY ord", (snapshot_id,))
            members = [SnapshotMemberDTO(**r) for r in cur.fetchall()]
        return SnapshotDetailDTO(**_snapshot_dto(row).model_dump(), context=row["context"], members=members)

    def list_snapshots(self, actor: ActorContext, session_id: UUID, cursor: str | None = None, limit: int = 50) -> Page:
        uid = require_user(actor)
        offset, limit = _page_args(cursor, limit)
        with self.as_user(uid) as cur:
            cur.execute("SELECT 1 FROM rav_pilot_session WHERE id = %s", (session_id,))
            if not cur.fetchone():
                raise ServiceError("not_found", f"không tìm thấy phiên {session_id}", 404)
            cur.execute("SELECT count(*) AS n FROM rav_pilot_snapshot WHERE session_id = %s", (session_id,))
            total = cur.fetchone()["n"]
            cur.execute("SELECT * FROM rav_pilot_snapshot WHERE session_id = %s ORDER BY n DESC LIMIT %s OFFSET %s",
                        (session_id, limit, offset))
            items = [_snapshot_dto(r) for r in cur.fetchall()]
        return Page(items=items, total=total, next_cursor=str(offset + limit) if offset + limit < total else None)


def _snapshot_dto(row: dict) -> SnapshotDTO:
    return SnapshotDTO(id=row["id"], session_id=row["session_id"], n=row["n"], n_members=row["n_members"],
                       decision_seq=row["decision_seq"], proposal_cutoff_n=row["proposal_cutoff_n"],
                       members_hash=row["members_hash"], context_hash=row["context_hash"], created_at=row["created_at"],
                       note=row["note"])


def _page_args(cursor: str | None, limit: int) -> tuple[int, int]:
    try:
        offset = int(cursor) if cursor else 0
    except ValueError:
        raise ServiceError("validation_error", "cursor không hợp lệ", 422) from None
    if offset < 0 or not 1 <= limit <= MAX_PAGE:
        raise ServiceError("validation_error", f"limit phải trong 1..{MAX_PAGE}", 422)
    return offset, limit
