# Interactive CPU release QA — #133

2026-10-14. Thiết kế [CONTRACTS §21.26](../design/CONTRACTS.md#2126-interactive-propose-và-cpu-không-quota-133-thay-phần-tương-tác-211523).

## Thay đổi đã release

Browser → Render xác minh JWT → Contabo TEXT FastAPI qua HTTPS/token riêng → RAM context → trả batch/URLs ảnh.
Không enqueue/poll PROPOSE, không CPU quota/hold/receipt. Giữ/bỏ nằm ở browser; bấm Đề xuất tiếp lưu batch trước và
full decisions bằng một DB function call/transaction, rồi compute. Không nút Lưu phiên. YOLO/phân tích/snapshot tự checkpoint trước.
Batch vừa tạo còn tạm; reload/restart trước lần chốt có thể mất batch đó. DB giữ batch đã chốt và decisions.

Contabo giữ model CLIP text resident; YOLO owner tái sử dụng model sau load đầu. PII chưa implement, không có ba model thật cùng chạy.
Cache context/features giới hạn 512 MiB, tối đa 8 phiên, TTL 15 phút, revalidate DB tối đa 15 giây; 1 request/user, tối đa 2 toàn service,
bận trả 429 Retry-After. Cold cache vẫn đọc metadata/feature NPZ từ DB/S3; warm compute không đọc video/hash/probe.
CPU usage vẫn telemetry cho job dài/recovery; GPU quota, storage, fence/lease/cleanup giữ. Job snapshot cũ default accounting true.

## Evidence

| Kiểm tra | Kết quả | Phạm vi |
|---|---|---|
| Full backend trước điều chỉnh auto-save/RPC cuối | 552 pass, 24 skip, 455.69 s | Hook Unicode loại riêng; không gọi đây là full suite của diff cuối |
| Focused cuối local | 6 interactive + 41 queue/publish/analysis pass, 2 skip | PostgreSQL/Moto cô lập; Ruff đạt |
| Frontend | 34 pass; TypeScript đạt; combined Docker build đạt | Không thêm bộ test rộng |
| Chrome thật | 1 pass, 35.93 s | Giữ không POST; không poll job; mất response rồi replay cùng request_id; auto-save/reload một batch |
| Contabo mixed load | 7 pass, 92.85 s | Actual CLIP text/YOLO, HTTP TEXT; synthetic video + seeded image embedding; PG/Moto cô lập, 4 CPU/6 GiB |
| TEXT 20 requests dưới tải | p95 235.0 ms | Không phải cloud/semantic-quality benchmark |
| Cloud proposal | cold open 4.531 s; 5 auto checkpoint + propose 1.078–2.688 s | Real Supabase/S3/Contabo, random recipe, scoped API/operator từ Windows; **không authenticated Render HTTP/browser E2E** |
| Cloud upload/media/delete | Đạt, owner isolation 404; raw/64 ảnh xóa sạch | Video tổng hợp 8 s; API/operator + public Render HTTP |

Cloud upload READY 34.047 s; batch16 READY lookup 0.594 s;
clip cold 19.516 s, warm lookup 0.672 s; delete 27.531 s.
Upload/preprocessing và clip mới vẫn chậm; không claim toàn hệ thống đã mượt hoặc p95 SLA ≤3–4 s.

Gate load phát hiện variant ảnh DB viết hoa, response tra chữ thường: đã sửa và chạy lại 7 tests đạt trước release.
Render env PUT từng ngắt mạng: rollback worker/policy cũ đạt; retry idempotent sau đối soát đã lên Live.
Migration 013/014 additive giữ qua rollback; không sửa migration cũ.

## Live identities và vận hành

- Render deploy `dep-db7l4r2vcj2c73e21npg`, image `docker.io/tonmeu/rav26@sha256:09f300d11afe48fc259811a3aabf3b770441b6a67f8d0a9673781fd6f1e4ec8f`.
- Worker local Docker image ID `sha256:2ac0d21a4021eb3648113cc57df3953e2350f3db62645e471336391337774377`; cap 4 CPU/6 GiB; TEXT/YOLO/BULK/MEDIA/GPU.
- Policy `interactive-20261014`, CPU accounting false; migration qua 014. Slot inventory giữ #132.
- TLS IP SAN `161.97.164.25:8443`, self-signed certificate verify bằng public PEM pin trên Render;
  không verify=false. Private key ở Contabo UID1000 mode0600, token riêng chỉ backend env. Certificate hết hạn sau365 ngày:
  thay cert/key và cập nhật pin Render cùng rollout, giữ rollback cũ.
- Worker env: RAV_INTERACTIVE_ENABLED=1, PORT=8443, TOKEN, TLS_CERT/TLS_KEY;
  Render env: URL HTTPS, TOKEN, CA_PEM. Không đưa các secret này vào FE/runtime-config/Git.
- Root-only rollback copies `/opt/rav26-worker/output/pre-interactive-20261014-{worker.env,release.env,compose.worker.yml}`;
  DB backup `output/interactive/pilot-before-interactive.dump` (ignored). Drain/restore image+profile policy đồng bộ, không drop additive migrations.
- Một source/session QA 8 s giữ để kiểm checkpoint: session `4ef76efb-09ba-4958-a053-1667e6e5434d`;
  source `0b9ee57d-42cd-44d4-aaee-291eb731f505`. Không sửa dữ liệu có sẵn; source có session pin, không bypass delete guard.

Artifact log/cache/benchmark ở `output/interactive/` (ignored, không commit JWT/secret/signed URLs).
Browser automation hiện lỗi khởi tạo kernel; local Chrome E2E cô lập đã đạt, browser cloud/auth expiry/A-B còn gate riêng.
GPU provider/Kaggle thật, PII/export, model-guided/reference report và restore drill không được kiểm trong lát này.
