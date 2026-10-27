# Những gì đã xác nhận và chưa xác nhận

> **LƯU TRỮ 2026-10-09.** Bản gốc: `docs/research-pack/05_hands_on_audit/05_verified_vs_unverified.md`. Chỉ tra lịch sử/tham khảo; đọc [index hiện hành](../../../README.md), không dùng các lệnh/hiện trạng cũ như contract.


## Đã xác nhận

### Market / capability
- Similarity, diversity và deduplication đã tồn tại ở các data-curation tools.
- FiftyOne và LightlyStudio đều có OSS codebase/local path được audit.
- Không thể coi “embedding + Top-K diverse gallery” là capability hoàn toàn mới.

### FiftyOne
- ingest data;
- similarity index;
- nearest similarity query;
- diversity/unique selection bằng test vectors;
- tags;
- export;
- plugin/operator extension points;
- reference-aware next-round mechanics có thể làm bằng custom policy.

### LightlyStudio
- video/frame ingest;
- diverse/deduplicate/preselected APIs tồn tại trong source;
- local GUI có thể launch;
- sequence sampling support có source evidence.

## Chưa xác nhận

- tool nào có video-review UX tốt hơn;
- tool nào tạo semantic ADAS frame selection tốt hơn;
- LightlyStudio diversity/dedup runtime bằng real embeddings;
- full FiftyOne review/video UI flow;
- full LightlyStudio UI flow;
- Near-Duplicate Rate@K thực tế trên cùng semantic encoder;
- incremental novelty có tương quan actual data value;
- selected frames giúp downstream model improve;
- final build-vs-buy choice.

## Build-vs-buy status

**No final recommendation** tại thời điểm audit (01–05).

Audit cuối cố ý không chốt vì UI flow và real-embedding evaluation vẫn thiếu.

**Cập nhật 2026-09-27:** sau lần thử lại với embedding thật (`06_findings_2026-09-27.md`), nhóm chốt tự làm giao diện
(quyết định #15) dựa trên công sức tích hợp và độ khó tiếp cận — không phải vì hai tool thiếu tính năng.
