"""Giải thích cấp đợt cho UI (#61, CONTRACTS §11.2 `explain_proposal`): chỉ dữ liệu thật của đề xuất đã lưu.

- Vì sao đợt này cho mục tiêu phiên: recipe + docstring component; có objective nào đọc `goal` không (`Objective.uses_goal`).
- Bổ sung gì so với tập đã giữ: với từng pick, độ tương đồng cos tới frame **đã giữ lúc đề xuất** gần nhất, đo bằng embedding recipe đã dùng
  (nếu recipe không đọc embedding thì đo bằng `emb.dinov2_s` và ghi rõ); số video, khoảng thời gian trải ra.
"""

from __future__ import annotations

import numpy as np

from ..core.registry import REGISTRY, discover
from ..core.types import RecipeConfig
from . import App, ServiceError
from .session import get_session, make_context

DEFAULT_FIELD = "emb.dinov2_s"


def explain_proposal(app: App, sid: str, n: int) -> dict:
    discover()
    row = get_session(app, sid)
    rec = next((r for r in app.db.proposals(sid) if r["n"] == n), None)
    if rec is None:
        raise ServiceError("proposal_not_found", f"phiên {sid}: không có proposal {n}", 404)
    return explain_record(rec, row["spec"]["goal"], make_context(app, row))


def explain_record(rec: dict, goal: str, ctx) -> dict:
    """Thuần trên bản ghi proposal + Context của pool (dùng chung local và worker pilot §21.15)."""
    discover()
    n = rec["n"]
    inp = rec["input"]
    if inp.get("config") is None:
        strategy = REGISTRY.strategy(inp["recipe"])
        return {"n": n, "recipe": strategy.name, "description": strategy.__doc__, "goal": goal, "uses_goal": False,
                "components": [], "semantic_query": inp.get("strategy_params"), "n_picks": len(rec["picks"]),
                "n_kept_at_proposal": len(inp["kept_ids"]), "relaxed": rec["relaxed"], "nearest_kept": [], "spread": [],
                "similarity_field": "emb.dinov2_s", "field_used_by_recipe": "DINO" in strategy.needs}
    cfg = RecipeConfig.model_validate(inp["config"])
    objectives = [*cfg.objectives, *(cfg.candidate_stage.objectives if cfg.candidate_stage else [])]
    parts = [("reference", cfg.reference.type), *[("objective", o.type) for o in objectives],
             ("combiner", cfg.combiner.type), ("selector", cfg.selector.type)]
    parts = list(dict.fromkeys(parts))
    components = [{"role": role, "type": t, "doc": (REGISTRY.get(role, t).__doc__ or "").strip()} for role, t in parts]
    uses_goal = any(getattr(REGISTRY.get("objective", o.type), "uses_goal", False) for o in objectives)

    by_id = {u.id: u for u in ctx.units}
    picks = [by_id[p["unit_id"]] for p in rec["picks"] if p["unit_id"] in by_id]
    kept = [by_id[i] for i in inp["kept_ids"] if i in by_id]
    used = [k["name"] for k in inp["fields_used"] if k["name"].startswith("emb.")]
    selector_field = cfg.selector.params.get("field", DEFAULT_FIELD)
    field = selector_field if selector_field in used else used[0] if used else DEFAULT_FIELD
    text = next((o for o in objectives if o.type == "text_match"), None)
    semantic_query = REGISTRY.build("objective", text).params.model_dump() if text else None

    nearest = []
    if picks and kept:
        x = np.asarray(ctx.field(field, picks + kept).values, np.float64)
        x /= np.maximum(np.linalg.norm(x, axis=1, keepdims=True), 1e-12)
        sim = x[:len(picks)] @ x[len(picks):].T
        best = sim.argmax(axis=1)
        nearest = [{"unit_id": u.id, "kept_unit_id": kept[j].id, "similarity": float(sim[i, j])} for i, (u, j) in enumerate(zip(picks, best))]

    spread = {}
    for u in picks:
        s = spread.setdefault(u.video_id, {"video_id": u.video_id, "n": 0, "t_min": u.t0, "t_max": u.t0})
        s["n"] += 1
        s["t_min"], s["t_max"] = min(s["t_min"], u.t0), max(s["t_max"], u.t0)

    return {"n": n, "recipe": inp["recipe"], "description": cfg.description or "", "goal": goal, "uses_goal": uses_goal,
            "components": components, "semantic_query": semantic_query, "n_picks": len(picks), "n_kept_at_proposal": len(kept), "relaxed": rec["relaxed"],
            "similarity_field": field, "field_used_by_recipe": bool(used), "nearest_kept": nearest, "spread": list(spread.values())}
