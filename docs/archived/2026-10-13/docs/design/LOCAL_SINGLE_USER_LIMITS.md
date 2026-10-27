# ⚠️ Giới hạn: MVP là bản 1 người dùng, chạy local

> Ghi 2026-09-27. **Đọc file này trước khi deploy lên bất kỳ server nào** (kể cả Live URL cho Demo Day).
> Kiến trúc hiện tại ([architecture_diagram.md](../../../../architecture_diagram.md)) cố ý tối giản cho **1 người, 1 video, 1 tiến trình, máy cá nhân**.
> Các giả định dưới đây sẽ sai khi có nhiều người dùng; phải xem lại từng dòng trước khi host.

## Giả định đang dùng và việc phải làm khi host

| # | Giả định trong MVP | Sai ở đâu khi nhiều người dùng | Cần làm trước khi host |
|---|---|---|---|
| 1 | **Phiên không gắn với người dùng** (`session_id` = uid, ai cũng liệt kê/mở được mọi phiên) | Người này mở và sửa phiên của người khác; hai tab cùng phiên ghi đè nhau | Phiên gắn `owner`; chỉ liệt kê/mở phiên của mình; khoá hoặc phiên bản hoá khi ghi |
| 2 | **Không có đăng nhập** | Ai có URL cũng xem/sửa/export được mọi phiên | Thêm auth (ít nhất token/Basic cho demo), kiểm tra quyền theo phiên |
| 3 | **Tính toán đồng bộ trong request** (mở phiên lần đầu mất vài phút) | Request vượt timeout của host/proxy; chặn worker, người khác phải chờ | Job nền + báo tiến độ (xem §10 của architecture); hoặc tính sẵn trước khi demo |
| 4 | **SQLite** một file | Ghi đồng thời bị khoá; không chia sẻ giữa nhiều instance | Bật WAL cho vài người; nhiều instance → Postgres |
| 5 | **Lưu trên đĩa local** (`data/videos/`, `output/`: `.npy`, SQLite, export) | Đĩa của nhiều host là tạm thời (mất khi restart/redeploy); không chia sẻ giữa instance | Persistent volume hoặc object storage (S3/GCS); dọn dẹp theo dung lượng |
| 6 | **Upload chỉ kiểm tra cơ bản** (mở thử bằng decoder, giới hạn dung lượng), mọi video chung một thư mục `data/videos/` | Người dùng thấy/dùng video của nhau; file độc hại hoặc quá lớn làm đầy đĩa, treo decoder | Tách thư mục + quyền theo người dùng; hạn mức dung lượng; giới hạn thời lượng/độ phân giải; xử lý decode trong tiến trình tách biệt có timeout |
| 7 | **Media phục vụ static, không kiểm soát truy cập** | Lộ frame/video của người khác qua URL đoán được | Media đi qua endpoint có kiểm tra quyền hoặc signed URL |
| 8 | **Export lưu trên đĩa server, giữ mọi lần, không dọn** (`output/exports/<sid>/<export_id>/` + zip; tải qua endpoint download, #54) | Đĩa đầy dần; ai gọi được API là tải được export của mọi phiên | Dọn theo hạn / số bản; lưu object storage + link có hạn; kiểm quyền theo người dùng |
| 9 | **Một tiến trình, model + cache nằm trong RAM** | Nhiều worker = nạp model nhiều lần, cache trong RAM không đồng bộ | 1 worker tính toán riêng; cache dùng chung qua field store trên đĩa/storage |
| 10 | **Máy dev, CPU, RAM thấp** | Host miễn phí thường ít RAM/CPU hơn; DINOv2 + nhiều người có thể OOM | Đo RAM thật; tính feature offline rồi chỉ deploy phần đọc; giới hạn số phiên đồng thời |
| 11 | **CORS mở, không rate limit** (dev) | Bị gọi tràn, lạm dụng | Giới hạn origin, rate limit endpoint nặng |
| 12 | **Không có event log / xác nhận hành động của agent** | Nhiều client (UI + agent + tab khác) không thấy thay đổi của nhau | Làm phần CONTRACTS §11.3 (event log, confirm) khi có agent hoặc nhiều client |
| 13 | **Frame/thumbnail decode theo request** (không cache ảnh trên server; decoder giữ file video mở trong tiến trình, mỗi video một khoá — request song song cùng video chờ nhau, #49) | Nhiều người xem lưới cùng lúc → CPU decode dồn, request cùng video xếp hàng sau một khoá | Cache ảnh trên đĩa/CDN; nhiều handle mỗi video (pool) hoặc tiến trình decode riêng |
| 14 | **Migration SQLite tự viết** (`store/db.py`: chuỗi migration theo `PRAGMA user_version`, sao lưu `rav.sqlite.v<cũ>-<thời điểm>.bak` cạnh DB trước khi nâng, #64). Không có rollback tự động về bản cũ; DB mới hơn app thì BE từ chối chạy | Nhiều instance cùng nâng một DB; backup nằm cùng đĩa với DB | Migration chạy một lần khi deploy (không lúc khởi động mỗi worker); Alembic khi lên Postgres; backup ra nơi khác |
| 15 | **P1 chạy nền trong tiến trình API, một worker, trạng thái job trong RAM** (`service/video.py`, #56) | Khởi động lại BE là mất trạng thái job (video về "chưa chuẩn bị", bấm Chuẩn bị lại); nhiều người upload cùng lúc phải xếp hàng; P1 ăn CPU/RAM của chính server API | Hàng đợi bền (DB / Redis), worker riêng (GPU), giới hạn theo người dùng |

## Gợi ý cho Live URL Demo Day (deliverable #5)

Cách ít việc nhất mà không vi phạm các giả định trên:

- **Tính sẵn** phiên cho video demo ở máy local rồi deploy kèm `output/` → không có tính toán nặng trên server (#3, #10).
- **Chế độ chỉ đọc** cho người xem công khai: xem đề xuất, view, chỉ số; keep/drop/export chỉ bật khi có token (#1, #2, #8).
- Nếu cho người xem tự keep/drop: mỗi lượt truy cập một phiên riêng (#1).

## Lộ trình lên nhiều người dùng / deploy thật (#60)

Không phải viết lại: FE chỉ gọi HTTP qua `api.ts`, mọi hành động đi qua command layer, dịch vụ ngoài nằm sau BE. Việc nặng là **thay hạ tầng BE**,
làm theo từng lát, mỗi lát gỡ vài dòng ở bảng trên:

| Lát | BE | FE | Gỡ dòng |
|---|---|---|---|
| 1. Đăng nhập + quyền | Bảng user / project / vai trò (Owner · Curator · Viewer), kiểm quyền ở command layer, `Decision.actor` = user thật; đăng nhập dùng dịch vụ có sẵn (OAuth Google / Auth0 / Supabase Auth), không tự viết | Router thật (route có guard, `/projects/:id/sessions/:sid`), `useAuth` (user, vai trò), màn đăng nhập / đăng ký, màn project + thành viên kiểu Drive; `api.ts` gắn token, 401 → đăng nhập | #2, #7, #8, #11 |
| 2. Dữ liệu bền | Postgres thay SQLite (+ migration), video / cache / export lên object storage (S3 …), URL ký có hạn | Không đổi (URL vẫn từ BE) | #1, #4, #5, #6, #14 |
| 3. Tính toán tách khỏi API | Hàng đợi bền (Redis / DB) + worker riêng (GPU) cho P1 và field nặng | Không đổi (vẫn poll / sau là event) | #3, #10, #13, #15 |
| 4. Nhiều người cùng phiên | Event log (CONTRACTS §11.3), SSE / WebSocket | Cache server-state (vd TanStack Query) thay biến đếm phiên bản; nhận event cập nhật lưới | #12 |

Giữ nguyên qua các lát: `api.ts` (một chỗ gọi HTTP), thành phần `ui/`, `UI_DESIGN.md`, renderer theo `view.type`, form sinh từ schema, CONTRACTS.
Ước lượng thô: lát 1 + 2 là phần lớn công sức (cỡ 2 tuần), lát 3 + 4 mỗi lát vài ngày; demo trước đó dùng mục "Gợi ý cho Live URL" ở trên.

## Cách giữ file này luôn đúng

- Mỗi khi thêm một giả định kiểu "chỉ đúng khi chạy local" vào code → thêm một dòng vào bảng.
- Code nên đánh dấu chỗ tương ứng bằng comment `# local-only: <lý do>` để `grep "local-only"` ra hết khi chuẩn bị host.

## 16. Import dataset local (S4)

Người dùng tự đặt thư mục dưới `RAV_DATASETS_DIR` trên máy backend. Import/hash đồng bộ trong request,
giới hạn `RAV_MAX_IMPORT_IMAGES` (50.000 mặc định), không upload ZIP. Khi host: upload/object storage và job import nền.

Dataset prepare (S5) dùng cùng hàng đợi P1 trong RAM (#15), một worker cho cả video/ảnh; khởi động lại mất trạng thái job, cache hoàn tất vẫn còn.

- S8: ZIP snapshot tạo tạm (giới hạn RAV_MAX_SNAPSHOT_ZIP_MB=256), cleanup sau gửi/lỗi và quét orphan khi API khởi động.
  Chỉ chạy một API local trên một output; không dùng chung output giữa nhiều tiến trình, tránh quét file của request đang chạy.

- S9–S10: CLIP job cùng worker P1 trong tiến trình API, state RAM mất khi restart; chỉ một API/output. Kaggle dùng token .env repo, tự tạo notebook private riêng nếu không có override, lưu slug/account trong output/clip-worker.json; một session job, không fallback CPU. Không tắt API khi job GPU còn chạy; worker dọn session trong finally khi job kết thúc/lỗi. RAV_CLIP_IMAGE_BACKEND=local là cấu hình rõ dành smoke/offline; máy ít RAM nên batch=8 và model CLIP không nạp khi chỉ mở kho. Model Hugging Face dùng cache thư viện có sẵn (hoặc HF_HOME); dữ liệu/payload/result của job nằm dưới output/tmp và dọn. Chuyển host cần worker bền, quản lý model/quota/credentials.
- M1: upload checkpoint dùng lock trong một App, không hỗ trợ nhiều API cùng kho model. Verify SHA đồng bộ ngoài event loop trước chọn model; kho checkpoint quản lý lưu dưới data/models hoặc RAV_MODELS_DIR. Không unpickle/execute file upload. Upload bị crash có thể để orphan file chưa có DB; không tự nhận orphan thành model. Trạng thái container_only không phải xác nhận model YOLO hợp lệ; inference cần kiểm riêng ở lát tiếp theo.
- M2: YOLO batch chạy CPU trong HTTP threadpool, một lock/App, trả yolo_busy khi có request khác. Một API/output, tối đa 256 picks; không worker phân tán/timeout/cancel job. Tắt API có thể ngắt inference; cache chỉ xuất bản atomic khi đủ kết quả, tmp orphan không được đọc. Mỗi request nạp một checkpoint, không giữ model cache RAM. Lần chạy explicit mới deserialize model Ultralytics; custom checkpoint yêu cầu lớp/version tương thích. Cache JSON dưới output/yolo_batches, YOLO_CONFIG_DIR dưới output/ultralytics để không ghi settings ra ngoài workspace.
- V2: một API/output, proposal publication RLock và cùng prepare worker; job JSON bền trạng thái nhưng không tự resume sau crash (queued/running chuyển interrupted). CPU consistency 2×tối đa 256 candidates, lock YOLO chung M2; chưa executor YOLO Kaggle. Generic bindings/cache/stage cho phép thêm executor sau. Raw hash/verify đồng bộ trong worker, dataset lớn cần đo trước. Quota/weights là heuristic, chưa đo uplift. Review/pool/proposal đổi trong job thì phải retry, không publish theo snapshot cũ.
- CLIP checkpoint local (#73): mặc định cache dưới data/model-cache của repo, không dùng cache Hugging Face toàn máy trên C. RAV_CLIP_CACHE_DIR cho phép chọn ổ có dung lượng; đường cache là tài nguyên local, không thuộc FieldKey. Chạy từ repo root. Không tự di chuyển/xoá cache toàn máy; dọn lần này theo yêu cầu người dùng.
- Lưu trữ trên ổ repo (#74): `scripts/use_project_storage.ps1` đặt cache HF/Torch/pip/npm/CLIP/YOLO và TEMP/TMP cho terminal hiện tại cùng tiến trình con dưới data/output. Trên máy người dùng, cache toàn máy HF hub/Torch/npm/pip được chuyển có kiểm SHA-256 sang data/system-cache trên D, giữ đường cũ trên C bằng junction để app khác vẫn đọc được. Log xet của backend đang mở giữ trên C tới khi tiến trình kết thúc; tiến trình mới từ terminal đã cấu hình dùng HF_HOME trên D. Không xoá thư mục đích khi junction còn được sử dụng. Pagefile/hiberfil do Windows quản lý, không chuyển hoặc thay cấu hình trong lát này; cache trên D không ngăn Windows tăng pagefile trên C khi thiếu RAM.
- C1 (#75): s3 transport giữ job bền và phục hồi session qua lease Supabase; local job store chỉ một API/output. Core catalog/session/feedback/FieldStore và bản làm việc raw vẫn local; chưa migrate toàn DB hoặc multi-tenant/auth. API dispatcher và P1 dùng một thread/App. `data/python-packages` là dependency install target trên ổ repo, helper thêm vào PYTHONPATH; không tự cài thêm package lên C.
- C2 (#76): backend cần nguồn Python và checkpoint DINOv2-S đã chuẩn bị trong TORCH_HOME trên ổ dự án; đóng băng SHA và upload models/ chỉ khi chạy job. Deploy cần provision đúng cache này trước (không đổi checkpoint tự động), hoặc lát sau resolve manifest model đã pin trên S3. Video upload không auto prepare; DINO/CLIP S3 dùng job durable, còn dataset import/prepare local giữ giới hạn §16. CLI prepare_video đồng bộ chỉ cho storage=local; cloud phải queue job.

- DEP5c (#96): `scripts/pilot_local_stack.py` dựng stack pilot giả lập chỉ trên máy dev (Postgres portable, `moto server` S3, token HS256 ký bằng secret cố định trong script, mật khẩu role cố định). Không dùng secret/mật khẩu/endpoint này ở bất kỳ môi trường thật nào; pilot thật dùng Supabase JWKS + S3 thật + role đặt mật khẩu theo runbook §5. Tab Access Token chỉ có ở `vite dev`.

- DEP7-Basic-Stability: Ghi nguyên tử `process.json` của worker trên Windows có thể chạm `PermissionError` (sharing violation / access denied) khi có tiến trình đọc đồng thời; thực hiện retry ngắn (10 lần x 50ms) trong `_write_process_file` để tránh chết supervisor khi test/monitor đọc liên tục.

## Spec target pilot (PL4, #79; chưa gỡ giới hạn)

[DEPLOYMENT_PILOT_SPEC](../../../../design/DEPLOYMENT_PILOT_SPEC.md) là target 2–3 account cấp thủ công, private ownership, system/demo protected, API/worker tách và PII export/report. **Chưa code/deploy/benchmark**; toàn bộ giới hạn hiện có ở trên vẫn phải đóng bằng gate D0–D8/A01–A11 trước mở pilot. Tài liệu mới không chứng minh auth/RLS/media/worker/quota đã có, không biến shared SQLite/local cache thành storage nhiều tenant, không cho phép raw export bypass.
