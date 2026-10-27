"""§21.23 atomic draft submission, nonblocking authorized reads and bounded image batches.

Run with RAV_PILOT_TEST_DATABASE_URL: real PostgreSQL + S3 moto, no production services.
"""
import threading

import psycopg
import pytest

from src.rav.errors import ServiceError
from src.rav.store.pilot_curation import ProposeIn
from src.rav.store.pilot_db import CreateSessionInput, DecisionChange
from src.rav.store.pilot_media import MediaRequestIn
from tests.test_pilot_curation import _ask
from tests.test_pilot_curation import cu as cu
from tests.test_pilot_media import actor, seed_frames, unit
from tests.test_pilot_media import md as md


def test_submit_commits_decisions_once_and_freezes_new_basis(cu):
    ids = cu.tables[0].unit_ids
    inp = ProposeIn(recipe="random", batch_size=4, seed=7, expected_state_version=1,
                    changes=[DecisionChange(unit_id=ids[0], action="keep"), DecisionChange(unit_id=ids[1], action="drop")])
    job = cu.cur.request_proposal(actor(cu.a), cu.session.id, inp, "atomic")
    assert job.accepted_state_version == 2
    assert cu.cur.request_proposal(actor(cu.a), cu.session.id, inp, "atomic").id == job.id
    assert cu.api.get_review_state(actor(cu.a), cu.session.id).kept_ids == [ids[0]]
    payload = cu.pg.sql("SELECT payload FROM rav_pilot_job WHERE id=%s", (job.id,))[0]["payload"]
    assert payload["state_version"] == 2 and payload["decision_seq"] > 0
    assert len(cu.pg.sql("SELECT * FROM rav_pilot_decision")) == 2
    changed = inp.model_copy(update={"batch_size": 5})
    with pytest.raises(ServiceError, match="key") as e:
        cu.cur.request_proposal(actor(cu.a), cu.session.id, changed, "atomic")
    assert e.value.code == "idempotency_conflict"
    assert cu.d.run_once()
    assert cu.cur.get_proposal(actor(cu.a), cu.session.id, 1).input["state_version"] == 2


@pytest.mark.parametrize("failure", ["enqueue", "invalid_unit", "cas", "feature", "budget"])
def test_submit_failure_rolls_back_entire_batch(cu, monkeypatch, failure):
    ids = cu.tables[0].unit_ids
    changes = [DecisionChange(unit_id=ids[0], action="keep")]
    recipe, expected = "random", 1
    if failure == "enqueue":
        def reject(*args, **kwargs):
            raise ServiceError("quota_exceeded", "probe admission error", 429)
        monkeypatch.setattr(cu.cur.runtime, "enqueue_in_tx", reject)
    elif failure == "invalid_unit":
        changes.append(DecisionChange(unit_id="foreign:0-0", action="drop"))
    elif failure == "cas":
        expected = 999
    elif failure == "feature":
        recipe = "diversity"
        cu.pg.sql("UPDATE rav_pilot_artifact SET state='DELETED' WHERE variant='DINO'")
    else:
        cu.session = cu.api.create_session(actor(cu.a), CreateSessionInput(name="budget", goal="g", target_k=1,
                                                                          pool_source_ids=[cu.sid]), "budget")
        changes.append(DecisionChange(unit_id=ids[1], action="keep"))
    with pytest.raises(ServiceError):
        cu.cur.request_proposal(actor(cu.a), cu.session.id, ProposeIn(recipe=recipe, expected_state_version=expected,
                                                                    changes=changes), "rollback")
    assert not cu.pg.sql("SELECT * FROM rav_pilot_decision")
    assert not cu.pg.sql("SELECT * FROM rav_pilot_job")
    assert cu.api.get_review_state(actor(cu.a), cu.session.id).state_version == 1


def test_read_job_does_not_wait_on_profile_lock_and_checks_access(cu):
    job = _ask(cu)
    done, results = threading.Event(), []
    def reader():
        try:
            results.append(cu.cur.runtime.get_job(actor(cu.a), job.id))
            results.append(cu.cur.runtime.list_jobs(actor(cu.a), kind="PROPOSE", resource_id=cu.session.id))
        finally:
            done.set()
    with psycopg.connect(cu.pg.admin_url) as conn:
        conn.execute("SELECT user_id FROM rav_pilot_profile WHERE user_id=%s FOR UPDATE", (cu.a,))
        t = threading.Thread(target=reader)
        t.start()
        completed = done.wait(2)
    t.join(5)
    assert completed and len(results) == 2 and results[1].items[0].id == job.id
    with pytest.raises(ServiceError) as e:
        cu.cur.runtime.get_job(actor(cu.b), job.id)
    assert e.value.code == "not_found"
    cu.pg.sql("UPDATE rav_pilot_profile SET active=false WHERE user_id=%s", (cu.a,))
    with pytest.raises(ServiceError) as e:
        cu.cur.runtime.get_job(actor(cu.a), job.id)
    assert e.value.code == "account_disabled"


def test_batch_images_order_duplicates_missing_and_no_job(md):
    _, rows = seed_frames(md["pg"], md["s3"], md["sid"], md["a"], md["path"], md["frames"])
    items = [MediaRequestIn(source_id=md["sid"], unit_id=unit(md["core"], f), variant="thumbnail")
             for f in md["frames"][:2]]
    done, responses = threading.Event(), []
    def reader():
        try:
            responses.extend(md["svc"].request_batch(actor(md["a"]), [items[1], items[0], items[1]]))
        finally:
            done.set()
    with psycopg.connect(md["pg"].admin_url) as conn:
        conn.execute("SELECT user_id FROM rav_pilot_profile WHERE user_id=%s FOR UPDATE", (md["a"],))
        t = threading.Thread(target=reader)
        t.start()
        completed = done.wait(2)
    t.join(5)
    assert completed and len(responses) == 3
    assert responses[0].id == rows[("THUMBNAIL", md["frames"][1].idx)]["id"] == responses[2].id
    assert all(r.state == "READY" and r.url and "object_key" not in r.model_dump() for r in responses)
    assert not md["pg"].sql("SELECT * FROM rav_pilot_job")
    with pytest.raises(ServiceError) as e:
        md["svc"].request_batch(actor(md["b"]), items)
    assert e.value.code == "not_found"
    with pytest.raises(ServiceError):
        md["svc"].request_batch(actor(md["a"]), items * 2)  # policy cap 3
    md["pg"].sql("UPDATE rav_pilot_source_asset SET availability='DELETE_PENDING' WHERE id=%s", (md["sid"],))
    with pytest.raises(ServiceError) as e:
        md["svc"].request_batch(actor(md["a"]), items)
    assert e.value.code == "source_unavailable"
