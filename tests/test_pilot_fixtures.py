"""Test kiem tra tinh hop le cua pilot contract fixtures (DEP0).

Muc dich:
    Xac minh file tests/fixtures/pilot_contract_fixtures.json tuan thu
    hop dong CONTRACTS.md §21 va cac spec ky thuat da khoa tai DEP0.
    Bao dam cac subagent sau nay doc fixture khong bi sai lech format.

Cach chay:
    . .\\scripts\\use_project_storage.ps1
    python -m pytest tests/test_pilot_fixtures.py -q
"""

import json
import re
import uuid
from pathlib import Path

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "pilot_contract_fixtures.json"


def test_fixtures_file_exists_and_valid():
    assert FIXTURES_PATH.exists(), f"Khong tim thay fixture tai {FIXTURES_PATH}"
    data = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    assert data.get("version") == "rav.pilot.fixtures.v1"


def test_actor_contexts_contract():
    data = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    contexts = data["actor_contexts"]
    assert "pilot_user_a" in contexts
    assert "pilot_user_b" in contexts
    assert "local_v1" in contexts

    # User A
    user_a = contexts["pilot_user_a"]
    assert uuid.UUID(user_a["user_id"])
    assert uuid.UUID(user_a["request_id"])
    assert user_a["actor_string"] == f"human:{user_a['user_id']}"

    # Local v1
    local = contexts["local_v1"]
    assert local["user_id"] is None
    assert local["actor_string"] == "human:local"


def test_sources_contract():
    data = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    sources = data["sources"]

    # Private source
    priv = sources["private_video"]
    assert priv["scope"] == "PRIVATE"
    assert priv["owner_id"] is not None
    assert uuid.UUID(priv["owner_id"])
    assert priv["protected"] is False
    assert priv["availability"] == "READY"
    assert priv["dino"]["ready"] is True

    # System demo video
    sys_vid = sources["system_demo_video"]
    assert sys_vid["scope"] == "SYSTEM"
    assert sys_vid["owner_id"] is None
    assert sys_vid["protected"] is True

    # System demo reference dataset
    sys_ds = sources["system_reference_dataset"]
    assert sys_ds["scope"] == "SYSTEM"
    assert sys_ds["kind"] == "IMAGE_SET"
    assert sys_ds["protected"] is True


def test_sessions_and_fork_contract():
    data = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    sessions = data["sessions"]
    templates = data["templates"]

    sess = sessions["session_dto"]
    assert uuid.UUID(sess["id"])
    assert sess["state_version"] >= 1
    assert sess["target_k"] > 0

    # Fork receipt: phien trong tu mau
    fork = templates["fork_receipt"]
    assert fork["replayed"] is False
    assert fork["counts"] == {"proposals": 0, "decisions": 0, "snapshots": 0}
    fork_sess = fork["session"]
    assert fork_sess["state_version"] == 1
    assert fork_sess["started_from_template_id"] == templates["template_dto"]["id"]


def test_jobs_and_quotas_contract():
    data = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    jobs = data["jobs"]
    quotas = data["quotas"]

    # Job states
    queued = jobs["job_dto_queued"]
    assert queued["state"] == "QUEUED"
    assert queued["progress"]["done"] == 0

    succeeded = jobs["job_dto_succeeded"]
    assert succeeded["state"] == "SUCCEEDED"
    assert succeeded["progress"]["done"] == succeeded["progress"]["total"]

    # Quota structure (runtime spec §4 QuotaDTO)
    q = quotas["quota_dto"]
    assert {"server_time", "next_reset_at", "cpu", "gpu", "admission"} <= set(q)
    assert q["cpu"]["base_available"] >= 0
    assert q["gpu"]["base_available"] >= 0


def test_export_and_confirmation_contract():
    data = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    exports = data["exports"]

    needs_confirm = exports["export_needs_confirmation"]
    assert needs_confirm["state"] == "NEEDS_CONFIRMATION"
    assert needs_confirm["download_url"] is None
    hash_val = needs_confirm["payload_hash"]
    assert re.match(r"^[0-9a-f]{64}$", hash_val)

    # Confirm input phai dung payload_hash
    confirm_in = exports["export_confirm_input"]
    assert confirm_in["payload_hash"] == hash_val

    # Ready export
    ready = exports["export_ready"]
    assert ready["state"] == "READY"
    assert ready["download_url"] is not None
    assert ready["payload_hash"] == hash_val


def test_python_dtos_parse_fixtures():
    """DTO Python của pilot đọc đúng fixture dùng chung (DEP2F #89) — FE/BE cùng một hình dạng."""
    from src.rav.store.pilot_db import ReviewStateDTO, SessionDTO, SourceDTO
    from src.rav.store.pilot_runtime import DeleteReceipt, JobDTO, QuotaDTO

    data = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    for src in data["sources"].values():
        SourceDTO.model_validate(src)
    SessionDTO.model_validate(data["sessions"]["session_dto"])
    state = ReviewStateDTO.model_validate(data["sessions"]["review_state_dto"])
    assert all(":" in u and not u.split(":")[0].count("-") == 4 for u in state.kept_ids)   # core unit id, không UUID
    for key in ("job_dto_queued", "job_dto_running", "job_dto_succeeded"):
        JobDTO.model_validate(data["jobs"][key])
    QuotaDTO.model_validate(data["quotas"]["quota_dto"])
    DeleteReceipt.model_validate(data["deletions"]["delete_receipt_pending"])


def test_upload_contract():
    """UploadDTO theo runtime spec §4 / §6.1 (sửa ở DEP2F→DEP4): state CREATED…ABORTED, `transfer` chỉ có ở bản tạo, key dưới prefix."""
    data = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    up = data["uploads"]
    assert set(up["create_upload_input"]) == {"filename", "declared_bytes"}
    base = {"id", "state", "validation_job_id", "source_id", "error"}
    states = {"CREATED", "UPLOADED", "VALIDATING", "READY", "FAILED", "ABORTED"}
    created = up["upload_created_dto"]
    assert set(created) == base | {"transfer"} and created["state"] == "CREATED"
    transfer = created["transfer"]
    assert set(transfer) == {"method", "url", "fields", "expires_at"} and transfer["method"] == "POST"
    key = transfer["fields"]["key"]
    assert re.match(rf"^[^/]+/staging/[0-9a-f-]{{36}}/{created['id']}/video$", key)
    for name in ("upload_validating_dto", "upload_ready_dto"):
        dto = up[name]
        assert set(dto) == base and dto["state"] in states and dto["validation_job_id"]
    assert up["upload_ready_dto"]["source_id"] and up["upload_validating_dto"]["source_id"] is None
    assert "raw_key" not in json.dumps(up) and "canonical" not in json.dumps(up)
