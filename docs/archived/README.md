# Tài liệu đã lưu trữ

Rà và chuyển ngày **2026-10-09**, theo yêu cầu người dùng ở PL6. 68 file gốc được move, kiểm SHA256 trước/sau; sau đó sửa liên kết và thêm nhãn lưu trữ. Không xóa bằng chứng hoặc lấy nội dung cũ làm contract hiện hành.

Đọc tài liệu đang dùng tại [docs/README](../README.md). Mọi file trong archived/ chỉ là lịch sử/tham khảo, kể cả câu “đã chốt/đang dùng” ở nội dung gốc; quyết định hiện hành tra decision log và SoT.

| Nhóm | Vì sao lưu trữ | Đọc hiện hành |
|---|---|---|
| [MVP gap mapping](planning/MVP_GAP_MAPPING.md) | Snapshot audit trước dataset/snapshot/CLIP/YOLO/S3 jobs; nhiều dòng “chưa có” đã được triển khai | [PLAN](../../PLAN.md), [CONTRACTS](../design/CONTRACTS.md) |
| [Multi-user plan](planning/MULTI_USER_RUNTIME_HOSTING_PLAN.md), [bandwidth/quota](planning/BANDWIDTH_AND_QUOTA_PLAN.md) | Còn host/provider chưa chốt, upload dataset user, PII trước preview và reset khác scope mới | [pilot spec](../design/DEPLOYMENT_PILOT_SPEC.md), [execution plan](../backlog/DEPLOYMENT_EXECUTION_PLAN.md) |
| [Platform roadmap](planning/CURATION_PLATFORM_ROADMAP.md) | Đề xuất rộng hơn bản thử: project/membership/invite/review. Giữ cho giai đoạn tương lai, chưa duyệt | Scope pilot theo spec; scope sau pilot vẫn trong PLAN/architecture §10 |
| [Guide làm lại MVP](planning/IMPLEMENT_GUIDE_V2_ORIGINAL.md) | Lệnh tạo nhánh/cherry-pick/xóa code và L1–L9 của lần làm lại đã qua | [guide hiện hành](../IMPLEMENT_GUIDE_V2.md) |
| [Product story cũ](product/PRODUCT_STORY_2026-09-27.md), [frontend README cũ](frontend/README_local_before_PL6.md) | Auto-P1, feature/screen inventory và hiện trạng cũ | [PRODUCT_STORY](../PRODUCT_STORY.md), [UI_DESIGN](../design/UI_DESIGN.md) |
| gate-1/ | PRD/brief/wireframe gốc; giữ origin, không dùng làm scope triển khai mới | [original problem](2026-10-13/docs/research-pack/01_origin/01_original_rav26_problem.md), [alignment](../design/RAV26_ALIGNMENT.md) |
| research/ | Audit OSS/market tại thời điểm đã ghi; không chứng minh version provider/tool hiện tại | Quyết định build/buy giữ trong decision log; nghiên cứu paper và đề bài vẫn ở research-pack |

Cập nhật #128 (2026-10-13): [đợt archive deployment](2026-10-13/README.md) chuyển local component/dataset/cloud/evaluation,
research/origin và plan hoàn thành; lưu snapshot trước khi thay architecture/PLAN/spec/UI/status.
CONTRACTS shared core/§21, architecture/UI deployment, status/runbooks/QA và roadmap liên quan deploy giữ active.
Decision log giữ toàn bộ lịch sử; yêu cầu chưa làm không bị xóa scope. Bảng PL6 phía trên là lịch sử đợt trước.

Khi thêm tài liệu thay thế: sửa index/links và ghi lý do. Không tạo hai plan hiện hành cho cùng một phạm vi; không xóa một yêu cầu tương lai chỉ vì lưu trữ draft cũ.

Ngày 2026-10-14: xóa 49 file template khóa học trong `templates/` (45 file Technical Book, 4 khung Markdown).
Không có runtime caller; đã sửa hai link tại JOURNAL và presentation README, lịch sử vẫn tra được trong Git.
Giữ nguyên archive của RAV-26 và bằng chứng QA. [Kết quả kiểm chứng audit](../setup/AUDIT_CLEANUP_QA.md).
