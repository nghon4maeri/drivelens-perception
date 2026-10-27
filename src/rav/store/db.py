"""SQLite (`sqlite3` chuẩn, không ORM): session, decision (schema CONTRACTS §12.3), proposal (kèm snapshot input) — quyết định #46.

- `session`: `spec` = session spec JSON (CONTRACTS §7: source, pool, decoder, units, feedback, goal, target_k), cố định khi tạo.
- `decision`: log quyết định, **chỉ ghi thêm**; `review_state` = replay theo `seq`.
- `proposal`: `snapshot` = input đã chạy (recipe, config, config_hash, kept_ids, dropped_ids, batch_size, seed, fields_used), `picks` JSON.
- `dataset`, `dataset_version`, `dataset_member` (v3): nguồn ảnh, manifest/version bất biến, nhãn YOLO (#65).
- `snapshot`, `snapshot_member` (v2): snapshot bất biến của collection, chỉ tham chiếu tới raw (CONTRACTS §14, #64).
- `model_checkpoint` (v4): metadata checkpoint bất biến; bytes trong kho model theo SHA (§16, #70).
Mỗi thao tác mở một kết nối riêng (route FastAPI chạy ở nhiều luồng).
Schema đổi bằng migration theo `PRAGMA user_version` (`migrate`): sao lưu file trước khi nâng, không bao giờ xoá DB (#64).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

from ..core.types import Decision

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS session (
    id TEXT PRIMARY KEY, name TEXT, created_at TEXT NOT NULL, spec TEXT NOT NULL, n_units INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS decision (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES session(id), unit_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('keep', 'drop', 'clear')), actor TEXT NOT NULL, goal TEXT NOT NULL,
    proposal_n INTEGER, decided_at TEXT NOT NULL, shown_at TEXT, reason TEXT, confidence REAL, extra TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS proposal (
    session_id TEXT NOT NULL REFERENCES session(id), n INTEGER NOT NULL, round INTEGER NOT NULL, created_at TEXT NOT NULL,
    snapshot TEXT NOT NULL, picks TEXT NOT NULL, relaxed INTEGER NOT NULL, PRIMARY KEY (session_id, n));
"""

# v2: snapshot bất biến của collection (CONTRACTS §14.2, #64) — trigger chặn sửa / xoá
SQL_V2 = """
CREATE TABLE snapshot (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES session(id), n INTEGER NOT NULL, created_at TEXT NOT NULL,
    created_by TEXT NOT NULL, note TEXT, decision_seq INTEGER NOT NULL, members_hash TEXT NOT NULL, n_members INTEGER NOT NULL,
    context TEXT NOT NULL, context_hash TEXT NOT NULL, UNIQUE (session_id, n));
CREATE TABLE snapshot_member (
    snapshot_id TEXT NOT NULL REFERENCES snapshot(id), ord INTEGER NOT NULL, unit_id TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('video', 'image_set')), source_id TEXT NOT NULL, provenance TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, ord), UNIQUE (snapshot_id, unit_id));
CREATE TRIGGER snapshot_no_update BEFORE UPDATE ON snapshot BEGIN SELECT RAISE(ABORT, 'snapshot is immutable'); END;
CREATE TRIGGER snapshot_no_delete BEFORE DELETE ON snapshot BEGIN SELECT RAISE(ABORT, 'snapshot is immutable'); END;
CREATE TRIGGER snapshot_member_no_update BEFORE UPDATE ON snapshot_member BEGIN SELECT RAISE(ABORT, 'snapshot is immutable'); END;
CREATE TRIGGER snapshot_member_no_delete BEFORE DELETE ON snapshot_member BEGIN SELECT RAISE(ABORT, 'snapshot is immutable'); END;
"""

# (version đích, SQL nâng từ version liền trước). Chỉ thêm vào cuối; không sửa migration đã phát hành.
SQL_V3 = """
CREATE TABLE dataset (
    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, description TEXT);
CREATE TABLE dataset_version (
    id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES dataset(id), n INTEGER NOT NULL, created_at TEXT NOT NULL,
    created_by TEXT NOT NULL, folder TEXT NOT NULL, manifest_hash TEXT NOT NULL, manifest TEXT NOT NULL,
    annotation_format TEXT NOT NULL CHECK (annotation_format IN ('none', 'yolo')), classes TEXT,
    n_images INTEGER NOT NULL, import_report TEXT NOT NULL, UNIQUE (dataset_id, n), UNIQUE (dataset_id, manifest_hash));
CREATE TABLE dataset_member (
    version_id TEXT NOT NULL REFERENCES dataset_version(id), sample_id TEXT NOT NULL, key TEXT NOT NULL,
    relpath TEXT NOT NULL, sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
    label_status TEXT NOT NULL CHECK (label_status IN ('none', 'labeled', 'empty', 'missing', 'invalid', 'conflict')),
    label_relpath TEXT, label_sha256 TEXT, label_counts TEXT,
    PRIMARY KEY (version_id, sample_id), UNIQUE (version_id, relpath));
CREATE TRIGGER dataset_version_no_update BEFORE UPDATE ON dataset_version BEGIN SELECT RAISE(ABORT, 'dataset version is immutable'); END;
CREATE TRIGGER dataset_version_no_delete BEFORE DELETE ON dataset_version BEGIN SELECT RAISE(ABORT, 'dataset version is immutable'); END;
CREATE TRIGGER dataset_member_no_update BEFORE UPDATE ON dataset_member BEGIN SELECT RAISE(ABORT, 'dataset member is immutable'); END;
CREATE TRIGGER dataset_member_no_delete BEFORE DELETE ON dataset_member BEGIN SELECT RAISE(ABORT, 'dataset member is immutable'); END;
CREATE TRIGGER dataset_no_delete BEFORE DELETE ON dataset BEGIN SELECT RAISE(ABORT, 'dataset is immutable'); END;
CREATE TRIGGER dataset_identity_no_update BEFORE UPDATE OF id, created_at ON dataset
    BEGIN SELECT RAISE(ABORT, 'dataset identity is immutable'); END;
"""

SQL_V4 = """
CREATE TABLE model_checkpoint (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, filename TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE,
    size_bytes INTEGER NOT NULL CHECK (size_bytes > 0), format TEXT NOT NULL CHECK (format = 'pytorch_zip'),
    created_at TEXT NOT NULL);
CREATE TRIGGER model_checkpoint_no_update BEFORE UPDATE ON model_checkpoint
    BEGIN SELECT RAISE(ABORT, 'model checkpoint is immutable'); END;
CREATE TRIGGER model_checkpoint_no_delete BEFORE DELETE ON model_checkpoint
    BEGIN SELECT RAISE(ABORT, 'model checkpoint is immutable'); END;
"""

MIGRATIONS: list[tuple[int, str]] = [(2, SQL_V2), (3, SQL_V3), (4, SQL_V4)]
SCHEMA_VERSION = MIGRATIONS[-1][0]


def migrate(path: Path, migrations: list[tuple[int, str]] = MIGRATIONS) -> Path | None:
    """Đưa DB lên version mới nhất, giữ dữ liệu (#64). Trả đường dẫn file backup (None nếu không phải nâng).

    DB mới (version 0, chưa có bảng) → tạo v1 rồi áp mọi migration, không backup. DB cũ → sao lưu `<tên>.v<cũ>-<UTC>.bak` cạnh DB,
    rồi áp các migration còn thiếu trong **một** transaction (lỗi → rollback, file như trước). DB mới hơn app → RuntimeError, không ghi gì.
    """
    target = migrations[-1][0]
    with closing(sqlite3.connect(path, isolation_level=None)) as c:
        version = c.execute("PRAGMA user_version").fetchone()[0]
        has_tables = c.execute("SELECT count(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0] > 0
        if version > target:
            raise RuntimeError(f"{path}: schema v{version} mới hơn app (v{target}); dùng bản app mới hơn, không ghi gì vào DB")
        if version == 0 and has_tables:
            raise RuntimeError(f"{path}: có bảng nhưng user_version = 0 — không rõ schema, không ghi gì")
        if version == target:
            return None
        backup = None
        if version == 0:
            script, start = SCHEMA_V1, 1
        else:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            backup = path.with_name(f"{path.name}.v{version}-{stamp}.bak")
            with closing(sqlite3.connect(backup)) as dst:
                c.backup(dst)
            script, start = "", version
        script += "".join(sql for v, sql in migrations if v > start)
        try:
            c.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {target};\nCOMMIT;")
        except sqlite3.Error as e:
            if c.in_transaction:
                c.execute("ROLLBACK")
            raise RuntimeError(f"{path}: migration v{version} → v{target} lỗi, DB giữ nguyên"
                               + (f" (backup: {backup.name})" if backup else "") + f": {e}") from e
        return backup


class DB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.backup = migrate(self.path)

    @contextmanager
    def _conn(self):
        with closing(sqlite3.connect(self.path)) as c:
            c.row_factory = sqlite3.Row
            with c:                  # commit khi xong, rollback khi lỗi → một lô ghi hết hoặc không ghi gì
                yield c

    # ---- model checkpoint (bất biến, CONTRACTS §16)

    def add_model(self, row: dict) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO model_checkpoint VALUES (:id, :name, :filename, :sha256, :size_bytes, :format, :created_at)", row)

    def get_model(self, model_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM model_checkpoint WHERE id = ?", (model_id,)).fetchone()
        return dict(row) if row else None

    def list_models(self) -> list[dict]:
        with self._conn() as c:
            return [dict(row) for row in c.execute("SELECT * FROM model_checkpoint ORDER BY created_at DESC, id")]

    # ---- session

    def add_session(self, row: dict) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO session VALUES (:id, :name, :created_at, :spec, :n_units)",
                      {**row, "spec": json.dumps(row["spec"])})

    def update_session_pool(self, sid: str, spec: dict, n_units: int) -> None:
        """Pool của phiên chỉ được thêm video (#56): ghi spec mới + số unit."""
        with self._conn() as c:
            c.execute("UPDATE session SET spec = ?, n_units = ? WHERE id = ?", (json.dumps(spec), n_units, sid))

    def get_session(self, sid: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM session WHERE id = ?", (sid,)).fetchone()
        return _session(r) if r else None

    def list_sessions(self) -> list[dict]:
        with self._conn() as c:
            return [_session(r) for r in c.execute("SELECT * FROM session ORDER BY created_at")]

    # ---- decision (chỉ ghi thêm)

    def add_decisions(self, decisions: list[Decision]) -> None:
        with self._conn() as c:
            c.executemany(
                "INSERT INTO decision (session_id, unit_id, action, actor, goal, proposal_n, decided_at, shown_at,"
                " reason, confidence, extra) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(d.session_id, d.unit_id, d.action, d.actor, d.goal, d.proposal_n, d.decided_at.isoformat(),
                  d.shown_at.isoformat() if d.shown_at else None, d.reason, d.confidence, json.dumps(d.extra))
                 for d in decisions])

    def decisions(self, sid: str) -> list[Decision]:
        return [d for _, d in self.decision_rows(sid)]

    def decision_rows(self, sid: str) -> list[tuple[int, Decision]]:
        """Log quyết định kèm `seq` (thứ tự ghi) — snapshot cần `seq` để biết đã chốt tới đâu."""
        with self._conn() as c:
            rows = c.execute("SELECT * FROM decision WHERE session_id = ? ORDER BY seq", (sid,)).fetchall()
        return [(r["seq"], Decision(r["session_id"], r["unit_id"], r["action"], r["actor"], r["goal"], r["proposal_n"],
                                    datetime.fromisoformat(r["decided_at"]),
                                    datetime.fromisoformat(r["shown_at"]) if r["shown_at"] else None,
                                    r["reason"], r["confidence"], json.loads(r["extra"]))) for r in rows]

    # ---- proposal

    def add_proposal(self, sid: str, row: dict) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO proposal VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (sid, row["n"], row["round"], row["created_at"], json.dumps({**row["input"], "_evidence": row.get("evidence", {}),
                                  **{"_" + k: row[k] for k in ("scores", "strategy_trace") if k in row}}),
                       json.dumps(row["picks"]), int(row["relaxed"])))

    def proposals(self, sid: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM proposal WHERE session_id = ? ORDER BY n", (sid,)).fetchall()
        records = []
        for r in rows:
            snapshot = json.loads(r["snapshot"])
            extra = {k: snapshot.pop("_" + k) for k in ("scores", "strategy_trace") if "_" + k in snapshot}
            records.append({"n": r["n"], "round": r["round"], "created_at": r["created_at"], **extra,
                            "evidence": snapshot.pop("_evidence", {}), "input": snapshot,
                            "picks": json.loads(r["picks"]), "relaxed": bool(r["relaxed"])})
        return records


    # ---- snapshot (bất biến; trigger chặn sửa / xoá — CONTRACTS §14.2)

    def add_snapshot(self, row: dict, members: list[dict]) -> tuple[dict, bool]:
        """Ghi snapshot nếu log quyết định chưa đổi kể từ `row["decision_seq"]`; trùng hai hash với snapshot mới nhất → trả nó.

        `BEGIN IMMEDIATE` giữ khoá ghi suốt lúc kiểm + ghi. Log đã đổi → `SnapshotConflictError`. Trả `(snapshot, created)`."""
        sid = row["session_id"]
        with closing(sqlite3.connect(self.path, isolation_level=None)) as c:
            c.row_factory = sqlite3.Row
            c.execute("BEGIN IMMEDIATE")
            try:
                seq = c.execute("SELECT coalesce(max(seq), 0) FROM decision WHERE session_id = ?", (sid,)).fetchone()[0]
                if seq != row["decision_seq"]:
                    raise SnapshotConflictError(f"log quyết định đã đổi ({row['decision_seq']} → {seq})")
                last = c.execute("SELECT * FROM snapshot WHERE session_id = ? ORDER BY n DESC LIMIT 1", (sid,)).fetchone()
                if last and last["members_hash"] == row["members_hash"] and last["context_hash"] == row["context_hash"]:
                    c.execute("ROLLBACK")
                    return _snapshot(last), False
                n = (last["n"] if last else 0) + 1
                c.execute("INSERT INTO snapshot VALUES (:id, :session_id, :n, :created_at, :created_by, :note, :decision_seq,"
                          " :members_hash, :n_members, :context, :context_hash)",
                          {**row, "n": n, "n_members": len(members), "context": json.dumps(row["context"])})
                c.executemany("INSERT INTO snapshot_member VALUES (?, ?, ?, ?, ?, ?)",
                              [(row["id"], i, m["unit_id"], m["source_kind"], m["source_id"], json.dumps(m["provenance"]))
                               for i, m in enumerate(members)])
                c.execute("COMMIT")
            except BaseException:
                if c.in_transaction:
                    c.execute("ROLLBACK")
                raise
        return self.snapshot(row["id"]), True

    def snapshots(self, sid: str) -> list[dict]:
        with self._conn() as c:
            return [_snapshot(r) for r in c.execute("SELECT * FROM snapshot WHERE session_id = ? ORDER BY n DESC", (sid,))]

    def snapshot(self, snapshot_id: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM snapshot WHERE id = ?", (snapshot_id,)).fetchone()
        return _snapshot(r) if r else None

    def snapshot_members(self, snapshot_id: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM snapshot_member WHERE snapshot_id = ? ORDER BY ord", (snapshot_id,)).fetchall()
        return [{"ord": r["ord"], "unit_id": r["unit_id"], "source_kind": r["source_kind"], "source_id": r["source_id"],
                 "provenance": json.loads(r["provenance"])} for r in rows]

    def max_decision_seq(self, sid: str) -> int:
        with self._conn() as c:
            return c.execute("SELECT coalesce(max(seq), 0) FROM decision WHERE session_id = ?", (sid,)).fetchone()[0]

    # ---- dataset: chỉ thêm version/member, ghi nguyên lô trong một transaction

    def add_dataset_version(self, dataset: dict | None, row: dict, members: list[dict]) -> tuple[dict, bool]:
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            if dataset is not None:
                c.execute("INSERT INTO dataset VALUES (:id, :name, :created_at, :description)", dataset)
            old = c.execute("SELECT * FROM dataset_version WHERE dataset_id = ? AND manifest_hash = ?",
                            (row["dataset_id"], row["manifest_hash"])).fetchone()
            if old:
                return _dataset_version(old), False
            n = c.execute("SELECT coalesce(max(n), 0) + 1 FROM dataset_version WHERE dataset_id = ?",
                          (row["dataset_id"],)).fetchone()[0]
            c.execute("INSERT INTO dataset_version VALUES (:id, :dataset_id, :n, :created_at, :created_by, :folder,"
                      " :manifest_hash, :manifest, :annotation_format, :classes, :n_images, :import_report)",
                      {**row, "n": n, **{k: None if row[k] is None else json.dumps(row[k], sort_keys=True)
                                         for k in ("manifest", "classes", "import_report")}})
            c.executemany("INSERT INTO dataset_member VALUES (:version_id, :sample_id, :key, :relpath, :sha256,"
                          " :size_bytes, :width, :height, :label_status, :label_relpath, :label_sha256, :label_counts)",
                          [{**m, "version_id": row["id"],
                            "label_counts": json.dumps(m["label_counts"]) if m["label_counts"] is not None else None}
                           for m in members])
        return self.dataset_version(row["id"]), True

    def dataset(self, dataset_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM dataset WHERE id = ?", (dataset_id,)).fetchone()
        return dict(row) if row else None

    def datasets(self) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM dataset ORDER BY created_at, id")]

    def dataset_versions(self, dataset_id: str) -> list[dict]:
        with self._conn() as c:
            return [_dataset_version(r) for r in c.execute("SELECT * FROM dataset_version WHERE dataset_id = ? ORDER BY n DESC",
                                                           (dataset_id,))]

    def dataset_version(self, version_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM dataset_version WHERE id = ?", (version_id,)).fetchone()
        return _dataset_version(row) if row else None

    def dataset_samples(self, version_id: str, offset: int, limit: int, label_status: str | None) -> tuple[int, list[dict]]:
        where, args = "version_id = ?", [version_id]
        if label_status is not None:
            where += " AND label_status = ?"
            args.append(label_status)
        with self._conn() as c:
            total = c.execute(f"SELECT count(*) FROM dataset_member WHERE {where}", args).fetchone()[0]
            rows = c.execute(f"SELECT * FROM dataset_member WHERE {where} ORDER BY key LIMIT ? OFFSET ?",
                             [*args, limit, offset]).fetchall()
        return total, [_dataset_member(r) for r in rows]

    def dataset_image(self, version_id: str, key: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM dataset_member WHERE version_id = ? AND key = ?", (version_id, key)).fetchone()
        return _dataset_member(row) if row else None


class SnapshotConflictError(Exception):
    """Log quyết định đổi trong lúc chốt snapshot."""


def _dataset_version(row: sqlite3.Row) -> dict:
    return {**dict(row), **{k: (json.loads(row[k]) if row[k] is not None else None) for k in ("manifest", "classes", "import_report")}}


def _dataset_member(row: sqlite3.Row) -> dict:
    return {**dict(row), "label_counts": json.loads(row["label_counts"]) if row["label_counts"] is not None else None}


def _snapshot(r: sqlite3.Row) -> dict:
    return {**dict(r), "context": json.loads(r["context"])}


def _session(r: sqlite3.Row) -> dict:
    return {**dict(r), "spec": json.loads(r["spec"])}
