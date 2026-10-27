# Dataset & Model Binding #137 (N3) — Báo cáo kiểm thử nghiệm thu (CONTRACTS §21.6, §21.7, §21.16, §21.17)

Ngày 2026-10-15. Thuộc lát cắt: **Dataset-Model-Binding #137 (N3)**.

## 1. Mục tiêu và Tiêu chí nghiệm thu

Triển khai đầy đủ năng lực khai thác kho Dataset & Model tham chiếu (SYSTEM) và cơ chế Binding phiên tương tác (Interactive Reference Binding) theo chuẩn CONTRACTS §21.6, §21.7, §21.16, §21.17:

1. **Kho Dataset & Model Catalog (Read APIs):**
   - Tuyến `GET /api/v2/datasets`: Trả về danh sách dataset SYSTEM kèm các phiên bản (`DatasetVersionDTO`).
   - Tuyến `GET /api/v2/datasets/{id}`: Tra cứu chi tiết một dataset cụ thể kèm các phiên bản.
   - Tuyến `GET /api/v2/models/{id}`: Tra cứu chi tiết model asset kèm các khai báo quan hệ provenance (`TRAIN`, `FINETUNE`) với các `dataset_version_id` tương ứng.
2. **Kiểm tra ràng buộc tham chiếu (Reference Validation):**
   - Khi tạo phiên hoặc cập nhật tham chiếu: Model chỉ hợp lệ nếu có khai báo quan hệ (`TRAIN` hoặc `FINETUNE`) với ít nhất một `dataset_version_id` trong danh sách tham chiếu.
   - Mỗi dataset chỉ được chọn tối đa một version trong một phiên. Nguồn dataset phải ở trạng thái `READY`.
   - Nếu vi phạm: Trả về HTTP 422 `reference_invalid`.
3. **Cơ chế Binding phiên tương tác (CAS & Pin Management):**
   - Tuyến `GET /api/v2/sessions/{id}/reference`: Tra cứu dataset versions và model đang liên kết với phiên.
   - Tuyến `PATCH /api/v2/sessions/{id}/reference`: Cập nhật hoặc gỡ bỏ (`reference: null`) tham chiếu phiên qua CAS `expected_state_version`. Trả về HTTP 409 `state_version_conflict` nếu phiên bản không khớp.
   - Khi cập nhật thành công: Tăng `state_version` của phiên, giải phóng (`released_at = now()`) các pin cũ của `REFERENCE`, thiết lập pin mới cho nguồn dataset vừa liên kết.
4. **Cô lập và bảo mật (RLS & Tenancy):**
   - Người dùng khác (User B) không thể tra cứu hoặc cập nhật reference của phiên thuộc User A (trả về HTTP 404).
5. **Cơ sở dữ liệu (Migration 018):**
   - Cấp quyền `DELETE` trên bảng `rav_pilot_session_reference` cho role `rav_pilot_api`.
   - Cập nhật trigger function `rav_pilot_pin_guard()` cho phép giải phóng pin `consumer_kind = 'REFERENCE'`.
   - Thay thế ràng buộc UNIQUE tuyệt đối trên `rav_pilot_source_pin` bằng partial unique index `WHERE released_at IS NULL` để hỗ trợ chu kỳ pin/unpin/re-pin.

## 2. Kết quả kiểm thử

### 2.1. Dataset & Model Binding Tests
Lệnh chạy: `. .\scripts\use_project_storage.ps1; $env:RAV_PILOT_TEST_DATABASE_URL='postgresql://postgres@127.0.0.1:25432/postgres'; python -m pytest tests/test_pilot_dataset_model_binding.py -v --basetemp=output/pytest-tmp`
Kết quả: **4/4 passed in 8.50s**:
- `test_catalog_datasets_and_models`: Tra cứu danh mục datasets, versions, models và model provenance declarations.
- `test_create_session_with_reference_validation`: Xác thực tính toàn vẹn tham chiếu khi tạo phiên (từ chối model không liên quan với HTTP 422, chấp thuận model có quan hệ TRAIN và kích hoạt pin nguồn dataset).
- `test_update_session_reference_cas_and_unpin`: Kiểm thử cập nhật tham chiếu phiên qua CAS `expected_state_version`, phát hiện xung đột 409, giải phóng pin nguồn cũ khi chuyển về `None`, và kiểm tra `state_version` tăng tịnh tiến.
- `test_api_v2_reference_and_catalog_routes`: Kiểm thử toàn bộ 5 route REST API qua TestClient, kiểm tra cô lập đa tài khoản (User B nhận 404).

### 2.2. Frontend Web Checks
- TypeScript compilation: `cd src/web && npx tsc --noEmit` -> **passed, 0 errors**.
- Vitest unit tests: `cd src/web && npx vitest run` -> **34 passed (4 test files)**.
- Client SDK `src/web/src/api_v2.ts`: Đã bổ sung types `DatasetVersionV2`, `DatasetV2`, `ModelDeclarationV2`, `ModelDetailV2`, `SessionReferenceV2` và các phương thức `datasets()`, `dataset(id)`, `model(id)`, `sessionReference(sid)`, `updateSessionReference(sid, body)`.

### 2.3. Linter & Toàn bộ Test Suite Repo
- Linter: `python -m ruff check src tests` -> **All checks passed!**
- Full pytest suite: `python -m pytest tests -q --ignore=tests/test_codex_hook.py --basetemp=output/pytest-tmp`
  -> **583 passed, 26 skipped, 1 warning in 476.43s (0:07:56)**.
  -> Không có bất kỳ regression nào trên toàn bộ 583 test cases.
