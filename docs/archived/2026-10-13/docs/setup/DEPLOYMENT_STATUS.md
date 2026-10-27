# Deployment c4-app-026 · T026 — trạng thái và bàn giao

Cập nhật release ảnh lưu sẵn **2026-10-13**, quyết định #123. Bản hiện tại tại **https://c4-app-026.onrender.com/app**,
service `srv-db68u7d9fdbs73f8v890`, deploy `dep-db6vffom7kps73bh8an0` Live;
image pin `sha256:4d2fd220a1cc4557f9ade7ceec63c912c0ca8c7c5fbc910db0b5193c5d8c23a4`, worker mới healthy.
Migration 010/policy mới đã áp. [Release, số đo và giới hạn kiểm thử](../../../../setup/FRAMES_CLOUD_QA.md): Supabase/S3 thật đạt luồng cơ bản,
nhưng upload/clip/publish còn chậm; chưa kiểm browser login/GPU/backfill cloud hoặc mở pilot đầy đủ.
[Runbook hostname](HOSTNAME_MIGRATION.md). Bằng chứng #100–#118 phía dưới là lịch sử, không thay trạng thái mới #123.
Đây là trạng thái đã kiểm chứng và hướng dẫn vận hành;
thiết kế vẫn theo [CONTRACTS §21](../../../../design/CONTRACTS.md), [architecture](../../../../architecture_diagram.md)
và [UI_DESIGN](../../../../design/UI_DESIGN.md). Hosting Live chưa đồng nghĩa mở pilot đầy đủ.

## 1. Hạ tầng hiện tại

FE/API chạy **một Docker Web Service**, hiện tại `https://c4-app-026.onrender.com` (#118).
Lịch sử trước đổi hostname: deploy `dep-db5qg3flot8c73frqcl0` **Live 2026-10-11** (20,6 giây), image `tonmeu/rav26:combined-delete-20261011`,
digest pin `sha256:0c547501c7758c28e86614963e59c9d997e9054034e6cc6720f279f585139a66` (#112). API đã có pool #110, log latency và retry source delete.
[Runbook gộp và rollback](../../../../setup/COMBINED_WEB_RELEASE.md). Operator đã lưu URL Session pooler TLS của role
`rav_pilot_api`, không dùng postgres admin trong service gộp; frontend dùng cùng origin (`VITE_API_URL` rỗng).
Health/readiness 200 DB/schema ready; landing/login, app redirect/deep link và assets đạt,
API chưa auth 401 JSON, route API không tồn tại 404 JSON. Sau suspend API riêng, readiness và assets vẫn 200.
API riêng và FE cũ **Suspended**; chỉ service gộp tiếp tục tính instance-hours trong nhóm ba service này.
Hai monitor hiển thị Up: FE liveness `/health`, API DB/schema `/health/ready`.
Smoke image mới non-root/import không ML/routes/header log đạt. Chưa benchmark capacity Render.
Deploy trước `dep-db5kcdd9fdbs73d6c11g` đã Canceled, giữ trong lịch sử; không phải release hiện tại.
Login account thật, upload ColdBrew và thumbnail đã smoke; frame/context, auth expiry/A-B và các gate pilot khác chưa đạt.

| Thành phần | Địa chỉ / định danh | Trạng thái đã xác minh |
|---|---|---|
| FE + API chính | [c4-app-026.onrender.com](https://c4-app-026.onrender.com/app), `srv-db68u7d9fdbs73f8v890` | Docker Web Service, Free, Singapore; deploy Live #118 |
| FE + API IrishCoffee cũ | `irishcoffee-t026.onrender.com`, `srv-db559q942hec73fp6790` | **Suspended 2026-10-12**; giữ image/env để rollback |
| API riêng cũ | `rav26-api.onrender.com`, `srv-db54lcjbc2fs73eai7u0` | **Suspended 2026-10-11** sau khi bản gộp đạt; giữ cấu hình để rollback |
| FE cũ | `rav26-web.onrender.com`, `srv-db54miad0e5s73e6lg4g`, tên hiển thị `irishcoffee-t026-web` | **Suspended 2026-10-11** theo yêu cầu để ngừng dùng quota; giữ cấu hình/image, có thể Resume |
| Render project | `T026-Rav026`, `prj-db4to7p42hec73evn4a0`, environment Production | Các service FE/API trong cùng project |
| Image registry | Docker Hub private `tonmeu/rav26` | Đã publish FE/API; Render pull bằng credential `rav26-dockerhub-readonly` |
| PostgreSQL/Auth | Supabase project `hwikrrfnbemcoxngvgbd` | API dùng role `rav_pilot_api` qua Session pooler; account thật đã login, expiry/A-B chưa kiểm |
| Object storage | S3 `aiia-irish-coffee`, region `us-east-1`, prefix `pilot-rav26` | Upload/source READY + thumbnail đạt; CORS/IAM scope đã sửa #109 |
| Worker | VPS Contabo của người dùng | CPU/MEDIA + DELETE_SOURCE, digest `d8dfc32b16ef…`, healthy; cloud delete synthetic SUCCEEDED, slots FREE; frame/context còn chờ kiểm |
| GPU | Kaggle qua worker theo CONTRACTS §21.14 | Chưa kiểm provider/quota/GPU cloud thật; chưa bật worker |

URL nộp ban tổ chức: **https://c4-app-026.onrender.com/app**, theo yêu cầu cú pháp c4-app-026 (#118).

| Route FE chính | Hành vi |
|---|---|
| `/`, `/landing` | Landing public giới thiệu nhóm/RAV-26 |
| `/login` | Email/password Supabase; không public signup hoặc anonymous account |
| `/app` | App pilot; chưa có session hợp lệ → `/login?next=...` |
| `/app#videos`, `/app#templates`, `/app#new` | Kho dữ liệu, Phiên mẫu, Phiên mới theo hash hiện có |
| `/app#s=ID&t=analysis` | Deep link phiên/tab, giữ qua đăng nhập; API vẫn kiểm owner |
| `/IrishCoffee-T026` | Alias path cũ trên host mới → `/app`, giữ hash |

`next` chỉ chấp nhận đường `/app` cùng origin. Logout/hết hạn tải lại trang về login để xóa state trong bộ nhớ.
Không có tài khoản mẫu/mật khẩu trong tài liệu; operator cấp account theo runbook.

## 2. Image và bằng chứng release

| Image | Tag | Digest |
|---|---|---|
| FE/API gộp #105 (rollback) | `tonmeu/rav26:combined-b4e1feb166d2` | `sha256:9519f215c97b86eabee8a5fc3afce61c79bc7a8c4b11a2dd7daecfa4a3748df0` |
| API riêng rollback | `tonmeu/rav26:api-3ddcb54f7d48` | `sha256:b367d1acd74083538a4a6727ab2050c2821835b153d75e99ffb15e0d865eebb1` |
| FE riêng rollback | `tonmeu/rav26:web-c75501972cb2` | `sha256:c0d8169f19a495787d7462be51f71d321b133185b9de53bc365998d9daa68e50` |
| FE cũ | `tonmeu/rav26:web-3ddcb54f7d48` | `sha256:0ebf2ad254176d7307848fcb7a2e323e528a82a4d21280532b7eea29f2af5af0` |

- Release gộp Live `dep-db5kj7vavr4c7394ubig`; FE/API riêng dưới đây là bằng chứng lịch sử trước gộp.
- Bằng chứng hiện tại: `output/docker-release/combined-render-live.png`, `combined-api-suspended.png`, `combined-monitors-up.png`.
- Bản gộp: pytest chọn lọc 7 pass/10 skip (test PostgreSQL thật chưa cấu hình), ruff, FE tsc/19 test/build pilot đạt khi build release; container healthy, user appuser, không ML nặng.
- FE riêng lịch sử: TypeScript, vitest **19/19**, build local/pilot và build Linux Docker đạt; container healthy, user nginx.
- API image nhẹ, user appuser; kiểm bootstrap/CORS **11/11**, không import ML nặng trong smoke release #100.
- Browser live: landing/login, app chưa auth chuyển login, alias/deep link giữ hash; mobile 390px không tràn ngang.
- API CORS preflight 200 cho cả origin FE mới/cũ. `/health/ready` trả `database=ready`, `schema=ready`.
- Toàn suite lần tích hợp DEP5F: **385 đạt, 3 bỏ qua**, Postgres thật/S3 moto; đây là bằng chứng local, không thay cloud smoke.
- Ảnh kiểm chứng trên máy operator: `output/docker-release/irishcoffee-landing-live.png`, `irishcoffee-login-live.png`.

Không chạy lại toàn suite chỉ để viết tài liệu. Các số trên là kết quả tại các lần release đã ghi trong decision log.

## 3. Cấu hình theo host

Không copy nguyên `.env` sang FE hoặc image. Bảng dưới chỉ liệt kê tên biến và giá trị không bí mật.

| Host | Biến | Giá trị / cách cấp |
|---|---|---|
| Service gộp | `PORT` | `10000` |
| Service gộp | `VITE_API_URL` | Rỗng, API cùng origin |
| Service gộp | `VITE_SUPABASE_URL` | `https://hwikrrfnbemcoxngvgbd.supabase.co` |
| Service gộp | `VITE_SUPABASE_PUBLISHABLE_KEY` | Publishable key của đúng project, được phép public |
| Service gộp | `RAV_FRONTEND_ORIGIN` | `https://c4-app-026.onrender.com` |
| Service gộp | `DATABASE_URL` | Session pooler TLS, role `rav_pilot_api`, không postgres admin |
| Service gộp | `SUPABASE_URL`, `SUPABASE_JWKS_URL` | Project/JWKS hiện có; xác thực token theo contract |
| Service gộp | `RAV_S3_BUCKET`, `RAV_PILOT_S3_PREFIX` | `aiia-irish-coffee`, `pilot-rav26` |
| Service gộp | Region AWS/S3 + AWS credential | Inject backend; không public, không build arg |
| Worker Contabo CPU/MEDIA | `RAV_PILOT_WORKER_DATABASE_URL` | Role riêng `rav_pilot_worker`, đã probe; không dùng `DATABASE_URL` API |
| Worker Contabo CPU/MEDIA | S3 bucket/prefix/region + scoped AWS credential | Cùng namespace pilot; smoke job/media chưa hoàn tất |
| Worker Contabo CPU/MEDIA | `RAV_PILOT_WORKDIR` | `output/pilot-worker`, mount writable UID 1000 |
| Worker GPU (chưa deploy) | `KAGGLE_API_TOKEN` | Chỉ worker; không có token → không claim GPU |

Mẫu URL API (placeholder, không phải URL dùng nguyên văn):

```text
postgresql://rav_pilot_api.hwikrrfnbemcoxngvgbd:PASSWORD@aws-0-ap-northeast-2.pooler.supabase.com:5432/postgres?sslmode=require
```

`PASSWORD` là mật khẩu riêng operator đặt cho role `rav_pilot_api`; mật khẩu postgres admin dành cho migration là mật khẩu khác.
Encode ký tự đặc biệt trong password khi đưa vào URL. Không gửi URL có mật khẩu vào chat/log/Git.
`/runtime-config.js` của FE chỉ xuất ba trường public, cache `no-store`; đổi chúng cần restart/deploy, không cần rebuild FE.

## 4. Luồng cập nhật hiện đang dùng

**Code → kiểm tra → commit local → build image → push Docker Hub private → đổi image trên Render → deploy → smoke.**
Git push và Docker push là hai thao tác riêng. Theo AGENTS, chỉ push Git khi người dùng yêu cầu.
Chi tiết lệnh build/smoke mới: [COMBINED_WEB_RELEASE](../../../../setup/COMBINED_WEB_RELEASE.md);
[DOCKER_IMAGE_RELEASE](DOCKER_IMAGE_RELEASE.md) giữ luồng hai image cho rollback.

1. Xác định thay FE, API hay worker; ghi lại digest/env release đang chạy để có điểm rollback.
2. Windows: source `scripts/use_project_storage.ps1` trước Python/npm/test. FE chạy tsc, vitest và build;
   API chạy ruff/test phù hợp, test RLS/transaction cần Postgres thật.
3. Commit đã kiểm tra, lấy SHA làm tag. Build `linux/amd64`, không secret trong context; không ghi đè tag release cũ.
4. Kiểm container/health/public config; xác minh repo registry còn private rồi push đúng image cần đổi.
5. Render service đúng ID → Settings/image reference → chọn tag/digest mới và deploy explicit, giữ credential read-only.
   Chỉ push tag lên registry chưa khiến service đang chạy cập nhật.
6. Chờ Live, kiểm API liveness + readiness + CORS, FE landing/login/deep link. Kiểm auth/upload/media riêng khi có account/worker.
7. Ghi tag/digest/deploy ID/env names và kết quả vào decision log; không ghi secret.

### CI/CD còn thiếu

`.github/workflows/ci.yml` đã có Python/ruff/pytest với Postgres 16 và FE tsc/vitest/build local/pilot.
Lần kiểm Actions trước bị chặn billing/spending limit của org, job chưa chạy; chưa xác minh đã được gỡ chặn.
Render GitHub App của org trước đây chờ admin duyệt. Luồng image hiện tại không phụ thuộc kết nối repo đó.
`render.yaml` là **Static Site fallback cũ**, không quản lý Docker service gộp đang chạy hoặc hai service suspended.

Chưa có workflow build/push registry, deploy hook, gate health tự động hoặc rollout worker Contabo.
Sau khi gỡ blocker, bổ sung pipeline cùng luồng kiểm tra phía trên; secrets đặt trong store của CI, không commit.
Chưa chọn runner thay thế, fork/mirror, registry public hoặc nâng gói trả phí.

## 5. Database, migration và khôi phục

Cloud đã chạy tới migration **007**; người dùng trực tiếp chạy 005/006/007 thành công, API kiểm checksum/schema ready.
Migration 008/export chưa có trong release này. Không sửa file SQL đã áp; tạo migration mới nếu cần đổi schema.
API/worker không tự migrate khi boot và không fallback SQLite.

Chạy migration trên máy operator, từ repo root bằng **PowerShell**, từng lệnh theo prompt:

```powershell
. .\scripts\use_project_storage.ps1
$dbInput = Read-Host 'Dan URL Session pooler role co quyen DDL, kem mat khau that' -AsSecureString
$env:RAV_PILOT_MIGRATE_DATABASE_URL = [System.Net.NetworkCredential]::new('', $dbInput).Password
try {
    python scripts/pilot_migrate.py
    if ($LASTEXITCODE -ne 0) { throw 'Migration that bai; dung deploy va kiem tra loi.' }
} finally {
    Remove-Item Env:RAV_PILOT_MIGRATE_DATABASE_URL -ErrorAction SilentlyContinue
    Remove-Variable dbInput -ErrorAction SilentlyContinue
}
```

Đến bước Read-Host mới dán URL thật; không dán nó thành command. Role DDL chỉ dùng trong phiên operator,
không đưa lên Render API. Profile/policy/CPU-MEDIA-GPU slots và import demo/model phải theo runbook/contract,
chưa có bằng chứng chúng đã seed đầy đủ trên cloud.

**Backup/restore chưa hoàn tất:** chưa chốt lịch, retention, nơi lưu, RPO/RTO hoặc chạy diễn tập restore DB/S3.
Trước migration mới: chuẩn bị backup có thể restore và lưu inventory/schema version/digests tương ứng.
Restore thử trên DB/bucket test riêng rồi đo thời gian; không ghi đè production để thử.
Rollback image không hạ schema. Nếu schema mới không tương thích image cũ, cần phương án forward-fix/restore đã duyệt;
không tự drop bảng hoặc xóa lịch sử migration.

## 6. Monitoring và quota

- API monitor [804231770](https://dashboard.uptimerobot.com/monitors/804231770):
  `https://irishcoffee-t026.onrender.com/health/ready`, Keyword GET, incident khi thiếu `"status":"ok"`, 5 phút, timeout 60 giây, email chủ tài khoản.
- FE monitor [804231926](https://dashboard.uptimerobot.com/monitors/804231926):
  `https://irishcoffee-t026.onrender.com/health`, cùng GET/keyword/5 phút/timeout 60 giây/email theo yêu cầu #105.
  Dashboard đã xác minh **cả hai monitor Up**; ảnh `output/docker-release/combined-monitors-up.png`. Không monitor FE cũ.
- `/health` là liveness nhẹ, không truy vấn DB/nạp model. Monitor Up không chứng minh DB, auth, worker hoặc AI sẵn sàng.
- `/health/ready` được API monitor dùng kiểm DB/schema; UptimeRobot từ chối hai monitor trùng URL/type. Chưa có heartbeat worker.
- API HEAD `/health` trả 405: dùng Keyword GET để tránh false alarm của HTTP monitor mặc định HEAD.
- Không cài cron ping Contabo trong luồng hiện tại. Không sửa monitor unrelated có sẵn trong tài khoản.

Render Free ngủ sau 15 phút không nhận traffic và có 750 instance-hours/workspace/tháng.
Service gộp luôn chạy cần khoảng **720–744 giờ/tháng**, chưa cộng service khác. Hai service cũ đã suspended; giờ đã dùng trước đó không hoàn lại.
Người dùng yêu cầu ping cả FE/API, nhưng ping không tăng quota, không loại bỏ restart hoặc đảm bảo uptime khi bị suspend.
Chưa đọc số giờ còn lại/bandwidth thực tế từ Billing; không đưa ra con số quota còn lại phỏng đoán.
Theo dõi Billing/Monthly Included Usage trước buổi demo; nếu thiếu cần người dùng chọn thay đổi hosting/compute plan,
không tự nâng gói hoặc mua thêm. [Quy định Render Free](https://render.com/docs/free).

## 7. Danh sách chưa làm, theo thứ tự tiếp tục

| Mức / hạng mục | Trạng thái/blocker | Điều kiện để coi là xong |
|---|---|---|
| P0 — AWS credential | Operator xác nhận đã cập nhật key mới trên Render và .env, thu hồi key cũ; app principal không có quyền IAM để xác minh độc lập | Giữ secret ngoài log; phân biệt xác nhận operator với kiểm chứng IAM độc lập |
| P0 — Auth/profile/quota cloud | Account thật đã vào app, thấy username/quota; chưa kiểm logout/expiry hoặc owner A/B | Logout/expiry và isolation hai account đạt |
| P0 — S3 CORS/IAM | CORS origin public POST/GET/HEAD đã lưu; IAM pilot namespace đã duyệt/lưu; probe 23.660 byte POST 204, HEAD/GET khớp, đã dọn probe | Upload thật qua app → source READY → media đạt; probe trực tiếp không thay E2E |
| P0 — Contabo worker | CPU/MEDIA đã rollout pool, role worker riêng; ColdBrew READY và thumbnail, bonus test đã cấp; hai slots FREE sau recreate | Frame/context cloud smoke; API Render pool + log đã release #111 |
| P1 — Import demo/feature/model | YOLO11n COCO SYSTEM đã apply/inference verified, Studio cloud 16 ảnh/6 đối tượng đạt (#114); mẫu SYSTEM thiếu SQLite/video gốc #90 | Mẫu đúng allowlist/provenance; tốc độ và media còn kiểm riêng |
| P1 — BDD/checkpoint train | Ba recipe cần model có training provenance vẫn unavailable (#97) | Dataset/checkpoint đúng nguồn và gate provenance; không lấy YOLO COCO giả là model train BDD |
| P1 — Kaggle/AI | DINO và CLIP 0909 thật SUCCEEDED, stop theo session ID/FINAL/cleanup verified (#113–#114) | Admission/recovery nhiều account và toàn gate DEP7/DEP8 còn riêng |
| P1 — CI/CD | Actions billing và GitHub App blocker lịch sử; chưa pipeline registry/deploy | CI xanh thực, image immutable, deploy health gate, rollback/worker update đã thử |
| P1 — Backup/restore | Chưa lịch/retention hoặc drill | Backup DB/S3 test restore, RPO/RTO và hướng dẫn rollback có bằng chứng |
| P1 — Quota/usage/cost | Chưa kiểm remaining hours/bandwidth/registry storage/Contabo usage thực | Đo dashboard, đặt ngưỡng/cách xử lý trước khi hết quota; không giả uptime 24/7 |
| P1 — DEP6 export/PII | Chưa triển khai hết contract/gate quality | DEP6, confirmation/READY bundle và quality tests đúng contract, không raw bypass |
| P1 — DEP8 cloud end-to-end | Chỉ local smoke/mock và hosting probes | 2–3 account, isolation, upload/prepare/propose/media/analysis/export theo A01–A11 |
| Đã xử lý — FE cũ | Suspended 2026-10-11 (#106), không monitor | Giữ cấu hình để Resume nếu cần; chưa xóa service, không redirect URL cũ |

Đã chuẩn bị `deploy/worker.env.example` và `deploy/compose.worker.yml`; quy trình bàn giao ở
[CONTABO_WORKER_RELEASE](../../../../setup/CONTABO_WORKER_RELEASE.md). Chưa coi worker đã deploy khi chỉ chuyển file cấu hình.
Sau phiên dừng theo yêu cầu, đã tiếp tục build và chạy worker CPU/MEDIA trên Contabo, sau đó rollout pool:
manifest hiện tại `sha256:ae54f3b033e116e25d788d93a5dcb5f8df6545aa03bf072ea2928e1895900ee9` (#113, GPU session ID),
Compose pin digest, memory 4g, grace 180s, role DB `rav_pilot_worker`, container healthy.
ColdBrew cũ quá hạn queue trong lúc worker dừng → FAILED; supervisor dọn staging đúng policy.
Upload mới ban đầu lỗi kết nối ở complete; thử lại qua tài khoản thật → job SUCCEEDED,
ColdBrew READY (79.635.620 byte, 29,767 giây, 120 unit), thumbnail đã hiển thị.
Base CPU smoke 10 giây thiếu cho media; supervisor giữ slot chờ tất toán, sau bonus đã trả FREE đúng policy.
Người dùng cấp 3 giờ CPU bonus cho hai account test hiện có (`duyla25clc`, `vuthien3002`),
adjustment_key idempotent; #113 cấp thêm 3 giờ GPU mỗi account test, giữ ngân sách Kaggle toàn cục.
Kaggle token chỉ worker, slot gpu-1/account meowluvmatcha; DINO 0909 T4 thật 539/539, artifact READY,
GPU 121,795 s, STOPPED/FINAL/cleanup_done, slot FREE. CLIP thật job `96f8fdaa-3154-49ee-961b-20881e2c4705`
SUCCEEDED, STOPPED/FINAL/cleanup_done/session_stop_verified (#114); rollback `output/release-pre-gpu.env`.
YOLO11n COCO SYSTEM `28e3a1b2-50f4-5933-9a2b-324ac9d5157d` đã import qua CLI SHA/inference verified.
Auth UI account thật tạo phiên 0909 → PROPOSE → YOLO_BATCH SUCCEEDED: 16 ảnh/6 đối tượng, evidence
đúng ordered anchors/model SHA, quyết định giữ/bỏ và proposal không đổi; reload đọc lại kết quả, không chạy lại YOLO.
Lần đầu 167,254 s enqueue→SUCCEEDED / CPU 250.046 ms: chưa coi tốc độ đạt; cần timing decoder/load/inference.
Một số thumbnail chưa tải được; đọc evidence sau reload gặp lỗi kết nối PostgreSQL một lần, chọn lại model thì đọc được.
Frame/context và độ ổn định API cloud E2E còn thiếu. Mẫu SYSTEM #90 thiếu SQLite/video gốc,
không thay bằng fixture dry-run cũ. Chi tiết/bằng chứng và checkpoint ở decision #114.
Pool DB (#110) đã rollout cả worker và API Render (#111); log stdout/Console theo [UPLOAD_LATENCY_LOGS](../../../../setup/UPLOAD_LATENCY_LOGS.md).
Probe transaction lặp lại từ Contabo: 2,322–2,338 → 0,780–0,835 giây; chưa đo lại toàn upload.
Local pytest mới **405 pass/3 skip**, PostgreSQL 16 thật; riêng reuse/RLS/rollback/queue/quota/store **61 pass**;
ruff/diff-check đạt. Đã recreate worker healthy, giữ nguồn/media/receipt và slots FREE;
reload app vẫn tải thumbnail 320×185;
frame/context cloud smoke còn thiếu, không tick DEP7-Docker hoặc push Git.
Các lệnh worker có ở [PILOT_RUNTIME_RUNBOOK §8](../../../../setup/PILOT_RUNTIME_RUNBOOK.md#8-entry-worker-chạy-được-dep4f);
không chạy chúng để mở admission khi policy/slot/gate cloud chưa đủ.

## 8. Tra lỗi và bàn giao nhanh

DEP6a (#112) đã release: DELETE_SOURCE xóa raw/artifact theo owner, DELETE + HEAD, publish tombstone/quota/receipt nguyên tử.
Key mới retry DELETE_PENDING chỉ khi job cũ terminal và usage/cleanup/slot/hold/pin đã đối soát; key cũ replay receipt.
410 backend đạt/3 bỏ qua trên PostgreSQL thật + moto, 20 FE đạt, ruff/tsc/build đạt. Smoke cloud qua services operator
(không phải auth HTTP E2E) tạo video tổng hợp 1 giây/1.556 byte trên account test rồi xóa đúng nguồn mới:
job `bc01fdb8-9095-46c3-9e2b-8fc29dd96324` SUCCEEDED, S3 HEAD absent, source DELETED, receipt released_bytes=1556,
usage FINAL/cleanup_done=true; CPU/MEDIA FREE. Hai video có sẵn của người dùng không bị xóa trong smoke.
ColdBrew cũ đang DELETE_PENDING vì job trước handler_unavailable; bản FE mới có nút Thử lại xóa và poll receipt.
Evidence: `output/contabo-release/delete-cloud-evidence.json`, `delete-cloud-smoke.log`, `delete-render-live.png`.
Rollback giữ digest #111 và VPS `output/release-pre-delete.env`; registry credential tạm trong RAM đã dọn. Export/PII/GPU/DEP8/backup/CI vẫn chưa đạt.

| Triệu chứng | Kiểm tiếp |
|---|---|
| FE loading lâu lần đầu | Free cold start hoặc quota; mở Render deploy/log và usage |
| API `dependency_unavailable` | Environment đúng service API, DB URL TLS/role/pooler, network; không fallback SQLite |
| API `schema_not_ready` | Version/checksum cloud so với image; operator migration, không auto-migrate |
| Login thất bại | Supabase URL/public key, account được cấp, profile/policy và `/api/v2/me`; chưa kết luận do password |
| CORS API lỗi | Origin chính xác không có path, env đã deploy; preflight cho hostname mới/cũ |
| Upload báo CORS | CORS bucket là lớp riêng với API CORS; scoped IAM/presigned POST và origin mới |
| Job đứng QUEUED | Worker chưa chạy/slot-policy chưa đủ; monitor API xanh không bảo đảm worker |
| GPU WAITING_RESOURCE | Snapshot budget/slot/token; không giả GPU đã sẵn sàng |
| Monitor báo DOWN | HTTP/keyword, timeout/cold start/quota; không đổi keyword để che lỗi thật |

Khi chỉnh env Render đang che giá trị, mở **riêng biến cần sửa**, nhập rồi đối chiếu trước Save and deploy;
không xuất toàn form chứa credential ra log. Chờ Live và smoke lại, không coi nút Save là đã thay runtime.

Tài liệu liên quan: [Build/release Docker](DOCKER_IMAGE_RELEASE.md), [Runbook pilot](../../../../setup/PILOT_RUNTIME_RUNBOOK.md),
[Hướng dẫn web pilot](PILOT_WEB_V2_GUIDE.md), [PLAN](../../../../../PLAN.md),
[Decision log](../../../../research-pack/06_project_state/01_confirmed_decisions.md).
Nguồn nhà cung cấp: [Render prebuilt image](https://render.com/docs/deploying-an-image).

## DEP7-Latency 2026-10-12 (#116)

Admin duyla25clc giữ quyền Vận hành. Đã sửa trạng thái chờ/lỗi viewer và tối ưu nhóm ghi bằng
Psycopg pipeline trong transaction cũ; 434 regression backend PostgreSQL thật/24 frontend đạt.
Upload tổng hợp READY 32,340 s, clip mới chờ 41,533 s: có cải thiện nhưng chưa đủ mượt,
không claim tốc độ video 80 MB. Digest, rollback và giới hạn đo xem [Latency QA](../../../../setup/LATENCY_QA.md).
Đây là lát con DEP7; export/PII, CI, DR toàn hệ thống và gate pilot tổng còn riêng.
