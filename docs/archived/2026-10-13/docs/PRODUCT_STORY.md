# RAV-26 — chọn dữ liệu để gán nhãn, có người duyệt

Cập nhật 2026-10-09. Bản giới thiệu cũ đã lưu trong [archive](../../product/PRODUCT_STORY_2026-09-27.md). Scope và quyết định theo [CONTRACTS](../../../design/CONTRACTS.md) và [decision log](../../../research-pack/06_project_state/01_confirmed_decisions.md).

Kỹ sư Data/ML có nhiều video cần chọn một số frame trong ngân sách K để gửi gán nhãn. App đề xuất từng đợt, cho xem ảnh/ngữ cảnh và bằng chứng; kỹ sư keep/drop, rồi tạo snapshot để xuất một tập dữ liệu xác định. Dataset ảnh tham chiếu giúp so với dữ liệu đã có; model tham chiếu chỉ được khai báo train/finetune trên dataset khi có căn cứ.

Bản local đã có video pool, dataset/version tham chiếu, snapshot, DINO/pHash, CLIP query explicit, YOLO trên đợt chọn, recipe/evidence và export snapshot. Upload video chỉ lưu; preparation DINO hoặc CLIP là thao tác riêng theo CONTRACTS §20. Goal tự do chưa phải bộ tiêu chí do LLM hiểu; chất lượng ngữ nghĩa text recipe còn chưa đạt trong [đánh giá V2](evaluation/V2_CRITERIA.md). Không claim mAP, rare-event recall hoặc hiệu quả downstream khi chưa có thí nghiệm.

[PLAN](../../../../PLAN.md) ghi tiến độ triển khai. Bản hiện tại vẫn chịu [giới hạn local một người](../../../design/LOCAL_SINGLE_USER_LIMITS.md), chưa đủ để mở nhiều tài khoản an toàn.

Mục tiêu deployment là bản thử 2–3 người: Supabase email/password do operator cấp; dữ liệu và phiên thao tác riêng mỗi user. Nguồn system/demo và phiên/lịch sử mẫu được mọi tài khoản xem **chỉ đọc** để học cách dùng. Bắt đầu từ mẫu chỉ tạo **phiên riêng trống**, cùng nguồn/cấu hình khởi tạo; không chép lịch sử/kết quả hoặc tự đề xuất. User thao tác mới tạo lịch sử của mình.

Render giữ frontend và API nhẹ; Contabo xử lý worker và điều phối Kaggle; dùng S3/Supabase/Kaggle hiện có. Credential bootstrap đã có trong `.env` repo; operator đọc explicit và cấu hình environment theo host/role. S3 giữ file, Supabase giữ metadata/queue. Preview raw cần quyền. Khi export, worker che mặt/biển số và tạo report từ đúng snapshot; owner xem/xác nhận trước tải ZIP. Nguồn còn được phiên/snapshot/job dùng bị chặn xóa. Thiết kế chi tiết và task song song ở [execution plan](../../../backlog/DEPLOYMENT_EXECUTION_PLAN.md), hiện còn draft D0.

Bootstrap hạ tầng có thể đi trước để đo. BDD subset/nano reference chưa được provision; cần tìm sau bootstrap và hoàn thiện provenance/PII/capacity/cost gates trước mở đủ bản thử. [Đề bài gốc](research-pack/01_origin/01_original_rav26_problem.md) và [alignment](../../../design/RAV26_ALIGNMENT.md) vẫn là căn cứ phạm vi dài hạn.
