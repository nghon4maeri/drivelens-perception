"""L5a — vòng duyệt từ đầu đến cuối qua command: phiên, propose, keep/drop, mở lại, snapshot tái lập (#46).

Video nhỏ sinh bằng PyAV; `emb.dinov2_s`, `hash.phash` ghi sẵn vào field store → execution đọc cache, không chạy DINOv2.
"""

from datetime import UTC, datetime

import numpy as np
import pytest

from src.rav.commands import COMMANDS, App, ServiceError
from src.rav.core.registry import REGISTRY, discover
from src.rav.core.types import Decision, FieldTable, RecipeConfig, ReviewState
from src.rav.pipeline import prepare as p1
from src.rav.pipeline.propose import propose
from src.rav.service.session import get_session, make_context

from .test_p1 import make_video

discover()


def run(app, command, /, **kw):
    cmd = COMMANDS[command]
    return cmd.run(cmd.Input(**kw), app)


def fresh_app(tmp_path):
    return App(tmp_path / "videos", tmp_path / "output", 1 << 30)


@pytest.fixture
def app(tmp_path):
    (tmp_path / "videos").mkdir()
    make_video(tmp_path / "videos" / "a.mp4", seconds=3)
    make_video(tmp_path / "videos" / "b.mp4", seconds=2, shift=100)
    app = fresh_app(tmp_path)
    rng = np.random.default_rng(0)
    for video in p1.catalog(app.spec).values():            # field giả, ghi đúng chỗ execution sẽ đọc
        ctx = p1.make_context([video], app.spec, app.fields)
        ids, part = [u.id for u in ctx.units], (video.id, ctx._execution.spec_hash)
        emb = rng.normal(size=(len(ids), 8)).astype(np.float32)
        emb /= np.linalg.norm(emb, axis=1, keepdims=True)
        for name, values, kind in (("emb.dinov2_s", emb, "vector"),
                                   ("hash.phash", rng.integers(0, 2**63, len(ids), dtype=np.uint64), "scalar")):
            app.fields.put(FieldTable(REGISTRY.feature_for(name)().key, ids, values, kind), part)
    return app


def video_id(app, name):
    return next(v.id for v in p1.catalog(app.spec).values() if v.meta["name"] == name)


def new_session(app, target_k=5):
    return run(app, "create_session", pool={"video_ids": [video_id(app, "a.mp4")]}, goal="khác biệt", target_k=target_k)


def test_keep_drop_replay():
    fb = REGISTRY.build("feedback", {"type": "keep_drop"})
    now = datetime.now(UTC)

    def d(uid, action):
        return Decision("s", uid, action, "human:t", "g", None, now, None)

    log = [d("u1", "keep"), d("u1", "drop"), d("u1", "clear"), d("u2", "clear"), d("u3", "drop")]
    state = fb.apply(log, ReviewState())
    assert state.kept == set() and state.dropped == {"u3"}
    assert [(x.unit_id, x.action) for x in state.history] == [("u1", "keep"), ("u1", "drop"), ("u1", "clear"), ("u3", "drop")]


def test_review_loop_survives_reopen(app, tmp_path):
    s = new_session(app)
    assert (s.n_units, s.n_proposals, s.pool.video_ids) == (12, 0, [video_id(app, "a.mp4")])
    p = run(app, "propose", session_id=s.id, recipe="diversity", batch_size=4)
    assert p.n == 1 and len(p.picks) == 4 and p.input.kept_ids == [] and "emb.dinov2_s@1" in p.input.fields_used
    ids = [x.unit_id for x in p.picks]
    run(app, "update_state", session_id=s.id, changes=[
        {"unit_id": ids[0], "action": "keep", "proposal_n": 1}, {"unit_id": ids[1], "action": "keep", "proposal_n": 1},
        {"unit_id": ids[2], "action": "drop", "proposal_n": 1}, {"unit_id": ids[3], "action": "clear"}])   # clear chưa có quyết định: bỏ qua

    app2 = fresh_app(tmp_path)                                # "tắt app mở lại"
    state = run(app2, "get_state", session_id=s.id)
    assert state.kept_ids == sorted(ids[:2]) and state.dropped_ids == [ids[2]]
    assert [h.action for h in state.history] == ["keep", "keep", "drop"] and state.history[0].goal == "khác biệt"
    assert [x.id for x in run(app2, "list_sessions").root] == [s.id]

    p2 = run(app2, "propose", session_id=s.id, recipe="random", batch_size=20)
    assert p2.n == p2.round == 2 and p2.input.kept_ids == sorted(ids[:2]) and p2.input.dropped_ids == [ids[2]]
    assert len(p2.picks) == 9 and not {x.unit_id for x in p2.picks} & set(ids[:3])   # pool 12 − 3 đã quyết định
    assert [x.n for x in run(app2, "list_proposals", session_id=s.id).root] == [1, 2]
    s2 = run(app2, "get_session", session_id=s.id)
    assert (s2.n_kept, s2.n_dropped, s2.n_proposals) == (2, 1, 2)


def test_update_state_rejects_whole_batch(app):
    s = new_session(app, target_k=2)
    units = [u.id for u in make_context(app, get_session(app, s.id)).units]
    with pytest.raises(ServiceError) as e:
        run(app, "update_state", session_id=s.id, changes=[{"unit_id": u, "action": "keep"} for u in units[:3]])
    assert (e.value.code, e.value.status) == ("target_k_reached", 422)
    with pytest.raises(ServiceError) as e:                    # unit của video ngoài pool
        run(app, "update_state", session_id=s.id,
            changes=[{"unit_id": units[0], "action": "keep"}, {"unit_id": f"{video_id(app, 'b.mp4')}:0.000-0.000", "action": "keep"}])
    assert e.value.code == "unit_not_in_pool"
    assert run(app, "get_state", session_id=s.id).history == []          # không ghi phần nào
    changes = [{"unit_id": u, "action": "keep"} for u in units[:2]] + [{"unit_id": units[2], "action": "drop"}]
    assert len(run(app, "update_state", session_id=s.id, changes=changes).kept_ids) == 2   # đúng bằng target_k: được


def test_propose_config_validation(app):
    s = new_session(app)
    config = REGISTRY.recipe("diversity").model_dump()
    with pytest.raises(ServiceError) as e:
        run(app, "propose", session_id=s.id, recipe="random", config=config, batch_size=4)
    assert (e.value.code, e.value.status) == ("recipe_mismatch", 422)
    config["selector"]["params"]["nms_s"] = 0.0               # chỉnh tham số: chạy được, config_hash khác bản gốc
    p = run(app, "propose", session_id=s.id, recipe="diversity", config=config, batch_size=4)
    base = run(app, "propose", session_id=s.id, recipe="diversity", batch_size=4)
    assert p.input.config.selector.params["nms_s"] == 0.0 and p.input.config_hash != base.input.config_hash
    config["selector"]["params"]["typo"] = 1
    with pytest.raises(ServiceError) as e:
        run(app, "propose", session_id=s.id, recipe="diversity", config=config, batch_size=4)
    assert e.value.code == "invalid_config"


def test_same_seed_and_snapshot_same_proposal(app):
    s = new_session(app)
    first = run(app, "propose", session_id=s.id, recipe="random", batch_size=4)
    run(app, "update_state", session_id=s.id, changes=[{"unit_id": first.picks[0].unit_id, "action": "keep"}])
    p = run(app, "propose", session_id=s.id, recipe="random", batch_size=4)   # seed sinh ngẫu nhiên, lưu trong snapshot
    again = run(app, "propose", session_id=s.id, recipe="random", batch_size=4, seed=p.input.seed)
    assert [x.unit_id for x in again.picks] == [x.unit_id for x in p.picks]

    snap = p.input                                             # dựng lại ctx chỉ từ snapshot → cùng proposal
    ctx = make_context(app, get_session(app, s.id), state=ReviewState(set(snap.kept_ids), set(snap.dropped_ids)))
    replay = propose(RecipeConfig.model_validate(snap.config.model_dump()), snap.batch_size, snap.seed, ctx)
    assert [x.unit.id for x in replay.picks] == [x.unit_id for x in p.picks] and replay.config_hash == snap.config_hash
