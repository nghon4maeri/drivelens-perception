# PII Masking & Snapshot Export #136 — Báo cáo kiểm thử nghiệm thu (CONTRACTS §21.8)

Ngày 2026-10-15. Thuộc lát cắt: **PII-Export #136** (Hạng 1 ROI theo PLAN).

## 1. Mục tiêu và Tiêu chí nghiệm thu

Triển khai đầy đủ luồng che mờ thông tin cá nhân (PII Redaction) và xuất gói dữ liệu Snapshot bất biến đã chốt theo chuẩn CONTRACTS §21.8:
- **Snapshot bất biến**: Đầu vào bắt buộc phải từ Snapshot đã chốt (tránh trôi dữ liệu và tuân thủ ranh giới bảo mật).
- **Vòng đời trạng thái**: `REQUESTED → PROCESSING → NEEDS_CONFIRMATION → FINALIZING → READY`.
- **Worker EXPORT_BUILD**:
  - Trích xuất frame full-res (materialized frame hoặc trực tiếp từ video container).
  - Thuật toán che mờ PII: YuNet (khuôn mặt) và ONNX/YOLO (biển số xe) kèm fallback deterministic phục vụ CI/test.
  - Hàm `redact()` kết hợp làm mờ hộp Pixelate 16px và Gaussian Blur (`ksize=31`, `sigma=11`).
  - Chuẩn hóa canonical payload bytes (RFC-8785 float64 roundtrip) để tính `payload_hash` SHA-256 bất biến.
  - Sinh `report.html` tự chứa (offline inline CSS, số liệu phát hiện, checklist PII) và `manifest.json`.
- **Owner Confirmation Gate (§21.8)**:
  - Chủ sở hữu review ảnh preview và báo cáo trước khi chốt.
  - Chỉ chấp thuận chuyển sang `FINALIZING` khi cung cấp đúng `payload_hash` (từ chối 409 `payload_mismatch` nếu sai).
- **Worker EXPORT_FINALIZE**:
  - Đóng gói toàn bộ ảnh đã che mờ + `report.html` + `manifest.json` thành file ZIP bất biến.
  - Đẩy lên S3 key canonical (`private/{owner_id}/exports/{export_id}/bundle.zip`), cập nhật trạng thái `READY`.
  - Cung cấp Presigned GET URL tải file ZIP.

## 2. Kết quả kiểm thử

### 2.1. Backend Unit & Pipeline Tests
- `tests/test_pilot_pii.py`: **5 passed in 0.34s**
  - `test_iou_and_deduplicate`: thuật toán IoU và NMS lọc trùng box PII.
  - `test_redact_pixels_and_dimensions`: che mờ pixelate + Gaussian blur, bảo toàn kích thước ảnh.
  - `test_pii_detector_fallback`: cơ chế fallback an toàn khi chạy môi trường không có GPU/weights.
  - `test_canonical_export_payload_determinism`: tính canonical payload hash SHA-256 nhất quán 100%.
  - `test_generate_export_report_html`: sinh báo cáo HTML tự chứa hợp lệ.
- `tests/test_pilot_export_pipeline.py`: **2 passed in 5.96s**
  - `test_export_lifecycle_full_pipeline`: Vòng đời đầy đủ `REQUESTED → NEEDS_CONFIRMATION → Gate review & confirm (bắt đúng 409 payload_mismatch) → FINALIZING → READY → Download`.
  - `test_export_api_v2_endpoints`: Kiểm thử REST API v2 (`/api/v2/snapshots/{id}/exports`, `/exports/{id}`, `/review`, `/confirm`, `/download`), kiểm tra phân quyền cô lập giữa các user (user B bị chặn 404).

### 2.2. Frontend Web Checks
- TypeScript compilation: `cd src/web && npx tsc --noEmit` **passed 0 errors**.
- Vitest unit tests: **34 passed (4 test files)**.
- Giao diện `ExportScreen.tsx`: Đã hoàn thiện giao diện chọn snapshot, review ảnh đã che mờ, nút xác nhận Gate và tải file ZIP.

### 2.3. Linter & Static Analysis
- `python -m ruff check src tests` **passed** (All checks passed).
