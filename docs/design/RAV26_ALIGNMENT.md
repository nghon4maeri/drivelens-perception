# Alignment — yêu cầu RAV-26 và deployment gates

Cập nhật #128. [Đề bài gốc](../archived/2026-10-13/docs/research-pack/01_origin/01_original_rav26_problem.md),
[PRD scope snapshot](../archived/2026-10-13/docs/research-pack/01_origin/02_original_prd_scope_snapshot.md),
[ma trận P/U/G và evaluation lịch sử đầy đủ](../archived/2026-10-13/docs/design/RAV26_ALIGNMENT.md).
Không bỏ yêu cầu gốc khi archive; không dùng kết quả MVP để tick cloud/semantic/downstream gate mới.

| Yêu cầu | Hiện trạng pilot | Gate còn lại |
|---|---|---|
| Chọn K frame ít trùng cho gán nhãn | Feature/recipe/selector/proposal có code; user duyệt keep/drop | Baseline cùng K/seed/video và ND-rate độc lập, không tự suy annotation saving |
| ODD/rare case và relevance theo goal | DINO/CLIP/evidence có; text query explicit | Labeled event/relevance evaluation; local V2E ngữ nghĩa chưa đạt, không claim hiểu goal |
| Dataset/model tham chiếu | Shared core/local có version/model primitives, pilot SYSTEM YOLO verified inference | Multi-user catalog/provenance/binding/recipe/report N3–N5; train relationship cần bằng chứng |
| Human decision và provenance | Append-only decisions, frozen proposal/snapshot/context hashes | Binding/report end-to-end và label/retrain outside lineage |
| Multi-user quyền và dữ liệu | Auth/RLS/owner/CAS, private S3, templates readonly/fork empty | Browser expiry/A-B/refresh/CAS cloud acceptance đầy đủ |
| Luồng dữ liệu mượt | Frames source b195c61 live, basic cloud smoke; compute966ab44 candidate test | DB/poll/publish optimization và mixed-load p50/p95/RSS/cost trước capacity claim |
| PII mặt/biển số và export | Contract preview/report/hash/owner-confirm đã chốt; handler chưa có | Model/quality/fault/recovery/storage/quota và confirm E2E trước mở |
| Mô hình tham chiếu report/downstream | YOLO detections/metrics hiện là evidence | Ground truth/class map/split cho accuracy; retrain + eval cho mAP uplift, không metric giả |
| Vận hành/deploy chuyên nghiệp | Combined Render, Contabo supervisor, migration/checksum, receipts/tracing, candidate isolation | Restore drill, CI image/health release, compatibility/drain/rollback, Kaggle provider thật |

[PLAN](../../PLAN.md), [status/QA](../setup/DEPLOYMENT_STATUS.md), [execution gates](../backlog/DEPLOYMENT_EXECUTION_PLAN.md)
giữ trạng thái hiện hành. Closed-loop active learning, annotation editor, training service/auto-retrain không tự thành đã implement.
