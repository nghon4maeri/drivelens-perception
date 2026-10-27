"""Đề xuất trên worker cho Studio v2 (CONTRACTS §21.15, #95): yêu cầu PROPOSE, đọc proposal, kết quả/thử lại job, hook publish.

- `request_proposal` (API, một transaction, khóa profile → nguồn → phiên): kiểm nhẹ (recipe pilot, cấu trúc config, giới hạn, đặc trưng sẵn sàng),
  đóng băng `state_version`/`decision_seq`/`proposal_count` + artifact FEATURE của pool rồi enqueue `PROPOSE` (queue CPU).
- `publish_in_tx` (hook runtime): khóa phiên, ba số còn khớp → INSERT proposal `n = proposal_count + 1`; lệch → `proposal_context_changed`.
- Catalog recipe pilot là JSON tĩnh sinh từ registry (`scripts/pilot_recipe_catalog.py`); module không import registry/numpy (API nhẹ).
"""

from __future__ import annotations

import copy
import json
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field

from ..actor import ActorContext
from ..errors import ServiceError
from ..proposal_response import proposal_response
from .pilot_db import DEFAULT_SAMPLE_SPEC, DecisionChange, DecisionInput, PilotDB, _In, canonical_hash, require_user
from .pilot_features import input_hash as feature_input_hash
from .pilot_runtime import ACTIVE, EnqueueSpec, JobDTO, PilotRuntime, _job_dto

PILOT_RECIPES = ("random", "uniform", "diversity", "text_diversity", "perception_instability", "context_uncertainty", "odd_coverage")
# Hướng tới (#97): cần dataset train + khai báo model↔dataset thật; catalog liệt kê nhưng `available=false`.
TRAINING_RECIPES = ("reference_diversity", "model_guided", "text_model_guided")
NEEDS: dict[str, tuple[str, ...]] = {
    "random": (), "uniform": (), "diversity": ("DINO",), "text_diversity": ("DINO", "CLIP"),
    "perception_instability": ("DINO",), "context_uncertainty": ("DINO", "CLIP"), "odd_coverage": ("DINO", "CLIP"),
}
CATALOG = Path(__file__).with_name("pilot_recipes.json")
MAX_CONFIG_BYTES = 32_768


@lru_cache(maxsize=1)
def catalog() -> list[dict]:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


class ProposeIn(_In):
    recipe: str = Field(min_length=1, max_length=100)
    config: dict[str, Any] | None = None
    strategy_params: dict[str, Any] | None = None
    batch_size: int = Field(16, ge=1, le=200)
    seed: int | None = Field(None, ge=0, lt=2**31)
    return_scores: bool = False
    expected_state_version: int = Field(ge=1)
    changes: list[DecisionChange] = Field(default_factory=list, max_length=500)
    model_id: UUID | str | None = None


class RetryIn(_In):
    pass


class ProposalV2DTO(BaseModel):
    id: UUID
    n: int
    round: int
    created_at: str
    job_id: UUID | None
    input: dict[str, Any]
    picks: list[dict[str, Any]]
    relaxed: bool
    explain: dict[str, Any] | None
    scores: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None
    uncertainty_summary: dict[str, Any] | None = None
    trace: list[dict[str, Any]] | None = None


def _types(cfg: dict) -> tuple:
    return (cfg.get("reference", {}).get("type"), cfg.get("combiner", {}).get("type"), cfg.get("selector", {}).get("type"),
            tuple(o.get("type") for o in cfg.get("objectives") or []))


def check_config(recipe: str, config: dict | None) -> None:
    """Kiểm nhẹ ở API: cùng loại component với recipe gốc; tham số chi tiết do worker kiểm bằng registry."""
    entry = next((r for r in catalog() if r["name"] == recipe), None)
    if entry and not entry["available"]:
        raise ServiceError("missing_model_training_provenance",
                           f"recipe {recipe!r} cần dataset train + model có khai báo train; pilot chưa có (Hướng tới)", 422)
    if entry is None:
        raise ServiceError("recipe_not_available", f"recipe {recipe!r} chưa dùng được ở pilot; có: {list(PILOT_RECIPES)}", 422)
    if config is None:
        return
    if len(json.dumps(config)) > MAX_CONFIG_BYTES:
        raise ServiceError("invalid_config", "config quá lớn", 422)
    if config.get("name") != recipe:
        raise ServiceError("invalid_config", "config.name phải trùng recipe", 422)
    if not entry.get("requires_model") and (config.get("features") or config.get("candidate_stage")):
        raise ServiceError("invalid_config", "recipe pilot không dùng features/candidate_stage", 422)
    try:
        same = _types(config) == _types(entry["config"])
    except AttributeError:
        same = False
    if not same:
        raise ServiceError("invalid_config", "config phải giữ đúng loại component của recipe", 422)


def resolve_strategy_params(entry, supplied, config=None):
    """Validate developer-defined public JSON Schema without importing worker/ML dependencies."""
    metadata = entry.get("strategy") or {}
    schema = metadata.get("parameters_schema", {})
    defaults = metadata.get("defaults", {})
    if supplied is None and config:
        objectives = [*(config.get("objectives") or []), *((config.get("candidate_stage") or {}).get("objectives") or [])]
        text = next((o for o in objectives if o.get("type") == "text_match"), None)
        if text:
            defaults = {**defaults, **{k: v for k, v in text.get("params", {}).items() if k in defaults}}
    values = {**defaults, **(supplied or {})}
    properties = schema.get("properties", {})
    try:
        encoded = json.dumps(values, allow_nan=False)
    except (ValueError, TypeError):
        raise ServiceError("invalid_strategy_params", "strategy parameters must be finite JSON", 422) from None
    if len(encoded) > MAX_CONFIG_BYTES or set(values) - set(properties):
        raise ServiceError("invalid_strategy_params", "unknown or oversized strategy parameters", 422)
    error = next(Draft202012Validator(schema).iter_errors(values), None)
    if error is not None:
        raise ServiceError("invalid_strategy_params", "strategy parameters do not match their schema", 422)
    # Nonblank query validation lives in Pydantic validators and is not expressed by the generated schema.
    for key in ("texts", "negative"):
        if key in values and (not isinstance(values[key], list) or
                              any(not isinstance(v, str) or not v.strip() or len(v) > 1000 for v in values[key])):
            raise ServiceError("invalid_strategy_params", f"invalid {key}", 422)
    return values


def _proposal_dto(row: dict, with_scores: bool) -> ProposalV2DTO:
    ev = row["evidence"] or {}
    units = ev.get("units") if isinstance(ev, dict) and "units" in ev else ev
    # Old interactive checkpoints have envelope metadata but no per-unit evidence.
    per_unit = {p["unit_id"]: units[p["unit_id"]] for p in row["picks"]
                if isinstance(units, dict) and isinstance(units.get(p["unit_id"]), list)}
    summary = ev.get("uncertainty_summary") if isinstance(ev, dict) else None
    return ProposalV2DTO.model_validate(proposal_response(dict(
        id=row["id"],
        n=row["n"],
        round=row["n"],
        created_at=row["created_at"].isoformat(),
        job_id=row.get("job_id"),
        input=row["frozen_input"],
        picks=row["picks"],
        relaxed=bool(ev.get("relaxed", False)),
        explain=ev.get("explain"),
        scores=ev.get("scores") if with_scores else None,
        evidence=per_unit,
        uncertainty_summary=summary,
        trace=ev.get("trace"),
    )))


def freeze_session_in_tx(db: PilotDB, runtime: PilotRuntime, cur, uid: UUID, session_id: UUID, expected_state_version: int,
                         kind: str, needs: tuple[str, ...] = ()) -> tuple[dict, list[UUID]]:
    """Khóa profile → nguồn → phiên (§21.3), kiểm CAS + giới hạn pool + một job `kind` đang chạy/phiên + đặc trưng cần có;
    trả (payload gốc đóng băng: pool + artifact + state_version/decision_seq/proposal_count, danh sách nguồn theo thứ tự pool)."""
    profile = db.lock_profile(cur, uid)
    cur.execute("SELECT id FROM rav_pilot_session WHERE id = %s AND owner_id = %s", (session_id, uid))
    if not cur.fetchone():
        raise ServiceError("not_found", f"không tìm thấy phiên {session_id}", 404)
    cur.execute("SELECT source_id FROM rav_pilot_session_source WHERE session_id = %s ORDER BY ord", (session_id,))
    ordered = [r["source_id"] for r in cur.fetchall()]
    sources = db.lock_sources(cur, uid, ordered)
    session = db.lock_own_session(cur, uid, session_id)
    if expected_state_version != session["state_version"]:
        raise ServiceError("state_version_conflict", f"state_version hiện là {session['state_version']}", 409)
    _, cfg = runtime._policy(cur, profile)
    max_units = ((cfg.get("chunk_bounds_by_kind") or {}).get("PROPOSE") or {}).get("max_units")
    if not isinstance(max_units, int) or max_units <= 0:
        raise ServiceError("policy_unconfigured", "policy thiếu chunk_bounds_by_kind.PROPOSE.max_units", 503)
    if session["n_units"] > max_units:
        raise ServiceError("pool_too_large", f"pool {session['n_units']} unit vượt {max_units}", 422)
    cur.execute("SELECT 1 FROM rav_pilot_job WHERE owner_id = %s AND kind = %s AND resource_id = %s AND state = ANY(%s)",
                (uid, kind, session_id, list(ACTIVE)))
    if cur.fetchone():
        code = "proposal_in_progress" if kind == "PROPOSE" else "analysis_in_progress"
        raise ServiceError(code, "phiên đang có một job cùng loại chạy; chờ xong hoặc hủy", 409)
    pool, missing = [], []
    for sid in ordered:
        s = sources[sid]
        if s["availability"] != "READY":
            raise ServiceError("source_unavailable", f"nguồn {sid} đang {s['availability']}", 409)
        feats = {}
        for feature in ("DINO", "CLIP"):   # bắt buộc theo `needs`; sẵn có thì đính kèm (giải thích/metric dùng DINO, pHash)
            cur.execute("SELECT id, object_key, sha256, size_bytes, manifest FROM rav_pilot_artifact WHERE kind = 'FEATURE' "
                        "AND state = 'READY' AND source_id = %s AND variant = %s AND input_hash = %s "
                        "AND (owner_id = %s OR scope = 'SYSTEM')", (sid, feature, feature_input_hash(s, feature), uid))
            art = cur.fetchone()
            if art is None:
                if feature in needs:
                    missing.append({"source_id": str(sid), "feature": feature})
                continue
            feats[feature] = {"artifact_id": str(art["id"]), "object_key": art["object_key"], "sha256": art["sha256"],
                              "size_bytes": art["size_bytes"], "field_keys": art["manifest"].get("field_keys", {})}
        pool.append({"source_id": str(sid), "core_source_id": s["core_source_id"], "raw_key": s["raw_key"],
                     "raw_sha256": s["raw_sha256"], "resource_version": s["resource_version"],
                     "n_units": (s["metadata"] or {}).get("n_units"), "features": feats,
                     "video_metadata": {k: (s['metadata'] or {}).get(k) for k in ('fps', 'n_frames', 'duration_ms')}})
    if missing:
        raise ServiceError("feature_not_ready", "pool chưa đủ đặc trưng; chuẩn bị DINO/CLIP trước", 409, {"missing": missing})
    cur.execute("SELECT coalesce(max(seq), 0) AS s FROM rav_pilot_decision WHERE session_id = %s", (session_id,))
    decision_seq = cur.fetchone()["s"]
    cur.execute("SELECT count(*) AS n FROM rav_pilot_proposal WHERE session_id = %s", (session_id,))
    proposal_count = cur.fetchone()["n"]
    base = {"session_id": str(session_id), "owner_id": str(uid), "goal": session["goal"], "target_k": session["target_k"],
            "spec": {k: DEFAULT_SAMPLE_SPEC[k] for k in ("decoder", "units")}, "spec_hash": DEFAULT_SAMPLE_SPEC["spec_hash"],
            "pool": pool, "state_version": session["state_version"], "decision_seq": decision_seq, "proposal_count": proposal_count}
    return base, ordered


class PilotCuration:
    def __init__(self, db: PilotDB, runtime: PilotRuntime | None = None):
        self.db, self.runtime = db, runtime or PilotRuntime(db)

    # ---- yêu cầu đề xuất ----
    def request_proposal(self, actor: ActorContext, session_id: UUID, inp: ProposeIn, idempotency_key: str) -> JobDTO:
        uid = require_user(actor)
        with self.db.backend() as cur:
            return self._request_in_tx(cur, uid, session_id, inp, f"propose:{idempotency_key}")

    def _request_in_tx(self, cur, uid: UUID, session_id: UUID, inp: ProposeIn, key: str, retry_of: UUID | None = None) -> JobDTO:
        self.db.lock_profile(cur, uid)
        cur.execute("SELECT * FROM rav_pilot_job WHERE owner_id = %s AND kind = 'PROPOSE' AND idempotency_key = %s", (uid, key))
        submission_hash = canonical_hash({"session_id": str(session_id), **inp.model_dump(mode="json")})
        if old := cur.fetchone():   # replay before CAS; never append/reserve twice after lost response
            if old["resource_id"] != session_id or (old["payload"].get("submission_hash") and
                                                    old["payload"]["submission_hash"] != submission_hash):
                raise ServiceError("idempotency_conflict", "key đã dùng cho một yêu cầu khác", 409)
            return _job_dto(old)
        check_config(inp.recipe, inp.config)
        entry = next(r for r in catalog() if r["name"] == inp.recipe)
        params = resolve_strategy_params(entry, inp.strategy_params, inp.config)
        base, ordered = freeze_session_in_tx(self.db, self.runtime, cur, uid, session_id, inp.expected_state_version, "PROPOSE",
                                             tuple(entry.get("needs", ())))
        if inp.changes:
            state = self.db.append_decisions_in_tx(cur, ActorContext(user_id=uid), session_id,
                                                   DecisionInput(changes=inp.changes, expected_state_version=inp.expected_state_version),
                                                   coalesce=True)
            base.update(state_version=state.state_version, decision_seq=state.decision_seq)

        model_info = None
        config = inp.config
        optional_model = (entry.get("strategy") or {}).get("inputs", {}).get("model_optional", False)
        if entry.get("requires_model") or (optional_model and inp.model_id):
            mid = inp.model_id
            if not mid and config and config.get("features", {}).get("det.yolo_consistency", {}).get("params", {}).get("model_id"):
                raw_mid = config["features"]["det.yolo_consistency"]["params"]["model_id"]
                if raw_mid != "$perception_model":
                    mid = raw_mid
            if not mid:
                cur.execute("SELECT model_id FROM rav_pilot_session_reference WHERE session_id = %s", (session_id,))
                ref_row = cur.fetchone()
                if ref_row and ref_row.get("model_id"):
                    mid = ref_row["model_id"]
            if not mid:
                raise ServiceError("model_required", "recipe perception_instability cần model_id được chỉ định", 422)
            try:
                model_uuid = UUID(str(mid))
                cur.execute("SELECT * FROM rav_pilot_model_asset WHERE id = %s", (model_uuid,))
            except (ValueError, TypeError):
                cur.execute("SELECT * FROM rav_pilot_model_asset WHERE core_model_id = %s", (str(mid),))
            model_row = cur.fetchone()
            if not model_row or not self.db.can_read_model_in_tx(cur, uid, model_row):
                raise ServiceError("model_not_found", f"không tìm thấy model {mid}", 404)
            if model_row["validation"] != "INFERENCE_VERIFIED":
                raise ServiceError("model_not_verified", f"model {mid} chưa được xác minh (INFERENCE_VERIFIED)", 422)
            if model_row["task"] != "detect":
                raise ServiceError("unsupported_yolo_model", "Perception instability cần model task detect", 422)
            model_info = {
                "id": str(model_row["id"]),
                "name": model_row["name"],
                "core_model_id": model_row["core_model_id"],
                "sha256": model_row["sha256"],
                "size_bytes": model_row["size_bytes"],
                "raw_key": model_row["raw_key"],
            }
            candidate_limit = max(32, min(96, inp.batch_size * 2))
            if config is None:
                entry = next((r for r in catalog() if r["name"] == inp.recipe), None)
                config = json.loads(json.dumps(entry["config"])) if entry else {}
            else:
                config = copy.deepcopy(config)
            if config is not None:  # Native strategies own orchestration; no legacy recipe is synthesized.
                if "features" not in config:
                    config["features"] = {}
                if "det.yolo_consistency" not in config["features"]:
                    config["features"]["det.yolo_consistency"] = {"type": "yolo_consistency", "params": {}}
                config["features"]["det.yolo_consistency"]["params"]["model_id"] = model_info["core_model_id"]
                if "candidate_stage" not in config or config["candidate_stage"] is None:
                    config["candidate_stage"] = {
                        "limit": candidate_limit,
                        "objectives": [{"type": "novelty", "strength": 1.0, "params": {"field": "emb.dinov2_s"}}],
                        "combiner": {"type": "product_strength"},
                        "selector": {"type": "greedy_nms", "params": {"field": "emb.dinov2_s", "nms_s": 1.0, "min_dist": 0.1}},
                    }
                else:
                    config["candidate_stage"]["limit"] = candidate_limit

        payload = {**base, "submission_hash": submission_hash, "recipe": inp.recipe, "config": config, "batch_size": inp.batch_size,
                   "seed": inp.seed if inp.seed is not None else secrets.randbelow(2**31), "return_scores": inp.return_scores}
        payload.update(strategy_params=params, strategy_version=entry["strategy"]["version"])
        if entry.get("config") is None:
            payload["config"] = None
        if model_info:
            payload["model"] = model_info
        job = self.runtime.enqueue_in_tx(cur, uid, EnqueueSpec(kind="PROPOSE", resource_id=session_id, idempotency_key=key,
                                                               payload=payload), ordered)
        if retry_of is not None:
            cur.execute("UPDATE rav_pilot_job SET retry_of = %s WHERE id = %s", (retry_of, job.id))
        return job

    # ---- đọc ----
    def list_proposals(self, actor: ActorContext, session_id: UUID) -> list[ProposalV2DTO]:
        uid = require_user(actor)
        with self.db.as_user(uid) as cur:
            cur.execute("SELECT 1 FROM rav_pilot_session WHERE id = %s", (session_id,))
            if not cur.fetchone():
                raise ServiceError("not_found", f"không tìm thấy phiên {session_id}", 404)
            cur.execute("SELECT * FROM rav_pilot_proposal WHERE session_id = %s ORDER BY n", (session_id,))
            return [_proposal_dto(r, False) for r in cur.fetchall()]

    def get_proposal(self, actor: ActorContext, session_id: UUID, n: int) -> ProposalV2DTO:
        uid = require_user(actor)
        with self.db.as_user(uid) as cur:
            cur.execute("SELECT * FROM rav_pilot_proposal WHERE session_id = %s AND n = %s", (session_id, n))
            row = cur.fetchone()
        if not row:
            raise ServiceError("not_found", f"phiên {session_id}: không có proposal {n}", 404)
        return _proposal_dto(row, True)

    # ---- job ----
    def job_result(self, actor: ActorContext, job_id: UUID) -> dict:
        uid = require_user(actor)
        with self.db.backend() as cur:
            self.db.read_profile(cur, uid)
            cur.execute("SELECT * FROM rav_pilot_job WHERE id = %s AND owner_id = %s", (job_id, uid))
            job = cur.fetchone()
            if not job:
                raise ServiceError("not_found", f"không tìm thấy job {job_id}", 404)
            if job["state"] != "SUCCEEDED":
                raise ServiceError("job_not_succeeded", f"job đang {job['state']}", 409)
            if job["kind"] == "PROPOSE":
                cur.execute("SELECT n FROM rav_pilot_proposal WHERE job_id = %s", (job_id,))
                row = cur.fetchone()
                return {"kind": "PROPOSE", "session_id": job["payload"]["session_id"], "proposal_n": row["n"] if row else None}
            if job["kind"] in ("PREPARE_DINO", "PREPARE_CLIP"):
                return {"kind": job["kind"], "source_id": job["payload"]["source_id"], "feature": job["payload"]["feature"],
                        "ready": job["result_ref"] is not None}
            if job["kind"] == "YOLO_BATCH":
                return {"kind": "YOLO_BATCH", "session_id": job["payload"]["session_id"], "proposal_n": job["payload"]["proposal_n"],
                        "model_id": job["payload"]["model"]["id"]}
            if job["kind"] == "METRICS":
                cur.execute("SELECT id FROM rav_pilot_analysis WHERE job_id = %s", (job_id,))
                row = cur.fetchone()
                return {"kind": "METRICS", "session_id": job["payload"]["session_id"], "analysis_id": str(row["id"]) if row else None,
                        "input_hash": job["payload"]["input_hash"]}
            return {"kind": job["kind"], "result_ref": str(job["result_ref"]) if job["result_ref"] else None}

    def retry(self, actor: ActorContext, job_id: UUID, idempotency_key: str) -> JobDTO:
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
            if job["state"] not in ("FAILED", "CANCELLED"):
                raise ServiceError("job_not_retryable", f"job đang {job['state']}; chỉ thử lại job FAILED/CANCELLED", 409)
            if job['payload'].get('sample_copy'):
                raise ServiceError('sample_copy_retired', 'Dùng Thêm vào kho mẫu để tham chiếu chỉ đọc.', 410)
            key = f"retry:{idempotency_key}"
            if job["kind"] == "PROPOSE":   # đóng băng lại theo state hiện tại, cùng recipe/config/batch/seed
                p = job["payload"]
                cur.execute("SELECT state_version FROM rav_pilot_session WHERE id = %s", (UUID(p["session_id"]),))
                inp = ProposeIn(recipe=p["recipe"], config=p["config"], strategy_params=p.get("strategy_params"), batch_size=p["batch_size"], seed=p["seed"],
                                return_scores=p["return_scores"], expected_state_version=cur.fetchone()["state_version"],
                                model_id=(p.get('model') or {}).get('id'))
                return self._request_in_tx(cur, uid, UUID(p["session_id"]), inp, key, retry_of=job_id)
            if job["kind"] in ("PREPARE_FRAMES", "PREPARE_DINO", "PREPARE_CLIP"):
                new_payload = {**job["payload"], "owner_id": str(uid)}
                new = self.runtime.enqueue_in_tx(cur, uid, EnqueueSpec(kind=job["kind"], resource_id=job["resource_id"],
                                                                       idempotency_key=key, payload=new_payload),
                                                 [job["resource_id"]])
                cur.execute("UPDATE rav_pilot_job SET retry_of = %s WHERE id = %s AND retry_of IS NULL", (job_id, new.id))
                return new
            raise ServiceError("retry_unsupported", f"job {job['kind']} không thử lại qua route này", 422)


# ------------------------------------------------------------------------------
# Hook runtime (cùng transaction publish/finish)
# ------------------------------------------------------------------------------
def prepare_publication(p: dict, outputs: dict) -> dict:
    """Pure proposal normalization/hash for legacy hook and server publication; no DB/ML."""
    out = outputs.get("proposal") or {}
    cores = {s["core_source_id"]: s["source_id"] for s in p["pool"]}
    picks = out.get("picks")
    if (out.get("n") != p["proposal_count"] + 1 or not isinstance(picks, list) or len(picks) > p["batch_size"]
            or any(not isinstance(k, dict) or str(k.get("unit_id", "")).rsplit(":", 1)[0] not in cores for k in picks)
            or not isinstance(out.get("input"), dict)):
        raise ServiceError("result_invalid", "kết quả đề xuất không khớp payload đóng băng", 422)
    frozen = {**out["input"], "decision_seq": p["decision_seq"], "state_version": p["state_version"]}
    return {"n": out["n"], "frozen": frozen, "hash": canonical_hash(frozen),
            "picks": [{**k, "source_id": cores[k["unit_id"].rsplit(":", 1)[0]]} for k in picks],
            "evidence": {**(out.get("evidence") or {}), "relaxed": bool(out.get("relaxed"))},
            "fields_used": out.get("fields_used") or []}


def publish_in_tx(cur, job: dict, lease, outputs: dict) -> None:
    """outputs["proposal"] = {n, input, picks, relaxed, fields_used, evidence}; ghi proposal, không đổi state_version."""
    from psycopg.types.json import Jsonb

    p = job["payload"]
    sid = UUID(p["session_id"])
    cur.execute("SELECT s.state_version, (SELECT coalesce(max(seq),0) FROM rav_pilot_decision WHERE session_id=s.id) AS seq, "
                "(SELECT count(*) FROM rav_pilot_proposal WHERE session_id=s.id) AS n "
                "FROM rav_pilot_session s WHERE s.id=%s FOR UPDATE OF s", (sid,))
    session = cur.fetchone()
    seq, count = session["seq"], session["n"]
    if (session["state_version"], seq, count) != (p["state_version"], p["decision_seq"], p["proposal_count"]):
        raise ServiceError("proposal_context_changed", "phiên đã đổi trong lúc đề xuất chạy; tải lại rồi đề xuất lại", 409)
    record = prepare_publication(p, outputs)
    cur.execute("INSERT INTO rav_pilot_proposal (id, session_id, owner_id, n, frozen_input, input_hash, picks, evidence, fields_used, "
                "state_version, job_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (uuid4(), sid, job["owner_id"], record["n"], Jsonb(record["frozen"]), record["hash"], Jsonb(record["picks"]),
                 Jsonb(record["evidence"]), Jsonb(record["fields_used"]),
                 p["state_version"], job["id"]))
    return None


def finish_in_tx(cur, job: dict, state: str, error: dict | None) -> None:
    """Đề xuất lỗi/hủy không để lại bản ghi phụ."""
