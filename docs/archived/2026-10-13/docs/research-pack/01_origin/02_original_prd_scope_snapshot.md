# Snapshot phạm vi PRD Gate 1 ban đầu

**Status: ORIGINAL**

Đây là snapshot ngắn của PRD Gate 1, không phải requirement hiện tại.

## Goals gốc

- phân tích pool dữ liệu bằng uncertainty, diversity và coverage;
- xếp hạng Top-K sample và giải thích lý do;
- cho phép kỹ sư approve/reject và tạo labeling batch;
- so sánh Agent Selection với Random Selection trên cùng annotation budget;
- theo dõi coverage, duplicate và active-learning iteration.

## Core workflow gốc

```text
Input Dataset
→ AI Analysis
→ Scoring: Uncertainty + Diversity + Coverage/Novelty
→ Agent Ranking
→ Human Review
→ Labeling
→ Retrain & Evaluation
```

## Functional blocks gốc

Dataset Overview; Dataset Explorer; Feature/Embedding; Uncertainty Scoring; Diversity Scoring; Agent Ranking; Human Review; Labeling Handoff; Evaluation; Iteration History.

## Success metrics gốc

- mAP/F1 sau labeling/retraining;
- duplicate/redundancy;
- ODD/class coverage;
- human approval rate;
- batch-selection time;
- annotation efficiency.

## Architecture gốc

Python/PyTorch, pretrained detector, CLIP/DINOv2, FAISS/Qdrant, agent/orchestration, FastAPI, React/Next.js, CVAT/Label Studio, MLflow/W&B, DVC, Docker + cloud GPU.

## Ghi chú

Nhiều block ở trên sau đó được xác định là quá rộng cho MVP đầu tiên. Chúng được giữ ở đây để giải thích lịch sử scope, không tự động là requirement hiện hành.
