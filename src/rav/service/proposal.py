"""Dựng Context từ DB (state = replay log, `proposals` cũ), gọi `pipeline.propose`, lưu Proposal kèm snapshot input (architecture §3, §5.3; #45, #46).

Bản ghi trả về / lưu có dạng `ProposalDTO` (architecture §5.3): `{n, round, created_at, input, picks, relaxed}`;
`input` = snapshot đúng input đã chạy: `recipe`, `config` (RecipeConfig đầy đủ), `config_hash`, `kept_ids`, `dropped_ids`,
`batch_size`, `seed`, `fields_used` (FieldKey đầy đủ, để dựng lại `Proposal` cho `ctx.proposals`).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from pydantic import ValidationError

from ..core.context import Context
from ..core.registry import REGISTRY, discover
from ..core.types import FieldKey, Pick, Proposal, RecipeConfig, ReviewState, Unit
from ..pipeline.propose import config_hash, get_score_candidates, propose
from . import App, ServiceError, publication_guard
from . import reference_model as model_svc
from .review_state import get_state
from .session import get_session, make_context


def resolve_recipe(recipe: str, config: RecipeConfig | None) -> RecipeConfig:
    """`config` đầy đủ nếu client gửi (`config.name` phải = `recipe`), không thì recipe có sẵn; validate Params mọi component (#45)."""
    discover()
    if config is None:
        try:
            config = REGISTRY.recipe(recipe)
        except KeyError as e:
            raise ServiceError("recipe_not_found", str(e.args[0]), 404) from None
    elif config.name != recipe:
        raise ServiceError("recipe_mismatch", f"config.name {config.name!r} khác recipe {recipe!r}")
    try:
        config_hash(config)            # resolve từng component: sai type / tham số → lỗi ở đây, trước khi chạy
    except (KeyError, ValueError, ValidationError) as e:
        raise ServiceError("invalid_config", str(e)) from None
    return config


def session_context(app: App, sid: str, config=None, state=None, *, reference_required=False) -> Context:
    """Context của phiên dựng từ DB: pool, state = replay log, `proposals` cũ (#46) — cho propose, view, metric."""
    row = get_session(app, sid)
    include = reference_required or config is not None and getattr(REGISTRY.get("reference", config.reference.type), "needs_reference_sets", False)
    models = {}
    if config:
        for binding in config.features.values():
            model_id = binding.params.get("model_id")
            if model_id:
                model_svc.verify_model(app, model_id)
                models[model_id] = app.models_dir / (model_id + ".pt")
    ctx = make_context(app, row, include_reference=include, state=state or get_state(app, sid),
                       feature_configs=config.features if config else {}, model_files=models,
                       output_dir=app.output_dir, model_lock=app.batch_yolo_lock)
    ctx.proposals = to_proposals(sid, app.db.proposals(sid), ctx.units)
    ctx.cache_only_fields.add("emb.clip_b32")  # CLIP image requires explicit prepare, including background proposals.
    return ctx


def bind_recipe(app, sid, config, model_id=None):
    """Resolve declared model resource tokens before hashing, independent of recipe name."""
    config = config.model_copy(deep=True)
    for binding in config.features.values():
        if str(binding.params.get("model_id", "")).startswith("$"):
            mid = model_id or get_session(app, sid)["spec"].get("reference", {}).get("model_id")
            if not mid:
                raise ServiceError("reference_model_required", "Recipe cần YOLO tham chiếu; tạo phiên mới chọn model và dataset train.")
            binding.params["model_id"] = mid
        cls = REGISTRY.get("feature", binding.type)
        binding.params = cls.Params.model_validate(binding.params).model_dump(mode="json")
    config_hash(config)
    return config


@publication_guard
def create_proposal(app: App, sid: str, recipe: str, config: RecipeConfig | None, batch_size: int, seed: int,
                    *, strategy_params=None, model_id=None) -> dict:
    discover()
    strategy = REGISTRY.strategy(recipe) if recipe in REGISTRY.strategy_names() else None
    if strategy is not None and not hasattr(strategy, "recipe"):
        raise ServiceError("proposal_requires_job", "Strategy này cần job nền.", 409)
    if strategy_params is not None and strategy is not None:
        params = strategy.Params.model_validate(strategy_params)
        config = strategy.resolve_config(resolve_recipe(recipe, config), params)
    config = bind_recipe(app, sid, resolve_recipe(recipe, config), model_id)
    if config.candidate_stage is not None:
        raise ServiceError("proposal_requires_job", "Recipe hai bước cần proposal job nền.", 409)
    ctx = session_context(app, sid, config)
    if any(o.type == "text_match" for o in config.objectives):
        from .clip_prepare import prepared
        if any(not prepared(app, video) for video in ctx.videos.values()):
            raise ServiceError("clip_not_prepared", "Video chưa có CLIP. Bấm Chuẩn bị CLIP và chờ xong rồi đề xuất.", 409)
    execution = None
    if ctx.reference_sets or config.features:
        from .proposal_jobs import metadata
        ids = [u.id for u in ctx.units if u.id not in ctx.state.kept | ctx.state.dropped]
        execution = metadata(app, sid, ctx, ids)
    try:
        prop = propose(config, batch_size, seed, ctx)
    except ValueError as exc:
        raise ServiceError("invalid_query", str(exc)) from None
    except OSError:
        raise ServiceError("clip_model_unavailable", "Không nạp được text model CLIP; kiểm checkpoint/cache/kết nối.", 503) from None
    if execution is not None:
        checked = session_context(app, sid, config)
        if metadata(app, sid, checked, ids) != execution:
            raise ServiceError("proposal_source_changed", "Nguồn đổi trong khi tính; không lưu đề xuất.", 409)
    return save_proposal(app, sid, config, batch_size, seed, ctx, prop, execution)


def save_proposal(app, sid, config, batch_size, seed, ctx, prop, execution=None):
    """Publish prepared result and finite evidence; caller holds publication lock."""
    record = proposal_record(config, batch_size, seed, ctx, prop, execution)
    app.db.add_proposal(sid, record)
    return record


def proposal_record(config, batch_size, seed, ctx, prop, execution=None) -> dict:
    """Bản ghi ProposalDTO từ kết quả `propose` (thuần; dùng chung local và worker pilot §21.15). Evidence phải hữu hạn."""
    state = ctx.state
    record = {"n": prop.n, "round": prop.round, "created_at": datetime.now(UTC).isoformat(),
              "input": {"recipe": prop.recipe, "config": config.model_dump(mode="json") if config else None, "config_hash": prop.config_hash,
                        "kept_ids": sorted(state.kept), "dropped_ids": sorted(state.dropped),
                        "batch_size": batch_size, "seed": seed, "fields_used": [asdict(k) for k in prop.fields_used]},
              "picks": [{"unit_id": p.unit.id, "rank": p.rank, "combined": p.combined, "breakdown": p.breakdown}
                        for p in prop.picks],
              "relaxed": prop.relaxed}
    if execution is not None:
        record["input"]["execution"] = execution
    record["evidence"] = {p.unit.id: ctx.evidence.get(p.unit.id, []) for p in prop.picks}
    import json
    json.dumps(record, allow_nan=False)   # reject non-finite evidence before DB publication
    return record


def list_proposals(app: App, sid: str) -> list[dict]:
    get_session(app, sid)
    return app.db.proposals(sid)


def scores(app: App, sid: str, record: dict) -> dict[str, list]:
    """Điểm của mọi candidate trong vòng của `record`, dựng lại ctx **chỉ từ snapshot** (không lưu, CONTRACTS §7, #48).
    Dạng cột: `{"unit_id": [...], "combined": [...], <objective>: [...]}`."""
    snap = record["input"]
    if snap.get("config") is None:
        # Native strategies persist score columns; reading a batch never repeats inference.
        return record.get("scores") or {}
    config = RecipeConfig.model_validate(snap["config"])
    ctx = session_context(app, sid, config, ReviewState(set(snap["kept_ids"]), set(snap["dropped_ids"])))
    if config_hash(config) != snap["config_hash"] or any(ctx._feature(k["name"]).key != FieldKey(**k) for k in snap["fields_used"]):
        raise ServiceError("proposal_config_changed", "Component/feature khác snapshot; không tính lại điểm cũ.", 409)
    ctx.cache_only_fields |= {name for name in config.features if ctx._feature(name).cache_scope == "requested"}
    if snap.get("execution"):
        from .proposal_jobs import metadata
        if metadata(app, sid, ctx, snap["execution"]["candidate_ids"]) != snap["execution"]:
            raise ServiceError("proposal_source_changed", "Nguồn snapshot khác hiện tại; không tính lại điểm.", 409)
    try:
        candidates, objective_scores, combined, _ = get_score_candidates(config, ctx, snap.get("execution", {}).get("candidate_ids"))
    except ValueError as exc:
        raise ServiceError("proposal_cache_invalid", str(exc), 409) from None
    return {"unit_id": [u.id for u in candidates], "combined": [float(x) for x in combined],
            **{s.name: [float(x) for x in s.values] for s in objective_scores}}


def to_proposals(sid: str, records: list[dict], units: list[Unit]) -> list[Proposal]:
    """Bản ghi DB → `Proposal` cho `ctx.proposals` (metric keep_rate_by_recipe, view timeline)."""
    by_id = {u.id: u for u in units}
    return [Proposal(sid, r["n"], r["round"], r["input"]["recipe"], r["input"]["config_hash"],
                     [Pick(by_id[p["unit_id"]], p["combined"], p["breakdown"], p["rank"]) for p in r["picks"]],
                     r["relaxed"], [FieldKey(**k) for k in r["input"]["fields_used"]]) for r in records]
