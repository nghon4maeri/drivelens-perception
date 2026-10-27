# selectors — chọn tập từ điểm gộp

Contract: [CONTRACTS §5 dòng 9](../../../../../design/CONTRACTS.md); quyết định §9.2/9.6/9.8; so thời gian chỉ trong cùng video §13.6; relax policy cho text §15 và V2 §18.

| Hiện có | Cách làm / định nghĩa tại CONTRACTS |
|---|---|
| `greedy_nms` | Diversity/NMS theo §9.2/9.6; recipe cũ giữ relax policy. Text recipe dùng `relax=False`, có thể trả ít/rỗng theo §15; không tự nới hard mask. |
| `random` | Ngẫu nhiên không lặp theo `ctx.rng` (propose đã seed lại). Baseline. |
| `uniform` | Cách đều trên **candidates** của từng video (theo `t0`): video có n candidates, k suất → chỉ số `floor((i + 0.5)·n/k)`. `allocation`: `proportional` chia K theo số candidates mỗi video, `equal` chia đều; làm tròn theo phần dư lớn nhất (tổng = K; hoà → theo `video_id`); video thiếu candidates thì phần dư chia cho video còn lại. Không dùng seed. Baseline. |
| `explore_diversity` | Ranking + exploration có seed, NMS/min_dist và batch ít/rỗng theo §18; không nới để đủ số. |

Kiểu trả về và bất biến theo CONTRACTS §2/9.6/15/18: indices duy nhất trong candidates hợp lệ, không kept/dropped, không phá mask; cùng seed tái lập.
Không yêu cầu mọi selector lấp đủ batch: relax policy và gate đã đóng băng của recipe quyết định số trả về.

**Thêm implementation:** class `@register("selector")` trong `src/rav/components/selectors/`, rồi thêm recipe trong `recipes.py`.

**Test tối thiểu:** pool 2 video, indices duy nhất/không kept-dropped/tái lập; baseline đủ khi có candidates, text/V2 thiếu hợp lệ trả ít/rỗng đúng gate (§15/18).

**Ý tưởng:** `kcenter` (coreset), `mmr`, `cluster_stratified` (k-means rồi lấy đều mỗi cụm).
