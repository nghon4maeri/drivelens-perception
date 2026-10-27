"""S2 — snapshot bất biến của collection (CONTRACTS §14.2, #64): đúng kept lúc chốt, idempotent theo hai hash, không đổi khi collection /
pool đổi, không sinh ảnh, lỗi chuẩn qua HTTP.

Dùng lại video PyAV + field giả của test_p5a (không chạy DINOv2).
"""

import hashlib
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from src.rav.api import create_app
from src.rav.commands import ServiceError
from src.rav.service.snapshot import members_hash

from .test_p5a import app, new_session, run, video_id  # noqa: F401  (fixture `app`)


def keep(app, sid, ids, action="keep"):  # noqa: F811
    return run(app, "update_state", session_id=sid, changes=[{"unit_id": u, "action": action, "proposal_n": 1} for u in ids])


def files(root):
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and not p.name.startswith("rav.sqlite")}


def test_snapshot_freezes_kept_and_is_idempotent(app):  # noqa: F811
    s = new_session(app)
    p = run(app, "propose", session_id=s.id, recipe="diversity", batch_size=4)
    ids = [x.unit_id for x in p.picks]
    before_files = files(app.output_dir)
    keep(app, s.id, ids[:3])

    snap = run(app, "create_snapshot", session_id=s.id, note="lần 1")
    assert snap.created and snap.n == 1 and snap.n_members == 3 and snap.is_current and snap.decisions_after == 0
    assert snap.members_hash == members_hash(ids[:3])
    assert files(app.output_dir) == before_files                       # keep + chốt không sinh ảnh / file nào

    detail = run(app, "get_snapshot", snapshot_id=snap.id)
    units = {u.id: u for u in run(app, "list_units", session_id=s.id).root}
    assert [m.unit_id for m in detail.members] == sorted(ids[:3], key=lambda u: (units[u].video_id, units[u].t0))
    m = detail.members[0]
    video = app.videos_dir / "a.mp4"
    assert m.source_kind == "video" and m.source_id == video_id(app, "a.mp4")
    assert m.provenance["video"] == {"id": m.source_id, "name": "a.mp4", "size_bytes": video.stat().st_size,
                                     "sha256": hashlib.sha256(video.read_bytes()).hexdigest()}
    assert m.provenance["decision"]["actor"] == "human:local" and m.provenance["decision"]["proposal_n"] == 1
    assert m.provenance["proposal"]["recipe"] == "diversity" and isinstance(m.provenance["frame_idx"], int)
    assert detail.context["session"]["reference"] == {"dataset_version_ids": []}
    assert detail.context["sample_spec"]["decoder"] == {"type": "pyav", "params": {"fps": 4}}

    again = run(app, "create_snapshot", session_id=s.id)
    assert not again.created and again.id == snap.id                   # không đổi gì → trả snapshot cũ
    keep(app, s.id, [ids[3]], "drop")                                  # drop frame chưa từng keep: collection không đổi
    same = run(app, "create_snapshot", session_id=s.id)
    assert same.id == snap.id and same.decisions_after == 1 and same.is_current


def test_old_snapshot_unchanged_when_collection_or_pool_changes(app):  # noqa: F811
    s = new_session(app)
    ids = [x.unit_id for x in run(app, "propose", session_id=s.id, recipe="diversity", batch_size=4).picks]
    keep(app, s.id, ids[:2])
    first = run(app, "get_snapshot", snapshot_id=run(app, "create_snapshot", session_id=s.id).id)

    keep(app, s.id, [ids[2]])                                          # collection đổi
    assert not run(app, "get_snapshot", snapshot_id=first.id).is_current
    second = run(app, "create_snapshot", session_id=s.id)
    assert second.created and second.n == 2 and second.n_members == 3

    run(app, "add_videos_to_session", session_id=s.id, video_ids=[video_id(app, "b.mp4")])   # chỉ context đổi
    assert not run(app, "get_snapshot", snapshot_id=second.id).is_current
    third = run(app, "create_snapshot", session_id=s.id)
    assert third.created and third.n == 3 and third.members_hash == second.members_hash and third.context_hash != second.context_hash

    old = run(app, "get_snapshot", snapshot_id=first.id)
    assert (old.members, old.context, old.members_hash, old.context_hash) == (first.members, first.context, first.members_hash, first.context_hash)
    assert [x.n for x in run(app, "list_snapshots", session_id=s.id).root] == [3, 2, 1]
    with closing(sqlite3.connect(app.output_dir / "rav.sqlite")) as c, pytest.raises(sqlite3.DatabaseError, match="immutable"):
        c.execute("UPDATE snapshot_member SET unit_id = 'x' WHERE snapshot_id = ?", (first.id,))


def test_empty_collection_and_conflict(app, monkeypatch):  # noqa: F811
    s = new_session(app)
    with pytest.raises(ServiceError) as e:
        run(app, "create_snapshot", session_id=s.id)
    assert (e.value.code, e.value.status) == ("empty_collection", 422)

    ids = [x.unit_id for x in run(app, "propose", session_id=s.id, recipe="diversity", batch_size=2).picks]
    keep(app, s.id, ids[:1])
    real = app.db.decision_rows

    def stale(sid):                                                    # giả lập: có quyết định ghi xen giữa lúc đọc và lúc ghi snapshot
        rows = real(sid)
        monkeypatch.setattr(app.db, "decision_rows", real)             # chỉ xen một lần
        keep(app, sid, ids[1:2])
        monkeypatch.setattr(app.db, "decision_rows", stale)
        return rows

    monkeypatch.setattr(app.db, "decision_rows", stale)
    with pytest.raises(ServiceError) as e:
        run(app, "create_snapshot", session_id=s.id)
    assert (e.value.code, e.value.status) == ("collection_changed", 409)
    monkeypatch.undo()
    assert run(app, "list_snapshots", session_id=s.id).root == []


def test_snapshot_routes(app):  # noqa: F811
    client = TestClient(create_app(app), raise_server_exceptions=False)
    s = new_session(app)
    res = client.post(f"/api/v1/sessions/{s.id}/snapshots", json={})
    assert res.status_code == 422 and res.json()["error"]["code"] == "empty_collection"
    ids = [x.unit_id for x in run(app, "propose", session_id=s.id, recipe="diversity", batch_size=2).picks]
    keep(app, s.id, ids)
    created = client.post(f"/api/v1/sessions/{s.id}/snapshots", json={"note": "gửi gán nhãn"}).json()
    assert created["created"] and created["note"] == "gửi gán nhãn"
    listed = client.get(f"/api/v1/sessions/{s.id}/snapshots").json()
    assert [x["id"] for x in listed] == [created["id"]] and listed[0]["is_current"]
    detail = client.get(f"/api/v1/snapshots/{created['id']}").json()
    assert len(detail["members"]) == 2 and detail["context"]["format"] == 1
    res = client.get("/api/v1/snapshots/khong-co")
    assert res.status_code == 404 and res.json()["error"]["code"] == "snapshot_not_found"
