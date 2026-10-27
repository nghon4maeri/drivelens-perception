# Runbook vận hành deployment pilot

Tài liệu hướng dẫn vận hành và quy trình bootstrap hạ tầng cho pilot (2–3 người dùng).
Cập nhật navigation #128; schema deploy tới 010, worker frames đã live, compute lanes mới candidate.
Trạng thái image/policy/gate tại [DEPLOYMENT_STATUS](DEPLOYMENT_STATUS.md);
lệnh trong runbook không chứng minh worker hoặc cloud smoke đã hoàn tất.

---

## 1. Phân bổ cấu hình theo host

| Biến môi trường | Render Web API | Contabo AI Worker | Ghi chú |
|---|---|---|---|
| `PORT` | `10000` trên Render, mặc định image `8000` | Không mở port public | API bind `0.0.0.0` |
| `RAV_S3_BUCKET`, `RAV_PILOT_S3_PREFIX`, `AWS_DEFAULT_REGION` hoặc `RAV_S3_REGION` | Có | Có | Bucket/namespace pilot tường minh |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Có (theo tác vụ storage API) | Có (download raw, upload result) | Scoped IAM permissions |
| `SUPABASE_URL` / `SUPABASE_JWKS_URL` | Có | Không cần | Xác minh token người dùng (JWKS RSA/EC) |
| `SUPABASE_JWT_SECRET` | Chỉ nếu còn HS256 legacy | Không | Không dùng secret API key thay thế |
| `DATABASE_URL` | Có (role `rav_pilot_api`) | Không dùng | TLS PostgreSQL/Session pooler |
| `RAV_PILOT_WORKER_DATABASE_URL` | Không | Có (role `rav_pilot_worker`) | Credential riêng worker |
| `KAGGLE_API_TOKEN` | **KHÔNG** | Chỉ nếu bật GPU | API không điều phối GPU |

---

## 2. Quy trình build container

### A. Build Web API (Render Free Web Service)
Render Web Service sử dụng Dockerfile mặc định (target `api`, chạy `uvicorn src.rav.api.pilot:app` — chỉ `/api/v2` + health):
```bash
docker build --target api -t rav-api:pilot .
```
- Image sử dụng `requirements-api.txt` siêu nhẹ (<200MB).
- Không chứa PyTorch, torchvision, ultralytics hay CUDA.
- `HEALTHCHECK` kiểm tra `/health` ở `$PORT` (mặc định 8000).

### B. Build AI Worker (Contabo VPS)
Contabo VPS build container với target `worker`:
```bash
docker build --target worker -t rav-worker:pilot .
```
- Image cài đầy đủ `requirements-worker.txt` (PyAV, OpenCV, PyTorch CPU, Ultralytics, Kaggle SDK).

---

## 3. Kiểm tra sức khỏe (Health & Readiness)

### A. Liveness Probe (`GET /health`)
- Dùng cho `HEALTHCHECK` và UptimeRobot Free mỗi 5 phút; không cần cron ping Contabo trong cấu hình hiện tại.
- Theo yêu cầu #105, monitor cả API `https://rav26-api.onrender.com/health` và FE `https://irishcoffee-t026.onrender.com/health`, Keyword GET, yêu cầu có `"status":"ok"`; báo incident khi thiếu keyword, timeout 60 giây, email tài khoản. API HEAD trả 405 nên không dùng HTTP HEAD mặc định.
- Render Free có 750 instance-hours/tháng dùng chung workspace. Hai service luôn chạy cần 1.440–1.488 giờ/tháng; ping không tăng quota, hết giờ có thể bị suspend. Xem chi tiết và trạng thái monitor trong DEPLOYMENT_STATUS.
- Endpoint trả về ngay lập tức (latency p95 < 5ms), không truy vấn database, không nạp model.
- Phản hồi mẫu:
  ```json
  {
    "status": "ok",
    "service": "rav-api-v2",
    "version": "2.0.0",
    "mode": "pilot"
  }
  ```

### B. Readiness Probe (`GET /health/ready`)
- Kết nối được Postgres **và** schema đúng version (`rav_pilot_schema_migration`) → 200 `{"status": "ok", ...}`.
- Thiếu `DATABASE_URL`/không kết nối được → 503 `dependency_unavailable`; chưa migrate/lệch checksum → 503 `schema_not_ready`.
  Endpoint nghiệp vụ trả cùng mã lỗi; API không bao giờ fallback SQLite hay tự migrate.

---

## 4. Đo đạc hiệu năng với Benchmark Harness

Chạy script đo đạc tài nguyên trên máy chủ hoặc môi trường test:
```bash
. .\scripts\use_project_storage.ps1
python scripts/pilot_benchmark_harness.py
```
Script sẽ kiểm tra:
1. Cold start time của Web API (tiêu chuẩn: < 2.0s).
2. Dung lượng bộ nhớ RAM (RSS) (tiêu chuẩn: < 512MB của Render Free).
3. Đảm bảo 100% không có module ML nặng nào (`torch`, `ultralytics`, `transformers`, `cv2`) bị rò rỉ vào tiến trình Web API.
4. Đo đạc độ trễ p50, p95, p99 của endpoint `/health`.
5. Ghi nhận kết quả vào `output/pilot-benchmark/measurements.json`.

---

## 5. Migration và role PostgreSQL (operator, DEP2F)

Candidate 011 publish PROPOSE là staged capability (§21.24): worker cũ/DB chưa có 011 dùng Python publish.
Readiness chấp nhận thiếu riêng 011; checksum sai vẫn bị chặn. Khi áp 011, restart worker để probe capability lại;
không tự migrate từ API. [QA](PROPOSE_PUBLISH_RPC_QA.md) và rollout riêng trước dùng live.

1. Trên máy operator (không phải Render/Contabo), dùng role có quyền DDL:
   ```bash
   python scripts/pilot_migrate.py
   ```
   In các migration mới thực sự được áp; deployment đã tới 010 theo status. Chạy lại khi đủ schema in `không có migration mới`.
   PowerShell từ repo root: source storage rồi nhập URL DDL bằng secure prompt; không paste credential vào shell history/chat.

   ```powershell
   . .\scripts\use_project_storage.ps1
   $migrationInput = Read-Host 'URL Session pooler role DDL' -AsSecureString
   $env:RAV_PILOT_MIGRATE_DATABASE_URL = [System.Net.NetworkCredential]::new('', $migrationInput).Password
   try {
       python scripts/pilot_migrate.py
       if ($LASTEXITCODE -ne 0) { throw 'Migration failed; stop rollout' }
   } finally {
       Remove-Item Env:RAV_PILOT_MIGRATE_DATABASE_URL -ErrorAction SilentlyContinue
   }
   ```
   Checksum lệch với bản đã áp → thoát mã 2, không sửa gì (tạo migration mới thay vì sửa file đã áp).
2. Migration tạo role NOLOGIN `rav_pilot_api`, `rav_pilot_worker` (và `authenticated`/`anon` nếu là Postgres thường). Bật đăng nhập:
   ```sql
   ALTER ROLE rav_pilot_api LOGIN PASSWORD '<mật khẩu riêng>';
   ALTER ROLE rav_pilot_worker LOGIN PASSWORD '<mật khẩu riêng>';
   ```
   Render dùng `rav_pilot_api` (đọc qua RLS `authenticated`, ghi có kiểm owner); Contabo dùng `rav_pilot_worker`.
3. Tài khoản pilot (§21.34): người dùng có thể tự đăng ký trên `/login`; trong lần xác thực đầu tiên, backend tự động tạo `rav_pilot_profile` và `rav_pilot_storage_account` gắn với policy có `signup_default = true` (hoặc `RAV_PILOT_SIGNUP_POLICY_VERSION`). Operator vẫn có thể tạo/sửa thủ công khi cần:
   ```sql
   INSERT INTO rav_pilot_profile (user_id, display_name, policy_id) VALUES ('<auth.users.id>', 'Tên', '<policy id>');
   ```
4. Quota policy và slot **chỉ khai báo sau khi có số đo DEP1** (thiếu khóa hoặc chưa đặt policy đăng ký → 503 `policy_unconfigured`):
   ```sql
   INSERT INTO rav_pilot_quota_policy (id, version, config, enabled, signup_default)
   VALUES (gen_random_uuid(), 'pilot-2026-10-a', '<config JSON đủ khóa runtime spec §3.2>', true, true);
   INSERT INTO rav_pilot_resource_slot (slot_key, kind, account_or_host, state) VALUES ('cpu-1', 'CPU', 'contabo-1', 'FREE');
   ```
   Policy bất biến: muốn đổi số thì thêm version mới, chuyển `signup_default = true` sang version mới (tắt ở version cũ) và đổi `profile.policy_id` nếu cần.

## 6. Test với PostgreSQL thật (dev)

Test RLS/transaction/race cần Postgres thật. Bản portable (không cần quyền admin, chỉ nghe 127.0.0.1):
```powershell
. .\scripts\use_project_storage.ps1
# một lần: giải nén binaries PostgreSQL 16 vào data/tools/pgsql, rồi
data\tools\pgsql\bin\initdb.exe -D data\pg-test -U postgres -A trust -E UTF8 --locale=C
# mỗi lần mở máy:
data\tools\pgsql\bin\pg_ctl.exe -D data\pg-test -o "-p 54329 -c listen_addresses=127.0.0.1" -l output\pg-test.log start
$env:RAV_PILOT_TEST_DATABASE_URL = 'postgresql://postgres@127.0.0.1:54329/postgres'
python -m pytest tests -q --basetemp=output/pytest-tmp
```
Repo ở đường dẫn có dấu: `initdb`/`pg_ctl` báo `invalid binary` → `subst R: .` rồi chạy các lệnh trên từ `R:\` (mất khi khởi động lại).
Thiếu biến → test pilot skip kèm lý do. CI dùng service `postgres:16` (`.github/workflows/ci.yml`).

## 7. Upload, media riêng tư và phiên mẫu (DEP4)

1. **S3**: API và worker cần `RAV_S3_BUCKET`, `RAV_PILOT_S3_PREFIX` (prefix riêng cho pilot, vd `pilot-rav26`; không dùng root bucket),
   `AWS_DEFAULT_REGION` và credential (env inject). Thiếu → upload/media trả 503 `dependency_unavailable`.
   CORS của bucket phải cho phép `POST` từ origin frontend (trình duyệt POST thẳng vào presigned URL staging).
2. **Policy** cần thêm (số đo DEP1): `storage_user_bytes`, `storage_global_bytes`, `upload_ttl_ms`, `staging_grace_ms`, `user_get_ttl_ms`,
   `max_media_requests`; tùy chọn `profiles_by_kind.RENDER_MEDIA.output_bytes` (giữ chỗ dung lượng kết quả media).
3. **Slot**: worker cần ít nhất một slot `CPU` (VALIDATE_UPLOAD) và một slot `MEDIA` (RENDER_MEDIA):
   ```sql
   INSERT INTO rav_pilot_resource_slot (slot_key, kind, account_or_host, state) VALUES ('media-1', 'MEDIA', 'contabo-1', 'FREE');
   ```
4. **Worker**: đăng ký handler `src.rav.worker.handlers.make_handlers(storage, db, workdir)` cho dispatcher queue `CPU` và `MEDIA`;
   supervisor nhận `PilotUploads` để dọn staging quá hạn. Worker dùng PyAV có `libx264` (clip ngữ cảnh mp4).
5. **Import phiên mẫu** (máy operator có `output/rav.sqlite` + `data/videos`, danh sách ở `docs/setup/pilot_demo_inventory.json`):
   ```bash
   python scripts/pilot_import_demo.py --prefix pilot-rav26                       # dry-run: in inventory_hash, ghi output/pilot-import/plan-*.json
   python scripts/pilot_import_demo.py --prefix pilot-rav26 --apply --operator-confirm <inventory_hash> --database-url "$RAV_PILOT_MIGRATE_DATABASE_URL"
   ```
   Kết quả mong đợi: `{"replayed": false, "sources": 2, "templates": 2, "events": 110}`; chạy lại cùng hash → `replayed: true`.
   Inventory đổi thì dùng `seed_version` mới (cùng seed_version khác hash → từ chối).

## 8. Entry worker chạy được (DEP4F)

Worker dùng riêng `RAV_PILOT_WORKER_DATABASE_URL` với role `rav_pilot_worker`; không lấy `DATABASE_URL` của API.
Trước claim, entry kiểm kết nối/checksum schema và cấu hình bucket/prefix S3. Không tự migrate hoặc tạo bucket.
`RAV_PILOT_WORKDIR` mặc định `output/pilot-worker`, phải là thư mục con trong `data/` hoặc `output/` từ working directory repo.
Cache/temp host tiếp tục theo `scripts/use_project_storage.ps1`; container mount các thư mục này vào `/app/data`, `/app/output`
và cấp quyền ghi cho UID 1000. Dependency API và worker nằm trong `/opt/venv`, đọc được bởi user `appuser`.

Chạy từ repo root, environment đã inject (PowerShell):

```powershell
. .\scripts\use_project_storage.ps1
# Set RAV_PILOT_WORKER_DATABASE_URL, RAV_S3_BUCKET, RAV_PILOT_S3_PREFIX,
# AWS_DEFAULT_REGION (hoặc RAV_S3_REGION), AWS credentials theo scoped IAM.
python -m src.rav.worker --mode operator-bootstrap
python -m src.rav.worker --mode pilot
```

Bootstrap chỉ probe schema/S3 config và báo `bootstrap_ready`, `pilot_ready=false`, `blocked`; không claim/recovery/AI.
Probe cấu hình S3 không chứng minh IAM/network dùng được: cần upload/media smoke riêng.
Linux lấy host boot UUID từ `/proc/sys/kernel/random/boot_id`; Windows operator inject `RAV_PILOT_HOST_BOOT_ID`
là UUID ổn định suốt một lần boot, đổi khi host boot mới. Worker ID mới mỗi process; process proof do dispatcher/supervisor ghi và kiểm,
không dùng UUID do người dùng tùy đổi để giả bằng chứng process đã dừng.

Container worker (env file chỉ chứa cấu hình worker, không đưa API/admin DB credential vào):

```bash
docker build --target worker -t rav-worker:pilot .
docker run --rm --env-file .env.worker --mount type=bind,src="$(pwd)/data",dst=/app/data --mount type=bind,src="$(pwd)/output",dst=/app/output rav-worker:pilot
# Kiểm probe bằng cùng image:
docker run --rm --env-file .env.worker rav-worker:pilot python -m src.rav.worker --mode operator-bootstrap
```

Entry poll CPU và MEDIA tuần tự, mỗi queue tối đa một claim mỗi vòng; `RAV_PILOT_WORKER_POLL_S` (mặc định 1 s)
và `RAV_PILOT_WORKER_RECONCILE_S` (10 s) phải >0 và ≤60 s. Supervisor nhận `PilotUploads` để sweep staging.
Policy/slot phải có trước admission: tối thiểu CPU/MEDIA slots và toàn bộ caps runtime spec §3.2 (`worker_rss_bytes`,
`worker_temp_bytes`, `lease_ms`, `heartbeat_ms`, `run_deadline_ms_by_kind`, `stop_deadline_ms`, `cleanup_deadline_ms`).
Poll interval không thay deadline/credit policy. SIGTERM/SIGINT ngừng claim mới và chuyển stop event tới handler;
operator đặt thời gian grace dừng container phù hợp stop/cleanup deadlines. Forced kill cần supervisor đối soát attempt/slot,
không tự trả slot QUARANTINED khi thiếu proof.

Readiness API vẫn qua `/health/ready` (DB/schema); log worker sẵn sàng chỉ xác nhận các queue đã nối.
Kind chưa có handler trả `handler_unavailable`, không publish kết quả giả; PROPOSE/METRICS/YOLO đã có code DEP5, chưa chứng minh worker cloud chạy được.
Readiness DINO/CLIP chỉ từ artifact FEATURE READY, không suy từ việc worker/container đã khởi động.

## 9. Chuẩn bị đặc trưng DINO/CLIP trên GPU Kaggle (DEP5a, CONTRACTS §21.14, #94)

1. **Slot GPU** (operator, DB): mỗi slot một dòng `rav_pilot_resource_slot(kind='GPU', account_or_host=<account Kaggle>)`,
   policy có `slot_inventory.GPU`, `profiles_by_kind.PREPARE_DINO|PREPARE_CLIP` (`gpu_ms`, `output_bytes`), `gpu_snapshot_fresh_ms`,
   `gpu_headroom_ms`, `run_deadline_ms_by_kind`, `stop_deadline_ms`, `remote_url_ttl_ms`. Một account Kaggle dành riêng cho pilot.
2. **Worker**: thêm `KAGGLE_API_TOKEN` (chỉ Contabo). Worker tự tạo/kiểm notebook private `<account>/rav26-pilot-<slot_key>`,
   chạy queue GPU trong tiến trình con riêng (spawn, CPU không tính lẫn với job CPU/MEDIA), ghi snapshot quota (`get_accelerator_quota_statistics`) mỗi `RAV_PILOT_GPU_BUDGET_S` giây.
   Thiếu token → không claim job GPU (job chờ `gpu_budget_unverified`).
3. **Snapshot dự phòng** khi SDK lỗi: `python scripts/pilot_gpu_budget.py --account ... --remaining-hours ... --reset-at ... --operator-id ... --evidence ...`.
4. **Asset DINO**: worker cần cache Torch Hub DINOv2-S (`TORCH_HOME`) để đóng gói lần đầu lên `<prefix>/system/models/dinov2_s/<sha>`;
   CLIP tải checkpoint/revision đã pin trên Kaggle.
5. **Đặc trưng nguồn SYSTEM**: tính local trước (FieldStore `output/fields`), rồi
   `python scripts/pilot_import_demo.py --prefix <prefix> --features` (dry-run, in `inventory_hash`) →
   `--features --apply --operator-confirm <hash> --database-url ...`. Seed đã import không kèm đặc trưng cần `seed_version` mới.
6. **Sự cố**: slot GPU QUARANTINED nghĩa là worker chưa chứng minh được session Kaggle đã dừng (launch không chắc / dừng chưa xác minh).
     Supervisor tự trả slot khi đúng session ID đã kết thúc; không tự đổi state slot bằng SQL. Xem `reconcile_evidence` của slot.
7. **Đối soát tương tác (#113)**: status notebook theo slug không phản ánh `create_kernel_session`. Dùng SDK logs stream theo session ID:
   SSE → đang chạy, HTTP 200 JSON rỗng/mảng log lưu → đã kết thúc, lỗi/envelope khác → UNKNOWN. Slot mới cần evidence
   `verified_session_id` từ smoke riêng đã dừng; attempt ghi `process_group.gpu_slot_key` cùng INTENT để tra đúng lịch sử.
   DINO 0909 trên T4 thật đạt 539/539, artifact READY và STOPPED/FINAL/cleanup_done/slot FREE. CLIP thật chưa smoke.

## 10. Đề xuất trên worker (DEP5b, CONTRACTS §21.15, #95)

- Job `PROPOSE` chạy trên queue CPU của cùng worker (`python -m src.rav.worker --mode pilot`); policy cần
  `profiles_by_kind.PROPOSE`, `chunk_bounds_by_kind.PROPOSE.max_units`, `run_deadline_ms_by_kind.PROPOSE`.
- Worker cache raw và artifact đặc trưng theo SHA trong `RAV_PILOT_WORKDIR/raw-cache`, `feature-cache` (dùng chung giữa job; xóa thư mục an toàn khi worker dừng).
- `text_diversity` mã hóa câu bằng CLIP text (checkpoint/revision pin) trên CPU worker: host worker cần cache HF model trong `data/` trước pilot.
- Đổi recipe/component → chạy lại `python scripts/pilot_recipe_catalog.py` và commit `src/rav/store/pilot_recipes.json` (test báo lệch).
- Lỗi `proposal_context_changed`: người dùng keep/drop trong lúc đề xuất chạy; FE tải lại state rồi đề xuất lại (DEP5c khóa duyệt khi chờ).

## 11. Giao diện pilot và smoke local (DEP5c, CONTRACTS §21.16, #96)

- Build FE pilot: `VITE_RAV_MODE=pilot`, `VITE_API_URL` (gốc API nếu khác origin), `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY`;
  `npm ci && npm run build` (thư mục `src/web`), chỉ deploy `dist`. Bản build không có tab Access Token. Bucket S3 cần CORS cho origin web (POST upload, GET media).
- Bản local (không đặt `VITE_RAV_MODE`) giữ UI cũ trên `/api/v1`, không đăng nhập.
- Test FE: `npm test` (vitest, HTTP giả) + `npx tsc --noEmit` + `npm run build`.
- **Smoke local giả lập** (không chạm cloud): PowerShell từng cửa sổ, đã `. .\scripts\use_project_storage.ps1`, Postgres portable đang chạy (§6):
  1. `python -m moto.server -H 127.0.0.1 -p 5005`
  2. `python scripts/pilot_local_stack.py setup` (DB `rav_pilot_smoke`, 3 tài khoản A/B/C, slot CPU/MEDIA, import mẫu + đặc trưng vào S3 giả)
  3. `python scripts/pilot_local_stack.py env` → đặt env nhóm `api` rồi `python -m uvicorn src.rav.api.pilot:app --port 8010`;
     nhóm `worker` rồi `python -m src.rav.worker --mode pilot`; nhóm `web` rồi `cd src/web; npx vite --port 3000`.
  4. Mở http://127.0.0.1:3000, tab "Access Token (dev)", dán `python scripts/pilot_local_stack.py token A` (B, C ở cửa sổ ẩn danh khác).
  Không có worker GPU: chuẩn bị DINO/CLIP cho video upload sẽ chờ `gpu_budget_unverified` (đúng hành vi); video mẫu đã có đặc trưng import.

## 12. Model YOLO và phân tích (DEP5b2, CONTRACTS §21.17, #97)

- **Model SYSTEM** (operator): `python scripts/pilot_import_model.py --file <ckpt.pt> --name "<tên>" --train-dataset <dataset gốc>` (dry-run: nạp đúng
  Ultralytics pin, chạy thử detect, in `plan_hash`) → thêm `--apply --operator-confirm <plan_hash> --database-url ...`. Ghi đúng dataset train gốc;
  checkpoint COCO không phải model train trên dữ liệu phiên. Người dùng không upload model ở pilot.
- YOLO chạy trên worker CPU (cache checkpoint theo SHA trong `RAV_PILOT_WORKDIR/model-cache`); kết quả lưu theo đợt + model + runtime, lần sau dùng lại.
- Phân tích là job `METRICS` bấm mới tính (tab Phân tích); policy cần `profiles_by_kind.METRICS`, `profiles_by_kind.YOLO_BATCH`.
- Recipe `reference_diversity`/`model_guided`/`text_model_guided`: chưa dùng ở pilot (cần dataset train + khai báo model↔dataset thật).

## 13. Ảnh lưu sẵn thumbnail/frame (DEP7-Frames, CONTRACTS §21.20, #119)

- Migration `010_pilot_frames.sql` (operator, ngoài boot). Policy version mới phải có `chunk_bounds_by_kind.VALIDATE_UPLOAD` và
  `.PREPARE_FRAMES` = `{frame_batch_size, frame_put_concurrency, max_frame_bytes}`, `profiles_by_kind.PREPARE_FRAMES`, và ước lượng CPU/deadline
  `VALIDATE_UPLOAD`/`PREPARE_FRAMES` đủ cho tách ảnh (đo local: decode+encode ≈ 11 s CPU cho video 354 s 640×360). Thiếu khóa → 503 `policy_unconfigured`.
- Upload mới tách ảnh trong `VALIDATE_UPLOAD`; nguồn cũ: người dùng bấm chuẩn bị → `POST /api/v2/sources/{id}/preparations {"feature": "FRAMES"}`
  (Idempotency-Key) → job `PREPARE_FRAMES` queue CPU. GET/media không tự chuẩn bị; chưa có ảnh → `NOT_PREPARED`.
- Nguồn SYSTEM (operator, dung lượng chỉ tính `GLOBAL`):
  ```powershell
  python scripts/pilot_prepare_system_frames.py --database-url <operator-url> --prefix <RAV_PILOT_S3_PREFIX>   # dry-run: số nguồn/ảnh + inventory_hash
  python scripts/pilot_prepare_system_frames.py --database-url <operator-url> --prefix <prefix> --apply --operator-confirm <inventory_hash> --policy-version <v>
  ```
  Kế hoạch ghi ở `output/pilot-frames/plan-<hash>.json`. Key ảnh tất định theo nguồn/nội dung/spec/encoding: chạy lại ghi đè đúng object, nguồn đã có bộ được bỏ qua.
- Xóa nguồn xóa cả ảnh theo lô (100 key/lô, 8 đồng thời); lỗi giữa chừng giữ `DELETE_PENDING`, thử lại sau khi job trước đã đối soát.
- Worker (F3): process chính chỉ chạy supervisor; CPU, MEDIA (và GPU khi có `KAGGLE_API_TOKEN`) mỗi queue một process `spawn` trong cùng container.
  Process con chết → khởi động lại riêng queue đó sau 1, 2, 4… tối đa 60 s; quá 5 lần trong 10 phút → worker thoát mã 3, Docker `restart: unless-stopped`
  khởi động lại container. Healthcheck đạt khi process chính và mọi process con cần thiết đều sống (`process.json` trong `RAV_PILOT_WORKDIR`).
  Mỗi process có pool DB tối đa 4 kết nối: worker dùng tối đa 12 kết nối (16 khi có GPU) thay vì 8. Đo local: process con lúc rảnh ≈ 63 MB RSS;
  `mem_limit` container phải tính thêm RAM khi job tách ảnh/clip chạy song song trên CPU và MEDIA.
- Cache đĩa worker (F4): `RAV_PILOT_CACHE_MAX_BYTES` (worker.env) đặt trần tổng `raw-cache`/`model-cache`/`feature-cache`; bỏ trống = không dọn.
  Dọn LRU theo mtime sau mỗi lần tải mới, chừa file dùng trong 10 phút; `.part` bỏ dở quá 24 giờ bị xóa. Giá trị sai → worker dừng ngay lúc khởi động.
- FE (F4): ô ảnh `NOT_PREPARED` có nút "Chuẩn bị"; Kho dữ liệu có huy hiệu/nút "Chuẩn bị ảnh". Thứ tự release/rollback và số đo local: [FRAMES_RELEASE](FRAMES_RELEASE.md).
- Test end-to-end local đúng luồng (API HTTP + worker spawn + moto server + PostgreSQL): `python -m pytest tests/test_pilot_frames_e2e.py -q --basetemp=output/pytest-tmp`.

## 14. Model che PII cho export (DEP6, CONTRACTS §21.8)

Worker fail-closed: thiếu/sai model → export `FAILED` `pii_model_unavailable`, không xuất ảnh chưa che.
Đặt hai file vào `data/models/pii` trên host worker (compose đã mount `./data`), hoặc chỉnh `RAV_PII_MODEL_DIR`:
```bash
mkdir -p data/models/pii && cd data/models/pii
curl -fsSLO https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
curl -fsSLO https://github.com/ankandrew/open-image-models/releases/download/assets/yolo-v9-t-640-license-plates-end2end.onnx
sha256sum *.onnx   # phải khớp CONTRACTS §21.8 DEP6
```
Image worker cần `onnxruntime` (đã có trong `requirements-worker.txt`); rebuild image trước khi bật lại export.

## 15. Admin upload mô hình tham chiếu (CONTRACTS §21.33, #142)

Admin = user_id trong `RAV_PILOT_OPERATOR_IDS` của API (cùng danh sách xem vận hành). Thứ tự bật trên live:
1. `python -m scripts.pilot_migrate --database-url ...` áp `021_pilot_model_verify.sql` và `022_pii_cache_index.sql` khi release bản gộp admin model + PII (additive; API/worker cũ vẫn chạy).
2. Policy mới có `profiles_by_kind.MODEL_VERIFY` (vd `{"cpu_ms": 120000, "gpu_ms": 0}`), thiếu → 503 `policy_unconfigured` khi complete.
3. Worker: thêm `MODEL_VERIFY` vào một lane trong `RAV_PILOT_CPU_LANES` (khuyến nghị lane YOLO, cùng Ultralytics) rồi đổi image;
   thiếu kind trong lanes → worker từ chối khởi động (`CPU lanes/budgets không hợp lệ`).
4. FE/API image mới: tab Mô hình tham chiếu (link trên Kho dữ liệu), form upload chỉ hiện với admin.

Browser POST thẳng lên S3 (presigned, staging `staging/system-models/`), cần CORS bucket cho origin FE như transport POST video.
Staging bỏ dở không có sweeper (Hướng tới: lifecycle rule S3 prefix đó). Operator vẫn có thể dùng `scripts/pilot_import_model.py` (cùng id/metadata).
