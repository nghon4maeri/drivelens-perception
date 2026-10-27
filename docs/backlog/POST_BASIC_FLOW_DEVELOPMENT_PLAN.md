# Phần việc sau ổn định luồng cơ bản

Khởi tạo 2026-10-13, cập nhật 2026-10-15. **2026-10-17:** upload lớn (qua ingress VPS #139) và PII export đã live;
trạng thái live hiện hành chỉ đọc ở [status](../setup/DEPLOYMENT_STATUS.md), các dòng "candidate" bên dưới là lịch sử.
- **N1 đã hoàn thành và live**: Rollout N1 (#130), Publish RPC (#131), Claim Lanes 4CPU/6GiB (#132), và Interactive Proposal không quota CPU (#133) đã release trên Render/Contabo với policy `interactive-20261014` và migration 014. Luồng đề xuất đã chuyển từ draft/polling sang Interactive HTTP trực tiếp có tự lưu batch bằng checkpoint RPC/CAS.
- **#134 (Large-Upload)**: Candidate local đã đạt (566 backend pass, video 536 MiB pass), đang mở gate browser interrupt/resume và cloud release.
- **#135 (Progress & ETA - N2)**: Đã hoàn thành candidate local (572 backend pass, UI ETA/fps tức thời).
- **#136 (PII Masking & Snapshot Export - CONTRACTS §21.8)**: Đã hoàn thành candidate local (YuNet/ONNX redact, confirmation gate, zip bundle, 579 backend pass).
- **#137 (Dataset-Model-Binding - N3 - CONTRACTS §21.17)**: Đã hoàn thành candidate local (catalog API, reference binding CAS, unpin/re-pin, 583 backend pass).
- **Các lát N4–N6 dưới đây**: Là lộ trình tính năng tiếp theo; [PLAN](../../PLAN.md) và [status](../setup/DEPLOYMENT_STATUS.md) giữ tiến độ hiện hành.

## Thứ tự ưu tiên

Rà kiến trúc tổng: [A0–A5 và các giả định deployment](../archived/2026-10-13/docs/backlog/ARCHITECTURE_DEPLOYMENT_REVIEW.md). A0/A1/A3 là nền tảng của N1;
A2 điều phối dùng chung, A4 nối N2/release, A5 nối N3–N6/DEP6. Proposal plan là một luồng trong nền tảng này;
không triển khai tính năng tham chiếu để bù việc upload/media/DB/queue còn chậm. Topology/số policy đề xuất cần chốt sau đo.

### N1 — Đo và tối ưu thời gian chờ trên cloud (Đã hoàn thành ở #130–#133)

- Đã thay thế luồng polling draft cũ bằng kiến trúc **Interactive HTTP trực tiếp (Render → Contabo)** ở #133: nạp model TEXT/Context resident trong RAM worker Contabo, Render gọi HTTP RPC, tự động lưu batch hiện tại bằng checkpoint DB RPC qua CAS khi bấm Đề xuất tiếp (CONTRACTS §21.26).
- CPU lanes tách biệt TEXT/YOLO/BULK/MEDIA đã bật ở #132 (4 CPU/6 GiB). [QA và latency đo thực tế](../setup/INTERACTIVE_CPU_QA.md).

### N2 — Tiến độ công việc và ước lượng thời gian thực tế (ETA)

- **Cơ chế kế toán CPU theo policy mới**: Theo quyết định #133 và CONTRACTS §21.26, policy mới (`accounting_enabled=false`) tắt reserve/consume/receipt CPU cho mọi kind (không áp hạn mức CPU cho người dùng). CPU telemetry (`process_cpu_ms`) vẫn được ghi nhận trong timing/log, và các job mang policy cũ vẫn tiếp tục tất toán theo snapshot policy cũ (tương thích ngược). Quota/hold bắt buộc chỉ còn áp dụng cho GPU (Kaggle session time) và Storage.
- **Tiến độ công việc chi tiết**: Bổ sung telemetry cho các job worker dài (đặc biệt video lớn ở #134): pha xử lý hiện tại (chuẩn bị raw -> giải mã -> trích xuất frame -> sinh thumbnail), số frame đã xử lý / tổng số frame, tốc độ xử lý fps tức thời và thời gian đã trôi qua (elapsed wall time).
- **Ước lượng thời gian còn lại (ETA)**: Trong pha trích xuất frame, ETA được tính theo công thức `(tổng frame - frame đã xử lý) / fps đo được`, có dải ước lượng (khoảng thời gian) thay vì đồng hồ đếm ngược tĩnh; khi chưa đủ dữ liệu hoặc job đang khởi động hiển thị "Đang ước tính...".
- **Hiển thị tài nguyên**: Quota GPU/Storage hiển thị rõ ràng, tách biệt hoàn toàn với thời gian chờ xử lý video.
- Test: Mock & real worker progress telemetry; video ngắn vs dài có tiến độ tăng dần đều đặn; job lỗi/hủy hiển thị lỗi rõ ràng; không có transaction trừ điểm CPU.

### N3 — Kho dataset/model tham chiếu và binding phiên pilot (Đã hoàn thành ở #137)

- Dataset version/manifest bất biến: source IDs, content hashes, số ảnh/nhãn, class map, provenance/lineage, feature readiness. Không dựng storage/catalog trùng local/pilot nếu helper hiện có dùng lại được.
- Model checkpoint cố định theo SHA/revision, task/class map/inference verification; khai báo dataset TRAIN/FINETUNE là quan hệ có provenance, không suy model được train trên dataset chỉ vì cùng lớp nhãn.
- Phiên chọn dataset version + model và đóng băng binding. Pool ứng viên là video/nguồn mới; dataset tham chiếu dùng để so sánh, không mặc nhiên đưa ảnh train vào tập cần đề xuất.
- Bronze = raw; Silver = nguồn/ảnh/feature đã kiểm; Gold = tập đã chọn/snapshot/export có mục đích. “Đã keep” chưa đồng nghĩa training-ready nếu chưa có nhãn/quality gate.
- Test: owner/SYSTEM readonly, immutable version, thiếu ảnh/feature/model/provenance bị chặn có lý do, đổi binding qua CAS và không làm thay kết quả snapshot/proposal cũ, session clone/fork đúng binding.

### N4 — Nối lựa chọn theo tham chiếu/model vào pilot

- Nạp reference dataset features vào Context và mở `reference_diversity` khi đủ input. Ghi mẫu tham chiếu gần nhất và novelty so train + session-kept để giải thích.
- Mở `model_guided`/`text_model_guided` khi model–dataset provenance đầy đủ; dùng candidate stage có giới hạn, model original/flip instability + embedding novelty và CLIP khi recipe yêu cầu.
- Pin config/seed/reference versions/model SHA/features/decision state lúc enqueue; có budget/deadline/cache cho model trên candidate set. Không tự chạy inference toàn video không giới hạn.
- Test: local/pilot parity với cùng input; missing provenance/feature 422/409 rõ; deterministic picks/IDs/scores; model/reference thay giữa job không publish; quota/cancel/recovery và owner isolation.
- Tên gọi phù hợp: lựa chọn mẫu hỗ trợ active learning. Prediction bất ổn là tín hiệu để người dùng xem, chưa phải bằng chứng model sai hoặc accuracy tăng.

### N5 — Proposal → model tham chiếu → evidence/report

- Dùng checkpoint đã gắn phiên làm lựa chọn mặc định cho “Chạy model tham chiếu” trên proposal; giữ nút explicit hiện có. Tự chạy sau proposal chỉ khi người dùng bật/chốt thiết kế, có job/quota riêng.
- Lưu proposal ID, model SHA/runtime, ordered anchors, prediction/box/class/confidence, tham số inference; cache cùng input, reload không chạy lại, đổi checkpoint không trộn kết quả.
- Report: số ảnh/đối tượng/phân bố lớp, ảnh không có detection, tín hiệu cần xem và lý do đề xuất. Phân tích curation (coverage/duplicate/keep rate) tách khỏi độ đúng prediction.
- Accuracy/mAP/recall chỉ khi có ground truth, class map và split đánh giá hợp lệ; không suy từ confidence hoặc keep/drop. So hai model phải cùng input/nhãn/tham số.
- Test: chạy đúng model binding, đúng proposal/anchor/order, A/B, cache hit không tính hai lần, model thiếu/chưa verified lỗi rõ, report không giả accuracy, proposal/decisions không bị sửa bởi inference.

### N6 — Frame đại diện/quality và vòng learning ngoài hệ thống

- Hiện tách đều 4 fps; chưa có scene/keyframe/blur selection. Ưu tiên thêm cờ chất lượng và chọn subset đại diện trên lưới hiện có; giữ exact anchor, ID, feature/snapshot. Phân biệt I-frame của codec với frame đại diện theo nội dung.
- Sampling khác cần spec version/binding/cache/IDs mới và thiết kế riêng; không sửa grid ngầm. Các cờ thiếu sáng/blur/motion ban đầu hỗ trợ duyệt, không tự loại dữ liệu có giá trị.
- Snapshot/export → gán nhãn ngoài hệ thống → import dataset version có nhãn → khai báo model retrain mới → đánh giá trên split độc lập theo video/trip → cập nhật binding phiên mới. Chưa thêm training service/auto retrain trong các lát N1–N5.
- Test: flags/selection tái lập, dữ liệu đêm/motion không bị mất âm thầm, không leakage train/validation cùng video, lineage đầy đủ, checkpoint/version cũ xem lại được.

## Release hiện tại để người dùng thử

Lát `DEP7-Frames-Release`: release commit đã kiểm `f0d278e` + tài liệu plan này, không implement N1–N6 trong lần deploy.

1. Lưu image/digest/env policy hiện tại, đọc jobs/leases/slots, target DB/bucket/prefix; backup DB trước migration additive nếu cần.
2. Kiểm migration 010 và policy frame bounds/deadline/reservation. Nếu thiếu, áp theo CONTRACTS §21.20/runbook; không sửa snapshot job đang chạy hoặc tự cấp quota bonus.
3. Build worker và combined cùng source, smoke image thật, publish registry và pin digest. Drain job/cửa sổ admission trước chuyển;
   release ảnh 2026-10-13 chuyển API trước worker vì API cũ enqueue upload thiếu payload.frames; xem FRAMES_RELEASE/FRAMES_CLOUD_QA.
4. Kiểm cloud readiness/auth/runtime config, upload video test mới → READY → thumbnails/frame/clip → source cũ chuẩn bị explicit → xóa đúng nguồn smoke. Kiểm receipts/quota/slot/trace.
5. Không auto backfill video có sẵn của người dùng hoặc apply SYSTEM inventory chưa duyệt. Màn Kho dữ liệu cho người dùng bấm chuẩn bị ảnh nguồn cũ.
6. Ghi release/rollback/số đo và các gate chưa chạy. Giữ DEP7/DEP8 tổng chưa tick, không claim đã có active learning/reference đầy đủ hoặc pipeline đã mượt.

Người dùng yêu cầu deploy 2026-10-13 là authorization cho release hiện tại và các prerequisite cần thiết; không cần hỏi lại từng bước có thể kiểm/rollback. Không push Git/merge main nếu chưa được yêu cầu; publish image/deploy là thao tác riêng.
