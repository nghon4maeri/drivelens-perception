# Checklist alignment với đề bài RAV-26 gốc

> Ghi 2026-09-27. Đề bài gốc: [01_original_rav26_problem.md](../research-pack/01_origin/01_original_rav26_problem.md),
> [02_original_prd_scope_snapshot.md](../research-pack/01_origin/02_original_prd_scope_snapshot.md).
> **Rà file này trước mỗi mốc** (chốt scope, trước demo, trước khi thêm tính năng lớn). Cập nhật cột *Trạng thái* và *Bằng chứng*.

Ký hiệu: ✅ MVP đáp ứng · 🟡 đáp ứng một phần / proxy · ⏳ chừa chỗ, phase sau · ❌ ngoài phạm vi (có lý do)

## 1. Vấn đề gốc → MVP giải quyết thế nào

| # | Vấn đề trong đề bài gốc | Trạng thái | MVP đáp ứng bằng | Bằng chứng cần có |
|---|---|---|---|---|
| P1 | Quá nhiều frame gần giống nhau đưa vào annotation | ✅ | Chọn K frame ít trùng; objective novelty + selector NMS thời gian | ND-Rate@K thấp hơn Random/Uniform, cùng K, nhiều seed (`eval/results/report.md`) |
| P2 | Bỏ sót rare case / ODD chưa cover | 🟡 | Novelty so với tập đã keep + embedding map/timeline để người dùng thấy vùng chưa chọn | Chỉ minh hoạ trực quan; **chưa** có Coverage@K / Event Recall (cần nhãn) |
| P3 | Chi phí annotation tăng | 🟡 | Ít frame trùng hơn trong batch export | Proxy: ND-Rate + keep rate; **không** claim % giảm chi phí |
| P4 | Không có quy trình định lượng chứng minh batch tốt hơn random | ✅ | CLI benchmark recipe × seed × K; metric độc lập (pHash) với embedding dùng để chọn | Bảng benchmark + spot-check cặp trùng (`duplicate_pairs`) |

## 2. Mục tiêu người dùng gốc

| # | Mục tiêu gốc | Trạng thái | Ghi chú |
|---|---|---|---|
| U1 | Chọn sample giá trị cao để gán nhãn | 🟡 | "Giá trị" MVP = ít trùng + mới so với tập đã keep; chưa phải training value |
| U2 | Giảm annotation cost | 🟡 | Như P3 |
| U3 | Cải thiện model | ⏳ | Cần labeling + retrain + holdout; không claim trong MVP |
| U4 | Có explanation để tin batch được đề xuất | ✅ | `breakdown` điểm từng objective cho mỗi frame; snapshot input của proposal |
| U5 | Human review | ✅ | Keep/drop từng frame kèm ngữ cảnh ±vài giây; vai trò Judge ([CONTRACTS §12](../../../../design/CONTRACTS.md)) |

## 3. Goals / workflow PRD Gate 1

| # | Mục PRD gốc | Trạng thái | Ghi chú |
|---|---|---|---|
| G1 | Phân tích pool bằng uncertainty | ⏳ | Chừa chỗ: feature detection + objective disagreement (YOLO11s/m vs 11x) |
| G2 | … bằng diversity | ✅ | Selector đa dạng + objective novelty |
| G3 | … bằng coverage | 🟡 | Trực quan hoá; metric coverage chờ nhãn |
| G4 | Xếp hạng Top-K + giải thích | ✅ | Proposal = k pick có rank + breakdown |
| G5 | Approve/reject + tạo labeling batch | ✅ | Keep/drop + export CSV + ảnh cho CVAT/Label Studio |
| G6 | So selection với Random cùng budget | ✅ | Benchmark cùng K, nhiều seed |
| G7 | Theo dõi coverage, duplicate, AL iteration | 🟡 | Duplicate ✅; lịch sử proposal/quyết định ✅; coverage và AL iteration ⏳ |
| W1 | Workflow: Input → Analysis → Scoring → Ranking → Human Review → Labeling → Retrain | 🟡 | MVP phủ tới Human Review + handoff labeling; Retrain ⏳ |
| W2 | Agent Ranking / Agent Review | ⏳ | Đã bỏ khỏi MVP (quyết định #5); workflow mới cho phép thuật toán/AI đóng vai Router/Judge ([CONTRACTS §12](../../../../design/CONTRACTS.md)) |

## 4. Success metrics gốc

| Metric gốc | Trạng thái | MVP dùng |
|---|---|---|
| mAP/F1 sau labeling/retrain | ⏳ | – |
| Duplicate/redundancy | ✅ | ND-Rate@K |
| ODD/class coverage | ⏳ | Cần nhãn (BDD100K có) |
| Human approval rate | ✅ | Keep rate theo recipe |
| Batch-selection time | 🟡 | Ghi thời gian propose như thông tin vận hành |
| Annotation efficiency | 🟡 | Proxy qua ND-Rate + keep rate |

## 5. Những gì MVP thêm ngoài đề bài (phải giải thích được)

| Thêm | Vì sao vẫn align |
|---|---|
| Vòng đề xuất nhiều lượt với keep/drop | Là "human review" + "inter-batch reference" của PRD phase sau, làm sớm ở mức tối giản |
| Kiến trúc component/recipe thay được | Để thêm uncertainty/coverage/sequence mà không viết lại — phục vụ G1, G3 |
| Workflow Router/Judge + log quyết định (tự động hoá được sau) | Phục vụ G6/G7 (đánh giá lặp lại được) và mở đường cho Agent Review (W2) |
| Trực quan hoá | Phục vụ U4 (giải thích) và P2 (thấy vùng chưa cover) |

## 6. Câu hỏi rà nhanh trước mỗi mốc

- [ ] Tính năng sắp làm phục vụ dòng nào ở §1–§4? Nếu không có → ghi vào §5 kèm lý do, hoặc bỏ.
- [ ] Có claim nào vượt bằng chứng không (mAP, % chi phí, rare-event recall)?
- [ ] Metric đánh giá có độc lập với tín hiệu dùng để chọn không?
- [ ] Human review vẫn còn trong luồng chính không (tự động hoá chỉ là tuỳ chọn, không thay thế claim "có người duyệt")?
- [ ] Các dòng ⏳ có còn đường mở rộng trong contract không?

## 7. Đánh giá kiến trúc hiện tại so với đề bài (2026-09-27)

Đối chiếu `docs/architecture_diagram.md` + `CONTRACTS.md` với đề bài gốc và PRD v2.1 (`docs/gate-1/`).

### 7.1. Khớp tốt

| Đề bài | Kiến trúc đáp ứng |
|---|---|
| Giảm trùng lặp, đo định lượng so với Random cùng K (P1, P4, G6, FR-07) | Proposer thay recipe bằng config; CLI benchmark recipe × seed × K; metric pHash độc lập với embedding chọn; quy tắc độc lập (§12.5) |
| Giải thích để tin batch (U4) | `breakdown` từng objective + snapshot input của proposal |
| Human review (U5) | Judge = người, keep/drop kèm ngữ cảnh ±vài giây; log quyết định |
| Handoff gán nhãn (G5) | Exporter CSV + ảnh |
| Mở rộng uncertainty / coverage / sequence (G1, G3) | Feature dạng structured (detection) + objective mới; unit dạng span |
| Hiệu chỉnh ngưỡng gần trùng bằng mắt (PRD §7) | View `duplicate_pairs` |

### 7.2. Lệch hoặc thiếu — cần quyết định

| # | Vấn đề | Mức | Đề xuất |
|---|---|---|---|
| A1 | **Quy mô:** đề bài nói về *pool dữ liệu rất lớn nhiều video*; kiến trúc từng gắn **phiên với 1 video** | ✅ Đã xử lý | Pool của phiên là bộ lọc trên bảng unit toàn cục (MVP lọc 1 video); thuật toán không phụ thuộc số video; `session_id` = uid (CONTRACTS §13, tham khảo AIC-HCMC-2026) |
| A2 | **Ngân sách gán nhãn** là trọng tâm đề bài | ✅ Đã xử lý | Phiên có `target_k` (K = tổng số frame muốn chọn); mỗi vòng đề xuất `batch_size`; lặp tới khi kept = K |
| A3 | **FR-01 cần pool ≥ 1.000 frame.** Dữ liệu trong `data/` chỉ là mock để thử (30 s ≈ 60 frame @2 fps) | Đang xử lý | Decoder 4 fps (quyết định #29); benchmark trên `data/youtube5m/6WTF1JV5knI.mp4` (5 phút ≈ 1.200 frame), PLAN lát L4 |
| A4 | **Giải thích mới chỉ là con số** (`breakdown`) | ⏳ Để sau | MVP chỉ trả điểm; giải thích trực quan cần bằng chứng hiệu quả, bàn sau (Lightly/FiftyOne cũng không có cơ chế giải thích riêng) |
| A5 | **Coverage / rare case (P2, G3) không có bằng chứng** trong MVP: video YouTube không có nhãn; nhãn BDD100K chủ yếu theo video (thời tiết, cảnh, giờ), không theo từng frame | Chấp nhận | Giữ 🟡, chỉ minh hoạ trực quan; không claim |
| A6 | **PRD v2.1 ghi rõ non-goal "approve/reject workflow có state"**, nhưng MVP có vòng keep/drop lưu state | Chấp nhận có lý do | Đã chốt ở quyết định #11 (human review là yêu cầu của đề bài gốc); ghi ở §5 |
| A7 | **Vòng Active Learning** (label → retrain → đánh giá) không có đường nhập lại nhãn / kết quả model vào hệ thống | Thấp (phase sau) | Chừa chỗ: `reference` nhận tập đã gán nhãn từ ngoài; feature nhận output model mới |
| A9 | **Frame "khác biệt" có thể chỉ là nhiễu thoáng qua** (gạt nước, lóa, mờ). Nghiên cứu đã cảnh báo diversity/khác biệt cục bộ một mình chưa đủ ([research evidence](../research-pack/03_research_backing/01_research_evidence_summary.md) §1). Novelty + NMS dễ ưu tiên đúng những frame này | Rủi ro | Ghi nhận; spot-check bằng mắt khi benchmark; có sẵn chỗ cho objective `threshold` theo độ nét (CONTRACTS §7) nếu thấy vấn đề |
| A10 | **Novelty so với tập đã keep = incremental novelty**, giá trị thật **chưa được chứng minh** ([reframing](../research-pack/02_problem_understanding/01_problem_reframing_confirmed.md) §5, [thuật ngữ](../research-pack/02_problem_understanding/02_terms_and_distinctions.md)) | Lưu ý khi trình bày | Nói "khác so với cái đã giữ", không nói "có giá trị hơn" |
| A8 | **Độ rộng thiết kế so với 4 ngày:** 16 extension point (kể cả view, judge, router) + command layer + web | Rủi ro tiến độ | Code tối giản: mỗi extension point 1 implementation, base class mỏng; ưu tiên luồng chạy được trước |

## 8. Rà mốc V2 (2026-10-07, #72)

V2 theo CONTRACTS §18 phục vụ G1/G3 (mốc dataset train + tín hiệu YOLO), U4 (nearest train, scalar có method, prediction) và U5 (người duyệt keep/drop). Hạ tầng chỉ mở thêm binding/requested cache/candidate stage/job; component không gọi DB, recipe cũ giữ hash/hành vi, không nhập reference vào pool.

Smoke DINO/CLIP/YOLO thật xác nhận luồng và giới hạn 16 ứng viên trên pool 86; không là benchmark hiệu quả curation. Dataset tham chiếu smoke là mô phỏng, không là train của nano COCO. Instability là proxy, 0 box không khẳng định model đúng; nearest/selection gain không là giá trị gán nhãn. Quota/weights heuristic cần so Random/diversity cùng K và metric độc lập. AP/mAP sau retrain, ODD/rare-event recall và chi phí annotation vẫn chưa có bằng chứng; việc đó còn ở backlog, không chuyển dấu sang đạt.

## 9. Rà chất lượng truy vấn V2E (2026-10-07)

[Giao thức và kết quả](../evaluation/V2_CRITERIA.md) kiểm G6 (đánh giá lặp lại được) và mức đáp ứng tiêu chí ngữ nghĩa G1/G3 bằng ảnh được chọn, độc lập CLIP/YOLO. 206 frame thật từ hai video nhỏ, reference train mô phỏng, một người chấm; không suy rộng kết quả.

Luồng/gate/NMS/seed/budget/cache đúng nhưng truy vấn chưa đạt: câu cao tốc có 4/8 ảnh khớp với text_diversity, 1/8 guided (Uniform 3/8); đèn/đêm/tuyết/chó đều 0/8 dù trả đủ 8. Ngưỡng 0.2 nhận cả 206 frame cho sáu câu. Negative giúp trên pool này, chưa chứng minh hiểu goal hoặc tìm đối tượng chính xác. Instability/novelty không thay ground truth; giữ mAP/ODD/hiệu quả curation ở trạng thái chưa có bằng chứng. Không đổi thiết kế theo kết quả xấu; hiệu chỉnh và giữ relevance trong final là đề xuất lát sau.
