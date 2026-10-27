# feedback — quyết định đổi state thế nào

Contract: [CONTRACTS §5 dòng 10](../../../../../design/CONTRACTS.md); `Decision` + log quyết định §12.3; `ReviewState = replay(history)` §2; không keep vượt `target_k` §9.7.

- MVP: `keep_drop`: `keep`/`drop` thêm vào tập tương ứng, `clear` bỏ quyết định trước. Unit hiện mà chưa phản hồi quay lại pool (§9.3).
- Trả state mới. Không cần invalidate cache: feature không đọc state (CONTRACTS §3).

**Thêm implementation:** class `@register("feedback")` trong `src/rav/components/feedback/`.

**Test tối thiểu:** replay keep → drop → clear cho cùng unit ra state rỗng; thứ tự log giữ nguyên.

**Ý tưởng:** more/less-like-this, tag lý do qua `Decision.extra` (§8, §12.3).
