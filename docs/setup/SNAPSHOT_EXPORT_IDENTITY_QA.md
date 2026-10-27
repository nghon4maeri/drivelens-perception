# Snapshot/export: đúng frame và lịch sử

2026-10-16, candidate local; chưa deploy FE/API hoặc worker cho lát này. CONTRACTS §21.8.

## Lỗi xác nhận

- Export tìm `variant='frame'` trong DB lưu `FRAME`. Raw fallback cố parse suffix `_idx` nhưng unit thật là
  `core:t0-t1`; parse lỗi → dùng ordinal của snapshot làm frame index. Vì thế ảnh mang đúng unit ID nhưng pixel sai.
  Fallback cuối còn trả ảnh nền giả. Manifest cũng ghi ordinal vào `frame_idx`.
- ExportScreen và SnapshotPanel giữ hai danh sách snapshot riêng; tạo #2 không refresh dropdown xuất.
  Active export chỉ nằm trong React state, không đọc lịch sử khi remount/reload.
- Chẩn đoán chỉ đọc đúng phiên người dùng báo: #1 có 28 member, #2 có 75; hai export đều chọn #1,
  cùng NEEDS_CONFIRMATION, build job SUCCEEDED. Thời gian tạo → publish 216.29 s và 239.26 s.
  Có sẵn 75 FRAME + 75 THUMBNAIL; không mất snapshot/export trong DB. Không sửa dữ liệu người dùng.

## Sửa

- Hai truy vấn metadata cho đúng selected members, kiểm nguồn owner/SYSTEM và availability; chọn READY FRAME
  đúng raw SHA/sample spec/encoding; download kiểm SHA. Không tải raw khi ảnh đã materialized.
- Legacy raw-only: kiểm SHA, giải unit ID theo lưới decoder, index stream thật; tải một lần mỗi nguồn và decode tiến
  bằng iterator; đóng container khi kết thúc/hủy/lỗi. Không dùng ord làm index, không sinh ảnh thay thế.
- Metadata output giữ frame index thật. Giữ PII detector thật, lease/publish và confirmation gate.
- Endpoint lịch sử theo phiên trả Page metadata mới nhất trước, RLS, limit/cursor; không trả mọi member/ảnh.
  UI khôi phục export, progress job, review hoặc link download; tạo snapshot chọn ngay snapshot mới.
  Mở lại READY xem được preview; đổi phiên remount để không lẫn state. Preview tải lazy.
- Thêm timing frame_load / pii / preview_put, progress elapsed/ETA tính theo số frame đã xử lý thực.

## Bằng chứng

- PostgreSQL riêng localhost: **27 passed** (export pipeline, PII, snapshot, snapshot export), gồm
  lifecycle model PII ghim SHA thật; so byte ảnh sau redact với đúng FRAME của các timestamp xa nhau;
  raw fallback nhiều vị trí, đọc lùi, nguồn khác; owner/checksum/unit không hợp lệ; lịch sử phân trang/RLS.
- Ruff src/tests; TypeScript, **50 Vitest** và build frontend local + **pilot** đạt trên code cuối (log trong output/snapshot-fix).
- Browser fixture local dùng ExportScreen/SnapshotPanel thật, API fixture lưu giả lập độc lập production:
  tạo #2 → dropdown chọn #2 ngay; reload REQUESTED giữ export ID và 1/2 progress;
  reload NEEDS_CONFIRMATION hiện đúng 2 preview; confirm fixture → reload READY giữ lịch sử/link/preview.
  Code cuối: click lại export READY đang chọn không làm mất preview.
  Fixture không thay thế kiểm thử auth/worker/S3; các phần đó chạy qua PostgreSQL+moto ở trên.
- Candidate đọc 3 FRAME thật của đúng phiên trên Contabo, detector hiện có, BULK 1 thread; không PUT/DB write/restart:

| Exact frame index | Ordinal | Load s | PII s | PNG s |
|---|---|---|---|---|
| 1845 | 0 | 0.943 | 1.416 | 0.329 |
| 2280 | 1 | 0.280 | 1.090 | 0.149 |
| 2340 | 2 | 0.189 | 1.039 | 0.145 |

Hai truy vấn metadata cả tập 2.834 s. Đây không phải latency EXPORT_BUILD toàn luồng: chưa gồm PUT/HEAD preview,
progress write, queue wait, publish hoặc finalize. Không suy ra cam kết thời gian cho 28/75 ảnh.

## Giới hạn / release

- Chưa rollout và chưa chạy export đầy đủ mới trên cloud. Bản sửa cần cả FE/API và worker; sửa FE riêng không sửa pixel.
- Không đổi hoặc xóa snapshot/export cũ. Export cũ có thể chứa ảnh sai; phải tạo export mới từ snapshot đã chốt,
  kiểm preview trước xác nhận; không dùng lại bản cũ như bằng chứng ảnh đã đúng.
- Không chạy full suite lại cho lát này; hook Unicode là lỗi đã biết. Không push Git.
- Rollout worker phải kiểm/drain jobs và giữ upload/PII models; restart có thể mất batch interactive tạm chưa checkpoint.
  Không tự reset phiên/decisions người dùng để thuận tiện deploy.
