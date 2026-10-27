# Thiết kế giai đoạn 1 — Nền tảng dataset và snapshot

> **Vai trò hiện hành (PL6):** giữ chi tiết schema local/manifest/hash được CONTRACTS §14 dẫn tới. Phần scope và kế hoạch bên dưới là mốc S1–S8 lịch sử; các slice đã triển khai xem PLAN, capability sau đó theo CONTRACTS §15–20. Không dùng câu “chưa ảnh hưởng đề xuất/chưa có export” của scope GĐ1 để phủ nhận V2/S8 hiện có. Deployment Postgres/ownership theo bộ spec draft D0 riêng.

> **Trạng thái: ĐÃ CHỐT để triển khai (người dùng, 2026-10-07, quyết định #64–#65; Q1/Q2 đã chốt ở §0.2).** Thiết kế theo lát: mỗi slice chép phần
> của mình ở §10 vào `CONTRACTS.md` / `architecture_diagram.md` / `UI_DESIGN.md` **trong cùng commit với code** (AGENTS.md luật 1). Phần đã chép vào
> CONTRACTS thì CONTRACTS là nguồn sự thật; file này giữ chi tiết và thứ tự slice.
> Bối cảnh và kế hoạch 4 giai đoạn: [`docs/archived/planning/MVP_GAP_MAPPING.md`](../../../planning/MVP_GAP_MAPPING.md) §4–§5.

## 0. Phạm vi

### 0.1. Giai đoạn 1 làm

1. Migration DB giữ nguyên dữ liệu (không còn "xoá DB để đổi schema").
2. **Snapshot bất biến** của collection (tập kept của phiên) để các giai đoạn sau so sánh, viết report, export cùng trỏ vào một bộ dữ liệu.
3. Kiểu dữ liệu phân biệt **loại nguồn**: video (có thời gian) và **tập ảnh** (không có thời gian).
4. **Import dataset ảnh tham chiếu** (thư mục ảnh, có/không nhãn YOLO), dataset + version bất biến, identity theo nội dung, dedup.
5. Chuẩn bị feature (DINOv2-S, pHash) cho dataset version để GĐ2/GĐ3 dùng; xem trạng thái và ảnh.
6. **Chọn dataset tham chiếu cho phiên.**
7. UI tối thiểu cho 4–6 và cho snapshot; dọn lệch nhỏ (ẩn baseline khỏi Studio).

**Không làm ở GĐ1:** so sánh collection với reference, metric mới, report, approval, export theo snapshot, goal có cấu trúc, ảnh trong pool
(xem Q1). Reference được lưu trong phiên nhưng **chưa ảnh hưởng đề xuất** — UI phải nói rõ điều này.

### 0.2. Câu hỏi chặn (chi tiết ở mapping §5.3)

| # | Câu hỏi | Khuyến nghị | Chặn slice |
|---|---|---|---|
| ~~Q1~~ | Pool mới có nhận tập ảnh trong MVP? | **Đã chốt (#65):** không — pool chỉ video; ảnh chỉ làm reference. Kiểu dữ liệu vẫn cho phép mở rộng | – |
| ~~Q2~~ | Đổi contract `Unit` (§10.1) | **Đã chốt:** người dùng giao cho quyết định kỹ thuật, với điều kiện giữ nguyên id + cache video cũ → làm theo §2.1 | – |

S1 (migration) và S2 (snapshot) **không** bị chặn. **Ghi chú Q1:** smoke test BDD100K (§11) yêu cầu "tách reference và pool không trùng ảnh";
Theo #65: pool = video (vd video BDD100K hoặc video hiện có), reference = ảnh BDD; không đưa ảnh BDD vào pool MVP.

### 0.4. Quyết định lưu trữ (người dùng chốt 2026-10-07, #64)

**Dataset và snapshot chỉ lưu manifest/tham chiếu tới dữ liệu raw.** Không materialize ảnh khi keep hay khi tạo snapshot; chỉ khi tải về mới đọc ảnh
gốc hoặc seek/decode frame rồi đóng ZIP (§12). Áp dụng cho frame video (tham chiếu video gốc + vị trí frame + cấu hình decoder) và ảnh độc lập
(tham chiếu file gốc). Thumbnail là cache xoá được. Không sao chép raw, không tách lưu toàn bộ frame, không tự chuyển/xoá file nguồn.
Hệ quả: **khả năng export phụ thuộc raw còn tồn tại và đúng nội dung** — snapshot/version không bao giờ bị sửa để khớp raw đã đổi; thay vào đó báo
nguồn không còn khả dụng.

### 0.3. Tái sử dụng / bổ sung

| Thành phần | Giai đoạn 1 | Ghi chú |
|---|---|---|
| `local_folder` source, `pyav` decoder, `per_frame` unit, `VideoRef`, catalog quét thư mục | **Giữ nguyên** | Video không đổi id, không đổi cache |
| `FieldStore`, `Execution`, `Context.field` | **Mở rộng nhỏ**: nhóm cache theo `source_id` thay vì tiền tố id; `Context.pixels` đọc được ảnh | Hành vi với video không đổi |
| Feature `dinov2_s`, `phash` | **Giữ nguyên** (đã gọi `ctx.pixels` trên `u.frames or (u.anchor,)`) | Chạy được trên ảnh qua `ImageRef` |
| `session` (spec JSON), `decision` (log), `proposal` | **Giữ nguyên bảng**; spec có thêm khoá tuỳ chọn `reference` | Không ghi lại dòng cũ |
| Command layer + bảng route | **Thêm command** | Cùng cơ chế sinh route |
| P1 nền (`queue_prepare`, một worker) | **Dùng lại hàng đợi** cho dataset version | – |
| `ReviewState.kept` | Là **collection**; không tạo bảng collection | Snapshot đóng băng nó |

## 1. Use case

| # | Actor | Input | Thao tác | Output | Lỗi | Hoàn thành khi |
|---|---|---|---|---|---|---|
| UC1 | Curator | Thư mục ảnh đã đặt dưới `RAV_DATASETS_DIR` (vd `data/datasets/bdd_ref_day/`), tên dataset, kiểu nhãn (`auto`/`none`/`yolo`) | Kho dữ liệu → "Nhập dataset" → chọn thư mục → xác nhận | Dataset + version 1, báo cáo import (số ảnh, bỏ qua, trùng, trạng thái nhãn, danh sách lớp); job chuẩn bị feature được xếp | Thư mục ngoài gốc / không tồn tại; 0 ảnh đọc được; `yolo` nhưng thiếu danh sách lớp; tên dataset trùng | Version ở trạng thái "đã nhập"; ảnh xem được; chuẩn bị xong → "Sẵn sàng" |
| UC2 | Curator | Dataset đã có + thư mục đã thay đổi | "Nhập version mới" | Version n+1 (nếu nội dung khác), version cũ không đổi | Như UC1 | Hai version cùng tồn tại, xem riêng được |
| UC3 | Curator | Dataset version | Mở dataset | Lưới ảnh (lọc theo trạng thái nhãn), số lớp, tiến độ chuẩn bị, phiên đang dùng làm tham chiếu | File ảnh đã bị xoá/sửa → báo version "hỏng" khi kiểm | – |
| UC4 | Curator | Video pool, goal, K, **0..n dataset version tham chiếu** | Phiên mới → chọn tham chiếu | Phiên có `reference` cố định | Version chưa chuẩn bị (409); hai version của cùng một dataset (422) | Phiên hiện "Tham chiếu: …" trên thanh phiên |
| UC5 | Curator | Phiên có kept ≥ 1 | Tab Xuất → "Chốt snapshot" (+ ghi chú) | Snapshot #n bất biến: danh sách mẫu + provenance + bối cảnh phiên | kept rỗng (422); collection đổi trong lúc chốt (409) | Snapshot xem được; nếu collection không đổi kể từ snapshot gần nhất → trả lại snapshot đó |
| UC6 | Curator / Reviewer | Phiên | Xem danh sách snapshot | Mỗi snapshot: số mẫu, thời điểm, người tạo, **Hiện hành / Không còn hiện hành** | – | Keep/drop sau khi chốt → snapshot cũ chuyển "Không còn hiện hành", nội dung không đổi |
| UC7 | Người vận hành | DB cũ (v1) | Khởi động BE bản mới | DB được nâng, file sao lưu cạnh DB | DB mới hơn app → BE từ chối chạy, không ghi gì | Mọi phiên / quyết định / proposal cũ còn nguyên |

## 2. Data model

### 2.1. Kiểu trong `core/types.py`

```python
SourceKind = Literal["video", "image_set"]

@dataclass(frozen=True)
class ImageRef:                  # MỚI — địa chỉ một ảnh độc lập, không chứa pixel (đối xứng với Frame)
    source_id: str               # dataset_version_id chứa ảnh (container cache / đọc file)
    key: str                     # sha256(bytes)[:16] — identity nội dung
    relpath: str                 # posix, tương đối với thư mục gốc của version

@dataclass(frozen=True)
class ImageSetRef:               # MỚI — một dataset version nhìn như một nguồn (đối xứng với VideoRef)
    id: str                      # dataset_version_id
    dataset_id: str
    root: str                    # đường dẫn tuyệt đối đã resolve lúc chạy (DB lưu dạng tương đối với RAV_DATASETS_DIR)
    meta: dict

@dataclass(frozen=True)
class Unit:
    id: str
    video_id: str | None         # ĐỔI: None với ảnh
    t0: float | None             # ĐỔI: None với ảnh — không có timestamp giả
    t1: float | None
    kind: Literal["frame", "span", "image"]        # ĐỔI: thêm "image"
    anchor: Frame | ImageRef | None                # ĐỔI
    frames: tuple[Frame | ImageRef, ...]           # ĐỔI

    @property
    def source_id(self) -> str:  # MỚI — container: video_id, hoặc dataset_version_id của ảnh
        return self.video_id if self.video_id is not None else self.anchor.source_id

    @property
    def order_key(self) -> tuple:   # MỚI — thứ tự ổn định: (video_id, t0) như cũ; ảnh: (source_id, key)
        return (self.source_id, self.t0) if self.t0 is not None else (self.source_id, self.anchor.key)
```

Thứ tự trường của `Unit` giữ nguyên → mọi chỗ khởi tạo theo vị trí hiện có (vd `units/per_frame.py`) không đổi.
Video: `source_id == video_id`, `order_key == (video_id, t0)` → `pool_hash` và id **không đổi** (yêu cầu tương thích §7).

Unit ảnh được dựng từ manifest: `Unit(id=sample_id, video_id=None, t0=None, t1=None, kind="image", anchor=ImageRef(...), frames=(ImageRef(...),))`.
Không có unit builder mới (ảnh luôn 1 mẫu = 1 unit); dựng trong `pipeline/datasets.py`.

### 2.2. Identity

| Đối tượng | Id | Tính chất |
|---|---|---|
| Video | `sha256(file)[:16]` (hiện có) | Theo nội dung |
| Frame unit | `"{video_id}:{t:.3f}-{t:.3f}"` (hiện có) | Không đổi |
| Dataset | `"ds-" + uuid4().hex[:12]` | Tạo một lần khi import lần đầu; `name` duy nhất, đổi tên không đổi id |
| Dataset version | `"dv-" + sha256(dataset_id + "\n" + manifest_hash)[:16]` | Theo nội dung → import lại cùng nội dung ra cùng id |
| Mẫu ảnh | `"{dataset_id}:{sha256(bytes)[:16]}"` | Theo nội dung, **không phụ thuộc version, đường dẫn, tên file**. Cùng ảnh ở hai dataset khác nhau = hai mẫu (trùng byte vẫn phát hiện được qua `sha256` ở GĐ3) |
| Snapshot | `uuid4().hex` + `n` trong phiên | `members_hash` = định danh nội dung collection |

Không trùng không gian tên: video id là 16 hex, dataset id có tiền tố `ds-`, version `dv-`.

### 2.3. Bảng DB (schema v2, v3 — xem §7)

```sql
-- v2 (slice S1): snapshot
CREATE TABLE snapshot (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES session(id), n INTEGER NOT NULL,
    created_at TEXT NOT NULL, created_by TEXT NOT NULL, note TEXT,
    decision_seq INTEGER NOT NULL,          -- seq lớn nhất của log quyết định phiên tại lúc chốt (0 nếu chưa có)
    members_hash TEXT NOT NULL, n_members INTEGER NOT NULL,
    context TEXT NOT NULL,                  -- JSON đóng băng, §4.2
    context_hash TEXT NOT NULL,             -- fingerprint context (pool, reference, goal, sample spec), §4.1
    UNIQUE (session_id, n));
CREATE TABLE snapshot_member (
    snapshot_id TEXT NOT NULL REFERENCES snapshot(id), ord INTEGER NOT NULL,
    unit_id TEXT NOT NULL, source_kind TEXT NOT NULL CHECK (source_kind IN ('video', 'image_set')), source_id TEXT NOT NULL,
    provenance TEXT NOT NULL,               -- JSON, §4.3
    PRIMARY KEY (snapshot_id, ord), UNIQUE (snapshot_id, unit_id));
CREATE TRIGGER snapshot_no_update BEFORE UPDATE ON snapshot BEGIN SELECT RAISE(ABORT, 'snapshot is immutable'); END;
CREATE TRIGGER snapshot_no_delete BEFORE DELETE ON snapshot BEGIN SELECT RAISE(ABORT, 'snapshot is immutable'); END;
-- (tương tự cho snapshot_member)

-- v3 (slice S4): dataset
CREATE TABLE dataset (
    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, description TEXT);
CREATE TABLE dataset_version (
    id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES dataset(id), n INTEGER NOT NULL, created_at TEXT NOT NULL,
    created_by TEXT NOT NULL, folder TEXT NOT NULL,          -- posix, tương đối với RAV_DATASETS_DIR
    manifest_hash TEXT NOT NULL, manifest TEXT NOT NULL, annotation_format TEXT NOT NULL CHECK (annotation_format IN ('none', 'yolo')),
    classes TEXT,                                            -- JSON list tên lớp theo chỉ số, NULL nếu 'none'
    n_images INTEGER NOT NULL, import_report TEXT NOT NULL,  -- JSON, §3.5
    UNIQUE (dataset_id, n), UNIQUE (dataset_id, manifest_hash));
CREATE TABLE dataset_member (
    version_id TEXT NOT NULL REFERENCES dataset_version(id), sample_id TEXT NOT NULL, key TEXT NOT NULL,
    relpath TEXT NOT NULL, sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
    label_status TEXT NOT NULL CHECK (label_status IN ('none', 'labeled', 'empty', 'missing', 'invalid', 'conflict')),
    label_relpath TEXT, label_sha256 TEXT, label_counts TEXT, -- JSON {tên lớp: số box}, chỉ khi 'labeled'
    PRIMARY KEY (version_id, sample_id), UNIQUE (version_id, relpath));
-- trigger chặn UPDATE/DELETE cho dataset_version, dataset_member (dataset: chỉ cho UPDATE name/description)
```

**Không có** cột "đã dùng train" hay trạng thái vòng đời trên dataset. Vai trò của dataset chỉ tồn tại **trong phiên** (§2.4).
Trạng thái chuẩn bị (feature đã cache chưa) **suy ra từ cache** như video (`is_prepared`), job đang chạy nằm trong RAM như P1 hiện tại (LOCAL_LIMITS #15).

### 2.4. Vai trò dataset trong phiên

Ngữ nghĩa sản phẩm đã làm rõ ở #69: dataset tham chiếu của luồng YOLO là dữ liệu train/finetune của checkpoint đang cần cải thiện trên pool mới chưa gán nhãn, không mặc định là validation/test. S6 mới lưu version dataset trong phiên; nạp/chọn checkpoint tương ứng và liên hệ train còn Hướng tới, không được suy ra từ schema hiện có. Vai trò trong phiên không thay cho provenance model–dataset.

Session spec (JSON, CONTRACTS §7) thêm khoá tuỳ chọn, cố định khi tạo phiên:

```json
{"pool": {"video_ids": ["6WTF1JV5knI…"]},
 "reference": {"dataset_version_ids": ["dv-1a2b…"]},
 "...": "các khoá hiện có giữ nguyên"}
```

- Thiếu khoá (mọi phiên cũ) = `{"dataset_version_ids": []}`.
- Tối đa một version của mỗi dataset trong một phiên (tránh cùng `sample_id` từ hai version).
- Đổi tham chiếu = tạo phiên mới (giống goal cố định, #63). Lý do: proposal và snapshot sau này phải tái lập được với đúng tham chiếu.
- Cùng một version có thể là tham chiếu của phiên này và (sau MVP) pool của phiên khác: vai trò là thuộc tính của **phiên**, không của dataset.

### 2.5. Collection

Collection = `ReviewState.kept` của phiên (replay log quyết định, hiện có). GĐ1 **không** thêm bảng collection; snapshot là cách đóng băng nó.

## 3. Import contract

### 3.1. Nguồn và vị trí

- Chỉ import thư mục nằm **dưới** `RAV_DATASETS_DIR` (mặc định `data/datasets/`, gitignore). Đường dẫn gửi lên là tương đối; resolve xong phải còn nằm
  trong gốc (chặn `..`, đường dẫn tuyệt đối, symlink ra ngoài) → lỗi `invalid_folder`.
- **Không sao chép ảnh.** DB lưu `folder` tương đối + `relpath` + `sha256` từng file. File bị sửa/xoá sau import → phát hiện khi đọc / kiểm (§3.6).
- `# local-only:` người dùng tự chép thư mục vào máy chạy BE; upload zip qua API để sau (thêm dòng LOCAL_LIMITS).

### 3.2. Định dạng hỗ trợ

| Thành phần | Hỗ trợ | Ghi chú |
|---|---|---|
| Ảnh | `.jpg .jpeg .png .bmp .webp` (không phân biệt hoa thường), đọc bằng PIL, áp `ImageOps.exif_transpose` | PIL vì đường dẫn có dấu (AGENTS §3); quét đệ quy, bỏ file/thư mục ẩn; thứ tự `relpath` posix |
| Nhãn `none` | Không đọc gì | Mọi mẫu `label_status = 'none'`, kể cả khi có thư mục `labels/` |
| Nhãn `yolo` | YOLO **detection** txt | Bố cục: `<root>/images/<sub>/<stem>.<ext>` ↔ `<root>/labels/<sub>/<stem>.txt`; danh sách lớp từ `classes.txt` (mỗi dòng một tên) hoặc `data.yaml` (`names:` list hoặc dict chỉ số → tên) |
| `auto` | Có `labels/` **và** tìm được danh sách lớp → `yolo`; ngược lại `none` | UI hiện kết quả nhận diện để người dùng xác nhận trước khi import |
| COCO, BDD JSON, Pascal VOC, segmentation, pose | **Không** | Chuyển sang YOLO detection bằng script ngoài app, hoặc import `none` |

Đọc `data.yaml` cần `PyYAML` → thêm `requirements.txt` ở slice S4.

### 3.3. Nhãn: đọc gì, giữ gì

| `label_status` | Khi nào | Lưu |
|---|---|---|
| `labeled` | File nhãn có ≥ 1 dòng, mọi dòng hợp lệ: đúng 5 số `cls cx cy w h`, `cls` nguyên trong `[0, len(classes))`, toạ độ trong `[0, 1]`, `w, h > 0` | `label_relpath`, `label_sha256`, `label_counts` (đếm box theo tên lớp) |
| `empty` | File nhãn tồn tại, rỗng | Ảnh âm tính hợp lệ — **khác** `missing` |
| `missing` | Không có file nhãn tương ứng | – |
| `invalid` | Có ít nhất một dòng sai (kể cả > 5 cột: segmentation/pose) | `label_relpath`, `label_sha256`, lý do trong báo cáo import; không dùng `label_counts` |
| `none` | Dataset `annotation_format = 'none'` | – |
| `conflict` | Ảnh trùng byte nhưng nhãn các bản khác nhau (§3.4) | Danh sách bản trong báo cáo import |

- MVP **chỉ đọc** lớp và số box để GĐ3 mô tả thành phần lớp của **dataset tham chiếu**. Toạ độ box không lưu (đọc lại từ file khi cần, có `label_sha256` để kiểm).
- **Giữ nguyên** file nhãn gốc (không sửa, không chuyển đổi). Không sinh nhãn cho pool.
- Nhãn của tham chiếu là dữ kiện đã có, được dùng làm bằng chứng mô tả tham chiếu; **nhãn ẩn của pool** (khi giả lập annotation) không bao giờ đi qua import này (focus §4B).
- Dataset không có nhãn vẫn dùng đầy đủ cho GĐ2–3 (embedding, pHash, khoảng cách); chỉ thiếu phần thành phần lớp → evidence ghi "tham chiếu không có nhãn".

### 3.4. Thuật toán import

1. Validate input; `dataset_id` có → version mới của dataset đó; không → tạo dataset theo `name` (trùng tên → `dataset_name_taken`).
2. Quét ảnh; với mỗi file: đọc bytes → `sha256`; mở PIL (verify + kích thước sau EXIF). Không mở được → bỏ qua, ghi `{relpath, reason}`.
3. Nhãn theo §3.3 cho **mọi** ảnh (kể cả bản trùng), hash cả file nhãn rỗng.
4. Trùng `sha256` ảnh trong cùng thư mục: nếu mọi bản có **cùng `label_status` và cùng `label_sha256`** → giữ `relpath` nhỏ nhất, ghi bản trùng vào báo
   cáo. Nếu nhãn khác nhau → **xung đột**: không chọn theo tên file; mẫu được giữ với `label_status = 'conflict'` (không có `label_counts`), báo cáo liệt kê
   mọi `relpath` + `label_sha256` của nhóm.
5. `manifest` chuẩn hoá = JSON (khoá sắp xếp) của `{"format": 1, "annotation_format", "classes", "members": [[key, relpath, sha256, label_sha256|null] …], "conflicts": [{key, copies: [{relpath, label_relpath, label_status, label_sha256} …]} …]}`
   (members và conflicts sắp theo `key`, copies theo `relpath`); `manifest_hash = sha256(manifest)`. Manifest lưu đầy đủ trong version, không cắt ở 200 nhóm; đổi nhãn bất kỳ bản xung đột nào tạo version mới (#65).
6. `(dataset_id, manifest_hash)` đã có → trả version đó, `created = false`. Ngược lại tạo version `n = max + 1`.
7. Ghi `dataset`(nếu mới) + `dataset_version` + mọi `dataset_member` **trong một transaction**.
8. Xếp job chuẩn bị feature (S5). Import không chờ job.

Giới hạn: `RAV_MAX_IMPORT_IMAGES` (mặc định 50.000) → vượt thì `too_many_images`. Import đồng bộ trong request (đọc + hash; ~vài nghìn ảnh/phút trên ổ SSD) — `# local-only:`,
khi host chuyển sang job nền (LOCAL_LIMITS #3).

### 3.5. Báo cáo import (`import_report`, lưu cùng version)

```json
{"n_files_seen": 1310, "n_images": 1300, "skipped": [{"relpath": "images/x.jpg", "reason": "cannot identify image"}],
 "duplicates": [{"kept": "images/a.jpg", "dropped": ["images/a_copy.jpg"]}],
 "labels": {"labeled": 1200, "empty": 60, "missing": 30, "invalid": 10, "invalid_examples": [{"relpath": "...", "reason": "class 7 ≥ 5"}]},
 "class_counts": {"pedestrian": 4210, "car": 9800}, "annotation_detected": "yolo"}
```

Danh sách `skipped`, `duplicates`, `invalid_examples` cắt ở 200 phần tử (ghi tổng số riêng).

### 3.6. Toàn vẹn sau import

- Đọc pixel (feature, thumbnail): file thiếu → lỗi `dataset_file_missing` (404), không thay ảnh khác.
- `verify_dataset_version` (S5): so `size_bytes` + `sha256` mọi file ảnh **và file nhãn** (kể cả file nhãn rỗng; nhãn `missing` mà nay xuất hiện file = thay
  đổi) → `{ok, missing[], changed[]}`; dùng trước khi chuẩn bị feature và trước export (§12). Danh sách lớp đã **đóng băng** trong version
  (`classes`); `classes.txt` / `data.yaml` đổi sau import không ảnh hưởng version, chỉ ảnh hưởng lần import sau.
- Raw đổi/xoá → version cũ **giữ nguyên manifest**, trạng thái "nguồn không còn khả dụng" (tính khi kiểm, không ghi đè DB). Không có chức năng xoá
  nguồn trong GĐ1; nếu thêm sau thì phải liệt kê dataset version / snapshot bị ảnh hưởng trước khi xoá.
- Version không bao giờ bị sửa để "khớp" thư mục mới — thư mục đổi thì import version mới.

## 4. Snapshot contract

### 4.1. Tạo

`create_snapshot(session_id, note?)`:

1. Trong **một kết nối, `BEGIN IMMEDIATE`**: đọc log quyết định của phiên kèm `seq` → replay bằng feedback của phiên → `kept`; `decision_seq = max(seq)` (0 nếu rỗng).
2. `kept` rỗng → `empty_collection` (422).
3. `members` = unit trong pool có id ∈ kept, thứ tự `order_key`.
   `members_hash = sha256("rav-collection/1\n" + "\n".join(sorted(unit_ids)))`.
4. `context_hash = sha256(JSON chuẩn hoá của context §4.2 bỏ khoá "app")`. Snapshot mới nhất của phiên có **cùng `members_hash` và cùng
   `context_hash`** → trả nó, `created = false` (không ghi). Khác một trong hai → snapshot mới.
5. Ghi `snapshot` (`n = max + 1`) + `snapshot_member`; commit. Nếu giữa bước 1 và 5 có quyết định mới ghi vào (kết nối khác) → `BEGIN IMMEDIATE` đã chặn ghi;
   nếu vì lý do nào đó `max(seq)` đổi khi kiểm lại trước commit → rollback, `collection_changed` (409).

### 4.2. Nội dung đóng băng (`snapshot.context`)

```json
{"format": 1,
 "session": {"goal": "…", "target_k": 200, "pool": {"video_ids": ["…"]}, "reference": {"dataset_version_ids": ["dv-…"]}},
 "sample_spec": {"decoder": {"type": "pyav", "params": {"fps": 4}}, "units": {"type": "per_frame"}, "spec_hash": "…",
                 "image_reader": {"name": "pil", "version": "1", "exif_transpose": true}},
 "app": {"schema_version": 3}}
```

Goal có cấu trúc (GĐ2) sẽ thêm `session.goal_structure` + phiên bản; khoá mới chỉ được thêm, không đổi nghĩa khoá cũ (`format` tăng khi đổi nghĩa).

### 4.3. Provenance từng mẫu (`snapshot_member.provenance`)

```text
// Minh họa hai dạng provenance; đây không phải một JSON document chạy được.
// video frame
{"video": {"id": "90bfbcf92da7c52e", "name": "6WTF1JV5knI.mp4", "size_bytes": 123456789, "sha256": "<đủ 64 hex>"},
 "t0": 12.25, "t1": 12.25, "frame_idx": 367,           // chỉ số frame gốc trong stream; decoder + spec_hash ở context.sample_spec
 "decision": {"seq": 412, "actor": "human:local", "decided_at": "…", "proposal_n": 7},
 "proposal": {"recipe": "diversity", "config_hash": "…"}}
// ảnh (sau MVP, khi hỗ trợ ảnh trong pool)
{"image": {"dataset_id": "ds-…", "dataset_version_id": "dv-…", "relpath": "images/a.jpg", "sha256": "…", "label_status": "none"},
 "decision": {…}, "proposal": {…} | null}
```

`decision` = quyết định `keep` cuối cùng làm mẫu nằm trong kept; `proposal` null nếu quyết định ngoài proposal.
Provenance được **chép vào** snapshot (không tham chiếu catalog) → snapshot vẫn đọc được khi file video bị đổi chỗ.

### 4.4. Bất biến và "hiện hành"

- Không có command sửa/xoá snapshot; trigger DB chặn `UPDATE`/`DELETE` (bảo vệ cả khi có code lỗi).
- `is_current = (members_hash == hash(kept hiện tại)) and (context_hash == hash(context hiện tại))` — đổi pool (thêm video), goal hoặc sample spec
  cũng làm snapshot không còn hiện hành. Snapshot không còn hiện hành **không bị sửa**; muốn bản mới → tạo snapshot mới.
- **Tách khỏi `is_current`:** `sources_available` = mọi nguồn trong snapshot còn tồn tại và đúng hash (kiểm khi được hỏi / trước export, §12) —
  trả `{ok, missing: [source], changed: [source]}`. Snapshot hiện hành vẫn có thể không export được nếu raw đã đổi.
- `decisions_after = max(seq hiện tại) − decision_seq`: thông tin phụ (có quyết định mới nhưng collection có thể không đổi, vd drop frame chưa từng keep).
- GĐ3/GĐ4: report và export nhận `snapshot_id`; "report đã cũ" = snapshot của nó không còn hiện hành.

## 5. API / command contract

Mọi command theo CONTRACTS §11.1 (Pydantic Input/Output, route sinh từ `api/routes.py::ROUTES`). Lỗi dạng `{"error": {"code", "message"}}`.

| Command | Route | Mutates / confirm | Input | Output | Lỗi chính |
|---|---|---|---|---|---|
| `create_snapshot` | `POST /sessions/{sid}/snapshots` | ✓ / ✓ | `{note?}` | `SnapshotDTO` + `created: bool` | `session_not_found` 404, `empty_collection` 422, `collection_changed` 409 |
| `list_snapshots` | `GET /sessions/{sid}/snapshots` | – | – | `SnapshotDTO[]` (mới nhất trước) | 404 |
| `get_snapshot` | `GET /snapshots/{snapshot_id}` | – | – | `SnapshotDetailDTO` (kèm `members`) | `snapshot_not_found` 404 |
| `list_import_folders` | `GET /datasets/import-folders` | – | – | `[{folder, n_images_hint, detected: "yolo"\|"none", classes?}]` (thư mục cấp 1–2 dưới gốc) | – |
| `import_dataset` | `POST /datasets/import` | ✓ / ✓ | `{folder, name?, dataset_id?, annotation_format: "auto"\|"none"\|"yolo", description?}` (đúng một trong `name` / `dataset_id`) | `{dataset: DatasetDTO, version: DatasetVersionDTO, created: bool, report}` | `invalid_folder`, `dataset_name_taken`, `dataset_not_found`, `empty_dataset`, `invalid_annotation`, `too_many_images` |
| `list_datasets` | `GET /datasets` | – | – | `DatasetDTO[]` | – |
| `get_dataset_version` | `GET /datasets/versions/{version_id}` | – | – | `DatasetVersionDTO` + `report` | `dataset_version_not_found` |
| `list_dataset_samples` | `GET /datasets/versions/{version_id}/samples` | – | query `offset, limit≤500, label_status?` | `{total, items: SampleDTO[]}` | 404 |
| `get_image` | `GET /datasets/versions/{version_id}/images/{key}` | – | query `max_side?` | `image/jpeg` | 404, `dataset_file_missing` |
| `queue_prepare_dataset` | `POST /datasets/versions/{version_id}/prepare` | ✓ / – | – | `DatasetVersionDTO` | 404 |
| `verify_dataset_version` | `POST /datasets/versions/{version_id}/verify` | – | – | `{ok, missing[], changed[]}` | 404 |
| `create_session` (sửa) | như cũ | như cũ | thêm `reference: {dataset_version_ids: []}` | `SessionDTO` thêm `reference` | thêm `dataset_version_not_found` 404, `duplicate_dataset_in_reference` 422, `reference_not_prepared` 409 |
| `get_session` / `list_sessions` (sửa) | như cũ | – | – | `SessionDTO.reference`, `SessionDTO.latest_snapshot?: {id, n, is_current}` | – |

DTO chính:

```ts
DatasetDTO        { id, name, description?, created_at, versions: DatasetVersionDTO[] }   // mới nhất trước
DatasetVersionDTO { id, dataset_id, n, created_at, folder, annotation_format, classes?, n_images,
                    label_counts: {labeled, empty, missing, invalid, none}, class_counts?,
                    prepared: boolean, prepare?: PrepareStatus, n_sessions_as_reference }
SampleDTO         { sample_id, key, relpath, width, height, label_status, label_counts?, thumb_url }
SnapshotDTO       { id, session_id, n, created_at, created_by, note?, n_members, members_hash, context_hash, decision_seq,
                    is_current, decisions_after }      // sources_available: command riêng verify_snapshot (đọc file, chậm)
SnapshotDetailDTO SnapshotDTO & { context, members: [{ord, unit_id, source_kind, source_id, provenance}] }
```

Validation thêm cho `create_session`: version tồn tại; mỗi dataset tối đa một version; mọi version `prepared` (giống video đang chuẩn bị → 409).
Q1 = "chỉ video": `pool` vẫn chỉ có `video_ids` (không đổi `PoolFilter`).

## 6. UI tối thiểu (dùng lại thành phần hiện có)

| Màn | Thay đổi | Dùng lại |
|---|---|---|
| Sidebar | "Kho video" → **"Kho dữ liệu"** (`#videos` giữ hash cũ, thêm `#datasets`) | `AppSidebar` |
| Kho dữ liệu → tab **Dataset tham chiếu** | Lưới thẻ dataset (tên, version mới nhất, số ảnh, trạng thái nhãn: "YOLO · 1.200/1.300 ảnh có nhãn · 5 lớp" hoặc "Chưa có nhãn", tiến độ chuẩn bị, số phiên dùng). Nút **"Nhập dataset"** → hộp: chọn thư mục (từ `list_import_folders`), tên, kiểu nhãn (gợi ý theo `detected`) → kết quả import (số liệu §3.5). Bấm thẻ → panel: các version, lưới ảnh (lọc theo trạng thái nhãn), "Nhập version mới", "Kiểm tra file" | `VideosScreen` (lưới, panel, `PrepareStatusLine`), `ui/` (`Panel`, `EmptyState`, `ErrorBanner`) |
| Phiên mới | Khối **"Dữ liệu tham chiếu (tuỳ chọn)"**: chọn dataset (mặc định version mới nhất đã sẵn sàng; đổi version trong menu). Ghi chú cố định: *"Dùng để so sánh trong report ở giai đoạn sau. Chưa ảnh hưởng đề xuất."* | `NewSessionScreen` |
| Thanh phiên | Chip "Tham chiếu: tên vN" (nếu có) | `SessionView` |
| Tab **Xuất** | Khối **Snapshot** trên cùng: nút "Chốt snapshot" (+ ghi chú), danh sách snapshot (số, thời điểm, số mẫu, nhãn **Hiện hành** / **Không còn hiện hành** bằng chữ + icon, không chỉ màu); bấm → bảng mẫu (dùng bảng của "Mẫu đã lưu"). Export hiện có giữ nguyên ở dưới (GĐ4 mới nối với snapshot) | `ExportScreen`, `ArchiveScreen` |
| Studio | Ẩn lựa chọn recipe/baseline (khối "Nâng cao" chỉ còn xem tham số, hoặc bỏ); recipe mặc định theo tên trong config | `CurationSidebar`, `RecipePanel` |

## 7. Migration và compatibility

### 7.1. Bộ chạy migration (`store/db.py`)

- `MIGRATIONS = [(2, SQL_V2), (3, SQL_V3)]`; `SCHEMA_VERSION = max`.
- Mở DB: `user_version == 0` và chưa có bảng → tạo schema mới nhất. `user_version < SCHEMA_VERSION` → **sao lưu** bằng `sqlite3.Connection.backup` sang
  `rav.sqlite.v{old}-{UTC timestamp}.bak` (cùng thư mục) → áp từng migration trong một transaction, cập nhật `user_version` trong cùng transaction.
  Lỗi → rollback, file gốc không đổi, BE dừng với thông báo trỏ tới file backup. `user_version > SCHEMA_VERSION` → từ chối, không ghi.
- Migration chỉ `CREATE TABLE/INDEX/TRIGGER`; **không ghi lại** dòng cũ. Không có migration xoá cột/bảng trong GĐ1.
- Sửa `LOCAL_SINGLE_USER_LIMITS.md` #14: bỏ "xoá DB", thay bằng "migration + backup; chưa có rollback tự động".

### 7.2. Tương thích

| Dữ liệu hiện có | Ảnh hưởng | Bảo đảm |
|---|---|---|
| `session.spec` JSON cũ | Không có `reference` | Đọc mặc định `[]`; không ghi lại |
| `decision`, `proposal` | Không đổi | `get_state`, `list_proposals`, `return_scores`, metric cho kết quả như trước |
| Unit id video, `pool_hash` | Không đổi (`order_key` video = `(video_id, t0)`) | Test vàng |
| Cache field `output/fields/<video_id>/<spec_hash>/…` | Không đổi đường dẫn | Ảnh dùng `output/fields/<dataset_version_id>/<image_spec_hash>/…` — không trùng vì tiền tố `dv-` |
| Export cũ `output/exports/…` | Không đổi | – |
| Execution tách cache theo tiền tố `"{video_id}:"` | Đổi sang nhóm theo `u.source_id` | Kết quả với video giống hệt; test cache cũ vẫn trúng (đếm số lần `compute`) |
| FE | `SessionDTO` thêm trường | Trường mới tuỳ chọn; `tsc` |
| `Context` | `pixels(x)` nhận `Frame` hoặc `ImageRef`; thêm `images: dict[str, ImageSetRef]` | Feature hiện có không đổi |

`image_spec_hash = sha256({"image_reader": {"name": "pil", "version": "1", "exif_transpose": true}, "units": "image"})[:16]` — đổi cách đọc ảnh
(vd bỏ EXIF) là cache khác, đúng tinh thần `spec_hash` của video (CONTRACTS §3). Không thêm kind `decoder` cho ảnh (chỉ có một cách đọc; tách kind khi có cách thứ hai).

## 8. Acceptance criteria và kiểm thử

| # | Nhóm | Kiểm thử (pytest, dữ liệu tổng hợp trong `tmp_path`) | Slice |
|---|---|---|---|
| T1 | Migration | DB v1 dựng bằng `SCHEMA` cũ + vài phiên/quyết định/proposal → mở bằng `DB` mới: có file `.bak`, `user_version` mới, mọi dòng bằng trước, `get_state` trùng | S1 |
| T2 | Migration | `user_version` lớn hơn app → lỗi, file không đổi (so hash) | S1 |
| T3 | Migration | Lỗi giữa chừng (migration giả lỗi) → rollback, file gốc không đổi | S1 |
| T4 | Snapshot | Keep 3 → snapshot có đúng 3 mẫu theo `order_key`, `members_hash` đúng, provenance có `decision.seq`, `proposal.recipe`, tên video | S2 |
| T5 | Snapshot | Gọi lại khi collection không đổi → cùng id, `created = false`; drop một frame **chưa từng keep** → vẫn cùng snapshot | S2 |
| T6 | Snapshot | Keep thêm → snapshot cũ `is_current = false`, nội dung/hash **byte-y-hệt** trước; tạo lại → `n = 2` | S2 |
| T7 | Snapshot | `UPDATE`/`DELETE` thẳng vào `snapshot`, `snapshot_member` → `sqlite3.IntegrityError`/`DatabaseError` | S2 |
| T8 | Snapshot | kept rỗng → 422 `empty_collection`; qua HTTP (`TestClient`) | S2 |
| T9 | Identity | Unit id + `pool_hash` của video tổng hợp giống giá trị ghi trước khi đổi kiểu (test vàng) | S3 |
| T10 | Compat | Cache field video ghi trước khi đổi kiểu vẫn được đọc, feature giả không bị gọi lại | S3 |
| T11 | Identity | Unit ảnh: `video_id/t0 = None`, `source_id` = version, `order_key` theo `key`; `ctx.field` trên unit ảnh với feature giả `per_sample=True` cache theo `(dv-…, image_spec_hash)` | S3 |
| T12 | Import | Thư mục 5 ảnh (1 trùng byte, 1 hỏng) → 3 mẫu, báo cáo đúng; tên thư mục / file có dấu | S4 |
| T13 | Import | Import lại → cùng version, `created = false`; đổi tên một file → version mới, **cùng `sample_id`**; sửa pixel một ảnh → version mới, `sample_id` mới cho ảnh đó; version cũ không đổi | S4 |
| T14 | Import | YOLO: các trạng thái `labeled/empty/missing/invalid` (dòng 6 cột, `cls` ngoài khoảng, toạ độ > 1); `classes.txt` và `data.yaml` (list, dict) | S4 |
| T15 | Import | `annotation_format = none` với thư mục có `labels/` → mọi mẫu `none` | S4 |
| T16 | Import | `folder` = `../x`, đường dẫn tuyệt đối, symlink ra ngoài → `invalid_folder`; 0 ảnh → `empty_dataset` | S4 |
| T17 | Import | UPDATE/DELETE `dataset_version`, `dataset_member` bị chặn | S4 |
| T18 | Prepare | Job chuẩn bị version (feature giả) → `prepared = true`; file ảnh bị xoá → `verify` báo `missing`, đọc pixel → `dataset_file_missing` | S5 |
| T19 | Phiên | `create_session` với reference: chưa chuẩn bị → 409; 2 version cùng dataset → 422; hợp lệ → `SessionDTO.reference`; phiên cũ → `reference = []`; test L5a/L6 cũ vẫn xanh | S6 |
| T20 | FE | `npx tsc --noEmit`; chạy thử tay: import, xem ảnh, tạo phiên có tham chiếu, chốt snapshot, keep thêm → nhãn "Không còn hiện hành" | S7 |
| T1b | Migration / lưu trữ | Keep + tạo snapshot không sinh file ảnh nào trong `output/` | S2 |
| T6b | Snapshot | Thêm video vào pool (collection không đổi) → snapshot cũ `is_current = false` vì `context_hash` khác; nội dung không đổi | S2 |
| T13b | Import | Sửa / làm rỗng file nhãn sau import → `verify` báo `changed`; ảnh trùng byte nhãn khác → `conflict` trong báo cáo, không chọn theo tên | S4–S5 |
| T21 | Export | ZIP chỉ chứa đúng mẫu của snapshot (kể cả khi kept đã đổi sau đó) + manifest khớp | S8 |
| T22 | Export | Video test sinh frame có số thứ tự vẽ trong ảnh → frame xuất đúng `frame_idx` | S8 |
| T23 | Export | Raw thiếu / bị thay → lỗi `source_unavailable` liệt kê nguồn, không có ZIP | S8 |
| T24 | Export | File tạm bị xoá sau thành công, sau lỗi giữa chừng; vượt giới hạn dung lượng → lỗi, dọn | S8 |
| T25 | Export | Export cũ theo kept vẫn chạy (tương thích) | S8 |
| T26 | CLIP | Encode ảnh + text local (checkpoint nhỏ), embedding chuẩn hoá, `params_hash` đổi khi đổi revision | S9 |
| T27 | CLIP | Truy vấn mẫu ("pedestrian at night", "cars on a rainy street") chạy được, trả điểm (không kiểm "đúng") | S9 |
| T28 | Kaggle (mock) | Kết quả thiếu / trùng id, sai shape, NaN → không ghi cache, job lỗi | S10 |
| T29 | Kaggle (mock) | Cache hit → không tạo session; chỉ sample thiếu được gửi | S10 |
| T30 | Kaggle (mock) | Timeout / lỗi → `stop_session` được gọi, file tạm dọn, không fallback CPU | S10 |
| T31 | Kaggle (live, tuỳ) | So embedding local vs Kaggle trên vài ảnh: cos ≥ 0,999 | S10 |
| T32 | Subset | Manifest ≤ 200, tái lập theo seed, reference ∩ pool = ∅; chỉ subset được import / upload | S11 |

Toàn bộ `python -m pytest tests -q` và `ruff` xanh sau mỗi slice (trừ `test_codex_hook.py` đã biết).

## 9. Implementation slices

Mỗi slice = một phiên (AGENTS luật 5): sửa docs cấp 2 tương ứng (§10) → code → test → commit → tick `PLAN.md` → dừng.

| Slice | Nội dung | File / module dự kiến | Kiểm chứng | Chặn bởi |
|---|---|---|---|---|
| **S1** | Bộ chạy migration + backup; schema v2 = bảng `snapshot`, `snapshot_member` + trigger (chưa có command) | `store/db.py`; `tests/test_db_migration.py`; `LOCAL_SINGLE_USER_LIMITS.md` #14; CONTRACTS §5 dòng 13 (store) | T1–T3 | – |
| **S2** | Snapshot: service + command + route; `db.decisions_with_seq` | `service/snapshot.py`, `commands/snapshots.py`, `commands/__init__.py`, `api/routes.py`, `store/db.py`; CONTRACTS §11.2, architecture §5.2–5.3 | T4–T8 | S1 |
| **S3** | Kiểu nguồn ảnh: `ImageRef`, `ImageSetRef`, `Unit` mở rộng (`source_id`, `order_key`), execution nhóm theo `source_id`, `pool_hash` theo `order_key`, `Context.pixels` đọc ảnh (PIL) | `core/types.py`, `core/execution.py`, `core/context.py`; CONTRACTS §2, §3, §4, §13 | T9–T11; toàn bộ test cũ | **Q2** |
| **S4** | Import dataset: schema v3, `pipeline/datasets.py` (quét, hash, nhãn YOLO, manifest), `service/dataset.py`, command `list_import_folders`, `import_dataset`, `list_datasets`, `get_dataset_version`, `list_dataset_samples`, `get_image`; CLI `import-dataset`; `PyYAML` | `store/db.py`, `pipeline/datasets.py`, `service/dataset.py`, `commands/datasets.py`, `cli/__main__.py`, `api/routes.py`, `requirements.txt` | T12–T17 | S1, S3 |
| **S5** | Chuẩn bị feature cho version (dùng hàng đợi P1), `prepared`, `verify_dataset_version` | `service/dataset.py`, `pipeline/datasets.py` (context trên unit ảnh), `commands/datasets.py` | T18 | S4 |
| **S6** | Tham chiếu trong phiên: `CreateSessionIn.reference`, validation, `SessionDTO.reference`, `latest_snapshot` | `service/session.py`, `commands/sessions.py`; CONTRACTS §7, §12.2 | T19 | S5 (Q1 đã chốt #65) |
| **S7** | FE: Kho dữ liệu (tab dataset, hộp import, panel version + lưới ảnh), Phiên mới (tham chiếu), chip phiên, khối Snapshot ở tab Xuất, ẩn baseline Studio | `src/web/src/api.ts`, `screens/VideosScreen.tsx` (hoặc `DatasetsScreen.tsx` mới), `screens/NewSessionScreen.tsx`, `screens/ExportScreen.tsx`, `components/CurationSidebar.tsx`, `types/navigation.ts`; `UI_DESIGN.md` §2–3 | T20 | S2, S6 |

S7 có thể tách hai: S7a (snapshot UI + ẩn baseline, chỉ cần S2) làm sớm; S7b (dataset UI) sau S6.

Slice bổ sung (người dùng yêu cầu 2026-10-07, #64):

| Slice | Nội dung | File / module dự kiến | Kiểm chứng | Chặn bởi |
|---|---|---|---|---|
| **S8** | Export theo snapshot, trích xuất theo yêu cầu (§12): kiểm nguồn → đọc ảnh gốc / seek-decode đúng frame → ZIP tạm có giới hạn → dọn. Export cũ (`csv_images` theo kept) giữ trong lúc chuyển đổi | `service/export.py`, `commands/export.py`, `api/routes.py`, `components/decoders/pyav.py` (kiểm frame đúng chỉ số) | T21–T25 | S2, S3 |
| **S9** | Feature CLIP (image) + mã hoá text local, cùng checkpoint/tokenizer/preprocessing (§11.2) | `components/features/clip_*.py`, helper text encode, `requirements.txt` | T26–T27 | S3 |
| **S10** | Adapter tính embedding từ xa (Kaggle, `kaggle_extend_api`) nối vào prepare/job: chỉ gửi sample thiếu, validate, ghi cache atomic (§11.3) | `pipeline/remote_embed.py` (hoặc `scripts/`), `service/video.py`/`service/dataset.py` (job) | T28–T31 (mock); live tuỳ credentials | S9 |
| **S11** | Smoke subset BDD100K ≤ 200 ảnh (§11.1): script chọn theo seed + metadata, manifest, tách reference / pool | `scripts/bdd_subset.py`, `eval/` | T32 | S4, S10; **dữ liệu BDD100K trên máy (chưa có)**; pool video (#65) |


## 10. Thay đổi contract (mỗi slice áp phần của mình)

### 10.1. Lý do phải đổi contract

- CONTRACTS §2 `Unit` bắt buộc `video_id`, `t0/t1: float`, `kind ∈ {frame, span}`, `anchor: Frame` — không biểu diễn được ảnh độc lập mà không giả timestamp.
- §3 / §13.4: cache `per_sample` "mỗi video một file theo `(video_id, spec_hash)`"; §13.5 thứ tự `(video_id, t0)`; code tách cache bằng tiền tố id. Ảnh cần container
  khác (`dataset_version_id`) và thứ tự không dựa trên thời gian.
- §4 `Context.pixels(frame: Frame)` chỉ đi qua decoder video.
- §5 dòng 13 / LOCAL_LIMITS #14: store "không migration".
- §7 session spec chưa có `reference`; §11.2 thiếu command dataset/snapshot.

### 10.2. Danh sách sửa

| Tài liệu | Mục | Sửa |
|---|---|---|
| CONTRACTS | §2 | `ImageRef`, `ImageSetRef`; `Unit` như §2.1; quy tắc: thành phần cần thời gian (NMS thời gian, `uniform`, `temporal_coverage`, `timeline`, clip ngữ cảnh, telemetry) chỉ áp cho unit có `t0 is not None`; MVP pool chỉ video (Q1) |
| CONTRACTS | §3, §13.4–13.5 | Cache `per_sample` theo `(source_id, spec_hash)`; `image_spec_hash`; thứ tự theo `order_key` |
| CONTRACTS | §4 | `pixels(x: Frame | ImageRef)`; `images` |
| CONTRACTS | §5 dòng 1, 13 | Nguồn tập ảnh nằm trong store (dataset/version), không phải kind `source` mới; store có migration |
| CONTRACTS | §7, §12.2 | `reference.dataset_version_ids` trong session spec, cố định |
| CONTRACTS | §11.2 | Command §5 |
| CONTRACTS | §13 (mới §13.9) | Identity dataset/version/mẫu ảnh §2.2; snapshot §4 |
| architecture | §5.2, §5.3, §9 | Route + DTO §5; module mới |
| UI_DESIGN | §2, §3 | §6 |
| LOCAL_LIMITS | #14 + dòng mới | Migration + backup; import thư mục trên máy BE (chưa upload zip); import đồng bộ |
| PLAN | Lát mới | GĐ1 S1–S7 |
| Log quyết định | #64 | Phạm vi 4 giai đoạn + nguyên tắc người dùng chốt 2026-10-07 + Q1/Q2 khi chốt |
| research-pack/01_origin | – | Ghi yêu cầu đề RAV-26: ≥ 2 vai trò, PII |


## 11. CLIP + tính embedding trên Kaggle + smoke subset BDD100K (S9–S11)

### 11.1. Smoke subset (S11)

- Mặc định **100 ảnh**, giới hạn cứng **200**; seed cố định; lưu manifest (danh sách ảnh, seed, tiêu chí). Giới hạn áp **trước** import / prepare / upload.
- Có metadata (BDD100K `attributes`: `timeofday`, `weather`, `scene`) thì chọn phân tầng theo nhóm; không quét pixel để chọn.
- Chỉ đọc file đã có trên máy; **không tự tải** BDD100K. Máy hiện **không có** bản BDD100K (kiểm 2026-10-07) → S11 chờ người dùng cung cấp đường dẫn / subset.
- Nhãn chỉ đọc cho ảnh trong subset; nhãn pool không vào thuật toán chọn. Reference và pool không trùng ảnh. Đây là smoke test tích hợp, **không** phải benchmark.

### 11.2. CLIP (S9)

- Một checkpoint nhỏ có cả image + text encoder; tên + **revision (commit hash)** ghi trong `Params` của feature → nằm trong `params_hash` (cache key).
  Chọn cụ thể khi làm S9 (ứng viên: `openai/clip-vit-base-patch32` qua `transformers`, pin revision).
- Feature `emb.clip_b32` (`per_sample=True`): preprocessing của chính checkpoint, chuẩn hoá L2. Text encode local cùng checkpoint/tokenizer, cũng chuẩn hoá L2.
- Giữ DINOv2. Chưa quantize. Similarity là điểm tương đồng, không phải xác suất hay xác nhận ODD.

### 11.3. Tính image embedding trên Kaggle (S10)

Adapter là **nơi tính khác cho cùng một feature** (cùng `FieldKey`), không phải feature mới; proposal/UI không biết Kaggle.

1. Chốt danh sách sample thiếu cache + cấu hình feature → kiểm số lượng (≤ 200 ở smoke), kích thước payload (giới hạn cấu hình), cache hit → không tạo job.
2. Ảnh: gửi file gốc của sample; video: trích **tạm** đúng các frame trong manifest thành JPEG/PNG không mất mát (cấu hình ghi trong kết quả), không upload cả video. File tạm dọn trong `finally`.
3. Một session Kaggle (GPU nếu có) mỗi lúc; mini-batch, timeout cấu hình được; không retry vô hạn; `stop_session` trong `finally`, chỉ session do job tạo.
4. Kết quả: `sample_ids` đúng thứ tự, hash nguồn, cấu hình trích xuất / preprocessing, checkpoint + revision, dim, dtype, normalization, job id + trạng thái.
5. Local kiểm: đủ id, không trùng, shape đúng, không NaN/Inf, metadata khớp `FieldKey` → mới ghi cache (atomic, như `FieldStore.put`). Thiếu một phần → không ghi, job lỗi.
6. Credentials từ môi trường; không log token / `jupyter_url`. Kaggle lỗi → job lỗi, **không** âm thầm tính trên CPU.
7. Thiếu credentials / không có GPU → vẫn hoàn thành adapter + test mock + smoke CLIP local nhỏ; báo rõ live chưa kiểm chứng.

`kaggle_extend_api` (thư mục `final/kaggle_extend_api/`) chỉ tham khảo protocol, không import/cài làm runtime của app (người dùng nhắc rõ 2026-10-07).
Adapter trong repo dùng SDK Kaggle chính thức; token và notebook đọc trực tiếp `.env` repo, thiếu thì báo, không mượn auth/notebook từ báo cáo thử nghiệm. GPU live cần kiểm chứng riêng.

## 12. Export theo snapshot, trích xuất theo yêu cầu (S8)

Input: `snapshot_id`.

1. Đọc manifest (`snapshot_member`) + `context` đã đóng băng.
2. Kiểm nguồn: video — tồn tại + `sha256` đầy đủ đúng; ảnh — tồn tại + `sha256`; nhãn liên quan — `label_sha256`. Lỗi → `{"error": {"code": "source_unavailable",
   "sources": [{"source_id", "kind", "problem": "missing"|"changed", "n_samples"}]}}`; **không** bỏ mẫu lỗi, không thay mẫu khác, không trả ZIP thiếu.
3. Trích xuất: ảnh — đọc file gốc; video — decoder theo `context.sample_spec`, seek rồi decode tới **đúng `frame_idx`** (frame giải mã có chỉ số khác
   `frame_idx` → lỗi `frame_mismatch`, không lấy frame kế tiếp).
4. ZIP ghi ra file tạm trong `output/tmp/` (giới hạn dung lượng cấu hình được, ghi tuần tự từng ảnh, bộ nhớ ~ một ảnh): ảnh + `manifest.csv`/`manifest.json`
   (unit id, nguồn, hash, vị trí, provenance) + report nếu đã có report cùng snapshot (GĐ3).
5. Trả file; xoá file tạm sau khi gửi xong, khi lỗi, và quét file tạm mồ côi khi BE khởi động (client ngắt kết nối).

Không giữ thư mục ảnh lâu dài sau export. ZIP không cần giống từng byte giữa các lần; danh sách mẫu và nội dung ảnh phải khớp manifest + cấu hình trích xuất.
Export cũ theo kept (`POST /sessions/{sid}/export`, thư mục ảnh lâu dài) giữ trong thời gian chuyển đổi; bỏ khi UI chuyển hẳn sang snapshot (ghi quyết định).
Luồng bàn giao có approval + report: GĐ3–GĐ4.
