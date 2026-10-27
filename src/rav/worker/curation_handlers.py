"""Handler curation của worker pilot: `PROPOSE` (CONTRACTS §21.15, #95).

Dựng lại đúng ngữ cảnh đã đóng băng lúc enqueue (`session_context.build_context`: raw theo SHA, đặc trưng chỉ từ artifact,
replay quyết định/đề xuất cũ) rồi gọi code core như local: `pipeline/propose.py` → bản ghi như `service/proposal.proposal_record`
+ giải thích đợt (`service/explain.explain_record`). Kết quả trả cho publish (runtime kiểm ngữ cảnh còn khớp trong transaction).
Chạy trong tiến trình worker (numpy, PyAV, registry).
"""

from __future__ import annotations

import json
import logging
import math
import threading
from pathlib import Path
from typing import Any

from ..errors import ServiceError
from ..store.pilot_db import PilotDB
from ..store.pilot_runtime import AttemptLease, PilotRuntime, Progress
from ..store.pilot_storage import PilotStorage
from .proposal_progress import ProposalProgress
from .session_context import ArtifactFieldStore, build_context, policy_of

log = logging.getLogger(__name__)
__all__ = ["ArtifactFieldStore", "make_curation_handlers"]


def finite(value):
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, list):
        return [finite(v) for v in value]
    if isinstance(value, dict):
        return {k: finite(v) for k, v in value.items()}
    return value


def make_curation_handlers(storage: PilotStorage, db: PilotDB, workdir: Path) -> dict[str, Any]:
    workdir.mkdir(parents=True, exist_ok=True)
    runtime = PilotRuntime(db)

    def execute(lease: AttemptLease, stop: threading.Event, progress: ProposalProgress) -> dict:
        from pydantic import ValidationError

        from ..core.registry import REGISTRY, discover
        from ..core.strategy import LegacyStrategy
        from ..core.types import RecipeConfig
        from ..pipeline.propose import config_hash
        from ..service.explain import explain_record
        from ..service.proposal import proposal_record
        from ..store.pilot_curation import catalog, resolve_strategy_params

        p = lease.payload
        progress.phase('VALIDATE_CONTEXT')
        discover()
        try:
            strategy = REGISTRY.strategy(p["recipe"])
            if p.get("strategy_version", strategy.version) != strategy.version:
                raise ValueError("strategy version differs from frozen snapshot")
            entry = next(r for r in catalog() if r["name"] == p["recipe"])
            params = strategy.Params.model_validate(resolve_strategy_params(entry, p.get("strategy_params"), p.get("config")))
            config = RecipeConfig.model_validate(p["config"]) if p.get("config") else None
            if config is None and p["recipe"] in REGISTRY.recipe_names():
                config = REGISTRY.recipe(p["recipe"])
            if strategy.reference_dataset:
                raise ValueError("training provenance unavailable")
            if isinstance(strategy, LegacyStrategy):
                config = strategy.resolve_config(config, params)
            if config is not None:
                config_hash(config)
        except (KeyError, ValueError, ValidationError) as exc:
            raise ServiceError("invalid_config", str(exc)[:300], 422) from None
        ctx = build_context(storage, db, p, workdir, policy_of(db, lease.job_id)["max_video_bytes"],
                            stop=stop, progress=progress.phase)
        ctx.cancel_event = stop
        if stop.is_set():
            return {}

        if p.get("model"):
            from .session_context import cached

            m = p["model"]
            progress.phase('LOAD_MODEL')
            model_path = cached(storage, workdir / "model-cache", m["raw_key"], m["sha256"], max(m["size_bytes"], 100 << 20), ".pt", stop=stop)
            ctx.model_files = getattr(ctx, "model_files", {})
            ctx.model_files[m["core_model_id"]] = model_path
            ctx.strategy_model_id = m["core_model_id"]
            ctx.model_lock = threading.Lock()
            ctx.output_dir = workdir

        ctx._on_progress = progress.phase
        progress.phase('STRATEGY')
        try:
            result = strategy.run(ctx, params, p["batch_size"], p["seed"], config=config)
            prop = result.proposal
        except ValueError as exc:
            raise ServiceError("invalid_query", str(exc)[:300], 422) from None
        except OSError:
            raise ServiceError("clip_model_unavailable", "không nạp được text model CLIP trên worker", 503) from None
        record = proposal_record(config, p["batch_size"], p["seed"], ctx, prop)
        if p.get("model"):
            record["input"].update(model_id=p["model"]["id"], model=p["model"])
        if p.get("strategy_version"):
            record["input"]["strategy_identity"] = {"name": strategy.name, "version": strategy.version, "model_sha256": (p.get("model") or {}).get("sha256")}
        record["input"].update(strategy_params=params.model_dump(mode="json"), strategy_version=strategy.version)
        progress.phase('EXPLAIN')
        try:
            explain = explain_record({"n": prop.n, "input": record["input"], "picks": record["picks"],
                                      "relaxed": record["relaxed"]}, p.get("goal", ""), ctx)
        except (ValueError, ServiceError):   # pool chưa có DINO và đã có frame giữ → không so được với frame đã giữ
            explain = None
        evidence: dict[str, Any] = {"explain": explain, "units": record["evidence"], "trace": result.trace}
        if strategy.model or p.get("model"):
            candidate_ids = result.scores.get("unit_id", [])
            check_units = [u for u in ctx.units if u.id in set(candidate_ids)]
            signaled_count = 0
            for u in check_units:
                for ev in ctx.evidence.get(u.id, []):
                    if ev.get("label") == "YOLO thiếu ổn định" and ev.get("observed"):
                        signaled_count += 1
            if signaled_count < p["batch_size"] or len(prop.picks) < p["batch_size"]:
                evidence["uncertainty_summary"] = {
                    "signaled_count": signaled_count,
                    "total_candidates": len(check_units),
                    "note": f"Chỉ tìm thấy {signaled_count} frame có phát hiện vật thể để đo độ ổn định; đề xuất {len(prop.picks)} frame (các frame còn lại chưa có tín hiệu).",
                }
        if p["return_scores"]:
            evidence["scores"] = result.scores
        evidence = finite(evidence)
        json.dumps(evidence, allow_nan=False)
        return {"proposal": {"n": prop.n, "input": record["input"], "picks": record["picks"], "relaxed": record["relaxed"],
                             "fields_used": record["input"]["fields_used"], "evidence": evidence}, "cleanup_done": True}

    def propose_handler(lease, stop):
        with ProposalProgress(runtime, lease, stop) as progress:
            outputs = execute(lease, stop, progress)
        if stop.is_set():
            return outputs
        runtime.update_progress(lease, Progress(sequence=progress.sequence + 1, phase='PUBLISH', done=0, total=0,
                                                stages=progress.stages))
        return outputs

    return {"PROPOSE": propose_handler}
