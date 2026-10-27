# N1 — Lựa chọn tạm, đề xuất theo lô và cache VPS

Plan gốc ngày 2026-10-13; trạng thái rà lại ngày 2026-10-14 sau #133.

P1/P2 và một phần P3 đã implement ở #129; publish RPC ở #131, claim/lanes ở #132.
#133 thay đường PROPOSE tương tác bằng HTTP/RAM và tự checkpoint batch trước; draft/queue/poll/CPU accounting
trong mô tả gốc bên dưới chỉ còn là lịch sử hoặc compatibility khi interactive chưa bật.
Thiết kế hiện hành theo [CONTRACTS §21.26](../design/CONTRACTS.md)
và [kiến trúc](../architecture_diagram.md), evidence ở [QA #133](../setup/INTERACTIVE_CPU_QA.md).

Không đánh dấu toàn bộ P0–P5 hoàn tất: browser cloud, A/B cold/warm và acceptance còn phải đối chiếu gate trong QA #133;
cache/capacity của upload/media/GPU/delete/report vẫn thuộc A0/A3 và pilot acceptance trong [PLAN](../../PLAN.md).
[QA #129](../setup/PROPOSAL_LOCAL_QA.md), [#131](../setup/PROPOSE_PUBLISH_RPC_QA.md) và [#132](../setup/CLAIM_LANES_QA.md)
giữ bằng chứng tại từng checkpoint, không mô tả cấu hình live hiện hành.
Luồng này thuộc [lộ trình kiến trúc A0–A5](../archived/2026-10-13/docs/backlog/ARCHITECTURE_DEPLOYMENT_REVIEW.md): P0 nằm A0, P1–P3 nằm A1, P4 nằm A3;
không thay việc rà DB/queue/cache/capacity/deployment trên upload/media/GPU/delete/report.
Thiết kế gốc: CONTRACTS §21.3–21.6, §21.15–21.16, §21.20 và architecture.
Trước code mỗi lát, cập nhật CONTRACTS/architecture/UI_DESIGN liên quan theo quyết định #124;
tài liệu này không tự thay signature hoặc điều kiện publish của contract hiện hành.

## Mục tiêu và bằng chứng

- Chọn/bỏ chọn hiển thị ngay, không gọi DB mỗi lần. Chỉ bấm Đề xuất mới gửi cả lô lựa chọn;
  reload/rời phiên trước khi gửi được phép mất lựa chọn tạm (người dùng đã chốt).
- Một request lưu quyết định và enqueue một job PROPOSE; giảm request đọc trạng thái và round-trip DB khi publish.
- Dùng lại raw/embedding đã cache trên VPS; chỉ thêm cache RAM/metadata khi đo được lợi ích.
- Hai job cloud gần nhất: handler 2,706/2,423 s, publish 10,941/12,559 s, attempt 17,433/18,813 s.
  DB timestamps trong transaction không thay thời gian span monotonic hoặc thời điểm commit; không cộng span lồng nhau.
  Đây là hai lượt, chưa phải baseline A/B. Chưa đo khoảng click → enqueue, decision save, cache hit/miss riêng.

Hiện frontend lưu từng thao tác theo queue; Giữ tất cả gửi một lô nhưng backend INSERT từng quyết định.
Polling lấy trang danh sách job của account rồi lọc phiên, và khóa profile. Worker cache raw/feature/model theo SHA trên đĩa;
mỗi job vẫn probe/dựng grid, đọc NPZ vào RAM và replay DB. JPEG hiển thị đi browser → S3.
VPS mount data/output bền qua recreate; cache cap đang 20 GiB, file mới dùng có grace 10 phút nên cap không phải giới hạn cứng tức thời.

## Phạm vi và thứ tự

Mỗi lượt triển khai chỉ một lát; docs trước code, test trước commit. Không thêm Redis, service cache, bảng job mới,
không đổi recipe/sampling/ID/seed/quota, không chạy embedding hoặc model ngầm. Không gộp N3–N6 tham chiếu vào đây.
Pilot v2 là phạm vi lựa chọn tạm; local v1 giữ hành vi hiện có trừ khi được yêu cầu đổi riêng.

### P0 — Đo baseline và chốt contract chi tiết

- Correlation click/propose request/job/attempt; tách draft flush+admission, queue, build_context, scoring/explain,
  publish/quota/close và result fetch/ảnh. Timer cache hit/miss, byte tải S3, NPZ load/probe/grid;
  thống kê lượt DB/wall theo phase, không ghi SQL/params/embedding/token/URL ký.
- Dùng trace hiện có; chỉ bổ sung mốc thiếu. Không lấy số GET polling làm số mẫu hoặc số job.
- Baseline cùng pool/feature/recipe/batch 16, cold/warm; lưu danh sách job và phase đúng cùng lượt.
  Browser click tới grid đo riêng với handler/publish để biết người dùng đang chờ đâu.
- Cập nhật contract cho body proposal có lô `changes` tùy chọn, CAS/version sau khi áp lô, idempotency,
  draft UI và đường đọc job không cần khóa độc quyền profile. Không bỏ kiểm account active/owner/RLS.
- Gate: trace có thể đối chiếu một lượt end-to-end và không cộng span cha/con; xác định DB round trips thực.

### P1 — Draft UI và submit quyết định + PROPOSE nguyên tử

Đụng chính: useSessionData.ts, pilot_api.ts, api_v2.ts, SessionView.tsx; proposal route/DTO,
pilot_db.py và pilot_curation.py. Rà callers state/sidebar/archive/snapshot/undo trước đổi.

- Pilot lưu draft theo unit trong bộ nhớ phiên, overlay lên state đã commit; undo/clear chỉ sửa draft.
  Nhiều lần đổi cùng unit gửi trạng thái cuối; nếu trở về trạng thái đã lưu thì không gửi thay đổi đó.
- Không PATCH/POST quyết định khi click. Không localStorage, autosave hoặc gửi ngầm khi rời phiên.
  Reload, đổi phiên hoặc logout xóa draft; response cũ không được ghi sang phiên/account mới.
- Bấm Đề xuất gửi một POST hiện có kèm `changes`, expected_state_version và Idempotency-Key.
  API trong một transaction: kiểm owner/active/CAS và unit/target_k → append cả lô decisions → tăng version một lần
  nếu có thay đổi → đóng băng state/decision_seq mới → reserve/enqueue PROPOSE. Không tạo job phụ chỉ để lưu status.
- Gom INSERT cả lô bằng khả năng DB hiện có; vẫn append-only và thứ tự decision seq xác định.
  Kiểm lại callers đọc lại state sau enqueue: response hoặc một GET cần thiết phải cho UI biết version đã commit,
  không tự suy version khi server loại no-op. Body không `changes` giữ tương thích client cũ.
- Lỗi validation/quota/feature_not_ready/enqueue: rollback cả decisions/version/hold/job, giữ draft để sửa/thử lại.
  Response mất sau commit: retry đúng key/body phải trả cùng job, không append decisions/reserve lần hai;
  kiểm replay trước CAS để retry không bị chặn bởi version vừa tăng. Key mới/body khác theo luật conflict hiện có.
- Khi API đã xác nhận enqueue, draft được commit dù worker sau đó lỗi/hủy. Worker retry dùng state đã lưu,
  không gửi lại draft lần hai. Chặn double click/in-flight và duyệt khi PROPOSE hoạt động như hiện tại.
- Draft chỉ thể hiện lựa chọn tạm; sidebar/tập đã lưu/snapshot phản ánh state server, không giả đã lưu.
  Khi có draft, chặn tạo snapshot/xuất dựa trên draft với giải thích cần gửi qua Đề xuất trước;
  không âm thầm lưu lựa chọn chỉ vì mở tab phân tích/tập đã lưu. Sau submit thành công refresh counts/state.
- Gate test: 16 thao tác = 0 request ghi trước submit, 1 POST proposal chứa lô; giữ/bỏ/clear/undo/no-op đúng;
  reload/rời phiên mất draft; quota/feature/CAS lỗi không ghi partial; timeout retry/double click đúng một job;
  source ngoài pool/A-B/target_k atomic; worker nhận decision_seq/version mới; local v1 regression giữ nguyên.

### P2 — Poll đúng job và đường đọc nhẹ

- Sau enqueue giữ job ID, dùng GET /jobs/{id} cho job hiện tại; không lấy toàn bộ danh sách account mỗi vòng.
  Reload dùng discovery có lọc theo phiên/PROPOSE ở server nếu cần; không bỏ sót job khi vượt trang 50 bản ghi.
- Một timer/request đang bay mỗi job; backoff giới hạn 1 → 2 → 3 giây, dừng khi terminal/unmount/logout;
  tránh effect đổi queued → running tạo polling chồng. Timeout/error retry có giới hạn, không POST lại proposal.
- Khi SUCCEEDED lấy result một lần rồi đúng proposal mới; giữ luồng tải lịch sử riêng khi người dùng cần.
  Không fetch result lặp trong mỗi vòng, không reload toàn bộ proposal evidence chỉ để hiện một batch.
- Job/status/result đọc kiểm auth/active/owner/RLS nhưng không giữ FOR UPDATE profile.
  Chốt consistency với disable account trước code; transaction ghi/claim/publish vẫn theo thứ tự khóa contract.
- Gate test: request log có một polling chain, cancel/retry/error/terminal đúng, response cũ bị bỏ;
  PostgreSQL thật: poll không đợi khóa độc quyền profile do publish, A/B và inactive bị từ chối;
  discovery đúng khi nhiều hơn 50 job, result chỉ tải một lần.

### P3 — Giảm round-trip publish, giữ bảo đảm dữ liệu

- Theo trace P0, gom đọc job/attempt/policy và batch cập nhật usage/hold/receipt/release/close trong transaction hiện có.
  Tận dụng helper nhóm ghi đã có; đo trước khi thêm SQL/function mới. Không thay bằng nhiều request ghi chạy song song.
- PROPOSE vẫn kiểm worker/fence/lease/slot generation/cancel và session state_version/decision_seq/proposal_count;
  batch chỉ publish một lần, immutable và đúng ordered IDs/evidence. Quota CPU/GPU thực tế và refund không đổi.
- Không trả SUCCEEDED trước commit hoặc defer quota/receipt sang tác vụ không bền. Không bỏ receipt vì PROPOSE không tạo ảnh.
  Đường dùng chung upload/media/delete/GPU phải giữ nguyên cleanup/storage accounting/quarantine/recovery.
- Gate test: publish replay, race hai worker, lease mất/cancel/context đổi, lỗi DB rollback, quota base/bonus/refund;
  heartbeat không bị đói khóa, các handler dùng chung regression. Đo số round trips/lock wait trước-sau.

### P4 — Cache VPS theo dữ liệu bất biến, có giới hạn

- Giữ cache SHA raw-cache/feature-cache/model-cache hiện có; cache miss tải S3 và kiểm SHA, hit không tải bytes lại.
  Cache là tối ưu sau kiểm quyền/payload; không dùng cache để bỏ qua publish guard hoặc trả dữ liệu của user khác.
- Ưu tiên cache metadata/probe/grid bất biến theo raw SHA + decoder/sample spec version, exact IDs và số unit;
  metadata của một phiên bản không được tái dùng cho source/spec khác. Không cần raw/probe lại khi đủ metadata đã kiểm
  chỉ sau cập nhật CONTRACTS và test parity, không tự thay đường dựng Context hiện hành.
- Chỉ khi P0 thấy NPZ load/RAM allocation đáng kể, thêm cache embedding đã nạp vào RAM trong process CPU:
  key gồm artifact SHA, feature key/producer/version/params/spec + ordered IDs; read-only, LRU theo bytes.
  Không chỉ cache 16 picks: scoring có thể cần toàn pool. Không giữ vô hạn một Context có decisions/proposals cũ.
- State/history/recipe/derived fields phụ thuộc state dựng mới theo payload đóng băng; cache base arrays bất biến.
  Derived field chỉ cache khi xác định đủ dependency key và đo được lợi ích; không thêm trước cho đầy đủ.
- Chốt RAM cap từ RSS/free RAM đo trên host, chừa CPU/MEDIA/GPU và hệ điều hành. Item lớn hơn cap chạy đường cũ,
  không OOM; restart worker mất cache RAM nhưng kết quả không đổi. Không nhân cache nặng sang mọi process.
- JPEG vẫn browser → S3. Không mirror toàn bộ ảnh xuống VPS cho PROPOSE chỉ dùng embeddings.
  YOLO cần ảnh: xử lý riêng theo exact anchor đã lưu nếu có lợi; không gộp thay decoder YOLO vào lát cache này.
- Gate test: cold/warm cùng picks/order/scores (tolerance contract), warm không S3 download cho artifact còn cache;
  đổi SHA/spec/version không dùng nhầm, thiếu/corrupt artifact không tự tính feature;
  read-only arrays giữa job, LRU/cap/oversize/restart/fallback, RSS không tăng vô hạn, cancel/delete/source change vẫn được kiểm.

### P5 — Kiểm local, Contabo cô lập rồi release riêng

- Local PostgreSQL thật + S3 mock: functional/fault gates từng lát; vitest/tsc/build local và pilot,
  pytest/ruff theo phần đổi. Trước Python/npm dùng use_project_storage.ps1, pytest basetemp trong output.
- Browser local thật: 16 lựa chọn không gọi API, submit một lô, job terminal → batch mới, reload/undo/lỗi rõ.
  Test với handler chậm chủ động; không suy smoke HTTP thành UI đã mượt.
- Contabo cô lập: cùng image candidate, env/DB/S3 test riêng; CPU/MEDIA concurrency, shutdown/recovery, cache budget.
  Không thay live service trước các gate này. Không chạy fixture fake JWT vào API live.
- A/B trên cùng input/pool/recipe/feature, đảo thứ tự trước/sau và tối thiểu 5 lượt mỗi điều kiện cold/warm;
  ghi p50/p95, queue/handler/publish/total tới batch hiện, DB calls, S3 byte, RSS. Replay state/seed cố định trong namespace riêng.
  Kết quả ít mẫu phải ghi giới hạn; không gán p95 chính xác hoặc claim tăng tốc do cache khi input khác.
- Mục tiêu đo: thao tác chọn không network ghi; job poll không khóa chặn; số round trips publish giảm;
  publish p50 giảm ít nhất 50% trên cùng hạ tầng, hướng tới dưới 5 s (mục tiêu, không cam kết).
  Nếu chưa đạt phải báo chặng còn chậm, không tick gate độ mượt chỉ vì functional pass.
- Release sau khi được yêu cầu: backup/digest/rollback, drain hoặc cửa sổ admission nếu thay request contract,
  worker/API phối hợp theo tính tương thích version; deploy rồi auth browser + Supabase/S3 thật trên nguồn/phiên test riêng.
  Kiểm quyết định đúng sau refresh, cùng job retry, receipt FINAL/cleanup/slot, không đụng dữ liệu user có sẵn.
  Ghi bằng chứng gate còn thiếu; không tự push/merge main hoặc auto backfill/GPU run.

## Bàn giao và điều kiện dừng

Mỗi lát P0–P5 báo file đổi, test đã chạy, số đo có giới hạn và phần chưa đạt; test xanh thì commit tiếng Việt không dấu,
tick riêng lát đã đạt trong PLAN rồi dừng. P4 cache RAM có thể kết thúc bằng quyết định không thêm nếu đo không cần;
vẫn phải ghi bằng chứng. Không dùng phần tính 2,4 s để hứa total dưới 3 s khi DB/polling/ảnh chưa tối ưu.
