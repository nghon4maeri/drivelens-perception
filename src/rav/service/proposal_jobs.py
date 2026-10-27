"""Persist declarative proposal jobs; one worker, frozen input and atomic publication (§18).

Commands queue/list jobs; stages call component pipelines only. Restart interrupts unfinished jobs, never auto-runs them.
"""
import json
import uuid
from datetime import UTC, datetime

from ..core.types import RecipeConfig, ReviewState
from ..pipeline.propose import propose, select_candidates
from ..pipeline.remote_embed import source_hashes
from . import ServiceError
from . import proposal as svc
from .session import get_session


def _write(app, job):
    path = app.output_dir / "proposal_jobs" / (job["id"] + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_text(json.dumps(job, allow_nan=False), encoding="utf-8")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _load(app):
    if hasattr(app, "proposal_jobs"):
        return app.proposal_jobs
    jobs = {}
    for path in (app.output_dir / "proposal_jobs").glob("*.json"):
        job = json.loads(path.read_text(encoding="utf-8"))
        if job["state"] in ("queued", "running"):
            job.update(state="error", error="API đã restart; job bị ngắt. Bấm Đợt mới để thử lại.")
            _write(app, job)
        jobs[job["id"]] = job
    app.proposal_jobs = jobs
    return jobs


def public(job):
    return {k: job[k] for k in ("id", "session_id", "state", "stage", "done", "total", "error", "proposal_n", "created_at")}


def list_jobs(app, sid):
    get_session(app, sid)
    with app.proposal_lock:
        return [public(j) for j in sorted(_load(app).values(), key=lambda x: x["created_at"])
                if j["session_id"] == sid]


def signature(app, sid):
    row = get_session(app, sid)
    state = svc.get_state(app, sid)
    return {"spec": row["spec"], "kept_ids": sorted(state.kept), "dropped_ids": sorted(state.dropped),
            "decision_count": len(app.db.decisions(sid)), "proposal_count": len(app.db.proposals(sid))}


def metadata(app, sid, ctx, candidate_ids):
    row = get_session(app, sid)
    versions = row["spec"].get("reference", {}).get("dataset_version_ids", []) if ctx.reference_sets else []
    from . import dataset
    return {"candidate_ids": candidate_ids, "reference_version_ids": versions,
            "reference_manifest_hashes": {v: dataset.get_version(app, v)["manifest_hash"] for v in versions},
            "model_ids": sorted(ctx.model_files), "source_hashes": source_hashes(ctx.units, ctx)}


def queue(app, sid, recipe, config, batch_size, seed, *, strategy_params=None, model_id=None):
    with app.proposal_lock:
        jobs = _load(app)
        active = next((j for j in jobs.values() if j["session_id"] == sid and j["state"] in ("queued", "running")), None)
        if active:
            return public(active)
        from ..core.registry import REGISTRY, discover
        discover()
        strategy = REGISTRY.strategy(recipe) if recipe in REGISTRY.strategy_names() else None
        native = strategy is not None and not hasattr(strategy, "recipe")
        if native:
            if config is not None:
                raise ServiceError("invalid_config", "Native strategy không nhận recipe config.", 422)
            params = strategy.Params.model_validate(strategy_params or {})
            cfg = None
            ctx = strategy_context(app, sid, strategy, model_id)
        else:
            cfg = svc.bind_recipe(app, sid, svc.resolve_recipe(recipe, config), model_id)
            if strategy_params is not None and strategy is not None:
                cfg = strategy.resolve_config(cfg, strategy.Params.model_validate(strategy_params))
            ctx = svc.session_context(app, sid, cfg)
        snap = signature(app, sid)
        job = {"id": uuid.uuid4().hex, "session_id": sid, "state": "queued", "stage": "Chờ worker",
               "done": 0, "total": 0, "error": None, "proposal_n": None, "created_at": datetime.now(UTC).isoformat(),
               "config": cfg.model_dump(mode="json") if cfg else None, "batch_size": batch_size, "seed": seed,
               "snapshot": snap, "execution": metadata(app, sid, ctx, [])}
        if native:
            job.update(strategy=recipe, strategy_params=params.model_dump(mode="json"), strategy_version=strategy.version,
                       model_id=getattr(ctx, "strategy_model_id", None))
        _write(app, job)
        jobs[job["id"]] = job
        # local-only: persisted status, one API prepare worker; no automatic resume after restart.
        app.prepare_queue.put(lambda: _run(app, job))
        return public(job)


def _run(app, job):
    sid = job["session_id"]
    def progress(stage, done=0, total=0):
        with app.proposal_lock:
            job.update(stage=stage, done=done, total=total)
            _write(app, job)
    try:
        with app.proposal_lock:
            job["state"] = "running"
            _write(app, job)
            if signature(app, sid) != job["snapshot"]:
                raise ServiceError("proposal_context_changed", "Phiên đã đổi từ lúc xếp job; thử đề xuất lại.", 409)
        cfg = RecipeConfig.model_validate(job["config"]) if job["config"] is not None else None
        snap = job["snapshot"]
        state = ReviewState(set(snap["kept_ids"]), set(snap["dropped_ids"]))
        strategy = None
        if cfg is None:
            from ..core.registry import REGISTRY
            strategy = REGISTRY.strategy(job["strategy"])
            if strategy.version != job["strategy_version"]:
                raise ServiceError("proposal_config_changed", "Strategy khác phiên bản snapshot.", 409)
            ctx = strategy_context(app, sid, strategy, job.get("model_id"), state)
        else:
            ctx = svc.session_context(app, sid, cfg, state)
        if metadata(app, sid, ctx, []) != job["execution"]:
            raise ServiceError("proposal_source_changed", "Nguồn đã đổi từ lúc xếp job.", 409)
        ctx._on_progress = progress
        progress("Chọn ứng viên")
        result = None
        if strategy is not None:
            result = strategy.run(ctx, strategy.Params.model_validate(job["strategy_params"]), job["batch_size"], job["seed"])
            prop = result.proposal
            ids = result.scores.get("unit_id", [p.unit.id for p in prop.picks])
        else:
            ids = select_candidates(cfg, job["seed"], ctx) if cfg.candidate_stage else [u.id for u in ctx.units
                    if u.id not in ctx.state.kept | ctx.state.dropped]
            ctx.evidence.clear()
            progress("Chấm ứng viên", 0, len(ids))
            prop = propose(cfg, job["batch_size"], job["seed"], ctx, ids)
        execution = metadata(app, sid, ctx, ids)
        if {**execution, "candidate_ids": []} != job["execution"]:
            raise ServiceError("proposal_source_changed", "Nguồn đổi trong khi tính; không lưu đề xuất.", 409)
        with app.proposal_lock:
            if signature(app, sid) != snap:
                raise ServiceError("proposal_context_changed", "Bạn đã thay quyết định/pool/đợt trong khi chạy; thử đề xuất lại.", 409)
            checked = strategy_context(app, sid, strategy, job.get("model_id")) if strategy else svc.session_context(app, sid, cfg)
            if metadata(app, sid, checked, ids) != execution:
                raise ServiceError("proposal_source_changed", "Nguồn đã đổi trong lúc chạy; không lưu đề xuất.", 409)
            if result is None:
                rec = svc.save_proposal(app, sid, cfg, job["batch_size"], job["seed"], ctx, prop, execution)
            else:
                rec = svc.proposal_record(None, job["batch_size"], job["seed"], ctx, prop, execution)
                rec["input"].update(strategy_params=job["strategy_params"], strategy_version=job["strategy_version"],
                                    model_id=job.get("model_id"))
                rec.update(scores=result.scores, strategy_trace=result.trace)
                json.dumps(rec, allow_nan=False)
                app.db.add_proposal(sid, rec)
            job.update(state="done", stage="Đã lưu đề xuất", done=len(ids), total=len(ids), proposal_n=rec["n"])
            _write(app, job)
    except Exception as exc:
        with app.proposal_lock:
            message = str(exc) if isinstance(exc, (ValueError, ServiceError)) else "Job đề xuất lỗi; kiểm model/dataset/dependencies rồi thử lại."
            job.update(state="error", error=message)
            _write(app, job)


def strategy_context(app, sid, strategy, model_id=None, state=None):
    """Provision local resources before calling a native strategy, preserving source/model identity checks."""
    from . import reference_model
    ctx = svc.session_context(app, sid, state=state, reference_required=strategy.reference_dataset)
    if "DINO" in strategy.needs:
        ctx.cache_only_fields.add("emb.dinov2_s")
    if strategy.model or (strategy.model_optional and model_id):
        mid = model_id or get_session(app, sid)["spec"].get("reference", {}).get("model_id")
        if not mid:
            raise ServiceError("reference_model_required", "Chọn model cho strategy.", 422)
        reference_model.verify_model(app, mid)
        ctx.strategy_model_id = mid
        ctx.model_files[mid] = app.models_dir / (mid + ".pt")
    if "CLIP" in strategy.needs:
        from .clip_prepare import prepared
        if any(not prepared(app, video) for video in ctx.videos.values()):
            raise ServiceError("clip_not_prepared", "Chuẩn bị CLIP trước khi chạy strategy.", 409)
    return ctx
