"""L8h — đủ chỉ số UI (#59): metric review_time / min_dist / temporal_coverage / cluster_coverage, thông tin video, bằng chứng (CONTRACTS §8.0).

Dùng fixture của test_p5a (video PyAV nhỏ, field giả). Field `det.*` / `tag.*` giả ghi thẳng vào cache — get_evidence chỉ đọc cache.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest

from src.rav.commands import ServiceError
from src.rav.core.component import Feature
from src.rav.core.registry import register
from src.rav.core.types import FieldTable
from src.rav.pipeline import prepare as p1

from .test_p5a import app, new_session, run, video_id  # noqa: F401  (fixture `app`)


@register("feature")
class FakeDet(Feature):
    """Detector giả cho test: chỉ có trong cache, không bao giờ được tính."""

    name = "fake_det"
    version = "1"
    provides = "det.fake"
    per_sample = True

    def compute(self, units, ctx):
        raise AssertionError("get_evidence không được tính field")


@register("feature")
class FakeTag(Feature):
    """Nhãn cảnh giả cho test."""

    name = "fake_tag"
    version = "1"
    provides = "tag.fake"
    per_sample = True

    def compute(self, units, ctx):
        raise AssertionError("get_evidence không được tính field")


def test_new_metrics_values(app):  # noqa: F811
    s = new_session(app)
    p = run(app, "propose", session_id=s.id, recipe="diversity", batch_size=4, seed=0)
    shown = (datetime.now(UTC) - timedelta(seconds=20)).isoformat()
    ids = [x.unit_id for x in p.picks]
    run(app, "update_state", session_id=s.id, changes=[{"unit_id": ids[0], "action": "keep", "proposal_n": 1, "shown_at": shown},
                                                       {"unit_id": ids[1], "action": "keep", "proposal_n": 1, "shown_at": shown}])
    vals = {r.type: r.values for r in run(app, "get_metrics", session_id=s.id).results}
    assert 9 <= vals["review_time"]["review_time"] <= 11 and vals["review_time"]["review_time/decisions"] == 2   # ~20 s / 2 frame
    assert 0 < vals["review_time"]["review_time/per_minute"]
    assert 0 <= vals["min_dist"]["min_dist/min"] <= vals["min_dist"]["min_dist"] <= 2
    assert 0 < vals["temporal_coverage"]["temporal_coverage"] <= 1
    assert 0 < vals["cluster_coverage"]["cluster_coverage"] <= 1 and 0 <= vals["cluster_coverage"]["cluster_coverage/entropy"] <= 1


def test_video_file_info(app):  # noqa: F811
    v = next(x for x in run(app, "list_videos").root if x.name == "a.mp4")
    assert v.width > 0 and v.height > 0 and v.fps > 0 and v.size_bytes > 0 and v.added_at


def test_evidence_reads_cache_only(app):  # noqa: F811
    s = new_session(app)
    units = run(app, "list_units", session_id=s.id).root[:3]
    ids = [u.id for u in units]
    assert all(e.detections is None and e.tags is None and e.telemetry is None
               for e in run(app, "get_evidence", session_id=s.id, unit_ids=ids).root)          # chưa có nguồn → null hết

    video = next(v for v in p1.catalog(app.spec).values() if v.id == units[0].video_id)
    ctx = p1.make_context([video], app.spec, app.fields)
    part, all_ids = (video.id, ctx._execution.spec_hash), [u.id for u in ctx.units]
    app.fields.put(FieldTable(FakeDet().key, all_ids, [[{"label": "person", "score": 0.9, "box": [0.1, 0.2, 0.3, 0.4]}]] * len(all_ids),
                              "structured"), part)
    app.fields.put(FieldTable(FakeTag().key, all_ids, [{"timeofday": "night"}] * len(all_ids), "structured"), part)
    t0 = units[0].t0
    (app.videos_dir / "a.mp4.telemetry.json").write_text(json.dumps([{"t": t0 + 1, "speed_kmh": 30}, {"t": t0 + 10, "speed_kmh": 50}]),
                                                         encoding="utf-8")
    e = run(app, "get_evidence", session_id=s.id, unit_ids=[ids[0]]).root[0]
    assert e.detections[0].label == "person" and e.detections[0].source == "det.fake" and e.tags == {"timeofday": "night"}
    assert [x.speed_kmh for x in e.telemetry] == [30]                                         # chỉ mẫu trong t0 ± 3 s

    with pytest.raises(ServiceError) as err:
        run(app, "get_evidence", session_id=s.id, unit_ids=["khong-co"])
    assert err.value.code == "unit_not_found"
