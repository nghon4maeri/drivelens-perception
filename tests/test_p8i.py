"""L8i — giải thích cấp đợt `explain_proposal` (#61): chỉ dữ liệu thật của đề xuất đã lưu, đúng phạm vi tập đã giữ lúc đề xuất."""

import pytest

from src.rav.commands import ServiceError

from .test_p5a import app, new_session, run  # noqa: F401  (fixture `app`)


def test_explain_proposal(app):  # noqa: F811
    s = new_session(app)
    first = run(app, "propose", session_id=s.id, recipe="diversity", batch_size=4, seed=0)
    e1 = run(app, "explain_proposal", session_id=s.id, n=1)
    assert e1.recipe == "diversity" and not e1.uses_goal and e1.n_picks == 4
    assert e1.n_kept_at_proposal == 0 and e1.nearest_kept == []                         # đợt đầu: chưa có tập đã giữ
    assert {c.role for c in e1.components} == {"reference", "objective", "combiner", "selector"} and all(c.doc for c in e1.components)
    assert e1.field_used_by_recipe and e1.similarity_field == "emb.dinov2_s" and sum(v.n for v in e1.spread) == 4

    kept = [p.unit_id for p in first.picks[:2]]
    run(app, "update_state", session_id=s.id, changes=[{"unit_id": u, "action": "keep"} for u in kept])
    run(app, "propose", session_id=s.id, recipe="random", batch_size=3, seed=1)
    run(app, "update_state", session_id=s.id, changes=[{"unit_id": first.picks[2].unit_id, "action": "keep"}])   # giữ sau đề xuất 2
    e2 = run(app, "explain_proposal", session_id=s.id, n=2)
    assert e2.n_kept_at_proposal == 2                                                     # phạm vi = lúc đề xuất, không phải hiện tại
    assert len(e2.nearest_kept) == 3 and all(n.kept_unit_id in kept and -1 <= n.similarity <= 1 for n in e2.nearest_kept)
    assert not e2.field_used_by_recipe                                                    # random không đọc embedding → chỉ là số đo thêm

    with pytest.raises(ServiceError) as err:
        run(app, "explain_proposal", session_id=s.id, n=9)
    assert err.value.code == "proposal_not_found"
