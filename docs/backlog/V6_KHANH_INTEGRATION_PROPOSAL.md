# De xuat tich hop logic v6_khanh vao pilot hien tai

Ngày rà soát: 2026-10-15. **Đề xuất để duyệt, chưa là quyết định thiết kế hoặc trạng thái đã implement.**
Không thay CONTRACTS/architecture/PLAN hoặc quyết định đã chốt bằng tài liệu này.

## 1. Baseline và kết luận về model

- Main đã kiểm: `13cdd6ed8af8f9507d6b8af405e0849aadd5da4a`.
- Nhánh nguồn: `origin/v6_khanh`, commit `a8298edeab2f2eade2008718c9c3c1dc33ff47f8`.
- Ứng dụng hiện hành của nhánh nguồn nằm trong `rav26-work/`; `app/` ở gốc là bản cũ.
- Checkout hiện tại còn WIP VPS-Upload-Ingest #139 của người khác. Không trộn WIP vào bản tích hợp.
- Không merge toàn nhánh: hai ứng dụng khác schema, auth, worker, storage, API và UI.

Luồng sản phẩm của nhánh nguồn (`app/services/traffic_model.py`):

1. Detector cố định `fasterrcnn_mobilenet_v3_large_320_fpn`, trọng số Torchvision `COCO_V1`,
   file `fasterrcnn_mobilenet_v3_large_320_fpn-907ea3f9.pth`.
2. Encoder mặc định ResNet18 ImageNet-1K, vector 512 chiều L2. DINOv2 có provider cho nghiên cứu;
   không có nghĩa detector uncertainty mặc định đổi sang DINO hoặc checkpoint do người dùng chọn.
3. Suy luận ảnh gốc và ảnh lật ngang; đưa box ảnh lật về tọa độ gốc, ghép Hungarian theo lớp/IoU,
   tính bất đồng gồm cả box không ghép được (`app/services/scoring.py`).
4. Detector không trả phân phối xác suất đầy đủ, nên entropy trong luồng này không có giá trị.
   Hai lượt đều không có detection => uncertainty null; không phải chứng minh model chắc chắn đúng.
5. `dataset_model_profile` gắn dataset đang được xử lý với profile config/trọng số dùng inference.
   Đây không phải khai báo checkpoint được train/finetune trên dataset đó. Worker mặc định khởi tạo
   `TrafficModel`; tham số `model_factory` là điểm tiêm phụ thuộc, không phải catalog checkpoint cho user.
6. Các script nghiên cứu train/eval BDD100K tách khỏi worker sản phẩm. Không tự dùng checkpoint
   nghiên cứu làm model tham chiếu hoặc suy diễn nó đã được triển khai trong UI.

Do đó detector COCO là model perception tham chiếu tổng quát, nhưng **không đáp ứng quan hệ
model của dự án ↔ dataset train đã chọn** như luồng main yêu cầu. Model và dataset dùng inference
khác với dataset dùng huấn luyện. Ground truth của pool mới không cần cho consistency;
ground truth và split đánh giá vẫn cần để đo lỗi/mAP/uplift.

## 2. Phần giữ và phần chuyển

Giữ kiến trúc pilot: Supabase Auth/PostgreSQL RLS, source/unit IDs, canonical S3, immutable artifacts,
queue/fence/receipts, interactive TEXT cho recipe nhanh, snapshot và confirmation export.
Giữ DINO/CLIP và YOLO đã gắn phiên; không âm thầm thay bằng encoder ResNet18 hoặc detector COCO.

| Logic nguồn | Cách tích hợp dự kiến |
|---|---|
| `selection_quality.py` | Chuyển các phép đo phân bố ODD, unknown rate, uncertainty components, cross-round redundancy vào metric/evidence hiện có; giới hạn số cặp hoặc tính theo block |
| `selection_explanation.py` | Bổ sung evidence có cấu trúc vào proposal/snapshot/report, giữ nguồn metadata và phiên bản thuật toán; không sinh lại lý do cho snapshot lịch sử |
| `advanced_selection.signature`, `cluster_allocation.allocate` | Dùng làm tham khảo cho nhóm ODD, support/deficit/cap và phân bổ chống một nhóm chiếm ưu thế; không coi cluster là ODD |
| `scoring.disagreement_components` | Thêm chẩn đoán cho YOLO consistency hiện có; giữ công thức hiện tại trừ khi duyệt thay đổi và tăng version/cache key |
| `pii.PIIDetector` | Tách detector mặt/biển số, model SHA, tiled detection/NMS và xử lý lỗi vào worker hiện tại; đo chi phí tile trên ảnh thật |
| Curator/reviewer | Tham khảo workflow; thiết kế migration quyền/RLS riêng trước khi áp dụng |
| `scripts/p4_rare_hard.py` | Đánh giá offline trên GT đã có sau khi freeze selection; không đưa GT của pool mới vào scoring production |

Không mang SQLite/schema/auth/launcher/UI của nhánh nguồn sang. Không đưa MILP vào request tương tác
khi chưa có benchmark: selector nguồn gọi feasibility nhiều lần, mỗi lần có time_limit 10 giây.
`odd_coverage=1` khi không có quota trong code nguồn phải đổi thành trạng thái chưa cấu hình nếu chuyển sang UI.
Corner flag upstream chỉ là candidate, chưa phải chứng minh hiếm/nguy hiểm. Cost proxy không phải số tiền tiết kiệm đã đo.

## 3. Luồng đích cần duyệt

1. **Nguồn mới:** Browser → đĩa VPS → canonical S3 → ordered exact units/frames READY.
2. **Chuẩn bị nguồn:** DINO/CLIP và PII có thể cùng phiên Kaggle theo kế hoạch trước đó;
   readiness/artifact độc lập, có raw hash, unit/spec identity, model/config/version và object receipts.
   Giữ embedding/inference theo pixel variant mà contract quy định; ảnh che PII là derived variant cho preview/export,
   không âm thầm thay ảnh đầu vào model hoặc ghi đè raw. Nếu muốn đổi variant phải đổi thiết kế/cache identity trước.
3. **Tham chiếu:** operator import dataset version có manifest và checkpoint verified; khai báo TRAIN/FINETUNE
   có provenance. Phiên chọn reference dataset versions và model thực sự được phép dùng. Dataset train là reference,
   không tự nhập vào pool ứng viên. Kiểm tra class map, preprocessing/runtime và embedding space tương thích.
4. **Chuẩn bị dataset tham chiếu:** nạp/tạo artifact DINO cùng producer với pool, pin version và validate ordered members.
   Việc catalog đã có reference không có nghĩa worker đã nạp được ảnh/features đó.
5. **Đề xuất nhanh:** random/uniform/diversity/text_diversity giữ interactive path hiện có, đọc features sẵn sàng.
   `reference_diversity` chỉ mở khi reference context đã nạp và nằm trong memory budget.
6. **Đề xuất theo model:** thao tác explicit riêng, có job/progress/cancel. Freeze checkpoint SHA, reference versions,
   pool/spec/features/ODD metadata revision, state_version/decision_seq, seed/config. Chọn candidate stage rẻ
   (contract hiện tại tối đa 128), rồi YOLO gốc/lật **trước khi chọn batch cuối**. Không dùng nút YOLO_BATCH
   sau proposal để giả thành uncertainty sampling. Publish proposal bằng fence/CAS hiện có; context đổi thì từ chối
   publish. Batch yêu cầu lớn hơn candidate capacity phải báo rõ, không âm thầm đổi cấu hình.
7. **Xếp hạng và ODD:** novelty so train + kept, uncertainty proxy trên candidates, diversity trong batch,
   ODD filter/allocation với nguồn metadata và unknown rõ ràng. Hiển thị thiếu support; hard constraints không tự nới.
   Thiếu checkpoint/dữ liệu/điểm thì lỗi có lý do; không tự thay model hoặc đổi sang random.
8. **Duyệt:** người dùng xem lý do và evidence, keep/drop; loại bỏ việc đề xuất lại theo semantics hiện có.
   Snapshot freeze đúng model/reference/config/metadata/decision basis tại thời điểm chốt.
9. **Xuất:** dùng đúng snapshot frame và PII artifact hợp lệ; lỗi/missing model hoặc sai hash chặn export;
   owner xác nhận preview/payload_hash, stream ZIP lên S3. Reviewer độc lập là lát quyền riêng nếu được chọn.
10. **Đánh giá:** cùng pool/split/K/seed và eligibility cho so sánh công bằng; ghi actual_n, fill/deficit và
    constraint violations của random. Tách random thuần và random có ràng buộc. mAP/uplift thực chỉ trên GT/split hợp lệ;
    mô phỏng phải có nhãn, giả định và kết quả riêng.

Model gắn phiên có thể đổi, nên inference uncertainty không nên bị gắn cứng vào lần chuẩn bị DINO/PII ban đầu.
Cache theo exact input pixels + unit/spec + checkpoint SHA + runtime/inference/transform + score version.
Tái sử dụng kết quả cùng identity, không tái sử dụng điểm của model cũ. Không gọi DB theo từng frame/box:
nạp metadata theo lô, tính trên worker, ghi kết quả/artifact theo lô; transaction ngắn lúc claim/publish/checkpoint.

## 4. Lát cắt, phụ thuộc và nghiệm thu

Mỗi phiên chỉ thực hiện một lát, docs/contract trước code. Các mục dưới là đề xuất, không tick PLAN hiện tại.

| Lát | Phụ thuộc | Công việc và nghiệm thu |
|---|---|---|
| A. Reference nhất quán | Baseline main ổn định | Sửa PATCH reference cập nhật spec và quan hệ/pins nguyên tử; CAS và invalidation interactive cache. Kiểm model task/verification/provenance và reference readiness. Test A→B trước/sau snapshot: snapshot cũ giữ A, proposal/snapshot mới dùng B; job đang chạy A không publish vào basis B |
| B. Reference context | A | Nạp immutable dataset members/DINO artifacts vào context, không chỉ truyền UUID. Mở reference_diversity với nearest training evidence; artifact thiếu/lệch space/hash lỗi rõ, không lẫn train vào candidates. Test local/pilot parity và cache rebuild sau restart |
| C. Model-guided đúng checkpoint | A+B, thiết kế async được duyệt | Nối candidate stage + checkpoint của phiên vào durable worker lane phù hợp; deadline/memory/cancel và cache. Test exact model SHA, pixels/order, gốc/lật, empty/missing/failed khác nhau, stale basis, retry/dedup/owner isolation; đo cold/warm latency và bảo đảm recipe nhanh không bị inference chiếm process |
| D. ODD + evidence/report | A; có thể thiết kế song song B/C | Metadata theo unit hoặc time interval có provenance/revision; filter, thống kê unknown và target/deficit. Chuyển logic giải thích/quality vào DTO/UI/report hiện có. Test thiếu ODD không thành đủ coverage, metadata đổi không sửa snapshot cũ, baseline cùng pool/K và hiển thị actual_n |
| E. PII/export đáng tin | Hoàn tất upload #139; phối hợp A để snapshot đúng | Tách detector biển số thật, strict model readiness, artifact PII versioned/receipted; sửa FRAME lookup, anchor decode, bỏ ảnh giả khi thiếu source, stream ZIP, enqueue/retry recovery. Test một video thật có mặt/biển số, sai/missing artifact chặn xuất và ZIP đúng ordered snapshot members |
| F. Quyền và đánh giá bàn giao | C/D/E | Curator/reviewer với quyền backend/RLS nếu duyệt scope; chạy benchmark và end-to-end thật, báo cáo giới hạn. Không tuyên bố closed-loop train hoặc uplift nếu chưa đo |

Trong hai ngày: một người tiếp tục upload/PII-export; một người phụ trách A→B→C;
một người D + benchmark/report, nhận evidence contract chung từ đầu. Đây là phân công con người đề xuất,
không cam kết tất cả lát C/F sẽ kịp. Checkpoint và dataset train có provenance sẵn là điều kiện quan trọng.
Không đưa full retraining loop, MILP hay auth hệ khác vào critical path bàn giao.

## 5. Những thay đổi thiết kế cần chốt trước implementation

- Luồng async explicit cho model_guided là phần bổ sung cho §21.26, không khôi phục queue cho mọi proposal.
- Metadata ODD/provenance/revision, nơi lưu và semantics filter/target/hard cap cần vào CONTRACTS/architecture.
- PII cùng phiên Kaggle, artifact publication và pixel variant identity cần được mô tả trước code.
- Quyền reviewer độc lập khác owner/operator hiện tại; cần thiết kế RLS, transition và migration riêng.
- Nếu đổi thuật toán uncertainty từ greedy matching hiện tại sang Hungarian của nhánh nguồn: duyệt,
  tăng component version, invalidation cache và benchmark lại. Không trộn hai loại điểm cùng tên/version.

## 6. Bằng chứng và giới hạn lần rà soát

- Đọc worker model preparation → TrafficModel.infer → score_predictions → selector/explanation của nhánh nguồn;
  đối chiếu core/pilot reference resolver, interactive loader và proposal guards trên main.
- 44 test unit/logic của nhánh nguồn pass trên snapshot trích xuất, 3 integration tests được loại vì cần fixture/DB riêng.
  Lần chạy ban đầu có lỗi fixture/import do tắt conftest; không kết luận đó là lỗi ứng dụng.
- Kiểm tra riêng scorer: cùng box sau unflip = 0; hai lượt rỗng = null; detection biến mất = 1.
- Chưa chạy model thật, tải trọng số, cloud GPU, integration DB hoặc benchmark latency của kế hoạch tích hợp.
- Nghiên cứu downstream P3.2 trong nhánh nguồn kết luận gain chưa được hỗ trợ; P3.3b bị chặn do capacity.
  Không dùng các nghiên cứu này làm bằng chứng thuật toán sẽ tăng mAP cho dữ liệu người dùng.
- Không merge, đổi code sản phẩm, cập nhật thiết kế đã chốt, commit hoặc push trong lần lập đề xuất này.
