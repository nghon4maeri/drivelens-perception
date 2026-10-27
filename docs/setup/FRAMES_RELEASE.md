# Release/rollback DEP7-Frames (ảnh lưu sẵn + worker tách process)

Contract: [CONTRACTS §21.20](../design/CONTRACTS.md); quyết định #117, #119, #120; plan gốc
[FRAME_MATERIALIZATION_PLAN §7](../archived/2026-10-13/docs/backlog/FRAME_MATERIALIZATION_PLAN.md). Commit trên nhánh `thien-frame-media`:
F1 `6f35b49`, F2 `3189734`, F3 `92fd167`, F4 (commit của lát này). Release hiện tại theo source `b195c61`, gồm fix ổn định `f0d278e`.
Người dùng đã yêu cầu rollout ngày 2026-10-13, bao gồm prerequisite migration/policy và smoke nguồn mới (#123).
Backfill SYSTEM hàng loạt vẫn cần duyệt inventory riêng; nguồn user chỉ chuẩn bị ảnh khi người dùng yêu cầu. Không suy deploy thành push Git.

## 0. Đã kiểm trên local (không phải cloud)

- Backend trên PostgreSQL 16 thật + S3 moto: toàn bộ suite pytest, ruff; FE vitest/tsc/build.
- End-to-end đúng luồng: `tests/test_pilot_frames_e2e.py` — API uvicorn thật (JWT ES256), worker `run` + process con spawn,
  moto server, PostgreSQL thật: upload → ảnh → lưới/frame/clip → cô lập A/B → nguồn cũ chuẩn bị ảnh với process CPU bị giết giữa job
  → nguồn SYSTEM qua CLI → xóa nguồn → không receipt treo, slot FREE. Chạy lại:
  ```powershell
  . .\scripts\use_project_storage.ps1
  $env:RAV_PILOT_TEST_DATABASE_URL = 'postgresql://postgres@127.0.0.1:54329/postgres'
  python -m pytest tests/test_pilot_frames_e2e.py -q --basetemp=output/pytest-tmp      # RAV_FRAMES_E2E_RUNS=5 để có p50/p95
  ```
- Số đo local (máy dev, moto server cùng máy; **không suy ra số cloud**), file `output/frames-bench/e2e-local-*.json`:

  | Chặng (5 lượt, video tổng hợp 30 s 640×360) | p50 | p95 |
  |---|---|---|
  | upload → READY (gồm tách 120 unit/240 ảnh) | 6,8 s | 7,4 s |
  | thumbnail đầu tiên (cold) | 99 ms | 226 ms |
  | thumbnail trong lưới 48 ảnh, 4 song song (cold / warm) | 106 / 105 ms | 165 / 173 ms |
  | cả lưới 48 ảnh (cold / warm) | 1,35 / 1,33 s | 1,45 / 1,43 s |
  | frame đầy đủ | 45 ms | 50 ms |
  | clip ±3 s (mới / cache) | 715 / 37 ms | 963 / 45 ms |
  | nguồn cũ chuẩn bị ảnh 60 s, có giết process CPU giữa job (lease 5 s) | 20,7 s (1 lượt) | — |

  Video thật 354 s 640×360 (1 lượt): upload → READY 81,0 s (1.417 unit, 2.834 PUT+HEAD qua moto HTTP), thumbnail p50 99 ms, clip mới 1,6 s.
  Đo riêng không S3: decode `read_exact` + encode ≈ 11 s, RAM đỉnh 83 MB; process con worker rảnh ≈ 63 MB.
  Thumbnail không còn job `RENDER_MEDIA` nào; số baseline cloud cũ ở [LATENCY_QA](LATENCY_QA.md) đo trên hạ tầng khác, không dùng làm A/B.

## 1. Chuẩn bị (chỉ đọc)

1. Ghi lại digest worker Contabo và combined Render đang chạy, `release.env`, policy version đang gắn cho profile; backup DB theo
   [DEPLOYMENT_STATUS](DEPLOYMENT_STATUS.md). Kiểm job/lease đang chạy, slot, receipt `INTENT/WRITTEN` = 0 trước cửa sổ release.
2. Kiểm ổ đĩa và RAM Contabo: worker giờ có process chính + CPU + MEDIA (+ GPU); kết nối DB tối đa 12 (16 có GPU) thay vì 8.

## 2. Migration và policy (operator, ngoài boot)

1. `python scripts/pilot_migrate.py --database-url <operator-url>` áp `010_pilot_frames.sql` (additive: 2 bảng + job kind `PREPARE_FRAMES`).
   Worker/API bản cũ vẫn chạy được (chỉ kiểm migration chúng biết).
2. Thêm **policy version mới** (bất biến, runbook §5) từ bản đang dùng, thay đổi — **số đề xuất, cần người dùng duyệt**:
   - `chunk_bounds_by_kind.VALIDATE_UPLOAD` và `.PREPARE_FRAMES` = `{"frame_batch_size": 64, "frame_put_concurrency": 8, "max_frame_bytes": 2147483648}`
     (360p 6 phút ≈ 62 MB; 1080p ước ×9 ≈ 0,6 GB/6 phút; trần thời lượng 15 phút).
   - `profiles_by_kind.VALIDATE_UPLOAD.cpu_ms` và `profiles_by_kind.PREPARE_FRAMES` (`gpu_ms: 0`) đủ cho tách ảnh; `run_deadline_ms_by_kind`
     `VALIDATE_UPLOAD`/`PREPARE_FRAMES` (đề xuất 1.800.000 ms). Ước lượng CPU quá thấp làm job dừng vì vượt ngân sách giữa chừng.
   - Gắn profile sang version mới. Job đang chạy giữ snapshot policy cũ; không sửa payload/hold trực tiếp.

## 3. Cửa sổ chuyển API/worker

API cũ enqueue upload không có `payload.frames`, trong khi worker mới yêu cầu trường này. Vì vậy không thay worker khi API cũ còn nhận job.
Sau backup/schema/policy staged: khóa profile theo thứ tự, kiểm không có job hoạt động, tạm disable policy hiện tại để chặn admission;
dừng worker, chuyển combined Render và chờ Live, gắn profile sang policy mới/khôi phục enabled policy cũ, rồi khởi động worker mới.
Job mới lúc worker chưa lên chỉ chờ queue. Không sửa payload/snapshot hoặc hủy job của người dùng để rollout.

Worker:

1. Build image worker từ commit đã review, pin digest; `operator-bootstrap` probe.
2. `worker.env`: đặt `RAV_PILOT_CACHE_MAX_BYTES` theo ổ đĩa (bỏ trống = không dọn cache); xem lại `mem_limit`/`stop_grace_period`.
3. `docker compose ... up -d worker`; kiểm `ps` healthy (health cần process chính + mọi process con), log có `process CPU/MEDIA sẵn sàng`,
   heartbeat ở màn Vận hành. Worker mới vẫn xử lý job `RENDER_MEDIA` thumbnail/frame do API cũ đã xếp.

## 4. Combined Render và kiểm tra sau chuyển

Build/publish image combined cùng commit, deploy theo [COMBINED_WEB_RELEASE](COMBINED_WEB_RELEASE.md); kiểm `/health/ready`, đăng nhập,
Kho dữ liệu (huy hiệu "Ảnh"), upload video ngắn → READY có ảnh, thumbnail/frame/clip, nguồn cũ hiện "Chưa chuẩn bị ảnh" + nút, màn Vận hành.

## 5. Backfill (sau khi 2–4 đạt)

- SYSTEM: `python scripts/pilot_prepare_system_frames.py --database-url <operator-url> --prefix <prefix>` (dry-run) → người dùng duyệt
  số nguồn/ảnh + `inventory_hash` → `--apply --operator-confirm <hash> --policy-version <v>`. Chỉ tính GLOBAL; chạy lại bỏ qua nguồn đã có.
- Nguồn của người dùng: chỉ khi họ bấm "Chuẩn bị ảnh"; không tự backfill hàng loạt.

## 6. Rollback

- **Combined:** đổi về digest cũ. API cũ lại xếp `RENDER_MEDIA` cho thumbnail (worker mới vẫn xử lý được); ảnh lưu sẵn nằm yên.
- **Worker:** đổi về digest cũ chỉ khi không còn job `PREPARE_FRAMES`/upload đang tách ảnh: worker cũ không claim `PREPARE_FRAMES`
  (job chờ tới hạn queue) và không xóa ảnh khi xóa nguồn (ảnh mồ côi + dung lượng chưa trừ). Nếu buộc rollback worker: rollback combined trước,
  hủy job `PREPARE_FRAMES` đang chờ, tạm ngưng xóa nguồn có frame set tới khi worker mới quay lại.
- **Policy:** gắn profile về version cũ (job đang chạy giữ snapshot). **Migration:** additive, không drop bảng/xóa ảnh để rollback.

## 7. Còn thiếu / Hướng tới

- Smoke cloud ngày 2026-10-13 đã kiểm Supabase/S3/Contabo thật: [FRAMES_CLOUD_QA](FRAMES_CLOUD_QA.md).
  Chưa có A/B cùng hạ tầng, browser login/GPU/backfill cloud; không claim nhanh hơn hoặc luồng đã mượt.
- Artifact MEDIA thumbnail/frame cũ do `RENDER_MEDIA` tạo vẫn tính dung lượng (Hướng tới, #120).
- Đường đọc media vẫn khóa profile/nguồn như trước (chưa gộp/bỏ `FOR UPDATE`); số local p50 ≈ 0,1 s/thumbnail chưa cho thấy cần tối ưu thêm.
- Heartbeat telemetry dùng chung component `worker` cho process chính và CPU/MEDIA (bảng heartbeat chỉ có api/worker/gpu).
