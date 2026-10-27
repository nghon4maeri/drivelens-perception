"""Pure baseline Unit checks and real PyAV context timing, without PostgreSQL/S3."""

from fractions import Fraction

import av
import numpy as np
import pytest

from src.rav.errors import ServiceError
from src.rav.store.pilot_units import frame_index
from src.rav.worker.handlers import _context_clip


@pytest.mark.parametrize("bad", ["v:0.1-0.1", "v:00.000-00.000", "v:0.000-0.250",
                                 "v:0.050-0.050", "v:6.000-6.000", "v:0.000-0.000\n", "other:0.000-0.000"])
def test_invalid_unit(bad):
    with pytest.raises(ServiceError) as e:
        frame_index(bad, "v", {"fps": 60, "n_frames": 360})
    assert e.value.code == "unit_not_in_source"


@pytest.mark.parametrize("fps,n", [(float("nan"), 3), (float("inf"), 3), (True, 3), (4, True), (4, 0), (None, 3)])
def test_unverified_metadata(fps, n):
    with pytest.raises(ServiceError) as e:
        frame_index("v:0.000-0.000", "v", {"fps": fps, "n_frames": n})
    assert e.value.code == "source_unavailable"


@pytest.mark.parametrize("fps", [2, 10, 29.97, 60])
def test_grid_matches_core_rounding(fps):
    for k in range(16):
        idx = round(k * max(fps / 4, 1))
        t = idx / fps
        assert frame_index(f"v:{t:.3f}-{t:.3f}", "v", {"fps": fps, "n_frames": 500}) == (idx, t)


@pytest.mark.parametrize("vfr", [False, True])
@pytest.mark.parametrize("center", [1, 4, 7])
def test_context_relative_timeline_and_anchor(tmp_path, vfr, center):
    source, output = tmp_path / "source.mp4", tmp_path / "context.mp4"
    times = [i / 60 for i in range(480)] if not vfr else [i / 60 for i in range(120)] + [2 + i / 10 for i in range(60)]
    with av.open(str(source), "w") as dst:
        stream = dst.add_stream("libx264", rate=60)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        stream.time_base = Fraction(1, 600)
        for t in times:
            image = np.full((48, 64, 3), round(t * 25), np.uint8)
            frame = av.VideoFrame.from_ndarray(image, format="rgb24")
            # Nonzero source start verifies normalized source-relative time.
            frame.pts, frame.time_base = round((t + 2) * 600), Fraction(1, 600)
            for packet in stream.encode(frame):
                dst.mux(packet)
        for packet in stream.encode():
            dst.mux(packet)
    assert _context_clip(source, center, 3, 64, output)[4:8] == b"ftyp"
    with av.open(str(source)) as raw:
        stream = raw.streams.video[0]
        source_origin = float(stream.start_time * stream.time_base)
        last = list(raw.decode(stream))[-1]
        source_duration = float(last.time) - source_origin + float(last.duration * last.time_base)
    start = max(0, center - 3)
    expected_duration = min(source_duration, center + 3) - start
    with av.open(str(output)) as result:
        stream = result.streams.video[0]
        frames = list(result.decode(stream))
        duration = float(stream.duration * stream.time_base)
        assert abs(duration - expected_duration) <= 1 / float(stream.average_rate) + 0.001
        assert len(frames) <= 181
        anchor = min(frames, key=lambda f: abs(float(f.time) - (center - start)))
        assert abs(float(anchor.time) - (center - start)) < 0.1
        assert abs(float(anchor.to_ndarray(format="rgb24").mean()) - center * 25) < 6
