# RAV-26 Research Pack

**Mục đích:** tạo một bộ tài liệu ngắn, có cấu trúc, để luôn biết RAV-26 bắt đầu từ đâu, đã được hiểu lại thế nào, research nào hỗ trợ, competitor nào đã khảo sát, thực nghiệm nào đã chạy, và những gì đã/chưa được xác nhận.

## Quy tắc biên soạn

Bộ này **không phải một PRD mới** và **không tự thêm product workflow mới**.

Chỉ giữ:
- **ORIGINAL:** nội dung từ đề bài/PRD Gate 1 ban đầu.
- **CONFIRMED:** quyết định hoặc cách hiểu đã được chốt rõ.
- **EVIDENCE / VERIFIED:** kết quả research hoặc audit có nguồn/bằng chứng.

Ý tưởng từng được bàn nhưng chưa chốt **không được biến thành requirement hay direction chính thức**.

## Cấu trúc

```text
research-pack/
├── 00_README.md
├── 01_origin/
│   ├── 01_original_rav26_problem.md
│   └── 02_original_prd_scope_snapshot.md
├── 02_problem_understanding/
│   ├── 01_problem_reframing_confirmed.md
│   ├── 02_terms_and_distinctions.md
│   └── 03_evaluation_scope_confirmed.md
├── 03_research_backing/
│   ├── 01_research_evidence_summary.md
│   ├── 02_searchad_notes.md
│   ├── 03_perception_reference_models.md   (khảo sát 27/09: YOLO11 mục tiêu vs tham chiếu)
│   └── 04_vlm_frame_criteria.md   (research 12/10: tiêu chí VLM lọc frame theo mục tiêu + rubric báo cáo; chưa là hợp đồng)
└── 06_project_state/
    ├── 01_confirmed_decisions.md
    └── 02_source_index.md
```

## Đọc nhanh

Nếu chỉ có 10 phút:
1. `01_origin/01_original_rav26_problem.md`
2. `02_problem_understanding/01_problem_reframing_confirmed.md`
3. `06_project_state/01_confirmed_decisions.md`
4. [PRODUCT_STORY hiện hành](../../../../PRODUCT_STORY.md) và [PLAN](../../../../../PLAN.md).

Market/OSS audit cũ (các nhóm 04/05) đã chuyển vào [archived/research](../../../README.md). Đây là bằng chứng ở version/thời điểm đã ghi, không được dùng như kiểm chứng provider/tool hiện tại. Các [finding 27/09](../../../research/05_hands_on_audit/06_findings_2026-09-27.md) và [competitor snapshot](../../../research/04_market_and_competitors/02_competitor_landscape.md) vẫn có thể tra để hiểu quyết định build/buy.
