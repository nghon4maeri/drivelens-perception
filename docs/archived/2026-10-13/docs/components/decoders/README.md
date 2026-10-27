# decoders — video thành frame và pixel

Contract: [CONTRACTS §5 dòng 2](../../../../../design/CONTRACTS.md); kiểu `Frame` ở §2 (địa chỉ, không chứa pixel). Session spec ở §7 (`fps` cố định theo phiên). Quyết định #37.

- MVP: `pyav`, **4 fps** (quyết định #29). Decoder **serve frame theo yêu cầu**: không ghi file ảnh; thumbnail cho UI qua command `get_frame`.
- `frames(video)` tính từ metadata (tất định) → dựng lại unit không cần decode; `read(video, frame)` giữ file mở, đọc tăng dần thì decode tiếp thay vì seek.
- Bẫy Windows: đường dẫn có dấu → không dùng `cv2.imread/imwrite`; PyAV mở được đường dẫn có dấu.
- Upload dùng decoder để mở thử file (architecture §5.1).

**Thêm implementation:** class `@register("decoder")` trong `src/rav/components/decoders/`.

**Test tối thiểu:** video 30 s @4 fps → ≈ 120 `Frame`, `t` tăng dần; `read(video, frame)` trả ảnh RGB đúng shape.

**Ý tưởng:** decode theo keyframe, `decord`/`PyAV` nhanh hơn.
