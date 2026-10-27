# Perception Instability — release QA, 2026-10-15

Worker source: `86c10ab7f50423e9dd2fe8537d0c1b4f2ebdb2e6`; integrated with VPS upload release before build.
FE/API hotfix source: `e746d70a0b5c4ae389d0e3c89d2421c5427a10fa`; details below.
Contract: [§21.31](../design/CONTRACTS.md); VPS upload retains §21.30.

## Fixes verified

- Background PROPOSE is routed to the existing YOLO dispatcher/slot when TEXT serves Interactive HTTP;
  no new slot, CPU thread, policy or schema migration.
- Model must be INFERENCE_VERIFIED and task detect. Training-dataset recipes remain gated.
- API retains per-unit matching evidence and uncertainty summary; unobserved candidates are masked.
- Inference cache persists across jobs. Predictions are decoded in source/time order and returned in candidate order.
  Cancellation is checked between candidates; progress is reported after completed inference/cache lookup in groups of eight.
- Interactive drafts/batches are checkpointed before enqueue; reopening Studio or finishing a background job refreshes
  its owner/session RAM context, keeping batch numbers aligned with persisted proposals.
- Grid and frame viewer use original proposal boxes; comparison boxes remain optional. Global M toggles grid boxes.

## Fresh automated checks

- 51 core/perception/worker-lane tests passed, including chronological decode with preserved output order.
- PostgreSQL isolated regression: 28 passed, 2 skipped (local real-YOLO fixture filename missing).
  This includes a real queue/worker/publication/API/cache test with only detection mocked.
- Interactive refresh/checkpoint regression after final schema fix: 7 passed on PostgreSQL/Moto.
- TypeScript and 38 frontend tests passed; the added SSR test proves original proposal boxes render without a separate YOLO batch,
  while comparison boxes stay hidden. Linux combined image runs TypeScript/tests/build; candidate health/routes and worker registry pass.
- Ruff and diff check pass. An initial Windows regression with unrestricted BLAS threads exceeded a test-only CPU allowance;
  rerun with one thread, matching worker budget, passed. Full backend suite was not run.

## Real cloud smoke and timing

QA account only; recorded comma.ai dashcam already in S3, 60 s, 1164×874, 240 units at 4 FPS.
DINO was prepared by the real Kaggle job before recipe testing. Model: YOLO11n COCO (Ultralytics), checkpoint SHA256
`0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1`.
No claim that this model was trained on the tested video or BDD100K.

32 candidates / requested batch 16, via authenticated public Render API and Contabo worker:

| Run | End-to-end seconds | Result |
|---|---:|---|
| Baseline before chronological decode | 279.313 | 16 picks, real model |
| Fresh inference cache after fix | 70.516 | 16 picks, matching evidence |
| Same request inputs, new job | 28.532 | Same picks; 32 inference-cache files and mtimes unchanged |
| After one Keep and one Drop | 56.968 | 16 picks; both decided frames excluded |

These are single-run observations, not an SLA. Timing includes API/enqueue/poll/readback; raw/model disk caches existed in the optimized run.
The 32 baseline derivative cache entries were preserved in a separate directory, not deleted, before the fresh-inference benchmark.
Measurements were taken on `86d95ac0`; inference code is unchanged in final source (subsequent fixes concern Studio refresh and box display).

Authenticated retry of the same idempotency key returns the same job. Progress phases/counts were observed through the public job API.
Per-unit evidence includes scalar/observed/matching and original/transformed detections.
Interactive → background → Interactive smoke passed with persisted/temporary batch numbers 4 → 5 → 6.
VPS transport regression: capability upload of five bytes, then abort, passed without creating a READY source.
Container sample during baseline inference: CPU 103.86%, RAM 948 MiB of 6 GiB; this is a sample, not peak capacity evidence.

## Final live identity and remaining limits

Final Render deploy: `dep-db8e6kdg1s2s739vu5n0`, https://c4-app-026.onrender.com/app.
Switch smoke repeated on the final image passed with batch numbers 6 → 7 → 8.
Health/readiness, landing/login/app return 200; worker healthy at the exact image below; no active jobs and all slots FREE.
API environment compared equal before/after rollout; policy stays enabled at `vps-upload-20261015`; no schema migration.
Final combined digest:
`sha256:df67734378a9c316dd027cccd8fde6f11de6e644b46a4366e0f2418ae93c0c67`;
worker image `sha256:5949acfa7952d4a3735884c16ed81d93a1a77ce262dfba3edc91c14fc24a6206`.

No manual browser acceptance, long-video/multi-user capacity test, ground-truth accuracy or uplift evaluation.
Build retains its existing >500 kB bundle warning. No Git push; image publication/deployment was authorized separately.
## Hotfix mở lại batch cũ (2026-10-15)

Phiên live có proposal interactive chỉ lưu envelope `{explain, scores, relaxed, interactive_basis}`,
không có `units`. Adapter đã coi envelope là evidence per-unit, khiến Studio gọi `.some()` trên object/boolean.
Sửa tuân thủ DTO hiện có: evidence chỉ gồm danh sách theo unit thuộc picks; metadata giữ ở trường riêng.
Kiểm hồi quy envelope cũ, null/empty units, flat evidence và evidence perception; xác minh mở lại phiên sau rollout.

Hotfix checks: 14 perception tests, 43 frontend tests, Ruff toàn src/tests, tsc và build đạt.
Test hồi quy mới thất bại trên adapter cũ rồi pass sau sửa. Combined Linux candidate health/routes và registry đạt.
Deploy FE/API `dep-db8edi3rjlhs73e36g8g`, digest
`sha256:651b91c66511f7e9efe0394a56caf7f105fce4714ba35872f91063e8a85d40e3`.
Giữ nguyên worker image, API env, schema và policy. Health/ready/landing/login/app đều 200; worker healthy.
Chrome reload đúng phiên báo lỗi, hiển thị batch 13 đủ 16 ảnh và 23/100 đã giữ, không còn error boundary.
Public API QA chỉ đọc 7 batch: 5 perception vẫn giữ detections, 2 interactive không trả envelope thành per-unit.
Harness ban đầu đòi summary trên mọi batch là sai (summary chỉ có khi thiếu frame có tín hiệu), đã sửa điều kiện.
Supabase SSL trên máy local bị reset ở lần kiểm lại; cùng QA API smoke chạy qua mạng worker VPS đạt.
Không chạy lại inference, không sửa quyết định của người dùng, không push Git.

