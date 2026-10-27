"""Kiểm thử Catalog Dataset & Model tham chiếu và Binding phiên pilot (CONTRACTS §21.6, §21.7, §21.16, §21.17, N3).

Bao gồm:
1. Catalog Dataset & Model: list/get dataset versions, model asset metadata và quan hệ provenance (khai báo TRAIN/FINETUNE).
2. Tạo phiên với Reference: kiểm tra ràng buộc model-dataset, bắt lỗi 422 reference_invalid khi khai báo không khớp.
3. Cập nhật Reference qua CAS: PATCH /sessions/{id}/reference kiểm tra Optimistic Concurrency Control (409),
   giải phóng pin cũ và xác lập pin nguồn mới an toàn.
4. Kiểm tra phân quyền và cô lập RLS qua REST API v2.
"""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import pytest
from starlette.testclient import TestClient

from src.rav.api.auth import AuthConfig
from src.rav.api.pilot import create_pilot_app
from src.rav.errors import ServiceError
from src.rav.store.pilot_db import CreateSessionInput, ReferenceIn, UpdateReferenceIn
from tests.test_pilot_api_v2 import ISS, JWK, auth
from tests.test_pilot_curation import cu  # noqa: F401
from tests.test_pilot_media import actor


def _setup_catalog_data(env):
    """Tạo dữ liệu mẫu cho Dataset, DatasetVersion, ModelAsset và quan hệ ModelDatasetDeclaration."""
    pg = env.pg
    uid = env.a

    # 1. Tạo SourceAsset cho dataset
    source_id = uuid4()
    core_src = f"ds-src-{source_id.hex[:8]}"
    pg.sql(
        "INSERT INTO rav_pilot_source_asset (id, core_source_id, kind, scope, owner_id, protected, name, raw_key, "
        "raw_sha256, size_bytes, availability) VALUES (%s, %s, 'IMAGE_SET', 'SYSTEM', %s, true, 'BDD100K Train', "
        "'system/datasets/bdd/raw.zip', '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef', 1000000, 'READY')",
        (source_id, core_src, None),
    )

    # 2. Tạo Dataset & Version
    dataset_id = uuid4()
    core_ds = f"dataset-bdd-{dataset_id.hex[:6]}"
    pg.sql(
        "INSERT INTO rav_pilot_dataset (id, core_dataset_id, source_id, name, description) "
        "VALUES (%s, %s, %s, 'BDD100K Detection', 'Tập dữ liệu nhận diện vật thể giao thông')",
        (dataset_id, core_ds, source_id),
    )

    version_id = uuid4()
    core_ver = f"{core_ds}@1"
    manifest = {"classes": ["car", "bus", "truck", "person"], "n_samples": 100}
    m_bytes = json.dumps(manifest, sort_keys=True).encode("utf-8")
    m_hash = hashlib.sha256(m_bytes).hexdigest()

    pg.sql(
        "INSERT INTO rav_pilot_dataset_version (id, core_version_id, dataset_id, n, manifest, manifest_hash, "
        "annotation_format, classes, import_report) VALUES (%s, %s, %s, 1, %s, %s, 'yolo', %s, '{}'::jsonb)",
        (version_id, core_ver, dataset_id, json.dumps(manifest), m_hash, json.dumps(manifest["classes"])),
    )

    # 3. Tạo Model Asset
    model_id = uuid4()
    core_model = f"ym-yolov8n-{model_id.hex[:6]}"
    m_sha = hashlib.sha256(b"fake_yolo_weights").hexdigest()
    pg.sql(
        "INSERT INTO rav_pilot_model_asset (id, core_model_id, scope, protected, name, raw_key, sha256, size_bytes, "
        "checkpoint_revision, task, class_map, runtime_manifest, license, provenance, validation) "
        "VALUES (%s, %s, 'SYSTEM', true, 'YOLOv8n Traffic', 'system/models/yolo8.pt', %s, 6000000, 'v8.0', "
        "'detect', '{\"0\": \"car\", \"1\": \"truck\"}'::jsonb, '{}'::jsonb, '{\"type\": \"MIT\"}'::jsonb, "
        "'{\"trained_on\": \"bdd100k\"}'::jsonb, 'INFERENCE_VERIFIED')",
        (model_id, core_model, m_sha),
    )

    # Model thứ 2 không có quan hệ với dataset
    unrelated_model_id = uuid4()
    pg.sql(
        "INSERT INTO rav_pilot_model_asset (id, core_model_id, scope, protected, name, raw_key, sha256, size_bytes, "
        "checkpoint_revision, task, class_map, validation) "
        "VALUES (%s, %s, 'SYSTEM', true, 'COCO Model', 'system/models/coco.pt', %s, 6000000, 'v8.0', 'detect', '{}'::jsonb, 'INFERENCE_VERIFIED')",
        (unrelated_model_id, f"ym-coco-{unrelated_model_id.hex[:6]}", hashlib.sha256(b"coco_weights").hexdigest()),
    )

    # 4. Tạo khai báo provenance ModelDatasetDeclaration
    pg.sql(
        "INSERT INTO rav_pilot_model_dataset_declaration (model_id, dataset_version_id, relation, declared_by, evidence) "
        "VALUES (%s, %s, 'TRAIN', %s, '{\"epoch\": 100, \"map50\": 0.65}'::jsonb)",
        (model_id, version_id, uid),
    )

    return {
        "source_id": source_id,
        "dataset_id": dataset_id,
        "version_id": version_id,
        "model_id": model_id,
        "unrelated_model_id": unrelated_model_id,
    }


def test_catalog_datasets_and_models(cu):  # noqa: F811
    env = cu
    meta = _setup_catalog_data(env)

    # 1. list_datasets
    datasets = env.api.list_datasets(actor(env.a))
    assert len(datasets) >= 1
    ds = next(d for d in datasets if d.id == meta["dataset_id"])
    assert ds.name == "BDD100K Detection"
    assert len(ds.versions) == 1
    ver = ds.versions[0]
    assert ver.id == meta["version_id"]
    assert ver.annotation_format == "yolo"
    assert "car" in ver.classes

    # 2. get_dataset
    single_ds = env.api.get_dataset(actor(env.a), meta["dataset_id"])
    assert single_ds.id == meta["dataset_id"]
    assert len(single_ds.versions) == 1

    # 3. get_model
    model_detail = env.api.get_model(actor(env.a), meta["model_id"])
    assert model_detail.id == meta["model_id"]
    assert model_detail.task == "detect"
    assert model_detail.validation == "INFERENCE_VERIFIED"
    assert len(model_detail.declarations) == 1
    assert model_detail.declarations[0].relation == "TRAIN"
    assert model_detail.declarations[0].dataset_version_id == meta["version_id"]


def test_create_session_with_reference_validation(cu):  # noqa: F811
    env = cu
    meta = _setup_catalog_data(env)

    # 1. Thất bại: Model không có quan hệ với dataset version (422 reference_invalid)
    with pytest.raises(ServiceError) as exc_info:
        env.api.create_session(
            actor(env.a),
            CreateSessionInput(
                name="Sess Invalid Ref",
                goal="test",
                target_k=10,
                pool_source_ids=[env.sid],
                reference=ReferenceIn(
                    dataset_version_ids=[meta["version_id"]],
                    model_id=meta["unrelated_model_id"],
                ),
            ),
            idempotency_key=str(uuid4()),
        )
    assert exc_info.value.code == "reference_invalid"

    # 2. Thành công: Model khớp với khai báo TRAIN của dataset version
    sess = env.api.create_session(
        actor(env.a),
        CreateSessionInput(
            name="Sess Valid Ref",
            goal="test",
            target_k=10,
            pool_source_ids=[env.sid],
            reference=ReferenceIn(
                dataset_version_ids=[meta["version_id"]],
                model_id=meta["model_id"],
            ),
        ),
        idempotency_key=str(uuid4()),
    )
    assert sess.reference is not None
    assert sess.reference.model_id == meta["model_id"]
    assert sess.reference.dataset_version_ids == [meta["version_id"]]

    # Kiểm tra nguồn dataset được pin bởi session
    pins = env.pg.sql(
        "SELECT * FROM rav_pilot_source_pin WHERE source_id = %s AND consumer_kind = 'REFERENCE' AND consumer_id = %s",
        (meta["source_id"], sess.id),
    )
    assert len(pins) == 1
    assert pins[0]["released_at"] is None


def test_update_session_reference_cas_and_unpin(cu):  # noqa: F811
    env = cu
    meta = _setup_catalog_data(env)

    # 1. Tạo session ban đầu KHÔNG có reference
    sess = env.api.create_session(
        actor(env.a),
        CreateSessionInput(
            name="Sess Update Ref",
            goal="test",
            target_k=10,
            pool_source_ids=[env.sid],
            reference=None,
        ),
        idempotency_key=str(uuid4()),
    )
    assert sess.state_version == 1
    assert sess.reference is None

    # Chưa có reference
    ref_dto = env.api.get_session_reference(actor(env.a), sess.id)
    assert len(ref_dto.dataset_versions) == 0
    assert ref_dto.model is None

    # 2. Cố tình gửi sai expected_state_version -> 409 state_version_conflict
    with pytest.raises(ServiceError) as exc_info:
        env.api.update_session_reference(
            actor(env.a),
            sess.id,
            UpdateReferenceIn(
                reference=ReferenceIn(
                    dataset_version_ids=[meta["version_id"]],
                    model_id=meta["model_id"],
                ),
                expected_state_version=99,
            ),
        )
    assert exc_info.value.code == "state_version_conflict"

    # 3. Gắn reference thành công qua CAS
    updated = env.api.update_session_reference(
        actor(env.a),
        sess.id,
        UpdateReferenceIn(
            reference=ReferenceIn(
                dataset_version_ids=[meta["version_id"]],
                model_id=meta["model_id"],
            ),
            expected_state_version=1,
        ),
    )
    assert updated.state_version == 2
    assert updated.reference is not None

    # Kiểm tra get_session_reference
    ref_dto2 = env.api.get_session_reference(actor(env.a), sess.id)
    assert len(ref_dto2.dataset_versions) == 1
    assert ref_dto2.dataset_versions[0].id == meta["version_id"]
    assert ref_dto2.model is not None
    assert ref_dto2.model.id == meta["model_id"]
    assert len(ref_dto2.model.declarations) == 1

    # Kiểm tra nguồn dataset đã được pin
    pin1 = env.pg.sql(
        "SELECT * FROM rav_pilot_source_pin WHERE source_id = %s AND consumer_kind = 'REFERENCE' AND consumer_id = %s AND released_at IS NULL",
        (meta["source_id"], sess.id),
    )
    assert len(pin1) == 1

    # 4. Xóa bỏ reference (chuyển về None) với expected_state_version=2
    unlinked = env.api.update_session_reference(
        actor(env.a),
        sess.id,
        UpdateReferenceIn(
            reference=None,
            expected_state_version=2,
        ),
    )
    assert unlinked.state_version == 3
    assert unlinked.reference is None

    # Kiểm tra pin cũ đã được release
    released_pins = env.pg.sql(
        "SELECT * FROM rav_pilot_source_pin WHERE source_id = %s AND consumer_kind = 'REFERENCE' AND consumer_id = %s AND released_at IS NOT NULL",
        (meta["source_id"], sess.id),
    )
    assert len(released_pins) == 1


def test_api_v2_reference_and_catalog_routes(cu):  # noqa: F811
    env = cu
    meta = _setup_catalog_data(env)

    client = TestClient(
        create_pilot_app(
            env.pg.api_url,
            AuthConfig(expected_issuer=ISS),
            raw_jwks={"keys": [JWK]},
            storage_factory=lambda: env.s3,
        )
    )

    # 1. GET /api/v2/datasets
    res_ds = client.get("/api/v2/datasets", headers=auth(env.a))
    assert res_ds.status_code == 200
    assert len(res_ds.json()) >= 1

    # 2. GET /api/v2/datasets/{id}
    res_single = client.get(f"/api/v2/datasets/{meta['dataset_id']}", headers=auth(env.a))
    assert res_single.status_code == 200
    assert res_single.json()["id"] == str(meta["dataset_id"])

    # 3. GET /api/v2/models/{id}
    res_m = client.get(f"/api/v2/models/{meta['model_id']}", headers=auth(env.a))
    assert res_m.status_code == 200
    m_data = res_m.json()
    assert m_data["id"] == str(meta["model_id"])
    assert len(m_data["declarations"]) == 1

    # 4. Tạo session
    res_sess = client.post(
        "/api/v2/sessions",
        headers={**auth(env.a), "Idempotency-Key": "sess-api-1"},
        json={"name": "API Ref Sess", "goal": "test", "target_k": 5, "pool_source_ids": [str(env.sid)]},
    )
    assert res_sess.status_code == 201
    sid = res_sess.json()["id"]

    # 5. GET /api/v2/sessions/{id}/reference (ban đầu rỗng)
    res_ref = client.get(f"/api/v2/sessions/{sid}/reference", headers=auth(env.a))
    assert res_ref.status_code == 200
    assert res_ref.json()["dataset_versions"] == []

    # 6. PATCH /api/v2/sessions/{id}/reference
    res_patch = client.patch(
        f"/api/v2/sessions/{sid}/reference",
        headers=auth(env.a),
        json={
            "expected_state_version": 1,
            "reference": {
                "dataset_version_ids": [str(meta["version_id"])],
                "model_id": str(meta["model_id"]),
            },
        },
    )
    assert res_patch.status_code == 200
    assert res_patch.json()["state_version"] == 2

    # 7. User B không được xem hoặc sửa session reference của User A (404)
    res_b_get = client.get(f"/api/v2/sessions/{sid}/reference", headers=auth(env.b))
    assert res_b_get.status_code == 404

    res_b_patch = client.patch(
        f"/api/v2/sessions/{sid}/reference",
        headers=auth(env.b),
        json={
            "expected_state_version": 2,
            "reference": None,
        },
    )
    assert res_b_patch.status_code == 404
