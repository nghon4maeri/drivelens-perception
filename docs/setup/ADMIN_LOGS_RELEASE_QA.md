# Nhật ký admin: tải cao, giá trị thực và release — 2026-10-17

Theo người dùng: test tải cao, chỉ deploy nếu ổn; bảo đảm log hoạt động và giúp chẩn đoán hệ thống.

## Gate tải cô lập trên Contabo

API candidate c45e54ca, DB PostgreSQL riêng 140.000 event tổng hợp (~7 ngày), network Docker internal, không credentials/DB/S3 production. API cap 0,75 CPU/512 MiB, PG 0,5 CPU/512 MiB; tải CPU tổng hợp cap 3 CPU và allocation 384 MiB, tối đa 60 giây. Monitor dừng tải nếu production có job QUEUED/RUNNING/CANCEL_REQUESTED, health thay đổi/restart hoặc RAM khả dụng <1,5 GiB. Không dùng bài này làm chứng nhận 8 client inference AI hay workload video lớn.

| Giai đoạn | Thời gian | Client | Request | Lỗi | p95 HTTP |
|---|---:|---:|---:|---:|---:|
| Baseline | 15 s | 8 | 423 | 0 | 366.38 ms |
| CPU cao | 60 s | 8 | 2479 | 0 | 253.85 ms |
| Hồi phục | 10 s | 8 | 239 | 0 | 450.62 ms |

CPU peak 96,28%; RAM khả dụng thấp nhất 5389 MiB. Worker production giữ healthy, không OOM/restart. Response tối đa 16.409 byte, <=50 items. API sau benchmark dùng 64,34 MiB, PG 103,3 MiB (đây là snapshot, không phải peak). Kết quả loaded nhanh hơn baseline không chứng minh tải làm tăng tốc: cache/khởi tạo và độ dao động khác nhau.

Gate trước chạy: 0 HTTP lỗi, p95 <2 s, CPU peak >=65%, giữ health/RAM và đọc hồi phục được. Các gate đạt. Container/network/anonymous PG volume tải đã dọn; không dựng stack Grafana/Loki/Prometheus.

## Giá trị và fault tolerance

Trong isolated API thật: tạo lỗi validation qua HTTP → ghi api.error có request_id → mở trace đúng lỗi, non-admin 403, raw secrets không được trả. Cố ý REVOKE INSERT telemetry của role API ở DB test: lỗi ghi log không làm fail GET log/readiness; GRANT lại thấy heartbeat báo 1 flush failure/2 dropped/pending0. Đây là log best-effort, không thay business receipts; giữ retention/cap/coverage partial.

Phát hiện các span export.frame_load/export.pii/export.preview_put bị allowlist loại; test tái hiện đỏ trước sửa. Đổi sang worker.export.*, thêm pii_cache.cache_hit, UI Dùng cache/Tính mới. Test actual export → collector → PostgreSQL, lần sau cache bỏ inference: xanh; 29 backend PG + Ruff đạt. Frontend 51 tests, tsc và Docker pilot build đạt. API/backend log-read so với candidate tải không đổi; chỉ worker timing và UI cache-label thay, đã test riêng/live.

## Live

- Source `c7a9b3df93599f248d0ae2f934ab7b2e0632da7a`; Render `dep-db96t8lg1s2s73c796jg`.
- Combined `docker.io/tonmeu/rav26@sha256:b8ee24a35a476fabe81635521cbeea6f87c7f30b48151794d97c2c867f933208`.
- Worker `sha256:5a9e2cc33fd23cee148065e9c3f39f5000693e2a15da5108354f30a001fa6910`, healthy/0 restart/không OOM sau smoke. Backup image/env trước rollout trên VPS output/pre-admin-logs-20261017-c7a9b3df; metadata service/env trong output local gitignore.
- Không migration mới, không đổi policy/credentials/upload/PII confirmation/training gates. Schema giữ 021–022. Idle/drain trước restart, candidate health/routes/pinned PII models đạt trước rollout.
- HTTP qua Render/Supabase: 12 request / 4 client, 0 lỗi, p50 1578.0 ms, p95 (max của 12 mẫu) 1782.0 ms. Đây là smoke nhỏ có RTT Internet, không đồng nhất với benchmark cô lập. Helper auth đầu bị race magic-link khi chạy song song; đã chạy lại auth tuần tự, không tính lượt đó là benchmark thành công.
- Lỗi API thật trace theo request ID đạt, tài khoản QA thường đọc log bị403; endpoint log đọc bounded/allowlist.
- Phiên QA `0267a1a3-7fe2-4361-8f34-869be9555850`, không sửa phiên/decisions thật. Export mới jobs `5e4aaa65-0d84-4bde-b5dc-093c2183b539` / `535a1ea4-3bab-4160-aa94-fd3c39e0493d` đạt NEEDS_CONFIRMATION; cố ý giữ gate, không phát hành ZIP của các export QA này.
- Cold 3 ảnh: trace có 3 frame_load, 3 pii, 3 preview_put, 3 cache-miss. Lần sau 3 cache-hit và không có pii span mới. Hai export đều source_identity_verified. PII trung bình 297,07 ms/ảnh; frame_load 397,93 ms; preview_put 3623,94 ms. preview_put gồm object receipt/DB/S3, không suy wall−CPU thành thời gian mạng; không cộng span lồng nhau. Điểm nghẽn lưu preview có bằng chứng để ưu tiên tối ưu tiếp.
- Browser IAB tải login live đạt. Chrome extension đã ngắt nên không test admin GUI live có đăng nhập; GUI log/filter/page/trace đã qua browser fixture trước đó, thêm nhãn cache qua tsc/build/tests. Không giả nhận đã kiểm UI private cloud.

## Giới hạn và rollback

Bài ngắn 60 s, CPU tổng hợp + đọc log; chưa soak-test, peak RAM nhiều model, 5 GiB/60 phút hay đo PII recall. Tracing có giới hạn 20k event/ngày và 7 ngày, có thể mất log; UI heartbeat/drop/failure/partial giúp phân biệt thiếu dữ liệu với không có lỗi. Không claim semantic/model uplift.

Rollback image worker từ release.env backup, restore Render image từ service-before, idle/drain trước restart; không đổi schema/policy/data. Không thực hành rollback trên live lượt này. Logs/results tại output/admin-logs-release (gitignore), chỉ commit QA đã loại secrets.
