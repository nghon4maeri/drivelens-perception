# Deployment — UI, PII export/report và operations

Cập nhật #128. [D0 export payload/state/quality details](../archived/2026-10-13/docs/design/DEPLOYMENT_UI_EXPORT_OPS_SPEC.md)
giữ rationale/test inputs; current contract/UI làm authority, chưa được phép coi PII gate hoàn tất.

## UI hiện hành

[UI_DESIGN](UI_DESIGN.md) + CONTRACTS §21.16/19/20: same-origin Render, auth_generation/token refresh,
private Studio/template fork, job/quota/source/media states, no fake metric, operator operations read allowlist.
Draft decisions/submit batch/poll/publish optimization còn [proposal plan](../backlog/PROPOSAL_LATENCY_PLAN.md).
Dataset/model pilot flows và report model binding thuộc N3–N5, global catalog/export còn ẩn tới implementation.

## Export hướng tới, không có handler live

CONTRACTS §21.8: frozen snapshot → worker exact raw anchors → face/plate redact full-resolution → report_basis/preview
→ immutable payload_hash → owner confirm đúng hash → EXPORT_FINALIZE → ZIP S3 private/TTL.
Không OCR/NER thay scope mặt/biển số; không raw ZIP bypass gate, không confidence-only quality claim.
Export states REQUESTED/PROCESSING/NEEDS_CONFIRMATION/FINALIZING/READY; storage/quota/pins/fence/receipt/recovery cùng runtime contract.
Quality data face/plate/cảnh tối/motion/false-negative và manual evidence trước mở; handler/model lựa chọn phải duyệt đúng lát.
Không coi N5 model report đồng nghĩa PII export report hoặc metrics là accuracy khi thiếu labels/split.

## Operations và acceptance

Timing request/upload/job/attempt, UTC/monotonic/CPU, nested span không double-count; bounded telemetry/retention/cap.
Operator JWT+server allowlist chỉ đọc; không quyền raw của user khác hoặc endpoint điều khiển/quota bypass.
[Operations runbook](../setup/OPERATIONS_RUNBOOK.md), [upload logs](../setup/UPLOAD_LATENCY_LOGS.md),
[release matrix](../setup/DEPLOYMENT_STATUS.md), [acceptance](../backlog/DEPLOYMENT_EXECUTION_PLAN.md).
Browser auth/expiry/media/CAS/confirm E2E, PG/S3 fault/recovery, provider lifecycle và PII quality là các gate riêng.
