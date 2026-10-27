# Review PR Nam và nhật ký admin — 2026-10-17

## PR được đọc

[PR #2 Integrate/nam duy](https://github.com/AI20K-Build-Phase-Cohort-4/P-026/pull/2), head `1abafe9f`. Không merge toàn nhánh, không sửa/đóng PR hay đăng comment thay người dùng.

- **Blocker khi deploy nguyên trạng:** Compose `observability/docker-compose.yml:57` bật anonymous Grafana role Admin, Loki auth_enabled=false và port 3100 publish trên host (:32). Cấu hình sandbox không phù hợp expose production.
- **Không khớp runtime:** `eval_server.py:68` ghi logs/rav26.log của sandbox, instrumentation theo `/api/evaluate`; không thu đủ worker/container Render hiện tại. Proxy Prometheus/Loki chỉ ở Vite dev config (:10), build FE/API production không có hai route đó. Startup eval nạp pipeline ML (:94), không phù hợp API nhẹ.
- **UI còn thiếu:** ObservabilityDashboard.tsx:24 chỉ lấy stream Loki đầu tiên; HTTP lỗi giữ dữ liệu cũ, thiếu báo lỗi/stale; interval 3s (:36) có thể chồng request, không abort/bỏ response cũ khi đổi tab. Không có pagination truy vấn lịch sử.
- Test observability của PR chủ yếu skip khi localhost stack không chạy; test log file không có vẫn pass. Không chứng minh auth/pilot integration. Sandbox còn có metric KMeans giả tại eval_server.py:129; không nhập code này vào báo cáo sản phẩm.

Tái sử dụng cách trình bày bảng log/filter và nhóm theo nguồn của Nam, chuyển data source sang telemetry pilot đã sanitize. Không claim đã tích hợp Loki/Prometheus/OTel/Grafana.

## Phạm vi code

CONTRACTS §21.19: GET /api/v2/operations/logs, operator/profile active, cửa sổ 1..24h, limit 1..100 (UI 50), source/errors_only, cursor received_at/id với cửa sổ đóng băng. Không COUNT/OFFSET/toàn bộ log, dùng index retention sẵn có; không migration/dependency mới. Data trả allowlist, loại raw URL/SQL/filename/message. Collector/inference giữ nguyên.

Vận hành → mở Nhật ký hệ thống mới tải log; bộ lọc 1/6/24h, nguồn/chỉ lỗi, Làm mới/Trang sau thay trang; ID mở trace hiện có. Không thêm polling, không nối vô hạn; response cũ bị bỏ khi đổi filters/unmount/logout. Retention/cap/partial được ghi rõ. Log UI này là timing metadata, không raw application log.

## Kiểm thử

- 27 pass PostgreSQL local cô lập cổng 25448: test_pilot_operations + test_pilot_api_v2. Bao gồm 401/403/disabled, tie timestamp phân trang không lặp/mất, log đến mới không chen cửa sổ, cursor đổi filter bị 422, bounds/source/lỗi và lọc secrets.
- 51 frontend pass, tsc --noEmit, Ruff src/tests, pilot build đạt. Build vẫn cảnh báo bundle >500 kB.
- Browser IAB fixture dùng OperationsLogs thật: lúc đóng 0 request, mở 1, page2 thay nội dung và disable next ở trang cuối, đổi source quay trang đầu, click ID chuyển callback trace, errors_only rỗng có hướng dẫn, đóng không fetch thêm. Không dùng tài khoản production; fixture fetch/auth giả, không claim cloud E2E.
- Logs `output/admin-logs` và `output/admin-logs-frontend.log`, `output/admin-logs-build.log` (gitignore).

## Trạng thái

Đã push bản model/PII trước đó `67755381` lên origin/feat/perception-instability theo yêu cầu. Phần nhật ký admin ở commit tiếp theo, chưa deploy và không bật/dựng observability services ngoài. Chưa chạy tải lớn/full-suite/stack sandbox của Nam. Production vẫn là source49a3ba8b của release admin model + PII.

Cập nhật release tiếp theo: nhật ký admin + worker spans đã deploy sau gate tải, xem [ADMIN_LOGS_RELEASE_QA](ADMIN_LOGS_RELEASE_QA.md). Trạng thái candidate ở trên giữ làm bằng chứng checkpoint c45e54ca.
