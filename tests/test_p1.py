"""L2 — P1: source, decoder pyav, unit per_frame, execution + field_store (cache theo per_sample), upload, get_frame.

Video giả sinh bằng PyAV trong thư mục tạm; không chạy DINOv2 (chậm) — cache kiểm bằng feature giả.
"""

import av
import numpy as np
import pytest

from src.rav.commands import COMMANDS, App, ServiceError
from src.rav.components.decoders.pyav import PyAV
from src.rav.components.sources.local_folder import LocalFolder
from src.rav.components.units.per_frame import PerFrame
from src.rav.core.component import Feature
from src.rav.core.context import Context
from src.rav.core.execution import Execution, spec_hash
from src.rav.core.registry import Registry
from src.rav.core.types import FieldKey, FieldTable
from src.rav.pipeline import prepare as p1
from src.rav.store.field_store import FieldStore


def make_video(path, seconds=3, fps=10, shift=0):
    """Video nhỏ, mỗi frame một màu khác nhau (đọc lại kiểm được đúng frame)."""
    with av.open(str(path), "w") as out:
        stream = out.add_stream("mpeg4", rate=fps)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        for i in range(seconds * fps):
            img = np.zeros((48, 64, 3), np.uint8)
            img[:, :, 0] = (i * 8 + shift) % 256
            img[:, : (i % 64) + 1, 1] = 200
            out.mux(stream.encode(av.VideoFrame.from_ndarray(img, format="rgb24")))
        out.mux(stream.encode())
    return path


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "videos"
    root.mkdir()
    make_video(root / "a.mp4")
    make_video(root / "b.mp4", seconds=2, shift=100)
    (root / "notes.txt").write_text("không phải video")
    return root


def videos_in(root):
    return sorted(LocalFolder({"root": str(root)}).iter_videos(), key=lambda v: v.meta["name"])


def test_source_lists_videos_with_stable_content_id(folder):
    a, b = videos_in(folder)
    assert [a.meta["name"], b.meta["name"]] == ["a.mp4", "b.mp4"]
    assert (a.fps, a.duration_s, b.duration_s) == (10.0, 3.0, 2.0)
    (folder / "a.mp4").rename(folder / "renamed.mp4")
    assert {v.id for v in videos_in(folder)} == {a.id, b.id}   # id = nội dung, không phụ thuộc tên


def test_decoder_frames_and_read(folder):
    video = videos_in(folder)[0]
    frames = PyAV({"fps": 4}).frames(video)
    assert len(frames) == 12 and [f.t for f in frames] == sorted(f.t for f in frames)
    assert [f.idx for f in frames[:4]] == [0, 2, 5, 8]          # round(k × 10 / 4)
    dec = PyAV()
    sequential = [dec.read(video, f).copy() for f in frames]
    assert sequential[0].shape == (48, 64, 3)
    PyAV._handles.clear()
    for i in (11, 3, 7, 0):                                      # seek lùi / nhảy xa → cùng ảnh như đọc tuần tự
        assert np.array_equal(dec.read(video, frames[i]), sequential[i])
    assert PyAV({"fps": 100}).frames(video)[-1].idx == 29        # fps > gốc: mỗi frame gốc một lần


def test_per_frame_units_deterministic_and_unique_across_videos(folder):
    units = []
    for v in videos_in(folder):
        units += PerFrame().build(v, PyAV().frames(v))
    assert len({u.id for u in units}) == len(units) == 12 + 8
    u = units[1]
    assert u.id == f"{u.video_id}:0.200-0.200" and u.kind == "frame" and u.frames == (u.anchor,)


def test_field_store_roundtrip(tmp_path):
    store = FieldStore(tmp_path)
    key = FieldKey("emb.x", "x", "1", "abc")
    assert store.get(key, ("v", "s")) is None
    store.put(FieldTable(key, ["v:1", "v:2"], np.eye(2, dtype=np.float32), "vector"), ("v", "s"))
    got = store.get(key, ("v", "s"))
    assert got.unit_ids == ["v:1", "v:2"] and np.array_equal(got.values, np.eye(2))
    store.put(FieldTable(key, ["p"], [{"a": 1}], "structured"), "poolhash")
    assert store.get(key, "poolhash").values == [{"a": 1}]


# ---- execution: cache theo per_sample (CONTRACTS §3), đếm số lần compute bằng feature giả

def fake_registry(calls, ver="1"):
    reg = Registry()

    @reg.register("feature")
    class Mean(Feature):
        """Độ sáng trung bình kênh đỏ của frame anchor."""
        name, version, provides, per_sample = "mean", ver, "fake.mean", True

        def compute(self, units, ctx):
            calls.append(("mean", sorted({u.video_id for u in units}), len(units)))
            return FieldTable(self.key, [u.id for u in units],
                              np.array([ctx.pixels(u.anchor)[..., 0].mean() for u in units]), "scalar")

    @reg.register("feature")
    class Rank(Feature):
        """Hạng của fake.mean trong cả pool — phụ thuộc cả tập nên per_sample=False."""
        name, version, provides, requires = "rank", "1", "fake.rank", ("fake.mean",)

        def compute(self, units, ctx):
            calls.append(("rank", sorted({u.video_id for u in units}), len(units)))
            return FieldTable(self.key, [u.id for u in units], ctx.field("fake.mean", units).values.argsort().argsort(),
                              "scalar")

    return reg


def make_ctx(videos, store, registry, fps=4):
    dec, builder = PyAV({"fps": fps}), PerFrame()
    units = sorted((u for v in videos for u in builder.build(v, dec.frames(v))), key=lambda u: (u.video_id, u.t0))
    return Context(units, execution=Execution(store, spec_hash(dec, builder)), videos={v.id: v for v in videos},
                   decoder=dec, registry=registry)


def test_execution_caches_per_video_and_per_pool(folder, tmp_path):
    a, b = videos_in(folder)
    store, calls = FieldStore(tmp_path / "fields"), []
    reg = fake_registry(calls)

    ctx = make_ctx([a], store, reg)
    first = ctx.field("fake.rank").values
    assert calls == [("rank", [a.id], 12), ("mean", [a.id], 12)]
    assert np.array_equal(make_ctx([a], store, reg).field("fake.rank").values, first)
    assert len(calls) == 2                                           # gọi lại (phiên khác, cùng pool): đọc cache

    calls.clear()
    ctx = make_ctx([a, b], store, reg)
    ctx.field("fake.rank", ctx.units[-3:])                           # hỏi tập con → vẫn tính trên cả pool
    assert calls == [("rank", sorted([a.id, b.id]), 20), ("mean", [b.id], 8)]   # thêm video: chỉ tính video mới

    calls.clear()
    make_ctx([a], store, reg, fps=2).field("fake.mean")
    assert calls == [("mean", [a.id], 6)]                            # cùng video khác fps → cache khác

    calls.clear()
    reg2 = fake_registry(calls, ver="2")
    make_ctx([a], store, reg2).field("fake.mean")
    assert calls == [("mean", [a.id], 12)]                           # đổi version → tính lại feature đó
    make_ctx([a], store, reg).field("fake.mean")
    assert len(calls) == 1                                           # version cũ vẫn còn cache


def test_context_field_aligns_to_requested_units(folder, tmp_path):
    a, b = videos_in(folder)
    ctx = make_ctx([a, b], FieldStore(tmp_path), fake_registry([]))
    ask = [ctx.units[15], ctx.units[2]]
    table = ctx.field("fake.mean", ask)
    assert table.unit_ids == [u.id for u in ask]
    assert table.values[1] == pytest.approx(ctx.pixels(ctx.units[2].anchor)[..., 0].mean())


def test_real_phash_and_pca2_through_pipeline_context(folder, tmp_path, monkeypatch):
    """phash thật; pca2 thật trên embedding giả (không chạy DINOv2)."""
    spec = {**p1.DEFAULT_SPEC, "source": {"type": "local_folder", "params": {"root": str(folder)}}}
    videos = list(p1.catalog(spec).values())
    ctx = p1.make_context(videos, spec, FieldStore(tmp_path))
    rng = np.random.default_rng(0)
    emb = FieldTable(FieldKey("emb.dinov2_s", "dinov2_s", "1", "x"), [u.id for u in ctx.units],
                     rng.normal(size=(len(ctx.units), 384)).astype(np.float32), "vector")
    real = ctx.field
    monkeypatch.setattr(ctx, "field", lambda name, units=None: emb if name == "emb.dinov2_s" else real(name, units))
    h = ctx.field("hash.phash").values
    assert h.dtype == np.uint64 and len(h) == 20 and len(set(h.tolist())) > 1
    xy = ctx.field("proj.pca2").values
    assert xy.shape == (20, 2) and xy[:, 0].var() >= xy[:, 1].var()


def test_upload_limits_dedupe_and_rejects_non_video(folder, tmp_path):
    app = App(videos_dir=tmp_path / "catalog", output_dir=tmp_path / "out", max_upload_bytes=10 << 20)
    upload = COMMANDS["upload_video"]
    src = folder / "a.mp4"
    first = upload.run(upload.Input(filename="clip.mp4", path=str(src)), app)
    again = upload.run(upload.Input(filename="other-name.mp4", path=str(src)), app)
    assert again.id == first.id and len(list(app.videos_dir.iterdir())) == 1
    # bản chép trùng có tên xếp trước bản gốc (cùng tên → tiền tố mtime; tên "a…") vẫn nhận ra là video cũ
    for name in ("clip.mp4", "a-copy.mp4"):
        assert upload.run(upload.Input(filename=name, path=str(src)), app).id == first.id
    assert len(list(app.videos_dir.iterdir())) == 1

    bad = tmp_path / "fake.mp4"
    bad.write_bytes(b"not a video" * 100)
    with pytest.raises(ServiceError) as err:
        upload.run(upload.Input(filename="fake.mp4", path=str(bad)), app)
    assert err.value.code == "invalid_video" and len(list(app.videos_dir.iterdir())) == 1

    app.max_upload_bytes = 10
    with pytest.raises(ServiceError) as err:
        upload.run(upload.Input(filename="big.mp4", path=str(folder / "b.mp4")), app)
    assert err.value.code == "file_too_large"


def test_get_frame_and_list_videos(folder, tmp_path):
    app = App(videos_dir=folder, output_dir=tmp_path / "out", max_upload_bytes=1 << 30)
    listed = COMMANDS["list_videos"].run(COMMANDS["list_videos"].Input(), app).root
    assert len(listed) == 2 and not any(v.prepared for v in listed)
    get = COMMANDS["get_frame"]
    out = get.run(get.Input(video_id=listed[0].id, idx=5, max_side=32), app)
    assert out.data[:3] == b"\xff\xd8\xff"
    with pytest.raises(ServiceError) as err:
        get.run(get.Input(video_id="nope", idx=0), app)
    assert err.value.code == "video_not_found"
