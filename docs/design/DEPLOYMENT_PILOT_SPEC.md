# Deployment pilot — scope và acceptance

Cập nhật #128. Scope đã khóa tại CONTRACTS §21 (#84 và các bổ sung); không còn coi toàn pilot là draft D0 chưa deploy.
[Spec gốc đầy đủ](../archived/2026-10-13/docs/design/DEPLOYMENT_PILOT_SPEC.md) giữ yêu cầu/acceptance lịch sử;
numeric estimates/provider snapshots cũ không tự dùng làm config. Current status/roadmap dẫn phía dưới.

## Scope đang áp dụng

Pilot 2–3 account operator cấp, Supabase password/login/reset closed; PRIVATE theo owner, SYSTEM/protected readonly.
Templates/history shared readonly; fork phiên riêng trống cùng nguồn/spec, không copy history/result hoặc tự propose.
Render FE/API gộp, Supabase auth/PostgreSQL metadata/queue/quota, S3 private direct transfers, Contabo worker, Kaggle GPU.
Host Contabo 4 vCPU/~8 GiB; live container3 CPU/4 GiB, compute candidate chưa rollout, bandwidth/capacity đo riêng.
Upload chuẩn bị ảnh S3, feature DINO/CLIP explicit; proposal/YOLO/metrics explicit. Exact grid/IDs/version không đổi ngầm.
Owner/RESTRICT/delete receipts, immutable policy/quota/fence/source pins và recovery là bắt buộc.

## Yêu cầu còn thiếu

Dataset/model version/provenance/binding và recipe/report đầy đủ pilot; PII face/plate export/report/owner-confirm gate;
browser/GPU/model/quality/mixed-load/cost/restore/CI acceptance. Không bỏ scope vì đã hosting Live.
Không claim accuracy/mAP từ confidence hoặc keep/drop; training/annotation outside, không tự auto retrain.
Quota/window/accounting theo CONTRACTS §21.6; numeric budgets từ policy measured version, không seed defaults tại boot.

## Tài liệu thực thi

[Identity/data](DEPLOYMENT_IDENTITY_DATA_SPEC.md), [runtime/storage](DEPLOYMENT_RUNTIME_STORAGE_SPEC.md),
[UI/export/ops](DEPLOYMENT_UI_EXPORT_OPS_SPEC.md), [CONTRACTS](CONTRACTS.md), [architecture](../architecture_diagram.md),
[status](../setup/DEPLOYMENT_STATUS.md), [execution gates](../backlog/DEPLOYMENT_EXECUTION_PLAN.md), [PLAN](../../PLAN.md).
Spec không tự cấp permission push/deploy/chạy GPU hoặc thay numeric policy.
