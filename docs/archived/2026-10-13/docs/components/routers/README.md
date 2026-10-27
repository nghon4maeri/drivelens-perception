# routers — vòng sau làm gì

Contract: [CONTRACTS §5 dòng 16, §12](../../../../../design/CONTRACTS.md) (`Action = Propose(recipe, config?, batch_size) | Stop`, #45).

**MVP: chỉ registry + contract, chưa implement** (quyết định #20). Người chọn recipe + bấm "Đề xuất" là router thủ công.

- Rủi ro Goodhart: router tối ưu theo keep rate của judge tự động → luôn kèm metric độc lập (§12.5).

**Ý tưởng:** `fixed`, `round_robin`, `bandit` (UCB/Thompson theo `keep_rate_by_recipe`), `llm_agent` (§11).
