# Deploy FE + API chung Docker — IrishCoffee T026 (#107)

**2026-10-12:** hostname hiện tại là `https://c4-app-026.onrender.com/app`;
xem [migration #118](HOSTNAME_MIGRATION.md). Nội dung URL/release #107 phía dưới là lịch sử trước migration.

URL giữ nguyên: `https://irishcoffee-t026.onrender.com`. Target `combined` trong Dockerfile
build React ở stage Node, chạy một Uvicorn/FastAPI nhẹ phục vụ FE và `/api/v2` cùng origin.
Không model/Node/Nginx ở runtime; decode, AI, thumbnail, PII, ZIP và Kaggle vẫn thuộc worker Contabo.
Worker chưa deploy và DEP6/DEP8 chưa đạt; không coi hosting Live là pilot hoàn tất.

## Build và kiểm tra

Từ repo root bằng PowerShell, Docker Desktop Linux containers:

```powershell
. .\scripts\use_project_storage.ps1
$releaseTag = git rev-parse --short=12 HEAD
docker build --platform linux/amd64 --target combined -t "tonmeu/rav26:combined-$releaseTag" .
docker run -d --name rav-combined-smoke -p 127.0.0.1:18080:8000 "tonmeu/rav26:combined-$releaseTag"
docker stats --no-stream rav-combined-smoke
Invoke-RestMethod http://127.0.0.1:18080/health
Invoke-WebRequest http://127.0.0.1:18080/login
docker stop rav-combined-smoke
docker rm rav-combined-smoke
docker push "tonmeu/rav26:combined-$releaseTag"
```

Repo Docker Hub phải private, đăng nhập bằng Docker Desktop/login; không ghi secret vào image/build args.
Container smoke không có DB thật nên readiness/business có thể 503; release cloud phải đạt readiness.
Kiểm `/assets` và không fallback HTML cho `/api/v2/missing`, `/health/missing`, file không tồn tại/traversal.
Runtime config chỉ có apiUrl, supabaseUrl, supabasePublishableKey; không xuất env backend.

## Cấu hình và rollout Render

Nếu chưa nhớ mật khẩu `rav_pilot_api`: operator mở Supabase SQL Editor, tự thay placeholder và Run:

```sql
ALTER ROLE rav_pilot_api WITH LOGIN PASSWORD 'THAY_BANG_MAT_KHAU_MOI';
```

Không chạy nguyên placeholder. Đây là mật khẩu role ứng dụng, không phải password postgres hoặc
đăng nhập Supabase. Nhập credential trực tiếp vào Render, không chia sẻ query/URL thật trong chat/log/docs.
URL pooler:

```text
postgresql://rav_pilot_api.hwikrrfnbemcoxngvgbd:PASSWORD@aws-0-ap-northeast-2.pooler.supabase.com:5432/postgres?sslmode=require
```

Chỉ một hàng `DATABASE_URL`; bỏ hàng thêm trùng và lưu bằng Save only. Thay username cũng cần đúng
password role tương ứng; không giữ password postgres rồi giả định dùng được với role khác.

1. Service giữ hostname: `srv-db559q942hec73fp6790`. Chuyển env API hiện có từ
   `srv-db54lcjbc2fs73eai7u0` trực tiếp qua UI, không in/copy vào Git/docs/image:
   `DATABASE_URL` **role rav_pilot_api**, `SUPABASE_URL`, `SUPABASE_JWKS_URL`, S3 bucket/prefix/region,
   scoped `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`; giữ `PORT=10000`.
2. Giữ hai biến public Supabase FE; đặt `VITE_API_URL` **rỗng** để dùng cùng origin.
   `RAV_FRONTEND_ORIGIN=https://irishcoffee-t026.onrender.com`; `RAV_WEB_DIST=/app/web-dist` do image đặt.
   Không dùng postgres admin, không bật worker/ML trong service này, không auto-migrate/seed policy.
3. Save env **không redeploy** nếu UI hỗ trợ; chọn image `tonmeu/rav26:combined-SHA` đã push,
   giữ registry credential `rav26-dockerhub-readonly`. **Connect trong Update Source với Existing Image
   cập nhật source và kích hoạt deploy ngay**, không phải bước kiểm trước một nút Deploy khác;
   chỉ bấm sau khi env đã đúng. Nếu source đã cập nhật nhưng deploy bị hủy, Manual Deploy → Deploy latest reference.
4. Kiểm landing/login/app redirect/deep link, runtime apiUrl rỗng, assets 200,
   `/health` 200, `/health/ready` database/schema ready; API business không HTML và auth vẫn kiểm quyền.
5. Chỉ khi đạt mới đổi API monitor `804231770` sang `https://irishcoffee-t026.onrender.com/health/ready`;
   FE monitor `804231926` giữ `/health`. UptimeRobot không cho hai monitor trùng URL/type;
   readiness kiểm thêm DB/schema. Keyword GET/5 phút; hai monitor không nhân đôi instance-hours.
6. Suspend API riêng `srv-db54lcjbc2fs73eai7u0`, không Delete. FE cũ `srv-db54miad0e5s73e6lg4g`
   đã suspended, giữ trạng thái. Sau rollout chỉ một Web Service tiêu thụ quota.

Một service luôn chạy khoảng 720–744 giờ/tháng, quota Free 750 giờ **chung workspace**;
giờ các service cũ đã dùng không hoàn lại. Không tự nâng plan, paid compute tính riêng theo service/thời gian.

## Cập nhật, rollback và bàn giao

Image đã build/push: `tonmeu/rav26:combined-b4e1feb166d2`, digest
`sha256:9519f215c97b86eabee8a5fc3afce61c79bc7a8c4b11a2dd7daecfa4a3748df0`.
Rollout cloud **Live 2026-10-11**, deploy `dep-db5kj7vavr4c7394ubig` (30,4 giây).
Operator đã đặt mật khẩu role và lưu Session pooler TLS; username `rav_pilot_api` đã kiểm qua UI,
`VITE_API_URL` rỗng đã xác minh qua public runtime config. Health/readiness 200, DB/schema ready,
API chưa auth 401 JSON, API không tồn tại 404 JSON; landing/login/app deep link và assets đạt.
API riêng đã **Suspended**; FE cũ giữ suspended. Hai monitor hiển thị Up sau cập nhật.
Chưa smoke đăng nhập tài khoản thật hoặc nghiệp vụ S3/worker; không coi rollout là admission pilot.

- Sửa source trong repo P-026, chạy test/ruff/tsc + build, commit rồi build/push tag SHA mới.
  Không cần repo FE riêng hoặc Render GitHub App; image không tự cập nhật chỉ vì push tag.
- Release FE/API đi cùng một image. Ghi tag/digest/deploy ID, env **tên biến** và bằng chứng vào
  [DEPLOYMENT_STATUS](../../../../setup/DEPLOYMENT_STATUS.md); worker release độc lập.
- Nếu bản gộp lỗi: Resume API riêng, đổi `VITE_API_URL` FE về `https://rav26-api.onrender.com`,
  chọn lại FE image `tonmeu/rav26:web-c75501972cb2`, deploy và kiểm health/CORS/routes;
  chuyển API monitor về API riêng. Hai service chạy lại sẽ cộng giờ quota.
- Rollback các bản gộp: chọn digest/tag gộp đã xác minh trước đó, không sửa schema ngược.
- Còn thiếu: worker Contabo, AWS key rotation đã báo #100, auth account thật/profile/quota,
  S3 upload/media/CORS, export/PII DEP6, cloud E2E DEP8, CI tự động và backup/restore đã thử.

## Release log latency #111 (2026-10-11)

Hiện tại Live: `tonmeu/rav26:combined-timing-20261011`, Render pin digest
`sha256:af13969836766ba3c5099238cedbcb564ff81b056ce26fb499bb226d3dcab88b`,
deploy `dep-db5pu6e7bikc73dlqvl0`, 31,6 giây. Có pool #110 và log browser/API/DB.
Build trên Contabo bằng cùng Dockerfile target combined; source archive loại env,
data/output/cache. Registry private hiện có, credential push chỉ lưu RAM host và
đã dọn sau push; Render giữ credential readonly cũ. Không đổi env/plan/URL hoặc push Git.
Smoke non-root/import không ML/routes/header đạt; health/ready live 200 DB/schema ready,
Render Logs có rav_timing. Bản combined-b4e1feb166d2 giữ rollback; worker release riêng.
[Cách thu và đọc log](../../../../setup/UPLOAD_LATENCY_LOGS.md). Không coi release này hoàn tất DEP7/DEP8.
