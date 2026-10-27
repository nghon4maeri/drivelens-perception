# Đề xuất phạm vi bàn giao RAV-26 trong hai ngày

Ngày: 2026-10-15. Nhân lực: 3 người. **Kế hoạch đề xuất, chưa implement hoặc xác nhận tính năng chạy được.**
Theo yêu cầu mới nhất: chỉ hoàn thiện plan. Không tự tải dataset, ghi S3 hoặc đổi code sản phẩm.
Thiết kế mới phải được duyệt và cập nhật CONTRACTS/architecture trước implementation; một phiên một lát theo AGENTS.
Kế hoạch chi tiết model/reference: [V6 integration](V6_KHANH_INTEGRATION_PROPOSAL.md).

## 1. Mục tiêu và giới hạn đã biết

Demo chọn dữ liệu ADAS chạy thật: nguồn có provenance → chuẩn bị → chấm/chọn → người duyệt → xuất PII → so với random.
Không làm chatbot. User xác nhận chưa có checkpoint YOLO train BDD100K, ưu tiên chọn mẫu chạy thật.
Không khai báo YOLO COCO được train trên BDD100K để vượt guard reference/model-guided.

Main tại lần rà soát chỉ nhận VIDEO làm pool phiên trong `pilot_db._create_session_core`.
Catalog có IMAGE_SET không đồng nghĩa Studio chọn batch từ bộ ảnh BDD100K được ngay.
Model-guided pilot còn bị chặn; main chưa nạp đủ reference vào worker; PATCH reference chưa đồng bộ session.spec.reference.
PII biển số và export có lỗi đã ghi trong đề xuất V6. Không coi các hàng candidate PASS cũ là bằng chứng các lỗi này đã hết.

## 2. Gói tính năng cần nối thành luồng

| Gói | Đầu ra cụ thể | Đáp ứng yêu cầu và điều kiện |
|---|---|---|
| 1. Nguồn và dữ liệu demo | Hoàn tất VPS upload/ingest đang làm; video demo cố định; bộ ảnh BDD nhỏ có manifest, nhãn, ODD, checksum | S3 là bytes, DB/artifact registry là metadata authority; không chỉ PUT ảnh rồi báo UI sẵn sàng |
| 2. ODD và corner candidates | time_of_day/weather/road_type theo unit hoặc interval, provenance/revision/unknown; UI lọc và phân bố pool/batch | Metadata BDD là observed annotation; dự đoán/manual phải ghi riêng; upstream corner flag không là sự thật hiếm/nguy hiểm |
| 3. Xếp hạng và chống lệch | Giữ diversity/text_diversity; thêm mục tiêu phân bổ ODD và deficit/cap; rarity theo tần suất nhóm được định nghĩa | Không coi novelty là rarity; không coi cluster là ODD; không tự nới hard constraints; không nhập MILP vào đường interactive |
| 4. Uncertainty và reference | Sửa binding/spec/cache; nối reference_diversity rồi model_guided khi có checkpoint và provenance thật | Có sẵn code không có nghĩa usable; không hứa model-guided trên BDD trong đợt này khi chưa có checkpoint phù hợp. Detection COCO chỉ là evidence riêng |
| 5. Giải thích và báo cáo | Mỗi ảnh có điểm/lý do/nguồn bằng chứng; batch có ODD, unknown, duplicate/diversity/corner-candidate rates | Chuyển logic chọn lọc từ V6; export giữ đúng evidence đã freeze, không sinh lại trên snapshot cũ |
| 6. Duyệt hai vai trò | Curator gửi batch, reviewer được phân quyền backend duyệt/từ chối; export chỉ sau phê duyệt và PII confirmation | Đây là thay đổi quyền/RLS cần thiết kế riêng; owner và operator đọc vận hành hiện tại chưa thay thế workflow này |
| 7. PII và export | Detector biển số thật, lỗi model không bỏ qua, đúng snapshot anchors, preview/confirmation, ZIP streaming | Ưu tiên sửa trước mở rộng tính năng; artifact version/hash/receipt; không trả ảnh giả khi decode thất bại |
| 8. So sánh hiệu quả | Random vs curated trên cùng pool và K, seed cố định; biểu đồ diversity/ODD/duplicate, actual_n/deficit | Ghi rõ constraint settings. Uplift mô phỏng phải có mô hình/giả định công khai và nhãn simulation; mAP/uplift thực cần train/eval hợp lệ |

Gói 4 là mục tiêu đầy đủ theo integration proposal, có phụ thuộc thật; chưa hoàn tất thì ghi rõ trạng thái.
Không gắn nhãn đạt đủ đề khi mới có catalog, nút UI hoặc proxy. Nếu bỏ bớt contract phải xin chốt và ghi Hướng tới.
Risk/safety cần taxonomy/quy tắc có căn cứ: có pedestrian/đêm không đủ kết luận nguy hiểm.
Trong demo có thể hiển thị nhóm tình huống cần xem, nhưng không thay tên thành safety score đã kiểm chứng.

## 3. Dataset và demo trên S3

Hai đầu ra khác nhau:

1. **UI vận hành hiện tại:** dùng 3–5 video recorded có nguồn/quyền dùng rõ ràng, giữ continuity để chứng minh temporal dedup,
   upload/prepare/keep/drop/PII/export. Chạy DINO/CLIP trước demo, vẫn cho thấy manifest và job evidence thật.
2. **Đánh giá có nhãn:** BDD100K subset khởi đầu 200 ảnh để smoke, tăng tối đa khoảng 500 ảnh khi luồng ổn.
   Lưu ảnh gốc, nhãn detection, attributes ODD, dataset/mirror revision, label vintage, class map, SHA và source IDs.
   Bản mirror đã kiểm có JSON theo ảnh với weather/scene/timeofday; chưa tải hay ghi S3.
   Nguồn chính thức hiện trỏ ETH; mirror phải ghi rõ provenance và không gọi nhãn 2018 là Detection 2020.

Chọn deterministic theo inventory/seed, không chọn ảnh sau khi biết kết quả thuật toán.
Nếu tạo subset stress đêm/mưa thì tách riêng, công khai phân bố; không đại diện toàn BDD100K.
Giữ GT detection khỏi scorer khi chọn, chỉ mở cho evaluation. ODD metadata có thể làm input explicit.
Một keyframe BDD không có nghĩa mọi frame của video tương ứng đều có GT.
Không ghép ảnh độc lập thành video giả rồi coi chúng là cùng sequence.

Muốn chọn ảnh BDD trực tiếp trên UI phải thêm lát **IMAGE_SET candidate pool**: docs/contract, immutable image units,
context loader, thumbnail/exact-pixel lookup, preparation, proposal/checkpoint, snapshot/export và metrics.
Đây là mở rộng thiết kế, chưa được implementation tự động trong kế hoạch hai ngày. Nếu chưa duyệt/kịp,
BDD dùng benchmark cùng core theo luồng đánh giá riêng; UI video và báo cáo BDD phải được trình bày đúng nguồn.
Nếu yêu cầu BDD end-to-end trên UI là bắt buộc, đưa lát này vào critical path và đánh giá lại lịch, không che bằng artifact S3.

Không tạo quan hệ TRAIN/FINETUNE giữa model COCO và subset BDD. Không cần train model để demo diversity/ODD;
model-guided theo dataset train và uplift thực vẫn cần checkpoint/dữ liệu hợp lệ riêng.

## 4. Chia việc và thứ tự tích hợp

| Người | Trách nhiệm chính | Thứ tự |
|---|---|---|
| A | Upload và PII/export | Hoàn tất WIP #139 → detector thật → exact snapshot frames → confirmation/ZIP/recovery |
| B | Curation và dữ liệu | Sửa binding → ODD schema/metadata/filter/allocation → reference context; model-guided sau khi thỏa provenance |
| C | Trải nghiệm bàn giao | Chốt DTO cùng B → explanation/Analysis/random benchmark → curator/reviewer backend+UI, phối hợp owner/RLS với B |

Ngày 1 đầu: chốt scope và cập nhật contract, thống nhất DTO/evidence/ODD/version; chuẩn bị dataset manifest.
Ngày 1 cuối: ghép luồng video chọn batch + evidence + review trước; từng người giao commit nhỏ theo lát,
không đổi schema/API dùng chung đồng thời khi chưa thống nhất.
Ngày 2 đầu: tích hợp export, benchmark/report và phân quyền; xử lý lỗi xuyên luồng.
Ngày 2 cuối: ngừng thêm feature, chạy kiểm tra bàn giao chung, sửa blocker, chuẩn bị phiên demo và bản rollback.

Đây là lịch mục tiêu, không cam kết cả tám gói hoàn tất. Cần checkpoint phù hợp để mở model-guided;
IMAGE_SET candidate, quyền reviewer và simulation mới là những việc đáng kể, không phải đổi vài dòng config.
Không đưa full retrain loop, CVAT hai chiều, chatbot hoặc solver mới vào critical path.

## 5. Kiểm tra tập trung để tiết kiệm thời gian

Không yêu cầu nghiệm thu thủ công riêng cho từng tính năng nhỏ. Một luồng end-to-end chung là acceptance chính:

1. Nguồn cố định → upload/READY → prepare → đề xuất → lọc ODD → keep/drop → gửi duyệt → reviewer duyệt.
2. Snapshot → preview PII → confirm → ZIP; đối chiếu ordered members/hash và ảnh đúng frame.
3. Random/curated cùng pool/K/seed, report ghi actual_n, unknown/deficit; không giả uplift.
4. Reload/retry một lần; checkpoint cũ/reference đổi không publish nhầm; reviewer/owner khác không vượt quyền.

Các thay đổi quyền, pixel identity, state transaction, PII/model failure vẫn cần focused automated tests khi code,
và ruff/tsc theo repo. Có thể gom thành suite chung, không bỏ các kiểm tra chống xuất sai ảnh/lộ PII/vượt quyền.
Sau một lượt checks phù hợp đã xanh, không chạy lặp toàn bộ suite nếu không có thay đổi hoặc nghi vấn mới.
Release note phân biệt code có, test local, cloud đã chạy và các gate chưa qua.

## 6. Kịch bản thuyết trình

Nêu bài toán và budget K → xem pool/distribution → chọn cùng K bằng random và curated → so duplicate/diversity/ODD
→ mở vài ảnh với lý do chọn → curator gửi/reviewer duyệt → xem PII preview và tải ZIP.
Trình bày kết quả thật kể cả curated không thắng một chỉ số. Không cố chứng minh mọi recipe luôn tốt hơn random.
Nếu có simulation, đặt ở panel riêng có giả định, không thay mAP đo thật. Không tuyên bố closed-loop active learning.

## 7. Trạng thái hiện tại

Đã có bản integration proposal và kế hoạch phạm vi/phân công này. Chưa triển khai các gói mới của kế hoạch.
Đã kiểm tra cấu hình S3 và HeadBucket thành công; chưa tải dataset hoặc PUT dữ liệu.
Chưa tạo migration/cập nhật thiết kế đã chốt, không commit/push. WIP upload của người khác giữ nguyên.
