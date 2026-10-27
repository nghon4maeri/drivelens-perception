"""Legacy auth trigger must not override explicit signup policy bootstrap."""
from uuid import uuid4

from src.rav.actor import ActorContext
from src.rav.store.pilot_db import PilotDB
from src.rav.store.pilot_runtime import PilotRuntime
from tests.conftest import POLICY_CONFIG


def test_legacy_trigger_leaves_profile_creation_to_verified_api(pg):
    uid = uuid4()
    # Simulate the dashboard-created auth trigger without touching real auth.users.
    pg.sql("CREATE TABLE qa_auth_signup (id uuid PRIMARY KEY)")
    pg.sql("CREATE TRIGGER qa_auth_created AFTER INSERT ON qa_auth_signup "
           "FOR EACH ROW EXECUTE FUNCTION public.handle_new_pilot_user()")
    pg.sql("INSERT INTO qa_auth_signup VALUES (%s)", (uid,))
    assert not pg.sql("SELECT 1 FROM rav_pilot_profile WHERE user_id=%s", (uid,))
    cfg = {**POLICY_CONFIG, "cpu_accounting_enabled": False, "gpu_base_ms": 3_000_000}
    pid = pg.policy(cfg)
    db = PilotDB(pg.api_url)
    assert db.ensure_profile(uid, "QA")["policy_id"] == pid
    q = PilotRuntime(db).read_quota(ActorContext(user_id=uid, request_id=uuid4()))
    assert q.cpu.enabled is False
    assert q.gpu.base_available == 3_000_000
