# RAV-26 — Video Frame Curation cho ADAS & Robotics

> ⚠️ **MVP là bản 1 người dùng, chạy local.** Trước khi deploy/host cho nhiều người (kể cả Live URL Demo Day), đọc [docs/design/LOCAL_SINGLE_USER_LIMITS.md](../../design/LOCAL_SINGLE_USER_LIMITS.md).

> Video dashcam/robot có rất nhiều frame gần giống nhau → kỹ sư đặt ngân sách K frame, hệ thống đề xuất từng batch frame ít trùng lặp,
> kỹ sư keep/drop, batch sau tự tránh những gì đã giữ → đủ K thì xuất tập frame để gán nhãn.
> Người dùng: Data/ML Engineer làm perception ADAS/Robotics.

▶️ **Muốn chạy ngay:** xem [Chạy thử (local)](#chạy-thử-local).

📖 **Mới vào dự án? Đọc [docs/PRODUCT_STORY.md](../../PRODUCT_STORY.md) trước** — câu chuyện sản phẩm ngắn (~5 phút), mỗi đoạn trỏ tới tài liệu chi tiết.

## Chủ đề

Đề bài gốc: **RAV-26 — Data Curation Copilot for ADAS & Robotics**. Chọn sample có giá trị dưới ngân sách gán nhãn hữu hạn,
có giải thích và người duyệt ([đề bài gốc](docs/research-pack/01_origin/01_original_rav26_problem.md)).

Nhóm thu hẹp cho MVP ([cách hiểu đã chốt](docs/research-pack/02_problem_understanding/01_problem_reframing_confirmed.md)):

> Trong một lượng lớn frame từ video, chọn một tập nhỏ hơn, ít trùng lặp hơn, nhưng vẫn giữ thông tin hữu ích cần quan sát tiếp.

- Tách **data curation** (MVP) khỏi **active learning / retrain** (phase sau).
- Đo bằng **Near-Duplicate Rate@K** so với Random và Uniform Temporal, cùng K. Không claim tăng mAP khi chưa retrain.
- Tự làm giao diện thay vì build trên FiftyOne/LightlyStudio: hai tool khó tiếp cận với việc "chọn frame từ video rồi duyệt",
  thuật toán chọn của chúng không vượt trội selector tự viết, và giao diện chọn frame của Lightly không có (xác minh trong source)
  → build trên đó có thể tốn công hơn tự build ([thử nghiệm 27/09](../research/05_hands_on_audit/06_findings_2026-09-27.md)).

## Bản local hiện tại

1. Nạp video (MVP thử trên **1 video** dashcam; sau mở rộng sang tập con BDD100K) → tách frame, giữ `video_id` + timestamp.
2. Tạo phiên với mục tiêu duyệt và **ngân sách K** (tổng số frame muốn chọn); mỗi vòng đề xuất một batch frame ít trùng nhau,
   khác tập đã keep; mỗi frame kèm điểm từng tiêu chí. Lặp tới khi đủ K.
3. Kỹ sư xem frame kèm vài giây ngữ cảnh, **keep/drop**; có thể đổi cách đề xuất giữa các vòng.
4. Trực quan hoá: bản đồ embedding, timeline điểm theo thời gian, lưới frame, cặp frame trùng.
5. Báo cáo ND-Rate@K và keep rate theo từng cách đề xuất; xuất frame đã keep (CSV + ảnh) sang CVAT/Label Studio.

Local đã có dataset/version tham chiếu, snapshot, CLIP query explicit và recipe model-guided; upload video chỉ lưu, preparation là thao tác riêng. Chất lượng ngữ nghĩa còn giới hạn theo [đánh giá V2](docs/evaluation/V2_CRITERIA.md); không claim downstream uplift.
Chi tiết phạm vi và checklist: [PLAN.md](../../../PLAN.md). Deployment nhiều user đang ở [docs để duyệt](../../backlog/DEPLOYMENT_EXECUTION_PLAN.md); tài liệu cũ/template nằm trong [archive](../README.md).

## Hướng mở rộng

Kiến trúc chia thành các **extension point thay được bằng config** (nguồn video, cách tách unit, feature, objective,
combiner, selector, feedback, metric, exporter, storage...). Component không gọi nhau trực tiếp mà trao đổi qua `Context`
bằng tên field, nên thêm hướng mới = thêm một file, không sửa pipeline ([contract](../../design/CONTRACTS.md)).

Các hướng đã chừa chỗ:

- **Sequence thay vì frame:** đề xuất và chấm cả đoạn clip/sự kiện (unit dạng span).
- **Tìm lỗi mô hình:** so detector mục tiêu (YOLO11s/m) với tham chiếu offline (YOLO11x), đề xuất frame hai model bất đồng
  ([khảo sát](docs/research-pack/03_research_backing/03_perception_reference_models.md)).
- **Phản hồi phong phú hơn:** "tìm giống/khác frame này", tag lý do.
- **Tự động hoá quyết định:** vai trò chọn recipe (Router) và keep/drop (Judge) có input/output tường minh và mọi quyết định được ghi log,
  nên sau này chỉ cần cắm một judge (rule, học từ log, LLM/VLM) để đánh giá recipe không cần người; human review vẫn là luồng chính.
- **Agent chatbot:** ra lệnh thao tác giao diện và báo cáo chỉ số, qua cùng command layer với UI.

## Chạy thử (local)

Cần: **Python 3.11+**, **Node.js 20.19+ hoặc 22.12+** (Vite 8), Git. Không cần GPU, không cần cài ffmpeg (PyAV kèm sẵn).
Mọi lệnh chạy ở **thư mục gốc repo**, trừ khi ghi khác.

### 1. Cài một lần

```bash
python -m venv .venv
source .venv/bin/activate              # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cd src/web && npm install && cd ../..
cp .env.example .env                   # Windows: copy .env.example .env
```

Hook ghi log AI (bắt buộc, dùng cho chấm điểm) — chạy một lần sau khi clone:

```bash
bash scripts/setup_hooks.sh                                      # Linux / macOS / Git Bash
powershell -ExecutionPolicy Bypass -File scripts\setup_hooks.ps1 # Windows PowerShell
```

Codex dùng `.codex/hooks.json` để ghi log vào `.ai-log/session.jsonl` (mở `/hooks` trong Codex CLI để tin cậy hook của repo trên từng máy);
`pre-push` chỉ gửi log đang chờ.

### 2. Chạy app — mở 2 terminal

Windows, để cache model/gói và file tạm nằm trên ổ chứa repo, chạy trong **mỗi terminal** trước Python/npm/test:

```powershell
. .\scripts\use_project_storage.ps1
```

Script chỉ cấu hình tiến trình terminal và các tiến trình con; không đổi TEMP toàn Windows hoặc pagefile.
Đặt checkout trên ổ D để dữ liệu/cache/temp của các lệnh đó nằm trên D. Test dùng
`python -m pytest tests -q --basetemp=output/pytest-tmp`.

### Deployment đang chạy và bàn giao

URL public: **[c4-app-026 · T026](https://c4-app-026.onrender.com/app)**. FE + API v2 chạy chung
một Docker Web Service trên Render, image private từ Docker Hub; API riêng và FE cũ đã suspended.
Landing ở `/`, đăng nhập ở `/login`, ứng dụng ở `/app`. AI/video và tác vụ nặng dành cho worker Contabo,
worker đã deploy trên Contabo; hosting Live chưa đồng nghĩa pilot nghiệp vụ đã hoàn tất.

- [Hướng dẫn build, cấu hình Supabase/S3, deploy image gộp và rollback](../../setup/COMBINED_WEB_RELEASE.md).
- [Hiện trạng bàn giao: image/deploy ID, monitoring/quota, cập nhật và phần chưa làm](../../setup/DEPLOYMENT_STATUS.md).
- [Runbook database, migration và runtime pilot](../../setup/PILOT_RUNTIME_RUNBOOK.md).
- [Bàn giao development tối ưu frame/worker và thứ tự release](docs/backlog/FRAME_MATERIALIZATION_PLAN.md#7-bàn-giao-development-và-release-sau-hostname-118).

Luồng cập nhật: kiểm tra code → commit → build/push tag image mới → chọn image trên Render → deploy →
kiểm health/readiness và giao diện. Push Git chưa tự đổi image đang chạy; credential chỉ nhập ở host,
không commit vào repo hoặc image.

Hệ thống hỗ trợ 2 chế độ chạy:

#### 🔹 Chế độ A: Chạy Pilot Deploy v2 (Multi-user, Supabase Auth & PostgreSQL)

Chế độ deploy pilot có xác thực phân quyền tài khoản, hạn mức Quota CPU/GPU 4h UTC, bảo vệ dữ liệu RLS và API v2:

1. **Khởi tạo database (chỉ chạy 1 lần khi thiết lập mới):**
   ```bash
   python scripts/pilot_migrate.py --database-url "<SUPABASE_DATABASE_URL>"
   ```
2. **Khởi chạy hệ thống (2 terminal):**
   ```bash
   # Terminal 1 — Backend Pilot v2 (cổng 8000)
   python -m uvicorn src.rav.api.pilot:app --env-file .env --port 8000

   # Terminal 2 — Giao diện web (Vite, cổng 3000; tự proxy /api sang cổng 8000)
   cd src/web
   npm run dev
   ```
3. **Đăng nhập:** Mở **http://127.0.0.1:3000**, đăng nhập bằng tài khoản email/mật khẩu đã tạo trên Supabase Auth (mục *Authentication ➔ Users* trên Supabase Dashboard).

#### 🔹 Chế độ B: Chạy Local MVP v1 (Offline, SQLite)

Chế độ nghiên cứu 1 người dùng local, không yêu cầu Supabase hay PostgreSQL (sử dụng database SQLite `data/rav.sqlite` có sẵn):

```bash
# Terminal 1 — backend Local MVP (FastAPI, cổng 8000)
python -m uvicorn src.rav.api:app --port 8000
# Terminal 2 — giao diện web (Vite, cổng 3000; tự proxy /api và /media sang cổng 8000)
cd src/web
npm run dev
```

Mở **http://127.0.0.1:3000**. Để backend chạy trong terminal riêng suốt lúc dùng — tắt nó thì web báo lỗi kết nối.

### 3. Dùng lần đầu

1. **Kho video → Tải video lên** (mp4, hoặc kéo thả). Cách khác: chép file vào `data/videos/`, tải lại trang, chọn video → bấm **Chuẩn bị**.
2. Upload chỉ lưu video. Bấm **Chuẩn bị DINO** hoặc **Chuẩn bị CLIP** riêng trên thẻ; mỗi feature có trạng thái/tiến độ riêng. Theo cấu hình cloud C1/C2, Kaggle tính DINO+pHash hoặc CLIP rồi backend kiểm và lưu cache; không tự chạy AI lúc upload.
3. Chọn video đã xử lý xong → **Phiên mới**: nhập mục tiêu và ngân sách K → vào Studio.
4. Studio: **Enter** để lấy đợt đề xuất, **K** giữ / **D** bỏ / **U** huỷ quyết định, **Space** xem chi tiết + clip, **?** xem mọi phím tắt.
   Quyết định tự lưu. Đủ K → tab **Xuất** (CSV + ảnh, tải zip ở màn "Dữ liệu đã xuất").

Tìm bằng CLIP: chọn **Tìm theo câu (CLIP)** trong Phiên mới, điền câu ngắn tiếng Anh (mỗi dòng một câu),
bấm **Chuẩn bị CLIP** cho từng video và chờ “CLIP sẵn sàng”. Image embedding chạy nền trên Kaggle bằng
`KAGGLE_API_TOKEN` đọc từ `.env` repo; không cần điền notebook, app tự tạo notebook private riêng và lưu slug trong
`output/clip-worker.json`. `RAV_KAGGLE_KERNEL` chỉ là override tuỳ chọn, phải thuộc tài khoản của token.
Transport local cần giữ backend chạy tới khi job xong. Với **S3 + Supabase**, xem
[cấu hình backend cloud](docs/setup/CLOUD_STORAGE.md): upload lưu raw lâu dài trên S3, Kaggle tải raw/tính CLIP rồi dọn dữ liệu tải về,
đẩy embedding/progress lên S3; backend kiểm và lưu cache, Supabase giữ job/lease để phục hồi sau restart.
Credential chỉ nằm ở backend; frontend dùng API hiện có. Text encode chạy local cùng checkpoint đã pin; không tự dịch/suy câu từ mục tiêu.
Trong Studio, mở **Nâng cao → recipe** để sửa câu tìm/loại trừ/ngưỡng. CLIP có thể trả ít hoặc không có frame,
không tự nới bộ lọc để lấp đợt; điểm tương đồng cần người duyệt kiểm tra. `kaggle_extend_api` chỉ tham khảo.

YOLO tham chiếu: **Kho dữ liệu → Mô hình tham chiếu → Upload mô hình** để lưu checkpoint `.pt` dạng PyTorch ZIP.
Sau đó trong **Phiên mới**, chọn model cùng phiên bản dataset đã dùng train/finetune model; liên kết được cố định cho phiên/snapshot.
Upload kiểm cấu trúc file và SHA-256. Có thể bấm **Kiểm tra checksum** để kiểm toàn bộ file.
Trong **Studio**, chọn model rồi bấm **Chạy YOLO · N ảnh**: chỉ infer các ảnh của đợt đang xem trên CPU, kết quả được cache theo model/đợt. Bấm **M** bật/tắt box; **Space → Ảnh gốc** xem nhãn/confidence. Có thể thử model trên phiên cũ mà không thay khai báo dataset train. V2 có recipe model_guided/text_model_guided theo CONTRACTS §18; confidence không phải accuracy trên dữ liệu chưa gán nhãn và relation train của model phải có căn cứ.

### Dữ liệu nằm ở đâu

| Thư mục | Nội dung | Đổi bằng biến môi trường (`.env`) |
|---|---|---|
| `data/videos/` | Video nguồn (upload cũng lưu vào đây) | `RAV_VIDEOS_DIR` |
| `data/models/` | Checkpoint tham chiếu upload, tên theo SHA-256 | `RAV_MODELS_DIR` |
| `output/` | SQLite (phiên, quyết định), cache feature `.npy`, ảnh thumbnail, bản xuất `output/exports/` | `RAV_OUTPUT_DIR` |

Các thư mục trên đều bị gitignore. Biến khác: `RAV_MAX_UPLOAD_MB` và `RAV_MAX_MODEL_MB` (mặc định 500); preparation explicit theo C2,
`RAV_API` (cho web, nếu backend không ở cổng 8000).

### Dòng lệnh (không cần web)

```bash
python -m src.rav.cli videos                                   # liệt kê video + id + trạng thái
python -m src.rav.cli prepare <video_id>                       # tiền xử lý một video
python -m src.rav.cli benchmark --videos <video_id> --k 20 50  # so recipe với Random / Uniform bằng ND-Rate@K
python -m src.rav.cli spotcheck <video_id>                     # lưới cặp frame theo khoảng cách pHash
```

`python -m src.rav.cli <lệnh> --help` để xem đủ tham số.

### Kiểm tra trước khi commit

```bash
python -m pytest tests -q
python -m ruff check src tests
cd src/web && npx tsc --noEmit         # nếu sửa frontend
```

Trên Windows, `tests/test_codex_hook.py` fail sẵn do đường dẫn có dấu — bỏ qua bằng `--deselect tests/test_codex_hook.py`.

### Lỗi hay gặp

| Triệu chứng | Cách xử lý |
|---|---|
| Web hiện lỗi kết nối / HTTP 502 | Backend chưa chạy hoặc đã tắt → chạy lại Terminal 1 |
| Video đứng ở "đang xử lý" lâu | Bình thường với video dài trên CPU; xem log ở terminal backend. Máy ít RAM: đóng bớt app |
| `npm run dev` báo lỗi phiên bản Node | Cập nhật Node lên 20.19+ / 22.12+ |
| Đọc / ghi ảnh lỗi khi repo nằm ở đường dẫn có dấu | Code đã dùng PIL + `imencode`; không dùng `cv2.imread` / `cv2.imwrite` khi viết thêm |

### Thử recipe bổ sung dữ liệu V2

Trong Kho dữ liệu, import và chuẩn bị dataset **đã dùng train/finetune** checkpoint của bạn, rồi upload model `.pt` ở tab Mô hình. Tạo phiên mới với video mới chưa nhãn, chọn đúng dataset train và model tham chiếu.

- **Bổ sung so dataset train**: DINO so với dataset train + ảnh đã giữ; không chạy YOLO.
- **Bổ sung + YOLO consistency**: chọn tối đa 128 ứng viên mặc định, chạy YOLO ảnh gốc/lật chỉ trên tập đó rồi chọn đợt để duyệt.
- **CLIP + dataset train + YOLO**: chuẩn bị CLIP cho video trước, nhập câu tìm/loại trừ; lọc CLIP trước bước YOLO.

Studio hiện tiến độ job. Trong Nâng cao có giới hạn ứng viên (1–256), trọng số và ngưỡng chống trùng. Mở chi tiết ảnh để xem mẫu train gần nhất, điểm consistency và prediction ảnh gốc. Ngưỡng không tự nới nên đợt có thể ít hơn số yêu cầu. Đổi quyết định/pool khi job đang chạy sẽ cần đề xuất lại; restart API đánh dấu job dở là lỗi để thử lại, cache hoàn chỉnh vẫn được dùng.

Phiên cũ giữ nguyên tham chiếu; muốn thêm/đổi model hoặc dataset train thì tạo phiên mới. YOLO V2 chạy CPU local, CLIP ảnh có prepare Kaggle theo cấu hình hiện có. Consistency/novelty là tín hiệu để duyệt, chưa chứng minh model sai hay cải thiện mAP.

Checkpoint CLIP local mặc định nằm trong `data/model-cache/huggingface/hub` của repo để tránh đầy ổ hệ thống; có thể đổi `RAV_CLIP_CACHE_DIR` trong `.env`. Cache kết quả vẫn ở `output/fields`. Chạy lệnh từ repo root.

## Tech stack

| Layer | Công nghệ |
|---|---|
| Vision | PyTorch, DINOv2-S (embedding), pHash (đánh giá độc lập), PyAV (giải mã video) |
| Backend | Python 3.11+, FastAPI, Pydantic |
| Frontend | React 19 + TypeScript, Vite, Tailwind |
| Lưu trữ | SQLite + file `.npy` |
| DevOps | GitHub Actions (ruff + pytest) |

## Cấu trúc thư mục (dev)

Backend `src/rav/` (mỗi tầng kiến trúc một thư mục), frontend `src/web/` ([README riêng](../../../src/web/README.md)).
Cây đầy đủ và chiều gọi: [architecture §9](../../architecture_diagram.md).

## Tài liệu

| | |
|---|---|
| [docs/PRODUCT_STORY.md](../../PRODUCT_STORY.md) | Câu chuyện sản phẩm (đọc đầu tiên) |
| [docs/README.md](../../README.md) | Mục lục tài liệu |
| [docs/architecture_diagram.md](../../architecture_diagram.md) | Kiến trúc hệ thống + trực quan hoá |
| [PLAN.md](../../../PLAN.md) | Scope MVP và việc cần làm |
| [docs/design/CONTRACTS.md](../../design/CONTRACTS.md) | Contract giữa các component, vai trò Router/Proposer/Judge |
| [docs/design/RAV26_ALIGNMENT.md](../../design/RAV26_ALIGNMENT.md) | Checklist alignment với đề bài gốc |
| [docs/research-pack/](docs/research-pack/00_README.md) | Đề bài, research, đối thủ, audit, quyết định |
| [eval/results/report.md](../../../eval/results/report.md) | Kết quả đánh giá |
| [AGENTS.md](../../../AGENTS.md) | Luật làm việc cho người và AI agent |

## Team P-026

| Thành viên | Vai trò | Mã học viên |
|---|---|---|
| Lê Anh Duy | – | – |
| Nguyễn Hồ Nam | – | – |
| Phạm Gia Khánh | – | – |
| Vũ Đức Thiện | – | – |

## License

MIT
