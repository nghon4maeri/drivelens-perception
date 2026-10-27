# QA Report: VPS Upload -> Ingest S3 -> READY Pipeline (CONTRACTS §21.30 / Decision #139)

**Date**: 2026-10-15  
**Scope**: Lát 2-3 gộp (VPS Upload -> S3 Ingest -> Frame Extraction -> READY).  
**Hợp đồng đối chiếu**: `docs/design/CONTRACTS.md` §21.30, `020_pilot_vps_upload.sql`.

---

## 1. Mục tiêu kiểm thử

Xác minh toàn bộ chu trình upload và ingest dữ liệu video dung lượng lớn:
1. Capability HMAC-SHA256 tự chứa (self-contained capability token), bảo vệ đường truyền nhị phân VPS.
2. Ghi tuần tự từng chunk 16 MiB xuống file `.part` kèm atomic update `.meta` và `fsync`.
3. Tự phát hiện và từ chối lệch offset (`409 Conflict` + `Upload-Offset: <durable_offset>`).
4. Khôi phục sau sự cố (crash recovery): tự động cắt tỉa (truncate) phần bytes chưa được ack/fsync.
5. Ingest trực tiếp từ file `.part` trên đĩa VPS lên S3, trích xuất frame 4 fps, publish snapshot `READY`.
6. Dọn dẹp an toàn: xóa sạch file `.part` và `.meta` sau khi publish thành công, khi abort hoặc sweep staging.
7. Đảm bảo tính chịu lỗi S3: lỗi upload S3 tạm thời không làm mất file `.part` để worker có thể retry.
8. Giữ nguyên 100% hai transport cũ (`POST`, `MULTIPART` S3 trực tiếp) để rollback.

---

## 2. Kết quả kiểm thử tự động

### A. Test chuyên sâu Lát 2-3 (`tests/test_pilot_vps_upload.py`)
Môi trường: PostgreSQL 16.15 (port 25432 trên RAM drive/disk), Python 3.12 (CPU).

| Test Case | Nội dung | Kết quả | Thời gian |
|-----------|----------|---------|-----------|
| `test_vps_capability_token` | Ký và xác thực HMAC capability, kiểm tra hạn dùng và sai lệch thông số | **PASSED** | 0.05s |
| `test_vps_disk_sequential_write_and_mismatch` | Ghi chunk tuần tự, kiểm tra fsync và từ chối lệch offset | **PASSED** | 0.06s |
| `test_vps_disk_crash_recovery` | Mô phỏng crash/unflushed bytes, verify tự động truncate về offset bền vững | **PASSED** | 0.04s |
| `test_vps_upload_http_api` | FastAPI endpoint `GET /vps-uploads/{id}` và `PUT /chunks` qua TestClient | **PASSED** | 0.08s |
| `test_vps_upload_full_pipeline_and_disk_cleanup` | E2E: Create -> Chunk write -> Complete -> Worker Ingest -> Frames -> READY -> Cleanup | **PASSED** | 3.52s |
| `test_vps_upload_s3_error_preserves_part_file` | Worker gặp lỗi S3 tạm thời: giữ nguyên file `.part` trên đĩa | **PASSED** | 1.84s |
| `test_vps_upload_abort_and_sweep` | Hủy upload và chạy sweep staging: dọn sạch `.part` và `.meta` | **PASSED** | 1.57s |

**Tổng kết suite `test_pilot_vps_upload.py`**: **7 passed in 7.16s**.

---

### B. Regression Suite (Pilot Uploads, Storage Unblocked, Deletion)
Chạy tổ hợp 4 file test liên quan:
`tests/test_pilot_uploads.py tests/test_pilot_delete.py tests/test_pilot_storage_unblocked.py tests/test_pilot_vps_upload.py`

- **Kết quả**: **46 passed in 100.44s** (100% PASS, 0 fail, 0 warning).
- Transport cũ (`POST` và `MULTIPART`) hoạt động bình thường, không suy giảm tính năng.

---

### C. Static Analysis & Frontend Typecheck
- **Ruff Lint**: `python -m ruff check src tests` -> `All checks passed!`
- **TypeScript**: `cd src/web && npx tsc --noEmit` -> `0 errors`.
- **Vitest**: `cd src/web && npx vitest run` -> **34 passed (34 tests)**.

---

## 3. Kết luận
Lát 2-3 gộp **“VPS upload -> Ingest S3 -> READY”** đã hoàn thiện toàn diện, đáp ứng đầy đủ yêu cầu của CONTRACTS §21.30 và Quyết định #139. Sẵn sàng tích hợp và chuyển tiếp sang bước tiếp theo khi có yêu cầu.
