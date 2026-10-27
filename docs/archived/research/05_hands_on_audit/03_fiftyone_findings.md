# FiftyOne — findings từ hands-on audit

> **LƯU TRỮ 2026-10-09.** Bản gốc: `docs/research-pack/05_hands_on_audit/03_fiftyone_findings.md`. Chỉ tra lịch sử/tham khảo; đọc [index hiện hành](../../../README.md), không dùng các lệnh/hiện trạng cũ như contract.


**Status: API_VERIFIED + SOURCE_VERIFIED + UI PARTIAL**

## Runtime / source

Audit ghi nhận source repo được pin và license Apache-2.0.

Có một inconsistency trong report: amendment nói runtime 1.22.0, trong khi phần metadata cũ còn ghi installed 1.23.0. Cần reconcile trước khi dùng version info cho benchmark chính thức.

## API đã chạy

Trên fixture:
- load frames;
- store deterministic RGB test vectors;
- `compute_similarity`;
- select unique samples;
- tag samples;
- query incoming samples against existing samples;
- export media.

Artifacts của audit ghi lại selected IDs và incoming-to-existing nearest pairs.

## Reference-loop PoC

Agent dựng PoC:

```text
4 proposed
→ 2 simulated accepts
→ 4 reference-aware next candidates
```

PoC chỉ chứng minh **mechanics/API feasibility**.

Nó không chứng minh selection quality vì:
- descriptor là RGB test vector, không phải semantic ADAS embedding;
- accept/reject là simulated.

## Extension

Source audit xác nhận FiftyOne có Plugins/Operators extension points.

Điều này chứng minh có đường extension mà chưa cần mặc định phải fork upstream.

## UI

UI đầy đủ bị BLOCKED bởi browser automation.

Chưa verify:
- video seek theo timestamp;
- side-by-side nearest historical review;
- approval UX;
- full export UX.

Không được diễn giải blocker này thành “FiftyOne không hỗ trợ”.
