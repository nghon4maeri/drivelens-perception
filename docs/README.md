# Tài liệu deployment DriveLens (RAV-26)

Cập nhật 2026-10-17 (architecture theo live `fc53ed8`). Docs active tập trung deploy; local MVP, kế hoạch đã hoàn thành và nghiên cứu lịch sử
được bảo toàn trong [archive](archived/2026-10-13/README.md). Archive không là trạng thái live hay permission implementation.

## Nguồn sự thật

| Cấp | Tài liệu | Vai trò |
|---|---|---|
| Sản phẩm/quyết định | [Product](PRODUCT_STORY.md), [alignment](design/RAV26_ALIGNMENT.md), [decision log](research-pack/06_project_state/01_confirmed_decisions.md) | Scope và quyết định đã chốt |
| Thiết kế | [CONTRACTS](design/CONTRACTS.md), [architecture](architecture_diagram.md), [UI](design/UI_DESIGN.md) | Code phải theo; §21 là deployment, core contracts vẫn dùng chung |
| Thực thi/bằng chứng | [PLAN](../PLAN.md), [guide](IMPLEMENT_GUIDE_V2.md), specs, runbooks, QA và code | Theo thiết kế; status/QA phân biệt live và candidate |

Không tự cắt contract hoặc đổi thiết kế khi dọn docs. Các core interfaces còn được worker dùng giữ trong CONTRACTS;
component/local guides chuyển archive để tra khi sửa core, không coi local API/SQLite là runtime pilot.
[Component navigation](components/README.md) giữ đường dẫn đọc cho shared worker/core.

## Đọc theo việc

| Việc | Tài liệu active |
|---|---|
| Hiểu tổng quan và luồng chi tiết | [Architecture](architecture_diagram.md), [deployment status](setup/DEPLOYMENT_STATUS.md) |
| Chọn lát/code/test | [PLAN](../PLAN.md), [execution plan](backlog/DEPLOYMENT_EXECUTION_PLAN.md), [guide](IMPLEMENT_GUIDE_V2.md) |
| Quyền, metadata và phiên | [Identity/data spec](design/DEPLOYMENT_IDENTITY_DATA_SPEC.md), CONTRACTS §21.1–4/10–11 |
| Queue/storage/quota/recovery | [Runtime/storage spec](design/DEPLOYMENT_RUNTIME_STORAGE_SPEC.md), CONTRACTS §21.5–7/9/20–22 |
| Scope, UI, PII/report | [Pilot spec](design/DEPLOYMENT_PILOT_SPEC.md), [UI/export/ops spec](design/DEPLOYMENT_UI_EXPORT_OPS_SPEC.md), [UI](design/UI_DESIGN.md) |
| Build/release/rollback | [Combined Render](setup/COMBINED_WEB_RELEASE.md), [Contabo](setup/CONTABO_WORKER_RELEASE.md), [runtime runbook](setup/PILOT_RUNTIME_RUNBOOK.md), [frames release](setup/FRAMES_RELEASE.md) |
| Agent dev/CI không có production web | [Dev/CI guide](setup/DEV_CI_GUIDE.md) |
| Theo dõi latency | [Operations](setup/OPERATIONS_RUNBOOK.md), [upload logs](setup/UPLOAD_LATENCY_LOGS.md), [latency QA](setup/LATENCY_QA.md) |
| Interactive proposal live | [#133 QA/rollout](setup/INTERACTIVE_CPU_QA.md), CONTRACTS §21.26 |
| Release/test evidence | [Frames cloud](setup/FRAMES_CLOUD_QA.md), [basic stability](setup/BASIC_FLOW_STABILITY_QA.md), [compute QA](setup/COMPUTE_RUNTIME_QA.md) |
| Compute candidate và model | [Runtime](setup/COMPUTE_RUNTIME.md), [CLIP/SigLIP2/Gemma4](research/ENCODER_RUNTIME_REVIEW.md) |
| Việc còn thiếu | [Proposal latency](backlog/PROPOSAL_LATENCY_PLAN.md), [N1–N6](backlog/POST_BASIC_FLOW_DEVELOPMENT_PLAN.md), [architecture solutions](backlog/ARCHITECTURE_SOLUTIONS.md) |
| Giới hạn dev/local khác deploy | [Local limits](design/LOCAL_SINGLE_USER_LIMITS.md) |
| Kiểm chứng dọn docs/archival | [Docs refresh QA](setup/DOCS_REFRESH_QA.md) |
| Kiểm chứng audit test/docs và dọn template | [Audit cleanup QA](setup/AUDIT_CLEANUP_QA.md) |
| Candidate tối ưu proposal/DB và cách test local | [N1 local QA](setup/PROPOSAL_LOCAL_QA.md) |
| Release QA gần nhất (live) | [ODD phase/cache](setup/ODD_PHASE_CACHE_QA.md), [mẫu tham chiếu](setup/ADMIN_SAMPLE_REFERENCE_QA.md), [ODD coverage](setup/ODD_COVERAGE_RELEASE_QA.md), [đăng ký/kho mẫu](setup/SAMPLE_CATALOG_RELEASE_QA.md), [admin model/PII](setup/ADMIN_PII_RELEASE_QA.md), [nhật ký admin](setup/ADMIN_LOGS_RELEASE_QA.md), [strategy](setup/STRATEGY_RELEASE_QA.md), [upload VPS](setup/VPS_UPLOAD_RELEASE_QA.md) |
| QA candidate/lát khác | Các file `setup/*_QA.md` còn lại; mỗi file ghi rõ local/candidate hay live |

## Quy ước

Ngày/source/image/môi trường và giới hạn evidence phải rõ; không trộn test mocked với cloud/model thật.
Không để secret, URL ký hoặc dump DB trong Git. Archive giữ quyết định/yêu cầu chưa làm, không xóa scope.
Đổi thiết kế theo AGENTS; giả định local ghi local limits + comment local-only; quyết định mới ghi decision log.
