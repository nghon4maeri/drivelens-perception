# DriveLens — mô tả nộp bài (team P-026 / IrishCoffee)

App: [c4-app-026.onrender.com](https://c4-app-026.onrender.com) · tài khoản demo: nút **Nạp tài khoản demo** ở `/login`.

## 1. Bài toán

Đội ADAS/robotics thu hàng nghìn giờ video dashcam, nhưng ngân sách gán nhãn chỉ đủ cho một phần nhỏ. Chọn tay hoặc lấy ngẫu nhiên thường:

- lấy quá nhiều frame gần giống nhau (xe đứng đèn đỏ, cao tốc thẳng);
- bỏ sót điều kiện hiếm (đêm, mưa, công trường, người đi bộ băng ngang);
- không có bằng chứng định lượng rằng batch đã chọn tốt hơn ngẫu nhiên;
- phải tự che mặt, biển số trước khi gửi dữ liệu cho bên gán nhãn.

**Người dùng:** kỹ sư Data/ML phụ trách dataset. **Mục tiêu:** chọn ít frame hơn nhưng đa dạng hơn và đúng nhu cầu. Có người duyệt và có provenance. Export phải an toàn về quyền riêng tư.

## 2. Giải pháp kỹ thuật

**Luồng chính:** upload video → tách frame 4 fps và tính đặc trưng → hệ thống đề xuất từng đợt → người dùng keep/drop → đóng băng snapshot → export ZIP đã che PII.

| Thành phần | Công nghệ |
|---|---|
| Web (landing, Studio, Operations) | React + Vite, cùng origin với API trên Render |
| API | FastAPI: JWT Supabase, phân quyền, ký URL S3, quota |
| Dữ liệu | Supabase PostgreSQL (metadata, hàng đợi job, RLS), S3 private (video, frame, ZIP) |
| Worker | Contabo VPS, Docker: upload ingress có resume, các lane TEXT/YOLO/BULK/MEDIA có trần CPU/RAM riêng |
| Model | DINOv2-S (đa dạng hình ảnh), CLIP (tìm theo câu chữ), YOLO11 (độ bất ổn phát hiện), YuNet + model biển số (che PII). Kaggle GPU là tùy chọn |

**Chiến lược đề xuất:**

- `diversity` / `text_diversity`: greedy max-min trên embedding DINOv2, cộng cổng ngữ nghĩa CLIP theo câu tìm/loại trừ. Có NMS thời gian 1 s để không lấy frame liền nhau.
- `odd_coverage`: người dùng khai báo điều kiện vận hành, đối tượng và kịch bản. CLIP chấm độ liên quan so với prompt đối chứng, DINO đo độ mới. Mỗi frame được chấm `0.35·liên quan + 0.40·mới + 0.25·độ phủ nhánh`, rồi chọn tham lam.
- `perception_instability`: chạy YOLO trên ảnh gốc và ảnh lật để đo độ không nhất quán, kết hợp với độ mới DINO. Dùng để tìm frame model đang yếu.
- Baseline `random` / `uniform` để so sánh công bằng.

**Đáng tin cậy:**

- Mỗi đề xuất kèm điểm và evidence.
- Snapshot đóng băng theo hash.
- Export chỉ mở sau khi qua bước che mặt và biển số, và chủ sở hữu xác nhận đúng snapshot.
- Phân tích đo ND-Rate@K (tỉ lệ cặp gần trùng, đo bằng pHash nên độc lập với embedding dùng để chọn), độ phủ thời gian và cụm, cùng bản đồ embedding.

## 3. Tính khả thi

- **Đang chạy thật:** đăng ký/đăng nhập, upload lớn có resume, Studio, các strategy nền, phân tích YOLO, export che PII, trang admin. Đã smoke trên cloud: một video đi hết luồng tới S3 (480 ảnh READY) trong 165 s.
- **Số đo sơ bộ** (video dashcam 5 phút, pool 1.200 frame, 5 seed): ND-Rate@200 là 5,7% ± 1,1% với random, 1,5% với uniform và 0% với diversity. Trên video 25 s, K=50: random 46,8%, diversity 24%.
- **Chi phí thấp:** một VPS CPU 8 GB cùng các gói free/hobby của Render và Supabase. Worker có trần RAM theo lane. Các phép tính trên cả kho (pairwise) được chia theo khối 512 frame, để kho khoảng 12 nghìn frame vẫn nằm dưới trần RAM.
- **Kiểm thử:** khoảng 450 test pytest, ruff, tsc. Mỗi lần release đều ghi image digest và cách rollback.
- **Giới hạn nói thẳng:**
  - Chưa có ground truth, nên không claim tăng mAP.
  - CLIP với ngưỡng mặc định còn trả false positive cho nội dung vắng mặt.
  - YOLO trên CPU chậm với kho lớn (khoảng 30 phút cho 12 nghìn frame).
  - Chưa benchmark 5 GiB, nhiều người dùng đồng thời, hay recall của bước che PII.

## 4. Hướng phát triển

1. **Đóng vòng active learning:** đưa nhãn trả về và model retrain vào lại hệ thống, đo uplift mAP thật trên BDD100K hoặc nuScenes.
2. **Tăng tốc:** chạy YOLO và embedding trên GPU (Kaggle hoặc GPU thuê theo job), cache kết quả theo model, tách worker theo tải.
3. **Tìm theo ngữ nghĩa tốt hơn:** thay CLIP bằng PE-Core, hiệu chỉnh ngưỡng theo từng kho, mở rộng câu truy vấn bằng LLM.
4. **Quy mô:** nhiều người dùng, quota GPU, dataset tham chiếu để đo độ mới so với tập train, và các recipe dựa trên model (`model_guided`).
5. **Tích hợp:** export sang CVAT / Label Studio, đo recall của bước che PII, bổ sung backup/restore và CI release.
