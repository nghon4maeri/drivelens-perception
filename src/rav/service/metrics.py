"""`get_metrics`: tiến độ phiên + metric chọn qua config, chấm bằng P3 `evaluate` (architecture §5.3 MetricsDTO, #43, #50).

`on = "kept"`: chấm tập đang kept; `fields_used` = gộp của các proposal có pick là unit đang kept (recipe nào đã dùng field của metric
→ `independent = 0`). `on = n`: chấm picks của proposal n với `fields_used` của nó — như benchmark chấm một proposal.
"""

from __future__ import annotations

from pydantic import ValidationError

from ..core.types import ComponentConfig
from ..pipeline.evaluate import evaluate
from . import App, ServiceError
from .proposal import session_context
from .session import get_session

DEFAULT_METRICS = [ComponentConfig(type=t) for t in                       # #59: thêm thời gian duyệt, phân tán, độ phủ
                   ("nd_rate_at_k", "keep_rate_by_recipe", "review_time", "min_dist", "temporal_coverage", "cluster_coverage")]


def get_metrics(app: App, sid: str, metrics: list[ComponentConfig] | None, on: str | int) -> dict:
    return metrics_record(session_context(app, sid), metrics, on, get_session(app, sid)["spec"]["target_k"])


def metrics_record(ctx, metrics: list[ComponentConfig] | None, on: str | int, target_k: int) -> dict:
    """Thuần trên Context của phiên (dùng chung local và worker pilot §21.17)."""
    metrics = metrics or DEFAULT_METRICS
    if on == "kept":
        unit_ids = [u.id for u in ctx.units if u.id in ctx.state.kept]
        fields_used = {k for p in ctx.proposals if any(pk.unit.id in ctx.state.kept for pk in p.picks) for k in p.fields_used}
    else:
        found = [p for p in ctx.proposals if p.n == on]
        if not found:
            raise ServiceError("proposal_not_found", f"không có proposal {on}", 404)
        unit_ids, fields_used = [pk.unit.id for pk in found[0].picks], found[0].fields_used
    try:
        values = evaluate(metrics, unit_ids, fields_used, ctx)
    except KeyError as e:
        raise ServiceError("metric_not_found", str(e.args[0]), 404) from None
    except ValidationError as e:
        raise ServiceError("invalid_params", str(e)) from None
    except ValueError as e:                 # vd field không hợp với metric
        raise ServiceError("invalid_params", str(e)) from None
    state = ctx.state
    return {"progress": {"target_k": target_k, "kept": len(state.kept),
                         "dropped": len(state.dropped), "proposals": len(ctx.proposals)},
            "on": on,
            "results": [{"type": m.type, "params": m.params, "values": v} for m, v in zip(metrics, values)]}
