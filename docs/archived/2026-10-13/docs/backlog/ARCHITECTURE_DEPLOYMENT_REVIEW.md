# Rà kiến trúc và giả định deployment — 2026-10-13

Đánh giá lại theo yêu cầu bỏ giới hạn skill: [giải pháp kiến trúc cụ thể cho từng vấn đề](../../../../backlog/ARCHITECTURE_SOLUTIONS.md).
Các câu “sửa nhỏ nhất/không chọn” bên dưới là hướng ưu tiên ban đầu của audit, không là giới hạn kiến trúc bắt buộc.

## Kết luận và phạm vi

RAV-26 chuẩn bị dữ liệu video, chọn mẫu theo recipe, hỗ trợ duyệt/inference/analysis và hướng tới dataset/model tham chiếu + export có PII.
Rà ở tải pilot **2–3 tài khoản**, VPS 3 vCPU/8 GiB theo topology hiện tại; chưa đánh giá hàng chục tenant hoặc 100k unit.
Phạm vi: code/contract/deployment config/luồng nóng hiện tại, release và trace cloud đã ghi, main/thien refs có sẵn trong checkout.
Main local `8d3135a`, origin/thien local `5e05c1c` ngày 2026-10-12; không fetch lại remote ở lượt này, không khẳng định tip mới nhất.
Không chạy lại full suite hoặc GPU/load test; không chấm điểm repo hay suy audit tĩnh thành performance acceptance.

**Phân tầng API nhẹ / worker nặng / DB metadata / S3 bytes vẫn phù hợp pilot.** Điểm yếu hiện tại là quá nhiều giao tiếp DB,
khóa đọc lẫn ghi, FIFO CPU và vòng đời artifact/cache chưa đồng bộ. Chưa có bằng chứng cần microservices/Redis/FAISS.
Giảm lượt DB và kiểm topology trước khi mở tính năng nặng; không bỏ quota/fence/receipt để đạt số đẹp.
Đây là report + đề xuất lộ trình, không đổi contract/schema/host/policy live. Thiết kế thay đổi cần chốt rồi đưa vào cấp 2 trước code.

## Bằng chứng mới và giả định cần thay

- Worker live dùng session pooler `aws-0-ap-northeast-2.pooler.supabase.com:5432`.
  5 SELECT 1 tuần tự sau warm connection: 260,796 / 260,361 / 260,320 / 260,539 / 521,174 ms, median **260,539 ms**.
  Đây là thời gian client tới kết quả, gồm network/provider/server; không tách riêng RTT hay lock wait, n nhỏ.
  Ví dụ 50 round trips × 0,261 s ≈ 13 s; chưa đếm rằng một job có đúng 50 truy vấn.
- Cache đĩa trên worker đã có: raw 6 file/134.749.065 byte; feature 4 file/2.536.480 byte; model 1 file/5.613.764 byte.
  Cap config 20 GiB; LRU bảo vệ file mới dùng 10 phút nên có thể vượt cap tạm thời. Cache file không bằng cache object/model RAM.
- Hai PROPOSE gần nhất: handler 2,706/2,423 s; publish 10,941/12,559 s. Upload/clip/delete cũng publish 15,920/14,619/13,137 s.
  [Cloud QA](../../../../setup/FRAMES_CLOUD_QA.md) ghi functional smoke thật và giới hạn, không A/B hoặc UX acceptance.
- Architecture §4.3 ước P2/P3 mili-giây là **thuật toán core trong MVP local**, không là ETA cloud gồm hydrate/admission/queue/DB/media.
  DRAFT D1 yêu cầu estimator/capacity evidence chưa được hoàn tất cho mọi job; policy test/bonus/hold hiện tại không là capacity plan.
- Batch 16 là output; input scoring là toàn pool. Upload 5 phút @4 fps khoảng 1.200 unit/2.400 JPEG, chưa kể raw/feature/clip.
  Không dùng một video nhỏ hoặc số local moto để chốt quota/ngân sách cloud.

## Các vấn đề nên xử lý, theo tác động

### 1. DB ở xa nhưng command/worker thực hiện nhiều bước hỏi-đáp

`pilot_runtime.publish/_settle`, enqueue/claim/pin và profile checks có nhiều execute phụ thuộc; pool giúp tránh connect mới nhưng không xóa round trips.
Đã có pipeline nhóm ghi (#116), nhưng publish vẫn chậm trên nhiều kind. Mỗi việc nhỏ cũng chịu chi phí kiểm trạng thái/quota/close.
**Sửa nhỏ nhất:** đo query groups/lock waits, gộp SELECT và batch write cùng transaction; dùng SQL set-based/pipeline cho nhóm độc lập.
Nếu còn nhiều nhánh phụ thuộc cần nhiều lượt qua WAN, cân nhắc function DB theo command cụ thể sau thiết kế/permission tests,
không đưa thuật toán/ML vào DB hoặc dựng bộ RPC mới cho mọi thứ. Bỏ qua: mọi tính năng mới kế thừa fixed overhead 10–15 s.

### 2. Read path đang cạnh tranh cùng khóa ghi của account

`PilotMedia.request/get`, `PilotRuntime.get_job/list_jobs` dùng lock_profile FOR UPDATE; poll/ảnh của cùng account có thể chờ transaction publish.
Auth middleware đọc profile, service có thể kiểm lại profile; lượt auth/authz cũng phải đo, không cache active lâu để tiết kiệm.
**Sửa nhỏ nhất:** read path kiểm active/owner/RLS bằng SELECT không khóa độc quyền, cấp URL/media theo batch có giới hạn cho lưới.
Snapshot/quota/enqueue/publish/delete vẫn dùng khóa hợp lệ theo contract. Poll đúng job thay list toàn account; DTO lịch sử tách summary/detail.
Không đổi sang SSE ngay: SSE không làm job nhanh hơn, Render Free/API RAM thấp và DB listener cần thêm lifecycle nếu chưa cần.
Bỏ qua: thêm user/batch ảnh sẽ tăng chờ khóa và request ngay khi CPU còn rảnh.

### 3. Queue CPU tách khỏi MEDIA nhưng vẫn chặn công việc tương tác

QUEUE_KINDS CPU gồm upload/prepare frames/PROPOSE/YOLO/METRICS/export/delete; một dispatcher CPU chạy một job một lúc.
Claim round-robin owner, trong owner FIFO theo created_at. Upload dài đang chạy vẫn làm proposal mới chờ; ưu tiên queued không preempt job đang chạy.
**Sửa nhỏ nhất:** đo queue wait theo kind và mixed workload trước. Nếu vượt budget tương tác, thiết kế phân nhóm interactive/bulk
với budget CPU/RAM tổng, fairness/starvation và lease/slot accounting; ưu tiên queued trước, thêm lane chỉ khi cần và đủ RAM.
Không tăng tất cả concurrency: OMP/MKL config 3 threads trong nhiều process cùng host 3 vCPU có thể oversubscribe khi cùng hoạt động.
Tune theo process/kind và benchmark CPU MEDIA, không nhận định hiện đã quá tải. Bỏ qua: report/export/reference jobs khiến UI chờ lâu hơn.

### 4. Artifact đã bền nhưng chuẩn bị/tiêu thụ chưa dùng chung hết

PROPOSE/METRICS mở raw cache để probe/grid và NPZ để load mỗi job. GPU handler tải raw vào attempt riêng,
Kaggle sau đó tải raw qua signed URL; output feature PUT S3 rồi xóa workdir, chưa chủ động populate feature-cache.
YOLO có checkpoint file cache nhưng load model mỗi job, decode exact từng anchor; JPEG S3 hiện là lossy quality 85.
**Sửa nhỏ nhất:** manifest unit/grid bất biến theo raw/spec/decoder, dùng raw cache thống nhất, promote validated feature vào disk cache,
model/array RAM cache có cap khi có lợi. Payload chỉ tham chiếu hash/version; quyền và publish guard vẫn kiểm DB.
Không thay pixel inference bằng JPEG hiện có: pixel đã nén khác exact RGB, có thể đổi embedding/boxes. Cần version/parity/evaluation riêng
nếu muốn chọn JPEG làm inference input. Bỏ qua: reference/model/report sẽ lặp hydrate/download/decode và trộn input semantics.

### 5. Materialize 4 fps tạo nhiều object và metadata hơn estimate ban đầu

Hai JPEG/unit, raw và feature/clip/PII derivatives tạo amplification ở PUT/HEAD/DB/inventory/quota/cleanup.
S3 upload không đi qua API là quyết định tốt, nhưng READY vẫn đợi đủ bộ ảnh; staging giữ 15 phút + grace 60 phút hợp lệ, không là leak.
**Sửa nhỏ nhất:** frame/receipt/storage batch toàn luồng, batch delete theo inventory có kiểm per-key/fence/retry và confirmation đầy đủ,
giới hạn media lookup/grid, đo object/bytes/temp/disk. Giữ 4 fps/ID ở giai đoạn tối ưu implementation.
Giai đoạn sau mới so optional representative subset/lazy higher-resolution với spec version/readiness/quota mới;
không âm thầm bỏ FRAME/THUMBNAIL hoặc đổi sampling để chốt speed. Bỏ qua: thêm PII/export có thể nhân đôi/triplicate storage và cleanup.

### 6. Telemetry có thể tự thêm tải DB và khóa toàn cục

`TimingCollector` flush tối đa 100 event; `append_events` lấy advisory lock chung rồi INSERT từng row;
supervisor/CPU/MEDIA có thể cùng flush/ghi heartbeat component worker. Đây là **rủi ro cần đo**, chưa chứng minh gây số publish hiện có.
**Sửa nhỏ nhất:** so trace on/off trong namespace test, batch INSERT có cap/retention, đo dropped/flush wait;
heartbeat tách danh tính process nhưng màn operator vẫn nhóm host. Giữ log bắt buộc và critical usage receipts, không tắt audit để claim tăng tốc.
Bỏ qua: tải test càng lớn thì chính đo đạc càng gây chờ, telemetry không phân biệt tiến trình chết.

### 7. Region, pool và gói free chưa được đưa vào budget tương tác

Deployment có Render Singapore, Supabase session endpoint ap-northeast-2; vị trí vật lý worker và region S3 cần inventory xác nhận,
không suy từ IP/provider. Connection pool max 4 mỗi PilotDB/process: supervisor+CPU+MEDIA+GPU tối đa 16, thêm API 4 thành 20,
chưa kể operator/maintenance và instance tạm khi rollout. Đây là upper bound cấu hình, không là số connection đang dùng.
**Sửa nhỏ nhất:** đo worker/API→DB, worker/Kaggle/browser→S3, connection occupancy/checkout và idle polling. Budget connection toàn hệ thống,
không tăng pool riêng từng process theo cảm tính. Direct/session/transaction pool là lựa chọn consistency, không là cách giảm khoảng cách network.
Giữ connection operator cho session advisory lock/import; không chuyển toàn bộ sang transaction pool mà chưa test RLS/reset/prepared/pipeline.

Render Free có sleep/cold start sau 15 phút không traffic theo [Render docs](https://render.com/docs/free).
Keepalive trong architecture không là SLA; warm/cold cần đo riêng. Đổi gói chỉ có thể giải quyết cold start/resource, không làm worker→DB gần hơn.
[Supabase connection guidance](https://supabase.com/docs/guides/database/connecting-to-postgres),
[Psycopg pipeline](https://www.psycopg.org/psycopg3/docs/advanced/pipeline.html) là nguồn kỹ thuật; không suy provider bottleneck từ query wall alone.

### 8. Plan deployment/feature chưa có bằng chứng thống nhất và release matrix

render.yaml là legacy static fallback; compose.release là API/web tách, live là combined. Runbook ghi lịch sử nhiều lát;
rollout vừa rồi gặp incompatibility upload payload API cũ/worker mới. Backup dump đã có nhưng restore acceptance chưa đầy đủ.
**Sửa nhỏ nhất:** release manifest source/image/API-payload/schema/policy/test evidence và bảng tương thích client/API/worker,
preflight fail trước mutation, schema expand/contract, drain/admission window cho cutover không tương thích, rollback đã rehearsal.
Phân biệt legacy config và một đường release hiện hành, không xóa hỗ trợ v1 chỉ để dọn docs. Bỏ qua: từng tính năng lại cần sửa live bằng tay,
không biết image nào với policy/schema nào hoặc rollback có an toàn cho data mới.

### 9. Dataset/model tham chiếu cần nền tảng artifact, chưa cần hệ thống dataset mới

Local v1 có dataset/model/reference/candidate-guided; pilot catalog có SYSTEM checkpoint/YOLO nhưng chưa binding train/reference đủ provenance.
Tái dùng source/artifact/manifest/version + owner/SYSTEM, không làm ba catalog Bronze/Silver/Gold hoặc ba bản copy dataset.
Bronze là raw receipt/source, Silver là validated unit/features/labels, Gold là snapshot curated/export có mục đích;
tag/lineage mô tả trạng thái, không coi keep tương đương labeled/training-ready.
**Chuẩn bị:** immutable dataset manifest/class map/split/feature keys/model SHA/runtime/train lineage; session pin binding,
bounded candidate/inference, result cache keyed proposal/model/config/anchors, report schema và metric ground-truth gates.
Kiểm nhãn BDD + model thực sự train/fine-tune trên version khai báo trước mở model-guided; không gán COCO nano là model train BDD.
Bỏ qua: tính năng tương lai phải tái dựng context, cache sai version và report khó kiểm chứng.

### 10. Report/export và vòng active learning còn thiếu gate sản phẩm

YOLO/analysis hiện có không đồng nghĩa report/export PII hoàn chỉnh; CONTRACTS §21.8/DEP6 còn gate detector/quality/owner confirm/hash/ZIP READY.
**Chuẩn bị:** một frozen snapshot/binding làm input inference/report/export, artifact/evidence cùng version, report trước hết có thể metadata-only;
ảnh/nearest-reference/contact-sheet xuất ra phải qua PII theo contract, không dùng preview raw lách cổng.
Auto inference sau PROPOSE phải opt-in/budget/dependency design, thất bại model không mất proposal. Không auto retrain.
Accuracy/mAP cần nhãn/split hợp lệ; active learning closed loop là export→label→dataset version→train ngoài→model version→eval→binding mới.
Bỏ qua: demo prediction có nhưng chưa có dataset deliverable hoặc bằng chứng cải thiện downstream.

## Lộ trình tổng, ánh xạ vào plan hiện có

| Lát | Kết quả và thứ tự | Dùng lại / gate |
|---|---|---|
| A0 | Inventory + latency/capacity/cost baseline toàn luồng, xác nhận region/pool/mixed workload | N1 P0 mở rộng; không mua/chuyển provider |
| A1 | Read/write DB boundaries, batch media/job read, enqueue/publish/recovery tối ưu chung, telemetry batch | N1 P1–P3 là phần proposal; upload/clip/delete/ops phải test regression và A/B |
| A2 | Scheduling tương tác/bulk + resource envelope/thread/pool budgets và fairness | 3 account, upload dài + proposal + clip/YOLO; priority không giả preempt |
| A3 | Manifest grid/features/model và cache xuyên prepare→propose→analysis/inference | N1 P4, exact pixel/version; restart/cap/owner/source guards |
| A4 | Policy estimator + quota UX + deployment compatibility/restore gate | N2 + DEP7/DEP8; numeric policy từ evidence, không thay quota qua UI |
| A5 | Dataset/model immutable binding và bounded recipe/report contracts | N3 trước N4/N5; N6 sampling/closed loop sau; DEP6 PII song song khi inputs đã ổn |

Triển khai từng lát; proposal plan là nhánh con của A1/A3, không thay toàn N1. A0 trước mọi đổi topology;
A1 trước A2 nếu contention chủ yếu DB; A3 foundations trước binding N3; không gộp cả lộ trình thành một commit/release.

## Đo lại ước lượng deployment

Phải lưu evidence theo hardware/image/policy/raw/spec/model/recipe, cold/warm/trace mode, concurrency 1 và 3 account.
Mỗi job: admission/queue/handler/DB publish/cleanup riêng, wall/CPU/GPU session ms riêng; peak RSS **toàn cây process**, temp/cache/bandwidth.
CPU quota không là ETA wall; GPU session gồm startup/transfer/stop verified. Hold/deadline/cap là guard, không là số thời gian hứa với người dùng.
Số tiền chỉ tính sau biết region/provider rate và giữ URL/ngày báo giá; trước đó báo object requests/egress/GB-hours, không ước tiền tùy ý.

Ma trận tối thiểu: video 21–30 s và 5 phút cùng file/resolution, 720p/1080p ở input envelope; multi-video pool theo max_units;
warm/cold disk và model RAM; upload+clip+proposal đồng thời; DINO/CLIP startup+session stop; YOLO/analysis; delete nhiều ảnh;
export/report khi implemented. Cold provider với worker restart là điều kiện khác cache cold.
Mock test correctness/fault riêng, benchmark cùng hạ tầng trước/sau đảo thứ tự ít nhất 5 lượt; p50/p95 ghi sample count/giới hạn.

Mục tiêu **để chốt sau A0**, không SLA đã duyệt: warm API read p95 <1,5 s; publish nhẹ p50 <5 s;
proposal warm click→batch p50 <10 s ở pool pilot chuẩn; clip cache/grid không chờ CPU bulk.
Upload dài dùng target tương đối thời lượng + object count, GPU dựa phase budget; không một hằng số cho mọi video.
Nếu A1 không đạt và network/client DB floor vẫn lớn, so topology có kiểm chứng ở môi trường cô lập.

## Các lựa chọn topology sau số đo

| Lựa chọn | Lợi ích dự kiến / giới hạn | Điều kiện trước triển khai |
|---|---|---|
| Giữ 4 dịch vụ, giảm command round trips | Ít thay nhất; giải fixed overhead và khóa, không giảm độ trễ vật lý mỗi query | Mặc định ưu tiên; A1/A3 đạt correctness và đo speed |
| Đặt worker gần DB/S3 (hoặc chọn region phù hợp cho bản triển khai mới) | Giảm I/O WAN cho cả upload/proposal/media/delete | RTT/transfer/quote/restore so sánh; không migrate live DB/Auth/S3 thiếu inventory/backup |
| Gom FE/API cùng VPS, giữ worker process/container riêng | Giảm số host vận hành, có thể giảm API↔worker adjacency; worker→DB vẫn như cũ | RAM/CPU/reserve cho API, TLS/auth/backup/uptime/rollback; không public worker bypass quota |
| API luôn bật, tách static/CDN nếu traffic cần | Giảm cold start và static load; không sửa publish slow | Đo cold/warm và cost; không mua gói để che DB vấn đề |

Không chọn tự host PostgreSQL, Redis/broker, vector DB/ANN hoặc giữ Kaggle session thường trực ở bước này.
Chúng chỉ vào thiết kế khi đo chứng minh giới hạn DB queue/compute/storage, hoặc scope vượt pilot và người dùng chốt.

## Kiểm chứng và quyết định

- Quyền/ownership/RLS, CAS/idempotency, lease/fence/generation, UNKNOWN/quarantine/stop-proof,
  storage reserve/persisted/receipt, partial cleanup, PII/confirm và immutable provenance không bị cắt.
- Mỗi lát code: focused functional/fault test + regression module dùng chung, ruff/tsc/build khi liên quan;
  full suite ở integration checkpoint, browser local, Contabo namespace cô lập, rồi cloud smoke khi được yêu cầu release.
- Test 3 account fairness/long jobs, profile disable lúc đọc/submit, burst image polling, no active jobs/holds/pins leak ngoài staging hợp lệ.
- A0 phải đề xuất plan số liệu và lựa chọn cần người dùng chốt; không coi report này là duyệt đổi host/schema/queue/sampling.
  Ưu tiên N1-Proposal đang đề xuất trong nền tảng A0/A1, chưa code/deploy hoặc push Git ở lượt audit này.

Chưa kiểm: full repository logic/test xanh hiện tại, live pool occupancy/locks/pg_stat_statements, tất cả region/giá,
GPU concurrency thật, tải 3 user, restore drill, tip remote mới và latency của tính năng chưa implement.
