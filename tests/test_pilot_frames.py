"""Ảnh lưu sẵn khi upload (DEP7-Frames F1, CONTRACTS §21.20): Postgres thật + S3 moto + worker VALIDATE_UPLOAD với video thật.

Kiểm: bộ đủ theo ordered IDs + exact anchor, dung lượng tính một lần, dedup/race, lỗi giữa lô, hủy, mất lease rồi retry,
policy thiếu khóa, giới hạn byte. Chạy: đặt `RAV_PILOT_TEST_DATABASE_URL` rồi `python -m pytest tests/test_pilot_frames.py -q`.
"""

from __future__ import annotations

import hashlib
import threading
from uuid import uuid4

import pytest
import requests

from src.rav.actor import ActorContext
from src.rav.errors import ServiceError
from src.rav.store import pilot_frames, pilot_objects
from src.rav.store.pilot_db import PilotDB
from src.rav.store.pilot_runtime import PilotRuntime, Usage, WorkerContext
from src.rav.store.pilot_uploads import PilotUploads, UploadIn
from src.rav.worker.dispatcher import PilotDispatcher
from src.rav.worker.handlers import _probe, make_handlers
from src.rav.worker.process import identity
from src.rav.worker.supervisor import PilotSupervisor
from tests.conftest import POLICY_CONFIG, make_video


def actor(uid) -> ActorContext:
    return ActorContext(user_id=uid)


@pytest.fixture
def make(pg, s3_storage, tmp_path):
    def build(**cfg):
        config = dict(POLICY_CONFIG, storage_user_bytes=10 << 20, staging_grace_ms=1)
        config.update(cfg)
        policy = pg.policy(config=config)
        a = pg.profile(policy_id=policy)
        pg.slot("cpu-1")
        worker = PilotRuntime(PilotDB(pg.worker_url))
        d = PilotDispatcher(worker, WorkerContext(worker_id=uuid4(), host_boot_id=uuid4()), "CPU", heartbeat_s=0.2)
        for kind, fn in make_handlers(s3_storage, PilotDB(pg.worker_url), tmp_path / "work").items():
            d.register_handler(kind, fn)
        return {"pg": pg, "s3": s3_storage, "a": a, "svc": PilotUploads(PilotDB(pg.api_url), lambda: s3_storage), "dispatch": d,
                "video": make_video(tmp_path / "clip.mp4", seconds=4, fps=10), "tmp": tmp_path,
                "supervisor": PilotSupervisor(worker, d.worker_ctx, PilotUploads(PilotDB(pg.worker_url), lambda: s3_storage))}
    return build


def start(u, data: bytes, key: str):
    svc, a = u["svc"], u["a"]
    created = svc.create(actor(a), UploadIn(filename="clip.mp4", declared_bytes=len(data)), key)
    r = requests.post(created.transfer.url, data=created.transfer.fields, files={"file": ("video", data)})
    assert r.status_code in (200, 204), r.text
    return created, svc.complete(actor(a), created.id)


def uploaded(u, data: bytes, key: str):
    created, _ = start(u, data, key)
    assert u["dispatch"].run_once()
    return created, u["svc"].get(actor(u["a"]), created.id)


def account(pg, uid):
    return pg.sql("SELECT persisted_bytes, reserved_bytes FROM rav_pilot_storage_account WHERE account_key = %s",
                  (f"USER:{uid}",))[0]


def receipts(pg) -> dict[str, int]:
    return {r["state"]: r["n"] for r in pg.sql("SELECT state, count(*)::int AS n FROM rav_pilot_worker_object GROUP BY state")}


def frame_objects(s3) -> list[str]:
    page = s3.client.list_objects_v2(Bucket=s3.bucket, Prefix=f"{s3.prefix}/private/")
    return [o["Key"] for o in page.get("Contents", []) if "/frames/" in o["Key"]]


def expected_jpegs(path):
    """JPEG worker phải tạo: encode từ `read_exact` (index stream thật) của decoder core."""
    from src.rav.components.decoders.pyav import PyAV
    from src.rav.store.pilot_db import DEFAULT_SAMPLE_SPEC
    from src.rav.worker.frame_writer import encode

    video = _probe(path)
    dec = PyAV(DEFAULT_SAMPLE_SPEC["decoder"]["params"])
    frames = dec.frames(video)
    try:
        out = {(v, f.idx): data for f in frames for v, data, _, _ in encode(dec.read_exact(video, f))}
    finally:
        PyAV._handles.pop(video.uri).container.close()
    return video, frames, out


@pytest.mark.parametrize("seconds,fps", [(4, 10), (3, 3)])   # 4 fps lấy mẫu từ 10 fps; nguồn fps thấp lấy mọi frame tới EOF
def test_upload_materializes_full_frame_set_from_exact_anchors(make, seconds, fps):
    from src.rav.store.pilot_media import MediaRequestIn, PilotMedia

    u = make()
    pg, a, s3 = u["pg"], u["a"], u["s3"]
    path = make_video(u["tmp"] / f"v{fps}.mp4", seconds=seconds, fps=fps)
    data = path.read_bytes()
    _, dto = uploaded(u, data, "u1")
    assert dto.state == "READY"
    video, frames, jpegs = expected_jpegs(path)
    fs = pg.sql("SELECT * FROM rav_pilot_frame_set")
    assert len(fs) == 1 and fs[0]["state"] == "READY" and fs[0]["source_id"] == dto.source_id and fs[0]["owner_id"] == a
    assert fs[0]["n_units"] == len(frames) and fs[0]["object_count"] == 2 * len(frames)
    assert fs[0]["job_id"] == dto.validation_job_id and fs[0]["encoding_version"] == pilot_frames.ENCODING_VERSION
    rows = pg.sql("SELECT * FROM rav_pilot_frame ORDER BY frame_idx, variant")
    assert sorted({r["frame_idx"] for r in rows}) == [f.idx for f in frames]
    if fps < 4:   # nguồn thưa hơn lưới: mọi frame tới EOF
        assert [f.idx for f in frames] == list(range(seconds * fps))
    for r in rows:
        t = f"{r['frame_idx'] / video.fps:.3f}"
        assert r["unit_id"] == f"{video.id}:{t}-{t}"
        body = s3.client.get_object(Bucket=s3.bucket, Key=r["object_key"])["Body"].read()
        assert body == jpegs[(r["variant"], r["frame_idx"])] and hashlib.sha256(body).hexdigest() == r["sha256"]
        assert r["size_bytes"] == len(body) and f"/frames/{fs[0]['id']}/{r['frame_idx']}/{r['variant'].lower()}/" in r["object_key"]
    assert sum(r["size_bytes"] for r in rows) == fs[0]["total_bytes"]
    assert receipts(pg) == {"ACCEPTED": 2 * len(frames) + 1}   # raw + mọi ảnh
    assert account(pg, a)["persisted_bytes"] == len(data) + fs[0]["total_bytes"]
    holds = pg.sql("SELECT state FROM rav_pilot_storage_hold WHERE job_id = %s AND upload_id IS NULL", (dto.validation_job_id,))
    assert holds and all(h["state"] == "FINAL" for h in holds)
    assert pg.sql("SELECT state FROM rav_pilot_resource_slot")[0]["state"] == "FREE"
    assert pg.sql("SELECT cleanup_done FROM rav_pilot_job_attempt")[0]["cleanup_done"] is True
    media = PilotMedia(PilotDB(pg.api_url), lambda: s3)
    unit = f"{video.id}:{frames[-1].t:.3f}-{frames[-1].t:.3f}"
    m = media.request(actor(a), MediaRequestIn(source_id=dto.source_id, unit_id=unit, variant="thumbnail"))
    assert m.state == "READY" and requests.get(m.url).content == jpegs[("THUMBNAIL", frames[-1].idx)]
    assert PilotDB(pg.api_url).get_source(actor(a), dto.source_id).frames.ready


def test_same_bytes_reuse_frames_without_new_objects(make):
    u = make()
    pg, a = u["pg"], u["a"]
    data = u["video"].read_bytes()
    _, first = uploaded(u, data, "u1")
    before = (account(pg, a)["persisted_bytes"], receipts(pg), len(frame_objects(u["s3"])))
    _, second = uploaded(u, data, "u2")
    assert second.state == "READY" and second.source_id == first.source_id
    assert (account(pg, a)["persisted_bytes"], receipts(pg), len(frame_objects(u["s3"]))) == before
    assert pg.sql("SELECT count(*)::int AS n FROM rav_pilot_frame_set")[0]["n"] == 1


def test_duplicate_of_source_without_frames_prepares_it(make):
    """Nguồn trùng nội dung chưa có bộ ảnh (vd nguồn cũ) → upload mới tách ảnh cho chính nguồn đó, không tạo nguồn thứ hai."""
    u = make()
    pg, a = u["pg"], u["a"]
    data = u["video"].read_bytes()
    _, first = uploaded(u, data, "u1")
    old = pg.sql("SELECT id, total_bytes FROM rav_pilot_frame_set")[0]
    pg.sql("UPDATE rav_pilot_frame_set SET state = 'DELETED' WHERE id = %s", (old["id"],))   # giả lập nguồn trước DEP7-Frames
    persisted = account(pg, a)["persisted_bytes"]
    _, second = uploaded(u, data, "u2")
    assert second.state == "READY" and second.source_id == first.source_id
    new = pg.sql("SELECT * FROM rav_pilot_frame_set WHERE state = 'READY'")
    assert len(new) == 1 and new[0]["id"] != old["id"] and new[0]["job_id"] == second.validation_job_id
    assert account(pg, a)["persisted_bytes"] == persisted + new[0]["total_bytes"]
    assert pg.sql("SELECT count(*)::int AS n FROM rav_pilot_source_asset")[0]["n"] == 1


def test_publish_race_loser_frames_cleaned_without_double_charge(make, monkeypatch):
    """Worker thấy chưa có bộ ảnh nhưng lúc publish đã có (race) → bộ thua không được nhận, cleanup rồi mới trả dung lượng."""
    u = make()
    pg, a, s3 = u["pg"], u["a"], u["s3"]
    data = u["video"].read_bytes()
    _, first = uploaded(u, data, "u1")
    winner = set(frame_objects(s3))
    persisted = account(pg, a)["persisted_bytes"]
    real, calls = pilot_frames.ready_set_in_tx, []

    def stale_first_check(cur, source_id, sha):
        calls.append(source_id)
        return None if len(calls) == 1 else real(cur, source_id, sha)
    monkeypatch.setattr(pilot_frames, "ready_set_in_tx", stale_first_check)
    _, second = uploaded(u, data, "u2")
    assert second.state == "READY" and second.source_id == first.source_id
    assert pg.sql("SELECT count(*)::int AS n FROM rav_pilot_frame_set")[0]["n"] == 1
    assert len(frame_objects(s3)) == 2 * len(winner)   # bộ thua đã PUT, chưa được nhận
    assert pg.sql("SELECT state FROM rav_pilot_resource_slot")[0]["state"] == "QUARANTINED"
    assert account(pg, a)["persisted_bytes"] == persisted and account(pg, a)["reserved_bytes"] > 2 * len(data)
    assert u["supervisor"].run_reconciliation()
    assert set(frame_objects(s3)) == winner
    assert account(pg, a) == {"persisted_bytes": persisted, "reserved_bytes": 2 * len(data)}   # chỉ còn staging của 2 upload
    assert receipts(pg) == {"ACCEPTED": len(winner) + 1, "DELETED": len(winner)}
    assert pg.sql("SELECT state FROM rav_pilot_resource_slot")[0]["state"] == "FREE"


def test_put_failure_mid_batch_publishes_nothing_and_cleans_up(make, monkeypatch):
    u = make()
    pg, a, s3 = u["pg"], u["a"], u["s3"]
    data = u["video"].read_bytes()
    real, count = s3.put_file, []

    def flaky(key, *args):
        count.append(key)
        if len(count) == 20:   # raw + 16 ảnh lô 1 + vài ảnh lô 2
            raise ServiceError("dependency_unavailable", "S3 lỗi", 503)
        return real(key, *args)
    monkeypatch.setattr(s3, "put_file", flaky)
    _, dto = uploaded(u, data, "u1")
    assert dto.state == "FAILED" and dto.source_id is None and dto.error["code"] == "dependency_unavailable"
    assert pg.sql("SELECT count(*)::int AS n FROM rav_pilot_frame_set")[0]["n"] == 0
    assert frame_objects(s3) and account(pg, a)["persisted_bytes"] == 0
    assert u["supervisor"].run_reconciliation()
    assert frame_objects(s3) == [] and set(receipts(pg)) == {"DELETED"}
    assert account(pg, a) == {"persisted_bytes": 0, "reserved_bytes": len(data)}   # chỉ còn staging chờ sweep
    assert pg.sql("SELECT state FROM rav_pilot_resource_slot")[0]["state"] == "FREE"


def test_cancel_between_batches_stops_and_cleans_up(make, monkeypatch):
    u = make()
    pg, a, s3 = u["pg"], u["a"], u["s3"]
    data = u["video"].read_bytes()
    created, job = start(u, data, "u1")
    api = PilotRuntime(PilotDB(pg.api_url))
    real = pilot_objects.written_batch

    def cancel_after_first(db, lease, keys):
        real(db, lease, keys)
        if pg.sql("SELECT state FROM rav_pilot_job WHERE id = %s", (job.validation_job_id,))[0]["state"] == "RUNNING":
            api.cancel(actor(a), job.validation_job_id, api.get_job(actor(a), job.validation_job_id).state_version)
    monkeypatch.setattr(pilot_objects, "written_batch", cancel_after_first)
    assert u["dispatch"].run_once()
    assert pg.sql("SELECT state FROM rav_pilot_job WHERE id = %s", (job.validation_job_id,))[0]["state"] == "CANCELLED"
    assert u["svc"].get(actor(a), created.id).state == "ABORTED"
    assert pg.sql("SELECT count(*)::int AS n FROM rav_pilot_frame_set")[0]["n"] == 0
    assert 0 < len(frame_objects(s3)) <= 16
    assert u["supervisor"].run_reconciliation()
    assert frame_objects(s3) == [] and account(pg, a) == {"persisted_bytes": 0, "reserved_bytes": len(data)}


def test_lease_lost_mid_frames_then_retry_publishes_once(make, monkeypatch):
    u = make()
    pg, a, s3 = u["pg"], u["a"], u["s3"]
    data = u["video"].read_bytes()
    created, job = start(u, data, "u1")
    runtime, ctx = u["dispatch"].runtime, u["dispatch"].worker_ctx
    lease = runtime.claim(ctx, "CPU")
    runtime.record_process(lease, identity())
    real = pilot_objects.written_batch

    def expire_after_first(db, lease_, keys):
        real(db, lease_, keys)
        pg.sql("UPDATE rav_pilot_job_attempt SET lease_until = now() - interval '1 minute' WHERE id = %s", (lease_.attempt_id,))
        runtime.recover_expired_leases()
    monkeypatch.setattr(pilot_objects, "written_batch", expire_after_first)
    handler = make_handlers(s3, PilotDB(pg.worker_url), u["tmp"] / "lost")["VALIDATE_UPLOAD"]
    with pytest.raises(ServiceError) as e:
        handler(lease, threading.Event())
    assert e.value.code == "lease_lost"
    runtime.record_stopped(lease, Usage(cpu_ms=10, gpu_ms=0, state="FINAL"))
    assert u["svc"].get(actor(a), created.id).state == "VALIDATING"
    assert pg.sql("SELECT count(*)::int AS n FROM rav_pilot_frame_set")[0]["n"] == 0
    lost_keys = set(frame_objects(s3))
    assert lost_keys
    monkeypatch.setattr(pilot_objects, "written_batch", real)
    assert u["supervisor"].run_reconciliation()   # xóa object attempt cũ rồi mới trả slot/hold
    assert not lost_keys & set(frame_objects(s3))
    assert u["dispatch"].run_once()   # attempt 2: frame_set mới, không ghi đè key cũ
    dto = u["svc"].get(actor(a), created.id)
    assert dto.state == "READY"
    fs = pg.sql("SELECT * FROM rav_pilot_frame_set")
    assert len(fs) == 1 and fs[0]["attempt_id"] != lease.attempt_id
    assert account(pg, a)["persisted_bytes"] == len(data) + fs[0]["total_bytes"]


def test_missing_frame_policy_blocks_complete(make):
    u = make(chunk_bounds_by_kind={"PROPOSE": {"max_units": 4000}})
    data = u["video"].read_bytes()
    with pytest.raises(ServiceError) as e:
        start(u, data, "u1")
    assert (e.value.code, e.value.status) == ("policy_unconfigured", 503)
    assert u["pg"].sql("SELECT count(*)::int AS n FROM rav_pilot_job")[0]["n"] == 0


@pytest.mark.parametrize("cfg,code", [
    ({"chunk_bounds_by_kind": {"VALIDATE_UPLOAD": {"frame_batch_size": 8, "frame_put_concurrency": 2, "max_frame_bytes": 1}}},
     "upload_invalid"),
    ({"storage_user_bytes": None}, "storage_exceeded"),
])
def test_frame_limits_fail_before_any_frame_object(make, cfg, code, tmp_path):
    data = make_video(tmp_path / "probe.mp4", seconds=4, fps=10).read_bytes()
    if cfg.get("storage_user_bytes", 1) is None:
        cfg = {"storage_user_bytes": 2 * len(data) + 100}   # raw + staging vừa đủ, lô ảnh đầu vượt
    u = make(**cfg)
    pg, a = u["pg"], u["a"]
    _, dto = uploaded(u, data, "u1")
    assert dto.state == "FAILED" and dto.error["code"] == code
    assert frame_objects(u["s3"]) == [] and pg.sql("SELECT count(*)::int AS n FROM rav_pilot_frame_set")[0]["n"] == 0
    u["supervisor"].run_reconciliation()   # raw canonical đã PUT nhưng không được nhận → cleanup
    assert account(pg, a) == {"persisted_bytes": 0, "reserved_bytes": len(data)}


def test_delete_source_removes_frames_in_batches_after_partial_failure(make, monkeypatch):
    """DELETE_SOURCE (§21.20): ảnh thuộc inventory; lỗi giữa lô giữ DELETE_PENDING, retry xóa nốt, trừ persisted một lần."""
    from src.rav.store import pilot_delete
    from src.rav.store.pilot_media import MediaRequestIn, PilotMedia

    u = make()
    pg, a, s3 = u["pg"], u["a"], u["s3"]
    monkeypatch.setattr(pilot_delete, "DELETE_BATCH", 5)
    _, dto = uploaded(u, u["video"].read_bytes(), "u1")
    keys = frame_objects(s3)
    assert len(keys) > 10
    media = PilotMedia(PilotDB(pg.api_url), lambda: s3)
    core = pg.sql("SELECT core_source_id FROM rav_pilot_source_asset")[0]["core_source_id"]
    thumb = media.request(actor(a), MediaRequestIn(source_id=dto.source_id, unit_id=f"{core}:0.000-0.000", variant="thumbnail"))
    rt = PilotRuntime(PilotDB(pg.api_url))
    req = rt.request_source_delete(actor(a), dto.source_id, 1, "delete")
    real, calls = s3.head, []

    def fail_third_batch(key):
        calls.append(key)
        if len(calls) == 12:
            raise ServiceError("storage_unavailable", "S3 lỗi", 503)
        return real(key)
    monkeypatch.setattr(s3, "head", fail_third_batch)
    assert u["dispatch"].run_once()
    assert rt.get_source_delete(actor(a), req.id).state == "FAILED"
    assert media.get(actor(a), thumb.id).state == "FAILED"   # nguồn DELETE_PENDING: không cấp URL ảnh mới
    receipt = pg.sql("SELECT receipt FROM rav_pilot_source_delete")[0]["receipt"]
    assert len(receipt["confirmed"]) == 10 and len(receipt["objects"]) == len(keys) + 1
    assert pg.sql("SELECT state FROM rav_pilot_frame_set")[0]["state"] == "READY"
    monkeypatch.setattr(s3, "head", real)
    retry = rt.request_source_delete(actor(a), dto.source_id, 2, "retry")
    assert u["dispatch"].run_once()
    assert rt.get_source_delete(actor(a), retry.id).state == "SUCCEEDED"
    assert frame_objects(s3) == [] and pg.sql("SELECT state FROM rav_pilot_frame_set")[0]["state"] == "DELETED"
    assert account(pg, a)["persisted_bytes"] == 0
    assert media.get(actor(a), thumb.id).state == "FAILED"   # nguồn DELETED: không cấp URL ảnh


def make_vfr_video(path, n=60, size=(64, 48)):
    """Video VFR thật: khoảng cách pts xen kẽ 50 ms / 150 ms (time_base 1/1000), mỗi frame một màu."""
    from fractions import Fraction

    import av
    import numpy as np

    w, h = size
    with av.open(str(path), "w") as out:
        stream = out.add_stream("mpeg4", rate=10)
        stream.width, stream.height, stream.pix_fmt = w, h, "yuv420p"
        stream.codec_context.time_base = Fraction(1, 1000)
        pts = 0
        for i in range(n):
            img = np.zeros((h, w, 3), np.uint8)
            img[:, :, 0] = (i * 4) % 256
            img[:, : (i % w) + 1, 1] = 200
            frame = av.VideoFrame.from_ndarray(img, format="rgb24")
            frame.pts, frame.time_base = pts, Fraction(1, 1000)
            out.mux(stream.encode(frame))
            pts += 50 if i % 2 else 150
        out.mux(stream.encode())
    return path


def test_vfr_upload_frames_follow_stream_index(make):
    """VFR (§21.20 gate): ordered IDs theo lưới decoder, ảnh khớp `read_exact` theo index stream thật, không suy từ timestamp."""
    u = make()
    pg = u["pg"]
    path = make_vfr_video(u["tmp"] / "vfr.mp4")
    import av
    with av.open(str(path)) as c:
        times = [float(f.pts * f.time_base) for f in c.decode(video=0)]
    gaps = {round(b - a, 3) for a, b in zip(times, times[1:])}
    assert len(gaps) > 1, gaps   # đúng là VFR
    _, dto = uploaded(u, path.read_bytes(), "vfr")
    assert dto.state == "READY"
    _, frames, jpegs = expected_jpegs(path)
    rows = pg.sql("SELECT variant, frame_idx, sha256 FROM rav_pilot_frame")
    assert len(rows) == 2 * len(frames) and sorted({r["frame_idx"] for r in rows}) == [f.idx for f in frames]
    assert all(r["sha256"] == hashlib.sha256(jpegs[(r["variant"], r["frame_idx"])]).hexdigest() for r in rows)
