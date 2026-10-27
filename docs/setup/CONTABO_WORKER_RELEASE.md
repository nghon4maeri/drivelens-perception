# Worker Contabo — release và rollback

Thiết kế: [CONTRACTS §21](../design/CONTRACTS.md), [architecture](../architecture_diagram.md).
Current image/config/gates tại [status](DEPLOYMENT_STATUS.md). Host 4 vCPU/~8 GiB; live container 3 CPU/4 GiB.
Compute lanes #127 còn candidate; bật profile/slots/policy chỉ sau [runtime gates](COMPUTE_RUNTIME.md).
Worker CPU/MEDIA riêng; không thay service Render hoặc mở port public.

## Cấu hình và gate trước chạy

- `deploy/worker.env.example` chỉ chứa biến worker. Credential DB phải là `rav_pilot_worker` qua Session pooler TLS;
  không copy DATABASE_URL/admin/Supabase secret/Kaggle token từ `.env` operator.
- `deploy/compose.worker.yml` dùng image đã kiểm theo tag/digest, UID 1000, thư mục data/output writable,
  restart unless-stopped, log có giới hạn, CPU/memory do operator cấp theo policy/host (defaults 3 CPU, chưa là toàn capacity host).
- PID namespace host và hostname ổn định giúp supervisor kiểm PID/start token sau container recreate;
  user không phải root, bỏ capabilities, không mount Docker socket. Không chạy hai worker cùng workdir.
- Health chỉ kiểm process identity/start token và boot hiện tại; không thay DB/schema probe hoặc cloud smoke.
  `operator-bootstrap` chỉ probe, không claim; `blocked=[]` không chứng minh IAM S3 hay job chạy được.
- CPU PyTorch/torchvision từ index chính thức trước requirements worker; không cài CUDA trên Contabo.
  Nguồn: [PyTorch](https://pytorch.org/get-started/previous-versions/).
- Trước queue: DB role LOGIN, schema checksum đúng; CPU/MEDIA slots FREE; policy đủ caps, profiles và
  deadline cho VALIDATE_UPLOAD/RENDER_MEDIA. Không tự sửa policy bất biến: tạo version mới, đổi profile khi operator duyệt.
- Thời gian dừng Compose phải lớn hơn stop_deadline_ms + cleanup_deadline_ms (cộng thời gian I/O/đối soát).
  Không tự đổi QUARANTINED về FREE nếu thiếu proof.

## Cài, probe và rollout

Docker từ [apt repository chính thức](https://docs.docker.com/engine/install/ubuntu/), không reset Docker hoặc xóa volume.
Thư mục host riêng `/opt/rav26-worker`, secrets chỉ root đọc, data/output UID 1000. Trong thư mục đó:

Image có thể build trên VPS từ source đã loại `.env`, private key, data/output và cache:
`docker build --platform linux/amd64 --target worker -t rav26-worker:<release> .`.
Ghi image ID/digest và revision source của bản đã kiểm; không coi tag mutable là bằng chứng release.
Image nằm trên host không đồng nghĩa đã publish registry hoặc có pipeline CI.

```bash
install -d -m 0750 -o 1000 -g 1000 data output output/tmp
install -d -m 0750 -o 1000 -g 1000 data/system-cache/huggingface data/system-cache/huggingface/xet data/system-cache/huggingface/xet/logs
chmod 600 worker.env release.env
# release.env: RAV_WORKER_IMAGE, RAV_WORKER_MEMORY_LIMIT, RAV_WORKER_STOP_GRACE_PERIOD.
docker compose --env-file release.env -f compose.worker.yml config --quiet
docker compose --env-file release.env -f compose.worker.yml run --rm worker python -m src.rav.worker --mode operator-bootstrap
# Chỉ sau khi gate trước chạy đạt:
docker compose --env-file release.env -f compose.worker.yml up -d worker
docker compose --env-file release.env -f compose.worker.yml ps
docker compose --env-file release.env -f compose.worker.yml logs --tail 50 worker
```

Không chạy `compose config` không có `--quiet`: có thể in secrets từ env_file.
Rollback: stop worker theo grace, đổi RAV_WORKER_IMAGE về digest đã kiểm rồi up;
giữ data/output, schema và receipts. Không docker system prune hoặc down -v.

## Smoke cloud bắt buộc

Release N1 2026-10-14: [QA và CLIP cache preflight](N1_RELEASE_QA.md).
Các số đo #112 dưới đây là lịch sử, không phải số đo release N1.

Trạng thái 2026-10-11: build worker pool trên VPS đạt, image manifest hiện tại
`sha256:d8dfc32b16eff2135ce848913cc548fae9d8cc25f4095e6390e58481a51ffdba` (#112, source delete/retry + log latency).
Compose dùng digest này, memory 4g, grace 180s; image trước pool `57fdfd2a282d…` giữ để rollback.
Bootstrap `blocked=[]`; probe xác nhận role `rav_pilot_worker`, CPU/MEDIA slots FREE;
container healthy và không có token Kaggle. Job ColdBrew cũ đã quá hạn queue khi worker
bị dừng, được supervisor chuyển FAILED đúng `queue_deadline_exceeded` và dọn staging.
Upload mới ban đầu lỗi kết nối ở complete; thử lại thành công: ColdBrew READY,
79.635.620 byte, 29,767 giây, 120 unit, SHA bắt đầu `af09a179b6aa3ff0`.
Thumbnail đã hiển thị trên app; MEDIA bị thiếu quota smoke và chờ reconcile, sau CPU bonus
đã tất toán/trả FREE. Hai account test hiện có được cấp 3 giờ CPU bonus theo #110.
Pool DB đã qua test PostgreSQL thật (405 pass/3 skip), bootstrap và rollout/recreate giữ dữ liệu,
media/receipts và hai slots FREE. Probe transaction 2,322–2,338 → 0,780–0,835 giây;
chưa đo lại toàn upload hoặc kiểm frame/context. API Render nay Live digest `0c547501c775…`, có pool/log và retry delete; xem [UPLOAD_LATENCY_LOGS](UPLOAD_LATENCY_LOGS.md).
DEP7-Docker vẫn chưa tick.

DEP6a #112: worker healthy sau recreate; smoke operator tạo video tổng hợp mới 1 giây/1.556 byte,
VALIDATE_UPLOAD READY → DELETE_SOURCE SUCCEEDED trên S3/Supabase thật; HEAD raw absent, source DELETED,
receipt released_bytes=1556, usage FINAL, cleanup_done=true, CPU/MEDIA FREE. Không xóa nguồn có sẵn trong smoke.
Giữ image #111 và `output/release-pre-delete.env` để rollback; export/PII và các gate khác vẫn riêng.

1. Account thật upload `c2-gpu-smoke.mp4` → S3 staging → complete → VALIDATE_UPLOAD SUCCEEDED → source READY.
2. Kiểm metadata/core ID/SHA và hold/usage/slot; không tính source READY khi chỉ staging POST đạt.
3. Xin thumbnail/frame/context qua API → RENDER_MEDIA → READY; GET signed URL trả đúng JPEG/mp4.
4. Restart có kiểm soát khi idle rồi xin lại media; không chạy GPU/Kaggle trước CPU/media đạt.
5. Ghi image/digest/host/runtime và kết quả thật ở DEPLOYMENT_STATUS, không ghi signed URL/secret.

Chưa đạt các bước này thì không tick DEP7-Docker hoặc DEP7. GPU/provider, export/PII, auth expiry/A-B,
backup/restore và release CI vẫn là các gate riêng.


## Live #133 interactive (2026-10-14)

[Deployment status](DEPLOYMENT_STATUS.md) và [Interactive QA](INTERACTIVE_CPU_QA.md) là checkpoint hiện tại.
TEXT chuyển thành HTTPS FastAPI khi RAV_INTERACTIVE_ENABLED=1; GPU/BULK/YOLO/MEDIA vẫn durable.
Render cấu hình URL/token/CA pin cùng worker TLS/token. CPU accounting tắt bằng policy mới; không sửa snapshot job cũ.
Drain/backup trước đổi worker/API/policy, giữ rollback cả ba; không chạy legacy PROPOSE queue khi TEXT đã chuyển HTTP.
