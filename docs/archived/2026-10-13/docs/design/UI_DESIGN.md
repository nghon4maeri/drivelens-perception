# UI_DESIGN — thiết kế giao diện web (`src/web/`)

Tài liệu thiết kế cấp 2 cho frontend (cạnh `CONTRACTS.md` cho backend). Code FE theo file này + `architecture_diagram.md` §3, §5.
Muốn làm khác → sửa file này trước. Quyết định: #53, #55, #56, #57, #58, #59, #61.

## 1. Nguyên tắc

1. **Duyệt nhanh là việc chính.** Màn Studio ưu tiên lưới ảnh và phím tắt; mọi thứ khác lùi về sau (tooltip, panel gập, màn riêng).
2. **Ít chữ, ít viền.** Phân vùng bằng nền (surface) và khoảng trắng; viền chỉ dùng cho trạng thái (ô đang focus, ô đã giữ) và ô nhập.
   Chi tiết (điểm, cách tính, id) nằm trong tooltip hoặc màn Phân tích.
3. **Số chỉ lấy từ backend.** FE không tự tính điểm; số backend chưa có thì **không hiện** (không dùng ô giữ chỗ, #55).
4. **Trạng thái không chỉ bằng màu.** Giữ / Bỏ / Chưa duyệt / Sẵn sàng / Lỗi luôn có chữ hoặc icon kèm màu.
5. **Quyết định tự lưu ngay (#61).** Mỗi Giữ / Bỏ / Huỷ quyết định là một `PATCH`, gửi tuần tự; ô đổi trạng thái **ngay khi bấm** (hiển thị
   tạm), backend là nguồn sự thật — phản hồi về thì lấy state của backend. Ô hiện **đang lưu** (chấm nhỏ) / **lưu lỗi** (nhãn đỏ + Thử lại);
   đã lưu thì không hiện gì thêm. Không toast cho từng quyết định. Lỗi lưu → ô quay về trạng thái đã lưu trước đó, có nút Thử lại.
6. **Batch là đơn vị giải thích, frame là đơn vị quyết định (#61).** Giải thích đặt một lần ở cấp đợt; card không lặp metric hay lời giải thích.
   Chỉ nói điều thuật toán / dữ liệu thực sự cung cấp; ghi rõ phạm vi (đợt đề xuất hay tập đang giữ); nhận xét AI (khi có) tách khỏi dữ liệu đo;
   không suy novelty / diversity thành lỗi model hay uplift.

## 2. Bố cục và điều hướng

```
┌──────────────┬───────────────────────────────────────────────┐
│ RAV-26       │  Màn global  ── hoặc ──  Phiên đang mở:        │
│ ─ Kho video  │                          [Studio|Đã lưu|Phân tích|Xuất]  │
│ ─ Đã xuất    │                          lưới / bảng …  │ panel phải (Studio) │
│              │                                               │
│ PHIÊN        │                                               │
│ + Phiên mới  │                                               │
│ · phiên A    │                                               │
│ · phiên B …  │                                               │
│ ⚙ theme · ?  │                                               │
└──────────────┴───────────────────────────────────────────────┘
```

- **Sidebar trái (kiểu web chat):** phần trên = điều hướng màn global (Kho video, Dữ liệu đã xuất); phần dưới = danh sách phiên
  (mới nhất trước, nút "Phiên mới"); chân = theme, phím tắt. Gập được thành dải icon.
- **Route bằng hash** (không thêm router): `#videos` (mặc định), `#exports`, `#new` (+ `&v=<id>,<id>` = video chọn sẵn), `#s=<sid>&t=<tab>` với
  `tab` ∈ `studio | archive | analysis | export`.
  Tải lại trang giữ nguyên màn và tab.
- **Trong một phiên:** thanh trên có tên phiên, nút "N video · + Thêm" (thêm video vào pool, thấy ở mọi tab) + 4 tab; Studio có panel phải (mục tiêu, ngân sách K, bản đồ nhỏ, 2 chỉ số, Nâng cao: recipe).
  Nút chính "Đợt mới" luôn ở thanh trên lưới, không phụ thuộc panel.
- **Studio ưu tiên ra quyết định (#61):** thứ tự trên màn = giải thích đợt ngắn → thanh đợt (trạng thái lưu, hoàn tác, Giữ cả, Bỏ cả, Đợt mới) →
  lưới ảnh. Panel phải chỉ còn mục tiêu, ngân sách K và các khối gập được (bản đồ 2D, chỉ số mô tả, Nâng cao: recipe). Đánh giá tốc độ,
  chất lượng quyết định, uplift ở màn Phân tích. Lưới tự giảm số cột khi ô nhỏ hơn 220 px (không ép 4×4); phím mũi tên theo số cột thật.

## 3. Màn hình

| Màn | Route | Nội dung |
|---|---|---|
| **Kho video** | `#videos` | Quản lý video kiểu file explorer: lưới thẻ (thumbnail, tên, thời lượng, trạng thái tiền xử lý, số phiên dùng); tìm theo tên; lọc trạng thái; sắp xếp. Nút "Thêm video" + kéo thả cả màn để tải lên. **Chọn nhiều** (ô tick trên thẻ) → thanh hành động dưới: "Tạo phiên mới" (sang `#new` chọn sẵn), "Thêm vào phiên…" (chọn phiên có sẵn), "Bỏ chọn"; video chưa sẵn sàng bị bỏ qua. Bấm thẻ → panel chi tiết: xem video, thông tin, các phiên đang dùng, "Tạo phiên từ video này", "Chuẩn bị" / "Thử lại". Xoá / đổi tên: chưa có (sau MVP). |
| **Phiên mới** | `#new` | Màn tập trung tạo phiên (thay hộp thoại, #58). Trái: lưới video (Sẵn sàng chọn được; đang tiền xử lý hiện %, chọn được khi xong; tải lên tại chỗ). Phải: mục tiêu (+ mẫu bấm điền), ngân sách K (gợi ý theo số frame ước tính = thời lượng × 4 fps, hiện % pool), tên (tự điền từ tên video), tóm tắt; nút "Tạo & bắt đầu duyệt" → tạo phiên, **tự đề xuất đợt đầu** bằng recipe mặc định, vào Studio. |
| **Dữ liệu đã xuất** | `#exports` | Mọi lần xuất, lọc theo phiên, tải `.zip` (#54). |
| Studio duyệt | `#s=…&t=studio` | Giải thích đợt (§3c), thanh đợt, lưới picks của đợt (tối đa 4 cột, ô ≥ 220 px), panel phải gập được. |
| Mẫu đã lưu | `t=archive` | Frame đã giữ, lọc đợt / video, xem clip, bỏ khỏi K. |
| Phân tích | `t=analysis` | Theo thứ tự tài liệu focus: (1) **Đánh giá** — tốc độ duyệt (giây / frame), chất lượng quyết định (chưa đo: cần thẩm định
độc lập), uplift (đo ngoài hệ thống, sau train); (2) **Số mô tả tập đã giữ** — gần trùng, độ phân tán, rải thời gian, keep rate theo recipe, ghi
"mô tả, không thay AP/mAP"; (3) **Kỹ thuật** (gập) — 3 view, bảng metric thô + tự chọn, lịch sử đề xuất. |
| Xuất dữ liệu | `t=export` | Xuất CSV + ảnh, tải `.zip`, các lần xuất của phiên. |
| Hộp Thêm video vào phiên | modal | Mở từ nút "N video · + Thêm": chọn video có DINO; upload tại chỗ chỉ lưu, nút chạy DINO explicit (#76). |

## 3b. Chỉ số và bằng chứng (#59)

| Số / bằng chứng | Hiện ở | Nguồn |
|---|---|---|
| Gần trùng (ND-Rate@K), keep rate | Panel phải Studio (khối gập "Chỉ số mô tả") + Phân tích | `get_metrics` |
| **Thời gian duyệt** — chỉ giây / frame (không hiện kèm frame / phút) | Màn Phân tích, khối Đánh giá | `get_metrics` |
| Min-dist DINOv2 (kèm cờ "không độc lập"), độ phủ thời gian | Màn Phân tích (số mô tả) | `get_metrics` |
| Độ phủ cụm (k-means, không có nghĩa nghiệp vụ) | Màn Phân tích, mục Kỹ thuật | `get_metrics` |
| Độ phân giải, fps, dung lượng, ngày thêm | Kho video (panel chi tiết) | `VideoDTO` |
| Khung detector | Thẻ frame khi bật **M**; cửa sổ clip (ảnh gốc) | `get_evidence` (CONTRACTS §8.0) |
| Nhãn cảnh (1–2 cái) | Thẻ frame; cửa sổ clip (đủ) | `get_evidence` |
| Điểm từng frame (hạng, combined, breakdown), frame đã giữ gần nhất | Cửa sổ chi tiết frame (Space), không trên card | `ProposalDTO`, `explain_proposal` |
| Telemetry (tốc độ, phanh, góc lái) | Cửa sổ clip, theo thời điểm đang phát | `get_evidence` |

Không có nguồn → không hiện (không ô giữ chỗ). **Không hiện:** tiền tiết kiệm gán nhãn (chưa có mô hình chi phí), cụm / độ phủ suy ra từ bản đồ 2D.

## 3c. Giải thích đợt (#61)

Mặc định **ẩn**: chỉ một nút **?** cạnh "Đợt n" trên thanh công cụ (#62 — khối luôn hiện làm mất tập trung khi duyệt); bấm mở
khung nổi, Esc / bấm ra ngoài để đóng. Nội dung từ command `explain_proposal(session_id, n)` (CONTRACTS §11.2), trả lời đúng hai câu, mỗi câu ghi phạm vi:

| Câu hỏi | Nội dung (chỉ từ dữ liệu thật) | Phạm vi ghi trên UI |
|---|---|---|
| Vì sao đợt này được đề xuất cho mục tiêu phiên? | Recipe + cách chấm (docstring component). **Nếu không objective nào đọc mục tiêu**, nói thẳng: "recipe này chưa dùng mục tiêu phiên; xếp hạng theo khác biệt với tập đã giữ" | "Đợt n · 16 frame đề xuất" |
| Nó bổ sung gì so với tập đã giữ? | Độ tương đồng tới frame đã giữ gần nhất (trung vị, cao nhất) trên embedding recipe đã dùng; số video, khoảng thời gian trải ra; tập đã giữ rỗng thì nói rõ | "so với N frame đã giữ lúc đề xuất" |

Nhận xét AI (khi có, sau MVP) là khối riêng, nhãn "Nhận xét AI — cần kiểm tra". Sau khi chuyển đợt, nếu đợt trước còn frame chưa duyệt:
trong khung nổi có một dòng "n frame chưa duyệt vẫn ở pool, có thể được đề xuất lại" (không hộp thoại chặn — Enter chỉ yêu cầu đề xuất tiếp, không chốt). Recipe tất định (vd `diversity`) không có quyết định mới thì
đề xuất lại đúng các frame đó → ghi chú thêm "m / n frame của đợt này đã có ở đợt trước"; riêng trường hợp này thanh công cụ hiện
thêm chữ nhỏ "lặp lại m" để người duyệt biết Enter đã chạy mà không cần mở khung.

## 4. Token

- **Màu:** CSS variables trong `index.css`, 3 theme (warm mặc định, dark, paper): `--canvas-bg` (nền trang), `--canvas-subtle` (sidebar, panel,
  thẻ), `--card-bg` (ô nhập, thẻ nổi, modal), `--border-color` (chỉ cho ô nhập, đường chia), `--text-primary/secondary/muted`,
  `--kept-border` (màu chính = Giữ), `--border-focus` (focus, nhấn). Không dùng hex trực tiếp trong component, trừ màu ngữ nghĩa
  amber (đang xử lý / chưa duyệt) và red (lỗi / bỏ).
- **Cỡ chữ:** 11 px nhãn / chú thích · 12 px nội dung phụ, bảng · 14 px nội dung chính, nút chính · 16 px tiêu đề khối · 20 px tiêu đề màn.
  Số và id dùng font mono.
- **Khoảng cách:** bội 4 px (4 / 8 / 12 / 16 / 24). Bo góc: 6 px (nút, ô nhập), 8 px (thẻ), 12 px (modal, panel lớn).
- **Đổ bóng:** chỉ modal và toast.

## 5. Thành phần dùng chung (`src/web/src/ui/`)

Component mới phải dùng các thành phần này, không chép chuỗi class:

| Thành phần | Dùng cho |
|---|---|
| `Button` (`primary` · `secondary` · `ghost` · `danger`; `sm` · `md`; icon tuỳ chọn) | Mọi nút có chữ |
| `IconButton` | Nút chỉ icon, bắt buộc `label` (tooltip + `aria-label`) |
| `Modal` (tiêu đề, nội dung, chân) | Hộp thoại; Esc / bấm nền để đóng |
| `Panel` | Khối nền `--canvas-subtle`, không viền |
| `ScreenHeader` (tiêu đề, mô tả, hành động) | Đầu mỗi màn |
| `Badge` (`ok` · `warn` · `error` · `neutral` · `accent`) | Trạng thái ngắn |
| `ProgressBar` | Ngân sách K, tiến độ tiền xử lý |
| `EmptyState` | Danh sách rỗng |
| `ErrorBanner` | Lỗi từ API (`errorText`), bấm để ẩn |
| `Field` / `inputClass` | Nhãn + ô nhập / select / textarea |
| `ErrorBoundary` (`main.tsx`) | Lỗi render → thông báo + tải lại, không trắng trang |

## 6. Dữ liệu và state

- **`api.ts` là chỗ duy nhất gọi `fetch`**; type DTO viết tay theo arch §5.3. Không thêm thư viện quản lý state / data (dự án nhỏ, ít màn).
- **`App`** giữ dữ liệu dùng chung: danh sách video và phiên, route; hỏi lại `GET /videos` 2 s một lần khi có video đang tiền xử lý (#56).
- **`useSessionData(sid)`** (hook) giữ dữ liệu một phiên: session, units, recipes, state, proposals; cung cấp `decide`, `undo`, `retry`, `propose`.
  Quyết định đi qua **hàng đợi tuần tự**: trạng thái tạm hiện ngay, `PATCH` lần lượt, phản hồi thay state; lỗi → bỏ trạng thái tạm, đánh dấu lỗi
  trên ô, `retry` gửi lại. `propose` chờ hàng đợi rỗng rồi mới gọi. Không tải lại session / units / bản đồ sau mỗi quyết định.
- **Không xáo trộn đợt đang duyệt:** thêm video, poll tiến độ, tải lại chỉ số / bản đồ không đổi lưới, thứ tự ô hay ô đang focus.
- **Bản đồ 2D** chỉ tải lại khi pool hoặc proposals đổi; màu điểm (giữ / bỏ / đợt này) tính lại ở FE từ state đang có.
  **Chỉ số** tải lại có độ trễ (~1 s sau thao tác cuối). **Danh sách phiên** ở sidebar cập nhật số đếm từ phiên đang mở, không gọi lại API.
- **Phím tắt:** một hook `useHotkeys` đăng ký một lần, đọc handler mới nhất qua ref; tắt khi đang gõ ô nhập hoặc có modal.

## 7. Phím tắt (Studio)

`← ↑ ↓ →` di chuyển · `K` **Giữ** · `D` **Bỏ** (không giữ — frame ra khỏi pool, không được đề xuất lại) · `U` **Huỷ quyết định** (xoá Giữ / Bỏ,
frame về Chưa duyệt, có thể được đề xuất lại) · `A` giữ cả đợt · `X` bỏ cả đợt · `Ctrl+Z` hoàn tác · `Space` chi tiết frame + clip ±3 s ·
`Enter` đợt mới (chỉ yêu cầu đề xuất tiếp) · `M` khung detector · `P` panel phải · `T` theme · `?` danh sách phím tắt.

**Focus khác Giữ:** ô đang focus có viền **ngoài** dày màu nhấn + số thứ tự đổi màu; ô Giữ có viền **trong** xanh + nhãn "Giữ".

## 8. Chưa làm (hướng tới)

- Xoá / đổi tên video, thư mục / gắn thẻ dataset, quyền truy cập — cùng đợt quản lý quyền (#55, arch §10).
- Test e2e FE trong repo (hiện kiểm bằng script Playwright ngoài repo); responsive dưới 1280 px.
- **Khi lên nhiều người dùng** (LOCAL_SINGLE_USER_LIMITS "Lộ trình", #60): router thật thay route hash (`types/navigation.ts` + `App`),
  `useAuth` + màn đăng nhập / project / thành viên, cache server-state thay `mapVersion` / `metricsVersion` (ruột `useSessionData`),
  SSE / WebSocket thay poll. Các màn và `ui/` giữ nguyên; `api.ts` thêm token + xử lý 401.

## 8. Kho dữ liệu, tham chiếu và snapshot (S7, #64–#66)

- Sidebar “Kho dữ liệu”; #videos giữ route cũ, #datasets mở tab “Dataset tham chiếu”. Hai tab Video / Dataset tham chiếu dùng chung phong cách hiện có.
- Dataset: thẻ tên/số ảnh/version/nhãn/trạng thái. Import chọn thư mục từ API, tên, kiểu nhãn auto/none/yolo, hiện gợi ý lớp và kết quả import.
  Panel xem version, lọc label_status (cả conflict), phân trang ≤500, thumbnail; nhập version mới, chuẩn bị/thử lại, kiểm file nguồn.
  Trong lúc job queued/running, poll 2 giây; lỗi/nguồn hỏng hiện bằng chữ, không chỉ màu. Không upload ZIP trong MVP local.
- Phiên mới: “Dữ liệu tham chiếu (tuỳ chọn)”, checkbox dataset + menu version sẵn sàng (mặc định version mới nhất đã sẵn sàng).
  Ghi rõ “Dùng để so sánh trong report ở giai đoạn sau. Chưa ảnh hưởng đề xuất.” Thanh phiên có chip tên dataset + vN.
- Xuất: snapshot đặt trên phần export hiện có, nút Chốt snapshot + ghi chú, danh sách số/ngày/số mẫu/Hiện hành hoặc Không còn hiện hành.
  Bấm snapshot tải thành viên và provenance đóng băng, không dùng kept hiện tại để dựng bảng. Export kept hiện có ghi rõ tập sẽ xuất.
- Studio: ẩn lựa chọn recipe/baseline, vẫn chỉnh tham số recipe hiện tại; recipe mặc định lấy theo tên diversity, không theo vị trí API.

S8: mỗi hàng snapshot thêm nút “Tải ZIP snapshot #n”; lỗi hiển thị nguồn thiếu/đổi và số mẫu bị ảnh hưởng.
Tải dùng snapshot đã chọn dù tập kept hiện tại đổi; ZIP được backend tạo tạm và dọn sau gửi. Export tập đang giữ bên dưới giữ riêng.

Biến thể màu `dark:` theo data-theme của app (dark/warm/paper), không theo theme hệ điều hành; badge cảnh báo ở warm/paper phải có chữ tối dễ đọc.

S9–S10: Phiên mới thêm cách đề xuất (Đa dạng / Tìm theo câu CLIP), câu tìm/loại trừ. CLIP dùng câu trong config, không tự suy/dịch goal; gợi ý câu ngắn tiếng Anh. Video hiển thị CLIP sẵn sàng hoặc nút Chuẩn bị CLIP cùng tiến độ/lỗi riêng, không che P1. RecipePanel chọn hai recipe sản phẩm, baseline ẩn; texts/negative nhập mỗi dòng một câu. Đợt ít/rỗng hiện “Không có thêm frame qua bộ lọc và chống trùng hiện tại; kiểm tra câu tìm/ngưỡng”, không tuyên bố pool chắc chắn không có đối tượng.
Đề xuất đầu lỗi: giữ form và câu tìm, hiện lỗi để sửa/thử lại; không chuyển sang recipe mặc định. Thử lại dùng phiên vừa tạo nếu pool/goal/K/name/reference chưa đổi, tránh tạo phiên trùng.

M1 (#70): Kho dữ liệu thêm tab Mô hình tham chiếu (#models), upload .pt/tên tuỳ chọn, liệt kê checkpoint và nút Kiểm tra file (SHA). Luôn ghi “Đã lưu checkpoint; chưa kiểm khả năng nạp/chạy YOLO”, không badge sẵn sàng inference. Phiên mới thêm chọn model tuỳ chọn, dataset bên dưới đổi nhãn thành dữ liệu train/finetune của model đã chọn; yêu cầu chọn ≥1 dataset prepared khi chọn model. Đây là khai báo người dùng, không suy provenance. Studio hiện model đang cố định trong phiên và “chưa chạy YOLO; chưa ảnh hưởng đề xuất”.

M2 (#71, CONTRACTS §17) thay trạng thái chưa inference toàn cục bằng trạng thái theo đợt: thanh YOLO dưới thanh đợt có chọn model, link upload, Chạy YOLO · N ảnh, đang chạy/lỗi/retry và kết quả/cache. Mặc định reference model của phiên nếu có. Chạy chỉ N picks, không tự chạy; vẫn giữ/bỏ/đổi đợt được. Xong bật M; ô có số đối tượng kể cả 0, ảnh gốc chi tiết có nhãn/confidence/source. Đổi đợt/model ẩn box cũ; reopen đọc đúng cache. Không đổi recipe hoặc khai báo dataset train.

V2 (#72, CONTRACTS §18): RecipePanel có reference_diversity/model_guided/text_model_guided, config stage/feature qua JSON nâng cao. Studio hiện job đang chuẩn bị, lỗi/retry, reload phục hồi; giữ/drop được khi chạy, state đổi thì job từ chối publish. Chi tiết frame render evidence primitives nearest/scalar/detections theo kind, không theo recipe. Nearest có thumbnail/link dataset/version/similarity, scalar có method/observed, prediction là bằng chứng cần kiểm. Phiên cũ thiếu reference báo tạo phiên mới; không tự gán model/dataset. Luồng cũ giữ nguyên.

C2 (#76, CONTRACTS §20): upload chỉ lưu video. Kho video/Phiên mới có trạng thái DINO và CLIP riêng, nút chạy Kaggle/retry riêng, stage + done/total + progress. Không tự xếp job; video chưa DINO vẫn thấy để bấm chạy, chỉ chọn vào phiên khi DINO+pHash sẵn. Recipe CLIP cần thêm CLIP.

## Deployment pilot — đã khóa tại DEP0 (#84)

> ⚠️ **Mục này là thiết kế giao diện web đã khóa cho deployment pilot (2–3 người dùng).**
> Quy chuẩn tại §1–7 tiếp tục áp dụng cho giao diện Studio và các thành phần cốt lõi.

### 1. Điều hướng và định tuyến deploy pilot
Deploy pilot sử dụng các route ứng dụng chính thức:
- Entry public (#104): `/` (alias `/landing`) giới thiệu IrishCoffee · T026 và RAV-26, không gọi API dữ liệu hoặc yêu cầu đăng nhập; có CTA đăng nhập/vào ứng dụng.
- `/app` chứa ứng dụng pilot với hash route ở §2b. Chưa đăng nhập → `/login?next=<đường /app kèm hash>`; đăng nhập thành công → đường nội bộ này. `next` chỉ chấp nhận `/app` cùng origin, không redirect sang URL bên ngoài. `/IrishCoffee-T026` là alias cũ vào `/app`, giữ hash.
- `/login`: Màn đăng nhập bằng Email/Password (Supabase Auth). Không có nút public signup hoặc anonymous account. Link hỗ trợ hiển thị hướng dẫn liên hệ operator để cấp/reset tài khoản.
- `/library`: Kho dữ liệu phân tách hai tab rõ ràng:
  - **Dữ liệu của tôi (Private)**: Video cá nhân đã upload, hiển thị trạng thái chuẩn bị (DINO+pHash, CLIP).
  - **Dữ liệu hệ thống / Demo (System Read-only)**: Video và tập ảnh tham chiếu có sẵn; chỉ xem, không có nút xóa.
  - **Phiên mẫu (System Demo Templates)**: Danh sách các phiên mẫu kèm lịch sử duyệt (proposals, decisions, snapshots) ở chế độ chỉ đọc. Nút **"Tạo phiên riêng từ mẫu"** tạo phiên private trống với cùng cấu hình/nguồn để người dùng tự bắt đầu.
- `/sessions/new`: Màn tạo phiên mới tập trung. Chọn video từ nguồn được phép (private hoặc system demo), mục tiêu, ngân sách K và dataset/model tham chiếu (nếu có).
- `/sessions/:id`: Studio duyệt phiên private của người dùng (gồm 4 tab: Studio, Đã lưu, Phân tích, Xuất).
- `/exports/:id`: Màn hình chi tiết xuất dữ liệu, xác nhận PII và tải bundle.

### 2. Máy trạng thái (State Machines) phía giao diện

#### A. Trạng thái xác thực (Auth State)
- `CHECKING`: Đang kiểm tra session của provider và gọi `GET /api/v2/me`.
- `SIGNED_OUT`: Chưa đăng nhập, hiển thị form `/login`.
- `SIGNED_IN`: Đã xác thực (`me.active = true`), tự động gắn Bearer Token vào mọi request `api.ts`.
- `REFRESHING`: Token sắp hết hạn, client tự động refresh ngầm một lần.
- `DISABLED_OR_EXPIRED`: Tài khoản bị khóa hoặc phiên hết hạn, dọn sạch dữ liệu tạm trên frontend và chuyển hướng về `/login`.
*Lưu ý:* Khi logout hoặc đổi tài khoản, frontend xóa sạch state, query cache, media URL trong bộ nhớ.

#### B. Trạng thái phục vụ Media (Media State)
- `CHƯA YÊU CẦU`: Lazy loading, chỉ request thumbnail khi thẻ cuộn vào viewport. Full frame và context chỉ gọi khi mở viewer.
- `PENDING`: Đang chờ worker giải mã, hiển thị skeleton/spinner + phase. Client poll định kỳ `GET /api/v2/media/requests/:id`.
- `READY`: Nhận Presigned GET URL, sử dụng cho đến thời điểm `expires_at` (TTL 5 phút). Khi hết hạn, client tự động xin capability URL mới.
- `FAILED`: Lỗi giải mã hoặc không có quyền truy cập, hiển thị icon cảnh báo an toàn.

#### C. Trạng thái Job và Quota (Job & Quota State)
- `QUEUED`: Đang xếp hàng trong database, hiển thị vị trí hoặc thông báo chờ. Có nút Hủy.
- `WAITING_RESOURCE`: Hết slot CPU/GPU hoặc chờ đến chu kỳ reset quota 4 giờ; hiển thị rõ lý do chờ (không báo ETA giả).
- `RUNNING`: Đang chạy, hiển thị tiến độ `done / total` và phase hiện tại.
- `CANCEL_REQUESTED`: Đang gửi yêu cầu dừng tới worker, hiển thị trạng thái dọn dẹp.
- `SUCCEEDED`: Hoàn thành, client tự động nạp kết quả mới nhất.
- `FAILED`: Lỗi kèm thông báo an toàn và nút Thử lại (giữ nguyên cấu hình đầu vào).
- `CANCELLED`: Đã hủy hoàn toàn.
- **Quota Widget**: Hiển thị thanh số dư quota CPU (ms) và GPU (ms) riêng biệt: hạn mức khả dụng (base), bonus (nếu có), quota đang giữ (held), và thời điểm reset tiếp theo (chu kỳ 4 giờ UTC).

#### D. Cổng xác nhận PII khi xuất dữ liệu (Export Confirmation Modal)
- Khi bấm "Xuất dữ liệu" từ một snapshot, hệ thống đưa export vào tiến trình xử lý.
- Khi export đạt trạng thái `NEEDS_CONFIRMATION`:
  - Frontend hiển thị modal xem trước: danh sách các ảnh đã được làm mờ/che mặt và biển số xe (YuNet + ONNX), cùng bản tóm tắt báo cáo (`report_basis`).
  - Hiển thị mã băm đóng băng `payload_hash`.
  - Nút **"Xác nhận xuất dữ liệu"** gửi `POST /api/v2/exports/:id/confirm` kèm `payload_hash`.
  - Sau khi xác nhận, worker đóng gói ZIP trên S3 và chuyển sang `READY`, UI cung cấp nút **"Tải về gói dữ liệu (.zip)"**.
  - Tuyệt đối không cung cấp nút tải raw ZIP bypass trên giao diện deploy.

### 2b. Giao diện pilot thực thi ở DEP5c (#96, CONTRACTS §21.16)
- Một codebase hai chế độ build: `local` (UI §1–8, `/api/v1`, không đăng nhập) và `pilot` (`/api/v2`, đăng nhập Supabase).
- Pilot giữ hash route hiện có bên trong `/app` (`#templates`, `#videos` = Kho dữ liệu, `#new`, `#s=<id>`), không thêm thư viện router; entry public/login theo §1. Sidebar chỉ hiện mục dùng được ở pilot. Local giữ hành vi cũ.
- Studio pilot: đề xuất là job (dòng trạng thái có lý do chờ, Hủy, Thử lại); khóa giữ/bỏ khi đang đề xuất; thiếu đặc trưng → nút "Chuẩn bị DINO/CLIP".
- Ảnh/clip qua media request; tab Phân tích, YOLO, xuất ZIP ẩn tới DEP5b2/DEP6.

### Màn Vận hành (#115, CONTRACTS §21.19)
- Pilot sidebar thêm Vận hành chỉ khi me.operations_read; hash #operations. Người khác mở deep link được báo không có quyền; server vẫn xác minh allowlist.
- Màn chỉ đọc: trạng thái heartbeat/freshness API/CPU-MEDIA/GPU, slots, snapshot GPU và quota; danh sách uploads/jobs có ID mở timeline, tìm UUID upload/job/request, lỗi API gần nhất. Manual refresh và mỗi 10 giây khi visible, có timestamp và cảnh báo số liệu cũ/lỗi.
- Timeline UTC kèm nguồn/browser tự báo, duration/CPU/queue/outcome; retention 7 ngày/cap/mất log không suy thành thời gian zero. Không cộng nested spans; không tự kết luận mạng hoặc accuracy. Không nút cấp quota/hủy/retry/khóa account trong lát này.

### 3. Quy ước xóa nguồn an toàn
- Nguồn SYSTEM / demo không có nút xóa.
- Nguồn PRIVATE: Khi người dùng bấm xóa, API kiểm tra nếu nguồn đang có phiên, snapshot hoặc job sử dụng thì trả về 409 `resource_in_use`. Giao diện hiển thị danh sách các nơi đang dùng và giải thích vì sao bị chặn (không cho phép xóa nguồn đang hoạt động).
