# Vận hành chỉ đọc và tracing (#115)

Nguồn thiết kế: [CONTRACTS §21.19](../design/CONTRACTS.md#2119-dep7-ops--vận-hành-chỉ-đọc-và-tracing-tập-trung-115).

## Cấu hình và quyền

API `RAV_TRACE_ENABLED=true`, `RAV_PILOT_OPERATOR_IDS=<UUID tài khoản được phép đọc>`; worker chỉ cần
`RAV_TRACE_ENABLED=true`. Các biến này chỉ nằm trên server, không dùng VITE hoặc JWT/body tự khai quyền.
Allowlist rỗng → không tài khoản nào xem `/api/v2/operations`. JWT/profile active vẫn bắt buộc.
Trước release apply migration `009_pilot_operations.sql` bằng operator sau backup/restore kiểm chứng;
không dùng credential postgres admin trong API. Schema cũ không tự nâng khi boot.

## Tra một upload chậm

1. Đăng nhập account được cấp quyền → **Vận hành** hoặc `/app#operations`.
2. Bấm UUID ở bảng Uploads; bảng timeline đọc timing theo upload và validation job, cùng request liên quan.
3. `browser.s3.transfer` là chặng truyền máy → S3; `browser.complete` là xác nhận; `browser.ready_wait`
   gồm queue/worker/poll. So với `worker.claimed.queue_ms` để biết chờ hàng đợi.
4. Worker có download+hash, probe+core hash, unit grid, container check, canonical PUT/HEAD và publication.
   DB checkout/execute/transaction lồng trong các chặng này; không cộng tất cả durations.
5. Với job YOLO, xem riêng `worker.yolo.model_load`, `worker.yolo.exact_decode`, `worker.yolo.inference`.
   GPU remote chỉ có thời gian worker thực sự quan sát; không coi cả thời gian job là GPU inference.
6. Lỗi API có request UUID để tra trong mục Request; lỗi không có tài nguyên vẫn có thể tra bằng mã đó.

Timestamp trong storage là UTC; màn hình đổi sang múi giờ trình duyệt. `wall_ms` dùng monotonic, `process_cpu_ms`
bao gồm thread của process và không thay receipt quota. MB/s suy từ bytes/duration chỉ có ý nghĩa cho span truyền file.
Browser tự báo timing; không dùng dữ liệu này làm bằng chứng billing/quota hoặc quyền.

## Giới hạn bằng chứng

- Chỉ log từ lúc release tracing mới; không dựng timeline cho job cũ. Trước đó tra stdout/Console theo
  [UPLOAD_LATENCY_LOGS](UPLOAD_LATENCY_LOGS.md).
- Retention 7 ngày, cap 20.000 events/ngày; tối đa 2000 browser events/upload, GET giới hạn 1000 events.
  Bounded queue 2048/batch 100; collector lỗi không làm fail job. Heartbeat ghi dropped/flush failures;
  có thể mất dữ liệu nếu process chết trước flush, nên timeline luôn là bằng chứng từng phần.
- Heartbeat quá 30 giây là stale, chưa có là unknown. Heartbeat không là health probe S3/Kaggle;
  GPU quota là snapshot kèm thời gian ghi, không realtime. API đọc lỗi thì số liệu cũ vẫn được ghi rõ.
- Đọc vận hành không cấp URL/raw video/secret/payload. Chưa có thao tác cấp quota/hủy/retry/khóa user.
- Backup pilot trước migration không thay backup S3/Auth hoặc diễn tập DR toàn hệ thống.

## Rollback

Current rollback phải theo [release matrix](DEPLOYMENT_STATUS.md), [frames compatibility](FRAMES_RELEASE.md)
và [compute prerequisites](COMPUTE_RUNTIME.md), không dùng image pre-ops #112/#113 với job/frame payload mới.
Migration 009 additive không có nghĩa mọi image cũ xử lý được schema/payload tới 010; drain/proof/receipts/policy giữ đúng.
Không drop bảng hoặc hạ migration. Các backup pre-ops lịch sử `output/release-pre-ops.env` và `output/worker-pre-ops.env` trên VPS, mode 0600;
không in các file env vào log. Bật/tắt collector không đổi job/receipt/quota.

## Release 2026-10-12

- Migration 009 đã apply sau backup phạm vi `rav_pilot_*` được người dùng duyệt; dump 240.346 byte,
  SHA256 `995635ee91b80f0caedc5b94e7082386c4b8eb1c347b17612429ea6218a04cc5`.
  Restore data vào PostgreSQL 17 trong container không network đã kiểm 2 profile, 36 job, 11 upload.
- Worker digest `sha256:746af4861ca116797a9041992381fa5d1092e47dc997de6d2c63fc160ef21a4b`, healthy;
  heartbeat CPU/MEDIA và GPU fresh, dropped/flush_failures = 0 khi kiểm trước rollout API.
- FE/API image private `tonmeu/rav26:combined-ops-20261012`, digest
  `sha256:110f5cde633178fcf656d0f29230371509c63fc2b96d980012aa5cd84482ebf2`.
  Quyền đọc chỉ cấp UUID tài khoản `duyla25clc` sau xác nhận riêng của người dùng.
  Render `dep-db5sckcs728c738sosrg` Live, deploy 20,4 s; readiness DB/schema 200 sau rollout.
- Regression PostgreSQL thật: 432 pass, 3 skip; bỏ qua `tests/test_codex_hook.py` có lỗi PowerShell đường dẫn
  được AGENTS.md ghi sẵn. Frontend 22 pass; tsc, ruff và diff-check đạt. Không push Git.
- Rollback FE/API về digest `sha256:0c547501c7758c28e86614963e59c9d997e9054034e6cc6720f279f585139a66`;
  tắt tracing/allowlist khi cần. Không drop telemetry migration hoặc sửa bảng nghiệp vụ.
- Smoke UI thật upload tổng hợp 1556 byte: `48375fc2-19e7-4dbb-9fef-9f57cc54befa`, job
  `c9d666b1-b9c5-4a54-8a3d-271d52a4e4e0` SUCCEEDED, READY, attempt CLOSED/FINAL/cleanup true.
  Timeline hiển thị và DB có 138 events browser/API/worker; ba heartbeat fresh, mất log/lỗi ghi 0.
  Browser tổng 37,992 s: create 1,409 s, S3 1,121 s, complete 3,462 s, READY wait 30,982 s.
  Worker download+hash 0,432 s, probe/hash 2,306 s, handler 6,944 s, publish 16,408 s, attempt 27,029 s;
  publish có nhiều DB round trip ~0,26 s/lệnh. Đây là quan sát cần tối ưu tiếp, không chứng minh tốc độ
  video lớn hoặc quy mọi chậm trễ cho mạng máy người dùng. Receipt CPU 1735 ms/GPU 0.
  Evidence ignored `output/operations-cloud-smoke.json`, `output/operations-release/operations-live.png`.
- Smoke lỗi từ UI: upload UUID không tồn tại → 404 `not_found`, hiện request
  `502cfc43-ecf2-43d2-9261-65f56e3985f1` trong lỗi API và tra được timeline request.
  Quyền 401/403/A-B kiểm bằng regression PostgreSQL thật; request unauth từ PowerShell bị edge
  Cloudflare chặn 403 HTML, nên không coi đó là bằng chứng API 401 cloud. Chưa chạy smoke đổi account cloud.
