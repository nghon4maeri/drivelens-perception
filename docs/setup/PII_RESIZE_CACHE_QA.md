# PII resize, cache bền và dataset ZIP

2026-10-16; candidate local, chưa deploy/push. Người dùng duyệt resize để inference và scale box để che ảnh gốc.
CONTRACTS §21.8; migration additive 022_pii_cache_index.sql.

## Thay đổi

- Pipeline `pii-onnx-resize-v2`: ảnh RGB giữ tỷ lệ, cạnh dài tối đa 640, ảnh nhỏ không phóng lên; YuNet và YOLOv9-t
  chạy trên ảnh này. Biển số chỉ một ô pad 640; box scale riêng theo chiều rộng/cao thực rồi clamp về ảnh gốc.
  Redact và PNG vẫn giữ kích thước ảnh nguồn. Evidence lưu config/model SHA và inference_size.
- Cache PRIVATE là artifact EXPORT_PREVIEW/PII_REDACTED trong DB + PNG trên S3; không dùng cache RAM làm nguồn sự thật.
  Hash gồm owner/source/unit/raw SHA/FRAME SHA hoặc raw anchor/spec/model manifest và config đầy đủ.
  Query chỉ các hash cần cho snapshot, index 022 theo owner/input. Cache hit kiểm SHA/size/ảnh; thiếu/hỏng → tính lại.
  Không lấy ảnh từ export cũ trước cơ chế này làm cache.
- Ảnh/report/bundle có INTENT/WRITTEN trước khi nhận; artifacts, ACCEPT, persisted bytes và review receipt cùng fenced publish.
  Cache chỉ READY khi build thành công; lỗi/hủy giữ receipt cho cleanup attempt có stopped proof. Key có attempt ID, retry không ghi đè.
  Cache hit dùng lại object immutable, không PUT bản ảnh trùng; mỗi export vẫn có payload/report/confirmation riêng.
- ZIP chỉ tạo sau owner xác nhận đúng payload/receipt, tải lại ảnh/report kiểm SHA, ghi ZIP xuống đĩa và hash theo chunk
  thay vì đọc cả ZIP vào RAM. Bundle S3 và metadata DB giữ sau reload, tải lại qua lịch sử.
- UI nói rõ đã lưu preview nhưng CHƯA có ZIP khi NEEDS_CONFIRMATION; READY nói dataset ZIP đã lưu S3.
  Không tự đăng ký dataset huấn luyện trong catalog, không tự xác nhận hộ người dùng.
- Theo ảnh lỗi người dùng gửi: review cũ thiếu marker exact-source-v2 vẫn xem để đối chiếu nhưng không được xác nhận/tải ZIP mới.
  UI chặn nút và yêu cầu xuất lại từ snapshot; backend cũng chặn, không chỉ dựa vào UI. Không xóa/sửa kết quả cũ.

## Kiểm chứng

- Unit resize ảnh ngang/dọc/kích thước lẻ/ảnh nhỏ: một lượt plate, box đúng tỷ lệ và output giữ kích thước gốc.
- PostgreSQL riêng + S3 moto: lifecycle model ghim SHA thật; cache qua handler/service instance mới, owner khác không hit,
  model/config/raw/frame đổi hash, cache hỏng tính lại; upload dở không publish cache và cleanup được;
  accounting tắt vẫn lưu dataset, không tạo tài khoản storage; preview/bundle receipts ACCEPTED và artifact READY.
- Regression cuối PostgreSQL riêng/S3 moto: **56 passed**, 102.90 s (PII, export pipeline, objects, storage unblocked,
  source deletion). Cache và ZIP thật qua lifecycle; có test chặn xác nhận preview legacy chưa verify nguồn.
- Browser fixture dùng component thật/API giả trên localhost: reload NEEDS_CONFIRMATION giữ preview và báo rõ chưa có ZIP;
  chế độ legacy hiện cảnh báo và nút xác nhận disabled; xác nhận fixture hợp lệ rồi reload READY giữ preview và link dataset.
  Browser không thay kiểm chứng S3/DB; phần đó dùng PostgreSQL+moto ở trên. Không dùng production DB chạy pytest.
- TypeScript + 50 frontend tests + pilot build đạt; Ruff src/tests đạt. Build có cảnh báo chunk >500KB hiện có.

## Số đo VPS (3 frame thật của phiên báo lỗi, đọc-only)

Worker hiện tại vẫn chạy baseline; code candidate chỉ nạp vào process Python tạm, không restart/upload/DB write.
Mỗi ảnh 1280×720 → inference 640×360; 1 thread theo BULK budget.

| Frame index | Baseline inference s | Resize inference s | Box baseline / resize |
|---|---|---|---|
| 1845 | 1.414 | 0.381 | 0 / 0 |
| 2280 | 1.146 | 0.303 | 0 / 0 |
| 2340 | 1.153 | 0.215 | 0 / 1 |

Chỉ đo detector, không bao gồm tải ảnh, PUT/HEAD, receipt/DB, queue, publish/ZIP. Không suy ra tốc độ toàn luồng hay chất lượng.
Số box khác không chứng minh tốt/xấu: chưa có ground truth; resize có thể sót PII nhỏ/xa. UI giữ cảnh báo và confirmation gate.

## Release

Operator áp migration 022 trước rollout đồng bộ FE/API + worker. Không tự migrate lúc boot; thiếu migration trả schema_not_ready.
Giữ model mount/SHA, auth/owner/lease/policy và dữ liệu cũ. Worker restart cần kiểm/drain jobs và batch interactive tạm.
Sau deploy cần xuất mới từ snapshot đúng, kiểm preview, xác nhận, đợi READY rồi tải dataset; gói cũ sai frame không tự được sửa.

## Gộp với admin model (2026-10-17)

Release áp lần lượt `021_pilot_model_verify.sql` và `022_pii_cache_index.sql`, cập nhật FE/API và worker đồng bộ. Model verify cần policy và CPU lane theo runbook §15; không tự sửa policy đang live trong bước gộp local.
