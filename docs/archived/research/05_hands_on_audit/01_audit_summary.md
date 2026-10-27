# Hands-on audit summary

> **LƯU TRỮ 2026-10-09.** Bản gốc: `docs/research-pack/05_hands_on_audit/01_audit_summary.md`. Chỉ tra lịch sử/tham khảo; đọc [index hiện hành](../../../README.md), không dùng các lệnh/hiện trạng cũ như contract.


**Status: VERIFIED / PARTIAL**

Mục tiêu audit:

> Kiểm tra FiftyOne và LightlyStudio ở mức source + API + local runtime, thay vì chỉ đọc feature list.

## Shared fixture

Audit cuối sử dụng một rainy urban dashcam clip khoảng 30 giây và trích xuất 60 frame cho các lần chạy liên quan.

## Agent đã làm

- clone/pin source;
- kiểm tra license;
- tạo local environment;
- ingest video/frames;
- chạy một số API selection/similarity;
- thử launch UI;
- inspect source code;
- tạo một reference-loop PoC nhỏ cho FiftyOne.

## Blocker chính

Browser/UI automation gặp Chrome breakpoint error.

Do đó:
- FiftyOne chỉ verify được basic gallery shell;
- LightlyStudio verify được server launch;
- full video/review UX vẫn **BLOCKED**, không được coi là unsupported.

## Ý nghĩa

Audit chứng minh được một số **software primitives** tồn tại và chạy được.

Audit chưa chứng minh:
- selection quality trên semantic ADAS embeddings;
- UI usability;
- training value;
- final build-vs-buy choice.
