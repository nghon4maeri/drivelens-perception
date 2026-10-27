# Danh mục Các Thành phần Dư thừa & Kế hoạch Tinh gọn (RAV-26)

> **Mục đích:** Tài liệu này rà soát toàn bộ tài liệu (docs) và mã nguồn (code) nhằm phát hiện các phần thừa, lỗi thời, trùng lặp hoặc chiếm dụng token không cần thiết đối với **Coding Agent** (AI pair programmer).  
> **Nguyên tắc:** Chưa thực hiện bất kỳ thay đổi nào lên code hoặc file hiện có. File này được lập để người dùng xem xét, phê duyệt trước khi quyết định tinh gọn.

---

## 1. Tác động của Dư thừa đối với Coding Agent

Khi coding agent làm việc trong codebase, agent thường dùng các công cụ tìm kiếm ngữ nghĩa, grep, hoặc đọc cây thư mục để nạp ngữ cảnh vào context window:
1. **Ô nhiễm ngữ cảnh (Context Pollution):** Các file PRD cũ, kế hoạch nháp (PL1-PL7), hoặc mẫu template khóa học khiến agent hiểu sai kiến trúc hiện tại, dễ đưa ra giải pháp theo kiến trúc cũ đã bị hủy.
2. **Lãng phí Token & Giảm tốc độ:** `docs/archived/` chứa ~6MB (hơn 1,5 triệu tokens tiềm năng). Nếu agent duyệt đệ quy hoặc grep trúng các file này, chi phí token tăng vọt và câu trả lời dễ bị loãng.
3. **Mâu thuẫn nguồn sự thật:** Khi có 4-5 file spec cùng mô tả một tính năng (dự thảo vs chính thức), agent có thể nhầm lẫn giữa bản nháp và [CONTRACTS.md](file:///D:/Coding/Vin%20AI%20Thực%20Chiến/final/P-026/docs/design/CONTRACTS.md).

---

## 2. Phần Tài liệu (Documentation Redundancy)

### 2.1. Thư mục Rỗng & Vỏ Thư mục Lỗi thời (Ưu tiên Cao - Dọn ngay)
Các thư mục này hoàn toàn không chứa file nào (0 bytes), là tàn dư sau các đợt tái cấu trúc:
- `docs/archive/` (trống hoàn toàn, đã chuyển sang `docs/archived/`).
- `docs/gate-1/` (trống hoàn toàn, tài liệu Gate 1 đã chuyển vào `docs/archived/gate-1/`).
- `docs/guide/` cùng **13 thư mục con trống**:
  - `docs/guide/anti-patterns/`
  - `docs/guide/architecture/`
  - `docs/guide/bmad/`
  - `docs/guide/book-media/` & `docs/guide/book-media/free-accounts/`
  - `docs/guide/code-style/`
  - `docs/guide/deliverables/`
  - `docs/guide/devops/`
  - `docs/guide/langgraph/`
  - `docs/guide/patterns/`
  - `docs/guide/resources/`
  - `docs/guide/setup/`
  - `docs/guide/testing/`
  *(Đây là khung thư mục mẫu từ template ban đầu, không có nội dung).*
- `docs/research-pack/04_market_and_competitors/` (thư mục rỗng).
- `docs/research-pack/05_hands_on_audit/` (thư mục rỗng).

> **Đề xuất tinh gọn:** Xoá các thư mục rỗng này để cây thư mục gọn gàng, tránh làm rối công cụ `find_files` hoặc `ls` của Agent.

---

### 2.2. Kho Lưu trữ Lịch sử Quá lớn trong `docs/archived/` (~6MB, 69 files)
Thư mục này lưu trữ các bản nháp từ những tuần trước và tài liệu mẫu template khóa học:
- **Template sách kỹ thuật không dùng:** `docs/archived/templates/technical-book/` gồm 10 chương (`chapter-01.md` đến `chapter-10.md`) cùng ảnh chụp tài khoản miễn phí (`huggingface.jpg`, `groq.jpg`, `langsmith.jpg`, `vercel.jpg`...). Các file này không liên quan đến sản phẩm RAV-26.
- **Tài liệu Gate 1 cũ:** `docs/archived/gate-1/RAV26_PRD_v2_1_MVP_3_weeks.docx` (file nhị phân Word nặng, agent không đọc được) và `wireframe.png`.
- **Kế hoạch nháp đã thay thế:** `docs/archived/planning/` (`MVP_GAP_MAPPING.md`, `MULTI_USER_RUNTIME_HOSTING_PLAN.md`, `CURATION_PLATFORM_ROADMAP.md`, `BANDWIDTH_AND_QUOTA_PLAN.md`).

> **Đề xuất tinh gọn:**
> - Thêm quy tắc vào `.agentignore` (hoặc cấu hình công cụ tìm kiếm của agent) để bỏ qua hoàn toàn `docs/archived/`.
> - Hoặc nén `docs/archived/` thành một file `.zip` (hoặc chuyển ra khỏi repo) nếu chỉ cần giữ làm kỷ niệm/lịch sử cá nhân.

---

### 2.3. Trùng lặp & Chồng lấn giữa các Bản Đặc tả Deployment (`docs/design/` & `docs/backlog/`)
Hiện tại có tới 5 file tài liệu đặc tả deployment với dung lượng rất lớn (~220 KB, ~55.000 tokens):
1. `docs/design/DEPLOYMENT_PILOT_SPEC.md` (62 KB)
2. `docs/design/DEPLOYMENT_RUNTIME_STORAGE_SPEC.md` (50 KB)
3. `docs/design/DEPLOYMENT_IDENTITY_DATA_SPEC.md` (40 KB)
4. `docs/design/DEPLOYMENT_UI_EXPORT_OPS_SPEC.md` (41 KB)
5. `docs/backlog/DEPLOYMENT_EXECUTION_PLAN.md` (22 KB)

**Vấn đề:**
- Các quyết định kỹ thuật chính thức đã được tổng hợp, đóng băng và khóa vào:
  - `docs/design/CONTRACTS.md §21` (Toàn bộ contract của Pilot v2).
  - `docs/architecture_diagram.md §11` (Kiến trúc phân tầng Pilot v2).
  - Các lát DEP0, DEP1, DEP2F, DEP4, DEP4F đã triển khai theo contract chính thức.
- Việc duy trì 4 file SPEC dự thảo ban đầu (vốn chứa nhiều giả thuyết chưa kiểm chứng hoặc các phương án đã bị bác bỏ) khiến Coding Agent dễ đọc nhầm bản nháp thay vì bản chốt ở `CONTRACTS.md §21`.

> **Đề xuất tinh gọn:**
> - Giữ lại `CONTRACTS.md §21` và `architecture_diagram.md §11` làm nguồn sự thật duy nhất (SoT).
> - Gom gọn hoặc chuyển 4 file spec dự thảo (`DEPLOYMENT_*_SPEC.md`) vào thư mục lưu trữ (`docs/archived/specs/`), chỉ để lại tài liệu hướng dẫn vận hành thực tế là `docs/setup/PILOT_RUNTIME_RUNBOOK.md`.

---

### 2.4. Phân mảnh Thư mục `docs/components/*` (14 file README nhỏ lẻ)
Mỗi thư mục con trong `docs/components/` có một file `README.md` riêng:
- `combiners/README.md`, `decoders/README.md`, `exporters/README.md`, `features/README.md`, `feedback/README.md`, `judges/README.md`, `metrics/README.md`, `objectives/README.md`, `references/README.md`, `routers/README.md`, `selectors/README.md`, `sources/README.md`, `units/README.md`, `views/README.md`.

**Vấn đề:**
- Theo luật số 4 trong [AGENTS.md](file:///D:/Coding/Vin%20AI%20Thực%20Chiến/final/P-026/AGENTS.md): *"`docs/components/*` chỉ trích dẫn CONTRACTS, không định nghĩa lại signature"*.
- Đa phần các file này chỉ có 1-2 đoạn văn trỏ tới `CONTRACTS.md §5`. Khi Agent muốn tìm hiểu các component, nó phải mở 14 file riêng biệt thay vì đọc một bảng tổng hợp.

> **Đề xuất tinh gọn:**
> - Gộp 14 file này vào file duy nhất `docs/archived/2026-10-13/docs/components/README.md` dạng một bảng danh mục component kèm link trỏ thẳng vào các mục tương ứng trong `CONTRACTS.md §5`.

---

### 2.5. Các File Markdown Gốc Dư thừa hoặc Rỗng ở Thư mục Root
- `CLAUDE.md`: Chỉ chứa 11 bytes (`@AGENTS.md`).
- `JOURNAL.md`: Chỉ chứa 6 dòng, thực chất là placeholder trỏ về `PLAN.md` và `01_confirmed_decisions.md`.
- `WORKLOG.md`: Chỉ có 1 bản ghi ngày đầu tiên (2026-09-16) và phần dưới là template rỗng, trong khi tiến độ hàng ngày thực tế đều được ghi cực kỳ chi tiết tại [PLAN.md](file:///D:/Coding/Vin%20AI%20Thực%20Chiến/final/P-026/PLAN.md).
- `CODE_OF_CONDUCT.md` (1.5 KB) & `SECURITY.md` (1.8 KB): Template nguồn mở tiêu chuẩn, không có nội dung đặc thù cho dự án học tập/thi đấu.

> **Đề xuất tinh gọn:**
> - Xóa `CLAUDE.md` (hoặc symlink nếu công cụ yêu cầu).
> - Xóa hoặc gộp `JOURNAL.md` và `WORKLOG.md` vào `docs/archived/` vì [PLAN.md](file:///D:/Coding/Vin%20AI%20Thực%20Chiến/final/P-026/PLAN.md) đã đảm nhiệm xuất sắc vai trò theo dõi tiến độ công việc.

---

## 3. Phần Mã nguồn & Scripts (Code Redundancy)

### 3.1. Component Rỗng / Chưa Triển khai (Pure Stubs)
Theo quyết định #20 trong nhật ký quyết định, hai vai trò này do con người thực hiện trực tiếp trên giao diện UI:
1. **Kind `judge`**:
   - `src/rav/components/judges/base.py` & `src/rav/components/judges/__init__.py`
   - Contract định nghĩa hàm `judge(req, ctx)` nhưng không có logic nghiệp vụ (con người bấm keep/drop).
2. **Kind `router`**:
   - `src/rav/components/routers/base.py` & `src/rav/components/routers/__init__.py`
   - Contract định nghĩa điều phối nhưng không có implementation (con người chọn recipe từ dropdown và bấm đề xuất).
3. **Core Types tương ứng trong `src/rav/core/types.py`**:
   - `JudgeRequest`, `RouterRequest`.

> **Đánh giá:** Hai stub này hiện đang giữ chỗ cho kiến trúc tương lai (Autonomous Agentic Loop). Tuy nhiên với MVP và Pilot hiện tại, chúng là "dead code".  
> **Khuyến nghị:** Nếu muốn giữ contract cho tương lai, cần ghi rõ comment `# stub-only: awaiting autonomous agent phase` để Coding Agent không cố tìm cách import hay viết test cho chúng.

---

### 3.2. Trùng lặp Scripts Điều hành (`scripts/`)
- `scripts/smoke_cloud_embedding.py` (2.3 KB): Là script chạy thử thủ công độc lập, trong khi logic này đã được bao phủ hoàn chỉnh bởi test suite `tests/test_cloud_embedding.py` và `tests/test_clip.py`.
- `scripts/_pyrun.cmd` và `scripts/_pyrun.sh`: Trùng lặp giữa Windows và Linux/Bash.
- `scripts/setup.sh`, `scripts/setup_hooks.sh`, `scripts/setup_hooks.ps1`: 3 script cài hook cho 3 môi trường khác nhau.
- *Lưu ý*: Các script `scripts/log_*.py` và file cấu hình `.codex/hooks.json` là **bắt buộc** theo thể lệ chấm điểm của VinAI, **tuyệt đối không được xóa**.

> **Đề xuất tinh gọn:** Giữ nguyên các hook tính điểm; loại bỏ các script test tạm (như `smoke_cloud_embedding.py`) hoặc chuyển vào thư mục test.

---

### 3.3. Rác Sinh ra từ Kiểm thử (`output/pytest-tmp/`)
- Khi chạy `pytest` với cờ `--basetemp=output/pytest-tmp`, thư mục này tích lũy hàng nghìn thư mục con tạm từ các lượt test cũ.
- Dù đã nằm trong `.gitignore`, việc để thư mục này chứa hàng chục nghìn file tạm sẽ làm chậm các lệnh tìm kiếm (`ripgrep`, `find`, `Get-ChildItem`) của coding agent.

> **Đề xuất tinh gọn:** Thêm lệnh tự dọn `output/pytest-tmp` trước khi chạy test hoặc trong script vệ sinh môi trường định kỳ.

---

### 3.4. Cấu hình AI Phân tán Nhiều Nền tảng
Hiện tại repo có đồng thời 5 thư mục cấu hình AI khác nhau:
- `.agents/` (Dành cho Antigravity / Google Agent)
- `.codex/` (Dành cho Codex hook chấm điểm)
- `.claude/` (Dành cho Claude Code)
- `.cursor/` (Dành cho Cursor IDE)
- `.gemini/` (Dành cho Gemini CLI)

> **Đề xuất tinh gọn:**
> - Giữ lại `.codex/` (phục vụ chấm điểm) và `.agents/` (phục vụ Antigravity và skills hiện hành).
> - Xem xét có cần giữ lại các cài đặt thừa của `.cursor/` và `.claude/` nếu đội ngũ không còn sử dụng các tool này.

---

## 4. Tóm tắt Bảng Kế hoạch Tinh gọn (Đề xuất)

| Hạng mục | Vị trí hiện tại | Tình trạng | Hành động đề xuất | Tiết kiệm / Lợi ích cho Agent |
|---|---|---|---|---|
| **Thư mục rỗng** | `docs/guide/*`, `docs/archive`, `docs/gate-1` | Trống hoàn toàn | Xoá thư mục | Cây thư mục sạch, không gây nhiễu search |
| **Archived templates** | `docs/archived/templates/technical-book/` | Template cũ không liên quan | Nén zip hoặc loại khỏi search | Giảm ~1.000.000 tokens tiềm năng |
| **Draft Specs** | `docs/design/DEPLOYMENT_*_SPEC.md` (4 files) | Trùng lặp với `CONTRACTS §21` | Lưu trữ vào `archived/specs/` | Tránh mâu thuẫn SoT, giảm ~45.000 tokens |
| **Component READMEs** | `docs/components/*/README.md` (14 files) | Chỉ trỏ link sang CONTRACTS | Gộp vào 1 file `docs/archived/2026-10-13/docs/components/README.md` | Giảm 14 lần I/O đọc file rời rạc |
| **Root placehoders** | `WORKLOG.md`, `JOURNAL.md`, `CLAUDE.md` | Trống / lỗi thời | Xóa hoặc archive | Agent tập trung vào `PLAN.md` |
| **Component Stubs** | `src/rav/components/judges/`, `routers/` | Chỉ có abstract base, chưa dùng | Gắn nhãn `# stub-only` rõ ràng | Tránh Agent cố viết code gọi stub |
| **Pytest temp junk** | `output/pytest-tmp/` | Hàng nghìn thư mục rác | Dọn dẹp định kỳ | Tăng tốc độ index và search file |
