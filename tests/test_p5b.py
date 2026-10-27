"""L5b — export CSV + ảnh (giữ mọi lần, list, zip — #54), list_recipes / describe_component, list_units, return_scores, n_sessions (#46, #48).

Dùng lại video PyAV + field giả của test_p5a (không chạy DINOv2).
"""

import csv
import zipfile

import pytest

from src.rav.commands import ServiceError

from .test_p5a import app, new_session, run, video_id  # noqa: F401  (fixture `app`)


def test_export_keeps_every_run_lists_and_zips(app):  # noqa: F811
    s = new_session(app)
    ids = [x.unit_id for x in run(app, "propose", session_id=s.id, recipe="random", batch_size=3, seed=0).picks]
    run(app, "update_state", session_id=s.id, changes=[{"unit_id": ids[0], "action": "keep"},
                                                       {"unit_id": ids[1], "action": "keep"},
                                                       {"unit_id": ids[2], "action": "drop"}])
    first = run(app, "export", session_id=s.id)
    rows = list(csv.DictReader(open(first.path, encoding="utf-8")))
    folder = app.output_dir / "exports" / s.id / first.id
    assert first.n_frames == 2 and sorted(r["unit_id"] for r in rows) == sorted(ids[:2])
    assert all((folder / r["file"]).stat().st_size > 0 for r in rows) and len(list((folder / "images").iterdir())) == 2
    assert first.download_url == f"/api/v1/exports/{s.id}/{first.id}/download"

    run(app, "update_state", session_id=s.id, changes=[{"unit_id": ids[0], "action": "clear"}])
    second = run(app, "export", session_id=s.id)               # bản mới, bản cũ còn nguyên (#54)
    assert second.id != first.id and second.n_frames == 1 and len(list((folder / "images").iterdir())) == 2
    listed = run(app, "list_exports", session_id=s.id).root
    assert [e.id for e in listed] == [second.id, first.id] and listed[0].goal == "khác biệt"
    assert [e.id for e in run(app, "list_exports").root] == [second.id, first.id]

    z = run(app, "get_export_file", session_id=s.id, export_id=first.id)
    with zipfile.ZipFile(z.path) as f:
        assert sorted(n for n in f.namelist() if not n.endswith("/")) == sorted(["export.csv", "meta.json", *[r["file"] for r in rows]])
    for bad in [("../" + s.id, first.id), (s.id, "../../x"), (s.id, "20990101T000000000Z")]:
        with pytest.raises(ServiceError) as e:
            run(app, "get_export_file", session_id=bad[0], export_id=bad[1])
        assert e.value.status == 404
    with pytest.raises(ServiceError) as e:
        run(app, "export", session_id=s.id, format="nope")
    assert e.value.status == 404


def test_return_scores_match_proposal(app):  # noqa: F811
    s = new_session(app)
    first = run(app, "propose", session_id=s.id, recipe="diversity", batch_size=2)
    run(app, "update_state", session_id=s.id, changes=[{"unit_id": first.picks[0].unit_id, "action": "keep"}])
    p = run(app, "propose", session_id=s.id, recipe="diversity", batch_size=4, return_scores=True)
    sc = p.scores
    assert sc["unit_id"] == [pick.unit_id for pick in p.picks] and first.picks[0].unit_id not in sc["unit_id"]
    for pick in p.picks:
        i = sc["unit_id"].index(pick.unit_id)
        assert sc["combined"][i] == pytest.approx(pick.combined) and sc["novelty"][i] == pytest.approx(pick.breakdown["novelty"])
    assert run(app, "propose", session_id=s.id, recipe="random", batch_size=2).scores is None


def test_list_recipes_config_round_trips(app):  # noqa: F811
    recipes = {r.name: r for r in run(app, "list_recipes").root}
    assert set(recipes) >= {"diversity", "random", "uniform"} and all(r.description for r in recipes.values())
    div = recipes["diversity"]
    assert set(div.params_schema) == {"reference", "objectives", "combiner", "selector"} and len(div.params_schema["objectives"]) == 1
    assert "nms_s" in div.params_schema["selector"]["properties"] and "novelty" in div.components
    s = new_session(app)
    sent = run(app, "propose", session_id=s.id, recipe="diversity", config=div.config.model_dump(), batch_size=2)
    base = run(app, "propose", session_id=s.id, recipe="diversity", batch_size=2)
    assert sent.input.config_hash == base.input.config_hash    # gửi lại nguyên config = recipe có sẵn
    d = run(app, "describe_component", kind="objective", name="novelty")
    assert d.doc and "field" in d.params_schema["properties"]
    with pytest.raises(ServiceError) as e:
        run(app, "describe_component", kind="objective", name="nope")
    assert e.value.status == 404


def test_list_units_and_n_sessions(app):  # noqa: F811
    s = new_session(app)
    units = run(app, "list_units", session_id=s.id).root
    a, b = video_id(app, "a.mp4"), video_id(app, "b.mp4")
    assert len(units) == 12 and [u.t0 for u in units] == sorted(u.t0 for u in units)
    assert units[1].frame_url == f"/api/v1/videos/{a}/frames/2" and units[1].thumb_url.endswith("?max_side=320")
    assert run(app, "list_units", session_id=s.id, video_id=b).root == []    # b ngoài pool
    new_session(app)
    counts = {v.id: v.n_sessions for v in run(app, "list_videos").root}
    assert counts == {a: 2, b: 0}
