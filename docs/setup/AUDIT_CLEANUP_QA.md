# Kiểm chứng audit test/docs — 2026-10-14

Audit đầu vào là [`audit_results.md` (archived)](../archived/2026-10-13/docs/audit_results.md) và bản người dùng gửi. Các số test/pass/skip trong audit là checkpoint
của tác giả, không phải kết quả chạy lại trong lát này. Repo đang có thay đổi upload #134 chưa commit;
lát này chỉ dọn tài liệu, không thay runtime, contract hoặc test.

## Kết luận đối chiếu

| Nhận định audit | Kết luận và bằng chứng | Xử lý |
|---|---|---|
| 49 file template ngoại lai | Đúng là template khóa học, không có runtime caller; 4 Markdown có nội dung mẫu, không phải file rỗng | Xóa 45 file Technical Book và 4 template; cập nhật archive index và hai link tại JOURNAL/presentation README |
| P5a/P5b trùng hoàn toàn P6 | Sai: P5a có replay sau reopen, atomic validation, seed/config; P5b có export/registry/schema, P6 kiểm HTTP/media/error | Giữ |
| P8e/P8h/P8i nhỏ nên thừa | Không có bằng chứng thừa: progress/upload explicit, cache-only evidence, explain là các hành vi riêng | Giữ |
| Snapshot và export snapshot trùng | Sai: snapshot kiểm hash/idempotency/context/DB immutability; export kiểm ZIP/exact pixels/VFR/source đổi/cleanup | Giữ |
| SQLite migration không còn cần | Sai: [architecture §11](../architecture_diagram.md) giữ local v1 regression/CLI/evaluator; test kiểm backup/rollback/newer-schema/data | Giữ |
| Worker 2 process đã bị thay hoàn toàn | Sai: `worker/__main__.py` vẫn dùng `tuple(cpu_lanes()) or ("CPU",)`; test cũ còn SIGKILL/daemon proof và CPU không chặn MEDIA, khác test lanes | Giữ |
| C1 đã chết, test cloud embedding bỏ được | Sai: CONTRACTS §19–20 còn compatibility; `service/__init__.py` gọi `SupabaseJobs`, pilot PREPARE dùng `remote_s3_embed` và worker C1/C2 | Giữ |
| Benchmark/capacity không có assertion | Sai: publish kiểm SUCCEEDED/RPC call count/receipts/usage; capacity kiểm overlap, READY, slot cleanup hoặc p95 và không CPU receipts | Giữ pytest opt-in và fixture hiện có |
| Pilot Frames trùng hơn 40% | Không có phép đo hoặc mapping assertion chứng minh tỷ lệ; frames kiểm VALIDATE_UPLOAD/F1, prepare kiểm PREPARE_FRAMES và SYSTEM import/F2; có gate dedup/accounting/fault/recovery riêng | Giữ; chưa có cơ sở xóa coverage |
| Hook fail vĩnh viễn nên loại | Chỉ có known failure Windows ghi ở AGENTS, chưa chứng minh vĩnh viễn; hook logging là yêu cầu chấm điểm | Giữ, không sửa hook |
| QA cũ là rác | Sai: checkpoint vẫn chứng minh fault/recovery/model/capacity/release; PLAN/status dùng links, #133 không thay mọi hành vi upload/media/GPU | Giữ bằng chứng và nhãn checkpoint |
| Proposal plan đã xong toàn bộ | Đúng một phần: #129/#131/#132 triển khai từng phần, #133 thay đường interactive; acceptance/A-B/browser cloud không tự hoàn tất | Cập nhật trạng thái đầu plan, giữ gate còn mở |
| Component README là stub vô dụng | Sai: là navigation từ docs index tới shared core guides theo CONTRACTS | Giữ |
| SQL C1 xung đột pilot migration | Không có bằng chứng xung đột: bảng `rav_embedding_jobs` khác `rav_pilot_*`; code C1 vẫn hướng dẫn chạy file này | Thêm nhãn legacy/local v1, chỉ rõ runbook/migrations pilot, giữ path |

## Phạm vi dọn và kiểm tra

- Xóa đúng 49 file tracked dưới `docs/archived/templates/`, không xóa dữ liệu/cache/output hoặc archive của RAV-26.
- Rà references bằng `git grep`/`rg`, kiểm các link Markdown tới file bị xóa và links mới trong docs đã sửa.
- `git diff --check` và kiểm diff staged chỉ chứa lát dọn này trước commit.
- Theo [guide](../IMPLEMENT_GUIDE_V2.md), docs-only không chạy lại full ML suite/ruff/tsc hoặc deploy.
  Không xác nhận lại số 327 pass/250 skip/1 fail hay 34 frontend của audit; PG/Linux/model capacity chưa chạy trong lát này.
- Commit tiếng Việt không dấu, tick riêng Audit-Cleanup trong PLAN, không gom thay đổi upload #134 và không push.

Kết quả kiểm fresh: xóa đúng 49 file; không còn link Markdown từ file tracked được giữ tới file đã xóa;
105 đích link local trong các tài liệu sửa/thêm đều tồn tại; `git diff --check` đạt.
