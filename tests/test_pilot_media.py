"""Media riêng tư pilot (DEP4 #91, §21.20): Postgres thật + S3 moto; thumbnail/frame từ ảnh lưu sẵn, context qua RENDER_MEDIA thật.

Chạy: đặt `RAV_PILOT_TEST_DATABASE_URL` (tests/conftest.py) rồi `python -m pytest tests/test_pilot_media.py -q`.
"""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import pytest
import requests

from src.rav.actor import ActorContext
from src.rav.errors import ServiceError
from src.rav.store.pilot_db import DEFAULT_SAMPLE_SPEC, PilotDB
from src.rav.store.pilot_frames import ENCODING_VERSION
from src.rav.store.pilot_media import MediaRequestIn, PilotMedia
from src.rav.store.pilot_runtime import PilotRuntime, WorkerContext
from src.rav.worker.dispatcher import PilotDispatcher
from src.rav.worker.handlers import _decoder, _probe, make_handlers
from tests.conftest import POLICY_CONFIG, make_video


def actor(uid) -> ActorContext:
    return ActorContext(user_id=uid)


def seed_source(pg, s3, path, owner):
    """Nguồn READY như sau VALIDATE_UPLOAD/import: raw trên S3 + metadata decoder."""
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    video = _probe(path)
    sid = uuid4()
    key = f"pilot-test/private/{owner}/{sid}/raw/{sha}/video.mp4" if owner else f"pilot-test/system/t/sources/{video.id}/raw/{sha}/video.mp4"
    s3.put_file(key, path, sha, "video/mp4")
    meta = {"width": video.meta["width"], "height": video.meta["height"], "duration_ms": round(video.duration_s * 1000),
            "n_frames": video.meta["n_frames"], "n_units": len(_decoder().frames(video)), "fps": video.fps}
    pg.sql("INSERT INTO rav_pilot_source_asset (id, core_source_id, kind, scope, owner_id, protected, name, raw_key, raw_sha256, "
           "size_bytes, metadata, availability) VALUES (%s, %s, 'VIDEO', %s, %s, %s, 'v.mp4', %s, %s, %s, %s, 'READY')",
           (sid, video.id, "PRIVATE" if owner else "SYSTEM", owner, owner is None, key, sha, path.stat().st_size, json.dumps(meta)))
    frames = _decoder().frames(video)
    return sid, video.id, frames


def seed_frames(pg, s3, sid, owner, path, frames, *, encoding=None, raw_sha=None):
    """Frame set READY đủ bộ như sau publish (§21.20): JPEG từ exact anchor của decoder core; trả (set id, {(variant, idx): row})."""
    from io import BytesIO

    from PIL import Image

    video = _probe(path)
    set_id, total, rows = uuid4(), 0, {}
    for f in frames:
        for variant in ("THUMBNAIL", "FRAME"):
            img = Image.fromarray(_decoder().read(video, f))
            if variant == "THUMBNAIL":
                img.thumbnail((320, 320))
            buf = BytesIO()
            img.save(buf, "JPEG", quality=85)
            data = buf.getvalue()
            sha = hashlib.sha256(data).hexdigest()
            base = f"pilot-test/private/{owner}/frames" if owner else f"pilot-test/system/t/frames/{video.id}"
            key = f"{base}/{set_id}/{f.idx}/{variant.lower()}/{sha}.jpg"
            tmp = path.parent / f"{set_id}-{f.idx}-{variant}.jpg"
            tmp.write_bytes(data)
            s3.put_file(key, tmp, sha, "image/jpeg")
            rows[(variant, f.idx)] = {"id": uuid4(), "key": key, "sha": sha, "size": len(data), "w": img.width, "h": img.height}
            total += len(data)
    pg.sql("INSERT INTO rav_pilot_frame_set (id, scope, owner_id, source_id, raw_sha256, sample_spec_hash, encoding_version, n_units, "
           "object_count, total_bytes, state) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'READY')",
           (set_id, "PRIVATE" if owner else "SYSTEM", owner, sid, raw_sha or hashlib.sha256(path.read_bytes()).hexdigest(),
            DEFAULT_SAMPLE_SPEC["spec_hash"], encoding or ENCODING_VERSION, len(frames), 2 * len(frames), total))
    for (variant, idx), r in rows.items():
        t = f"{idx / video.fps:.3f}"
        pg.sql("INSERT INTO rav_pilot_frame (id, frame_set_id, variant, frame_idx, unit_id, object_key, sha256, size_bytes, width, height) "
               "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
               (r["id"], set_id, variant, idx, f"{video.id}:{t}-{t}", r["key"], r["sha"], r["size"], r["w"], r["h"]))
    return set_id, rows


def unit(core, frame):
    return f"{core}:{frame.t:.3f}-{frame.t:.3f}"


@pytest.fixture
def md(pg, s3_storage, tmp_path):
    policy = pg.policy(config=dict(POLICY_CONFIG, max_media_requests=3))
    a, b = pg.profile(policy_id=policy), pg.profile(policy_id=policy)
    pg.slot("media-1", "MEDIA")
    worker = PilotRuntime(PilotDB(pg.worker_url))
    d = PilotDispatcher(worker, WorkerContext(worker_id=uuid4(), host_boot_id=uuid4()), "MEDIA", heartbeat_s=0.2)
    for kind, fn in make_handlers(s3_storage, PilotDB(pg.worker_url), tmp_path / "work").items():
        d.register_handler(kind, fn)
    path = make_video(tmp_path / "v.mp4", seconds=8, fps=10, size=(128, 96))
    sid, core, frames = seed_source(pg, s3_storage, path, a)
    sys_path = make_video(tmp_path / "s.mp4", seconds=2, shift=50)
    sys_sid, sys_core, sys_frames = seed_source(pg, s3_storage, sys_path, None)
    return {"pg": pg, "a": a, "b": b, "svc": PilotMedia(PilotDB(pg.api_url), lambda: s3_storage), "dispatch": d, "s3": s3_storage,
            "sid": sid, "core": core, "frames": frames, "sys": (sys_sid, sys_core, sys_frames), "path": path, "sys_path": sys_path}


def fetch(url: str) -> bytes:
    r = requests.get(url)
    assert r.status_code == 200
    return r.content


def jpeg_size(body: bytes) -> tuple[int, int]:
    from io import BytesIO

    from PIL import Image
    assert body[:2] == b"\xff\xd8"
    return Image.open(BytesIO(body)).size


def test_thumbnail_without_frames_is_not_prepared_and_never_renders(md):
    svc, a, pg = md["svc"], md["a"], md["pg"]
    for variant in ("thumbnail", "frame"):
        dto = svc.request(actor(a), MediaRequestIn(source_id=md["sid"], unit_id=unit(md["core"], md["frames"][5]), variant=variant))
        assert (dto.state, dto.id, dto.job_id, dto.url) == ("NOT_PREPARED", None, None, None)
    assert pg.sql("SELECT count(*) AS n FROM rav_pilot_job")[0]["n"] == 0
    assert pg.sql("SELECT count(*) AS n FROM rav_pilot_media")[0]["n"] == 0
    assert not PilotDB(pg.api_url).get_source(actor(a), md["sid"]).frames.ready


def test_stored_thumbnail_and_frame_served_without_job(md):
    svc, a, pg = md["svc"], md["a"], md["pg"]
    f = md["frames"][5]
    _, rows = seed_frames(pg, md["s3"], md["sid"], a, md["path"], md["frames"])
    assert PilotDB(pg.api_url).get_source(actor(a), md["sid"]).frames.ready
    cases = (("thumbnail", None, "THUMBNAIL"), ("thumbnail", 64, "THUMBNAIL"), ("thumbnail", 320, "THUMBNAIL"),
             ("thumbnail", 321, "FRAME"), ("frame", None, "FRAME"), ("frame", 100, "FRAME"))
    for variant, side, stored in cases:
        dto = svc.request(actor(a), MediaRequestIn(source_id=md["sid"], unit_id=unit(md["core"], f), variant=variant, max_side=side))
        row = rows[(stored, f.idx)]
        assert (dto.state, dto.id, dto.job_id, dto.sha256) == ("READY", row["id"], None, row["sha"]), (variant, side)
        body = fetch(dto.url)
        assert hashlib.sha256(body).hexdigest() == row["sha"] and dto.expires_at
        assert jpeg_size(body) == (row["w"], row["h"])
    assert jpeg_size(fetch(svc.get(actor(a), rows[("FRAME", f.idx)]["id"]).url)) == (128, 96)
    assert jpeg_size(fetch(svc.get(actor(a), rows[("THUMBNAIL", f.idx)]["id"]).url)) == (128, 96)   # nguồn nhỏ hơn 320
    assert pg.sql("SELECT count(*) AS n FROM rav_pilot_job")[0]["n"] == 0
    assert pg.sql("SELECT count(*) AS n FROM rav_pilot_media")[0]["n"] == 0


def test_stored_lookup_normalizes_variant_before_sql(md):
    """Direct lookups and API batch must select the same actual stored frame, for either case."""
    from src.rav.store.pilot_frames import lookup_in_tx

    f = md["frames"][5]
    _, rows = seed_frames(md["pg"], md["s3"], md["sid"], md["a"], md["path"], md["frames"])
    with PilotDB(md["pg"].api_url).backend() as cur:
        for variant in ("frame", "FRAME", "thumbnail", "THUMBNAIL"):
            got = lookup_in_tx(cur, {"id": md["sid"]}, variant, f.idx)
            expected = rows[(variant.upper(), f.idx)]
            assert (got["id"], got["object_key"], got["sha256"]) == (expected["id"], expected["key"], expected["sha"])


def test_frame_set_must_match_raw_and_encoding(md):
    svc, a, pg = md["svc"], md["a"], md["pg"]
    seed_frames(pg, md["s3"], md["sid"], a, md["path"], md["frames"][:2], encoding="0" * 16)
    req = MediaRequestIn(source_id=md["sid"], unit_id=unit(md["core"], md["frames"][1]), variant="thumbnail")
    assert svc.request(actor(a), req).state == "NOT_PREPARED"
    seed_frames(pg, md["s3"], md["sid"], a, md["path"], md["frames"][:2], raw_sha="f" * 64)
    assert svc.request(actor(a), req).state == "NOT_PREPARED"
    assert not PilotDB(pg.api_url).get_source(actor(a), md["sid"]).frames.ready


def test_one_ready_frame_set_per_source_content_spec(md):
    import psycopg

    pg, a = md["pg"], md["a"]
    seed_frames(pg, md["s3"], md["sid"], a, md["path"], md["frames"][:1])
    with pytest.raises(psycopg.errors.UniqueViolation):
        seed_frames(pg, md["s3"], md["sid"], a, md["path"], md["frames"][:1])


def test_frame_tables_closed_to_authenticated(md):
    import psycopg

    pg, a = md["pg"], md["a"]
    seed_frames(pg, md["s3"], md["sid"], a, md["path"], md["frames"][:1])
    for table in ("rav_pilot_frame_set", "rav_pilot_frame"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), PilotDB(pg.api_url).as_user(a) as cur:
            cur.execute(f"SELECT 1 FROM {table}")
    with psycopg.connect(pg.api_url) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute("UPDATE rav_pilot_frame_set SET state = 'DELETED'")


def test_context_clip_is_mp4(md):
    svc, a = md["svc"], md["a"]
    m = svc.request(actor(a), MediaRequestIn(source_id=md["sid"], unit_id=unit(md["core"], md["frames"][10]), variant="context"))
    assert m.state == "PENDING" and m.job_id
    md["dispatch"].run_once()
    body = fetch(svc.get(actor(a), m.id).url)
    assert body[4:8] == b"ftyp"


def test_unit_must_belong_to_source_grid(md):
    svc, a, core = md["svc"], md["a"], md["core"]
    for bad in (f"{core}:0.050-0.050", f"{core}:999.000-999.000", f"other:{md['frames'][0].t:.3f}-{md['frames'][0].t:.3f}",
                f"{core}:0.000-0.250", "garbage"):
        with pytest.raises(ServiceError) as e:
            svc.request(actor(a), MediaRequestIn(source_id=md["sid"], unit_id=bad, variant="thumbnail"))
        assert e.value.code == "unit_not_in_source", bad


def test_scope_isolation_and_system_source(md):
    svc, a, b, pg = md["svc"], md["a"], md["b"], md["pg"]
    seed_frames(pg, md["s3"], md["sid"], a, md["path"], md["frames"][:1])
    req = MediaRequestIn(source_id=md["sid"], unit_id=unit(md["core"], md["frames"][0]), variant="thumbnail")
    with pytest.raises(ServiceError) as e:
        svc.request(actor(b), req)
    assert e.value.status == 404
    m = svc.request(actor(a), req)
    assert m.state == "READY"
    clip = svc.request(actor(a), MediaRequestIn(source_id=md["sid"], unit_id=req.unit_id, variant="context"))
    for media_id in (m.id, clip.id):
        with pytest.raises(ServiceError) as e:
            svc.get(actor(b), media_id)
        assert e.value.status == 404
    sys_sid, sys_core, sys_frames = md["sys"]
    sys_req = MediaRequestIn(source_id=sys_sid, unit_id=unit(sys_core, sys_frames[1]), variant="thumbnail")
    assert svc.request(actor(b), sys_req).state == "NOT_PREPARED"
    _, rows = seed_frames(pg, md["s3"], sys_sid, None, md["sys_path"], sys_frames)
    for user in (a, b):
        dto = svc.request(actor(user), sys_req)
        assert dto.state == "READY" and dto.id == rows[("THUMBNAIL", sys_frames[1].idx)]["id"]
        assert svc.get(actor(user), dto.id).url
        assert PilotDB(pg.api_url).get_source(actor(user), sys_sid).frames.ready


def test_no_url_after_source_delete_pending(md):
    svc, a, pg = md["svc"], md["a"], md["pg"]
    seed_frames(pg, md["s3"], md["sid"], a, md["path"], md["frames"][2:3])
    m = svc.request(actor(a), MediaRequestIn(source_id=md["sid"], unit_id=unit(md["core"], md["frames"][2]), variant="thumbnail"))
    clip = svc.request(actor(a), MediaRequestIn(source_id=md["sid"], unit_id=unit(md["core"], md["frames"][2]), variant="context"))
    md["dispatch"].run_once()
    assert svc.get(actor(a), m.id).url and svc.get(actor(a), clip.id).url
    pg.sql("UPDATE rav_pilot_source_asset SET availability = 'DELETE_PENDING' WHERE id = %s", (md["sid"],))
    for media_id in (m.id, clip.id):
        dto = svc.get(actor(a), media_id)
        assert dto.url is None and dto.state == "FAILED" and dto.error["code"] == "source_unavailable"
    with pytest.raises(ServiceError) as e:
        svc.request(actor(a), MediaRequestIn(source_id=md["sid"], unit_id=unit(md["core"], md["frames"][3]), variant="thumbnail"))
    assert e.value.code == "source_unavailable"


def test_media_limit_is_separate_from_heavy_jobs(md):
    svc, a = md["svc"], md["a"]
    for i in range(3):
        svc.request(actor(a), MediaRequestIn(source_id=md["sid"], unit_id=unit(md["core"], md["frames"][i]), variant="context"))
    with pytest.raises(ServiceError) as e:
        svc.request(actor(a), MediaRequestIn(source_id=md["sid"], unit_id=unit(md["core"], md["frames"][4]), variant="context"))
    assert (e.value.code, e.value.status) == ("quota_exceeded", 429)


def test_failed_render_can_be_requested_again(md):
    svc, a, pg = md["svc"], md["a"], md["pg"]
    req = MediaRequestIn(source_id=md["sid"], unit_id=unit(md["core"], md["frames"][6]), variant="context")
    m = svc.request(actor(a), req)
    md["dispatch"]._handlers.pop("RENDER_MEDIA")
    md["dispatch"].run_once()
    failed = svc.get(actor(a), m.id)
    assert failed.state == "FAILED" and failed.error["code"] == "handler_unavailable"
    retry = svc.request(actor(a), req)
    assert retry.id == m.id and retry.state == "PENDING" and retry.job_id != m.job_id
    assert pg.sql("SELECT count(*) AS n FROM rav_pilot_job WHERE kind = 'RENDER_MEDIA'")[0]["n"] == 2
