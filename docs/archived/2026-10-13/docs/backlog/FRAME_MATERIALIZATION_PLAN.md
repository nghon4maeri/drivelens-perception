# Lưu frame trên S3 và tách worker — thiết kế để duyệt

Ngày: 2026-10-12. Lát DEP7-Frames. Trạng thái: yêu cầu sản phẩm đã xác nhận;
chi tiết dưới đây là đề xuất, chưa thay CONTRACTS/architecture, chưa code hoặc deploy.

## 1. Yêu cầu đã xác nhận

- Khi xử lý video, tách frame và lưu sẵn trên S3; khi xem thumbnail/frame không decode video theo request.
- Người dùng cho phép chọn nhịp lấy mẫu hợp lý thay vì bắt buộc 5 frame/giây.
- Giữ clip ngữ cảnh từ video gốc (người dùng xác nhận riêng); clip vẫn render/cache theo yêu cầu.
- Đồng thời tối ưu điều phối worker và luồng media đã phân tích trong
  `output/latency-release/resource-flow-review.md`.

## 2. Phương án đề xuất

Giữ `DEFAULT_SAMPLE_SPEC` hiện tại: decoder pyav 4 fps + per_frame. Đây là lưới index
baseline, không bảo đảm đúng 4 frame trong mọi giây đối với nguồn fps thấp hoặc VFR.
Dùng ordered frame indices hiện có, đọc tuần tự bằng `read_exact`, không suy index từ timestamp.
Giữ core IDs, Unit IDs, FieldKey, embedding và snapshot cũ. Không thêm bước chọn keyframe
hay lọc độ nét: việc đó thay candidate pool và cần một thiết kế thuật toán khác.

Mỗi unit có JPEG đầy đủ độ phân giải và thumbnail JPEG cạnh dài tối đa 320 px, quality 85.
Hai ảnh được encode trực tiếp từ cùng RGB exact anchor, thumbnail không resize từ JPEG đã nén.
Ảnh JPEG dùng để hiển thị; DINO/CLIP/YOLO/export tiếp tục dùng pixel exact từ raw theo contract
hiện tại, không lấy ảnh JPEG lossy thay đầu vào ML. Video raw được giữ cho các luồng đó và clip.

Video 5 phút ở nhịp danh nghĩa 4 fps có khoảng 1.200 unit / 2.400 object ảnh.
Đây là số lượng, chưa phải ước lượng byte hoặc chi phí S3. Chuyển chi phí decode/encode/PUT
sang bước chuẩn bị; upload tới READY có thể lâu hơn, còn mở ảnh tránh job render.

## 3. Upload và nguồn cũ

Nguồn mới:

1. Direct upload staging như hiện tại; worker kiểm SHA, giới hạn video và lưới unit.
2. Kiểm dedup owner/content trước khi tách ảnh. Nguồn trùng đã có bộ ảnh hoàn chỉnh dùng lại nguồn cũ.
3. Encode tuần tự vào workspace trên ổ repo/volume worker; RAM giữ số frame hữu hạn.
   Kiểm giới hạn byte/file tạm và dung lượng đĩa trước/trong xử lý, hủy phải dừng giữa các lô.
4. Đếm byte thực tế; giữ thêm dung lượng USER/GLOBAL trước PUT ảnh. Khoản `2 × declared_bytes`
   hiện có chỉ dành cho staging/raw, không đủ để mặc nhiên chứa frame.
5. Commit receipt INTENT theo lô trước PUT; ghi S3 với concurrency hữu hạn, kiểm checksum/kích thước.
   Mỗi object vẫn có receipt riêng, không tạo transaction riêng cho từng ảnh.
6. Publish source + bộ ảnh hoàn chỉnh + dung lượng trong một transaction fenced.
   Upload READY chỉ sau khi raw và mọi frame/thumbnail trong manifest đã xác minh.
   Trùng nội dung khi publish: chỉ một bộ được nhận; bộ dư chờ cleanup trước nhả dung lượng.

Nguồn đang tồn tại: job chuẩn bị bộ ảnh explicit, dedup một active job mỗi source/version/spec.
API đọc không tự enqueue backfill. UI có trạng thái chưa chuẩn bị/đang chuẩn bị/lỗi và nút chuẩn bị.
Backfill giữ nguyên source ID, resource version và feature refs nếu raw/spec không đổi;
không làm phiên/feature đang dùng bị stale chỉ vì thêm artifact hiển thị.
SYSTEM do operator chuẩn bị riêng; user không được ghi artifact vào nguồn SYSTEM.
Chuyển nguồn cũ theo inventory và dry-run; không tự xử lý tất cả dữ liệu cloud trong rollout.

Không có fallback decode ảnh tại lúc xem. Nguồn cũ chưa chuẩn bị hiển thị trạng thái rõ ràng;
ảnh cache cũ chỉ dùng được nếu xác minh đủ provenance và cùng exact index/encoding spec.

## 4. Storage, metadata và phục hồi

Tái sử dụng artifact/object receipt/storage hold hiện có. Migration additive thêm metadata
bộ ảnh và mapping `(source, raw_sha, sample_spec_hash, encoding_version, unit, variant)` → artifact.
Chỉ đánh dấu bộ ảnh READY khi đủ ordered IDs và cả hai variant; partial attempt không được cấp URL.
Object key do server dựng và có attempt/artifact identity; retry không overwrite ảnh của attempt đã được nhận.
DB lưu key/hash/byte/index/provenance, không lưu signed URL hoặc bytes ảnh.

Publication nhận cả raw và frame receipts của VALIDATE_UPLOAD. Hiện tại
`pilot_objects.accept_selected_in_tx` ở nhánh upload chỉ nhận raw: phải mở rộng cùng thay đổi,
nếu không supervisor có thể xóa nhầm ảnh vừa publish.
Storage reservation phải xét tổng byte các INTENT chưa được nhận, không chỉ byte của object hiện tại.
Không trả storage/pin/slot trước khi receipt dư đã cleanup và usage được đối soát.

Source delete inventory gồm raw, ảnh, manifest và artifact khác. Xác minh fence/version/pins theo lô;
ghi tiến độ bền theo key, giữ DELETE_PENDING khi lỗi. Không bỏ kiểm tồn tại sau DELETE,
không quét/xóa cả prefix, không nhả persisted bytes khi còn object chưa được xác nhận xóa.
Batch DB và S3 phải giữ khả năng phục hồi partial delete và lease lost giữa lô.

## 5. Media và điều phối worker

Thumbnail/frame mặc định lookup mapping READY và cấp URL S3, không enqueue RENDER_MEDIA.
Giữ endpoint/MediaDTO tương thích nếu có thể; request kích thước khác 320 cần policy rõ:
đề xuất trả ảnh đủ độ phân giải cho client resize, không sinh biến thể tùy ý trong request.
Đổi hành vi `max_side` phải ghi trong CONTRACTS và cập nhật mọi caller/test liên quan.
Clip vẫn theo RENDER_MEDIA và cache hiện có.

Đường đọc READY gộp profile active/policy/owner/source/artifact lookup; lock đồng bộ với
delete/disable tài khoản phải được thiết kế và kiểm race trên PostgreSQL thật trước bỏ FOR UPDATE.
Batch cấp URL không thuộc lần đầu nếu lookup đơn đã đáp ứng số đo, tránh thêm endpoint sớm.

CPU và MEDIA dùng process riêng, supervisor không chạy nối tiếp sau handler. GPU giữ process riêng.
Mỗi process có DB pool/context/identity riêng; thống nhất boot identity của supervisor và các child,
ghi PID/start token đúng process để chứng minh dừng. Stop/deadline/restart không release slot sớm.
Giới hạn ban đầu một dispatcher CPU và một MEDIA, claim vẫn theo slot/policy/owner round-robin.
Đo RAM, thread ML và tổng DB connections; không mặc nhiên tăng pool/slots khi thêm process.

FE chỉ giữ suất trong từng HTTP call; polling có backoff, dedup, ưu tiên clip/frame đang mở,
ngừng polling khi không còn consumer; logout/auth generation bỏ response cũ.
Không giữ suất khi chờ timer, không để hàng nghìn poll làm tăng tải DB.

## 6. Phạm vi thay đổi và gates

Trước code: duyệt thiết kế, cập nhật CONTRACTS §21.7/12/13/16, architecture §11 và runtime/storage spec;
định nghĩa payload/job kind chuẩn bị ảnh cũ, schema mapping, policy giới hạn disk/byte/batch/deadline.
Lát bao gồm migration, upload/backfill/media/delete/cleanup/worker entry, FE trạng thái và polling,
fixtures/test/runbook tương ứng. Không chuyển DINO/CLIP sang JPEG hoặc đổi scoring.

Gates bắt buộc:

- CFR/VFR/index thấp/EOF: ordered IDs khớp baseline, ảnh đúng exact anchor, đầy đủ hai variant.
- Upload dedup/race, byte reservation, hết disk/storage/CPU, cancel/crash/lease lost và retry:
  không publish partial, không nhả hold sớm, không xóa ảnh đã nhận, không tính dung lượng hai lần.
- Media A/B, account disabled, source DELETE_PENDING; thiếu ảnh không decode video ngầm.
- CPU job dài không chặn MEDIA/supervisor; quota CPU theo đúng process; spawn thật và shutdown.
- Xóa hàng nghìn ảnh, partial delete/retry và cleanup crash trên PostgreSQL thật + S3 mock.
- Backfill không đổi proposal/snapshot/feature IDs và không tự chạy khi GET.
- pytest phù hợp + ruff + tsc + FE tests/build; không sửa hooks, không push.
- Benchmark cùng video/anchor/recipe/concurrency, cold/warm riêng, p50/p95:
  upload→READY, ảnh đầu/lưới đầy/clip mở, queue/publish, S3 PUT count, DB round trips, RAM/disk/bytes.
  Đo nhiều lượt; không suy số tăng tốc từ một smoke.

Release cloud là bước riêng sau gates và duyệt target/backfill inventory. Hiện chưa migrate,
backfill, thay image hoặc thay quota trên cloud.

## 7. Bàn giao development và release sau hostname #118

Baseline ngày 2026-10-12: commit `c6d18e0` (hostname), trước đó `e23c894` (latency).
URL hiện tại `https://c4-app-026.onrender.com/app`; FE/API là image Docker gộp trên Render,
worker Contabo giữ riêng. Trạng thái image/digest/rollback ở
[DEPLOYMENT_STATUS](../../../../setup/DEPLOYMENT_STATUS.md) và
[HOSTNAME_MIGRATION](../setup/HOSTNAME_MIGRATION.md). Các ID/digest trên là snapshot bàn giao,
không mặc nhiên là release mới nhất lúc teammate bắt đầu.

Không cần tạo hosting/service mới hoặc đổi hostname/CORS/Auth cho tối ưu này.
Không dùng `render.yaml` để deploy service hiện tại: file đó là Static Site fallback cũ.
Push Git chạy CI theo workflow hiện có nhưng không tự build/push/chọn image trên Docker service đang chạy.

### Trước khi code

1. Checkout baseline đã push, đọc AGENTS → PLAN → guide → CONTRACTS/architecture và tài liệu này.
2. Chốt thiết kế §2–5 với người dùng; phần đã xác nhận chắc chắn là ảnh lưu sẵn S3 và giữ clip từ raw.
   Cụ thể hóa schema/index/RLS, DTO trạng thái chuẩn bị ảnh, payload/job kind backfill,
   policy output/disk/batch/concurrency/deadline, `max_side` và kế toán SYSTEM.
   Không coi draft này là đủ signature để tự code hoặc tự bỏ scope contract.
3. Cập nhật CONTRACTS/architecture/runtime spec và quyết định trước code. Không chỉnh checksum
   migration đã áp; dùng migration additive với số phiên bản được kiểm tra từ repo lúc bắt đầu.
4. Làm trên nhánh riêng, ví dụ `codex/frame-materialization`; một phiên một lát trong PLAN.
   Không sửa hooks/logging, commit tiếng Việt không dấu, không lấy env/secret thật vào fixtures.

### Thứ tự development

1. Schema/storage/receipt theo lô + encode exact và publish upload đủ bộ; kiểm failed/cancel/crash/dedup.
2. Backfill nguồn cũ + SYSTEM operator path; đọc media từ mapping, FE trạng thái và nút chuẩn bị.
   Nối delete/cleanup/storage accounting trong cùng thay đổi trước coi materialization hoàn tất.
3. Tách process CPU/MEDIA/supervisor, sửa identity/boot/watchdog/telemetry/healthcheck và shutdown.
   Kiểm cache raw dùng chung giữa các process: threading.Lock hiện tại không khóa được process khác.
   Chọn khóa liên process hoặc cache tách theo process; atomic file replacement, giới hạn disk và eviction rõ ràng.
4. Sửa suất HTTP/polling/priority; tối ưu lookup READY và round trips có số đo, giữ transaction/fence/quota.
5. Chạy gates §6, cập nhật runbook và gửi diff/commit cho review trước release cloud.

Nếu chia thành nhiều commit, ghi rõ commit nào chỉ là nền tảng chưa đủ phục vụ nguồn mới;
không deploy một commit có API mới nhưng worker/schema/policy chưa tương thích.

### Thứ tự release cloud

1. Kiểm schema/code/image đang chạy, target DB/bucket/prefix, active jobs/leases và snapshot quota;
   backup DB, lưu digest/env policy hiện tại và chuẩn bị rollback. Không log secret.
2. Chạy migration additive bằng operator ngoài boot; xác minh role API/worker và policy đầy đủ.
   Nếu policy cũ có job đang chạy, giữ policy snapshot cũ và đối soát job đó, không sửa payload/hold trực tiếp.
3. Build/smoke image `worker` và `combined` từ cùng commit đã review, pin digest.
   Ưu tiên worker hỗ trợ cả payload cũ/mới trước API phát job mới. Nếu không tương thích,
   phải chốt cửa sổ maintenance/drain trước triển khai; không để worker cũ nhận job mới rồi FAILED.
4. Update worker Contabo và kiểm CPU/MEDIA/GPU/supervisor heartbeat; update combined trên service
   c4-app-026 và kiểm `/health/ready`, login, media, upload, clip, quota và operations.
5. Backfill inventory được duyệt bằng job explicit/dry-run trước apply. Kiểm nguồn SYSTEM riêng,
   dự trù bytes và PUT count; không chạy hàng loạt mọi video của user khi service boot.
6. Đo cloud trước/sau theo §6; image rollback không hoàn tác các ảnh/DB đã publish.
   Migration additive và reader cũ phải tương thích, hoặc có kế hoạch chuyển reader/feature gate
   được duyệt trước cutover. Không drop bảng hoặc xóa ảnh/raw để rollback.

### Khi gửi lại để review

Cung cấp branch/commit và diff so với baseline, docs SoT đã sửa, migration/policy mới,
kết quả pytest PostgreSQL thật/ruff/tsc/FE tests/build và smoke spawn process.
Kèm số đo frame count/bytes/RAM/DB round trips/latency; nêu test nào skip và cloud nào chưa kiểm.
Review tập trung: source/frame identity, partial publish/cleanup, quota và storage không tính trùng,
lease lost/crash/shutdown, owner isolation và disable/delete race, migration/rollback,
polling không tăng tải DB và worker không chặn supervisor.

Bàn giao hiện tại là tài liệu; chưa có implementation tối ưu. Không tick DEP7-Frames hoàn tất.
