# Release FE/API bằng Docker image — DEP7-Docker (#100)

Phương án người dùng chốt #107: FE/API chung image; hướng dẫn mới ở
[COMBINED_WEB_RELEASE](../../../../setup/COMBINED_WEB_RELEASE.md). Bên dưới giữ luồng hai image để rollback;
đối chiếu DEPLOYMENT_STATUS trước khi suspend hoặc đổi image/service.

FE và API là hai Render Web Service từ image build sẵn; worker vẫn Contabo và chưa bật trong lát này.
Không cần Render đọc repo GitHub. Registry được người dùng chọn: **Docker Hub private**.
FE/API đã deploy Live trên Render, DB/schema readiness và CORS đạt (#100–#104); worker/cloud nghiệp vụ chưa được kiểm chứng.
Đọc [trạng thái deployment và phần còn thiếu](../../../../setup/DEPLOYMENT_STATUS.md) trước khi cập nhật host.

## 1. Điều kiện trước khi build/publish

- Docker Desktop dùng Linux containers, engine đang chạy. AGENTS.md mặc định yêu cầu cache ở D; người dùng đã cho phép dùng storage Docker hiện tại trên máy này (#100), không cần đổi disk để build.
- Đăng nhập Docker Hub trên máy bằng `docker login`. Không gửi password/token vào chat hoặc ghi vào Git.
- Tạo một repository **private** `rav26` (hai image dùng tag `api-SHA` và `web-SHA`) trong tài khoản Docker Hub của bạn trước khi push;
  không push tên chưa tạo để tránh registry tự tạo repository public theo mặc định.
- Chọn tag của commit đã kiểm thử; namespace bên dưới là username Docker Hub, không phải GitHub org.
- Render nhận credential Docker Hub chỉ đọc để pull. Cấp credential ở dashboard, không nhúng vào image/URL.

## 2. Build, kiểm container và push

PowerShell từ repo root (chưa có secret trong build args/context):

```powershell
. .\scripts\use_project_storage.ps1
$depNamespace = 'YOUR-DOCKERHUB-USERNAME'
$depTag = git rev-parse --short=12 HEAD
docker build --platform linux/amd64 --target api -t "${depNamespace}/rav26:api-${depTag}" .
docker build --platform linux/amd64 -f src/web/Dockerfile -t "${depNamespace}/rav26:web-${depTag}" .
```

API chỉ dùng `requirements-api.txt`, không model/PyTorch. FE image build chế độ pilot, chạy Nginx dưới user nginx,
nhận cấu hình public từ environment lúc startup; secret API không đưa vào FE. Images dùng port `$PORT` (API mặc định 8000, web 8080).
Image base tag được resolve khi build; ghi lại digest image release sau push, không coi tag base là pin bất biến.

Smoke tối thiểu trước push (API liveness không cần DB; readiness không cấu hình phải trả 503):

```powershell
docker run -d --name rav-api-smoke -e PORT=18000 -p 127.0.0.1:18000:18000 "${depNamespace}/rav26:api-${depTag}"
docker run -d --name rav-web-smoke -e PORT=18080 -e VITE_API_URL=http://localhost:18000 -p 127.0.0.1:18080:18080 "${depNamespace}/rav26:web-${depTag}"
Invoke-RestMethod http://127.0.0.1:18000/health
Invoke-RestMethod http://127.0.0.1:18080/health
Invoke-WebRequest http://127.0.0.1:18080/runtime-config.js
Invoke-WebRequest http://127.0.0.1:18080/login
Invoke-WebRequest http://127.0.0.1:18080/app
docker exec rav-api-smoke python -c "import sys; import src.rav.api.pilot; assert not {'torch','numpy','av','ultralytics'} & set(sys.modules)"
docker logs rav-api-smoke
docker logs rav-web-smoke
docker rm -f rav-api-smoke rav-web-smoke
```

Kiểm HTML SPA route, config public, image health và user non-root; API readiness 503 khi thiếu DB/schema không phải app đã sẵn sàng nghiệp vụ.
Không chạy migration hoặc nạp `.env` thật trong smoke liveness. Đóng container smoke sau kiểm.

Sau khi xác minh repository là private và smoke đạt:

```powershell
docker push "${depNamespace}/rav26:api-${depTag}"
docker push "${depNamespace}/rav26:web-${depTag}"
docker inspect --format '{{json .RepoDigests}}' "${depNamespace}/rav26:api-${depTag}"
docker inspect --format '{{json .RepoDigests}}' "${depNamespace}/rav26:web-${depTag}"
```

## 3. Tạo Render service

New → Web Service → Existing Image. Dùng image URL dạng `docker.io/USERNAME/rav26@sha256:API-DIGEST`
và `docker.io/USERNAME/rav26@sha256:WEB-DIGEST`. Registry credential chỉ đọc, cùng region; chọn plan free explicit nếu phù hợp tài khoản,
không tự nâng lên paid khi plan free không khả dụng. Không dùng Blueprint chọn repo vì vẫn cần quyền GitHub.

- API: nhập biến từ [api.env.example](../../../../../deploy/api.env.example); URL DB role `rav_pilot_api`, không admin/service role;
  health `/health`, `/health/ready` phải kiểm DB/schema riêng. Operator chạy migration/seed ở lát phù hợp, không API boot tự làm.
- Web: nhập biến public từ [web.env.example](../../../../../deploy/web.env.example), URL API HTTPS và Supabase đúng project;
  health `/health`. `VITE_RAV_MODE=pilot` đã build, không cần đặt runtime.
- Sau khi biết URL web, cập nhật API `RAV_FRONTEND_ORIGIN` đúng origin đó (nhiều origin phân cách dấu phẩy).
  Không wildcard. Supabase allowlist redirect và S3 CORS phải theo đúng origin/project đã xác minh.
- `/runtime-config.js` là public, cache no-store; chỉ ba trường URL API, URL Supabase, publishable key.
  Đổi environment/restart không cần build lại FE.

## 4. Cập nhật và rollback

Release FE hiện tại (#104): `tonmeu/rav26:web-c75501972cb2`, digest
`sha256:c0d8169f19a495787d7462be51f71d321b133185b9de53bc365998d9daa68e50`.
Render service `irishcoffee-t026` (`srv-db559q942hec73fp6790`), URL nộp nhóm `https://irishcoffee-t026.onrender.com`:
- `/` (alias `/landing`): trang giới thiệu public.
- `/login`: form đăng nhập; tài khoản vẫn do operator cấp.
- `/app`: yêu cầu đăng nhập, nghiệp vụ qua hash như `/app#templates`, `/app#videos`, `/app#s=ID&t=analysis`.
- `/IrishCoffee-T026`: alias vào `/app`, giữ hash.

API giữ image release #100; `RAV_FRONTEND_ORIGIN=https://irishcoffee-t026.onrender.com,https://rav26-web.onrender.com`.
Service FE cũ `srv-db54miad0e5s73e6lg4g` (`rav26-web.onrender.com`, tên hiển thị `irishcoffee-t026-web`) đã suspend 2026-10-11 để ngừng dùng quota (#106); giữ image/config, có thể Resume, không redirect URL cũ. S3 CORS cần kiểm origin mới trước cloud upload smoke;
credential hiện có trả AccessDenied khi đọc GetBucketCors, chưa đổi CORS bucket hoặc quyền IAM.

Code mới → kiểm test/build → commit → build/push tag mới → ghi digest → đổi image reference trên Render và deploy explicit.
Giữ digest trước để rollback; không xóa release đang dùng. Render không tự deploy khi tag registry đổi.
Chưa nối CI tự động/hook trong lát này vì Actions org đang bị chặn billing; không đưa deploy-hook URL vào Git/log.

FE/API có URL live không chứng minh worker, media, AI, export PII hoặc admission đã sẵn sàng. DEP6/DEP8 và smoke cloud riêng còn bắt buộc.

Nguồn: [Render prebuilt images](https://render.com/docs/deploying-an-image),
[Docker Desktop WSL storage](https://docs.docker.com/desktop/features/wsl/).
