"""S1 — migration SQLite giữ dữ liệu (CONTRACTS §5 dòng 13, §14.2; #64): backup, nâng v1 → v2, từ chối DB mới hơn, rollback khi lỗi,
snapshot bất biến bằng trigger."""

import hashlib
import json
import sqlite3
from contextlib import closing

import pytest

from src.rav.store import db as dbmod
from src.rav.store.db import DB, SCHEMA_V1, SCHEMA_VERSION, migrate

TABLES_V1 = ("session", "decision", "proposal")


def make_v1(path):
    """DB đúng như bản app cũ tạo ra (schema v1, user_version = 1) kèm một phiên, ba quyết định, một proposal."""
    with closing(sqlite3.connect(path)) as c, c:
        c.executescript(SCHEMA_V1)
        c.execute("PRAGMA user_version = 1")
        spec = {"pool": {"video_ids": ["v1"]}, "goal": "g", "target_k": 5}
        c.execute("INSERT INTO session VALUES ('s1', 'phiên', '2026-10-01T00:00:00+00:00', ?, 10)", (json.dumps(spec),))
        for uid, action in (("v1:0.000-0.000", "keep"), ("v1:0.250-0.250", "drop"), ("v1:0.500-0.500", "keep")):
            c.execute("INSERT INTO decision (session_id, unit_id, action, actor, goal, proposal_n, decided_at, shown_at, reason,"
                      " confidence, extra) VALUES ('s1', ?, ?, 'human:local', 'g', 1, '2026-10-01T00:01:00+00:00', NULL, NULL, NULL, '{}')",
                      (uid, action))
        c.execute("INSERT INTO proposal VALUES ('s1', 1, 1, '2026-10-01T00:00:30+00:00', '{\"recipe\": \"diversity\"}', '[]', 0)")


def dump(path, tables=TABLES_V1):
    with closing(sqlite3.connect(path)) as c:
        return {t: c.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in tables}


def version(path):
    with closing(sqlite3.connect(path)) as c:
        return c.execute("PRAGMA user_version").fetchone()[0]


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_v1_upgrade_keeps_every_row_and_writes_backup(tmp_path):
    path = tmp_path / "rav.sqlite"
    make_v1(path)
    before = dump(path)

    db = DB(path)

    assert version(path) == SCHEMA_VERSION == 4
    assert db.backup is not None and db.backup.parent == tmp_path and db.backup.name.startswith("rav.sqlite.v1-")
    assert dump(db.backup) == before and version(db.backup) == 1          # backup là bản v1 nguyên vẹn
    assert dump(path) == before                                            # migration không ghi lại dòng cũ
    assert [d.action for d in db.decisions("s1")] == ["keep", "drop", "keep"]
    assert db.get_session("s1")["spec"]["target_k"] == 5 and len(db.proposals("s1")) == 1
    assert DB(path).backup is None                                         # mở lại: đã mới nhất, không backup thêm


def test_fresh_db_is_created_at_latest_version_without_backup(tmp_path):
    path = tmp_path / "out" / "rav.sqlite"
    db = DB(path)
    assert db.backup is None and version(path) == SCHEMA_VERSION
    assert list(tmp_path.joinpath("out").glob("*.bak")) == []
    with closing(sqlite3.connect(path)) as c:
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"session", "decision", "proposal", "snapshot", "snapshot_member"} <= names


def test_newer_db_is_refused_untouched(tmp_path):
    path = tmp_path / "rav.sqlite"
    make_v1(path)
    with closing(sqlite3.connect(path)) as c:
        c.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    h = file_hash(path)
    with pytest.raises(RuntimeError, match="mới hơn app"):
        DB(path)
    assert file_hash(path) == h and list(tmp_path.glob("*.bak")) == []


def test_failed_migration_rolls_back(tmp_path):
    path = tmp_path / "rav.sqlite"
    make_v1(path)
    before = dump(path)
    broken = [*dbmod.MIGRATIONS, (SCHEMA_VERSION + 1, "CREATE TABLE extra (x INTEGER); INSERT INTO no_such_table VALUES (1);")]
    with pytest.raises(RuntimeError, match="DB giữ nguyên"):
        migrate(path, broken)
    assert version(path) == 1 and dump(path) == before                    # v2 (thành công trong cùng lô) cũng không được giữ
    with closing(sqlite3.connect(path)) as c:
        assert c.execute("SELECT count(*) FROM sqlite_master WHERE name IN ('snapshot', 'extra')").fetchone()[0] == 0
    assert len(list(tmp_path.glob("*.bak"))) == 1                          # backup đã tạo trước khi nâng


def test_snapshot_rows_are_immutable(tmp_path):
    path = tmp_path / "rav.sqlite"
    make_v1(path)
    DB(path)
    with closing(sqlite3.connect(path)) as c, c:
        c.execute("INSERT INTO snapshot VALUES ('x', 's1', 1, 't', 'human:local', NULL, 3, 'mh', 1, '{}', 'ch')")
        c.execute("INSERT INTO snapshot_member VALUES ('x', 0, 'v1:0.000-0.000', 'video', 'v1', '{}')")
    for sql in ("UPDATE snapshot SET note = 'sửa'", "DELETE FROM snapshot",
                "UPDATE snapshot_member SET unit_id = 'khác'", "DELETE FROM snapshot_member"):
        with closing(sqlite3.connect(path)) as c, pytest.raises(sqlite3.DatabaseError, match="immutable"):
            c.execute(sql)
    assert dump(path, ("snapshot", "snapshot_member")) == {
        "snapshot": [("x", "s1", 1, "t", "human:local", None, 3, "mh", 1, "{}", "ch")],
        "snapshot_member": [("x", 0, "v1:0.000-0.000", "video", "v1", "{}")]}
