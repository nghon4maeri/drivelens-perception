# RAV-26 — PRD v2.1: Video Frame Sample Selection

> **LƯU TRỮ 2026-10-09.** Bản gốc: `docs/gate-1/RAV26_PRD_v2_1_MVP_3_weeks.md`. Chỉ tra lịch sử/tham khảo; đọc [index hiện hành](../../README.md), không dùng các lệnh/hiện trạng cũ như contract.


**Trạng thái:** Draft cập nhật Evaluation Plan · **Ngày:** 20/09/2026 · **Thời hạn MVP:** 3 tuần

## 1. Product summary

**Sản phẩm:** Công cụ chọn một tập nhỏ frame từ video/dataset ảnh có liên kết theo thời gian, ưu tiên **ít trùng lặp và có tính đại diện**, để kỹ sư kiểm tra và xuất tập dữ liệu phục vụ labeling hoặc nghiên cứu tiếp theo.

**Đối tượng:** Data/ML engineer, computer-vision engineer xử lý footage từ ADAS, giao thông hoặc camera quan sát.

**Problem statement:** Video có nhiều frame tương quan cao; trích xuất theo FPS cố định hoặc chọn ngẫu nhiên có thể giữ nhiều frame gần giống và bỏ lỡ một số tình huống khác biệt. Với ngân sách xử lý/gán nhãn có hạn, người dùng cần chọn Top-K frame và hiểu mức đánh đổi giữa giảm redundancy và giữ coverage.

**Giả thuyết MVP (chỉ kiểm chứng một giá trị):** Embedding-based diversity selection chọn ra Top-K frame có **tỷ lệ gần trùng lặp thấp hơn Random Sampling** với cùng số lượng frame K. Uniform Temporal Sampling là baseline đối chiếu thứ hai. Tính đại diện, giữ sự kiện và hiệu quả training là các câu hỏi tiếp theo, **chưa phải điều kiện nghiệm thu MVP**.

## 2. Khảo sát cộng đồng: tín hiệu và giới hạn

Khảo sát định tính từ một số bài Reddit và một GitHub issue; **không đại diện cho toàn thị trường**, không đo willingness to pay. Điểm vote là snapshot, số comment dưới đây là số đọc được, không khẳng định tổng comment.

| Luồng ý kiến | Dấu hiệu tương tác được ghi trong báo cáo khảo sát | Hàm ý sản phẩm |
|---|---|---|
| Near-duplicate trong video | [r/computervision — 17/09/2025](https://www.reddit.com/r/computervision/comments/1njbnbg/impact_of_nearduplicate_samples_for_datasets_from/): bài +2, 2 comment đọc được | So sánh temporal và embedding diversity, không mặc định chỉ cần giữ 20% frame. |
| Sampling cố định chưa chắc đủ đa dạng | [r/computervision — 28/10/2025](https://www.reddit.com/r/computervision/comments/1oimr5n/how_to_effectively_collect_and_label_datasets_for/): bài +5, ≥4 trao đổi đọc được | Đo coverage; giữ split theo video/camera khi cần đánh giá downstream. |
| Chọn frame/QA annotation tốn công | [r/computervision — 12/03/2026](https://www.reddit.com/r/computervision/comments/1rrln8x/whats_your_biggest_annotation_pain_point_right_now/): bài +8, ≥10 comment đọc được; comment về auto-annotation +11 | Xuất batch dễ kiểm tra; không xây annotation platform mới. |
| Active learning có chắc hơn random? | [r/MachineLearning — 04/10/2025](https://www.reddit.com/r/MachineLearning/comments/1ny6ol1/d_experiences_with_active_learning_for_real/): bài +4, ≥3 trao đổi có nội dung đọc được | Không đưa retraining/uncertainty thành điều kiện MVP; nghiên cứu sau bằng cùng annotation budget. |
| Rare/edge cases | [r/SelfDrivingCars — 05/09/2023](https://www.reddit.com/r/SelfDrivingCars/comments/16ar7em): bài +4, ≥10 comment đọc được; một comment +26 | Nếu có event labels, kiểm tra event recall@K bên cạnh diversity. |
| Nhu cầu rất cụ thể về frame khác biệt | [FiftyOne issue #4568](https://github.com/voxel51/fiftyone/issues/4568): muốn 100 frame khác biệt từ video 30 FPS | Top-K từ video là workflow có người chủ động tìm giải pháp; không có số reaction đã xác minh. |

**Diễn giải:** Có tín hiệu pain point thật, nhưng chưa đủ kết luận rằng chiến lược selection của RAV-26 sẽ cải thiện model, hoặc người dùng cần Agent. Bản gốc dẫn `RAV26_social_pain_points_report.md`; file này không có trong repo, nên PL6 giữ tên nguồn và bỏ liên kết hỏng.

## 3. Mục tiêu và phi mục tiêu của MVP

### Must-have
1. Đọc video hoặc thư mục frame; lưu `sample_id`, `video_id`, `timestamp/frame_index`, `image_path`.
2. Trích xuất và cache embedding bằng **một** vision encoder pretrained.
3. Cùng budget K, chạy được **Random**, **Uniform Temporal**, **Embedding Diversity**.
4. Diversity selection cập nhật reference set **trong cùng batch** để giảm chọn frame tương tự; nếu người dùng truyền reference set sẵn có thì dùng được nhưng **không yêu cầu** multi-iteration workflow.
5. Xuất Top-K có ID/đường dẫn/timestamp/strategy và tạo contact sheet hoặc gallery để quan sát thủ công.
6. Xuất báo cáo **Near-Duplicate Rate@K** của ba strategy trên cùng tập dữ liệu và cùng K; ghi thời gian chạy như thông tin vận hành, chưa dùng coverage/event recall làm metric thành công.

### Chưa làm trong MVP
- LLM Agent, Agent Review và workflow approve/reject riêng; không xây hệ thống labeling.
- Active Learning loop, retraining, uncertainty scoring, hybrid scoring, auto-adaptive strategy.
- Realtime inference, LiDAR/point cloud, cloud deployment phức tạp, nhiều tài khoản/role, tích hợp API CVAT/Label Studio.
- Không hứa hẹn mAP tăng, chi phí labeling giảm theo % cố định, hay phát hiện mọi rare event.

## 4. User flow MVP

```text
Video / Frame Folder
  → manifest (video ID, timestamp, sample ID)
  → embeddings (cached)
  → chọn K + strategy [random | temporal | diversity]
  → Top-K result + gallery/contact sheet
  → export CSV/JSON + selected frame paths
  → offline comparison report
```

**Không cần Agent hay human approval:** người dùng có thể nhìn gallery và tự quyết định có dùng batch hay không; gallery không phải một review workflow phải lưu state.

## 5. Functional requirements & acceptance

| ID | Requirement | Điều kiện nghiệm thu |
|---|---|---|
| FR-01 | Ingest | Import được subset tối thiểu 1.000 frame; manifest giữ `video_id` và thứ tự thời gian. |
| FR-02 | Embedding | Chạy encoder và cache; rerun không phải tính lại toàn bộ khi input không đổi. |
| FR-03 | Random baseline | Có seed cố định, chọn chính xác K sample không lặp ID. |
| FR-04 | Temporal baseline | Lấy frame cách đều trong từng video; lưu rõ quy tắc phân bổ K giữa video. |
| FR-05 | Diversity selection | Chọn chính xác K ID duy nhất, có cập nhật reference set sau từng sample được chọn. |
| FR-06 | Inspect/export | Có contact sheet/gallery; CSV/JSON gồm sample ID, source, timestamp, strategy, rank (nếu có). |
| FR-07 | Evaluate | Cùng candidate pool và K; xuất Near-Duplicate Rate@K cho cả ba strategy, seed/config/ngưỡng similarity và runtime; đối chiếu với Random. |

## 6. Data plan

**Development:** thử trên một vài sequence **MOT17** hoặc **UA-DETRAC** để debug pipeline và xem frame liên tục. **Domain validation:** cân nhắc một subset video **BDD100K**; **nuScenes mini** là lựa chọn khác để smoke-test ADAS. Cần kiểm tra điều khoản sử dụng và annotation có sẵn theo từng task/subset trước khi tải/chọn.

**MVP không cần nhãn object detection/event/scenario.** Chỉ cần một tập nhỏ **cặp frame** được nhìn kiểm tra thủ công để hiệu chỉnh ngưỡng “gần trùng lặp” cho phép đo độc lập. Nếu muốn tự gán nhãn `near_duplicate_group`, `scenario`, `interesting_event`, `low_quality` trên khoảng 100–200 frame thì để dành cho bước validation tiếp theo, không chặn tiến độ MVP.

**Split:** nếu dùng nhiều clip/camera, báo cáo selection theo video; khi sang downstream training ở phase sau, split theo video/scene để tránh leakage giữa train và test.

## 7. Evaluation plan — một metric cho MVP

**Câu hỏi kiểm chứng:** Với cùng tập frame đầu vào và cùng số frame được chọn **K**, Embedding Diversity có giảm tỷ lệ frame gần trùng lặp so với Random Sampling không? Uniform Temporal là baseline đối chiếu bắt buộc, nhưng chưa đặt mục tiêu phải thắng mọi baseline ở MVP.

**Primary Metric duy nhất: Near-Duplicate Rate@K.** Một frame được tính là gần trùng nếu trong **cùng batch Top-K** có ít nhất một frame khác đạt ngưỡng similarity đã chốt. Tỷ lệ = số frame thỏa điều kiện / K. Ví dụ 20/100 frame có ít nhất một frame gần trùng → rate = 20%. Không dùng raw embedding diversity score của thuật toán lựa chọn để tự chấm thuật toán.

| Nội dung | Quy tắc MVP |
|---|---|
| Input so sánh | Cùng candidate pool và cùng K (thử K=50/100/200 nếu pool cho phép; chốt một K chính trước khi chạy). |
| Baselines | Random, Uniform Temporal, Embedding Diversity; chọn đúng K ID duy nhất. |
| Đo gần trùng | Ưu tiên pHash/Hamming distance hoặc một phép đo độc lập với embedding selection; kiểm tra thủ công một tập nhỏ **cặp frame** để chốt threshold **trước** khi so sánh. pHash là proxy cho trùng lặp hình ảnh, không bảo đảm nhận diện trùng ngữ nghĩa. |
| Random seed | Công khai seed; cố gắng chạy ≥5 seed cho Random và báo cáo trung bình + độ phân tán. Các strategy dùng cùng pool/K và cùng quy tắc đánh giá. |
| Báo cáo | Near-Duplicate Rate@K từng strategy; chênh lệch tuyệt đối và tương đối so với Random; lưu dataset version, threshold, seed, cấu hình máy và runtime để tái lập. Runtime chỉ là thông tin vận hành, chưa phải metric thành công. |

**Success target (giả thuyết sản phẩm, cần kiểm chứng):** Near-Duplicate Rate@K của Embedding Diversity **thấp hơn ít nhất 20% tương đối** so với trung bình Random ở K chính đã chốt. Ví dụ Random 25% → Diversity ≤20%. Vẫn báo cáo kết quả so với Uniform Temporal, kể cả khi Diversity kém hơn. Nếu Random có tỷ lệ gần 0%, ghi nhận mức chênh lệch tuyệt đối và đánh giá lại độ phù hợp của benchmark; không diễn giải phần trăm cải thiện gây hiểu nhầm.

**Phân biệt nghiệm thu:** (1) **Technical MVP complete** khi pipeline chọn đúng K, export được và đánh giá có thể tái lập; (2) **Product hypothesis validated** chỉ khi target giảm near-duplicate được quan sát trên benchmark đã chốt. Không tự động coi việc giảm duplicate là bằng chứng giữ được rare event, coverage hay cải thiện mAP.

**Mở rộng sau khi có kết quả MVP:** Nếu tín hiệu giảm duplicate ổn định và xem ảnh không thấy failure nghiêm trọng, nghiên cứu **Coverage@K / Representativeness** trước; sau đó mới **Event Recall@K** khi có event annotation, và cuối cùng **downstream mAP/F1** trong Active Learning. Các metric này **không phải yêu cầu của ba tuần đầu**.

## 8. Kiến trúc tối giản

Python + PyTorch/pretrained encoder + NumPy/scikit-learn + CSV/Parquet + local image cache. CLI trước; Streamlit **chỉ thêm nếu còn thời gian**, vì contact sheet tĩnh đã đủ để inspection. Không yêu cầu LangGraph, Qdrant, React, FastAPI, DVC, MLflow hoặc GPU cloud phức tạp ngay ở MVP.

## 9. Delivery plan: 3 tuần

| Tuần | Công việc | Gate |
|---|---|---|
| 1 | Chốt dataset/subset; ingest; manifest; embedding cache; random + temporal | Tạo được hai batch Top-K từ cùng pool, tái lập bằng seed/config. |
| 2 | Diversity reference-aware; export; contact sheets; hiệu chỉnh threshold trên cặp frame | Có 3 batch cùng K và script Near-Duplicate Rate@K. |
| 3 | Chạy trên benchmark đã chốt; Random nhiều seed; so sánh ba strategy và failure cases; demo | Báo cáo metric chính, target 20% tương đối có/không đạt, hạn chế và quyết định phase tiếp. |

## 10. Phase tiếp theo — mở rộng có điều kiện, không phải cam kết MVP

- **Selection quality:** Sau khi kiểm chứng giảm near-duplicate, đo Coverage@K / Representativeness để kiểm tra thông tin bị bỏ sót.
- **Event preservation:** Chỉ thêm Event Recall@K khi có nhãn event được chốt độc lập.
- **Uncertainty + diversity:** cần baseline model phù hợp và score được kiểm định.
- **Inter-batch reference:** tránh chọn tương tự với labeled/selected set ở các iteration trước.
- **Active Learning:** selection → gán nhãn → retrain → test set cố định; so sánh random vs proposed strategy với **cùng annotation budget**, nhiều seed.
- ODD/rare-case-aware selection; tích hợp labeling tool; UI/review workflow **chỉ bổ sung khi có nhu cầu cụ thể**.

## 11. Các quyết định cần chốt trước khi code

1. Dataset đầu tiên và quy mô pool phù hợp tài nguyên máy.
2. Encoder đầu tiên (chỉ một model), kích thước ảnh và chiến lược cache.
3. Quy tắc chia K cho nhiều video; chốt K chính và bộ video benchmark.
4. Chọn phép đo near-duplicate độc lập với embedding selection; số cặp frame kiểm tra thủ công, threshold và target 20% tương đối trước thí nghiệm.
