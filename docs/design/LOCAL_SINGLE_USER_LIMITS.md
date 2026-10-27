# Giới hạn local và ranh giới deployment

Cập nhật #128. [Bảng local đầy đủ tại checkpoint cũ](../archived/2026-10-13/docs/design/LOCAL_SINGLE_USER_LIMITS.md)
giữ nguyên để tra comment local-only. Không dùng giả định local cho pilot đang deploy.

## Phân biệt hai runtime

| Local v1/compatibility | Deployment pilot |
|---|---|
| SQLite, folder paths, API/prepare threadpool, local RAM jobs | PostgreSQL rav_pilot_* + RLS/owner, durable queue/worker/lease |
| Static/local raw, dataset import folder, single App locks | Private S3/signed media, receipts/holds, worker cache có cap |
| Không multi-user security/cross-host durability | Supabase auth, CAS, source pins, fence/quarantine/recovery |
| Export local không PII-confirm gate | Export pilot chặn tới khi PII handler/quality/confirm đúng contract |

Local test stack `scripts/pilot_local_stack.py` dùng credentials/test services giả chỉ máy dev;
không deploy nguyên env/token/seed này. Mock GPU/S3 không thay provider/PII/RLS cloud acceptance.
Windows unicode: PIL đọc ảnh, cv2.imencode + Path.write_bytes ghi; process.json atomic replace retry local
cho sharing violation khi polling/tests, không là thay lease/fence production. Hook Unicode failure giữ theo AGENTS.
Cache/temp tại data/output, không tự đổi pagefile. Một API/output local và không shared mutable model
giữa process; shared core modifications vẫn giữ local regression khi cần.

Thêm giả định local mới phải thêm dòng ở đây và comment local-only trong code; history còn trong archive.
Capacity/policy live không lấy từ RAM thấp máy dev hoặc quota test fixtures. Xem [architecture](../architecture_diagram.md)
và [status](../setup/DEPLOYMENT_STATUS.md) trước host/release.

#131 latency simulator: delay tại mỗi db.execute, nhóm db.pipeline và hai boundary transaction chỉ là
mô hình chờ cố định cho test local, không packet/RTT thật; không mô phỏng TLS, savepoint hoặc lock contention.
Tách model cold/warm/cache-hit và machine/thread/checkpoint; không ngoại suy CPU laptop thành VPS SLA.

#134 E2E lanes Windows: supervisor chạy trong process spawn riêng với stop Event chung để shutdown/join các process lane
trước khi fixture xóa database, đồng thời giữ phép đo RSS supervisor riêng khỏi API/Moto/pytest.
SIGTERM của Popen trên Windows là terminate cưỡng bức, có thể để lại process con;
Linux E2E vẫn chạy supervisor riêng và kiểm SIGTERM. Worker con và luồng HTTP/DB/S3 của bài test không đổi.
