# Docs deployment refresh — QA #128

2026-10-13, docs-only sau compute candidate commit 966ab44. Không đổi code/runtime/schema/policy/live.
[Manifest](../archived/2026-10-13/README.md), [docs index](../README.md), [architecture](../architecture_diagram.md).

## Phạm vi

41 tài liệu được chuyển sang archive: local component/dataset/cloud/evaluation, research/origin/source indexes,
plan/handoff đã hoàn thành và audit snapshot. 16 snapshots giữ bản trước rewrite: root README/PLAN,
docs index/product/guide/architecture/UI/alignment/local boundary/status/combined runbook/execution plan và bốn specs D0.
Archive index và README_LOCAL_HISTORY tách tên để không đè bản README local.
CONTRACTS shared core/§21 và decision log vẫn active. components/README chỉ còn navigation tới guides archive.
Yêu cầu chưa làm giữ roadmap, không bị cắt scope.
JSON labels, demo inventory và SQL compatibility vẫn giữ đường dẫn do code/scripts dùng; không đổi code chỉ để dọn docs.

## Kiểm tra

- Đối chiếu **57 bản lưu với Git HEAD 966ab44**: nội dung giữ nguyên ngoài relative links và đường dẫn chuyển archive.
- Kiểm local Markdown targets và GitHub-style anchors trong docs active, đợt archive mới và root navigation;
  tất cả không lỗi. Không kiểm lại mạng/provider/external URLs hoặc toàn bộ archive template cũ.
- Rà consistency live b195c61/image frames/migration010/3CPU4GiB và candidate966ab44/model-owner/4CPU6GiB test;
  không dùng root HEAD docs mới làm source image đang live. Hướng tới PII/reference/capacity vẫn mở.
- Kiến trúc có bốn Mermaid diagrams: topology, data dependencies, upload sequence và worker configurations;
  rà cú pháp/content thủ công, không claim đã render bằng engine Mermaid.
- `git diff --check` đạt; diff chỉ Markdown. Không chạy lại application tests cho docs-only.
  518 local/95 Contabo tests là evidence source #127 ở [compute QA](COMPUTE_RUNTIME_QA.md), không là test mới của lượt này.

Script/report ignored tại output/docs-refresh, không commit secret/dump/output. Không push hoặc deploy trong lát #128.
