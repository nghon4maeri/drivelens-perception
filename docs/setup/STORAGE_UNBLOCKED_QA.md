# Storage Unblocked & Independent Accounting #138 (Lát 1) — Báo cáo kiểm thử nghiệm thu (CONTRACTS §21.29)

Ngày 2026-10-15. Thuộc lát cắt: **Storage-Unblocked #138 (Lát 1)**.

## 1. Mục tiêu và Tiêu chí nghiệm thu

Triển khai cơ chế chính sách bỏ chặn quota dung lượng và độc lập hóa việc dọn dẹp lưu trữ theo chuẩn CONTRACTS §21.29, phục vụ mở đường cho luồng tải lên video lớn (lên tới 5 GiB/60 phút) và chuẩn bị AI độc lập:

1. **Cờ cấu hình chính sách (Policy Config):**
   - Hỗ trợ cờ `storage_accounting_enabled: boolean` trong cấu hình quota policy (mặc định `true` khi thiếu flag để tương thích ngược 100% với các phiên bản cũ).
   - Khi `storage_accounting_enabled=false`:
     - Chấp thuận `storage_user_bytes: 0` và `storage_global_bytes: 0` trong cấu hình (không bắt buộc số nguyên dương).
     - Tắt hoàn toàn việc kiểm tra hạn mức dung lượng USER và GLOBAL. Không phát sinh mã lỗi HTTP 429 `storage_exceeded` từ việc kiểm tra quota tài khoản.
     - Byte mới không cập nhật `reserved_bytes`/`persisted_bytes` trên tài khoản; xóa byte cũ đã tính vẫn khấu trừ theo receipt gốc.
2. **Bảo toàn Hold, Biên nhận và Quản lý vòng đời (Lifecycle & Tracking):**
   - Vẫn tạo các bản ghi `rav_pilot_storage_hold` (`reserved_bytes`, `receipt_key`, `upload_id`/`job_id`) gắn kèm cờ `storage_accounting_enabled=false`.
   - Mục đích giữ hold: duy trì tính idempotent của upload/job, theo dõi dung lượng thực tế của file để giám sát hạ tầng và phục vụ cơ chế dọn dẹp (cleanup/abort).
   - Khi hoàn tất upload hoặc kết thúc job (`settle`, `release_job_holds`), bản ghi hold được cập nhật `persisted_bytes` và chuyển trạng thái sang `FINAL` bình thường mà không gây ảnh hưởng đến tài khoản dung lượng.
3. **Tương thích ngược và Đóng băng Job/Hold Snapshot (Immutability):**
   - Migration additive `019_pilot_storage_optional.sql`: Thêm cột `storage_accounting_enabled boolean NOT NULL DEFAULT true` vào bảng `rav_pilot_job` và `rav_pilot_storage_hold`, kèm trigger bảo vệ tính bất biến sau khi tạo.
   - Các job và upload mang policy cũ (`storage_accounting_enabled=true`) tiếp tục áp dụng đầy đủ quy tắc kế toán dung lượng nghiêm ngặt theo snapshot lúc tạo.
4. **Xóa nguồn độc lập với Accounting (`pilot_delete`):**
   - Khi xóa nguồn (`DELETE_SOURCE`), chỉ trừ byte đã tính theo hold upload gốc và snapshot job tạo artifact/frame set; không dùng policy job xóa hoặc `min(account.persisted_bytes, size)`.
   - Không có byte đã tính thì không khóa/cập nhật USER/GLOBAL. Có byte đã tính thì vẫn kiểm ledger và trừ đúng phần đó; dữ liệu legacy/import không có producer receipt giữ semantics đã tính quota.
   - Cơ chế dọn dẹp staging (`sweep_staging`), abort multipart và thu hồi pin nguồn tiếp tục hoạt động độc lập, không phụ thuộc vào trạng thái quota.

## 2. Kết quả kiểm thử tại commit 8b53e94a (trước sửa mixed-policy)

### 2.1. Backend Unit & Integration Tests
Lệnh chạy: `. .\scripts\use_project_storage.ps1; $env:RAV_PILOT_TEST_DATABASE_URL='postgresql://postgres@127.0.0.1:25432/postgres'; python -m pytest tests/test_pilot_storage_unblocked.py -v --basetemp=output/pytest-tmp`
Kết quả: **5/5 passed in 5.72s**:
- `test_policy_problem_with_storage_accounting_flag`: Chấp thuận policy với `storage_accounting_enabled=false` và hạn mức = 0; từ chối type không phải boolean.
- `test_storage_reserve_and_settle_unblocked`: Khai báo 50 GiB vượt xa hạn mức, reserve thành công, `rav_pilot_storage_account` giữ nguyên 0 bytes, settle chuyển `FINAL` an toàn.
- `test_storage_reserve_backward_compatibility`: Policy cũ (`storage_accounting_enabled=true`) vẫn giữ nguyên kiểm tra quota nghiêm ngặt, trả 429 `storage_exceeded` khi vượt hạn mức.
- `test_job_and_hold_flag_immutability`: Cột `storage_accounting_enabled` trên `rav_pilot_job` và `rav_pilot_storage_hold` được bảo vệ bất biến bởi PostgreSQL triggers.
- `test_upload_and_delete_with_storage_unblocked`: Toàn bộ vòng đời Upload -> Validate READY -> Delete SUCCEEDED mà không phát sinh lỗi 409 `storage_reconcile_required`.

### 2.2. Regression Test Suites
Lệnh chạy: `python -m pytest tests/test_pilot_uploads.py tests/test_pilot_delete.py tests/test_pilot_storage_unblocked.py -v --basetemp=output/pytest-tmp`
Kết quả: **26/26 passed in 34.81s** (Toàn bộ các ca kiểm thử upload và delete cũ tiếp tục xanh 100%, không phát sinh regression).

### 2.3. Frontend Web Checks & Linter
- Linter: `python -m ruff check src tests` -> **All checks passed!**
- Web Typecheck: `cd src/web && npx tsc --noEmit` -> **passed 0 errors**.
- Web Unit Tests: `cd src/web && npx vitest run` -> **34/34 passed (4 test files)**.

## 3. Sửa mixed-policy sau review (2026-10-15)

- Phạm vi: sửa riêng Lát 1; không làm VPS_DISK/Kaggle/PII, không đổi schema hoặc checksum migration đã áp.
- Raw lấy cờ trên hold của upload thực sự tạo nguồn; upload trùng nội dung không thay receipt này. Artifact/frame set lấy snapshot job tạo chúng; dữ liệu legacy không có producer giữ semantics đã tính quota.
- Một SQL tổng hợp tính byte đã tính, chỉ khóa/trừ USER và GLOBAL khi tổng dương. Không dùng policy job xóa, không trừ `min(...)`. Ledger thiếu vẫn báo `storage_reconcile_required` và không publish tombstone.
- Receipt giữ `released_bytes` vật lý và thêm `accounted_released_bytes`. Test replay kiểm không trừ lần hai.
- Test mới bao phủ: bốn tổ hợp policy tạo/xóa; đổi policy giữa create/complete (raw khác frames); artifact khác policy nguồn; legacy; dedup sau đổi policy; ledger thiếu; GLOBAL đang bị connection khác giữ khóa.
- Red: sáu ca đầu fail trên code cũ; sau sửa, lượt đầu storage/upload/delete **32 passed**. Các lượt regression cuối ghi bên dưới khi hoàn tất.
- PostgreSQL 16.15 test riêng, UTF-8, port 25439; pytest tạo database tạm và tự drop. S3 dùng moto; không chạm DB/S3 live. Helper test và cluster nằm trong `output/`.
- Chưa đo latency cloud, chưa deploy/push; không chạy lại frontend vì không đổi web. Số frontend ở §2 là evidence của commit cũ.

### Kết quả cuối sau sửa

- Regression trên PostgreSQL thật + S3 moto: **95 passed, 1 warning trong 290.93s**. Gồm storage-unblocked, upload, delete, media, frames, frames-prepare, frames-e2e, multipart, features. Warning là Starlette/AnyIO deprecation, không có skip/fail.
- Ba test thêm cuối: **3 passed, 15 deselected trong 9.37s**, gồm dedup hai chiều policy và ledger thiếu khi policy xóa tắt. Tổng **98 test riêng biệt pass** trên phạm vi regression này.
- `python -m ruff check src tests`: **All checks passed**. `git diff --check`: pass.
- Test giữ khóa GLOBAL từ connection khác: xóa nguồn không tính quota hoàn tất trước khi nhả khóa (deadline 3s). Đây là bằng chứng không chờ row lock GLOBAL trong ca này, không phải benchmark latency cloud.
- Không tự sửa ledger đã lệch từ lần chạy code cũ; trường hợp đó vẫn cần đối soát thay vì trừ nhầm dữ liệu khác.

Lệnh pytest (sau source storage và đặt `RAV_PILOT_TEST_DATABASE_URL` tới server test):

```powershell
python -m pytest tests/test_pilot_storage_unblocked.py tests/test_pilot_uploads.py tests/test_pilot_delete.py tests/test_pilot_media.py tests/test_pilot_frames.py tests/test_pilot_frames_prepare.py tests/test_pilot_frames_e2e.py tests/test_pilot_multipart.py tests/test_pilot_features.py -q --tb=short --show-capture=no --basetemp=output/pytest-storage-regression
python -m pytest tests/test_pilot_storage_unblocked.py -q -k 'deduplicated_upload or consistent_ledger' --tb=short --show-capture=no --basetemp=output/pytest-storage-final
```
