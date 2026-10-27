# Compute runtime — kiểm chứng candidate 2026-10-13

Lát #127, [cấu hình](COMPUTE_RUNTIME.md), [encoder evidence](../research/ENCODER_RUNTIME_REVIEW.md).
Đây là kiểm chứng code candidate, chưa là nghiệm thu capacity hoặc rollout production.

## Contabo thật

- Host 4 vCPU/~8 GiB; namespace `/opt/rav26-test/compute-runtime-20261013`.
- Image build từ source cuối: `rav26-worker:compute-20261013`, ID
  `sha256:cb86415c13597dce3c21a4c5565fa9e317a7e04c856762becff2ba6fb7c1769b`.
- Test container cap 4 CPU/6 GiB, PostgreSQL 17 và network riêng, test dependencies riêng;
  không truyền production env/DB/S3 credentials. YOLO public COCO checkpoint, ảnh tổng hợp.
- **95 passed trong 119,61 s**: lanes/config/budgets, actual supervisor health/restart/shutdown,
  ba process nhận ba job đồng thời, policy/owner heavy limits, QUARANTINED slots,
  CPU/GPU lifecycle regression, text cache và YOLO thật. Log local `output/compute-runtime/contabo-complete.log`.
- Probe ba job dùng handler kiểm scheduling rồi chủ động FAILED; kiểm FINAL usage, cleanup và slot FREE.
  Nó không chứng minh ba model AI hoàn chỉnh chạy đồng thời. PII handler/quality gate chưa có.
- Model-owner check thực tế: CLIP text parity max error 5,96e-8; YOLO warm gọi lại không load model lần hai,
  predictor giữ Torch threads=1. Encoder timing/RSS và giới hạn benchmark nằm trong báo cáo encoder.

## Local Windows

PostgreSQL 16.15 thật, S3 moto, test dependencies dưới `output/compute-runtime/testdeps`;
cache/temp tại repo. Hook test được loại rõ ràng vì lỗi PowerShell đường dẫn có dấu đã ghi trong AGENTS.md.
Suite source cuối **518 passed, 5 skipped, 1 warning trong 401,46 s**, exit code 0:
`python -m pytest tests -q --ignore=tests/test_codex_hook.py --basetemp=output/pytest-tmp-compute-complete2`.
Năm skip: symlink Windows, hai test thiếu checkpoint YOLO local và hai test Linux-only.
Warning là Starlette/AnyIO deprecated alias. Contabo đã kiểm YOLO thật và các case Linux liên quan riêng.
Log `output/compute-runtime/complete-suite2.log` và exit `complete-suite2.exit`.
Lượt sandbox đứng đã dừng đúng tiến trình pytest rồi chạy lại với quyền test socket local;
không dùng lượt chưa có summary làm bằng chứng pass.
Ruff `src tests scripts/benchmark_encoders.py` và `git diff --check` đạt. Không đổi frontend.

## Ý nghĩa và gate còn lại

Đã có khả năng opt-in phân CPU thành TEXT/YOLO/BULK với process giữ model, MEDIA riêng,
kiểm budgets/policy slots/quota và restart đúng process. Chưa bật cấu hình này trên live.
Worker live vẫn image `rav26-worker@sha256:13dafd8a5d6fadd9ac3b553719d3717cbe7b0dac41ceb016c86e11a35fb963dc`,
healthy, cap 3 CPU/4 GiB; Render và policy production không đổi.

Trước rollout cần tải hỗn hợp upload/decode/clip/proposal/YOLO/GPU hydrate để đo peak RSS, queue p95,
CPU và latency UI; provision slots/policy version có kế hoạch drain/rollback theo runtime guide.
Không có kiểm S3/Supabase/Kaggle thật trong suite candidate này, không kiểm native systemd,
không benchmark retrieval ADAS hoặc thực hiện model PII. DB publish round trips thuộc lát khác.
Docker Linux không tạo VM riêng cho mỗi model; chưa có evidence cần chuyển native.
