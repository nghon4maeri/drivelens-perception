# Báo Cáo Audit Dự Án RAV-26: Phân Tích Test Thừa và Docs Thừa

> **Ngày thực hiện audit:** 2026-10-14  
> **Phạm vi:** Toàn bộ test suite (`tests/`, `src/web/src/`) và tài liệu (`docs/`, `PLAN.md`, `AGENTS.md`)  
> **Nguyên tắc:** Đánh giá tĩnh và động toàn diện; **tuyệt đối không sửa code hay bất kỳ file hiện hữu nào** trong dự án.

---

## 1. Tổng Quan Kết Quả Audit

### 1.1. Hiện trạng Test Suite
- **Tổng số file test:** 64 file (gồm 62 test module Python trong `tests/`, 1 `conftest.py`, 1 `__init__.py`) + 4 file test frontend trong `src/web/src/`.
- **Tổng số test cases Python:** 578 test cases.
  - **Passed:** 327 test cases (chạy trong ~40s).
  - **Skipped:** 250 test cases (chiếm tới **43.2%** tổng số test; chủ yếu do yêu cầu PostgreSQL thật `RAV_PILOT_TEST_DATABASE_URL`, môi trường Linux, hoặc cờ đo tải opt-in).
  - **Failed:** 1 test case (`tests/test_codex_hook.py` do lỗi encoding đường dẫn tiếng Việt trên PowerShell Windows).
- **Frontend test (Vitest):** 4 file, 34 test cases, 100% Passed. Typecheck `tsc --noEmit` và `ruff check` sạch 100%.

### 1.2. Hiện trạng Documentation
- **Tài liệu Active:** 41 file (nằm tại `docs/`, `docs/design/`, `docs/backlog/`, `docs/setup/`, `docs/research/`, `docs/research-pack/`).
- **Tài liệu Archived:** 72 file (nằm tại `docs/archived/`).
- **Vấn đề cốt lõi:**
  1. Có **49 file ngoại lai / rác boilerplate** (`docs/archived/templates/technical-book/` và các template Markdown của khóa học khác như LangGraph, BMAD, RAG...) hoàn toàn không liên quan đến đề tài RAV-26.
  2. Có **12 file QA log** riêng lẻ nằm rải rác trong `docs/setup/` được tạo qua từng lát cắt (#126 – #133), chứa dữ liệu checkpoint cũ đã bị supersede bởi kiến trúc Interactive-CPU (#133).
  3. Tồn tại script SQL cũ (`docs/setup/SUPABASE_EMBEDDING_JOBS.sql`) xung đột với hệ thống migration chính thức (`001`–`014`).

---

## 2. Chi Tiết Audit: Các Test Thừa, Trùng Lặp & Lạc Hậu

Dưới đây là phân loại chi tiết các test thừa hoặc cần tái cấu trúc:

```
tests/ (62 files)
├── [NHÓM 1: 10 files] Test lát cắt MVP Local v1 (P1-P8) phân mảnh, trùng lặp luồng v1
├── [NHÓM 2:  2 files] Test kiến trúc cũ đã bị supersede (Worker 2-process, C1 Supabase Queue)
├── [NHÓM 3:  3 files] Benchmark / Capacity opt-in đặt nhầm chỗ trong regression test suite
├── [NHÓM 4:  1 file ] Test môi trường Windows fail vĩnh viễn (Codex hook)
├── [NHÓM 5:  3 files] Trùng lặp chồng chéo trong bộ test Pilot Frames (F1-F4)
└── [HỢP LỆ : 43 files] Test Core, Component, Security/RLS, Pilot API v2, Dispatcher, v.v.
```

### Nhóm 1: Test Lát Cắt MVP Local v1 Phân Mảnh và Trùng Lặp (10 files)
Các file test có tiền tố `test_p*.py` được tạo trong các lát cắt đầu tiên (L1–L8). Chúng kiểm thử API v1 (`/api/v1`) và cơ chế chạy local đơn người dùng qua SQLite / command runner.
1. **`tests/test_p5a.py` (5 tests) & `tests/test_p5b.py` (4 tests) so với `tests/test_p6.py` (4 tests):**
   - *Hiện trạng:* `test_p5a.py` test command `create_session`, `propose`, `update_state`; `test_p5b.py` test command export CSV/ZIP. Trong khi đó, `test_p6.py` (`test_review_flow_over_http`) bọc lại chính xác toàn bộ luồng đó qua FastAPI `/api/v1` và dùng chung fixture `app`.
   - *Đánh giá thừa:* Logic kiểm thử bị lặp lại 2 lần (ở tầng Service/Command và tầng HTTP Router v1).
2. **`tests/test_p8e.py` (3 tests), `tests/test_p8h.py` (3 tests), `tests/test_p8i.py` (1 test):**
   - *Hiện trạng:* Các file này rất nhỏ, kiểm thử những phần vá tính năng của local MVP:
     - `test_p8i.py`: Chỉ có đúng 1 test function `test_explain_proposal`, kiểm tra giải thích đề xuất trên SQLite cục bộ.
     - `test_p8e.py`: Test queue tiến độ P1 local và thêm video vào session local.
     - `test_p8h.py`: Test các chỉ số UI phụ (`review_time`, `min_dist`, `evidence`).
   - *Đánh giá thừa:* Quá phân mảnh thành các file vi mô (1-3 test) chỉ phục vụ nghiệm thu lát cắt cũ trong lịch sử; không phải cấu trúc test suite theo module chuẩn.
3. **`tests/test_snapshot.py` (4 tests) so với `tests/test_export_snapshot.py` (7 tests):**
   - *Hiện trạng:* Cả hai file đều thiết lập session, freeze snapshot và kiểm tra tính bất biến (immutability) trên SQLite.
   - *Đánh giá thừa:* Trùng lặp fixture và các assertion kiểm tra snapshot hash / context hash.
4. **`tests/test_db_migration.py` (5 tests):**
   - *Hiện trạng:* Kiểm tra nâng cấp SQLite schema từ v1 lên v4 và trigger snapshot immutability.
   - *Đánh giá thừa:* Theo `LOCAL_SINGLE_USER_LIMITS.md`, SQLite chỉ là lớp local fallback cũ; toàn bộ hệ thống pilot đang vận hành trên PostgreSQL qua 14 migration SQL độc lập. Bộ test SQLite migration này không còn phục vụ mục tiêu kiến trúc production.

---

### Nhóm 2: Test Kiến Trúc Cũ Đã Bị Thay Thế Hoàn Toàn (Superseded) (2 files)
1. **`tests/test_pilot_worker_spawn.py` (4 tests):**
   - *Lý do thừa:* Được viết cho DEP7-Frames F3 khi worker chỉ có 2 process con (`CPU` và `MEDIA`). Tại lát #132 (N1-Claim-Lanes), hệ thống đã chuyển sang mô hình 4 lanes độc lập (`TEXT`, `YOLO`, `BULK`, `MEDIA`).
   - Hiện tại:
     - 2 test SIGTERM bị skip trên Windows (`sys.platform != "linux"`).
     - 2 test supervisor spawn (`test_worker_spawn_smoke`, `test_worker_spawn_survives_cpu_crash`) kiểm tra mô hình 2 process đã lạc hậu, đã được thay thế toàn diện bởi `tests/test_worker_lanes_spawn.py` (kiểm tra 4 process con và 3 slot đồng thời).
2. **`tests/test_cloud_embedding.py` (23 tests, 546 dòng code):**
   - *Lý do thừa:* Viết cho lát C1 cũ, kiểm thử hàng đợi Supabase PostgREST trên bảng `rav_embedding_jobs` (`s3_clip_worker.py`, `SupabaseJobs`).
   - Hệ thống hiện tại đã chuyển hoàn toàn sang PostgreSQL pilot (`rav_pilot_job`, `rav_pilot_job_attempt`, transactional leases qua advisory locks và RPC). Cơ chế C1 cũ đã ngừng sử dụng trên hạ tầng pilot.

---

### Nhóm 3: Test Benchmark / Đo Tải Đặt Nhầm Vào Regression Suite (3 files)
1. **`tests/test_pilot_publish_latency.py` (1 test - parameterized):**
   - *Lý do thừa trong pytest:* Test này có `pytestmark = pytest.mark.skipif(not TARGET, reason="opt-in controlled latency benchmark, not normal regression")`. Mục đích là chèn độ trễ nhân tạo (RTT 0/100/260ms) và ghi log ra file JSONL phục vụ vẽ biểu đồ. Đây là benchmark script, không phải test kiểm tra tính đúng đắn.
2. **`tests/test_worker_lanes_capacity.py` (1 test):**
   - *Lý do thừa trong pytest:* `pytestmark = pytest.mark.skipif(os.environ.get("RAV_RUN_LANE_CAPACITY") != "1" or not YOLO.exists(), ...)`. Test yêu cầu tải model YOLO thật trên máy VPS Contabo, luôn bị skip trên môi trường CI / local.
3. **`tests/test_interactive_capacity.py` (1 test):**
   - *Lý do thừa trong pytest:* `pytestmark = pytest.mark.skipif(os.environ.get("RAV_RUN_INTERACTIVE_CAPACITY") != "1" or not YOLO.exists(), ...)`. Tương tự, đây là bài đo tải VPS.

---

### Nhóm 4: Test Lỗi Vĩnh Viễn Do Môi Trường (1 file)
1. **`tests/test_codex_hook.py` (1 test):**
   - *Hiện trạng:* Thực hiện lệnh PowerShell gọi subprocess vào đường dẫn `D:\Coding\Vin AI Thực Chiến\final\P-026`. Do thư mục chứa ký tự Unicode có dấu, lệnh `Set-Location` của PowerShell bị lỗi `ItemNotFoundException`.
   - AGENTS.md §3 đã ghi chú: *"`tests/test_codex_hook.py` fail sẵn vì PowerShell `Set-Location` với đường dẫn có dấu — không phải lỗi mới."*
   - Test này luôn gây ra `1 failed` trong mỗi lần chạy `pytest tests`, làm giảm độ tin cậy của tín hiệu CI.

---

### Nhóm 5: Trùng Lặp Fixture và Phạm Vi Trong Pilot Frames (3 files)
- `tests/test_pilot_frames.py` (11 tests), `tests/test_pilot_frames_prepare.py` (14 tests), `tests/test_pilot_frames_e2e.py` (1 test, 311 dòng).
- *Vấn đề:* Cả 3 file đều chứa fixture lặp lại (`pg`, `s3`, `moto`, `seed_frames`, upload video giả). Logic kiểm tra tách frame, lưu thumbnail và dọn cache giữa `test_pilot_frames.py` và `test_pilot_frames_prepare.py` có độ trùng lặp cao (overlap ~40%).

---

## 3. Chi Tiết Audit: Các Docs Thừa, Lạc Hậu & Rác Boilerplate

```
docs/
├── [NHÓM 1: 49 files] docs/archived/templates/technical-book/ (RÁC HOÀN TOÀN NGOẠI LAI)
├── [NHÓM 2: 12 files] docs/setup/*_QA.md (Log QA theo lát cắt cũ, checkpoint đã supersede)
├── [NHÓM 3:  2 files] docs/backlog/PROPOSAL_LATENCY_PLAN.md & docs/components/README.md
└── [NHÓM 4:  1 file ] docs/setup/SUPABASE_EMBEDDING_JOBS.sql (Xung đột với migrations 001-014)
```

### Nhóm 1: Tài Liệu Rác Ngoại Lai / Boilerplate Khóa Học Khác (49 files)
Nằm trong thư mục `docs/archived/templates/`:
- **`docs/archived/templates/technical-book/` (45 files):**
  - Chứa toàn bộ nội dung giáo trình `chapter-01.md` đến `chapter-10.md`, `cost-management.md`, `troubleshooting.md`.
  - Các mục con: `langgraph/` (nodes-and-edges, state, tools), `bmad/`, `patterns/rag-pattern.md`, `devops/docker-cicd.md`.
  - Thư mục ảnh `book-media/free-accounts/` chứa 8 ảnh JPG: `cohere.jpg`, `gemini.jpg`, `groq.jpg`, `huggingface.jpg`, `langsmith.jpg`, `mistral.jpg`, `render.jpg`, `vercel.jpg`.
  - **Đánh giá:** Đây là tài liệu mẫu từ template khóa học LLM/Agentic AI, **hoàn toàn 100% không liên quan** đến hệ thống Active Learning / Video Curation RAV-26.
- **`docs/archived/templates/` (4 files Markdown gốc):**
  - `CONTRIBUTING.md`, `JOURNAL.md`, `presentation_README.md`, `README_boilerplate.md`. Chỉ là các file template trống.

---

### Nhóm 2: Các File QA Log Checkpoint Cũ Bị Phân Mảnh (12 files trong `docs/setup/`)
Trong quá trình triển khai từ lát #126 đến #133, mỗi lát cắt tạo ra một file QA riêng. Nhiều file trong số này ghi nhận cấu hình và số liệu của những giải pháp đã bị hủy bỏ hoặc thay thế:
1. `docs/setup/PROPOSE_PUBLISH_RPC_QA.md` (#131)
2. `docs/setup/PROPOSAL_LOCAL_QA.md` (#129)
3. `docs/setup/N1_RELEASE_QA.md` (#130)
4. `docs/setup/CLAIM_LANES_QA.md` (#132)
5. `docs/setup/BASIC_FLOW_STABILITY_QA.md` (#126)
6. `docs/setup/COMPUTE_RUNTIME_QA.md` (#127)
7. `docs/setup/DOCS_REFRESH_QA.md` (#128)
8. `docs/setup/DASHCAM_LOCAL_QA.md`
9. `docs/setup/FRAMES_CLOUD_QA.md`
10. `docs/setup/LATENCY_QA.md`
11. `docs/setup/UPLOAD_LATENCY_LOGS.md`
12. `docs/setup/INTERACTIVE_CPU_QA.md` (#133 - file QA live duy nhất hiện nay)

*Bằng chứng chính thức từ `docs/setup/DEPLOYMENT_STATUS.md` (dòng 45–46):*
> *"Evidence lịch sử: [#130](N1_RELEASE_QA.md), [#131](PROPOSE_PUBLISH_RPC_QA.md), [#132](CLAIM_LANES_QA.md). Các số đo/source/policy ở các file đó là checkpoint cũ, không mô tả cấu hình live #133."*

Ví dụ điển hình: `N1_RELEASE_QA.md` và `PROPOSE_PUBLISH_RPC_QA.md` đo đạc luồng PROPOSE qua DB queue và claim worker. Nhưng lát #133 (`INTERACTIVE_CPU_QA.md`) đã loại bỏ hoàn toàn việc xếp hàng PROPOSE job, chuyển sang gọi trực tiếp Contabo qua HTTPS và tự checkpoint trên RAM. Các file QA cũ không còn giá trị vận hành trực tiếp.

---

### Nhóm 3: Tài Liệu Kế Hoạch Đã Hoàn Thành / Bị Thay Thế
1. **`docs/backlog/PROPOSAL_LATENCY_PLAN.md`:**
   - Kế hoạch thiết kế N1 (P0–P5) viết trước ngày 2026-10-13.
   - Các giai đoạn P1/P2/P3 đã làm xong ở #129 và #131; P4/P5 đã được giải quyết bằng kiến trúc Interactive-CPU ở #133.
   - File này hiện đã hoàn thành nhiệm vụ lịch sử, giữ trong `docs/backlog/` gây hiểu nhầm rằng đây là việc chưa làm (backlog).
2. **`docs/components/README.md`:**
   - File chỉ dài 7 dòng, nội dung duy nhất là dẫn link sang `docs/archived/2026-10-13/docs/components/README.md`. Là file stub thừa không có nội dung thực chất.

---

### Nhóm 4: Script SQL Cũ Gây Xung Đột Hạ Tầng (1 file)
1. **`docs/setup/SUPABASE_EMBEDDING_JOBS.sql`:**
   - Tạo bảng `public.rav_embedding_jobs` cho cơ chế C1 cũ.
   - Toàn bộ cơ sở dữ liệu Supabase / PostgreSQL hiện tại quản lý qua 14 migration chuẩn tại `src/rav/store/migrations/001_...` đến `014_...`.
   - File này nằm lẻ loi trong `docs/setup/`, gây nguy cơ rất lớn người vận hành (operator) chạy nhầm script cũ vào DB mới.

---

## 4. Ma Trận Đánh Giá & Đề Xuất Xử Lý (Actionable Recommendations)

| Thành phần | Đường dẫn | Loại thừa | Đề xuất giải pháp (khi được duyệt) |
|---|---|---|---|
| **Boilerplate rác** | `docs/archived/templates/technical-book/**` (45 files) | Ngoại lai 100% | Xóa bỏ hoàn toàn (Delete) để nhẹ repo |
| **Boilerplate rác** | `docs/archived/templates/*.md` (4 files) | Khung mẫu trống | Xóa bỏ hoàn toàn |
| **SQL cũ gây nhiễu** | `docs/setup/SUPABASE_EMBEDDING_JOBS.sql` | Lỗi thời / Xung đột | Chuyển vào `docs/archived/2026-10-13/` |
| **QA Checkpoint cũ** | `docs/setup/*_QA.md` (#126 đến #132, 10 files) | Checkpoint lịch sử | Chuyển vào `docs/archived/setup_qa_history/` |
| **Plan đã hoàn thành** | `docs/backlog/PROPOSAL_LATENCY_PLAN.md` | Đã hoàn thành / Thay thế | Đánh dấu completed hoặc chuyển vào archive |
| **Docs stub** | `docs/components/README.md` (7 dòng) | Stub rỗng | Có thể gộp vào `docs/README.md` |
| **Test supervisor cũ** | `tests/test_pilot_worker_spawn.py` | Lạc hậu (mô hình 2 worker cũ) | Xóa bỏ hoặc gộp assertions giá trị vào `test_worker_lanes_spawn.py` |
| **Test C1 cũ** | `tests/test_cloud_embedding.py` | Lạc hậu (Supabase PostgREST queue) | Xóa hoặc archive vào thư mục test lịch sử |
| **Test benchmark** | `tests/test_pilot_publish_latency.py` | Không phải regression test | Chuyển sang `scripts/benchmarks/` |
| **Test capacity** | `tests/test_worker_lanes_capacity.py`, `test_interactive_capacity.py` | Test đo tải VPS opt-in | Chuyển sang `tests/capacity/` hoặc `scripts/` |
| **Test lỗi môi trường** | `tests/test_codex_hook.py` | Fail vĩnh viễn trên Windows | Sửa quote đường dẫn trong command hook hoặc bỏ qua |
| **Test lát cắt v1 cũ** | `tests/test_p8e.py`, `test_p8h.py`, `test_p8i.py` | Phân mảnh siêu nhỏ (1-3 test) | Gộp vào các module test service tương ứng |
| **Test trùng lặp** | `tests/test_p5a.py`, `test_p5b.py` vs `test_p6.py` | Trùng lặp kiểm thử luồng | Giữ `test_p6.py`, tinh gọn bớt các test command thừa |

---

## 5. Kết Luận
- Dự án có nền tảng kiểm thử và tài liệu hóa rất chặt chẽ, tuân thủ kỷ luật cao qua từng lát cắt.
- Tuy nhiên, qua quá trình phát triển nhanh từ MVP Local (L1-L8) -> Cloud Prototype (C1) -> Pilot v2 (DEP0-DEP8) -> N1 Lanes & Interactive CPU (#130-#133), hệ thống đã tích lũy một lượng đáng kể **"nợ kỹ thuật tài liệu và kiểm thử"**:
  - Hơn **40% test case đang bị skip** hoặc kiểm thử những mô hình trung gian đã bị thay thế.
  - Hơn **50 file tài liệu** trong `docs/` là tài liệu template khóa học ngoại lai hoặc log checkpoint cũ.
- Báo cáo này đã định vị chính xác từng file và lý do thừa mà không làm thay đổi bất kỳ file nào trong hệ thống, sẵn sàng làm cơ sở cho đợt dọn dẹp kế tiếp nếu có yêu cầu.
