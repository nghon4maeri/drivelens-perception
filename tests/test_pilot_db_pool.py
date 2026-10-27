"""Pool PostgreSQL thật: reuse, RLS theo checkout, rollback và connection hỏng."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.rav.errors import ServiceError
from src.rav.store.pilot_db import PilotDB


def test_reuse_rls_and_rollback(pg):
    a, b = pg.profile(), pg.profile()
    sa, sb = pg.source(a, "a"), pg.source(b, "b")
    db = PilotDB(pg.api_url)
    try:
        with db.backend() as cur:
            cur.execute("SELECT pg_backend_pid() AS pid")
            pid = cur.fetchone()["pid"]
        for owner, source in ((a, sa), (b, sb)):
            with pytest.raises(RuntimeError):
                with db.as_user(owner) as cur:
                    cur.execute("SELECT id FROM rav_pilot_source_asset")
                    assert [r["id"] for r in cur.fetchall()] == [source]
                    raise RuntimeError("rollback")
            with db.backend() as cur:
                cur.execute("SELECT pg_backend_pid() AS pid,current_user AS role,current_setting('request.jwt.claims',true) AS claims")
                row = cur.fetchone()
                assert row == {"pid": pid, "role": "rav_pilot_api", "claims": ""}
        with pytest.raises(RuntimeError):
            with db.backend() as cur:
                cur.execute("UPDATE rav_pilot_profile SET display_name='rollback' WHERE user_id=%s", (a,))
                raise RuntimeError("rollback")
        assert pg.sql("SELECT display_name FROM rav_pilot_profile WHERE user_id=%s", (a,))[0]["display_name"] != "rollback"
        with db.backend() as cur:
            cur.execute("UPDATE rav_pilot_profile SET display_name='committed' WHERE user_id=%s", (a,))
        assert pg.sql("SELECT display_name FROM rav_pilot_profile WHERE user_id=%s", (a,))[0]["display_name"] == "committed"
    finally:
        db.close()


def test_concurrent_checkout_keeps_claims_separate(pg):
    owners = [pg.profile(), pg.profile()]
    db = PilotDB(pg.api_url)
    def read(i):
        uid = owners[i % 2]
        with db.as_user(uid) as cur:
            cur.execute("SELECT pg_backend_pid() AS pid,current_setting('request.jwt.claims') AS claims,pg_sleep(0.02)")
            row = cur.fetchone()
            assert json.loads(row["claims"])["sub"] == str(uid)
            return row["pid"]
    try:
        with ThreadPoolExecutor(max_workers=8) as workers:
            pids = list(workers.map(read, range(16)))
        assert 1 <= len(set(pids)) <= 4
    finally:
        db.close()


def test_broken_connection_discarded_without_retry(pg):
    db = PilotDB(pg.api_url)
    try:
        with db.backend() as cur:
            cur.execute("SELECT pg_backend_pid() AS pid")
            old_pid = cur.fetchone()["pid"]
        with pytest.raises(ServiceError) as error:
            with db.backend() as cur:
                cur.execute("SELECT pg_terminate_backend(pg_backend_pid())")
        assert error.value.code == "dependency_unavailable"
        with db.backend() as cur:
            cur.execute("SELECT pg_backend_pid() AS pid")
            assert cur.fetchone()["pid"] != old_pid
    finally:
        db.close()
