# DEP5 — kế hoạch thực thi: chuẩn bị đặc trưng → curation trên worker → FE pilot

Ngày 2026-10-10. Quyết định #94 (sửa sau khi Duy thêm DEP4F #92 và DEP-UI #93). Bản kế hoạch để duyệt — **chưa phải hợp đồng**: mỗi lát khi bắt đầu phải chốt phần thiết kế còn để ngỏ
vào CONTRACTS §21 (mục mới cho lát) trước khi code (AGENTS luật 1). Nguồn: PLAN dòng DEP5, execution plan §4 (gate A06),
pilot spec §18 D5, runtime spec §3.2/§4/§5/§7, UI-export spec §2/§9, CONTRACTS §15–18, §21.11–21.13, quyết định #89/#91–#93.

## 0. Phạm vi chung

- **Không train model.** DEP5 chỉ chạy inference bằng model có sẵn (DINOv2, CLIP, YOLO) và thuật toán chọn frame hiện có trên worker
  thay vì trên API. Train/đánh giá downstream là backlog B3, ngoài lộ trình DEP.
- **Không đổi công thức core** (`pipeline/propose.py`, component, `FieldKey`, `spec_hash`, ordered unit IDs): mọi handler gọi lại code core;
  gate là parity với local (picks/ID/thứ tự khớp 100%, điểm sai số ≤ 1e-6).
- API (Render) giữ import nhẹ: không load model, không đọc NPZ/ma trận, không decode. Worker (Contabo) làm tính toán; GPU qua Kaggle.
- Kết quả nghiệp vụ chỉ ghi trong transaction publish của runtime (hook theo kind như DEP4), có kiểm lease/fence/hủy/ngữ cảnh.
- CPU thực dùng = `time.process_time` (user + system của process), không phải thời gian thực.
- Test: PostgreSQL thật + S3 `moto` + transport Kaggle giả; GPU/S3/Supabase thật chỉ chạy smoke riêng khi người dùng xác nhận.
- Đánh số migration theo thứ tự làm: 004 đã là `004_pilot_worker_objects.sql` (DEP4F); **005** (DEP5a), **006** (DEP5b), export dời **007** (DEP6).

## 1. Thứ tự và phụ thuộc

```
DEP4 + DEP4F (upload/media/mẫu/worker entry — xong), DEP-UI (đăng nhập/quota badge/phiên mẫu — xong)
  └─► DEP5a đặc trưng DINO/CLIP (artifact FEATURE + Kaggle + ngân sách GPU)
        └─► DEP5b curation trên worker (PROPOSE cần đặc trưng; YOLO; METRICS)
              └─► DEP5c FE phần còn lại (job thật thay placeholder, upload, media lười)
```

Đề xuất trên video vừa upload cần `emb.dinov2_s` + `hash.phash` (và `emb.clip_b32` cho recipe text) — chúng do PREPARE tạo, nên 5a phải trước 5b.
FE cần route của 5a/5b và DEP4, nên 5c cuối.

## 2. DEP5a — chuẩn bị đặc trưng (PREPARE_DINO / PREPARE_CLIP)

**Mục tiêu:** nguồn PRIVATE (upload) và SYSTEM (mẫu) có đặc trưng hợp lệ, lưu thành artifact FEATURE có manifest; `SourceDTO.dino/clip.ready` đọc từ đó.

**Docs trước:** CONTRACTS §21.14 (5a): payload PREPARE, manifest FEATURE, readiness, vòng đời khởi chạy Kaggle, ngân sách GPU, import đặc trưng SYSTEM.

**Migration `005_pilot_features.sql`**
- `rav_pilot_gpu_budget_snapshot` (runtime spec §3.2): `provider_account`, `observed_at`, `remaining_ms`, `reset_at`, `usage_baseline_seq`,
  `external_session_active`, `operator_id`, `evidence_ref`, `policy_version`.
- `rav_pilot_gpu_hold`: `attempt_id` UNIQUE, `snapshot_id`, `reserved_ms`, `consumed_ms`, `released_ms`, `state OPEN|FINAL`.
- Quyền: worker INSERT/UPDATE `rav_pilot_artifact` kind FEATURE (đã có), gpu_hold, snapshot (worker tự đọc Kaggle SDK, #94); CLI operator chỉ dự phòng.

**Route**
- `POST /api/v2/sources/{id}/preparations {feature: "DINO"|"CLIP"}` + Idempotency-Key: artifact FEATURE READY cùng nguồn/feature/spec
  → 200 `{source_id, feature, ready: true, job_id: null}`; chưa có → enqueue `PREPARE_DINO|PREPARE_CLIP` (queue GPU) → 202 JobDTO.
- `SourceDTO.dino/clip = {ready, job_id}`: ready từ artifact READY; job_id = job PREPARE đang chạy của chính owner.

**Payload đóng băng lúc enqueue:** source UUID, `core_source_id`, `raw_key`, `raw_sha256`, `resource_version`, feature
(`DINO` = `emb.dinov2_s` + `hash.phash`; `CLIP` = `emb.clip_b32`), checkpoint/revision đã pin, `spec_hash`, `n_units`.

**Worker GPU** (tái dùng `pipeline/remote_s3_embed.py`, `pipeline/s3_clip_worker.py`, kiểm `remote_embed.validate_result`)
- Tái dùng transport C1/C2 (`remote_s3_embed.compute`) nhưng trạng thái lưu vào attempt pilot (launch_state/marker/session_id), key S3 theo `jobs/<job>/<attempt>/`.
- Mỗi slot GPU một notebook private cố định (#94); đối soát = trạng thái session của notebook đó.
- Vòng đời Kaggle theo runtime spec §7.1, **thay `KaggleLifecycleAdapter` giả trong RAM** (hiện trả `ABSENT_VERIFIED` khi không biết → có thể mở trùng GPU):
  ghi launch intent (marker `pilot:<deployment>:<job>:<attempt>:<fence>`) trước khi tạo session → ghi handle bất biến → `reconcile_launch(marker)`
  hỏi provider thật: `FOUND` gắn lại, `ABSENT_VERIFIED` chỉ khi có bằng chứng, còn lại `UNKNOWN` (giữ slot, chặn mở mới, báo operator).
  Dừng chưa xác minh → `STOP_PENDING`, slot GPU chưa FREE.
- Supervisor đọc `get_accelerator_quota_statistics` định kỳ → snapshot (`remaining = total − used − reserved`, `reset_at = quota_refresh_time`).
- **Ngân sách GPU toàn cục:** claim GPU chỉ khi snapshot còn tươi (`gpu_snapshot_fresh_ms`) và
  `remaining − usage sau baseline − gpu_hold OPEN − gpu_headroom_ms ≥ ước lượng`; không đủ/không tươi → `WAITING_RESOURCE` `gpu_budget_unverified`.
- Kết quả NPZ: kiểm shape/dtype/finite/L2/ordered IDs/pHash/pixel probes → artifact FEATURE (PRIVATE theo owner; SYSTEM dùng chung, chỉ đọc)
  với manifest `{source_id, core_source_id, feature, field_keys, spec_hash, checkpoint, ordered_ids_hash, npz_sha256}`.
  Worker khôi phục vào FieldStore theo namespace owner/resource khi cần.
- **Nguồn SYSTEM:** người dùng không chạy PREPARE (409 `system_feature_missing` nếu thiếu); operator import đặc trưng tính sẵn qua
  `scripts/pilot_import_demo.py` (kiểm ordered IDs/shape/finite/L2 với nguồn, dry-run + `--operator-confirm`), artifact FEATURE scope SYSTEM.
- Usage: GPU = thời gian session từ tạo tới dừng đã xác minh; CPU = phần điều phối của worker.

**Test / gate A05:** cache hit không khởi chạy; FOUND/ABSENT/UNKNOWN; mất ACK sau khi tạo session; stop chưa xác minh giữ slot; NPZ hỏng/sai ID không publish;
snapshot GPU cũ chặn; A/B không thấy artifact của nhau; SYSTEM dùng chung chỉ đọc; readiness DTO đúng. Smoke GPU thật: riêng, cần xác nhận.

## 3. DEP5b — Studio chạy trên v2 với đề xuất trên worker (#95)

Rà UI DEP-UI (#93): chỉ đăng nhập/quota/danh sách mẫu/fork gọi `/api/v2`; Studio, sidebar, kho video, phân tích vẫn gọi `/api/v1`
(app pilot không mount) → sau fork Studio không tải được. Studio cần thêm route đọc, không chỉ job PROPOSE.

**Docs trước:** CONTRACTS §21.15.

- **Recipe pilot** (không cần model): `random`, `uniform`, `diversity` (DINO), `text_diversity` (DINO + CLIP; câu mã hóa bằng CLIP text trên worker CPU).
  `reference_diversity`, `model_guided`, `text_model_guided` → DEP5b2 (pilot chưa có đường đưa model/dataset vào).
- **Migration `006_pilot_curation.sql`**: worker INSERT `rav_pilot_proposal`; cột `job_id` (truy job → proposal).
- **Route**: `GET /recipes` (catalog tĩnh của pilot, API không import registry/numpy), `POST /sessions/{id}/proposals` → 202 job,
  `GET /sessions/{id}/proposals`, `GET /sessions/{id}/proposals/{n}` (picks + giải thích đợt + scores nếu yêu cầu),
  `GET /jobs/{id}/result`, `POST /jobs/{id}/retry`. Không cần route units (unit ID mang `core:t0-t1`; ảnh qua media request DEP4).
- **API kiểm nhẹ**: recipe trong danh sách pilot, cấu trúc config (đúng loại component của recipe, không features/candidate_stage),
  `batch_size`, `expected_state_version`, một job đề xuất đang chạy/phiên, pool đủ đặc trưng (thiếu → 409 `feature_not_ready` kèm nguồn/feature).
  Worker kiểm đầy đủ bằng registry → FAILED `invalid_config`.
- **Đóng băng** lúc enqueue: `state_version`, `decision_seq`, `proposal_count`, recipe/config, seed (server sinh nếu thiếu), artifact FEATURE từng nguồn.
- **Worker** (queue CPU): raw cache theo SHA → Context core; field chỉ đọc từ artifact (cache-only, không tự tính); state = replay decision
  `seq ≤ decision_seq`; proposal cũ `n ≤ proposal_count` → `pipeline/propose.py`; giải thích đợt tính cùng job.
- **Publish**: khóa phiên, ba số còn khớp → proposal `n = proposal_count + 1` (không đổi `state_version`); lệch → FAILED
  `proposal_context_changed`, không ghi, vẫn tính CPU. FE khóa thao tác duyệt khi job đề xuất đang chạy (DEP5c).
- **Test / gate:** parity với local (picks/ID/thứ tự 100%, điểm ≤ 1e-6); ngữ cảnh đổi; một job/phiên; hủy tính CPU; A/B; 429; `feature_not_ready`;
  **A06**: 3 tài khoản cùng job nặng, keep/drop API vẫn nhanh, claim xoay vòng.

## 4. DEP5c — chuyển Studio sang v2 (#95)

**Docs trước:** UI_DESIGN mục deploy + CONTRACTS nếu đổi DTO.

- Sửa lệch DTO của DEP-UI: fork trả `{session: {id}}` (FE đọc `.id`), số đếm mẫu `counts.*`, quyết định dùng khóa `changes`.
- Lớp chọn v1/v2 theo chế độ build, giữ interface hook (`useSessionData`…) để bản local không đổi; sidebar/phiên/kho nguồn dùng v2 ở pilot.
- Đề xuất: 202 → poll job → nạp đợt; khóa duyệt khi đang chạy; Hủy/Thử lại; `feature_not_ready` → nút "Chuẩn bị DINO/CLIP" (DEP5a).
- Ảnh frame/thumbnail/ngữ cảnh qua media request (DEP4); upload trực tiếp.
- Pilot ẩn YOLO, Phân tích, bản đồ embedding/timeline cho tới DEP5b2 (không hiện thông báo lỗi giả).
- Rà đăng nhập theo UI-export spec §2. **Test / gate FE-AUTH, FE-JOB, FE-TEMPLATE** + `tsc`/`build`; smoke 3 tài khoản.

## 4b. DEP5b2 — YOLO, recipe cần model, phân tích (#95)

- Quyết định trước: đưa model vào pilot (operator seed model SYSTEM hay route upload) + khai báo model↔dataset.
- YOLO_BATCH (evidence theo proposal/model/runtime), `reference_diversity`/`model_guided`/`text_model_guided`.
- METRICS bấm mới tính, cache theo `decision_seq` (không tự chạy sau mỗi quyết định như local).
- Bản đồ embedding/timeline: cần field `proj.pca2` (thêm vào đặc trưng hoặc tính ở worker).

## 5. Ngoài DEP5

Train/đánh giá downstream (B3); export/PII/báo cáo/xóa vật lý (DEP6); build/deploy/backup/chi phí/admission (DEP7);
seed BDD + checkpoint thật + khai báo model↔dataset (việc operator trước DEP8); VLM lọc theo mục tiêu (chưa có lát).

## 6. Câu hỏi

| Lát | Câu hỏi | Trạng thái |
|---|---|---|
| 5a | Nguồn ngân sách GPU | Chốt #94: worker đọc Kaggle SDK |
| 5a | Đối soát session Kaggle | Chốt #94: 1 notebook riêng/slot GPU |
| 5a | Đặc trưng nguồn SYSTEM | Chốt #94: operator import sẵn |
| 5a | Smoke GPU thật | Chốt #94: để riêng, khi người dùng bảo |
| 5a | Trạng thái lát | **Xong 2026-10-10** (CONTRACTS §21.14); còn smoke GPU thật |
| 5b | Mức kiểm ở API | Chốt #95: recipe/cấu trúc/giới hạn/đặc trưng ở API, registry ở worker |
| 5b | Ngữ cảnh đổi khi đề xuất chạy | Chốt #95: nghiêm (FAILED), FE khóa duyệt khi chờ |
| 5c | Sửa lệch DTO của DEP-UI | Chốt #95: làm ở đầu DEP5c |
| 5b | Trạng thái lát | **Xong 2026-10-10** (CONTRACTS §21.15) |
| 5c | Trạng thái lát | **Xong 2026-10-10** (CONTRACTS §21.16); smoke 3 tài khoản ở mức HTTP trên stack local |
| 5b2 | Trạng thái lát | **Xong 2026-10-10** (CONTRACTS §21.17); recipe cần dataset train để Hướng tới (#97) |
| 5c | Thêm `vitest` cho FE? | Chưa hỏi |
