# Rà soát các nhánh chưa gộp — ý tưởng đưa vào backlog (2026-10-03)

Rà các nhánh remote có commit chưa nằm trong `main`. **Không gộp nhánh nào**: chỉ lấy ý tưởng, viết lại theo CONTRACTS + registry hiện tại khi tới lát tương ứng.

## ⚠️ Không được trích dẫn

`origin/nam-init/mvp-v2` — `eval/results/FINAL_ROI_REPORT.md` ghi "mAP50-95 Random 0.38 → Curated 0.45 (YOLOv8s, BDD100K)". Con số **giả**:

- `experiments/bdd100k_downstream/04_train_evaluate.py` không train: lớp `YOLO` giả trả cứng `0.45` nếu recipe là `diversity`, ngược lại `0.38`.
- `02_run_curation.py`: embedding DINOv2 và pHash là `np.random`; `03_yolo_bridge.py`: ảnh là file rỗng (`touch`), nhãn bốc ngẫu nhiên.
- Nhánh còn commit ~27k ảnh rỗng dưới `experiments/bdd100k_downstream/datasets/`.

Không dùng trong slide, báo cáo, Gate. Uplift chỉ được nói khi có thí nghiệm thật (focus §4B, quyết định #61). Đã kiểm: `main` không chứa con số này.

## Backlog ý tưởng

| # | Ý tưởng | Nguồn | Vì sao đáng làm | Cách làm khi tới lát |
|---|---|---|---|---|
| B1 | Objective `disagreement`: điểm rủi ro theo độ lệch giữa 2 detector (MVP nhánh cũ: chênh số box, `1 − exp(−0.5·diff)`) | `nam-init`: `src/rav/objectives/risk.py` | Bám selling point B (tìm chỗ model yếu); đọc được field `det.<model>` của contract bằng chứng (CONTRACTS §8.0) | Viết lại thành objective trong registry, đọc 2 field `det.*`; nâng lên so khớp IoU thay vì đếm box. `uses_goal = False` |
| B2 | Kiểm chứng uncertainty / disagreement: Spearman giữa điểm và lỗi ảnh `(FP+FN)/(TP+FP+FN)`, **chỉ trên validation** | `khanh`: `scripts/validate_uncertainty.py` | Trước khi UI / báo cáo nói "điểm cao = model sai" phải có bằng chứng này (#61) | Script / metric đánh giá trong P3, chạy trên tập có nhãn; báo cáo ở màn Phân tích mục Đánh giá |
| B3 | Thí nghiệm downstream thật: curate (recipe vs Random, cùng K) → dataset YOLO → train → mAP | `nam-init`: khung `experiments/bdd100k_downstream/01–04` | Đúng quy trình focus §4B (uplift so với Random) | Giữ cấu trúc 4 bước, thay toàn bộ phần giả: ảnh + nhãn BDD100K thật, train thật, nhiều seed. Dữ liệu trong `data/`, không commit ảnh |
| B4 | Bộ chủ đề ADAS bối cảnh Việt Nam (xe máy tạt đầu điểm mù, mưa đêm chói đèn, người đi bộ ban đêm, vòng xuyến hỗn loạn, công trường, …) | `thien`: `web/src/data/topics.ts` | Mẫu mục tiêu phiên / danh mục cho tính năng "chủ đề" (đã để sau) | Chỉ lấy tên + mô tả tiêu chí; các ngưỡng kiểu "phanh −4.8 m/s²", "cự ly < 15 m" là bịa cho pool mock — phải đo được từ dữ liệu mới được hiện |
| B5 | Nguyên tắc adapter model thật: "lỗi thì dừng, không bao giờ sinh điểm giả"; manifest không đọc nhãn | `khanh`: `scripts/infer_torchvision.py`, `import_dataset.py` | Khớp nguyên tắc "không có nguồn → không hiện" (UI_DESIGN §3b) | Áp dụng khi viết feature `det.*` / `tag.*` thật |

## Nhánh không còn gì để lấy

- `develop` — đã gộp hết vào `main`.
- `duy-mvp/init` — MVP cũ; `duy-mvp/v2` làm lại từ `0fd6c91` thay thế.
- `duy` — pipeline cũ đã lỗi thời; khảo sát thị trường đã có trong `04_market_and_competitors/`, `05_hands_on_audit/`.
- `khanh`, `thien` (phần còn lại) — kiến trúc khác (`app/`, UI mock); UI tốt của `thien` đã gộp ở lát L8b.
- `nam-init/mvp-v2` (phần còn lại) — UI / backend là bản sao cũ của `duy-mvp/v2`; `docs/EVALUATION_METRICS.md` trùng ND-Rate + PCA đã có, phần ROI dựa trên số giả ở trên.
