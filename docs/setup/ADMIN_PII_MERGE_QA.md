# QA gộp admin model và PII — 2026-10-17

Candidate local: merge `feat/admin-models` (746eac3f) vào `feat/perception-instability` (888e2e9b). Không push/deploy, không đổi DB/policy/worker đang live.

## Giải quyết xung đột

Giữ cả hai phần PLAN/decision log. Migration theo thứ tự `021_pilot_model_verify.sql` → `022_pii_cache_index.sql`; SQL hai file đã đối chiếu byte sau chuẩn hóa CRLF với hai commit nguồn, không thay nội dung. Manifest schema/checksum và hướng dẫn release đồng bộ. Không thay gates auth/owner, receipt, confirmation hoặc training.

## Kiểm tra trên bản ghép

- PostgreSQL local riêng cổng 25447, cluster `output/admin-pii-merge-pg`, S3 moto; không dùng production DB.
- 66 pass: `test_pilot_models`, `test_pilot_claim_rpc`, `test_pilot_pii`, `test_pilot_export_pipeline`, `test_pilot_objects`, `test_pilot_storage_unblocked`, `test_pilot_delete`. Gồm checkpoint YOLO11n thật, PII weights thật, cache/đúng frame/ZIP và storage/cleanup.
- 15 pass: `test_pilot_api_v2`, `test_pilot_analysis`.
- 29 pass / 1 skip: `test_worker_lanes`, `test_worker_lanes_capacity`, `test_batch_yolo`. Capacity là opt-in, không bật. Lượt đầu trong sandbox bị treo, đã dừng đúng tiến trình test riêng và chạy lại ngoài sandbox thành công.
- Ruff `src tests`, TypeScript, 50 frontend tests và build `VITE_RAV_MODE=pilot` đạt. Build vẫn cảnh báo bundle >500 kB; warning Starlette/AnyIO deprecation có trong pytest.
- Logs: `output/admin-pii-merge/{tests,api,lanes-retry,frontend,pilot-build}.log` (gitignore). Không chạy full suite hay browser acceptance mới cho lần ghép; không claim đã kiểm UI upload end-to-end trên cloud.

## Release riêng

Theo runbook §15: áp cả migration 021 và 022, thêm MODEL_VERIFY vào policy và CPU lane, cập nhật worker rồi FE/API. Resize/cache PII cần worker mới; các export cũ sai identity phải tạo lại từ snapshot, không sửa âm thầm artifact cũ. Bản ghép này chưa live.
