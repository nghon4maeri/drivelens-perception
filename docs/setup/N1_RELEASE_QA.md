# N1 release — 2026-10-14

Người dùng yêu cầu deploy bản mới nhất. Source `bbd7d7a265c712aac0ba32ace85c3bf6fa0a21cb`,
bao gồm compute #127 và N1 draft/batch/read/publish #129. Git không push/merge main.

## Release identity và compatibility

- Render service `srv-db68u7d9fdbs73f8v890`, deploy `dep-db7hceu7bikc73atesag`, Live.
- Combined registry digest `docker.io/tonmeu/rav26@sha256:0cbbf62c6f0bfe274fbaaff6298915b375cd567ec82c86955f9cc0e84155147a`.
- Worker local immutable image ID `sha256:f4c0f2b1724622414533eb105aed9e5087dcbd38ebe9027459f43883b3ff6082`,
  tag `rav26-worker:n1-bbd7d7a`, container `rav26-worker-worker-1` healthy. ID không phải registry digest worker đã push.
- Schema tới 010, policy `frames-test-20261013`, CPU/MEDIA/GPU slots và giới hạn 3 CPU/4 GiB giữ nguyên.
  CPU lanes chưa bật; cần mixed-load/capacity gate riêng. Không backfill, GPU/PII/export hay model switch.
- Source archive chỉ tracked src/scripts/requirements/build files, không secrets/data/output.
  Build trên Contabo vì Docker Desktop local không chạy; combined build kiểm tsc/Vitest/build,
  smoke `/health`, `/login`, UID 1000 đạt. Regression local trước release tại [N1 QA](PROPOSAL_LOCAL_QA.md)
  và [dashcam QA](DASHCAM_LOCAL_QA.md).

Backup DB trước rollout: `output/n1-release/pilot-before-frames.dump`, 1,019,196 bytes,
SHA256 `14fed60f62dcc9686c716f74cbf96d2b5352e5f5c6b0839f5e9ced147011edde`.
Active jobs=0, tất cả slots FREE, không pending worker objects trước chuyển.
Đã pause admission và stop worker khi đổi; mở lại đúng enabled state khi worker mới chạy.
Script rollout ban đầu thay token cả tên biến RAV_WORKER_IMAGE gây assertion trước sửa config;
đã hoàn tất bằng `finish_cutover.py`. Không dùng exit fail của script đầu làm bằng chứng release thành công.

## CLIP text

Tái hiện trên VPS: offline load lỗi `NoneType` tại `.endswith`
khi checkpoint chưa đầy đủ; online tải lỗi Permission denied trong HF Xet cache.
`data/system-cache` do root sở hữu, worker UID 1000 chưa tạo được thư mục HF bên dưới.
Đã tạo riêng `data/system-cache/huggingface/xet/logs` và các parent cần thiết với UID/GID 1000,
không chown toàn bộ dữ liệu. Provision `openai/clip-vit-base-patch32` revision
`3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268` vào cache bền hiện tại.

Image mới và chính container live đều chạy text inference offline đạt `(1, 512)`;
evidence `output/n1-release/clip-preflight.log`, `clip-live.log`. Không đổi checkpoint/embedding space.
Đây là kiểm loader/inference, chưa là authenticated browser text_diversity proposal toàn luồng,
chưa đối chiếu được job lỗi cụ thể của người dùng vì chưa có job ID.

## Post-release checks

`/health`, `/health/ready`, `/login`, `/app` trả 200; `/api/v2/jobs` không auth trả 401.
Render API xác nhận đúng deploy Live/image digest; worker inspect đúng image/healthy.
Evidence `output/n1-release/live-verified.json`.

Smoke tạo nguồn tổng hợp 8 s riêng, dùng operator services với Supabase/S3/Contabo thật;
không thay dữ liệu nguồn/phiên có sẵn. Public Render HTTP riêng, không coi là authenticated browser E2E.
Smoke **passed**, exit 0: VALIDATE_UPLOAD/RENDER_MEDIA/DELETE_SOURCE SUCCEEDED,
usage FINAL, cleanup_done; raw + 64 ảnh đã xóa, cross-owner 404.
Upload READY 54.891 s, batch 16 URL READY 0.719 s (lookup, chưa gồm tải ảnh),
thumbnail lookup+fetch 2.125 s, full frame 2.109 s, clip cold 33.218 s,
clip warm lookup 0.797 s, delete 41.5 s. Một video tổng hợp 8 s, không p95 hoặc benchmark trước/sau.
Kết quả lưu `output/n1-release/cloud-smoke.json` và exit/log; latency upload/clip vẫn cao.
Không lấy số lookup operator services làm latency HTTP Render có auth.

## Rollback

Combined trước: `docker.io/tonmeu/rav26@sha256:4d2fd220a1cc4557f9ade7ceec63c912c0ca8c7c5fbc910db0b5193c5d8c23a4`.
Worker trước: `rav26-worker@sha256:13dafd8a5d6fadd9ac3b553719d3717cbe7b0dac41ceb016c86e11a35fb963dc`.
Config copies root-only: `/opt/rav26-worker/output/pre-n1-bbd7d7a-{release.env,worker.env,compose.worker.yml}`.
Render trước lưu `output/n1-release/render-before.json`. Không in/copy secrets vào docs.
Trước rollback drain/stop và kiểm payload compatibility, không restore DB/drop schema chỉ để đổi image.
Cache provisioning giữ nguyên, không xóa checkpoint. Gate capacity/quality/cloud proposal latency vẫn riêng.
