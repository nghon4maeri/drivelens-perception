# Implement guide v2 — làm lại MVP RAV-26 từ mốc docs đã confirm

> **LƯU TRỮ 2026-10-09.** Bản gốc: `docs/IMPLEMENT_GUIDE_V2.md`. Chỉ tra lịch sử/tham khảo; đọc [index hiện hành](../../README.md), không dùng các lệnh/hiện trạng cũ như contract.


> **Đọc trước (cập nhật sau Phase 0+1):** file này là kế hoạch ban đầu, viết trước các quyết định #24–#34.
> Checklist lát cắt hiện hành là [PLAN.md](../../../PLAN.md); chữ ký và công thức theo [CONTRACTS](../../design/CONTRACTS.md).
> Chỗ nào file này khác hai file đó (vd mục 1.3 đã chốt ở #28, `execution` nay nằm trong `core/`, view còn 3) thì theo PLAN/CONTRACTS.

> Viết 2026-09-29 cho **phiên chat mới**. Đọc hết file này trước khi làm gì.
> File này **chưa track** trong git (nằm ở gốc repo) để còn nguyên khi chuyển nhánh. Bước 0.3 sẽ commit nó vào nhánh mới.

## 0. Bối cảnh (đọc để hiểu vì sao làm lại)

- Docs thiết kế đã được người dùng đọc và **confirm** tới commit `0fd6c91` (nhánh `duy-mvp/init`).
- Sau đó code bị lệch contract qua 3 bước:
  1. `1fef62f` (Codex, 27/09): người dùng nói *"implement theo kiến trúc"*, Codex làm theo "luồng demo", tự thu hẹp contract mà không hỏi.
  2. `77acd79` (Claude, 28/09): viết `docs/algorithms/` mô tả đúng code lệch, thêm câu *"khi lệch thì code theo file này"* → hai nguồn sự thật.
  3. `d67546a`…`e1ec2ce` (Claude, 29/09, **chưa push**): refactor dựa trên code lệch, tự chọn "proposer là một hàm" khi người dùng chưa trả lời.
- Quyết định: **làm lại từ `0fd6c91` trên nhánh mới**, code theo docs; nhánh cũ chỉ để tham khảo.

## 1. Luật làm việc (bắt buộc, cho mọi agent)

1. **Nguồn sự thật (SoT):**
   - Cấp 1 — sản phẩm: `docs/PRODUCT_STORY.md`, `docs/design/RAV26_ALIGNMENT.md`, `docs/research-pack/06_project_state/01_confirmed_decisions.md`.
   - Cấp 2 — **thiết kế, code phải theo**: `docs/design/CONTRACTS.md` (interface) + `docs/architecture_diagram.md` (tầng, luồng, API).
   - Cấp 3 — dẫn xuất: `docs/components/*` (hướng dẫn, chỉ trích dẫn CONTRACTS), `PLAN.md` (tiến độ), code.
2. **Docs trước, code sau.** Muốn code khác docs cấp 2 → sửa docs trước (cùng commit), và **hỏi người dùng nếu thay đổi thiết kế**.
3. **Không tự cắt/đổi phạm vi contract.** Muốn làm ít hơn contract (vd chưa làm command layer) → hỏi, rồi ghi quyết định vào log quyết định + đánh dấu "Hướng tới" trong CONTRACTS.
4. **Người dùng hỏi thì trả lời, không tự chọn đáp án rồi làm tiếp.**
5. **Một phiên = một lát cắt** (mục 4). Xong lát: test xanh → commit → cập nhật checklist `PLAN.md` → dừng, báo người dùng.
6. Commit message tiếng Việt không dấu như lịch sử repo, kết thúc bằng dòng `Co-Authored-By` theo hướng dẫn hệ thống. **Không push nếu người dùng chưa bảo.**

## 2. Môi trường (Windows, đã kiểm)

- Repo nằm ở đường dẫn **có dấu** (`Vin AI Thực Chiến`):
  - `cv2.imread` / `cv2.imwrite` **không** đọc/ghi được → đọc ảnh bằng `PIL.Image.open`, ghi bằng `cv2.imencode(...)` + `Path.write_bytes`.
  - `tests/test_codex_hook.py` fail sẵn vì PowerShell `Set-Location` với đường dẫn có dấu — không phải lỗi mới.
- Python có đủ deps: `python` (scoop python312, torch 2.14 CPU). Không có `.venv`.
- DINOv2-S có sẵn trong `torch.hub` cache: `torch.hub.load(<hub_dir>/facebookresearch_dinov2_main, "dinov2_vits14", source="local")`.
- Lệnh kiểm: `python -m pytest tests -q`, `python -m ruff check src tests`, FE: `npx tsc --noEmit` trong thư mục web.
- Dữ liệu: `data/youtube5m/6WTF1JV5knI.mp4` (5 phút, ~1.200 frame @4 fps — dùng cho benchmark), `data/youtube/…` (30 s, mock), `data/videos/commaai-sample.mp4` (21 s, không phải BDD100K). `data/`, `output/` gitignore.
- Máy RAM thấp: đóng bớt app khi tính embedding.

## 3. Phase 0 + 1 — nhánh mới và docs (một phiên, dừng để người dùng review)

### 0.1 Tạo nhánh
```bash
git switch duy-mvp/init            # 4 commit refactor chưa push: để nguyên, KHÔNG push
git switch -c duy-mvp/v2 0fd6c91
```

### 0.2 Cherry-pick hạ tầng hook Codex
```bash
git cherry-pick de3c89f eee4a6a    # sửa hook ghi log Codex trên Windows (+ tests/test_codex_hook.py)
```
`de3c89f` có sửa `README.md` (2 dòng về hook Codex) — giữ.

### 0.3 Commit guide này vào nhánh mới
`git mv`/`git add IMPLEMENT_GUIDE_V2.md` → chuyển thành `docs/IMPLEMENT_GUIDE_V2.md`, commit.

### 0.4 Dọn repo (không đụng hook)
- Xoá `src/config.py, embeddings.py, evaluation.py, ingestion.py, main.py, selectors.py`, `tests/test_selectors.py`.
- Xoá mọi `scripts/*.py` **trừ** `log_antigravity.py, log_hook.py, log_manual.py, submit_log.py` và giữ `setup.sh, setup_hooks.sh, setup_hooks.ps1, _pyrun.sh, _pyrun.cmd`.
  (Script thử Lightly/FiftyOne còn trong nhánh `duy-test/lightly-based` và commit `7aa7619`.)
- `requirements.txt`: bỏ `timm`, `pandas`, `pydantic-settings`. Giữ `python-dotenv` (dùng bởi `submit_log.py`).
- `Makefile`, `Dockerfile`, `scripts/setup.sh`: đổi `src.main:app` → `src.rav.api:app` (bản cũ trỏ tới app không tồn tại).
- Xoá thư mục rỗng trên đĩa nếu có: `src/agents`, `src/api`, `src/models`.

### 1.1 Cập nhật docs theo các quyết định người dùng đã chốt SAU `0fd6c91`
Thêm vào log quyết định (`01_confirmed_decisions.md`), sửa CONTRACTS + architecture + README + PRODUCT_STORY + PLAN cho khớp:

| # | Quyết định (người dùng chốt 2026-09-29) | Sửa ở đâu |
|---|---|---|
| D1 | **`strategy` đổi tên thành `recipe`** ("cách đề xuất" trong văn tiếng Việt). Recipe = **công thức ghép** objective + combiner + selector (đúng CONTRACTS §5–7), khai báo **trong code Python** `src/rav/components/recipes.py`, **không** dùng file JSON ngoài `src/`. API: `POST /sessions/{sid}/proposals {recipe, config?, batch_size, seed?}` (#45), `GET /api/v1/recipes`, metric `keep_rate_by_recipe`, DTO `RecipeDTO`, `RecipeConfig` | Mọi docs cấp 1–3 và log quyết định |
| D2 | **Thứ bậc SoT + quy trình** ở mục 1 của guide này | `docs/README.md` (thay dòng "CONTRACTS là nguồn sự thật cho code"), `AGENTS.md` |
| D3 | **Không tự cắt phạm vi contract; hỏi trước** | `AGENTS.md`, log quyết định |
| D4 | **`web/` nằm trong `src/`**: `src/web/` (frontend React, project Node riêng) cạnh `src/rav/` (backend) | README, architecture §9, CONTRACTS (nếu nhắc đường dẫn) |
| D5 | **Tên thư mục `components/`** cho cả `src/rav/components/` và `docs/components/` (khớp 1–1 với CONTRACTS §5) | architecture §9, docs/README |
| D6 | **Mỗi tầng kiến trúc một thư mục** trong `src/rav/` (cây ở mục 1.2) | README (mục "Cấu trúc thư mục (dev)"), architecture §9 |
| D7 | Thuật toán **không phụ thuộc số video**; phiên dùng `pool`; `session_id` = uid — **đã có** trong docs ở `0fd6c91`, chỉ kiểm lại | – |

### 1.2 Cây thư mục đích (đưa vào README "Cấu trúc thư mục (dev)" và architecture §9)
```
src/
├── rav/                        backend Python (package duy nhất ngoài web)
│   ├── api/                    [entry] HTTP: route gọi command, media, upload
│   ├── cli/                    [entry] prepare · benchmark (python -m src.rav.cli)
│   ├── commands/               command layer (CONTRACTS §11): mỗi hành động 1 hàm, Pydantic in/out; UI + agent dùng chung
│   ├── service/                phiên, review_state (log quyết định), proposal, export — gọi pipeline, lưu qua store
│   ├── pipeline/               P1 prepare (source→decoder→unit builder→features) · P2 propose · P3 evaluate (architecture §4)
│   ├── core/                   types.py (§2) · component.py (§3: Component + Params Pydantic) · registry.py · context.py (§4)
│   ├── components/             mỗi kind ở CONTRACTS §5 một thư mục; MVP mỗi kind 1 implementation
│   │   ├── sources/  decoders/  units/
│   │   ├── features/  execution/
│   │   ├── references/  objectives/  combiners/  selectors/
│   │   ├── feedback/  metrics/  views/  exporters/
│   │   ├── judges/  routers/   chỉ registry + contract, chưa implement (§12)
│   │   └── recipes.py          recipe = công thức ghép (D1)
│   └── store/                  field_store.py (cache field theo FieldKey) · db.py (SQLite)
└── web/                        frontend React + TS: src/api.ts · pages · renderers theo view.type
tests/                          một test cho mỗi ranh giới (xem lát cắt)
docs/components/<kind>/         hướng dẫn viết từng kind, chỉ trích dẫn CONTRACTS
```
Lệnh chạy: `python -m uvicorn src.rav.api:app`, `python -m src.rav.cli ...`, FE: `cd src/web && npm run dev`.

### 1.3 Chốt các chỗ docs còn **mở** (hỏi người dùng, ghi vào CONTRACTS) — ĐÃ CHỐT ở quyết định #28, CONTRACTS §9.5–9.10
Codex đã nêu đúng các chỗ này khi rà kiến trúc; cần quyết trước khi code P2:
1. Vòng đầu chưa có frame keep: objective `novelty` trả gì? (đề xuất: 1.0 cho mọi unit; selector tự lo đa dạng)
2. Selector không đủ `batch_size` vì NMS/ngưỡng: nới bộ lọc mềm và báo `relaxed: true`? (đề xuất: có; không bao giờ nới tính duy nhất của ID)
3. Không cho keep vượt `target_k`: chặn ở service (đề xuất: có).
4. Baseline `random`/`uniform` là **selector** với objective rỗng, không qua bộ lọc diversity (đề xuất: có — để so sánh công bằng).
5. `Params` Pydantic: khai báo thế nào cho component dạng hàm vs class (đề xuất theo §3: class `Component` có `Params`).
6. Ngưỡng pHash (mặc định 5) cần spot-check bằng view `duplicate_pairs` trước benchmark chính thức.

### 1.4 Tạo `AGENTS.md` + `CLAUDE.md`
- `AGENTS.md` (Codex tự đọc): luật mục 1, bẫy môi trường mục 2, lệnh test, trỏ tới `PLAN.md` và guide này. Gộp nội dung còn dùng được của `AGENT.md` (quy trình nhóm) vào đây rồi xoá `AGENT.md` (cập nhật các chỗ tham chiếu).
- `CLAUDE.md`: một dòng `@AGENTS.md` (import) + ghi chú riêng nếu cần.

### 1.5 `docs/components/` (thay cho `docs/algorithms/` của nhánh cũ)
Một README chung + mỗi kind MVP một README ngắn: câu hỏi kind trả lời, **trích dẫn mục CONTRACTS** (không định nghĩa lại signature), cách thêm 1 implementation, test tối thiểu, ý tưởng tiếp theo.
Tham khảo nội dung (phần ý tưởng, test) từ nhánh cũ: `git show duy-mvp/init:docs/algorithms/<nhóm>/README.md` — **bỏ** mọi câu "code theo file này".

### 1.6 `PLAN.md`
Thay mục "Các bước" bằng checklist các lát cắt ở mục 4, mỗi lát ghi mục CONTRACTS tương ứng.

**→ Dừng. Commit docs, báo người dùng review. Không code trong phiên này.**

## 4. Các lát cắt code (mỗi lát một phiên mới)

Mỗi phiên: đọc `AGENTS.md` + `PLAN.md` + guide này + **các mục CONTRACTS của lát** → code → test → commit → tick `PLAN.md` → dừng.

| Lát | Nội dung | CONTRACTS / architecture | Xong khi |
|---|---|---|---|
| L1 | `core/`: types, Component + Params, registry (bắt buộc docstring, auto-import), Context (field theo tên, reference, pixels, rng, fields_used) | §1–4, §3 docstring | test registry + Context với field giả |
| L2 | P1: source (thư mục + upload), decoder opencv 4 fps, unit per_frame, features `emb.dinov2_s` `hash.phash` `proj.pca2`, execution lazy + disk, `store/field_store` theo `FieldKey` | §5 (1–5, 13), §7 session spec, §13, arch §4.2–4.3 | CLI `prepare` chạy trên youtube 30 s; cache đúng; đổi version một feature không mất cache feature khác |
| L3 | P2: reference `session_kept`, objective `novelty`, combiner `product_strength`, selector `greedy_nms` `random` `uniform`, `recipes.py` (diversity/random/uniform), `pipeline/propose.py` thuần | §5 (6–9), §6, §7, §9, §13 (pool nhiều video) | test contract mọi recipe trên pool 2 video; breakdown là điểm **từng objective** |
| L4 | P3 + CLI benchmark: metric `nd_rate_at_k` (pHash, độc lập), recipe × seed × K | §5 (11), §12.5 quy tắc độc lập, arch §4 | benchmark youtube5m K=200 chạy; ghi `eval/results/report.md` |
| L5a | `store/db.py` + `service/` + `commands/`: phiên (pool, goal, target_k), review_state = replay log quyết định (schema §12.3), proposal lưu snapshot input (#46) | §2, §9.7, §10, §11.1–11.2, §12.3, arch §5–6 | test vòng: tạo phiên → propose → keep/drop → mở lại → propose tiếp tránh kept |
| L5b | export CSV + ảnh, `list_recipes` / `describe_component`, `list_units`, `return_scores`, `n_sessions` (#46) | §5 (12), §7, §11.2, arch §5 | test: export 2 unit kept → CSV 2 dòng + 2 ảnh; `return_scores` khớp proposal |
| L6 | `api/`: route gọi command, upload, media, lỗi chuẩn | arch §3, §5 | TestClient smoke toàn bộ endpoint arch §5.2 |
| L7a | views (3: `embedding_map`, `timeline`, `duplicate_pairs`) + metric `keep_rate_by_recipe`, command `get_view` / `get_metrics` + route, CLI `spotcheck` (#50) | §5 (11, 14), §11.2, arch §5, §7 | `get_view` 3 view JSON serializable; `get_metrics` nhiều metric |
| L7b | spot-check pHash (người chốt ngưỡng) → benchmark chính thức → `eval/results/report.md` (#41, #50) | §9.10 | số chính thức trong report |
| L8 | `src/web/`: port `App.tsx` của nhánh cũ theo API mới, renderers theo `view.type` | arch §3, §5–7; rà `WEB_UI_ARCHITECTURE.md`, `FRONTEND_FOLLOWUP.md` ở nhánh cũ | tsc + build; người dùng test tay |
| L9 | README "Chạy MVP local", `MVP_IMPLEMENTATION.md` (viết mới), rà `RAV26_ALIGNMENT.md` §6 | – | người dùng review |

`judges/`, `routers/`: chỉ registry + contract trong L1/L5, **không** implement (quyết định #20).

## 5. Đồ tái sử dụng từ nhánh cũ (tham khảo, **viết lại theo contract**, không copy nguyên)

Xem bằng `git show duy-mvp/init:<path>` (bản cuối, commit `e1ec2ce`) hoặc `git show 1fef62f:<path>` (bản Codex gốc):

| Cần | Nhìn ở | Ghi chú |
|---|---|---|
| Decode + thumbnail an toàn với đường dẫn có dấu | `src/rav/pipeline/video.py` | `imencode` + `write_bytes` |
| DINOv2 nạp local, batch 8, `inference_mode`, đọc ảnh bằng PIL | `src/rav/algorithms/features/dinov2_s.py` | |
| Feature cache `fields/<tên>@<version>.npy`, `requires`, phát hiện vòng | `src/rav/algorithms/features/__init__.py` | L2 phải làm theo `FieldKey` §2 (thêm `params_hash`) |
| Greedy max-min + NMS theo từng video + nới bộ lọc mềm | `src/rav/algorithms/proposers/diversity.py` | Tách thành objective `novelty` + selector `greedy_nms` |
| ND-Rate@K, duplicate_pairs | `src/rav/algorithms/metrics/near_duplicate.py` | |
| SQLite: bảng `decisions` đúng §12.3, optimistic version, `json_each` đếm phiên theo video, đổi tên DB cũ | `src/rav/service/session.py` | Tách `store/db.py` |
| Upload: giới hạn 500 MB, hash nội dung, mở thử bằng decoder | `src/rav/api/app.py` | |
| Test dựng video giả có field sẵn (không chạy DINOv2) | `tests/test_rav.py::make_video` | |
| FE React sáng, tối giản | `web/src/App.tsx`, `web/src/api.ts`, `web/package.json`, `web/vite.config.ts` | Đổi sang `src/web/`, API mới |
| Số benchmark cũ | `eval/results/report.md` §6 (bản Codex) | **Chạy lại** ở L4, không chép số |

## 6. Khi bị hỏi "kiểm tra lại"
Người dùng sẽ nhờ một phiên khác kiểm tra. Tự kiểm trước khi báo xong mỗi phase/lát:
- Mọi thay đổi thiết kế đã nằm trong CONTRACTS/architecture **trước** code?
- Có chỗ nào code làm ít hơn contract mà chưa hỏi / chưa ghi "Hướng tới"?
- `docs/components/*` có câu nào tự định nghĩa lại signature khác CONTRACTS?
- Test, ruff (và tsc nếu đụng web) xanh? Chưa push?
