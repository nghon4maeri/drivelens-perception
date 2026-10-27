# units — thứ được chấm, chọn, duyệt, export

Contract: [CONTRACTS §5 dòng 3](../../../../../design/CONTRACTS.md); kiểu `Unit` ở §2 (id `"{video_id}:{t0:.3f}-{t1:.3f}"`), thứ tự `(video_id, t0)` ở §13.

- MVP: `per_frame` (`kind="frame"`, `t0 == t1`); UI tua ±vài giây quanh `anchor`.
- Đổi unit builder làm đổi `unit_id` → thuộc session spec, muốn đổi thì tạo phiên mới (§10).

**Thêm implementation:** class `@register("unit")` trong `src/rav/components/units/`.

**Test tối thiểu:** id deterministic và duy nhất trên pool 2 video.

**Ý tưởng:** `window` sinh `Unit(kind="span")` (CONTRACTS §8).
