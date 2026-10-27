# Progress và ETA thực tế #135 (N2) — kiểm thử

Ngày 2026-10-15. Contract: [§21.28](../design/CONTRACTS.md).

## Phạm vi thực hiện

- Chuẩn hóa các pha xử lý của tác vụ worker: `DOWNLOADING` → `PROBING` → `SAVING_CANONICAL` → `EXTRACTING_FRAMES` → `FINALIZING`.
- Thêm migration additive `016_pilot_progress.sql`: bổ sung cột `progress_detail JSONB NOT NULL DEFAULT '{}'::jsonb` vào bảng `rav_pilot_job`.
- Telemetry chi tiết: `phase`, `done`, `total`, `fps`, `elapsed_s`, `eta_s`, `detail`.
- Đo lường và tính toán tốc độ thực tế trong pha trích xuất frame (`materialize`):
  - `fps = round(units_done / max(elapsed_s, 0.001), 2)`
  - `eta_s = round(remaining_units / max(fps, 0.1), 1)` khi `units_done > 0`; `None` khi chưa đủ dữ liệu hoặc đang khởi động.
- Bảo vệ liveness heartbeat: Heartbeat nền (ping liveness với `done=0, total=0, phase="working"`) chỉ gia hạn lease/slot, không ghi đè làm mất hoặc lùi tiến độ mà worker đã cập nhật.
- Bảo vệ giảm tiến độ: Heartbeat hoặc update tiến độ cố tình hạ `done < job["done"]` bị từ chối với lỗi `validation_error` (422).
- API DTO & Single-poll:
  - `UploadCreatedDTO` giữ đúng các trường tạo ban đầu (`{"id", "state", "validation_job_id", "source_id", "error", "transfer", "declared_bytes", "filename"}`).
  - `UploadDTO` tự động làm giàu trường `progress` từ validation job khi upload ở trạng thái `VALIDATING`, cho phép frontend chỉ cần một vòng lặp polling duy nhất qua `GET /api/v2/uploads/{id}`.
- Giao diện người dùng:
  - Frontend hiển thị thanh tiến độ mượt mà từ 0% đến 100% qua các giai đoạn tải lên và xử lý.
  - Nhãn trạng thái hiển thị chi tiết bằng tiếng Việt: số frame đã xử lý / tổng frame, tốc độ `fps`, và thời gian còn lại định dạng dễ đọc (vd: "~45s", "~1m 20s").
  - Quota CPU không áp dụng theo §21.26; chỉ đo thời gian và fps phục vụ UX.

## Kết quả kiểm thử

- **Suite chuyên biệt `tests/test_pilot_progress_eta.py`**:
  - `test_migration_016_adds_progress_detail_column`: **PASSED**
  - `test_update_progress_and_get_job`: **PASSED**
  - `test_liveness_heartbeat_preserves_job_progress`: **PASSED**
  - `test_heartbeat_rejects_progress_decrease`: **PASSED**
  - `test_upload_dto_enriches_validation_progress`: **PASSED**
  - `test_worker_validate_upload_pipeline_with_progress`: **PASSED**
- **Nhóm regression pilot liên quan (`queue`, `uploads`, `api_v2`, `progress_eta`, `multipart`)**: **58 passed**, 0 failed.
- **Full kiểm tra môi trường dev (`python scripts/check_dev.py --ignore-known-windows-hook`)**:
  - Ruff lint & format: **All checks passed!**
  - Pytest full backend regression trên PostgreSQL 16 cluster test local cổng 25432: **572 passed, 26 skipped, 0 failed**.
  - TypeScript compiler: **tsc --noEmit PASSED**.
  - Vitest web tests: **34 passed (34)**.
  - Web production build (`vite build` cả local và pilot): **PASSED**.
