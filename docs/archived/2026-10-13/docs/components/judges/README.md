# judges — frame có đáng giữ theo `goal`

Contract: [CONTRACTS §5 dòng 15, §12](../../../../../design/CONTRACTS.md) (`JudgeRequest`, `Decision`, quy tắc độc lập §12.5).

**MVP: chỉ registry + contract, chưa implement** (quyết định #20). Người làm judge qua UI; mọi quyết định ghi log đủ schema §12.3.

- `reason` bắt buộc với actor `algo:`/`ai:`; judge dùng cùng tín hiệu với metric → kết quả không phải bằng chứng.
- Trước khi thay người: chạy judge trên proposal người đã duyệt, đo tỉ lệ đồng ý / Cohen's κ.

**Ý tưởng:** `rule_dup`, `rule_quality`, `oracle_labels`, `learned` (học từ log), `vlm` (§12.4).
