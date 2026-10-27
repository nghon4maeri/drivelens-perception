# V2 — kiểm tiêu chí trên dữ liệu thật

Giao thức lập trước khi chạy truy vấn (2026-10-07), theo yêu cầu người dùng. CONTRACTS §15/18 vẫn là nguồn thiết kế; lát này chỉ đánh giá, không thay thuật toán hay ngưỡng.

## Dữ liệu và tính độc lập

Hai video local đã có CLIP: commaai-sample và 6WTF1JV5knI. Dùng toàn bộ pool đã prepare, không tải model/dataset mới, không gọi Kaggle. Kho thử riêng dưới `output/v2-criteria-eval`, không thay phiên người dùng. DINO/CLIP và YOLO11 nano thật, checkpoint pin như contract. Bốn ảnh reference từ smoke cũ chỉ mô phỏng quan hệ train; nano là COCO pretrained, không chứng minh model được train trên bốn ảnh.

Xem contact sheet của pool trước để kiểm kê bối cảnh; đánh giá tiêu chí bằng ảnh gốc khi cần. Không dùng cosine/box YOLO để gán đáp án. Một người đánh giá, dữ liệu nhỏ và tương quan theo thời gian, không suy rộng sang BDD100K.

## Tiêu chí ngữ nghĩa đã định trước

| ID | Câu tìm tiếng Anh | Quy tắc ảnh khớp |
|---|---|---|
| cars | a dashcam photo of cars on a road | Có ít nhất một xe ô tô nhìn thấy trên đường; không tính xe quay camera/bề mặt kính là xe ngoài đường |
| highway | a dashcam photo of a highway | Đường nhiều làn theo cùng hướng, có dải phân cách/biên đường kiểu cao tốc; cảnh phố/giao lộ không khớp; thiếu dấu hiệu phân biệt ghi ambiguous |
| traffic_light | a dashcam photo of a road with a visible traffic light | Nhìn thấy cụm đèn giao thông; cột/biển báo không đủ, vật quá nhỏ không xác định ghi ambiguous |
| night | a dashcam photo of a road at night | Trời tối, chiếu sáng nhân tạo/đèn xe; cảnh ban ngày không khớp |
| snow | a dashcam photo of a snow covered road | Tuyết nhìn thấy phủ đường/lề; trời sáng/đường màu nhạt không đủ |
| dog | a dashcam photo of a dog on the road | Có chó nhận diện được trên mặt đường; không suy từ box detector |

Với mỗi câu: `text_diversity` và `text_model_guided`, batch_size=8, seed=20261007, min_similarity=0.2, negative=[], candidate_limit=16. Giữ NMS=1 s, min_dist=0.1 và exploration mặc định, không nới để đủ batch. Uniform trên pool, cùng số picks thực nhận, làm mốc mô tả. Đo số trả về, số khớp/không khớp/ambiguous và precision trên số trả về; batch rỗng không có precision. Tiêu chí vắng mặt mong đợi rỗng về mặt nhu cầu người dùng, dù contract CLIP chỉ bảo đảm cosine gate heuristic. Tách kết luận chất lượng khỏi kết luận đúng công thức.

## Điều kiện cơ học

- Tất cả picks thuộc pool, không là ảnh reference, không có ID trùng; giữ/drop bị loại ở vòng sau.
- Tất cả picks/candidates stage đều vượt gate CLIP đúng câu, ngưỡng và negative_margin; không cưỡng ép đủ 8.
- Các cặp picks giữ NMS thời gian và khoảng cách DINO quy định; cả candidate stage và final.
- Guided chỉ infer original+flip trên ứng viên: số gọi detector không vượt 2×16; đo trực tiếp số gọi engine, không chỉ đọc config.
- Cùng config/state/seed cho cùng IDs và điểm; đọc replay không gọi YOLO, không ghi quyết định.
- Positive giống negative trả rỗng; min_similarity=1 trả rỗng trên pool này; job rỗng không gọi detector.
- Kiểm negative thật: cars positive, highway negative; xác minh cosine gate và xem ảnh trả về có còn cao tốc hay không.
- Recipe model_guided không có text_match: goal tự do không là bộ lọc ngữ nghĩa; xác nhận hai goal khác nhau với cùng config/seed giữ output, không tuyên bố goal được hiểu.
- Boundary riêng (không dùng chấm chất lượng): đặt NMS=100 s ở cả hai bước, giữ batch_size=8. Mỗi video chỉ đủ một ảnh; mong đợi 2 picks, không relaxed, tối đa 4 lượt YOLO. Bổ sung sau lượt ngữ nghĩa đầu tiên để kiểm đợt thiếu, không thay kết quả/ngưỡng truy vấn đã định trước.

Kết quả, IDs/config/hash/số gọi engine và lưới lưu dưới `output/v2-criteria-eval`; báo cáo chốt thêm sau chạy. Nếu phát hiện sai contract thì sửa và chạy lại; sai ngữ nghĩa không tự đổi thiết kế/ngưỡng trong lát đánh giá.

## Kết quả 2026-10-07

**Luồng chạy đúng contract; tìm theo nội dung chưa đạt với ngưỡng mặc định.** Đã chạy 9 cấu hình × 2 recipe trên toàn pool 206 frame, seed cố định. Sáu câu chính đều cho 206/206 frame vượt gate 0.2; cả hai recipe trả 8 ảnh cho mỗi câu, kể cả nội dung vắng mặt. Dưới đây là chấm bằng mắt, không phải accuracy của detector:

| Tiêu chí | text_diversity: khớp / trả về | text_model_guided: khớp / trả về | Uniform cùng K |
|---|---|---|---|
| Xe trên đường | 6/8 + 2 ambiguous | 7/8 + 1 ambiguous | 6/8 + 2 ambiguous |
| Cao tốc | 4/8 (50%) | 1/8 (12.5%) | 3/8 (37.5%) |
| Đèn giao thông nhìn thấy | 0/8 | 0/8 | 0/8 |
| Ban đêm | 0/8 | 0/8 | 0/8 |
| Tuyết phủ đường | 0/8 | 0/8 | 0/8 |
| Chó trên đường | 0/8 | 0/8 | 0/8 |
| Xe, loại trừ cao tốc | 7/8 | 8/8 | 5/8 |
| Positive = negative | 0 picks, đúng gate | 0 picks, đúng gate | Không tính precision |
| min_similarity=1 | 0 picks, đúng gate | 0 picks, đúng gate | Không tính precision |

`ambiguous` là xe quá xa/nhỏ để xác nhận chắc trong ảnh gốc, không tự tính đúng. Precision xe nằm trong khoảng 75–100% / 87.5–100%; khoảng này chỉ phản ánh cách xử lý ảnh không xác định, **không là confidence interval**. Tiêu chí loại trừ là đồng thời có xe và không cao tốc. Cảnh phố có SUV/pickup nhìn thấy; không dùng xe của camera hay box trên kính làm đáp án. Chấm cao tốc gồm đoạn có làn/biên đường kiểu cao tốc và đường nối tiếp có guardrail, theo quy tắc đã định. Các ảnh được trả cho traffic_light không có cụm đèn nhận diện được; không suy recall cho đèn quá nhỏ chưa quan sát chắc toàn pool.

Nhãn gắn với exact ordered IDs trong [V2_VISUAL_LABELS.json](../../../../evaluation/V2_VISUAL_LABELS.json), bộ tính báo cáo kiểm IDs trước khi dùng lại nhãn. Đã xem sáu trang contact sheet phủ toàn pool, tất cả lưới picks của 7 tiêu chí có ảnh và lưới Uniform (cùng IDs khi cùng K); mở thêm ảnh gốc của các trường hợp xe xa. Lưới, 19 ảnh gốc xe và dữ liệu chi tiết nằm trong `output/v2-criteria-eval`. Không đo recall toàn bộ corpus hoặc detector AP/mAP.

### Vì sao tìm nội dung chưa đạt

- Cosine thực trên pool: cars 0.270–0.324; highway 0.257–0.325; traffic_light 0.273–0.329; night 0.207–0.282; snow 0.209–0.301; dog 0.264–0.343. Nội dung vắng mặt vẫn vượt ngưỡng 0.2: câu có cùng bối cảnh dashcam/road đủ gây false positive. Các text embeddings/điểm khác nhau; không phải mọi query bị thay bằng một câu mặc định.
- text_diversity nhân score CLIP `(cos+1)/2` với novelty. Score text trong khoảng hẹp trong khi novelty trải từ 0 tới 1; sáu câu trả cùng tập 8 ảnh (thứ tự có đổi). Đa dạng đang lấn át khác biệt câu tìm trên pool này.
- text_model_guided chỉ dùng CLIP để gate/chọn ứng viên; bước final xếp theo novelty và instability. Khi gate nhận cả pool, final không giữ mức liên quan câu tìm. Reference mô phỏng lấy từ commaai, nên ưu tiên cảnh phố khác reference là hành vi novelty có thể dự kiến; đây không phải chứng minh YOLO guided tốt hơn cho nội dung hoặc lỗi model.
- Negative highway giảm eligible 206 xuống 126 và cải thiện kết quả cảnh phố; vẫn không chứng minh mọi negative là bộ lọc đối tượng chính xác. model_guided thuần có hai goal cars/snow nhưng cùng IDs, đúng `uses_goal=False` hiện tại.

### Cách chạy lại

Kiểm cơ học đạt ở cả lượt đầu và lượt cache: IDs thuộc pool, reference đứng ngoài, NMS/min_dist đúng ở hai bước, gate/seed/scores khớp, replay không gọi detector hoặc ghi quyết định. Lượt đầu guided có 16 ứng viên và đúng 32 lần gọi engine thật ở mỗi query có ảnh; không chạy toàn pool 206. Lượt hai giữ IDs/nhãn và dùng requested cache, 0 lần infer cho các query đó. `first-run-results.json` giữ số gọi lượt đầu; `results.json` là lượt sau, `checks.json` giữ boundary. Review keep/drop loại đúng hai IDs ở đợt tiếp theo. Boundary NMS=100 s yêu cầu 8 nhưng trả 2, không relaxed, đúng 4 lượt YOLO. Positive=negative và ngưỡng 1 không có picks/ứng viên guided, 0 lượt infer.

Regression `test_algorithm_v2`, `test_clip`, `test_p5b`, `test_p8i`: **38 passed**; ruff src/tests/script và diff-check đạt. Không thay production algorithm/FE/contract, không thêm thư viện. Đây là đánh giá đã hoàn thành nhưng **kết quả ngữ nghĩa chưa đạt**, không đánh dấu thành công chất lượng chỉ vì test code xanh.

```powershell
python -m scripts.evaluate_v2_criteria audit
python -m scripts.evaluate_v2_criteria run
python -m scripts.evaluate_v2_criteria report
```

Cần fixture local `output/ui-smoke-v2` (dataset/model và DB) cùng cache hai video/CLIP revision đã pin. Cache/model/dữ liệu không commit. Script dùng offline và kho riêng; không sửa main store, không gọi GPU/Kaggle. `report` dùng nhãn đã chấm trong repo nếu chưa có labels.json local và từ chối nếu IDs khác. Chạy lại trên cùng nguồn cho cùng kết quả; nhãn chỉ áp dụng bộ ảnh này, không tự đánh giá corpus mới.

Lưu ý thực nghiệm máy RAM thấp: chạy evaluation và regression lần lượt. Dữ liệu/model thử nằm D; cuối lượt kiểm, C còn khoảng 1.2 GiB, metadata `pagefile.sys` khoảng 19.6 GiB. Cache HF trên C 0.545 GiB, pip 0, không có cache mới trong giờ kiểm. Không có baseline kích thước pagefile trước lượt để quy toàn bộ biến động cho test; Windows có thể tăng file bộ nhớ ảo khi có áp lực RAM, đường cache D không kiểm soát file này. Tiến trình evaluation/pytest đã kết thúc; không xoá pagefile đang dùng hoặc tự đổi cấu hình Windows.

Đề xuất lát thuật toán sau: hiệu chỉnh gate bằng dữ liệu có nhãn positive/negative và tập holdout; giữ relevance trong final, có quyết định trả rỗng khi không đủ bằng chứng; đối chiếu CLIP-only/novelty/guided cùng budget trên nhiều video và seed. Chưa áp dụng các thay đổi này trong lát đánh giá, chưa thay contract để hợp thức hoá kết quả.
