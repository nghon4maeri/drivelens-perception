# Feature comparison từ audit

> **LƯU TRỮ 2026-10-09.** Bản gốc: `docs/research-pack/05_hands_on_audit/02_feature_comparison.md`. Chỉ tra lịch sử/tham khảo; đọc [index hiện hành](../../../README.md), không dùng các lệnh/hiện trạng cũ như contract.


**Status: VERIFIED MATRIX**

| Capability | FiftyOne OSS | LightlyStudio OSS |
|---|---|---|
| Video/frame ingest | **API_VERIFIED** on 60 dashcam frames | **API_VERIFIED** on 1 video / 60 frames |
| Diversity | **API_VERIFIED** through `find_unique` with test vectors | **SOURCE_VERIFIED**, runtime **NOT_TESTED** |
| Nearest similarity | **API_VERIFIED** through `sort_by_similarity` | **NOT_TESTED** |
| Deduplication | **NOT_TESTED** | **SOURCE_VERIFIED**, runtime **NOT_TESTED** |
| Sequences | **SOURCE_VERIFIED** `to_frames` | **SOURCE_VERIFIED** sequence sampling |
| Review/reference loop | **API_VERIFIED POC** via tags + custom policy | **SOURCE_ONLY** `preselected_tag_name` |
| UI evaluation | **UI_VERIFIED** gallery shell only | **UI_LAUNCH_LOGGED** only |
| Extensions | **SOURCE_VERIFIED** plugins/Operators | **NOT_TESTED** custom extension route |

**Quan trọng:** `NOT_TESTED` không có nghĩa là unsupported.
