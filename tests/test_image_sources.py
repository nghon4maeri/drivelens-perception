"""S3: identity/cache video cũ và nguồn ảnh (PIL, EXIF, cache theo version).

Dữ liệu tổng hợp trong tmp_path; chạy python -m pytest tests/test_image_sources.py -q.
"""

import numpy as np
from PIL import Image

from src.rav.core.component import Feature
from src.rav.core.context import Context
from src.rav.core.execution import Execution, image_spec_hash, pool_hash
from src.rav.core.registry import Registry
from src.rav.core.types import FieldTable, Frame, ImageRef, ImageSetRef, Unit, unit_id
from src.rav.store.field_store import FieldStore


def video_unit(source, t):
    frame = Frame(source, int(t * 4), t)
    return Unit(unit_id(source, t, t), source, t, t, "frame", frame, (frame,))


def image_unit(version, key, relpath):
    image = ImageRef(version, key, relpath)
    return Unit(f"ds-a:{key}", None, None, None, "image", image, (image,))


def mean_feature(calls):
    class Mean(Feature):
        """Trung bình pixel RGB, dùng để kiểm tính và cache nguồn ảnh."""

        name, version, provides = "test_image_mean", "1", "test.image_mean"
        per_sample = True

        def compute(self, units, ctx):
            calls.append([u.id for u in units])
            return FieldTable(self.key, [u.id for u in units],
                              np.array([ctx.pixels(u.anchor).mean() for u in units]), "scalar")

    registry = Registry()
    registry.register("feature")(Mean)
    return Mean, registry


def test_video_identity_and_pool_hash_golden():
    units = [video_unit("b", 0), video_unit("a", .25), video_unit("a", 0)]
    assert units[1].id == "a:0.250-0.250"
    assert units[1].source_id == "a" and units[1].order_key == ("a", .25)
    assert pool_hash(units) == "4405ecc8c2c79c6b"


def test_old_video_cache_is_read_without_compute(tmp_path):
    calls = []
    feature, registry = mean_feature(calls)
    unit = video_unit("a", 0)
    store = FieldStore(tmp_path)
    # Đường dẫn và bảng cache theo định dạng trước S3.
    store.put(FieldTable(feature().key, [unit.id], np.array([42.]), "scalar"), ("a", "old-spec"))
    ctx = Context([unit], execution=Execution(store, "old-spec"), registry=registry)
    assert ctx.field(feature.provides).values.tolist() == [42.]
    assert ctx.cached(feature.provides, [unit]).values.tolist() == [42.]
    assert calls == []


def test_images_group_by_version_not_sample_id_and_align(tmp_path):
    root = tmp_path / "ảnh có dấu"
    root.mkdir()
    Image.new("RGB", (4, 3), (12, 12, 12)).save(root / "một.png")
    Image.new("L", (4, 3), 90).save(root / "hai.png")
    units = [image_unit("dv-one", "b", "một.png"), image_unit("dv-one", "a", "hai.png")]
    extra = image_unit("dv-two", "c", "một.png")
    calls = []
    feature, registry = mean_feature(calls)
    store = FieldStore(tmp_path / "fields")
    images = {v: ImageSetRef(v, "ds-a", str(root)) for v in ("dv-one", "dv-two")}
    ctx = Context([*units, extra], images=images, execution=Execution(store, "video-spec"), registry=registry)
    assert units[0].video_id is None and units[0].t0 is None and units[0].t1 is None
    assert units[0].source_id == "dv-one" and units[0].order_key == ("dv-one", "b")
    assert [u.anchor.key for u in sorted(units, key=lambda u: u.order_key)] == ["a", "b"]
    assert ctx.cached(feature.provides, [units[0]]) is None
    table = ctx.field(feature.provides, [units[1], units[0]])
    assert table.values.tolist() == [90., 12.]
    assert calls == [[u.id for u in units]]  # hỏi một phần: tính cả version, chưa tính version khác
    cached = store.get(feature().key, ("dv-one", image_spec_hash()))
    assert cached.unit_ids == [u.id for u in units]
    assert store.get(feature().key, ("dv-two", image_spec_hash())) is None
    ctx2 = Context([*units, extra], images=images, execution=Execution(store, "other-video-spec"), registry=registry)
    assert ctx2.field(feature.provides, [units[0]]).values.tolist() == [12.]
    assert len(calls) == 1  # đổi fps video không vô hiệu cache ảnh
    assert ctx2.field(feature.provides, [extra]).values.tolist() == [12.]
    assert calls[-1] == [extra.id]


def test_pixels_transposes_exif_and_converts_rgb_without_decoder(tmp_path):
    image = Image.new("L", (4, 2), 73)
    exif = image.getexif()
    exif[274] = 6
    image.save(tmp_path / "xoay.png", exif=exif)
    ref = ImageRef("dv-one", "key", "xoay.png")
    ctx = Context([], images={"dv-one": ImageSetRef("dv-one", "ds-a", str(tmp_path))},
                  execution=Execution(FieldStore(tmp_path / "fields"), "unused"))
    pixels = ctx.pixels(ref)
    assert pixels.shape == (4, 2, 3) and pixels.dtype == np.uint8
    assert np.all(pixels == 73)
    assert ctx.pixels(ref) is pixels


def test_mixed_sources_use_real_phash_and_separate_cache(tmp_path):
    from src.rav.core.registry import discover
    from src.rav.core.types import VideoRef

    discover()
    pixels = np.arange(36, dtype=np.uint8).reshape(3, 4, 3)
    Image.fromarray(pixels).save(tmp_path / "ảnh.png")
    image = image_unit("dv-one", "key", "ảnh.png")
    video = video_unit("video", 0)

    class Decoder:
        def read(self, source, frame):
            return pixels

    store = FieldStore(tmp_path / "fields")
    ctx = Context([image, video], execution=Execution(store, "video-spec"), decoder=Decoder(),
                  videos={"video": VideoRef("video", "unused", 4, 1)},
                  images={"dv-one": ImageSetRef("dv-one", "ds-a", str(tmp_path))})
    table = ctx.field("hash.phash", [video, image])
    assert table.unit_ids == [video.id, image.id] and table.values[0] == table.values[1]
    assert store.get(table.key, ("video", "video-spec")).unit_ids == [video.id]
    assert store.get(table.key, ("dv-one", image_spec_hash())).unit_ids == [image.id]


def test_temporal_components_ignore_image_time_without_rejecting_image_novelty():
    from src.rav.components.metrics.temporal_coverage import TemporalCoverage
    from src.rav.components.selectors.greedy_nms import GreedyNMS
    from src.rav.components.selectors.uniform import Uniform
    from src.rav.components.views.timeline import Timeline

    units = [image_unit("dv-one", "a", "a.png"), video_unit("video", 0),
             image_unit("dv-one", "b", "b.png"), video_unit("video", .25)]
    ctx = Context(units, execution=None)
    combined, mask = np.ones(4), np.ones(4, dtype=bool)
    assert Uniform().pick(units, combined, mask, 4, ctx).indices == [1, 3]
    assert TemporalCoverage().evaluate([units[0].id], ctx) == {"temporal_coverage": 0., "by_video/video": 0.}
    view = Timeline().build(ctx)
    assert view.data["video_id"] == "video" and view.data["t"] == [0., .25]

    # NMS thời gian chỉ chặn frame video; ảnh vẫn tham gia khoảng cách embedding.
    ctx.field = lambda name, asked: FieldTable(None, [u.id for u in asked], np.eye(4), "vector")
    result = GreedyNMS().pick(units, combined, mask, 3, ctx)
    assert set(result.indices) == {0, 1, 2} and not result.relaxed
    ctx.units = [units[0]]
    assert TemporalCoverage().evaluate([units[0].id], ctx) == {"temporal_coverage": 0.}
    assert Timeline().build(ctx).data["t"] == []
    assert Uniform().pick(ctx.units, np.ones(1), np.ones(1, dtype=bool), 1, ctx).indices == []
