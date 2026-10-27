"""review_state = replay log quyết định (Decision, CONTRACTS §12.3) bằng feedback của phiên (MVP `keep_drop`); chặn vượt target_k (§9.7).

`update_state` xét **cả lô**: unit không thuộc pool, hoặc sau lô `|kept| > target_k` → từ chối, không ghi gì (422, #46).
Chỉ ghi những quyết định feedback giữ trong `history` (vd `clear` unit chưa có quyết định thì bỏ qua).
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..core.registry import REGISTRY, discover
from ..core.types import Decision, ReviewState
from . import App, ServiceError, publication_guard
from .session import get_session, make_context

ACTOR = "human:local"   # local-only: chưa có đăng nhập nên mọi quyết định qua command là một người (LOCAL_SINGLE_USER_LIMITS #2)


def _feedback(row: dict):
    discover()
    return REGISTRY.build("feedback", row["spec"]["feedback"])


def get_state(app: App, sid: str) -> ReviewState:
    row = get_session(app, sid)
    return _feedback(row).apply(app.db.decisions(sid), ReviewState())


@publication_guard
def update_state(app: App, sid: str, changes: list[dict]) -> ReviewState:
    """`changes`: `{unit_id, action, proposal_n?, shown_at?, reason?}` theo thứ tự áp."""
    row = get_session(app, sid)
    pool = {u.id for u in make_context(app, row).units}
    outside = [c["unit_id"] for c in changes if c["unit_id"] not in pool]
    if outside:
        raise ServiceError("unit_not_in_pool", f"{len(outside)} unit không thuộc pool của phiên, vd {outside[:3]}")
    feedback, now = _feedback(row), datetime.now(UTC)
    state = feedback.apply(app.db.decisions(sid), ReviewState())
    decisions = [Decision(sid, c["unit_id"], c["action"], ACTOR, row["spec"]["goal"], c.get("proposal_n"), now,
                          c.get("shown_at"), c.get("reason")) for c in changes]
    new = feedback.apply(decisions, state)
    target_k = row["spec"]["target_k"]
    if len(new.kept) > target_k:
        raise ServiceError("target_k_reached", f"lô này làm kept = {len(new.kept)} > target_k = {target_k}; không ghi gì")
    app.db.add_decisions(new.history[len(state.history):])
    return new
