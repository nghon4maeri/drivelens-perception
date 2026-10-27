# CLIP, SigLIP 2 và Gemma 4 — benchmark CPU Contabo

2026-10-13. Người dùng yêu cầu xem encoder mới và cách tách model. [Runtime](../setup/COMPUTE_RUNTIME.md), CONTRACTS §21.22.
Chưa thay model mặc định/FieldKey/threshold/pilot API. Không áp các skill theo yêu cầu người dùng.

## Lựa chọn

- **Hiện tại: CLIP text-only.** Giữ model/revision/embedding space cũ, chỉ bỏ vision weights khỏi process tính text.
  CLIP image vẫn GPU/local prepare như trước; không phải retrain/re-prepare vì vector parity đã kiểm.
- **Thử nghiệm tiếp: SigLIP2 base 224.** Model được thiết kế cho image-text retrieval/zero-shot và có API text/vision riêng;
  hợp bài toán ranking hơn việc lấy hidden state bất kỳ từ một LLM. Đây chưa là kết luận tốt hơn trên ADAS.
  [Google model card](https://huggingface.co/google/siglip2-base-patch16-224),
  [encoder APIs](https://huggingface.co/docs/transformers/model_doc/siglip2).
- **Gemma 4: research cho reasoning/report/VLM judge, chưa benchmark.** Family sinh đa phương thức có vision encoder ở một số variant,
  còn 12B encoder-free. Tách tower không tự tạo cặp text/vision contrastive đã aligned thay CLIP;
  chưa có bằng chứng trong lượt này cho khả năng thay trực tiếp cosine retrieval pipeline.
  Không tải checkpoint gated/LLM hoặc upgrade toàn repo chỉ để thử model lớn.
  [Google Gemma 4 card](https://ai.google.dev/gemma/docs/core/model_card_4).

## Môi trường và độ tin cậy

VPS 4 vCPU/~8 GiB, candidate image dùng worker runtime pinned Transformers 4.57.6/Ultralytics 8.3.228,
benchmark container CPU cap 3, memory cap 6 GiB, mỗi encoder Torch intra-op 1 thread/inter-op 1.
N=8 text EN/VN và N=8 ảnh tổng hợp 640×360; official preprocessing từng model, float32 + L2, 7 warm repeats.
Chạy lần lượt; shared VPS còn live service, không là randomized A/B hoặc p95/SLA. RSS là resident sau inference, **không peak/PSS**.
Load timer gồm tải checkpoint nếu cache miss nên không so cold load giữa model. Ảnh tổng hợp kiểm parity/performance,
không có ground truth ADAS, không đo retrieval/accuracy hay chất lượng tiếng Việt.

Checkpoints/revisions:

- CLIP `openai/clip-vit-base-patch32@3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268`.
- SigLIP2 `google/siglip2-base-patch16-224@75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2` (public Apache-2.0).
  Bản fixed-resolution này dùng lớp SiglipModel/TextModel/VisionModel trong runtime hiện có; không giả mọi SigLIP2 variant cùng API.

| Variant | Params | RSS sau inference (MiB, xấp xỉ) | Text 8 câu warm p50 | Vision 8 ảnh warm p50 |
|---|---:|---:|---:|---:|
| CLIP full | 151.277.313 | 987 | 0,349 s | 1,766 s |
| CLIP text-only | 63.428.096 | 624 | 0,357 s | — |
| SigLIP2 full | 375.187.970 | 1.440 | 1,827 s | 6,138 s |
| SigLIP2 text-only | 282.303.744 | 1.191 | 2,077 s | — |
| SigLIP2 vision-only | 92.884.224 | 1.226 | — | 6,144 s |

Split/full output max absolute error **0** trên cùng mẫu cho CLIP text, SigLIP2 text, SigLIP2 vision.
Tách tower giúp quản lý/resident weights theo workload, không mặc định tăng tốc forward hoặc giảm tổng RAM nếu cả hai tower
chạy trong hai process (framework/activation overhead có thể nhân lên; RSS cũng có shared pages).
SigLIP2 text base trong phép thử nặng hơn CLIP text, không chọn chỉ vì model mới.

## Model owners trên source candidate thật

Gọi `clip_b32.encode_texts` đã sửa, cùng 8 câu: max error so full baseline **5,96e-8**; cold offline module/model load+encode 13,385 s,
cache prompt hit 0,0000177 s (một lượt, không SLA), RSS 600 MiB.
YOLO checkpoint public cached `0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1.pt`, 8 ảnh tổng hợp:
cold load+inference 1,536 s; ba warm lượt 1,143/1,091/1,095 s, kết quả giống nhau, model load count=1,
Torch threads sau predictor init=1. RSS cùng process chứa CLIP text+YOLO khoảng 667 MiB.
Đây là kiểm model reuse, không là test hai process model chạy đồng thời hoặc ba model có PII.

Script reproducible: `scripts/benchmark_encoders.py --variant ... --output output/encoder-bench --threads 1`.
Evidence ignored `output/compute-runtime/encoder-evidence`, candidate `output/encoder-bench/*.{json,npz}`, `model-owner.json`.
Không commit weights hoặc output; không credentials production trong containers test.

## Trước khi dùng SigLIP2 cho phiên thật

1. Dataset evaluation gồm scene/nhãn độc lập theo video/trip, query EN/VN và negative prompts,
   cùng candidates/K/seeds cho CLIP/SigLIP2; đánh giá retrieval precision/coverage/duplicates và downstream evidence.
2. Text+vision cùng model/revision/preprocessing/space. SigLIP2 768D không dùng với CLIP artifact 512D.
   Producer/FieldKey/model profile/spec/hash mới; prepare vision lại, cache theo hash/version, giữ phiên cũ replay được.
3. Calibrate gate/scoring theo model, không copy threshold CLIP hoặc sigmoid/logit scale từ SigLIP2 sang cosine ngầm.
4. Vision prepare GPU có thể phù hợp hơn CPU; text-only warm/cache prompt trên VPS, đo RAM khi cùng YOLO/PII/decode.
   Không tự launch Kaggle, quantize hay giảm frame/candidate để ghi uplift.
5. Nếu chất lượng tăng đủ đáng chi phí, cập nhật contract/model profile/catalog/binding/DTO và migration compatibility trước code pilot;
   nếu không, giữ CLIP text-only đã tối ưu. PII/model quality và three-model concurrency vẫn là gate riêng.
