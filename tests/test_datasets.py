"""S4: import/identity YOLO, immutability, đường dẫn an toàn, API và CLI.

Ảnh tổng hợp trong tmp_path; không dùng Kaggle/embedding. Chạy python -m pytest tests/test_datasets.py -q.
"""

import io
import json
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from src.rav.api import create_app
from src.rav.commands import COMMANDS, App, ServiceError
from src.rav.pipeline import datasets as pipeline
from src.rav.store.db import DB, SCHEMA_V1, SQL_V2


def run(app, command, **kwargs):
    cmd = COMMANDS[command]
    return cmd.run(cmd.Input(**kwargs), app)


@pytest.fixture
def app(tmp_path):
    root = tmp_path / "datasets"
    root.mkdir()
    return App(tmp_path / "videos", tmp_path / "output", 1 << 20, datasets_dir=root)


def image(root, name, color=10):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 6), (color, 0, 0)).save(path)
    return path


def import_new(app, folder="tập ảnh", **kwargs):
    return run(app, "import_dataset", folder=folder, name="dataset", **kwargs)


def test_import_dedup_corrupt_hidden_and_unicode_without_copying_raw(app):
    root = app.datasets_dir / "tập ảnh"
    first = image(root, "ảnh.png", 10)
    image(root, "hai.PNG", 20)
    image(root, "nested/ba.png", 30)
    (root / "trùng.png").write_bytes(first.read_bytes())
    (root / "hỏng.jpg").write_bytes(b"not an image")
    image(root, ".ẩn.png", 40)
    image(root, ".cache/ẩn.png", 50)
    before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    out = import_new(app)
    assert out.created and out.version.n_images == 3 and not out.version.prepared
    assert out.report["n_files_seen"] == 5 and out.report["n_skipped"] == 1 and out.report["n_duplicates"] == 1
    assert out.version.label_counts["none"] == 3
    assert before == {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert not list(app.output_dir.rglob("*.png")) and not list(app.output_dir.rglob("*.jpg"))


def test_import_reuse_rename_change_pixels_and_frozen_versions(app):
    root = app.datasets_dir / "tập ảnh"
    original = image(root, "a.png")
    one = import_new(app)
    before = app.db.dataset_version(one.version.id)
    old_members = app.db.dataset_samples(one.version.id, 0, 500, None)[1]
    same = run(app, "import_dataset", folder="tập ảnh", dataset_id=one.dataset.id)
    assert not same.created and same.version.id == one.version.id
    original.rename(root / "b.png")
    renamed = run(app, "import_dataset", folder="tập ảnh", dataset_id=one.dataset.id)
    assert renamed.created and renamed.version.n == 2
    member = app.db.dataset_samples(renamed.version.id, 0, 500, None)[1][0]
    assert member["sample_id"] == old_members[0]["sample_id"]
    image(root, "b.png", 30)
    changed = run(app, "import_dataset", folder="tập ảnh", dataset_id=one.dataset.id)
    assert changed.version.n == 3
    assert app.db.dataset_samples(changed.version.id, 0, 500, None)[1][0]["sample_id"] != member["sample_id"]
    assert app.db.dataset_version(one.version.id) == before
    assert app.db.dataset_samples(one.version.id, 0, 500, None)[1] == old_members
    assert [v.n for v in run(app, "list_datasets").root[0].versions] == [3, 2, 1]


def yolo_root(app):
    root = app.datasets_dir / "tập ảnh"
    (root / "labels").mkdir(parents=True)
    (root / "classes.txt").write_text("car\nperson\n", encoding="utf-8")
    return root


def test_yolo_statuses_counts_and_none_mode(app):
    root = yolo_root(app)
    labels = {"labeled": "0 .5 .5 .2 .2\n1 .5 .5 .1 .1", "empty": "", "missing": None,
              "six": "0 .5 .5 .2 .2 .3", "class": "7 .5 .5 .2 .2", "coords": "0 1.1 .5 .2 .2",
              "nan": "0 nan .5 .2 .2", "zero": "0 .5 .5 0 .2"}
    for i, (name, label) in enumerate(labels.items()):
        image(root, f"images/{name}.png", i + 1)
        if label is not None:
            (root / "labels" / f"{name}.txt").write_text(label, encoding="utf-8")
    out = import_new(app)
    assert out.version.annotation_format == "yolo" and out.version.classes == ["car", "person"]
    assert out.version.label_counts == {"labeled": 1, "empty": 1, "missing": 1, "invalid": 5, "none": 0, "conflict": 0}
    assert out.version.class_counts == {"car": 1, "person": 1}
    assert out.report["labels"]["n_invalid_examples"] == 5
    no_labels = run(app, "import_dataset", folder="tập ảnh", name="no labels", annotation_format="none")
    assert no_labels.version.annotation_format == "none" and no_labels.version.classes is None
    assert no_labels.version.label_counts["none"] == 8 and no_labels.version.class_counts == {}


@pytest.mark.parametrize("names", ["[car, person]", "{0: car, 1: person}", "{'0': car, '1': person}"])
def test_yaml_names_are_frozen(app, names):
    root = yolo_root(app)
    (root / "classes.txt").unlink()
    (root / "data.yaml").write_text(f"names: {names}\n", encoding="utf-8")
    image(root, "images/a.png")
    out = import_new(app)
    (root / "data.yaml").write_text("names: [truck]\n", encoding="utf-8")
    assert run(app, "get_dataset_version", version_id=out.version.id).classes == ["car", "person"]
    new = run(app, "import_dataset", folder="tập ảnh", dataset_id=out.dataset.id)
    assert new.version.n == 2 and new.version.classes == ["truck"]


def test_conflict_manifest_changes_for_any_label_and_keeps_no_chosen_label(app):
    root = yolo_root(app)
    first = image(root, "images/a.png")
    (root / "images/b.png").write_bytes(first.read_bytes())
    (root / "labels/a.txt").write_text("0 .5 .5 .2 .2", encoding="utf-8")
    (root / "labels/b.txt").write_text("1 .5 .5 .2 .2", encoding="utf-8")
    one = import_new(app)
    before = app.db.dataset_version(one.version.id)
    member = app.db.dataset_samples(one.version.id, 0, 500, None)[1][0]
    assert one.version.label_counts["conflict"] == 1 and one.version.class_counts == {}
    assert member["label_counts"] is None and member["label_sha256"] is None and member["label_relpath"] is None
    assert len(before["manifest"]["conflicts"][0]["copies"]) == 2
    (root / "labels/b.txt").write_text("1 .5 .5 .3 .3", encoding="utf-8")
    two = run(app, "import_dataset", folder="tập ảnh", dataset_id=one.dataset.id)
    assert two.created and two.version.n == 2
    assert app.db.dataset_version(two.version.id)["manifest_hash"] != before["manifest_hash"]
    assert app.db.dataset_version(one.version.id) == before


def test_same_byte_labels_dedup_and_empty_to_missing_creates_new_version(app):
    root = yolo_root(app)
    first = image(root, "images/a.png")
    (root / "images/b.png").write_bytes(first.read_bytes())
    for name in ("a", "b"):
        (root / "labels" / f"{name}.txt").write_text("", encoding="utf-8")
    one = import_new(app)
    assert one.version.n_images == 1 and one.report["n_duplicates"] == 1
    assert one.version.label_counts["empty"] == 1
    before = app.db.dataset_version(one.version.id)
    for name in ("a", "b"):
        (root / "labels" / f"{name}.txt").unlink()
    two = run(app, "import_dataset", folder="tập ảnh", dataset_id=one.dataset.id)
    assert two.version.n == 2 and two.version.label_counts["missing"] == 1
    assert app.db.dataset_version(one.version.id) == before


def test_manifest_keeps_conflicts_beyond_report_cap(app):
    root = yolo_root(app)
    for i in range(201):
        first = image(root, f"images/{i:03d}a.png", i)
        (root / "images" / f"{i:03d}b.png").write_bytes(first.read_bytes())
        (root / "labels" / f"{i:03d}a.txt").write_text("0 .5 .5 .2 .2", encoding="utf-8")
        (root / "labels" / f"{i:03d}b.txt").write_text("1 .5 .5 .2 .2", encoding="utf-8")
    one = import_new(app)
    assert one.report["n_conflicts"] == 201 and len(one.report["conflicts"]) == 200
    before = app.db.dataset_version(one.version.id)
    assert len(before["manifest"]["conflicts"]) == 201
    (root / "labels/200b.txt").write_text("1 .5 .5 .3 .3", encoding="utf-8")
    two = run(app, "import_dataset", folder="tập ảnh", dataset_id=one.dataset.id)
    assert two.version.n == 2 and two.version.id != one.version.id


@pytest.mark.parametrize("folder", ["../outside", "C:/outside", "/outside", "missing", "."])
def test_folder_boundary(app, folder):
    with pytest.raises(ServiceError) as err:
        import_new(app, folder)
    assert err.value.code == "invalid_folder"


def test_symlink_boundary_including_classes_file(app, tmp_path):
    root = yolo_root(app)
    image(root, "images/a.png")
    outside = tmp_path / "outside.txt"
    outside.write_text("car", encoding="utf-8")
    (root / "classes.txt").unlink()
    try:
        (root / "classes.txt").symlink_to(outside)
    except OSError:
        pytest.skip("Windows không cấp quyền tạo symlink")
    with pytest.raises(ServiceError) as err:
        import_new(app, annotation_format="yolo")
    assert err.value.code == "invalid_folder"


def test_errors_are_atomic_and_input_requires_one_identity(app):
    root = app.datasets_dir / "tập ảnh"
    root.mkdir()
    for kwargs in ({}, {"name": "x", "dataset_id": "ds-x"}, {"name": " "}):
        with pytest.raises(ValidationError):
            run(app, "import_dataset", folder="tập ảnh", **kwargs)
    with pytest.raises(ServiceError, match="không có ảnh"):
        import_new(app)
    assert app.db.datasets() == []
    image(root, "a.png")
    with pytest.raises(ServiceError) as err:
        import_new(app, annotation_format="yolo")
    assert err.value.code == "invalid_annotation" and app.db.datasets() == []
    app.max_import_images = 1
    image(root, "b.png", 20)
    with pytest.raises(ServiceError) as err:
        import_new(app)
    assert err.value.code == "too_many_images" and app.db.datasets() == []
    app.max_import_images = 10
    import_new(app)
    with pytest.raises(ServiceError) as err:
        import_new(app)
    assert err.value.code == "dataset_name_taken"
    with pytest.raises(ServiceError) as err:
        run(app, "import_dataset", folder="tập ảnh", dataset_id="ds-nope")
    assert err.value.code == "dataset_not_found" and len(app.db.datasets()) == 1


def test_database_versions_members_identity_are_immutable(app):
    image(app.datasets_dir / "tập ảnh", "a.png")
    out = import_new(app)
    for sql in ("UPDATE dataset_version SET folder = 'elsewhere'", "DELETE FROM dataset_version",
                "UPDATE dataset_member SET relpath = 'elsewhere'", "DELETE FROM dataset_member",
                "UPDATE dataset SET id = 'elsewhere'", "DELETE FROM dataset"):
        with closing(sqlite3.connect(app.db.path)) as c, pytest.raises(sqlite3.DatabaseError, match="immutable"):
            c.execute(sql)
    with closing(sqlite3.connect(app.db.path)) as c, c:
        c.execute("UPDATE dataset SET name = 'renamed', description = 'new'")
    assert app.db.dataset(out.dataset.id)["name"] == "renamed"


def test_db_import_rolls_back_whole_transaction_on_bad_member(app):
    image(app.datasets_dir / "tập ảnh", "a.png")
    out = import_new(app)
    row = app.db.dataset_version(out.version.id)
    members = app.db.dataset_samples(out.version.id, 0, 500, None)[1]
    dataset = {"id": "ds-new", "name": "new", "created_at": "t", "description": None}
    bad = {**members[0], "label_status": "unknown"}
    with pytest.raises(sqlite3.IntegrityError):
        app.db.add_dataset_version(dataset, {**row, "id": "dv-new", "dataset_id": "ds-new"}, [bad])
    assert app.db.dataset("ds-new") is None and app.db.dataset_version("dv-new") is None


def test_upgrade_v2_keeps_snapshot_and_creates_backup(tmp_path):
    path = tmp_path / "rav.sqlite"
    with closing(sqlite3.connect(path)) as c, c:
        c.executescript(SCHEMA_V1 + SQL_V2)
        c.execute("PRAGMA user_version = 2")
        c.execute("INSERT INTO session VALUES ('s', 'name', 't', '{}', 1)")
        c.execute("INSERT INTO snapshot VALUES ('x', 's', 1, 't', 'human:local', NULL, 0, 'mh', 1, '{}', 'ch')")
        c.execute("INSERT INTO snapshot_member VALUES ('x', 0, 'u', 'video', 'v', '{}')")
    db = DB(path)
    assert db.backup.name.startswith("rav.sqlite.v2-")
    assert db.snapshot_members("x")[0]["unit_id"] == "u" and db.snapshot("x")["n_members"] == 1
    with closing(sqlite3.connect(db.backup)) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 2


def test_api_import_list_filter_page_and_image_integrity(app):
    root = yolo_root(app)
    image(root, "images/a.png")
    image(root, "images/b.png", 20)
    (root / "labels/a.txt").write_text("", encoding="utf-8")
    client = TestClient(create_app(app))
    folders = client.get("/api/v1/datasets/import-folders").json()
    hint = next(f for f in folders if f["folder"] == "tập ảnh")
    assert hint["detected"] == "yolo" and hint["n_images_hint"] == 2
    response = client.post("/api/v1/datasets/import", json={"folder": "tập ảnh", "name": "dataset"})
    assert response.status_code == 200
    version = response.json()["version"]["id"]
    url = f"/api/v1/datasets/versions/{version}"
    assert client.get(url).json()["report"]["labels"]["empty"] == 1
    assert len(client.get("/api/v1/datasets").json()) == 1
    assert client.get(url + "/samples?offset=1&limit=1").json()["total"] == 2
    items = client.get(url + "/samples?label_status=empty").json()["items"]
    assert len(items) == 1 and items[0]["label_status"] == "empty"
    thumb = client.get(items[0]["thumb_url"] + "&max_side=4")
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
    assert thumb.headers["cache-control"] == "no-cache"
    with Image.open(io.BytesIO(thumb.content)) as result:
        assert max(result.size) == 4
    assert client.get(url + "/samples?limit=501").status_code == 422
    assert client.get(url + "/samples?label_status=bad").status_code == 422
    assert client.get("/api/v1/datasets/versions/missing").status_code == 404
    image_url = items[0]["thumb_url"]
    before = app.db.dataset_version(version)
    image(root, "images/a.png", 60)
    assert client.get(image_url).json()["error"]["code"] == "dataset_file_changed"
    (root / "images/a.png").unlink()
    missing = client.get(image_url)
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "dataset_file_missing"
    assert app.db.dataset_version(version) == before


def test_cli_uses_import_command(app, monkeypatch, capsys):
    from src.rav.cli.__main__ import main

    image(app.datasets_dir / "tập ảnh", "a.png")
    monkeypatch.setattr(App, "from_env", classmethod(lambda cls: app))
    monkeypatch.setattr("sys.argv", ["cli", "import-dataset", "tập ảnh", "--name", "CLI dataset"])
    main()
    output = json.loads(capsys.readouterr().out)
    assert output["created"] and output["dataset"]["name"] == "CLI dataset"
    assert output["version"]["n_images"] == 1


def test_importer_rejects_symlink_error_without_reclassifying(app, monkeypatch):
    root = yolo_root(app)
    def outside(*args):
        raise pipeline.DatasetImportError("invalid_folder", "outside")
    monkeypatch.setattr(pipeline, "inside", outside)
    with pytest.raises(pipeline.DatasetImportError) as err:
        pipeline.annotation(root, "yolo")
    assert err.value.code == "invalid_folder"
