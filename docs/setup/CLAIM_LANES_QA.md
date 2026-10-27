# Claim và lanes — checkpoint #132, 2026-10-14

CPU/MEDIA claim + process identity + frozen policy dùng một DB call; handler-stopped proof vẫn riêng.
Local: 546 passed/24 skipped trong 481.42 s (hook Unicode loại riêng; 18 latency opt-in, capacity opt-in,
Windows/Linux/checkpoint skips). Focused 58 + claim RPC 6; E2E cuối 1 passed trong 62.65 s. Ruff đạt.

Contabo namespace test riêng, PostgreSQL 17/Moto, không production credentials: 84 regression Linux passed,
mixed-load 1 passed/3 rounds trong 128.38 s. CLIP text và YOLO inference thật, video tổng hợp 90 s,
image embeddings seed giả; không quality/GPU/PII hoặc browser SLA claim. Cả ba round PROPOSE/YOLO/upload overlap.
Peak RSS: TEXT 670 MiB, YOLO 383 MiB, BULK 119 MiB, MEDIA 167 MiB; cgroup test peak 1.18 GiB.
Batch16 lookup 78–101 ms, clip 1.21–1.72 s; không phải latency cloud Supabase/S3.

Live worker image `sha256:acb5095ef6ff6d4effa22863ed812020f6882d5c102102839230a03d38745f40`;
cap 4 CPU/6 GiB, TEXT/YOLO/BULK/MEDIA và GPU coordinator có sẵn healthy, admission đã mở lại.
Schema 011/012 additive; policy mới `lanes-20261014`, giữ heavy/user=2, GPU/storage/CPU numbers cũ.
CPU slots cpu-text/cpu-yolo/cpu-bulk; cpu-1 giữ để rollback. Render source bbd7d7a không đổi, public ready/app=200,
API anonymous=401. Backup DB/configs giữ ngoài Git. Compose host cũ hardcode 3 CPU đã sửa để nhận RAV_WORKER_CPUS.

Evidence ignored tại output/claim-lanes: full/e2e/linux/capacity logs và JSON, live-health, rollout-state.
Cloud functional smoke/proposal chưa hoàn tất: người dùng chuyển sang duyệt kiến trúc interactive trực tiếp #133.
Không suy health thành cloud E2E hoặc latency ≤4 s. Không push Git.
