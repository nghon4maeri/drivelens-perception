# Compute runtime: CPU lanes và model owners

2026-10-13, CONTRACTS §21.22/#127. **Trạng thái 2026-10-17:** lanes TEXT/YOLO/BULK đã live từ #132 (cap 4 CPU/6 GiB);
TEXT chạy HTTP interactive (#133), YOLO thêm MODEL_VERIFY (#142) và PROPOSE nền (§21.32); EXPORT_BUILD/FINALIZE + PII đã live
(smoke 3 mẫu, [release QA](ADMIN_PII_RELEASE_QA.md)). Phần dưới giữ ghi chép candidate gốc; live xem [status](DEPLOYMENT_STATUS.md).

## Cấu hình và quyền sở hữu

Mỗi CPU lane là chương trình/process spawn sống lâu, nhận một job/lần và giữ model cache trong RAM.
Không dùng FastAPI thread pool để chạy ML; không có public endpoint bypass quota. DB CPU queue vẫn authority.
Ví dụ profile candidate (số khởi đầu để benchmark, không numeric policy production đã duyệt):

```json
[
  {"name":"TEXT","kinds":["PROPOSE"],"slots":["cpu-text"],"threads":1,"rss_bytes":1610612736},
  {"name":"YOLO","kinds":["YOLO_BATCH","MODEL_VERIFY"],"slots":["cpu-yolo"],"threads":1,"rss_bytes":1610612736},
  {"name":"BULK","kinds":["VALIDATE_UPLOAD","METRICS","EXPORT_BUILD","EXPORT_FINALIZE","DELETE_SOURCE","PREPARE_FRAMES"],"slots":["cpu-bulk"],"threads":1,"rss_bytes":1610612736}
]
```

JSON trên là `RAV_PILOT_CPU_LANES`; chỉ partition CPU kinds, mỗi lane một slot không trùng.
`RAV_PILOT_RUNTIME_CPU_THREADS=4`, `RAV_PILOT_RUNTIME_RSS_BYTES=6442450944` (6 GiB),
`RAV_PILOT_RUNTIME_AUX_RSS_BYTES=1610612736` (1,5 GiB). Aux chia supervisor 256 MiB, MEDIA 512 MiB, GPU 768 MiB.
GPU coordinator hydrate/validate cũng dùng CPU; các thread limits không là pin vật lý CPU, tổng container hard cap giữ giới hạn host.
Ba lane một thread + MEDIA một thread không có nghĩa chạy đầy 4 core mọi lúc; throughput và latency phải benchmark cả tải hỗn hợp.

Đặt `RAV_WORKER_CPUS=4`, `RAV_WORKER_MEMORY_LIMIT=6g` trong release profile candidate; compose default CPU=3 giữ tương thích env live cũ.
Budget vượt cgroup-v2 container thì preflight fail. RSS watch gồm model resident; vượt budget dừng hợp tác/thoát theo deadline,
quá cap cả khi idle restart process để nhả cache. Đây là soft RSS guard; Docker/systemd cgroup là hard cap.
PyAV/OMP/MKL/OpenBLAS theo thread budget; Ultralytics pinned select_device còn tự reset threads nên runtime override constant
process-local tương ứng, đã kiểm `torch.get_num_threads()==1` trên inference thật.

Trước bật lane phải provision slots CPU và policy version mới liệt kê chúng. Không tự provision trong app boot.
Claim kiểm kind/slot filter và inventory trong **policy snapshot của job**, bỏ qua job/owner policy không tương thích để không starvation.
Giữ fairness/cursor/CAS/lease/fence/quota. Không có lanes → runtime legacy như trước.
`heavy_active_per_user` được kiểm tại claim, tính cả CPU/GPU HELD/QUARANTINED; MEDIA riêng.
Ba CPU slots không đồng nghĩa một account được chạy ba heavy jobs nếu policy chỉ cho một/hai; đổi numeric policy cần evidence riêng.
Policy/operator steps trên production là phần rollout riêng; không UPDATE config của policy bất biến hoặc sửa snapshot job đang chạy.

Text owner dùng CLIP text+projection cùng revision/tokenizer/float32/L2, còn image encoder GPU/local giữ như cũ.
YOLO owner cache một model theo checkpoint content hash/runtime/path; thay bytes phải load lại, inference local serialize vì predictor mutable.
Model nạp lần đầu khi cần rồi dùng lại, không tải mọi model lúc health/start. PII/export handler còn Hướng tới §21.8;
BULK chừa routing EXPORT_BUILD/FINALIZE nhưng **không có PII fake**, không mở export trước quality gate.

## Kiểm tra và release

Kết quả và giới hạn nghiệm thu: [Compute runtime QA](COMPUTE_RUNTIME_QA.md).

- Baseline local suite checkpoint và final focused tests, Ruff; test lanes partition/budgets/cgroup, policy-slot incompatibility,
  real spawn 3 RUNNING cùng lúc + FINAL/cleanup/slot FREE, actual supervisor split health/restart/shutdown.
- Contabo namespace `/opt/rav26-test/compute-runtime-20261013`, candidate image `rav26-worker:compute-20261013`,
  PostgreSQL 17/network riêng, no production env. Không đổi `/opt/rav26-worker` configs/slots/policy hoặc Render.
- Real CLIP/YOLO tests và encoder benchmark chỉ dùng checkpoint public/ảnh tổng hợp, không lấy raw private hoặc tự chạy GPU.
  [Bằng chứng encoder](../research/ENCODER_RUNTIME_REVIEW.md); logs ignored dưới `output/compute-runtime` và namespace candidate.
- Rollout sau capacity envelope thật: source/image/policy compatibility, drain jobs có policy cũ hoặc kế hoạch xử lý chúng,
  stage CPU slots/policy, config preflight, recreate supervisor, kiểm toàn process healthy rồi functional smoke.
  Không mặc định copy profile ví dụ lên live; cần kiểm RSS model/cache + clip + GPU hydrate khi cùng chạy.
- Rollback bỏ lanes về legacy sau drain/stop verified, giữ ledger/slots/policy snapshots; không xóa slots/receipts đang được job dùng.
  DB publication latency vẫn là lát riêng, lane parallel không xóa fixed overhead 10–15 giây đã đo.

## Docker và native

Docker Engine trên Linux chia sẻ kernel; một container chạy nhiều model-owner processes không tạo VM mỗi model.
Weights/cache trên volume, không bake vào image. Kiểm tài nguyên theo process/cgroup thay vì coi image size là RAM dùng.
[Docker container architecture](https://docs.docker.com/get-started/docker-concepts/the-basics/what-is-a-container).

Native có thể chạy cùng `python -m src.rav.worker --mode pilot` dưới systemd, supervisor vẫn quản lý các chương trình con.
Nếu chọn native: venv dependency pin, Torch CPU wheels, account riêng, env secret file quyền hạn chế,
hard CPU/memory cap systemd slice, persistent data/output, boot identity, SIGTERM/shutdown timeout đủ dài,
health/log rotation/restart và rollback code/venv. Native service setup/capacity chưa test ở lượt này;
preflight cgroup trong container không thay operator kiểm hard limits của systemd scope.
Không mở API inference trên VPS chỉ để giao tiếp model; job DB hiện có đã là giao diện riêng của từng owner process.
