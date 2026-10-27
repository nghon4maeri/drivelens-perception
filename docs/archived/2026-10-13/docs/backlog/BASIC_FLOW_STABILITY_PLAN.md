# Ưu tiên luồng cơ bản trước tính năng tham chiếu

2026-10-12. Baseline: nhánh thien `5e05c1c`. Nhánh thực thi local: `codex/basic-flow-stability`.
Một lát hiện tại: DEP7-Basic-Stability. Không merge main, push hoặc deploy trong lát này.

2026-10-13: theo yêu cầu tiết kiệm quota, bàn giao phần chưa hoàn tất cho Antigravity bằng [plan tiếp tục](ANTIGRAVITY_BASIC_FLOW_HANDOFF.md). Task 3 vẫn là code nháp chưa nghiệm thu; Task 4–5 chưa hoàn tất.

2026-10-13 sau commit AGY `ca5bc13`: Codex review và sửa recovery/temp budget, bổ sung browser RED/GREEN và đo A/B. Bằng chứng hiện hành: [QA luồng cơ bản](../../../../setup/BASIC_FLOW_STABILITY_QA.md). Báo cáo AGY là mốc lịch sử, không thay gate release.

## Mục tiêu và thứ tự

1. **Đang làm:** upload/direct S3, kiểm video, tách ảnh, xem ảnh/clip, backfill, xóa và phục hồi hoạt động ổn định.
2. **Sau gate 1:** giảm số lượt DB/khóa trên đường media READY và publish theo bằng chứng đo; không đổi thuật toán AI.
3. **Sau gate 2:** Kho tham chiếu: dataset version, model checkpoint, TRAIN/FINETUNE declaration, chọn reference cố định trong phiên.
4. **Sau gate 3:** thêm cờ chất lượng/lineage và các recipe tham chiếu đủ input; Bronze/Silver/Gold là cách tổ chức trạng thái, không dựng stack mới.

Không gộp bước 2–4 vào phiên này. Các giai đoạn sau phải có lát và test riêng; ghi rõ chưa thực hiện, không tick toàn pipeline.

## Global Constraints

- Giữ raw/core/unit IDs, lưới 4 fps, exact RGB và feature/snapshot hiện có; JPEG chỉ phục vụ hiển thị.
- Giữ owner/RLS, source DELETE_PENDING, fence/lease, quota, storage holds và receipts; không publish partial hoặc nhả byte sớm.
- Dữ liệu/cache/output chỉ trong data/ hoặc output/ trên ổ repo D; không sửa hooks, secret hoặc production.
- Docs cấp 2 trước code. Người dùng đã duyệt hai thay đổi thiết kế sau giải thích cơ chế (#121); CONTRACTS §21.20–21 và architecture đã cập nhật trước code.
- Một integrator giữ CONTRACTS/architecture/PLAN/decision log; subagent không commit/push hoặc sửa file ngoài ownership.

## Task 1 — Shutdown worker có thời hạn

Allowed files: `src/rav/worker/__main__.py`, `tests/test_pilot_worker_entry.py`, `tests/test_pilot_worker_spawn.py`.
Giữ stop event hợp tác. Chờ có timeout, terminate có timeout; process vẫn sống thì kill và join có timeout.
Không gọi join vô hạn; không xóa process.json hoặc coi dừng thành công khi còn child sống. Child bỏ qua SIGTERM trên Linux
nên terminate không đủ. Không trả slot/hold/usage FINAL qua đường shutdown này; supervisor vẫn chứng minh dừng qua PID/start token.
Test: child hợp tác, child bỏ qua terminate nhưng kill được, child không dừng phải báo lỗi; crash loop/restart không đổi.
Thêm Linux-specific test thực với process bỏ qua SIGTERM; Windows skip có lý do, không coi Windows test là bằng chứng Linux.
RED trước fix, GREEN sau; focused tests + smoke spawn thật.

## Task 2 — Xóa S3 không chặn heartbeat (đã duyệt thiết kế)

Allowed files: `src/rav/store/pilot_delete.py`, `tests/test_pilot_delete.py`, `tests/test_pilot_frames.py`.
Đề xuất: transaction ngắn kiểm fence/lease, source version/DELETE_PENDING/pins và inventory trước lô; commit trước DELETE+HEAD S3.
Network I/O hữu hạn ngoài transaction giữ profile/job/attempt; kiểm stop giữa các lần I/O.
Sau lô mở transaction mới kiểm lại fence/lease/cancel/source và chỉ ghi confirmed khi còn hợp lệ.
Lease mất hoặc hủy không ghi confirmed/publish/tombstone/quota; lần retry xóa idempotent inventory cũ.
Source vẫn DELETE_PENDING suốt quá trình, raw/key bất biến, retry không bắt đầu trước khi process attempt cũ đã dừng/đối soát.
Không nới ownership, RESTRICT hoặc nhận key từ client. Giữ batch/concurrency hiện có nếu test cho thấy bounded.
Test PostgreSQL thật: chặn S3 có kiểm soát, heartbeat vẫn hoàn tất trước khi thả S3; S3 lỗi giữa lô;
hủy/lease lost giữa I/O và ghi receipt; retry, owner isolation, accounting đúng một lần, không publish partial.
RED dùng regression local đã tái hiện, đưa vào suite repo; GREEN sau fix.

## Task 3 — SYSTEM import không tạo ảnh ngoài kế toán (đã duyệt thiết kế)

Allowed files: `scripts/pilot_prepare_system_frames.py`, `tests/test_pilot_frames_prepare.py`.
Nếu cần sửa helper storage/schema, báo integrator trước; không tự mở thêm role/bảng.
Đề xuất: kiểm policy/nguồn/spec trước PUT; giữ dung lượng GLOBAL và ghi ledger object bền trước I/O.
Ưu tiên tái sử dụng `rav_pilot_import_receipt` (mapping/counts/hashes/state) cho import có idempotency, không tạo queue/service mới.
Chỉ chuyển reserved→persisted và COMMITTED cùng transaction publish đủ bộ; không tính USER cho SYSTEM.
Lỗi/kill phải giữ ledger và reservation đủ để resume hoặc cleanup theo đúng key trước nhả byte.
Một import cho cùng source/raw/spec/encoding tại một thời điểm; recovery không đụng object của bộ READY đã nhận,
không tự suy process khác đã dừng hoặc quét prefix. Plan dry-run và hash/operator-confirm giữ nguyên.
Test PostgreSQL thật + S3 mock: policy thiếu/disabled, hết quota trước PUT (zero ảnh), lỗi sau một PUT, DB publish lỗi,
retry/cùng inventory không tính trùng, cleanup sau crash có bằng chứng dừng, hai importer cùng nguồn và READY giữ nguyên.
Test leak 16 ảnh của local report được chuyển thành regression repo.

## Task 4 — Kiểm các luồng và đo trước/sau

Integrator chạy lần cuối sau review các task:

- Pytest toàn repo với PostgreSQL thật; hook path lỗi sẵn chạy/báo riêng, mọi skip có lý do.
- Ruff src/tests; Vitest, tsc và build local/pilot nếu FE vẫn tương thích DTO hiện có.
- E2E HTTP + CPU/MEDIA spawn + S3 moto + PostgreSQL thật: upload → đủ frame → frame/lưới/clip → backfill → A/B → xóa.
- Fault injection: S3 chậm, cancel/crash/lease lost, disk/storage hết; heartbeat và usage/cleanup/slots có bằng chứng.
- Đo cùng video và concurrency, cold/warm riêng, tối thiểu 5 lượt video tổng hợp + video thật; thêm video 5 phút cho preprocessing.
- Báo thời gian upload READY, decode/encode/PUT/publish, ảnh đầu/lưới/clip, queue, byte/object count và RAM khi có đo.
  Không trộn build time vào runtime latency; không suy wall−CPU thành network.
- Không suy số local thành tốc độ cloud; không đặt gate latency giả đã được người dùng duyệt.
  So baseline local đã đo và nêu từng chặng chưa đạt mong muốn; cải thiện tốc độ tổng là bước 2 riêng.
- Browser click-through là gate riêng; HTTP/FE unit tests không chứng minh giao diện thật đã mượt.
- Nếu browser smoke phát hiện lỗi cơ bản, sửa tối thiểu và thêm regression trước bàn giao; hiện nghi vấn thumbnail không refresh khi toolbar chuẩn bị ảnh.
- Linux shutdown: cần Linux runtime local thực; nếu máy không có runtime, giữ gate release BLOCKED, không claim đã kiểm.
- Sau local gates, người dùng cho phép truy cập Contabo để test trước: preflight CPU/RAM/disk/image/job chỉ đọc,
  candidate/tests/DB/S3 giả cô lập, không thay worker live, không nhận queue/credentials production hoặc chạy Kaggle.
  Không migrate/backfill DB cloud hay đổi Render khi chưa được yêu cầu. Báo bằng chứng host riêng với số local.

## Task 5 — Bàn giao

Review từng task và toàn diff. Test xanh → commit tiếng Việt không dấu → tick đúng phần đã kiểm trong PLAN → dừng.
Không tick cloud hoặc tính năng tham chiếu. Lưu số đo/lỗi/giới hạn trong runbook/report; người dùng duyệt release riêng.
Main vẫn ở baseline trước; không tự merge/push. Không giữ PostgreSQL test hoặc process API/worker chạy sau phiên nếu không cần UI smoke.

## Tiến độ

- [x] Đọc main/thien, review, chạy local baseline: 470 regression đạt/3 skip, 30 FE, 3 E2E + 1 video thật; 2 fault regression fail.
- [x] Task 1 shutdown — unit/Windows spawn đạt; Docker Linux 19 test đạt, gồm SIGTERM-ignore và interpreter-exit boundary; review sạch.
- [x] Duyệt thiết kế Task 2/3
- [x] Task 2 delete heartbeat — PostgreSQL/moto 21 test đạt, heartbeat/cancel/lease/stop regression xanh; review scoped sạch.
- [x] Task 3 SYSTEM import — PostgreSQL/moto regression đạt; cleanup-only khi nguồn/policy đổi, temp trước download/ghi ảnh, stale receipt/operator-confirm.
- [x] Task 4 regression/E2E/benchmark/UI/Linux gates — 496 local regression/5 skip có lý do, browser RED/GREEN thật, A/B 5 lượt, video 21 s/5 phút, 19 Linux + 45 integration cô lập Contabo. Số đo và giới hạn/chặng chưa đo ở QA; không claim cloud speed hoặc toàn pipeline đã mượt.
- [x] Task 5 review/commit/bàn giao — review diff + final importer/ruff/FE/tsc/build; commit local của lượt sửa review, không merge/push/deploy. Ghi hash thực sau commit trong báo cáo output.
