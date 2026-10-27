# Dev và CI cho người/agent chỉ có repo

Theo CONTRACTS §21.11/18 và architecture §9: dùng PostgreSQL test riêng, S3 giả trong fixture;
build/smoke image kiểm packaging, không thay acceptance cloud. Không cần web production, tài khoản Supabase thật,
S3 thật hoặc credential deploy để kiểm regression. Python 3.12, Node 22.22.0, Docker Linux nếu kiểm image.

## Sau clone

Đọc AGENTS → PLAN → guide → contract của lát. Cài hook theo README. Cài `requirements.txt` và `npm ci`
trong `src/web`; trên Windows source `scripts/use_project_storage.ps1` trước Python/npm.

Dựng PostgreSQL test bằng Docker (chọn port trống, không trỏ vào production):

```sh
docker run --rm -d --name rav26-dev-postgres -p 127.0.0.1:25432:5432 -e POSTGRES_PASSWORD=rav-dev-only postgres:16
docker exec rav26-dev-postgres pg_isready -U postgres
```

Chờ `pg_isready` báo accepting connections. Đặt URL rồi chạy **một lệnh kiểm chung với CI**:

```powershell
. .\scripts\use_project_storage.ps1
$env:RAV_PILOT_TEST_DATABASE_URL = 'postgresql://postgres:rav-dev-only@127.0.0.1:25432/postgres'
python scripts/check_dev.py
```

Linux/macOS:

```sh
export RAV_PILOT_TEST_DATABASE_URL=postgresql://postgres:rav-dev-only@127.0.0.1:25432/postgres
python scripts/check_dev.py
```

Runner đặt cache/temp trong `data/output`, chạy Ruff + toàn bộ pytest (kèm lý do skip) + TypeScript + Vitest + build local/pilot.
Thiếu URL PostgreSQL sẽ fail ngay, không báo xanh khi bỏ toàn bộ pilot tests. Fixture tạo/xóa DB test riêng và thay mật khẩu
role test, nên URL phải là cluster test chuyên dụng. Capacity/GPU/model thật vẫn opt-in và không được coi là đã kiểm.

Chỉ sửa một phía: `--scope backend` hoặc `--scope frontend`. Runner dừng và trả lỗi của lệnh đầu tiên thất bại.
Windows có known hook Unicode failure trong AGENTS: dùng `--ignore-known-windows-hook` nếu cần kiểm phần còn lại,
báo rõ đã loại đúng test này; mặc định vẫn chạy hook, CI Linux không được dùng tùy chọn đó.

Trên Windows, kiểm `netsh interface ipv4 show excludedportrange protocol=tcp` nếu port bị Permission denied;
chọn port ngoài các dải Windows reserve và sửa URL tương ứng. Có thể dùng PostgreSQL 16 native/portable trên ổ repo
thay Docker, với cluster test riêng và cùng biến URL. Trước build/pull Docker, kiểm ổ chứa disk image của Docker Desktop
còn đủ chỗ; cache/temp của runner ở D không di chuyển disk image của Docker. Không tự prune hoặc reset Docker để né lỗi.

## Dev UI hoặc agent không có browser

Agent không có browser vẫn chạy đầy đủ lệnh trên và smoke HTTP. Đổi layout/tương tác cần người hoặc runner có browser
kiểm thêm, không gọi test HTTP là browser E2E.

Khi cần UI pilot local, dùng `scripts/pilot_local_stack.py` hiện có: PostgreSQL test, `python -m moto.server -H 127.0.0.1 -p 5005`,
`setup`, `env`, `token A`. Script setup nhập demo từ dữ liệu local: clean clone phải chuẩn bị dữ liệu demo trước;
không coi đây là bootstrap không cần dữ liệu hoặc cấu hình interactive #133. Xem docstring script và QA của lát.
Token giả chỉ dùng tab Access Token của Vite dev. Không đưa env/token/policy mock vào production.

Khi xong kiểm regression, `docker stop rav26-dev-postgres` dọn container và dữ liệu test tạm của lệnh trên.

## CI và kiểm image

`.github/workflows/ci.yml` chạy khi PR vào main/develop/duy-mvp/v2, push các nhánh đó hoặc `codex/**`, và workflow_dispatch.
Backend có PostgreSQL 16 riêng; frontend dùng cùng runner. Sau hai job xanh, CI build cả `combined` và `worker`.
Combined khởi động không có credential, smoke health, SPA/assets, allowlist runtime-config, readiness 503 khi chưa cấu hình DB,
API missing giữ JSON. Worker kiểm entry `--help` trong container không mạng, không nạp model/chạy GPU.

Kiểm combined local:

```sh
docker build --platform linux/amd64 --target combined -t rav26:dev-combined .
docker run -d --name rav26-dev-combined -p 127.0.0.1:18080:8000 rav26:dev-combined
python scripts/smoke_combined.py
docker logs rav26-dev-combined
docker rm -f rav26-dev-combined
```

Smoke này dùng container không có DB env; không dùng để đánh giá readiness của bản production.
CI không publish registry, migrate, seed policy hoặc deploy. CD giữ [runbook Render](COMBINED_WEB_RELEASE.md)
và [worker](CONTABO_WORKER_RELEASE.md); Git push/publish/deploy theo authorization riêng.
Agent thiếu mạng/dependencies bàn giao commit/patch để runner có môi trường chạy; kết quả local không phải GitHub Actions xanh.
