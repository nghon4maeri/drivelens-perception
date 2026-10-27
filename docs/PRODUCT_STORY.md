# DriveLens (RAV-26) — chọn frame để gán nhãn, có người duyệt

Cập nhật 2026-10-13 (#128). Scope theo [CONTRACTS](design/CONTRACTS.md) và [decision log](research-pack/06_project_state/01_confirmed_decisions.md).

Kỹ sư Data/ML nạp video, chuẩn bị ảnh/đặc trưng, chọn recipe và xem từng đợt đề xuất; keep/drop rồi đóng băng
snapshot. Mục tiêu là chọn dữ liệu trong ngân sách gán nhãn, có provenance và evidence để người dùng quyết định.
Không suy novelty/confidence/keep rate thành mAP hoặc lỗi model khi chưa có ground truth/thí nghiệm downstream.

Pilot đang chạy FE/API gộp Render, metadata/auth trên Supabase, file private S3, worker Contabo và GPU coordinator Kaggle.
User chỉ xem/sửa dữ liệu và phiên của mình; nguồn SYSTEM và phiên mẫu shared chỉ đọc. Fork mẫu tạo phiên riêng trống
cùng nguồn/cấu hình, không chép history hoặc tự đề xuất. [Status và giới hạn](setup/DEPLOYMENT_STATUS.md).

Upload chuẩn bị ảnh S3 trên grid mặc định 4 fps; thumbnail/frame đọc ảnh đã lưu, clip ±3 giây còn dùng raw.
DINO/CLIP chuẩn bị explicit, proposal dùng feature artifact; YOLO trên proposal cũng explicit.
Luồng cơ bản đã smoke cloud nhưng latency upload/clip/publish còn cần tối ưu; compute lanes mới là candidate đã test.

Dataset/model tham chiếu và recipe/model-guided đầy đủ trên pilot, report theo model binding, PII export/confirm,
quality/frame đại diện và vòng gán nhãn/retrain ngoài hệ thống là phần tiếp theo trong [PLAN](../PLAN.md).
Không tự gọi hệ thống hiện tại là closed-loop active learning hoặc training platform hoàn chỉnh.
Export chỉ mở sau handler/quality gate mặt và biển số, preview/report đúng snapshot và owner xác nhận.

[Đề bài gốc](archived/2026-10-13/docs/research-pack/01_origin/01_original_rav26_problem.md),
[alignment](design/RAV26_ALIGNMENT.md) giữ yêu cầu dài hạn; [local evaluation](archived/2026-10-13/docs/evaluation/V2_CRITERIA.md)
chỉ là evidence ở checkpoint cũ, không chứng minh chất lượng deployment mới.
