# RAV-26 — core và deployment contracts

> Cập nhật navigation #128 (2026-10-13): **§21 là deployment pilot hiện hành**, gồm frames/stability/compute bổ sung.
> §1–18 giữ shared core semantics và compatibility local; §19–20 là cloud-local lịch sử, không thay authority §21.
> [Architecture](../architecture_diagram.md), [live/candidate status](../setup/DEPLOYMENT_STATUS.md),
> [local boundary](LOCAL_SINGLE_USER_LIMITS.md). Phần Hướng tới vẫn là yêu cầu chưa implement, không suy thành tính năng live.

> Ghi 2026-09-27. Mục đích: chốt cách các component nói chuyện với nhau **trước** khi viết code.
> Nguyên tắc: mọi component thay được bằng tên trong config; component không gọi trực tiếp nhau.
> File này là thiết kế **code phải theo** (thứ bậc nguồn sự thật: [docs/README.md](../README.md)).
> Các thay đổi từ 2026-09-29: xem log quyết định #24 trở đi (`docs/research-pack/06_project_state/01_confirmed_decisions.md`).

## 1. Nguyên tắc tương tác

1. **Không gọi chéo.** Component chỉ nhận `ctx` (Context) và trả về một kiểu dữ liệu chuẩn ở §2.
   Muốn dùng kết quả của component khác → hỏi qua `ctx` (blackboard pattern).
2. **Khai báo phụ thuộc, không tự chạy.** Component khai báo `requires` (tên field cần đọc).
   `ctx` tự tìm ai `provides` field đó, chạy (hoặc lấy cache), rồi trả về. Có phát hiện vòng lặp.
3. **Căn theo `unit_id`.** Mọi kết quả (field, score, mask) là mảng căn theo đúng thứ tự `unit_ids`
   được truyền vào. Không component nào tự sắp xếp lại mà không trả kèm `unit_ids`.
4. **Stateless.** Không lưu state theo request/phiên trên `self`. State phiên nằm trong `ctx.state` (read-only; feature không đọc, §3).
5. **Khi nào tính là chính sách, không phải giai đoạn.** Component không biết mình chạy offline/lazy/stream;
   `ExecutionPolicy` quyết định. Feature chỉ khai báo `per_sample` để policy biết cache thế nào (§3).
6. **Giải thích được.** Mỗi unit được chọn mang theo breakdown điểm của từng objective.

## 2. Kiểu dữ liệu dùng chung (`rav.core.types`)

```python
@dataclass(frozen=True)
class VideoRef:
    id: str; uri: str; fps: float; duration_s: float; meta: dict

@dataclass(frozen=True)
class Frame:                      # địa chỉ, không chứa pixel
    video_id: str; idx: int; t: float

SourceKind = Literal["video", "image_set"]

@dataclass(frozen=True)
class ImageRef:                   # địa chỉ ảnh độc lập, không chứa pixel
    source_id: str                # dataset_version_id, container cache
    key: str                     # sha256(bytes)[:16]
    relpath: str                 # posix, tương đối với root của version

@dataclass(frozen=True)
class ImageSetRef:
    id: str; dataset_id: str; root: str; meta: dict

@dataclass(frozen=True)
class Unit:                       # thứ DUY NHẤT được score / chọn / duyệt / export
    id: str                       # video: id cũ; ảnh: "{dataset_id}:{key}"
    video_id: str | None
    t0: float | None; t1: float | None  # ảnh: None, không timestamp giả
    kind: Literal["frame", "span", "image"]
    anchor: Frame | ImageRef | None
    frames: tuple[Frame | ImageRef, ...]

    @property
    def source_id(self) -> str:
        return self.video_id if self.video_id is not None else self.anchor.source_id

    @property
    def order_key(self) -> tuple:
        return (self.source_id, self.t0) if self.t0 is not None else (self.source_id, self.anchor.key)

@dataclass(frozen=True)
class FieldKey:                   # định danh một kết quả tính toán → cũng là cache key
    name: str                     # vd "emb.dinov2_s", "hash.phash", "det.yolo11x"
    producer: str; version: str; params_hash: str

@dataclass
class FieldTable:                 # kết quả của một Feature trên một tập unit
    key: FieldKey
    unit_ids: list[str]
    values: np.ndarray | list     # vector: (N,D) | scalar: (N,) | structured: list[dict]
    kind: Literal["vector", "scalar", "structured"]

@dataclass
class Scores:                     # output của Objective
    name: str                     # tên objective trong config (vd "novelty")
    unit_ids: list[str]
    values: np.ndarray            # (N,), quy ước: [0,1], CAO = NÊN CHỌN
    mask: np.ndarray | None       # (N,) bool, False = loại cứng (threshold)

@dataclass
class Pick:
    unit: Unit
    combined: float
    breakdown: dict[str, float]   # objective name -> score  (phần "giải thích")
    rank: int

@dataclass
class Proposal:
    session_id: str | None        # propose() để None; service điền (benchmark: giữ None)
    n: int                        # số thứ tự proposal trong phiên, bắt đầu từ 1 (= len(ctx.proposals) + 1). Decision.proposal_n trỏ tới đây
    round: int                    # vòng duyệt. Re-roll (§10, sau MVP) tạo proposal mới cùng round → n tăng, round giữ.
                                  # MVP chưa có re-roll nên round == n
    recipe: str                   # recipe sinh ra proposal này; keep rate theo recipe tính từ đây (§10)
    config_hash: str              # hash của recipe đã resolve (config client gửi, nếu có — #45), cách tính ở §7
    picks: list[Pick]
    relaxed: bool = False         # = SelectResult.relaxed của selector (§9.6)
    fields_used: list[FieldKey] = []   # field recipe đã đọc khi propose (= ctx.fields_used() cuối lượt); metric kiểm độc lập với danh sách này (§12.5, #44)

@dataclass
class SelectResult:               # output của Selector
    indices: list[int]            # index vào `units` truyền cho selector, theo thứ hạng (rank 1 đầu tiên)
    relaxed: bool = False         # True nếu phải nới bộ lọc mềm mới đủ n (§9.6)

@dataclass
class ReviewState:               # chỉ SessionService / FeedbackHandler được ghi
    kept: set[str]; dropped: set[str]
    history: list[Decision]       # log quyết định, schema §12.3; state = replay(history)

# Decision (một quyết định keep/drop/clear) định nghĩa ở §12.3 — kiểu duy nhất cho log quyết định.
```

## 3. Base component (`rav.core.component`)

```python
class Component:
    kind: ClassVar[str]           # "feature" | "objective" | ...  (= registry)
    name: ClassVar[str]           # id trong registry, dùng trong config
    version: ClassVar[str]        # đổi khi đổi logic → vô hiệu cache
    Params: ClassVar[type[BaseModel]]   # pydantic: default + validate + schema cho UI
    requires: ClassVar[tuple[str, ...]] = ()   # tên field cần đọc (có thể lấy từ params)

    def __init__(self, params: Params): ...

class Feature(Component):        # kind = "feature", §5 dòng 4
    provides: ClassVar[str]      # tên field, vd "emb.dinov2_s"
    per_sample: ClassVar[bool] = False
    #   True : giá trị của mỗi mẫu chỉ tính từ chính mẫu đó (DINOv2, pHash; kể cả span lấy trung bình frame của nó)
    #   False: giá trị của một mẫu phụ thuộc cả tập mẫu tính cùng (PCA, chuẩn hoá theo percentile, clustering)
    def compute(self, units: list[Unit], ctx) -> FieldTable
```

`per_sample` **chỉ ảnh hưởng cache**, không ảnh hưởng logic (không liên quan frame hay span):

| `per_sample` | Execution gọi `compute` với | Cache key |
|---|---|---|
| `True` | **mọi unit của những nguồn chưa có file cache** (video hoặc dataset version, không có file đầy một nửa); gom batch tuỳ ý | `(FieldKey, source_id, spec_hash)`: mỗi nguồn một file (ảnh dùng `image_spec_hash`, §14.3), dùng chung mọi phiên cùng cách tách frame; thêm video vào pool không tính lại video cũ |
| `False` | **cả pool** (`ctx.units`, không phải candidates), một lần; ai hỏi tập con thì cắt từ kết quả | `(FieldKey, pool_hash)`, dùng chung giữa các phiên cùng pool |

- **Gom nhóm là logic bên trong `compute`**, không phải cơ chế của execution: vd `proj.pca2_video` nhận cả pool rồi tự
  `groupby(video_id)` và fit PCA từng nhóm; `proj.pca2` fit một lần trên cả pool. Cùng thuật toán, cách gom khác = implementation khác
  (khác `name`, khác field `provides`); recipe / view chọn bằng tên field trong params. Khi nhiều implementation lặp lại cùng một kiểu gom
  thì mới tách thành helper/callback dùng chung.
- Khai `per_sample=True` cho feature thật ra phụ thuộc cả tập là lỗi: cache sẽ trả giá trị sai khi pool đổi.
- **Luật phụ thuộc:** feature `requires` bất kỳ field `per_sample=False` thì bản thân cũng phải `per_sample=False`
  (vd `score.x` dùng `proj.pca2`). Vi phạm → registry/execution raise khi resolve, không âm thầm cache sai.
- **Hai hash trong cache key:**
  - `spec_hash` = `sha256` của cấu hình `decoder` + `units` trong session spec (§7), vd `{"pyav", fps 4, "per_frame"}`.
    Cùng một video nhưng tách frame khác (2 fps vs 4 fps) là hai bộ unit khác nhau → hai cache khác nhau.
    Hash này biết được **trước** khi decode (decoder serve frame theo yêu cầu, không lưu ảnh; §5 dòng 2).
  - `pool_hash` = `sha256` của danh sách `unit_id` trong pool, theo thứ tự ổn định `Unit.order_key` (§13.5).
    `unit_id` chứa video và thời điểm, nên đổi pool hay đổi cách tách frame đều ra hash khác; hai phiên cùng pool dùng chung cache.
- **Tham số của feature:** feature luôn chạy với `Params` **mặc định** (recipe chỉ gọi field bằng tên, không truyền tham số cho feature).
  Biến thể mặc định (vd ảnh 336 thay vì 224) có thể tạo feature mới **kế thừa** feature cũ, chỉ đổi `name`, `provides` và default của `Params`,
  vd `emb.dinov2_s336`. Tên field nói rõ nó là gì; hai biến thể cache riêng và so được trong cùng một benchmark.
  `Params` của feature chỉ chứa thứ **làm đổi kết quả** (vì nằm trong `params_hash`); `batch_size`, `device`, số thread
  là cấu hình của execution, không vào `params_hash`. Binding feature theo recipe và tính tập con được bổ sung ở §18; recipe cũ vẫn dùng mặc định.
- **Feature không đọc `ctx.state`** (feature là thuộc tính của mẫu). Thứ phụ thuộc kept/dropped (reference, objective, selector)
  đọc `ctx.state` mỗi lần propose và không cache (mili-giây, architecture §4.3).

Đăng ký: `@register("objective")` trên class; mỗi kind một thư mục `src/rav/components/<kind>s/` (tên kind + "s", vd `objective` → `objectives/`;
`feedback` giữ nguyên) có `base.py` + 1 file / implementation;
auto-discover khi import package. **Mọi implementation là class** kế thừa `Component`, có `Params(BaseModel)` lồng trong class
(không có dạng hàm đăng ký riêng; §9.9).
`Params` cấm tham số lạ (`extra="forbid"`): gõ sai tên trong recipe / `config` client gửi → lỗi ngay, không âm thầm dùng giá trị mặc định.

**Mô tả = docstring của class** (bắt buộc, registry từ chối class không có docstring). Viết *làm gì* và *tính thế nào*, vd
`"""Mức khác biệt so với tập đã keep: (1 − max cos(emb[u], emb[kept])) / 2. Cao = chưa có frame nào giống."""`.
Registry đọc `__doc__` và trả qua API (`GET /recipes`, `describe_component`); UI hiện cạnh điểm trong `breakdown`.
Không có method giải thích riêng; nếu cần giải thích theo từng frame thì làm thành một View.

## 4. Context (`rav.core.context`) — cổng giao tiếp duy nhất

```python
class Context:
    units: list[Unit]                       # pool của phiên (§6: pool ≠ candidates)
    videos: dict[str, VideoRef]             # video của pool theo id → ctx.pixels biết mở file nào (#37)
    state: ReviewState                     # read-only
    proposals: list[Proposal]               # read-only, lịch sử đề xuất của phiên (rỗng khi benchmark);
                                            # cho metric keep_rate_by_recipe (Proposal.recipe) và view timeline
    rng: np.random.Generator                # seed từ config → tái lập được

    def field(self, name: str, units: list[Unit] | None = None) -> FieldTable
    def cached(self, name: str, units: list[Unit]) -> FieldTable | None   # chỉ đọc cache, không tính (bằng chứng hiển thị, #59)
        # resolve producer theo name → ExecutionPolicy.run(feature, units) → căn theo units
    def reference(self) -> list[Unit]       # do component `reference` của recipe quyết định (MVP: kept set)
    def set_reference(self, ref) -> None    # pipeline gắn reference của recipe trước khi chấm (recipe đổi được giữa vòng, §10)
    images: dict[str, ImageSetRef]           # dataset version theo id
    def pixels(self, frame: Frame | ImageRef) -> np.ndarray  # RGB uint8, cache nhỏ
    # Frame: decoder.read; ImageRef: PIL đọc root/relpath, EXIF transpose rồi convert RGB
    # on_progress(field, done, total) (tuỳ chọn, hạ tầng #56): execution đếm số lần pixels() khi tính một field → tiến độ P1
    def fields_used(self) -> set[FieldKey]  # mọi field đã đọc qua ctx; P2 chép vào Proposal.fields_used (#44)
```

## 5. Contract từng extension point

| # | Kind | Signature | Đọc gì từ ctx | Trả về |
|---|---|---|---|---|
| 1 | `source` | `iter_videos() -> Iterable[VideoRef]`; tuỳ chọn `telemetry(video) -> list[TelemetrySample] \| None` (mặc định None; `local_folder` đọc file kèm `<video>.telemetry.json`, #59) | – | VideoRef |
| 2 | `decoder` | `frames(video) -> list[Frame]` (tất định từ metadata + params); `read(video, frame) -> ndarray` RGB. **Serve frame theo yêu cầu**, không ghi file ảnh (#37) | – | Frame / pixel |
| 3 | `unit` | `build(video, frames) -> list[Unit]` | – | Unit |
| 4 | `feature` | `provides: str`; `per_sample: bool`; `compute(units, ctx) -> FieldTable` (§3) | field khác (`requires`), pixels | FieldTable |
| 5 | `execution` | `run(feature, units, ctx) -> FieldTable` | – | FieldTable (cache/batch/lazy). **Hạ tầng** `core/execution.py` cạnh Context, không đăng ký qua registry (chỉ có một cách chạy); nâng thành kind khi có chính sách thứ hai, vd job nền |
| 6 | `reference` | `select(ctx) -> list[Unit]` | state | Unit[] |
| 7 | `objective` | `score(units, ctx) -> Scores`; `uses_goal: bool` (mặc định False — objective có đọc mục tiêu phiên không, cho giải thích đợt #61) | field, reference, state | Scores |
| 8 | `combiner` | `combine(scores: list[tuple[Scores, strength]], unit_ids: list[str]) -> (combined, mask)` — `unit_ids` = candidates đang chấm, để `objectives` rỗng vẫn trả đúng độ dài (#40) | – | ndarray |
| 9 | `selector` | `pick(units, combined, mask, n, ctx) -> SelectResult` | field (vd embedding cho NMS/MMR) | index + `relaxed` |
| 10 | `feedback` | `apply(decisions: list[Decision], state) -> ReviewState` | – | state mới |
| 11 | `metric` | `fields -> tuple[str, ...]` (field metric đọc, khai báo); `evaluate(unit_ids, ctx) -> dict[str, float]` | field **khác** `Proposal.fields_used` (P3 kiểm, #44); ctx chỉ để đọc dữ liệu | số đo phẳng `{<tên metric>: tổng, "by_video/<video_id>": …}` (#42); P3 `evaluate(metrics, unit_ids, fields_used, ctx)` thêm `"independent": 1.0/0.0` (#44) |
| 12 | `exporter` | `export(units, dest, ctx) -> Path` — `units` = unit đã keep theo thứ tự pool; `dest` = thư mục rỗng, mỗi lần xuất một thư mục mới (service tạo, không ghi đè lần trước, #54) (#48) | pixels | file |
| 13 | `store` | fields: `get/put(FieldKey, part)` với `part` = `(source_id, spec_hash)` (`per_sample=True`, ảnh dùng `image_spec_hash`) hoặc `pool_hash` (`False`) (§3); sessions: CRUD session / decision / proposal / snapshot (§14). Schema SQLite đổi bằng **migration theo `PRAGMA user_version`**, sao lưu file DB trước khi nâng, không bao giờ xoá DB (#64) | – | – (**tầng hạ tầng** `src/rav/store/`, không đăng ký qua registry, không thay bằng config) |
| 14 | `view` | `build(ctx) -> ViewSpec` (chỉ dữ liệu, không ảnh); tham số trong `self.params` như mọi component (#50) | field, `ctx.state`, `ctx.proposals` | ViewSpec |
| 15 | `judge` | `judge(req: JudgeRequest, ctx) -> list[Decision]` (§12) — MVP: người qua UI | pixels, clip, field, goal | Decision[] |
| 16 | `router` | `next(history, ctx) -> Action` (§12) — MVP: người qua UI | history, metrics | Action |

**Tập mẫu đưa vào `compute` do `execution` chọn theo `per_sample` (§3).** Feature tính trên đúng `units` được đưa vào,
gom nhóm (nếu cần) trong logic của nó. `ctx.field(name, units)` lấy (hoặc tính) các phần cache cần thiết, nối lại và căn theo `units` được hỏi.

**Quy tắc bind field:** objective/selector/metric là generic; field cụ thể truyền qua params
(`{"type": "novelty", "params": {"field": "emb.dinov2_s"}}`, §7), giống Lightly `input`. Đổi model embedding = đổi config, không đổi code.

## 6. Luồng một vòng đề xuất

```text
Source → Decoder → Unit builder ──► pool (§13.3) ──► candidates = pool − (kept ∪ dropped)
                                          │
   ┌─ get_score_candidates(recipe, ctx)   ▼   (chỉ lấy điểm, không chọn; tái dùng khi cần xem điểm)
   │  ctx.set_reference(recipe.reference)
   │  for obj in recipe.objectives:
   │      Scores_i = obj.score(candidates, ctx) ──(ctx.field)──► Feature ──(execution / field_store)
   └─ combined, mask = Combiner([(Scores_i, strength_i)], candidates.ids)
      sel = Selector.pick(candidates, combined, mask, batch_size, ctx)
      Proposal(picks with breakdown)  ──► UI
                                          │
      Decision[] (Judge) ──► FeedbackHandler.apply ──► ReviewState mới   (lặp tới khi kept = target_k)
```

Thuật ngữ: **pool** = tập unit của phiên theo `PoolFilter` (§13.3), chỉ được **thêm video** (không bớt, #56); **candidates** = pool trừ unit đã keep/drop, đổi sau mỗi feedback.
Objective/selector chấm và chọn trên `candidates` (đọc `ctx.state` mỗi vòng, không cache); feature `per_sample=False` tính trên `pool`.

Benchmark = cùng luồng, `state` rỗng, lặp `recipes × seeds × K`, rồi `Metric.evaluate` với từng metric chọn qua config của lần benchmark (không qua recipe: recipe không tự chọn thước chấm mình, §12.5).

## 7. Config — tách **session spec** (cố định) và **recipe** (đổi được)

Session spec quyết định danh tính của unit, nên cố định khi tạo phiên:

```json
{
  "source":   {"type": "local_folder", "params": {"root": "data/videos"}},
  "pool":     {"video_ids": ["6WTF1JV5knI"]},
  "decoder":  {"type": "pyav", "params": {"fps": 4}},
  "units":    {"type": "per_frame"},
  "feedback": {"type": "keep_drop"},
  "goal":     "giữ frame khác biệt về cảnh/ánh sáng/đối tượng; bỏ frame mờ hoặc gần trùng frame đã giữ",
  "target_k": 200
}
```

`goal` (§12.2) và `target_k` (ngân sách: tổng số frame muốn chọn) thuộc phiên, cố định khi tạo phiên. `pool.video_ids` chỉ được thêm video đã chuẩn bị
(`add_videos_to_session`, #56); proposal cũ vẫn tái lập được vì snapshot input lưu `kept_ids`/`dropped_ids` và candidates lúc đó.

**Hình dạng một component trong config** (session spec và recipe, quyết định #39): `{"type": <name>, "params": {...}}`.
Mọi tham số của component (kể cả `field`) nằm trong `params` và validate thẳng bằng `Params` của class; `params` khớp 1–1 với
`params_schema` (UI sinh form). Khoá ngoài `params` chỉ là khoá nối dây: `type`, và `strength` của objective (thuộc combiner).
Bỏ trống `params` = mặc định. Tạo component: `REGISTRY.build(kind, config)`.

**Recipe** ("cách đề xuất") là **công thức ghép** reference + objective + combiner + selector (§5 dòng 6–9),
đổi được giữa các vòng (§10). Recipe khai báo **trong code Python** ở `src/rav/components/recipes.py`
(Pydantic `RecipeConfig`, định nghĩa ở `core/types.py`, hình dạng như JSON dưới), **không** dùng file JSON ngoài `src/`.
`RecipeConfig` có thêm `description: str = ""` (mô tả cho người/agent, `RecipeDTO.description`), không tính vào `config_hash` (#48).
`recipes.py` đăng ký từng recipe vào registry (nạp cùng `discover()`); pipeline / service / command đọc recipe qua registry,
không import `components/` (luật import, architecture §2):

```json
{
  "name": "diversity",
  "reference":  {"type": "session_kept"},
  "objectives": [
    {"type": "novelty",   "strength": 1.0, "params": {"field": "emb.dinov2_s"}},
    {"type": "threshold", "strength": 1.0, "params": {"field": "q.sharpness", "min": 0.2}}
  ],
  "combiner": {"type": "product_strength"},
  "selector": {"type": "greedy_nms", "params": {"field": "emb.dinov2_s", "nms_s": 1.0, "min_dist": 0.1}}
}
```

Baseline là **selector** với `objectives` rỗng, không qua bộ lọc diversity (§9.8):

```json
{"name": "random",  "reference": {"type": "session_kept"}, "objectives": [], "combiner": {"type": "product_strength"}, "selector": {"type": "random"}}
```

```json
{"name": "uniform", "reference": {"type": "session_kept"}, "objectives": [], "combiner": {"type": "product_strength"},
 "selector": {"type": "uniform", "params": {"allocation": "proportional"}}}
```

`objectives` rỗng → combiner trả `combined = 1` cho mọi unit, `mask` toàn `True`.

`batch_size` và `seed` không thuộc recipe: truyền theo từng lần đề xuất — `propose(recipe, batch_size, seed, ctx)`.
`seed` là nguồn ngẫu nhiên duy nhất: `propose` tạo lại `ctx.rng` từ `seed` → cùng input + seed → cùng output.
Kept/dropped **không** là tham số riêng: `propose()` đọc từ `ctx.state` (một nguồn state duy nhất; architecture §4.4).

`Scores.name` = `type` của objective, cũng là key trong `Pick.breakdown` (Hướng tới: khoá `name` tuỳ chọn khi recipe có hai objective cùng type).

**`config_hash`** = 16 ký tự hex đầu của sha256 trên JSON (key sắp xếp) của recipe **đã resolve**: với mỗi component
`[type, version, params đã validate qua Params (điền mặc định, ép kiểu)]`, objective kèm `strength`. Cùng cấu hình thật → cùng hash,
dù JSON gửi lên viết khác (`{}` vs `{"nms_s": 1.0}`, thứ tự key); đổi `version` component → hash khác.

**Điểm để xem** (`return_scores`, so sánh với field): `Proposal` chỉ chứa kết quả chọn. Ai cần điểm của mọi candidate trong một vòng
thì dựng lại `ctx` từ snapshot input của proposal (kept/dropped, recipe, seed) và gọi `get_score_candidates(recipe, ctx)` — tất định,
mili-giây, không lưu thêm (quyết định #39).

## 8. Mở rộng về sau không phá contract

### 8.0. Bằng chứng hiển thị cho từng frame (#59)

UI hiện bằng chứng nếu có, ẩn nếu không — giai đoạn thuật toán chỉ cần tạo field / dữ liệu theo quy ước dưới, không sửa UI:

| Nguồn | Quy ước | Giá trị mỗi unit |
|---|---|---|
| Detector | field tên `det.<model>`, `per_sample=True`, kind `structured` | `[{"label": str, "score": float, "box": [x1, y1, x2, y2]}]`, box chuẩn hoá 0–1 theo ảnh |
| Nhãn cảnh | field tên `tag.<nhóm>` (vd `tag.scene`), `per_sample=True`, kind `structured` | `{"weather": "rain", "timeofday": "night", …}` (giá trị chữ) |
| Telemetry | `Source.telemetry(video)` | `TelemetrySample = {t, speed_kmh?, brake?, steer_deg?, lat?, lon?}` theo giây video |

Command `get_evidence(session_id, unit_ids)` chỉ **đọc cache** (`ctx.cached`), không tính — tính field nặng là việc của P1 / thuật toán. Nhãn ẩn của dataset
(giả lập annotation) **không** được đưa vào đây (tài liệu focus §4B).

YOLO theo đợt (§17) trả detection cùng định dạng qua command riêng; FE overlay đúng đợt/model, không chèn vào get_evidence mặc định hoặc scoring.


- **Sequence/clip:** unit builder `window` sinh `Unit(kind="span")`; feature span có thể `requires` field frame rồi aggregate. Objective/selector/UI giữ nguyên.
- **YOLO disagreement:** feature `det.yolo11s`, `det.yolo11x` (structured) → feature `score.disagree` (scalar) → objective `weights`.
- **More/less-like-this:** ghi vào `Decision.extra` (§12.3) + FeedbackHandler mới + objective `similarity` đọc state.
- **Chọn theo mô tả text** (S9, #68): feature `emb.clip_b32` + objective `text_match`, xem §15.
  Text là tham số trong config proposal; luồng LLM suy structure từ goal (#63) vẫn Hướng tới, không giả lập dịch/hiểu goal.

## 9. Quyết định (2026-09-27)

1. **Objective tự chuẩn hoá** về [0,1], cao = nên chọn. Combiner không chuẩn hoá lại.
2. **Diversity nằm ở Selector** (quyết định cấp tập hợp: NMS/MMR). Objective chỉ chấm từng unit, độc lập nhau.
3. **Unit đã hiện nhưng chưa phản hồi được hiện lại** nếu vẫn đứng top. Chỉ keep/drop loại unit khỏi candidates.
   `shown` chỉ để lưu lịch sử, không dùng để lọc.
4. **`Params` dùng Pydantic** (validate + default + JSON schema cho form ở UI).

Bổ sung 2026-09-29 (người dùng chốt, trước khi code P2):

5. **Novelty khi reference rỗng** (vòng đầu chưa keep gì): so với **tâm của pool** thay cho tập đã keep.
   `c = normalize(mean(emb[pool]))`; `d_u = 1 − cos(emb[u], c)`; min-max `d` về [0,1] trên các candidates đang chấm (mọi `d` bằng nhau → 1.0).
   Ưu tiên frame ít điển hình; đa dạng trong batch vẫn do selector lo (§9.2). Có reference → `(1 − max cos(emb[u], emb[ref])) / 2` (chia 2 để cos ∈ [−1, 1] vẫn cho điểm trong [0,1], §9.1).
6. **Selector không đủ `batch_size`** vì NMS/ngưỡng khoảng cách: nới dần bộ lọc mềm (NMS thời gian, `min_dist`) tới khi đủ
   hoặc hết candidates; trả `SelectResult.relaxed = True`, pipeline chép sang `Proposal.relaxed`. **Không bao giờ** nới tính duy nhất của `unit_id` và `mask` cứng của objective.
   Candidates ít hơn `batch_size` → trả hết candidates.
7. **Không keep vượt `target_k`:** service từ chối thay đổi làm `|kept| > target_k` (lỗi 422, `code = "target_k_reached"`).
   Drop / clear vẫn được.
8. **Baseline `random` / `uniform` là selector** với `objectives` rỗng (§7), không qua bộ lọc diversity → so sánh công bằng với recipe diversity.
9. **`Params`:** mọi implementation là class kế thừa `Component` với `Params(BaseModel)` lồng trong class (§3). Không có dạng hàm.
10. **ND-Rate@K** = tỉ lệ frame trong tập K frame có ít nhất một frame khác trong tập cách Hamming pHash ≤ ngưỡng. Thấp = tốt; tập rỗng → 0.
    **Ngưỡng** mặc định Hamming ≤ **8** (chốt sau spot-check L7b, #51; trước đó 5). pHash luôn có 32 bit 1 → khoảng cách luôn chẵn, chỉ ngưỡng chẵn có nghĩa.
    Trước benchmark chính thức **bắt buộc** spot-check bằng view
    `duplicate_pairs` và ghi kết quả spot-check vào `eval/results/report.md`. Số benchmark trước đó (PLAN L4) ghi rõ là **sơ bộ**; spot-check + số chính thức ở L7 (#41).

## 10. Đổi cách propose giữa phiên

Tình huống: đang duyệt, thấy recipe hiện tại đề xuất tệ, muốn đổi sang recipe khác.

**MVP hỗ trợ:** đổi recipe, áp dụng từ vòng sau.

- Mỗi vòng đọc một **snapshot** của recipe lúc bắt đầu vòng; đổi recipe không ảnh hưởng vòng đang chạy.
- Keep/drop đã có **giữ nguyên**: đó là đánh giá về unit, không phụ thuộc recipe nào đề xuất.
  Recipe mới vẫn loại unit đã drop và tính novelty so với tập đã keep.
- Unit của vòng dở chưa phản hồi vẫn nằm trong candidates (§9.3).
- Mỗi `Proposal` lưu `recipe` + `config_hash` → tính được **keep rate theo recipe** ngay trong phiên (metric online để biết recipe nào tệ):
  keep rate của recipe R = kept / (kept + dropped), đếm trên các `Decision` có `proposal_n` trỏ tới proposal do R sinh ra.
- Feature mới mà recipe mới cần (vd đổi `emb.dinov2_s` → `emb.clip_b32`) được tính lazy khi `ctx.field` hỏi tới;
  cache cũ giữ nguyên vì `FieldKey` khác. Một phép tính không bao giờ trộn hai `FieldKey`
  (pool và reference luôn lấy cùng một key).
- Session spec (§7) **không đổi** giữa phiên: đổi source/decoder/unit builder làm đổi `unit_id`. Muốn đổi → tạo phiên mới.

API: `POST /sessions/{id}/proposals {"recipe": "<name>", "config": {...RecipeConfig đầy đủ, tuỳ chọn}, "batch_size": 16}` (#45) (chi tiết: `docs/architecture_diagram.md` §5).

**Chừa chỗ, chưa làm trong MVP:**
- *Re-roll*: bỏ vòng hiện tại, propose lại với recipe/param khác, không tăng `round`.
- *Preview A vs B*: dry-run recipe trên state hiện tại, không ghi DB, hiện hai lưới cạnh nhau.
- *Fork phiên*: tách nhánh từ vòng N để so sánh hai recipe trên cùng điểm xuất phát.

## 11. Chừa chỗ cho agent (chatbot điều khiển giao diện + report chỉ số)

Mục tiêu tạm thời: người dùng không biết bắt đầu từ đâu (như khi mở Lightly/FiftyOne) → hỏi chatbot.
Agent **chỉ ra lệnh thao tác và report**, không có logic chọn mẫu riêng.

### 11.1. Command layer là nguồn sự thật duy nhất

Mọi hành động (UI bấm nút, REST, agent) đều đi qua một **command** đã đăng ký:

```python
class Command:
    name: str                     # "propose"
    description: str              # agent đọc để biết khi nào dùng
    Input: type[BaseModel]        # Pydantic → JSON schema → tool schema cho LLM + route FastAPI
    Output: type[BaseModel]
    mutates: bool                 # có ghi state không
    needs_confirm: bool           # agent gọi thì UI phải hỏi user trước
    def run(self, inp, app) -> Output
```

- REST route và tool schema của agent đều **sinh từ cùng danh sách command** → UI và agent không bao giờ lệch nhau.
  REST: bảng `(method, path, command)` + handler chung (architecture §5.1). Command trả danh sách có Output là mảng (`RootModel[list[...]]`),
  không bọc trong object → command, API và agent thấy cùng một hình dạng (#49).
- Agent không import pipeline/store; chỉ gọi command. Đổi agent (LLM, prompt, framework) không đụng phần còn lại.

### 11.2. Command tối thiểu

| Command | Mutates | Confirm khi agent gọi | Dùng cho |
|---|---|---|---|
| `list_recipes` / `describe_component(kind, name)` | – | – | "có những cách chọn nào, param gì" (từ registry + Pydantic schema) |
| `list_videos()` / `upload_video(file)` | – / ✓ | – / ✓ | xem / thêm video vào catalog (architecture §5.2) |
| `prepare_video(video_id)` | ✓ | – | chạy P1 cho một video, đồng bộ (CLI `prepare`; `create_session` gọi cùng service cho video chưa có cache) |
| `queue_prepare(video_id)` | ✓ | – | xếp P1 chạy nền (một worker); `upload_video` tự gọi; tiến độ ở `VideoDTO.prepare` (#56) |
| `add_videos_to_session(session_id, video_ids)` | ✓ | ✓ | thêm video **đã chuẩn bị** vào pool của phiên (không bớt; video đang chuẩn bị → 409 `video_preparing`, #56) |
| `get_frame(video_id, idx, max_side?)` | – | – | ảnh JPEG một frame (UI: thumbnail / ảnh lớn), decode theo yêu cầu (#37) |
| `get_video_file(video_id)` | – | – | đường dẫn file video trên máy chủ cho route media `GET /media/videos/{video_id}` (tua, Range); không lộ ra client (#49) |
| `list_sessions()` / `get_session(sid)` | – | – | chọn / mở lại phiên |
| `list_units(session_id, video_id?)` | – | – | unit trong pool của phiên; `video_id` chỉ lọc hiển thị (API `GET /sessions/{sid}/units`, #46) |
| `create_session(pool, goal, target_k, name?)` | ✓ | ✓ | bắt đầu phiên |
| `propose(recipe, config?, batch_size, seed?)` | ✓ | – | "đề xuất 16 frame bằng novelty" / đổi recipe (§10). `config` = `RecipeConfig` **đầy đủ** (client lấy từ `list_recipes`, sửa rồi gửi lại); bỏ trống = recipe có sẵn tên `recipe`. Không ghép một phần (#45) |
| `list_proposals(session_id)` | – | – | lịch sử đề xuất của phiên, dạng `ProposalDTO` kèm snapshot input (API `GET /sessions/{sid}/proposals`) |
| `get_state()` / `update_state(changes)` | – / ✓ | – / ✓ | xem / keep / drop / clear — "drop hết frame mờ vừa rồi" |
| `get_view(session_id, name, params)` | – | – | dữ liệu biểu đồ; view dựng trên ctx của phiên (state + proposals), `params` validate bằng `Params` của view (#50) |
| `get_metrics(session_id, metrics?, on?)` | – | – | report: `progress` (target_k, kept, dropped, proposals) + `results` = mỗi metric một dict phẳng (#42). `metrics` = `{"type", "params"}` như `run_benchmark` (mặc định `[nd_rate_at_k, keep_rate_by_recipe, review_time, min_dist, temporal_coverage, cluster_coverage]`, #59); `on` = `"kept"` (mặc định) hoặc `proposal_n`. Chấm bằng P3 `evaluate`; `fields_used` = của proposal đó / gộp các proposal đã đề xuất unit kept (#50) |
| `explain_proposal(session_id, n)` | – | – | giải thích cấp đợt cho UI (#61): recipe + docstring, có objective nào đọc `goal` không (`Objective.uses_goal`), tập đã giữ lúc đề xuất, độ tương đồng tới frame đã giữ gần nhất của từng pick (embedding recipe đã dùng), số video / khoảng thời gian; chỉ dữ liệu thật |
| `get_evidence(session_id, unit_ids)` | – | – | bằng chứng hiển thị của các unit: detections / tags / telemetry quanh t0, chỉ đọc cache (§8.0, #59) |
| `explain_pick(unit_id)` | – | – | ⏳ để sau (giải thích trực quan cần bằng chứng); MVP chỉ có `Pick.breakdown` |
| `run_benchmark(recipes, K, seeds, metrics?, video_ids?)` | – | – | so baseline: chọn một lần K frame (batch_size = K); `metrics` = danh sách `{"type", "params"}` áp chung mọi recipe (không nằm trong recipe, §12.5), mặc định `nd_rate_at_k` (#43) |
| `export(session_id, format)` | ✓ | ✓ | xuất cho gán nhãn; mỗi lần một bản mới `exports/<sid>/<export_id>/`, giữ mọi lần (#54). Chuyển dần sang export theo `snapshot_id` (§14, S8) |
| `create_snapshot(session_id, note?)` | ✓ | ✓ | đóng băng collection (kept) + context thành snapshot bất biến; trùng cả `members_hash` lẫn `context_hash` với snapshot mới nhất → trả lại nó (`created = false`); kept rỗng → 422 `empty_collection` (§14.2) |
| `list_snapshots(session_id)` / `get_snapshot(snapshot_id)` | – | – | snapshot của phiên (mới nhất trước) kèm `is_current`, `decisions_after`; `get_snapshot` kèm context + danh sách mẫu có provenance (§14.2) |
| `list_exports(session_id?)` | – | – | các lần xuất (mọi phiên, hoặc một phiên), mới nhất trước — UI màn "Đã xuất" (#54) |
| `get_export_file(session_id, export_id)` | – | – | đường dẫn file zip của một lần xuất (CSV + ảnh) cho route tải về; không lộ ra client (#54) |
| `ui_focus(target)` | – | – | điều hướng giao diện: mở frame, nhảy tới timestamp, lọc lưới |

### 11.3. Đồng bộ với giao diện (sau MVP, khi có agent hoặc nhiều client)

- Command có `mutates` ghi một **event** `{actor, command, input, output_ref, at}` vào log phiên.
  UI theo dõi log (poll/SSE) → khi agent propose, lưới trên UI tự cập nhật; user thấy agent đã làm gì.
- `ui_focus` không đổi state, chỉ trả `ui_intent` (vd `{"action": "open_unit", "unit_id": ...}`) để frontend thực hiện.
- Hành động của agent có `needs_confirm` → UI hiện hộp xác nhận; sau khi user đồng ý mới chạy, `actor = "agent"`.

### 11.4. Phạm vi MVP

- Làm: command layer (UI dùng luôn) + REST sinh từ command.
- Chừa chỗ: agent = một client của command layer (kind `agent`, thay được LLM/prompt). Không có trong vòng 4 ngày đầu trừ khi còn thời gian.
- Không làm: agent tự chọn recipe/tự tune, agent tự keep/drop không hỏi.

## 12. Vai trò ra quyết định: Router · Proposer · Judge

Mục tiêu: nền tảng dành cho con người là một **workflow tường minh**, mỗi quyết định có input/output rõ ràng,
để sau này **người, thuật toán hoặc AI** đều lấp được vai mà chỉ cần thêm một file implementation,
không phải dựng kiến trúc tự động riêng.

**Thứ tự ưu tiên của tự động hoá** (quyết định 2026-09-27):
1. **(a) Đánh giá recipe không cần người** — mục tiêu chính.
2. **(b) Giảm công duyệt** — nhờ log quyết định của người thành dữ liệu huấn luyện.
3. **(c) Tự động ở một mức nào đó** — khi có judge đủ tin (LLM/VLM rẻ hơn, hoặc thuật toán nắm rõ tiêu chí người), chỉ cần cắm judge.

**Phạm vi MVP: chỉ implementation `human` + log quyết định đầy đủ.** Judge/router tự động và CLI `simulate` đều chưa làm.

### 12.1. Ba vai trò

| Vai trò | Câu hỏi | Context để quyết định | Output |
|---|---|---|---|
| **Router** | Làm gì tiếp: recipe nào, batch_size bao nhiêu, hay dừng (đủ `target_k`)? | **Tổng hợp:** lịch sử proposal + decision, chỉ số, ngân sách | `Action` |
| **Proposer** | Nên xem frame nào tiếp? | **Rộng, nông:** cả pool, field rẻ (embedding), `review_state` | `Proposal` — chính là `propose()` |
| **Judge** | Frame này có đáng giữ không, theo `goal`? | **Hẹp, sâu:** từng frame được đề xuất, ảnh gốc, clip ±vài giây, breakdown, tập đã keep, `goal`; (tuỳ nguồn) nhãn dataset | `Decision[]` |

Proposer và Judge tách riêng vì context và chi phí khác nhau (cascade: tầng rộng rẻ lấy ứng viên, tầng hẹp đắt quyết định).
Judge được phép dùng tín hiệu đắt (ảnh gốc, clip, VLM, nhãn) vì chỉ chạy trên batch_size frame.

### 12.2. Goal là input tường minh của phiên

Mỗi phiên có `goal`: câu mô tả "đáng giữ" nghĩa là gì, vd *"giữ frame khác biệt về cảnh/ánh sáng/đối tượng; bỏ frame mờ hoặc gần trùng frame đã giữ"*.
Người đọc nó trên UI; judge tự động sau này nhận nó làm input. `goal` được lưu cùng phiên và ghi vào mỗi decision.
MVP: câu tự do. Sau: có thể thêm tiêu chí có cấu trúc.

### 12.3. Contract

```python
Actor = str                       # "human:<tên>" | "algo:<name>@<version>" | "ai:<model>"

@dataclass(frozen=True)
class Decision:                   # = một dòng log quyết định; schema giữ ổn định từ MVP
    session_id: str
    unit_id: str
    action: Literal["keep", "drop", "clear"]   # clear = bỏ quyết định trước đó
    actor: Actor
    goal: str                     # goal của phiên lúc quyết định
    proposal_n: int | None        # quyết định cho proposal nào (None nếu sửa ngoài proposal)
    decided_at: datetime
    shown_at: datetime | None     # lúc frame được hiện → thời gian cân nhắc = decided_at − shown_at
    reason: str | None = None     # tuỳ chọn với người; bắt buộc với algo/ai
    confidence: float | None = None
    extra: dict = field(default_factory=dict)   # chừa chỗ: tag lý do, more/less-like-this (§8). Có từ MVP để schema log không phải đổi

@dataclass
class JudgeRequest:
    proposal: Proposal
    goal: str
    context_s: float = 2.0        # kept/dropped đọc qua ctx.state, không truyền riêng

class Judge(Component):           # kind = "judge"
    def judge(self, req: JudgeRequest, ctx: Context) -> list[Decision]
    # ctx cho phép: pixels(frame), clip(unit, context_s), field(...), labels(...) nếu nguồn có nhãn

Action = Propose(recipe: str, config: RecipeConfig | None, batch_size: int) | Stop(reason: str)   # config như command propose (#45)
History = list[Proposal | Decision]   # theo thời gian: mỗi proposal, rồi các quyết định cho nó

class Router(Component):          # kind = "router"
    def next(self, history: History, ctx: Context) -> Action
```

Người là một implementation như mọi implementation khác: UI hiện `JudgeRequest` (proposal + ngữ cảnh + goal), người bấm keep/drop
tạo `Decision` với `actor = "human:..."`; người chọn recipe + bấm "Đề xuất" là `Router` thủ công.
**MVP không có class `HumanJudge`/`HumanRouter` chạy được** — UI + API chính là chúng.

### 12.4. Cắm tự động sau này (không cần kiến trúc mới)

```python
def run_workflow(session, router: Router, judge: Judge) -> History:   # ~10 dòng, CLI
    while not isinstance(action := router.next(history, ctx), Stop):
        proposal  = propose(action.recipe, action.batch_size, seed, ctx)       # đọc ctx.state
        decisions = judge.judge(JudgeRequest(proposal, session.goal), ctx)
        ctx.state = apply(decisions, ctx.state)                               # feedback §5 dòng 10
        history += [proposal, *decisions]
```

| Chế độ | Router | Judge | Trạng thái |
|---|---|---|---|
| Thủ công | người (UI) | người (UI) | **MVP** |
| Gợi ý | người | algo/ai → gợi ý, người xác nhận | ⏳ |
| Mô phỏng (mục tiêu a) | algo | algo (vd dùng nhãn dataset) | ⏳ |
| Tự động (c) | algo/ai | algo/ai đã được kiểm chứng | ⏳ |

Ý tưởng implementation (chưa làm): judge `rule_dup`, `rule_quality`, `oracle_labels`, `learned` (học từ log quyết định của người), `vlm`;
router `fixed`, `round_robin`, `bandit`, `llm_agent` (§11).

### 12.5. Đánh giá mà log này mở ra

1. **(a) Benchmark mô phỏng:** recipe × router × judge mô phỏng × seed → keep rate, số proposal để đạt ngân sách, ND-Rate trong tập keep, coverage (khi có nhãn).
2. **Độ tin của judge tự động:** chạy judge trên đúng các proposal người đã duyệt → tỉ lệ đồng ý, Cohen's κ. Chỉ khi đủ cao mới cho thay người.
3. **(b) Học từ người:** log (embedding, goal, action) → mô hình nhỏ dự đoán "người sẽ giữ" → dùng làm judge gợi ý hoặc objective mới cho proposer.

**Quy tắc độc lập:** judge, proposer và metric khai báo field mình dùng (proposer: `Proposal.fields_used`; metric: `Metric.fields`, #44); nếu judge dùng cùng tín hiệu với metric đang chấm
(vd judge dựa pHash + metric ND-Rate pHash) thì kết quả ghi rõ là không độc lập, không dùng làm bằng chứng.
**Rủi ro Goodhart:** router tối ưu theo keep rate của một judge tự động sẽ học theo độ lệch của judge → luôn kèm một metric độc lập.

### 12.6. Quan hệ với agent (§11) và alignment

- Agent chatbot ở §11 là trường hợp riêng: `ai:<model>` đóng vai Router (và Judge ở chế độ gợi ý).
- Human review vẫn là luồng chính của sản phẩm (đề bài gốc yêu cầu); tự động hoá là chế độ bổ sung
  ([RAV26_ALIGNMENT.md](RAV26_ALIGNMENT.md) U5, W2).

## 13. Pool tổng quát: 1 video / 1 frame chỉ là trường hợp n = 1

Nguyên tắc: **thuật toán không biết và không cần biết có mấy video.** `propose()` nhận một pool unit bất kỳ;
1 video, 1 frame hay 1 unit dạng span đều là trường hợp suy biến của trường hợp tổng quát (tham khảo AIC-HCMC-2026,
`local-client/local-backend/app/db/numpy_vector_store.py`: một bảng phẳng mọi frame của mọi video, id toàn cục,
thao tác theo video chỉ là **lọc** để hiển thị / hậu xử lý).

### 13.1. Quy tắc

1. **Id toàn cục.** `Unit.id` chứa `video_id` (`"{video_id}:{t0}-{t1}"`) → không trùng giữa video; `FieldTable`,
   `ReviewState`, `Decision` chỉ dùng `unit_id`.
2. **Phiên không gắn với dữ liệu trong id.** `session_id = uid` (ngẫu nhiên). Dữ liệu của phiên do `pool` quyết định.
3. **Pool = bộ lọc trên bảng unit toàn cục.** Session spec có `pool: PoolFilter`:
   ```python
   class PoolFilter(BaseModel):
       video_ids: list[str] | None = None   # None = mọi video trong catalog
       # chừa chỗ: t_range, tags (thời tiết, giờ...), exclude_unit_ids ...
   ```
   MVP: pool lọc đúng 1 video. Thêm điều kiện lọc = thêm trường, không đổi hình dạng API.
4. **Tính và cache theo `per_sample` (§3), đọc theo pool.** Feature `per_sample=True` lưu **mỗi nguồn một file**
   theo `(source_id, spec_hash)` (ảnh dùng `image_spec_hash`, §14.3) (thêm video không tính lại video cũ; đổi fps là cache khác); frame không lưu, decoder serve theo yêu cầu (#37);
   feature `per_sample=False` tính trên cả pool, lưu theo `pool_hash` (định nghĩa ở §3);
   `ctx.field(name)` nối các file (mmap) của những video trong pool thành một `FieldTable` căn theo `unit_ids`.
5. **Thứ tự unit ổn định:** `Unit.order_key`: video `(video_id, t0)`, ảnh `(source_id, key)`.
6. **Video chỉ xuất hiện ở ba chỗ, đều ngoài lõi thuật toán:**
   - Lọc pool (điểm 3) — chọn dữ liệu đầu vào.
   - Lọc hiển thị ở UI/API (`?video_id=`), view `timeline` — hậu xử lý.
   - Tham số nội bộ của component cần so thời gian: NMS thời gian và ngữ cảnh ±vài giây chỉ so trong cùng `video_id`;
     baseline `uniform` có `allocation ∈ {proportional, equal}` để chia K giữa video (PRD FR-04).
7. **Metric báo tổng, kèm `by_video`** như một góc nhìn hậu xử lý.
8. **Split theo video** khi sau này đánh giá downstream (train/test), tránh rò rỉ.

## 14. Dataset, snapshot và lưu trữ theo tham chiếu (giai đoạn 1, #64)

### 14.0. Ngữ nghĩa dataset và model tham chiếu (#69)

Trong luồng người dùng đã làm rõ, **dataset tham chiếu** là dữ liệu train/finetune của **YOLO tham chiếu**: checkpoint hiện có cần cải thiện trên pool dữ liệu mới chưa gán nhãn. Không mặc định coi dataset này là validation/test độc lập, hoặc YOLO này là teacher lớn hơn. Mục tiêu là chọn mẫu bổ sung để gán nhãn rồi train/finetune lại checkpoint đó; dataset làm mốc dữ liệu model đã học. Nhận định model perform kém trên pool là thông tin người dùng cung cấp, chưa phải metric hệ thống đo.

Nạp/chọn checkpoint và khai báo liên hệ với version dataset train theo M1 (§16); inference/cache prediction để xem từng đợt theo M2 (§17). V2 (§18) nối prediction consistency và mốc dataset vào tiêu chí curation; không đưa teacher/pseudo-label/training vào app. Recipe S9 chỉ dùng CLIP và novelty với kept. Nhãn tập train không phải ground truth của pool mới; prediction/confidence/embedding không tự chứng minh lỗi hay uplift trên pool chưa gán nhãn. Kiểm chứng downstream vẫn Hướng tới.

Chi tiết và thứ tự slice: [PHASE1_DATASET_SNAPSHOT.md](../archived/2026-10-13/docs/design/PHASE1_DATASET_SNAPSHOT.md). Mục này giữ phần đã triển khai; các slice sau bổ sung vào đây.

### 14.1. Lưu trữ theo tham chiếu

- Dataset version và snapshot **chỉ lưu manifest / tham chiếu tới raw**: frame video = video gốc (id + `sha256`) + chỉ số frame + cấu hình decoder;
  ảnh = file gốc (đường dẫn tương đối + `sha256`). Không materialize ảnh khi keep hay khi tạo snapshot; chỉ khi tải về mới đọc / decode và đóng ZIP.
- Thumbnail là cache xoá được. Không sao chép raw, không tách lưu toàn bộ frame, không tự chuyển / xoá file nguồn.
- Khả năng export phụ thuộc raw còn tồn tại và đúng nội dung. Raw đổi → **không** sửa manifest cũ; báo nguồn không còn khả dụng.

### 14.2. Snapshot (dữ liệu — S1; command — S2)

```text
snapshot(id, session_id, n, created_at, created_by, note, decision_seq, members_hash, n_members, context, context_hash)
snapshot_member(snapshot_id, ord, unit_id, source_kind ∈ {video, image_set}, source_id, provenance JSON)
```

- **Bất biến:** không có lệnh sửa / xoá; trigger SQLite chặn `UPDATE` / `DELETE` trên hai bảng.
- `members_hash = sha256("rav-collection/1
" + "
".join(sorted(unit_ids)))`; `context` = JSON đóng băng (goal, target_k, pool, reference, sample spec);
  `context_hash` = sha256 của context chuẩn hoá. Tạo lại khi **cả hai** hash bằng snapshot mới nhất → trả snapshot cũ.
- `decision_seq` = `seq` lớn nhất của log quyết định phiên lúc chốt.
- Hai câu hỏi tách riêng: **còn hiện hành không** (hai hash so với collection + context hiện tại) và **nguồn còn khả dụng không** (raw tồn tại, đúng hash —
  kiểm khi export, S8).
- **Tạo (S2):** đọc log quyết định kèm `seq` → replay → kept; ghi snapshot trong `BEGIN IMMEDIATE` và kiểm `max(seq)` chưa đổi (đổi → 409 `collection_changed`).
  Thành viên theo thứ tự pool `(video_id, t0)`. Không đọc / ghi pixel nào.
- `context` (format 1) = `{"format", "session": {goal, target_k, pool, reference}, "sample_spec": {decoder, units, spec_hash}, "app": {schema_version}}`;
  `context_hash` = sha256 JSON (khoá sắp xếp) của context bỏ khoá `app`. `reference` = `{"dataset_version_ids": []}` cho tới S6.
- `provenance` của frame video = `{"video": {id, name, size_bytes, sha256 (đủ 64 hex)}, "t0", "t1", "frame_idx",
  "decision": {seq, actor, decided_at, proposal_n}, "proposal": {recipe, config_hash} | null}` — `decision` là quyết định keep cuối cùng của mẫu.


### 14.3. Nguồn ảnh và tương thích cache (S3)

- `Context(images=...)` đọc `ImageRef` bằng PIL, EXIF transpose và RGB; ảnh không cần decoder video.
- Execution nhóm theo `Unit.source_id`, không suy container từ tiền tố `unit_id` (ảnh dùng dataset id, cache dùng version id).
- Video giữ nguyên `(video_id, spec_hash)` và `pool_hash`; ảnh dùng `(dataset_version_id, image_spec_hash)`.
- `image_spec_hash = sha256(json.dumps({"image_reader": {"name": "pil", "version": "1", "exif_transpose": true}, "units": "image"}, sort_keys=True))[:16]`.
- Thành phần cần thời gian (NMS, uniform, timeline, temporal_coverage, ngữ cảnh, telemetry) chỉ áp cho unit có `t0 is not None`.
  Các luồng phiên hiện tại vẫn dùng pool video; S3 chưa đưa ảnh vào pool phiên (Q1 đã chốt: ảnh chỉ làm tham chiếu (#65)).

### 14.4. Import dataset ảnh (S4)

#### 14.4.1. Nguồn và vị trí

- Chỉ import thư mục nằm **dưới** `RAV_DATASETS_DIR` (mặc định `data/datasets/`, gitignore). Đường dẫn gửi lên là tương đối; resolve xong phải còn nằm
  trong gốc (chặn `..`, đường dẫn tuyệt đối, symlink ra ngoài) → lỗi `invalid_folder`.
- **Không sao chép ảnh.** DB lưu `folder` tương đối + `relpath` + `sha256` từng file. File bị sửa/xoá sau import → phát hiện khi đọc / kiểm (PHASE1_DATASET_SNAPSHOT §3.6).
- `# local-only:` người dùng tự chép thư mục vào máy chạy BE; upload zip qua API để sau (thêm dòng LOCAL_LIMITS).

#### 14.4.2. Định dạng hỗ trợ

| Thành phần | Hỗ trợ | Ghi chú |
|---|---|---|
| Ảnh | `.jpg .jpeg .png .bmp .webp` (không phân biệt hoa thường), đọc bằng PIL, áp `ImageOps.exif_transpose` | PIL vì đường dẫn có dấu (AGENTS §3); quét đệ quy, bỏ file/thư mục ẩn; thứ tự `relpath` posix |
| Nhãn `none` | Không đọc gì | Mọi mẫu `label_status = 'none'`, kể cả khi có thư mục `labels/` |
| Nhãn `yolo` | YOLO **detection** txt | Bố cục: `<root>/images/<sub>/<stem>.<ext>` ↔ `<root>/labels/<sub>/<stem>.txt`; danh sách lớp từ `classes.txt` (mỗi dòng một tên) hoặc `data.yaml` (`names:` list hoặc dict chỉ số → tên) |
| `auto` | Có `labels/` **và** tìm được danh sách lớp → `yolo`; ngược lại `none` | UI hiện kết quả nhận diện để người dùng xác nhận trước khi import |
| COCO, BDD JSON, Pascal VOC, segmentation, pose | **Không** | Chuyển sang YOLO detection bằng script ngoài app, hoặc import `none` |

Đọc `data.yaml` cần `PyYAML` → thêm `requirements.txt` ở slice S4.

#### 14.4.3. Nhãn: đọc gì, giữ gì

| `label_status` | Khi nào | Lưu |
|---|---|---|
| `labeled` | File nhãn có ≥ 1 dòng, mọi dòng hợp lệ: đúng 5 số `cls cx cy w h`, `cls` nguyên trong `[0, len(classes))`, toạ độ trong `[0, 1]`, `w, h > 0` | `label_relpath`, `label_sha256`, `label_counts` (đếm box theo tên lớp) |
| `empty` | File nhãn tồn tại, rỗng | Ảnh âm tính hợp lệ — **khác** `missing` |
| `missing` | Không có file nhãn tương ứng | – |
| `invalid` | Có ít nhất một dòng sai (kể cả > 5 cột: segmentation/pose) | `label_relpath`, `label_sha256`, lý do trong báo cáo import; không dùng `label_counts` |
| `none` | Dataset `annotation_format = 'none'` | – |
| `conflict` | Ảnh trùng byte nhưng nhãn các bản khác nhau (§14.4.4) | Danh sách bản trong báo cáo import |

- MVP **chỉ đọc** lớp và số box để GĐ3 mô tả thành phần lớp của **dataset tham chiếu**. Toạ độ box không lưu (đọc lại từ file khi cần, có `label_sha256` để kiểm).
- **Giữ nguyên** file nhãn gốc (không sửa, không chuyển đổi). Không sinh nhãn cho pool.
- Nhãn của tham chiếu là dữ kiện đã có, được dùng làm bằng chứng mô tả tham chiếu; **nhãn ẩn của pool** (khi giả lập annotation) không bao giờ đi qua import này (focus §4B).
- Dataset không có nhãn vẫn dùng đầy đủ cho GĐ2–3 (embedding, pHash, khoảng cách); chỉ thiếu phần thành phần lớp → evidence ghi "tham chiếu không có nhãn".

#### 14.4.4. Thuật toán import

1. Validate input; `dataset_id` có → version mới của dataset đó; không → tạo dataset theo `name` (trùng tên → `dataset_name_taken`).
2. Quét ảnh; với mỗi file: đọc bytes → `sha256`; mở PIL (verify + kích thước sau EXIF). Không mở được → bỏ qua, ghi `{relpath, reason}`.
3. Nhãn theo §14.4.3 cho **mọi** ảnh (kể cả bản trùng), hash cả file nhãn rỗng.
4. Trùng `sha256` ảnh trong cùng thư mục: nếu mọi bản có **cùng `label_status` và cùng `label_sha256`** → giữ `relpath` nhỏ nhất, ghi bản trùng vào báo
   cáo. Nếu nhãn khác nhau → **xung đột**: không chọn theo tên file; mẫu được giữ với `label_status = 'conflict'` (không có `label_counts`), báo cáo liệt kê
   mọi `relpath` + `label_sha256` của nhóm.
5. `manifest` chuẩn hoá = JSON (khoá sắp xếp) của `{"format": 1, "annotation_format", "classes", "members": [[key, relpath, sha256, label_sha256|null] …], "conflicts": [{key, copies: [{relpath, label_relpath, label_status, label_sha256} …]} …]}`
   (members và conflicts sắp theo `key`, copies theo `relpath`); `manifest_hash = sha256(manifest)`. Manifest lưu đầy đủ trong version, không cắt ở 200 nhóm; đổi nhãn bất kỳ bản xung đột nào tạo version mới (#65).
6. `(dataset_id, manifest_hash)` đã có → trả version đó, `created = false`. Ngược lại tạo version `n = max + 1`.
7. Ghi `dataset`(nếu mới) + `dataset_version` + mọi `dataset_member` **trong một transaction**.
8. Xếp job chuẩn bị feature ở S5. S4 chỉ import manifest; từ S5 import tự xếp job khi App.auto_prepare bật.

Giới hạn: `RAV_MAX_IMPORT_IMAGES` (mặc định 50.000) → vượt thì `too_many_images`. Import đồng bộ trong request (đọc + hash; ~vài nghìn ảnh/phút trên ổ SSD) — `# local-only:`,
khi host chuyển sang job nền (LOCAL_LIMITS #3).

#### 14.4.5. Báo cáo import (`import_report`, lưu cùng version)

```json
{"n_files_seen": 1310, "n_images": 1300, "skipped": [{"relpath": "images/x.jpg", "reason": "cannot identify image"}],
 "duplicates": [{"kept": "images/a.jpg", "dropped": ["images/a_copy.jpg"]}],
 "labels": {"labeled": 1200, "empty": 60, "missing": 30, "invalid": 10, "invalid_examples": [{"relpath": "...", "reason": "class 7 ≥ 5"}]},
 "class_counts": {"pedestrian": 4210, "car": 9800}, "annotation_detected": "yolo"}
```

Danh sách `skipped`, `duplicates`, `invalid_examples` cắt ở 200 phần tử (ghi tổng số riêng).


Schema v3 có `dataset_version.manifest` (JSON đầy đủ, bất biến; #65), thêm `dataset`, `dataset_version`, `dataset_member` theo PHASE1_DATASET_SNAPSHOT §2.3; migration giữ dữ liệu + backup. Version/member có trigger chặn UPDATE/DELETE; dataset chỉ cho đổi name/description.

Command S4: `list_import_folders`, `import_dataset`, `list_datasets`, `get_dataset_version`, `list_dataset_samples`, `get_image` theo bảng API ở architecture. CLI `import-dataset` gọi cùng command. `get_image` đọc nguồn bằng PIL/EXIF, không sinh ảnh lưu trữ; file thiếu → `dataset_file_missing` 404.

Ảnh raw đã đổi hash → `get_image` báo `dataset_file_changed` (409), không trả pixel của nguồn mới như thể thuộc version cũ.

### 14.5. Chuẩn bị và kiểm nguồn dataset (S5)

- `queue_prepare_dataset(version_id)` → DatasetVersionDTO; một worker của App xử lý lần lượt video/dataset, trạng thái job trong RAM.
  Import ở API (`auto_prepare=true`) tự xếp job, không chờ. CLI/test mặc định có thể tắt tự prepare.
- Dataset context dựng unit ảnh từ dataset_member, thứ tự order_key. Tính `emb.dinov2_s`, `hash.phash` qua registry/Context,
  cache theo version + image_spec_hash; `prepared` suy từ cache đủ ID, không lưu cờ trong DB.
- `verify_dataset_version(version_id)` → `{ok,missing: string[],changed: string[]}`: kiểm size/hash nguồn ảnh và nhãn trong manifest
  (gồm mọi bản conflict, kể cả nhãn rỗng); nhãn missing nay xuất hiện → changed. Danh sách lớp đã đóng băng, không kiểm lại classes.txt/data.yaml.
- Verify trước prepare (kể cả cache hit); nguồn hỏng → job error, không sửa manifest, không dùng nguồn thay thế.
  File thiếu khi đọc pixel → lỗi dataset_file_missing; chuẩn bị lỗi hiển thị mã lỗi qua prepare.error.

### 14.6. Dataset tham chiếu trong phiên (S6)

- `CreateSessionIn.reference = {dataset_version_ids: []}` (mặc định); session spec lưu cố định lúc tạo.
- Kiểm version tồn tại (404 dataset_version_not_found), mỗi dataset tối đa một version (422 duplicate_dataset_in_reference),
  đã chuẩn bị và không đang chạy job (409 reference_not_prepared). Pool chỉ nhận video (#65), không nhận image IDs.
- SessionDTO thêm reference và `latest_snapshot?: {id,n,is_current}`. Phiên cũ thiếu reference đọc thành [] và không ghi lại spec cũ.
- Reference không tham gia recipe cũ của GĐ1. Từ V2 (§18), UI ghi dataset train cố định, dùng trong các recipe bổ sung dữ liệu; không tự đổi recipe phiên cũ.
  Snapshot context đóng băng reference IDs; thông tin lớp của dataset version không bị thay khi raw/classes đổi.

Khởi động API khởi tạo DB và chạy migration trước khi đăng ký route/nhận request; không để các request đầu chạy migration song song. CLI tiếp tục khởi tạo DB khi dùng service. Đây là bảo đảm vận hành cho một tiến trình local, không thay schema hay hợp đồng command.

### 14.7. Export snapshot (S8, triển khai theo PHASE1_DATASET_SNAPSHOT §12)

- Command `export_snapshot(snapshot_id)` → `{path,filename}`; API GET `/snapshots/{snapshot_id}/download` trả ZIP tạo theo yêu cầu.
  Dữ liệu lấy trực tiếp từ snapshot/member/context đã đóng băng; kept hiện tại không quyết định mẫu xuất.
- Kiểm mọi nguồn liên quan trước khi tạo ZIP: hash SHA-256 đầy đủ, ảnh/nhãn theo manifest; thiếu/đổi trả 409
  `source_unavailable` với `error.sources: [{source_id,kind,problem: missing|changed,n_samples}]`. Không trả ZIP thiếu mẫu.
- Video đọc theo decoder đóng băng bằng `read_exact`: chỉ trả khi chỉ số decode bằng frame_idx; khác → 409 frame_mismatch.
  Ảnh: đọc bytes gốc, hash ảnh và nhãn liên quan. Pool vẫn chỉ video; không mở phạm vi pool ảnh.
- ZIP có `images/`, `manifest.json` (snapshot/context/members/provenance) và `manifest.csv`; video encode PNG không mất mát.
  Report cùng snapshot chưa tồn tại ở GĐ1; sẽ bổ sung ở GĐ3. Export kept cũ vẫn giữ nguyên để tương thích.
- File `output/tmp/snapshot-<uuid>.zip`: ghi tuần tự, giới hạn kích thước ZIP thực tế trong mọi lần write qua
  `RAV_MAX_SNAPSHOT_ZIP_MB` (mặc định 256); vượt → 413 export_too_large. Lỗi bất kỳ xoá ZIP dở.
  API xoá file trong finally sau gửi, kể cả send bị huỷ; khởi động quét đúng tên file tạm snapshot của tiến trình local.

S8 dùng thêm `verify_snapshot(snapshot_id)` → `{ok:true}` hoặc source_unavailable, đúng command kiểm nguồn riêng đã nêu trong PHASE1 §5. API POST `/snapshots/{snapshot_id}/verify`. FE kiểm nguồn rồi tải trực tiếp qua GET download; không giữ toàn ZIP trong RAM của FE. Lỗi tạo/truyền ZIP sau bước verify thuộc đường tải HTTP của trình duyệt.

S8 `read_exact` đếm chỉ số frame theo thứ tự giải mã thật, không suy index từ timestamp × fps (video có thể VFR). Handle bắt đầu từ seek(0), đọc tiến một lần qua các index tăng; lùi index hoặc UI dùng lại handle thì bắt đầu đếm lại. ID/spec/cache feature cũ không đổi; chỉ đường export snapshot dùng cách đọc strict.

## 15. CLIP và recipe tìm theo câu (S9–S10, #68)

Transport S3 và trạng thái job bền vững được mở rộng ở §19 (#75); transport PNG ZIP dưới đây chỉ còn dùng khi storage backend `local` được chọn rõ.

- Feature `emb.clip_b32`: `clip_b32@1`, per_sample, vector float32 (N,512), L2; checkpoint `openai/clip-vit-base-patch32`, revision `3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268`. Params chứa model/revision; preprocessing và tokenizer của revision đó. Text encode cùng checkpoint local, chưa quantize, giới hạn 77 token, không âm thầm cắt câu.
- `text_match@1`: params `field=emb.clip_b32`, `texts: list[str]` (ít nhất một câu không rỗng), `negative: list[str]=[]`, `min_similarity=0.2` trong [-1,1], `negative_margin=0` trong [0,2]. OR câu tìm: p=max cosine ảnh với texts; q=max cosine negative nếu có. Score=clip((p+1)/2,0,1); mask=(p>=min_similarity) AND (p>q+negative_margin nếu có negative). Ngưỡng là heuristic chưa hiệu chỉnh, không phải xác suất/khẳng định có đối tượng; vẫn phải human review. Không tự hạ ngưỡng để đủ batch.
- Text nằm trong config proposal, không sửa goal phiên; `uses_goal=False`. `semantic_query` của explain trả texts/negative/min_similarity/negative_margin từ config đã lưu. LLM chuyển goal thành structure (#63) vẫn Hướng tới; UI không tự dịch tiếng Việt. Gợi ý câu ngắn tiếng Anh; tokenizer báo lỗi câu quá dài.
- Recipe `text_diversity` = session_kept + text_match × novelty(DINOv2) + greedy_nms(DINOv2, relax=False). `diversity` cũ giữ mặc định và cấu hình. greedy_nms@3 thêm Params `relax=True` (giữ hành vi cũ); False chỉ chạy ngưỡng gốc, trả ít/rỗng, không nới tới 0. Baseline không hiện FE. Dataset reference chưa tham gia novelty (GĐ2 tiếp theo).
- Chuẩn bị CLIP độc lập P1 cũ: VideoDTO thêm `clip_prepared: bool`, `clip_prepare?: PrepareStatus`. Command `queue_prepare_clip(video_id)` → VideoDTO, POST `/videos/{video_id}/prepare-clip`. Job chạy cùng worker P1 một hàng đợi. Cache đủ đúng IDs → không tạo job. Propose text_match chỉ đọc cache ảnh; thiếu → 409 `clip_not_prepared`, không chạy image model trong request; đang job không ngăn recipe diversity cũ.
- `RAV_CLIP_IMAGE_BACKEND=kaggle` mặc định; `local` chỉ dùng khi cấu hình rõ cho smoke/offline, không fallback khi Kaggle lỗi. `RAV_KAGGLE_KERNEL` tuỳ chọn và `KAGGLE_API_TOKEN` đọc trực tiếp `.env` repo (người dùng yêu cầu); không fallback auth env/toàn máy. Accelerator t4, timeout và max payload cấu hình. Thiếu token → job error với hướng dẫn, không lộ token/URL. Chỉ stop session của job, không đụng session đang chạy sẵn.
- Adapter giới hạn payload trước upload, chỉ sample thiếu cache, file raw ảnh hoặc PNG tạm frame đọc strict; manifest chứa FieldKey, source SHA256, extraction, model/revision, ordered IDs, dim=512/dtype=float32/L2. Remote kiểm PNG SHA; local kiểm manifest + IDs đủ/duy nhất/đúng thứ tự + shape/finite/L2 trước ghi. Không commit cache nếu lỗi; cache công bố atomic. Quét lại SHA raw trước ghi để không nhận kết quả từ nguồn đã đổi. Cleanup payload/result và stop session trong finally, kể cả timeout. Một session/job trong worker, không retry vô hạn.
- Context thêm `pixels_exact(Frame|ImageRef)` cho feature CLIP local và adapter để cả hai đọc cùng chỉ số thật; `pixels` cũ và cache DINO không đổi. Adapter dùng SDK Kaggle chính thức + Jupyter protocol trong repo; `kaggle_extend_api` chỉ tham khảo, không runtime dependency. Token và slug chỉ đọc `.env` repo; chưa có slug không tạo job trên notebook của người khác.
- Người dùng cho phép tự tạo notebook khi đã có credential: `RAV_KAGGLE_KERNEL` là override tuỳ chọn. Thiếu override → introspect đúng token `.env`, tạo notebook private riêng có tên ngẫu nhiên, quick-save không chạy cell, không gắn dataset. Lưu slug + kernel ID + account vào `output/clip-worker.json`, không lưu token/URL session; tái sử dụng chỉ khi account khớp token hiện tại. Tạo/permission lỗi báo rõ; không mượn notebook từ package. Session từng job vẫn mới và dừng trong finally.

## 16. Nạp checkpoint YOLO tham chiếu (M1, #70)

- Lát M1 theo yêu cầu nạp model: upload/list/get/verify checkpoint và chọn model với dataset train trong phiên. Inference/prediction theo đợt ở M2 (§17); sử dụng trong tiêu chí curation ở V2 (§18). Không tính YOLO hoặc thêm teacher ở bước upload.
- Nhận file `.pt` định dạng PyTorch ZIP (có data.pkl và version), không rỗng, giới hạn `RAV_MAX_MODEL_MB` mặc định 500; giới hạn cả tổng kích thước entry giải nén, không extract/unpickle/import model. Kiểm container không chứng minh đây là model YOLO detection hợp lệ; DTO/UI luôn ghi chưa kiểm khả năng nạp/inference. Format khác hoặc legacy pickle chưa hỗ trợ, trả 422 rõ ràng.
- Checkpoint upload được sao chép vào kho quản lý `RAV_MODELS_DIR` (mặc định data/models), tên file do hệ thống sinh từ SHA256 đầy đủ. Model ID = `ym-<sha256>`, metadata tên/filename/sha256/size/format/created_at bất biến; cùng byte trả model cũ, không đổi tên hay metadata. Đường dẫn người dùng chỉ làm tên hiển thị đã bỏ phần thư mục, không dùng làm đường dẫn đích. Upload dở/lỗi dọn file tạm; chỉ công bố DB sau file hoàn chỉnh; lỗi insert dọn file vừa tạo. Một API/output local, lock upload cùng App.
- Schema v4 thêm model_checkpoint và trigger chặn UPDATE/DELETE; migration backup, giữ mọi bảng/dòng cũ. Không có lệnh xoá hay thay byte checkpoint. File raw bị sửa/thiếu: verify hoặc chọn cho phiên trả 409 model_file_changed/model_file_missing; không nhận byte mới như model cũ.
- Command `upload_reference_model(filename,path,name?)` → `{model: ModelDTO,created:bool}`; `list_reference_models` → ModelDTO[]; `get_reference_model(model_id)` → ModelDTO; `verify_reference_model(model_id)` → `{ok:true}` hoặc lỗi. REST POST `/models` multipart file/name, GET `/models`, GET `/models/{model_id}`, POST `/models/{model_id}/verify`. ModelDTO không có đường dẫn host, có `available` (file hiện diện và đúng size; chưa thay cho kiểm SHA), `validation="container_only"`, `inference_ready=false`.
- DatasetReference thêm `model_id?:str|null`; bỏ/None vẫn tương thích phiên cũ, không ghi model_id:null vào spec cũ. Nếu chọn model: phải tồn tại, verify SHA và có ≥1 dataset_version_ids đã prepare, đây là khai báo người dùng rằng các version đó dùng train/finetune checkpoint. Không tự suy quan hệ từ bytes/nhãn. Reference được đóng băng lúc tạo phiên và đi vào snapshot.context; không sửa reference phiên cũ. SessionDTO thêm reference_model metadata hoặc null để FE hiển thị model đang chọn, chưa tham gia đề xuất.
- Kho dữ liệu có tab Mô hình tham chiếu: nút upload, tên, dung lượng, SHA, thời điểm, trạng thái lưu/check file; lỗi rõ và retry. Phiên mới chọn model tuỳ chọn (giữ phiên CLIP/diversity cũ), chọn dataset train tương ứng; nút tạo chặn khi chọn model mà chưa chọn dataset. Studio hiện metadata model tham chiếu phiên; trạng thái inference theo đợt ở §17. Không tự chọn model hoặc dataset cho người dùng khi tạo phiên.

## 17. Chạy YOLO cho đợt đang xem (M2, #71)

- Server resolve đúng ordered picks của proposal n từ DB; không nhận unit_ids/pool/path từ client, không infer toàn video/dataset. Chỉ đọc anchor pixels_exact đúng frame_idx; đợt rỗng hoặc >256 ảnh trả 422 (giới hạn evidence), có hướng dẫn giảm batch_size. Không chạy P1/CLIP/scoring, không đổi picks/state/reference/snapshot.
- Commands `run_batch_yolo(session_id,n,model_id)` → YoloBatchDTO, `get_batch_yolo(session_id,n,model_id)` → YoloBatchDTO|null (chỉ đọc artifact). POST/GET `/sessions/{sid}/proposals/{n}/yolo`, model_id trong body/query. Validate session/proposal/model; SHA đầy đủ checkpoint và nguồn liên quan trước cache/run, kiểm lại sau run; đổi nguồn/model không công bố kết quả mới. Lỗi rõ, không lộ host path hoặc exception thư viện.
- Command → service/batch_yolo (resolve/integrity/cache) → pipeline/batch_yolo (Ultralytics 8.3.228 pin). Chỉ checkpoint detection; reject classify/seg/pose/obb trước đọc ảnh. CPU local, một model/request chưa cache, predict lần lượt từng ảnh. Params cố định: imgsz=640, conf=0.25, iou=0.7, max_det=300, device=cpu. Detection: label, score, box xyxy chuẩn hoá 0–1, source=`yolo:<model_id>`; kiểm shape/finite/range/box. [] = chạy nhưng không có box; null = chưa chạy. Custom .pt phụ thuộc lớp/phiên bản Ultralytics; lỗi nạp không ảnh hưởng upload catalog.
- YoloBatchDTO: session_id, proposal_n, model_id, model_name, model_sha256, engine_version, params, created_at, total_images, items[{unit_id,detections}], cached. JSON lưu atomic trong output/yolo_batches; key gồm session/n, ordered IDs, sample spec hash, SHA nguồn/checkpoint, version engine/implementation và params. Cùng input dùng cache sau restart; lỗi/hủy không lưu partial. Không lưu raw ảnh/ground truth, không chèn FieldStore hoặc dùng vào scoring.
- Một App có lock YOLO: tối đa một request inference; request khác trả 409 yolo_busy, không xếp hàng model song song trên máy RAM thấp. Command chạy threadpool; FE vẫn giữ/drop/đổi đợt được. Đổi đợt/model xoá box cũ ngay, completion chỉ gắn nếu khớp context hiện tại. GET/mở trang/propose không tự infer.
- Studio có selector model (mặc định reference model nếu có, còn lại để trống), link upload, `Chạy YOLO · N ảnh`, đang chạy/lỗi/retry/kết quả cache. Không cần dataset để xem thử prediction, không sửa khai báo model–dataset train của phiên. Chặn chạy khi thiếu model/ảnh/đang chạy. Xong bật M; grid có số đối tượng kể cả 0, ảnh gốc chi tiết hiện nhãn/confidence/source. M vẫn bật/tắt box.
- ModelDTO M1 vẫn container_only/inference_ready=false: upload không chứng nhận model cho mọi input. Trạng thái inference theo đợt ở YoloBatchDTO; UI kho model/metadata phiên không khẳng định chưa từng chạy sau M2. Confidence không là metric accuracy/AP/mAP hoặc lỗi trên pool chưa nhãn. Model/dataset scoring còn Hướng tới; phần inference/evidence ở §14.0 và §16 chuyển sang M2 cho các picks.
- Box được đặt theo tỷ lệ ảnh gốc (object-contain khi bật overlay), không kéo/crop như ảnh 16:9 với video tỷ lệ khác. Frame endpoint dùng read_exact cùng index với inference, kể cả VFR; không overlay box anchor lên clip đang phát.

## 18. Proposal hai bước và recipe bổ sung dữ liệu (V2, #72)

- Context giữ `units` là pool video, thêm `reference_sets: dict[str,list[Unit]]` (training), `feature_configs`, `model_files`, `output_dir`, `evidence` per-unit. Service nạp manifest/version đã verify, component không đọc DB/HTTP. `ctx.field` dùng chung FieldKey trên pool/reference; execution tìm mọi unit của nguồn trong cả hai khi source-cache thiếu. Reference không trở thành candidates, không đổi n_units/state. Thiếu dataset/model recipe cần → lỗi rõ; không tự sửa phiên cũ.
- RecipeConfig thêm features={} và candidate_stage=None, cũ giữ hash/hành vi nếu không dùng. Binding field → feature type/Params kiểm provides/producer/version/params_hash; `$reference_model` trong Params.model_id resolve ID bất biến trước hash/chạy, managed path chỉ qua Context. Feature.Params chỉ chứa thứ đổi kết quả, Context kiểm dependency per_sample trên producer đã bind. Field/model config đầy đủ đóng băng trong proposal.
- Feature cache_scope source|requested (mặc định source, giữ per_sample). Requested chỉ cho per_sample=True: compute đúng ordered units được hỏi; partition riêng gồm IDs/spec/SHA raw, subset không giả nguồn prepared. Kiểm nguồn trước/sau, không publish partial. Source DINO/CLIP cũ giữ nguyên. Structured feature có detection/scalar diagnostics; không cache state/reference/score như feature.
- CandidateStage gồm objectives/combiner/selector/limit 1–256, dùng chung reference/bindings. Cheap score trên pool−kept−dropped, selector lấy limit; final objectives chỉ chấm tập đó. Giữ interface component, không if theo recipe trong service. Requested fields tính trong job bằng Context/execution. Recipe không stage vẫn POST propose đồng bộ; có stage gọi endpoint cũ trả 409 proposal_requires_job.
- Commands queue_proposal(session_id,recipe,config?,batch_size,seed?) → JobDTO và list_proposal_jobs(session_id) → JobDTO[]. POST/GET /sessions/{sid}/proposal-jobs. Job id/session/state queued|running|done|error, stage/done/total/error/proposal_n; atomic JSON output/proposal_jobs, không migration. Một prepare worker local, dedup active mỗi phiên. Freeze state/pool/reference/config/seed/proposal_count tại queue; publish dưới App RLock chỉ nếu state/pool/proposal_count chưa đổi, nếu đổi → proposal_context_changed không lưu proposal cũ. Propose đồng bộ/update_state/add_videos dùng cùng lock. Restart queued/running → interrupted, không tự chạy lại; cache hoàn chỉnh tái sử dụng.
- Proposal input optional execution={candidate_ids,reference_version_ids,reference_manifest_hashes,model_ids,source_hashes}; evidence per-pick lưu cùng record (finite JSON). return_scores replay candidate IDs/bindings/reference đúng snapshot. Evidence primitives nearest (source/version/key/similarity/field/image_url), scalar (label/value/method/observed), detections (label/boxes/source). FE theo kind, không theo recipe, unsupported kind ẩn; explain không infer lại. Dynamic selection_gain là scalar riêng, combined/breakdown giữ điểm objective.
- reference_diversity: training+kept, reference_novelty DINO `(1-max cos)/2` + nearest train, explore_diversity. model_guided: candidate_stage 128 bằng novelty/explore_diversity; binding det.yolo_consistency model phiên; final reference_novelty .6 + detection_instability .4, weighted_mean, explore_diversity. text_model_guided thêm text_match ở candidate stage, query explicit chưa LLM goal.
- YOLO consistency CPU: original+horizontal flip, map box về original; one-to-one greedy IoU matching không điều kiện class. Instability=1−mean(IoU×class-agreement) gồm unmatched với agreement=0. Cả hai 0 box → observed=False, instability=0, không chứng minh chắc chắn. Same Params M2, engine/transform/matching pin trong version/params; model load một lần, lock chung M2. Tối đa 2×candidate_limit input, không infer cả pool. Không teacher, không dùng nhãn train làm lỗi pool, confidence không error probability.
- explore_diversity: 75% quota ưu tiên combined, gain giảm khi gần ảnh vừa chọn; phần còn lại random seed từ candidates hợp lệ. Tất cả NMS thời gian/min_dist; không nới để đủ N. Quota/weights heuristic config chưa uplift. Novelty train là điểm mềm, không loại hết ảnh giống train. Nearest chỉ dùng cùng FieldKey/dimension, sai → lỗi.
- FE chọn recipe mới, job progress/error/retry/reload; chuyển đợt khi done đúng sid. Review vẫn được, context đổi cần thử lại. Chi tiết ảnh render nearest/scalar/detections có nguồn/method; prediction không gọi là nhãn. Recipe reference cần dataset; guided cần model phiên. Phiên cũ giữ recipe, muốn reference khác tạo phiên mới.
- Verification: hash/recipe cũ, source/requested/changed raw, runtime/model isolation, ref không lọt pool, bounded inference, match/unmatched/empty, weighted/exploration seed, job dedup/restart/context/error/replay, FE evidence. Không claim mAP/AL/ODD/cost trước benchmark độc lập.

V2 lưu evidence trong key riêng `_evidence` của JSON snapshot proposal hiện có, tách ra khi đọc thành DTO; không đổi schema SQLite hay input công khai. Requested partition kiểm cả SHA checkpoint đã resolve trước đọc cache và trước/sau compute. Params feature YOLO đóng băng inference Params M2; cấu hình khác không hỗ trợ. CLIP image trong proposal job cũng chỉ đọc cache đã prepare (§15), thiếu cache báo cần chuẩn bị, không tự chạy CPU/Kaggle.

Command `get_proposal(session_id,n,return_scores=False)` → ProposalDTO, GET `/sessions/{sid}/proposals/{n}`. Đọc một đợt đã lưu, tuỳ chọn replay điểm từ snapshot/cache; thiếu requested cache báo lỗi, tuyệt đối không chạy YOLO. FE dùng đường đọc này cho điểm của đợt nền; query CLIP trong explain lấy cả candidate-stage config đã đóng băng.

Replay kiểm config_hash và mọi FieldKey đã lưu trước đọc điểm; producer/version/defaults đổi thì trả 409 proposal_config_changed. Thiếu/không hợp lệ cache cần đọc trả 409 proposal_cache_invalid với hướng dẫn, không âm thầm thay checkpoint/feature hoặc infer lại. Prediction của đợt đã lưu trên lưới có nhãn riêng với nút chạy model xem thử.

RecipeDTO.config điền mặc định Params của từng component, cả candidate stage/binding, để UI hiển thị đúng query/ngưỡng đang dùng; hash recipe cũ giữ nguyên vì vốn resolve mặc định. §18 nối phần scoring Hướng tới ở §14–17 cho ba recipe mới; recipe cũ không sử dụng dataset/model reference.

Theo yêu cầu dọn ổ C (#73), CLIP local lưu checkpoint/tokenizer tại `RAV_CLIP_CACHE_DIR` (mặc định `data/model-cache/huggingface/hub`, tương đối repo). Nạp `pytorch_model.bin` đúng revision đã pin với `use_safetensors=False`, tránh auto-conversion tải thêm trọng số từ revision khác. Đường cache/định dạng container không đổi FieldKey hay embedding; dữ liệu này gitignore. Không thay cache model toàn máy khác hoặc DINO đang dùng.

## 19. S3 raw → Kaggle → S3 embedding, Supabase job (#75)

§20 thay luồng video tự prepare: upload chỉ lưu, DINO/CLIP chạy khi người dùng yêu cầu.

Command `get_cloud_status` → GET `/api/v1/cloud/status` trả storage_backend/job_backend, s3_ready/jobs_ready và safe error; không key hoặc signed URL. PrepareStatus thêm job_id/result_key nullable (key kết quả chỉ khi cache đã publish), UI cũ tương thích.

- `RAV_STORAGE_BACKEND=local|s3`, default local để tương thích; chọn s3 yêu cầu credential AWS từ `.env` repo, `RAV_S3_BUCKET`, region. Không fallback auth máy hoặc fallback local khi cloud lỗi. Boto3 multipart có giới hạn concurrency; SHA256 đầy đủ và kích thước trước/sau upload, object metadata giữ SHA. Raw lưu lâu dài dưới videos/, datasets/, models/ bằng key theo hash; temp/ chỉ file có thể tạo lại. Upload video/model và import dataset phải hoàn tất lưu S3 trước trả thành công. Bản local trên ổ repo là bản làm việc phục vụ decoder/FE hiện tại, không bị tự xoá; chưa chuyển toàn bộ catalog/session/feedback SQLite sang cloud.
- CLIP s3 transport gửi manifest nhỏ, tải video gốc một lần trên Kaggle, decode PyAV theo **index thật** từ đầu (VFR cũng vậy); ảnh tham chiếu dùng raw + PIL EXIF/RGB. Không ZIP toàn bộ PNG, không lấy timestamp×fps làm index, không đổi unit ID/spec/FieldKey/checkpoint/preprocessing. Chỉ giữ batch nhỏ trong RAM. Manifest pin PyAV, FFmpeg library versions, Pillow và hashes RGB của các frame kiểm chứng đầu/giữa/cuối; remote khác pixel/runtime thì lỗi, không publish cache. Mỗi nguồn kiểm SHA256 đầy đủ sau tải.
- Backend chỉ cấp presigned HTTPS GET cho input và PUT cho đúng result/progress của một job; không gửi AWS/Supabase secret tới Kaggle. Kaggle stream tải/đẩy file; progress JSON có job_id, manifest_hash, sequence, stage, done, total, state, result_sha256 khi hoàn tất. URL/credential không nằm trong job DB, DTO, lỗi/log/history. TTL bao phủ deadline job có giới hạn; restart không tự gia hạn worker đang chạy.
- Kaggle worker chạy process nền riêng trong **session do job tạo**, giữ session_id + operation_name để backend phục hồi. Ghi result.npz lên `results/clip/{job_id}/` trước báo hoàn tất; xoá thư mục raw tải về, config chứa URL và result local trong finally, rồi báo cleanup_done. Backend chỉ nhận done khi cleanup_done=true; kiểm job_id/manifest/IDs/shape float32 (N,512)/finite/L2/SHA và kiểm lại raw local trước khi công bố atomic FieldStore. Không tự xoá raw trên S3. Stop session sau thu hồi kết quả hoặc lỗi/timeout; stop lỗi được ghi bền vững để phục hồi dọn session, không báo hoàn tất giả.
- `RAV_JOB_BACKEND=local|supabase`, local lưu JSON atomic trên ổ repo, supabase dùng bảng `rav_embedding_jobs` qua HTTPS 443 với `SUPABASE_URL`/`SUPABASE_SECRET_KEY` **backend-only**. SQL cung cấp RLS (không quyền anon/authenticated), unique job ID và active input key, RPC lease/CAS cho job worker. DB lưu manifest và trạng thái, result/progress object keys, session ownership, timestamps/lease; không chứa signed URL hoặc raw/embedding bytes. SQLite nghiệp vụ cũ giữ nguyên. Thiếu cấu hình/bảng Supabase: báo lỗi có hướng dẫn, không silently đổi backend.
- Progress Kaggle đi HTTPS PUT S3; backend poll đọc đúng job/manifest/sequence rồi persist Supabase và cập nhật `VideoDTO.clip_prepare` đang có. Worker backend khởi động lại tự lấy job queued/running, claim lease, attach đúng session/operation, nhận progress/result; không tạo GPU thứ hai cho job đã launch. Pending result có thể recover dù backend từng offline. Nếu remote bị mất/URL hết hạn/deadline quá, job error rõ, retry explicit tạo attempt/job mới; không claim checkpoint vector từng batch hoặc tự resume số frame đã tính sau mất GPU.
- Test: multipart/hash/dedup + cloud error không success, video và ảnh/VFR pixel parity, manifest/result corruption, no secret exposure, progress validation/monotonicity, restart cùng session không chạy trùng, lease, timeout/cleanup/stop retry, Supabase contract/RLS, cache publication và regression luồng local. Live smoke S3 + GPU + Supabase khi đủ cấu hình; báo rõ nếu phần live chưa chạy.

## 20. Video lưu trước, DINO/CLIP chạy theo yêu cầu (C2, #76)

- Upload video chỉ validate file, lưu raw S3 và catalog; không xếp P1/DINO/CLIP, kể cả auto_prepare=true. GET/list/mở trang không tạo job. Dataset import §14.5 giữ hành vi riêng.
- Hai hành động explicit: POST /videos/{id}/prepare cho DINO + pHash, POST /videos/{id}/prepare-clip cho CLIP. Khi storage=s3, cả hai dùng Kaggle GPU qua transport §19, không fallback CPU. Command prepare_video đồng bộ trả prepare_requires_job trên S3 để tránh bypass GPU; local backend được chọn rõ vẫn hỗ trợ prepare local/CLI.
- VideoDTO.prepared/prepare là readiness/job DINO+pHash; clip_prepared/clip_prepare độc lập. Readiness suy từ cache, giữ IDs/spec/FieldKey cũ; đủ cache không tạo job. Job payload thêm feature=dino|clip; job cũ thiếu feature đọc là clip. Input key/active dedup, status/recovery tách feature, không đổi schema Supabase.
- DINO dùng đúng nguồn Torch Hub và checkpoint ViT-S/14 hiện có: đóng gói nguồn Python + LICENSE, SHA256 nguồn/trọng số, lưu models/ S3, cấp GET scoped. Chỉ chuẩn bị assets lúc chạy job explicit. Thiếu cache nguồn/trọng số báo lỗi cấu hình, không âm thầm đổi model. Manifest pin OpenCV/Pillow/ImageHash/scipy, image_size; remote resize INTER_AREA và mean/std ImageNet float32, trung bình vector frame trước L2, vector (N,384). pHash uint64 của anchor được trả cùng NPZ. PCA tính nhẹ từ embedding đã cache khi cần, không chạy encoder CPU.
- Worker kiểm SHA/model assets/pixel parity, trả đầy đủ DINO+pHash cùng ordered IDs; BE validate cả hai trước publication, không công bố readiness khi thiếu một field. Result DINO dưới results/dino/{job_id}/; CLIP giữ results/clip/. Raw/assets tải về và result/config được dọn trên Kaggle; raw/model S3 giữ lâu dài. Lease/recovery/progress/cleanup/secret isolation theo §19.
- FE hiện riêng chưa chạy/đang chờ/đang chạy/sẵn sàng/lỗi cho từng feature, stage + done/total, nút chạy/retry riêng và chặn double click. Upload không tự chạy. Kho video và Phiên mới thấy mọi video; chỉ chọn vào phiên khi DINO+pHash sẵn, recipe CLIP yêu cầu thêm CLIP. Cache cũ vẫn dùng, không tự chạy lại.

## 21. Hợp đồng deployment pilot — đã khóa tại DEP0 (#84)

> ⚠️ **Mục này là thiết kế đã khóa cho deployment pilot (2–3 người dùng).**
> Quy chuẩn tại §1–20 tiếp tục áp dụng nguyên vẹn cho core logic và local v1.
> Thiết kế dưới đây là Source of Truth cho các lát triển khai DEP1–DEP8.

### 21.1 Context người dùng (`ActorContext`) và command layer
- Dataclass `ActorContext(user_id: UUID | None, request_id: UUID)` đóng băng (frozen).
  - Runtime deploy pilot bắt buộc `user_id` hợp lệ được xác thực từ Supabase Auth bearer token. Chuỗi định danh actor: `human:<user_id>`.
  - Runtime local v1 dùng `user_id=None`. Chuỗi định danh actor: `human:local`.
- Chữ ký chuẩn của mọi command: `Command.run(inp, services, actor: ActorContext) -> Output`.
- Body và query của HTTP request cấm chứa `owner_id`, `actor`, `admin` hoặc quyền tự khai (`extra="forbid"`). Toàn bộ quyền sở hữu được suy trực tiếp từ `actor.user_id` đã xác thực.
- Profile người dùng phải ở trạng thái `active=true`. Nếu bị vô hiệu hóa (`active=false`), API trả về 401 `account_disabled`.

### 21.2 Định danh tài nguyên (UUID ↔ Core Identity)
- API v2 giao tiếp với client bằng UUID cho mọi tài nguyên (`source_id`, `session_id`, `snapshot_id`, `template_id`, `job_id`, `export_id`).
- Tầng Core (`Unit`, `Frame`, `ImageRef`, `FieldKey`, `spec_hash`, `pool_hash`, ordered picks) giữ nguyên 100% ngữ nghĩa và kiểu dữ liệu baseline. UUID nằm ở tầng ngoài làm namespace sở hữu, tuyệt đối không chèn UUID vào thuật toán tính hash cốt lõi.
- Một pool không chấp nhận hai tài nguyên có cùng `core_source_id` (trả về 422 `duplicate_core_source_in_pool`).

### 21.3 Cơ sở dữ liệu PostgreSQL `rav_pilot_*`, RLS và tính bất biến
- Tên bảng dùng prefix `rav_pilot_`.
- Toàn bộ Foreign Keys nghiệp vụ dùng `ON DELETE RESTRICT`, không sử dụng `CASCADE`.
- Triggers chặn tuyệt đối `UPDATE` và `DELETE` (chỉ cho phép `INSERT`) trên các bảng dữ liệu bất biến:
  `rav_pilot_dataset_version`, `rav_pilot_dataset_member`, `rav_pilot_model_asset`, `rav_pilot_model_dataset_declaration`,
  `rav_pilot_proposal`, `rav_pilot_decision`, `rav_pilot_snapshot`, `rav_pilot_snapshot_member`, `rav_pilot_demo_template_event`, `rav_pilot_usage_receipt`.
- Row Level Security (RLS) bật trên mọi bảng. Kết nối API dùng direct TLS PostgreSQL với vai trò `authenticated`:
  `SET LOCAL ROLE authenticated; SELECT set_config('request.jwt.claims', $1, true);`
  Chính sách RLS kiểm tra: tài nguyên PRIVATE yêu cầu `owner_id = auth.uid()`; tài nguyên SYSTEM cho phép đọc chung cho các tài khoản đang active.
- Thứ tự khóa transaction bắt buộc khi ghi để chống deadlock:
  `profile` → `sources` (sắp xếp tăng dần theo UUID) → `logical resource` → `admission/quota/accounts` → `job` → `slots`.
- Theo #110, mỗi `PilotDB` dùng pool Psycopg thread-safe, mở lười, tối đa 4 kết nối; chờ checkout tối đa 10 giây.
  Mỗi checkout giữ nguyên một transaction tường minh: commit khi thành công, rollback khi lỗi;
  `SET LOCAL ROLE`/claims không tồn tại ngoài transaction và không truyền từ tài khoản A sang B hoặc backend.
  Không tự retry transaction ghi khi mất kết nối; kết nối hỏng bị loại khỏi pool. Pool đóng khi instance/process kết thúc.
  API/worker vẫn dùng URL TLS và role riêng; không chia pool giữa hai role hoặc các process.
- Theo #116, các nhóm ghi tất toán quota/storage và đóng attempt được gửi bằng Psycopg pipeline
  trong transaction hiện có, giữ nguyên thứ tự SQL/khóa. Chỉ gom lệnh không cần đọc kết quả/rowcount
  hoặc xử lý lỗi giữa chừng; thoát pipeline phải sync và ném lỗi trước khi caller chạy tiếp.
  Không bao toàn transaction/savepoint bằng pipeline, không retry ghi hoặc đổi idempotency/RLS.
  Log `db.pipeline` đo cả gửi và sync; `db.enqueue` chỉ đo đưa lệnh vào pipeline,
  không được hiểu là thời gian PostgreSQL thực thi. `db.execute` giữ nghĩa đồng bộ ngoài pipeline.

### 21.4 Giao diện REST API v2 và quy ước lỗi
- Theo #111, đo latency upload bằng JSON log `rav_timing` (UTC + monotonic wall ms;
  CPU process ms chỉ để chẩn đoán, không thay receipt quota). Correlation là request_id,
  upload_id, job_id, attempt_id; DB đo pool checkout, transaction và từng execute không ghi SQL/params.
  API đo request và S3 HEAD; worker đo download+hash streaming, probe/hash core, unit grid,
  dedup/receipt, canonical PUT+HEAD, handler, publish và queue (timestamp DB).
  Trình duyệt ghi Console create, S3 transfer (byte/s), complete, READY wait, tổng upload;
  không gửi telemetry browser về server, không thêm bảng/endpoint/dependency.
  Span ghi cả lỗi bằng mã/loại lỗi, không message/payload/path/key/URL/credential/tên file.
  Các span có thể lồng nhau; không cộng thời gian lồng hoặc CPU nhiều thread như các chặng độc lập.
- Toàn bộ endpoint deploy sử dụng prefix `/api/v2/`.
- Mã lỗi chuẩn:
  - `401 unauthorized` / `account_disabled`: Token không hợp lệ, hết hạn hoặc tài khoản bị khóa.
  - `403 forbidden`: Truy cập tài nguyên ngoài quyền sở hữu hoặc cố sửa/xóa tài nguyên SYSTEM.
  - `404 not_found`: Không tìm thấy tài nguyên trong phạm vi được phép của người dùng.
  - `409 resource_in_use`: Nguồn đang được tham chiếu bởi session/snapshot/job/reference, từ chối xóa.
  - `409 state_version_conflict`: Xung đột phiên bản trạng thái phiên duyệt (Optimistic Concurrency Control).
  - `409 idempotency_conflict`: Cùng Idempotency-Key nhưng khác payload đầu vào.
  - `422 validation_error`: Sai schema, vượt giới hạn dung lượng hoặc trùng core source trong pool.
  - `503 policy_unconfigured`: Chưa có cấu hình quota/chính sách từ operator.
- Không mount các endpoint upload/media/export v1 trong chế độ deploy mode để tránh bypass phân quyền và PII.

### 21.5 Hàng đợi bền vững (Durable Queue) và Contabo AI Worker
- Quản lý hàng đợi qua bảng `rav_pilot_job`, `rav_pilot_job_attempt` và `rav_pilot_resource_slot` trên PostgreSQL.
- Job kinds: `VALIDATE_UPLOAD`, `RENDER_MEDIA`, `PREPARE_DINO`, `PREPARE_CLIP`, `PROPOSE`, `YOLO_BATCH`, `METRICS`, `EXPORT_BUILD`, `EXPORT_FINALIZE`, `DELETE_SOURCE`.
- Job states: `QUEUED`, `WAITING_RESOURCE`, `RUNNING`, `CANCEL_REQUESTED`, `SUCCEEDED`, `FAILED`, `CANCELLED`.
- Worker nhận việc theo cơ chế Compare-And-Swap (CAS), nhận lease có hạn và gửi heartbeat kèm `fence` token.
- Điều phối GPU Kaggle: Lưu dấu vết session sở hữu (`launch_marker`). Khi hoàn tất, worker Kaggle phải xác nhận `cleanup_done=true` và kết quả NPZ hợp lệ thì backend mới công bố kết quả vào FieldStore.

### 21.6 Động cơ Quota (Quota Engine)
- Chu kỳ cửa sổ quota cố định 4 giờ UTC: `start = floor(epoch(now) / 14400) * 14400`.
- Theo dõi độc lập: CPU usage (tổng milliseconds CPU thực tế của tiến trình worker) và GPU usage (session wall-time milliseconds trên Kaggle).
- Khi tạo job, hệ thống tính toán ước lượng và tạo `quota_hold`: trừ từ quota cơ bản (base) trước, nếu thiếu thì trừ tiếp từ `bonus` ledger do operator cấp.
- Khi job kết thúc, đối soát số milliseconds thực tế (`actual_ms`), tạo `usage_receipt` bất biến và giải phóng phần dư thừa.
- Quản lý ngân sách Kaggle toàn cục: kiểm tra hạn mức còn lại trước khi cấp phát session GPU mới.

### 21.7 Tải lên trực tiếp S3 và phục vụ Media riêng tư
- **Direct Video Upload**:
  1. Client gọi `POST /api/v2/uploads` kèm `declared_bytes` → API cấp Presigned POST vào thư mục `staging/` trên S3.
  2. Client tải file trực tiếp lên S3.
  3. Client gọi `POST /api/v2/uploads/{id}/complete` → API xếp job `VALIDATE_UPLOAD`.
  4. Worker tải file, kiểm tra sandbox/hash/định dạng, rồi upload chính file đã kiểm tra vào canonical key `private/<owner>/<source>/raw/`. Sau khi hoàn tất, chuyển trạng thái nguồn sang `READY`.
- **Private Media**:
  - Không phục vụ static trực tiếp.
  - Client gửi yêu cầu `POST /api/v2/media/requests` (variant: thumbnail, frame, context).
  - Nếu đã có cache: API kiểm tra quyền và cấp Presigned GET URL ngắn hạn (TTL 5 phút).
  - Nếu chưa có cache: API xếp job `RENDER_MEDIA` trả về trạng thái `PENDING` (từ DEP7-Frames chỉ còn cho `context`; thumbnail/frame lookup ảnh lưu sẵn, §21.20). Endpoint `GET /api/v2/media/requests/{id}` chỉ đọc trạng thái/cấp URL, không chạy giải mã video đồng bộ trong request đọc.

### 21.8 Pipeline xuất dữ liệu (PII Redaction & Confirmation Gate)
- Mọi export bắt buộc xuất phát từ một `snapshot` bất biến đã chốt. Cấm tải bản ZIP raw trực tiếp.
- Vòng đời export: `REQUESTED → PROCESSING → NEEDS_CONFIRMATION → FINALIZING → READY`.
- Worker giải mã đúng chỉ số frame theo snapshot, chạy mô hình phát hiện khuôn mặt và biển số xe (YuNet + ONNX) để che (redact) trên ảnh độ phân giải đầy đủ.
- Sửa lỗi nhận diện ảnh (2026-10-16): ưu tiên ảnh `FRAME` trong set READY đúng nguồn/raw SHA/sample spec/encoding và quyền owner/SYSTEM,
  kiểm checksum tải xuống. Khi cần raw fallback, kiểm SHA nguồn và đối chiếu unit ID với exact anchor của decoder;
  ordinal chỉ là thứ tự gói xuất, không phải frame index. Thiếu/sai nguồn hoặc anchor phải thất bại, không sinh ảnh giả.
- UI đọc lịch sử export theo phiên từ DB, phân trang mới nhất trước; reload/tab không làm mất lần chạy.
  `GET /sessions/{id}/exports` trả Page metadata, không tải tất cả member/preview. Tạo snapshot cập nhật ngay danh sách chọn xuất.
  Tiến trình lấy từ build/finalize job thật; xem lại review của đúng export và chỉ owner xác nhận payload đã đóng băng.
- Tạo báo cáo đóng băng `report_basis` (HTML offline tự chứa style + JSON).
- Đóng băng `payload_hash` (gồm danh sách thành viên snapshot, checksum các ảnh đã che và nội dung báo cáo).
- **Owner Confirmation Gate**: Chủ sở hữu xem trước các ảnh đã che và báo cáo trên UI, sau đó bấm xác nhận (gửi đúng `payload_hash`).
- Sau khi xác nhận, worker đóng gói file ZIP lên S3, chuyển trạng thái export sang `READY` và cấp link tải có hạn.
- **PII resize/cache (2026-10-16, người dùng duyệt).** Inference trên ảnh RGB thu nhỏ giữ tỷ lệ, cạnh dài tối đa 640,
  không phóng ảnh nhỏ lên. YuNet và plate detector dùng ảnh này; plate pad một ô 640. Scale box theo kích thước RGB
  thực tế về ảnh gốc, clamp rồi che trên ảnh đủ phân giải. Ghim version pipeline mới và ghi mode/kích thước/config trong evidence.
  Giảm khả năng bắt PII nhỏ/xa so với tile full-res; không claim chất lượng tương đương hay tự bỏ confirmation gate.
  Cache PRIVATE theo owner/source/unit/raw SHA/FRAME SHA (hoặc exact raw anchor)/model SHA/toàn bộ config và version.
  Ảnh đã che trên S3 + detections/kích thước/frame index/model/config lưu qua `rav_pilot_artifact` kind EXPORT_PREVIEW,
  variant PII_REDACTED; chỉ READY sau fenced publish EXPORT_BUILD. PUT có INTENT/WRITTEN, ACCEPT và accounting cùng publish;
  lỗi/hủy dùng cleanup attempt hiện có. Preview/report/bundle cũng có artifact và receipt; key chứa attempt ID để retry không ghi đè output cũ.
  Source pin/snapshot giữ nguồn; xóa nguồn dùng inventory artifact hiện có. Cache giữa snapshot tái dùng ảnh/evidence, không tái dùng confirmation.
  NEEDS_CONFIRMATION nghĩa đã lưu preview + evidence, chưa có ZIP; sau xác nhận mới lưu bundle S3, READY tải lại qua lịch sử.
  Migration additive `022_pii_cache_index.sql` lập index PRIVATE owner/input cho cache READY trên artifact hiện có;
  operator áp trước rollout FE/API/worker mới, không tự migrate lúc boot.
  Preview cũ thiếu marker xác minh exact source/frame được giữ để đối chiếu nhưng không cho xác nhận/tải ZIP mới;
  UI yêu cầu tạo export mới từ snapshot. Không ghi đè hoặc xóa kết quả cũ để che lỗi.
- **DEP6 — model thật và fail-closed (2026-10-16, người dùng duyệt).** Audit #136: thiếu weights thì detector trả 0 box,
  nhánh biển số là stub, worker Docker không mount thư mục model → export có thể ra ảnh gốc mang nhãn "đã che". Sửa:
  - Model cố định theo SHA-256: `face_detection_yunet_2023mar.onnx` (OpenCV Zoo, MIT)
    `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`; `yolo-v9-t-640-license-plates-end2end.onnx`
    (open-image-models, MIT) `c3c1026ca7d0585dd88084d68182dd897113712fa734ae1557ca70174440c076`.
    Thư mục `RAV_PII_MODEL_DIR`, mặc định `data/models/pii` (worker Docker đã mount `./data`). Runtime `onnxruntime` CPU.
    Nạp model từ chính bytes đã kiểm SHA (không đọc lại theo path). Thread = `RAV_MODEL_THREADS` của CPU lane (BULK 1).
    `manifest.json` trong ZIP ghi `pii_models` (file + SHA) và `pii_evidence` từng ảnh.
  - **Fail-closed:** thiếu file, sai SHA hoặc nạp lỗi ở bất kỳ model nào → EXPORT_BUILD lỗi `pii_model_unavailable`,
    export `FAILED`, không upload preview/ZIP. Không có detector giả trong runtime; test lifecycle tự inject detector.
  - Thiết lập DEP6 ban đầu (được resize/cache ở trên thay thế): YuNet trên ảnh full-res, ngưỡng `face_threshold`. Biển số: tile 640, chồng `tile_overlap`, pad 114,
    RGB/255 CHW; output end2end `[batch, x1, y1, x2, y2, class, score]` theo tọa độ tile → cộng offset, ngưỡng
    `plate_threshold`; gộp box theo `nms_iou`. Evidence từng ảnh ghi model + SHA; report nói rõ che tự động có thể sót,
    chủ sở hữu phải xem preview trước khi xác nhận. Không claim recall/precision khi chưa có tập nhãn PII.
  - Kiểm chất lượng: chạy trên frame dashcam thật trong `data/` (gitignore), xem bằng mắt, ghi số box/frame và ca sót
    trong QA. Không commit ảnh chứa PII. Deploy/provision model lên Contabo là bước riêng, người dùng quyết định.

### 21.9 Xóa nguồn an toàn (RESTRICT)
- Nguồn SYSTEM/protected: từ chối xóa (403 `forbidden`).
- Nguồn đang có session, snapshot, reference hoặc job tham chiếu: từ chối xóa (409 `resource_in_use`). Không hỗ trợ release-pin tự do trong pilot.
- Nguồn PRIVATE không còn tham chiếu: chuyển trạng thái sang `DELETE_PENDING`, xếp job `DELETE_SOURCE`. Worker xóa file trên S3 và chuyển trạng thái sang `DELETED`. Hệ thống lưu receipt tombstone audit, không xóa dòng trong database.
- Retry đã duyệt (#112): key cũ luôn replay receipt cũ. Key mới trên `DELETE_PENDING` chỉ được tạo job khi job xóa trước FAILED/CANCELLED, mọi attempt usage FINAL/cleanup_done và không còn slot/hold/pin chưa đối soát. Job còn hoạt động trả `delete_in_progress`; chưa đối soát trả `delete_reconcile_pending`. Mỗi yêu cầu dùng CAS resource_version; không đưa nguồn về READY.
- Worker kiểm owner, lease/fence, version và RESTRICT trước I/O; đóng băng danh sách raw + artifact của đúng nguồn/owner, DELETE từng key và HEAD xác nhận không còn. Partial delete/lỗi/hủy giữ DELETE_PENDING và dung lượng persisted; retry DELETE idempotent cả key đã mất. Chỉ publish sau khi toàn bộ key đã xác nhận: artifact DELETED, source DELETED, trừ persisted USER/GLOBAL đúng một lần, receipt SUCCEEDED cùng transaction. Receipt FAILED ghi lỗi an toàn; không trả object key qua DTO.
- Receipt chỉ worker ghi; nếu API hủy job còn trong hàng đợi hoặc đọc receipt từ worker cũ thiếu handler, DTO chiếu job FAILED/CANCELLED thành FAILED kèm lỗi job, không mở thêm quyền UPDATE cho API.

### 21.10 Phiên mẫu hệ thống (Demo Template) và Tạo phiên riêng trống
- Các phiên mẫu và lịch sử mẫu được lưu ở scope SYSTEM, chỉ đọc cho mọi người dùng để tìm hiểu cách thức hoạt động.
- Nút "Tạo phiên riêng từ mẫu" (`POST /api/v2/demo-templates/{id}/fork`):
  - Tạo một phiên PRIVATE **hoàn toàn trống** cho người dùng, kế thừa cấu hình khởi tạo hợp lệ (pool video, reference model/dataset, goal, target_k).
  - Phiên mới bắt đầu với `state_version = 1`, `kept_ids = []`, `dropped_ids = []`, `decision_seq = 0`.
  - Toàn bộ số lượng proposals, decisions, snapshots, jobs của phiên mới đều bằng **0**.
  - Tuyệt đối không sao chép lịch sử duyệt, đề xuất hay kết quả cũ của mẫu; không tự động chạy đề xuất đợt đầu. Người dùng chủ động bắt đầu phiên duyệt của riêng mình.

### 21.11 Ghi chú triển khai đã chốt ở DEP2F (#89)
- **Chỉ PostgreSQL.** `PilotDB` từ chối URL không phải `postgresql://`; deploy thiếu `DATABASE_URL` → 503 `dependency_unavailable`, không fallback SQLite/REST/service_role.
- **Migration** `001_pilot_identity.sql` → `002_pilot_runtime.sql` → `003_pilot_storage.sql` (DEP4) → `004_pilot_worker_objects.sql` (DEP4F) → `005_pilot_features.sql` (DEP5a), `006_pilot_curation.sql` (DEP5b), `007_pilot_analysis.sql` (DEP5b2) → `008_pilot_export.sql` (DEP6, #94, #97) do operator chạy `python -m scripts.pilot_migrate --database-url ...` (advisory lock, ghi `rav_pilot_schema_migration(version, checksum)`).
  API/worker khi boot/request chỉ kiểm version đã áp; thiếu hoặc checksum khác → 503 `schema_not_ready`. Không tự chạy migration.
- **Role DB.** Migration tạo (nếu chưa có) `authenticated`, `anon` (Supabase đã có), `rav_pilot_api`, `rav_pilot_worker` dạng NOLOGIN; operator bật `LOGIN PASSWORD` theo runbook.
  `rav_pilot_api` là thành viên `authenticated`. Đọc dữ liệu người dùng: `SET LOCAL ROLE authenticated` + `request.jwt.claims` `{"sub": <user_id>, "role": "authenticated"}` → RLS.
  Ghi (tạo phiên, quyết định, snapshot, xóa nguồn, enqueue): role `rav_pilot_api` không SET ROLE, adapter kiểm owner/scope tường minh và khóa `SELECT … FOR UPDATE`
  theo thứ tự §21.3. `authenticated`/`anon` không có quyền ghi trực tiếp; cột `raw_key` không cấp SELECT cho `authenticated`. Worker chỉ dùng `rav_pilot_worker`.
- **Profile** không có cột vai trò; từ §21.34 hệ thống tự động tạo profile và storage account trong lần xác thực đầu tiên nếu operator đã cấu hình policy đăng ký hợp lệ (thiếu/sai policy đăng ký → 503 `policy_unconfigured`). `active=false` → 401 `account_disabled`.
- **Auth** xác minh bằng JWKS (RSA hoặc EC), issuer `<SUPABASE_URL>/auth/v1`, audience `authenticated`; HS256 chỉ khi operator cấp riêng `SUPABASE_JWT_SECRET` (khóa JWT legacy), không dùng secret API key.
- **Review state** = replay log `rav_pilot_decision` (append-only, `seq` identity); `rav_pilot_session.state_version` tăng 1 mỗi lô; `unit_id` là core ID baseline (§2) và phải thuộc nguồn trong pool.
  Snapshot chỉ nhận `{note, expected_state_version}`; server tự tính `members_hash`/`context_hash` (cùng công thức `service/snapshot.py`), cùng hash → trả snapshot cũ.
- **Quota policy** gắn qua `rav_pilot_profile.policy_id`; config thiếu khóa bắt buộc (runtime spec §3.2) hoặc `enabled=false` → 503 `policy_unconfigured`. Migration không seed số.
- **Queue.** Claim dùng `FOR UPDATE SKIP LOCKED` cho slot + job, chọn owner round-robin qua `rav_pilot_dispatch_cursor`; heartbeat/publish kiểm worker, fence, slot generation và lease còn hạn;
  publish idempotent theo `settlement_key`; lease hết hạn → attempt `LOST`, usage `UNKNOWN`, slot `QUARANTINED` cho tới `reconcile_slot` có bằng chứng process đã dừng.
  Worker không có handler cho kind → job `FAILED` `handler_unavailable`, không publish giả.
- **App deploy** `python -m uvicorn src.rav.api.pilot:app` chỉ mount `/api/v2` + health; `src.rav.api:app` (local v1) không mount v2.
- **Test** RLS/transaction/race chạy trên PostgreSQL thật qua `RAV_PILOT_TEST_DATABASE_URL` (thiếu → skip có lý do, CI dùng service postgres).

### 21.12 Ghi chú triển khai đã chốt ở DEP4 (#91)
- **Migration `003_pilot_storage.sql`**: storage account/hold, upload, artifact, media, demo template (+ status, event), import receipt; FK `session.started_from_template_id` và `job.result_ref`.
- **S3**: `RAV_S3_BUCKET` + `RAV_PILOT_S3_PREFIX` bắt buộc (không mặc định root); thiếu → 503 `dependency_unavailable` cho upload/media. Key do server dựng (runtime spec §6.3):
  `<prefix>/staging/<owner>/<upload>/video`, `<prefix>/private/<owner>/<source>/raw/<sha256>/video.<ext>`, `<prefix>/private/<owner>/artifacts/<artifact>/<sha256>`,
  `<prefix>/system/<seed_version>/sources/<core_source_id>/raw/<sha256>/video.<ext>`. Không nhận bucket/key/prefix từ client.
- **Upload**: `CREATED → VALIDATING → READY | FAILED`, `CREATED → ABORTED`. Presigned POST đúng key staging, `content-length-range [1, declared_bytes]`, TTL `upload_ttl_ms`.
  `complete` HEAD staging (thiếu/lớn hơn khai báo → 409 `upload_invalid`) rồi enqueue `VALIDATE_UPLOAD` cùng transaction; gọi lại trả cùng job.
  Worker: tải có giới hạn, SHA-256 đủ, probe bằng `local_folder._probe` (cùng fps/n_frames), kiểm duration/pixel theo policy, `core_source_id = sha256[:16]` (= local),
  `n_units` = số frame decoder `pyav` 4 fps, PUT canonical kèm `ChecksumSHA256`, HEAD đối chiếu → publish tạo nguồn PRIVATE `READY`.
  Cùng bytes đã có trong kho của chính owner → upload `READY` trỏ nguồn cũ, xóa bản canonical thừa, không tính dung lượng hai lần (giống local #52).
- **Dung lượng**: tài khoản `USER:<uuid>` và `GLOBAL`, giới hạn lấy từ policy (`storage_user_bytes`, `storage_global_bytes`). Upload giữ `2 × declared` (staging + canonical);
  publish chuyển phần canonical sang persisted; phần staging giữ tới khi supervisor xóa staging sau `capability_expires_at + staging_grace_ms`.
  Job có `profiles_by_kind[kind].output_bytes` thì giữ chỗ lúc enqueue, publish ghi đúng byte artifact. Vượt → 429 `storage_exceeded`.
- **Media** (thumbnail/frame đổi sang ảnh lưu sẵn từ DEP7-Frames, §21.20): `POST /media/requests {source_id, unit_id, variant: thumbnail|frame|context, max_side?}`; unit phải thuộc nguồn (core ID + thời điểm có trong lưới decoder);
  cache theo `(owner, input_hash)`; READY → presigned GET TTL `user_get_ttl_ms`; chưa có → job `RENDER_MEDIA` (queue MEDIA, không tính vào `pending_per_user`,
  giới hạn riêng `max_media_requests` job media đang chờ). `thumbnail` JPEG ≤ `max_side` (mặc định 320), `frame` JPEG đủ phân giải, `context` mp4 H.264 ±3 s (≤ 640 px).
  Nguồn không còn READY → không cấp URL mới.
- **Phiên mẫu**: chỉ mẫu `VERIFIED` mới tạo phiên được; DTO lịch sử là projection đã lọc (proposal: n/recipe/picks/thời điểm; decision: seq/unit/action/actor/proposal_n/thời điểm/lý do;
  snapshot: n/số frame/members_hash/decision_seq/created_by). "Tạo phiên riêng" = cùng logic `create_session` + receipt `FORK_TEMPLATE`, đếm 0, không enqueue.
- **Import mẫu** `scripts/pilot_import_demo.py`: inventory commit ở `docs/setup/pilot_demo_inventory.json` (danh sách #90); mặc định dry-run in kế hoạch + `inventory_hash`;
  `--apply --operator-confirm <hash>` mới ghi. UUID tất định (uuid5 theo seed_version + core ID) để chạy lại không nhân đôi; S3 PUT xác minh trước, rồi một transaction DB;
  receipt `COMMITTED` cùng hash → no-op. Không chép `.env`, không ghi path máy.

### 21.13 Hoàn thiện DEP4F (2026-10-10)
- Actual CPU/GPU không được cắt bằng `min(actual, reserved)`: `extend_hold` thêm phần thiếu theo cùng quota/window/bonus rules trước tiếp tục công việc hoặc settlement. Nếu credit không đủ để tất toán actual đã đo, lưu usage vào attempt, giữ hold/pin/slot chờ đối soát; chặn admission của owner cho tới khi tất toán, không trả phần dư hoặc báo usage FINAL giả.
- Lease hết hạn: job chờ `WAITING_RESOURCE`, không trở lại `QUEUED` trước khi attempt cũ được xác nhận dừng và usage được đối soát. Claim không nhận job còn attempt chưa reconcile, kể cả có slot khác rảnh.
- Decision dùng cùng phép kiểm Unit với media: core source, định dạng ID baseline, t0=t1, thời điểm nằm trong phạm vi và lưới decoder cố định; kiểm cả lô trước khi ghi.
- Object do worker PUT có receipt theo attempt trước ghi S3; publication xác nhận object được nhận, hoặc cleanup xóa object chưa được nhận sau khi process đã dừng. Dung lượng/pin không được nhả trước cleanup. Receipt bền phục hồi được sau crash; không quét/xóa cả prefix.
- Giữ layout canonical §21.12; receipt phân biệt `(attempt_id, object_key)` và chỉ một receipt chưa DELETED cho mỗi key. Cleanup khóa profile rồi receipt, kiểm lại DELETE_PENDING trước DELETE/ghi DELETED, để supervisor chạy đồng thời không xóa lại key đã tái sử dụng bởi retry.
- Migration bổ sung `004_pilot_worker_objects.sql` lưu receipt object; số `released_bytes`/`persisted_bytes` của hold giữ việc nhả canonical chờ cleanup đúng một lần. Export dùng `008_pilot_export.sql` (005–007 thuộc DEP5, #94, #97). Worker lưu PID, host và process start token trước handler; supervisor chỉ tự đối soát khi handler xác nhận đã dừng hoặc chứng minh process đó đã thoát trên cùng host/boot. Tín hiệu dừng có deadline theo policy; quá deadline worker thoát, giữ lease/quota/receipt cho phục hồi.
- Context preview giữ thời gian tương đối của nguồn (CFR/VFR), cửa sổ ±3 s; giảm output fps phải resample theo thời gian, không encode mọi frame vào fps thấp hơn.
- Worker entry `python -m src.rav.worker --mode pilot`: dispatcher CPU/MEDIA và supervisor, schema/config tường minh, xử lý SIGTERM/KeyboardInterrupt, không chạy API/local recovery. `operator-bootstrap` chỉ probe, không claim job. Docker worker dùng entry này với dependency đọc được bởi user không phải root.

### 21.14 DEP5a — chuẩn bị đặc trưng DINO/CLIP (#94)
- **Migration `005_pilot_features.sql`**: `rav_pilot_gpu_budget_snapshot` (cột runtime spec §3.2 + `source KAGGLE_SDK|OPERATOR`, `evidence JSONB`; chỉ INSERT, bất biến),
  `rav_pilot_gpu_hold` (runtime spec §3.2), unique artifact FEATURE `READY` theo `(source_id, variant)`. Worker INSERT snapshot, INSERT/UPDATE gpu_hold; API không ghi hai bảng này.
- **Feature**: `DINO` = `emb.dinov2_s` + `hash.phash`; `CLIP` = `emb.clip_b32` (checkpoint/revision pin của component). Artifact `kind=FEATURE`, `variant=DINO|CLIP`,
  `input_hash = canonical_hash({core_source_id, raw_sha256, feature, field_keys, spec_hash})`; NPZ `{ids, <field>…}` (float32 L2 / pHash uint64) + manifest
  `{source_id, core_source_id, feature, field_keys, spec_hash, n_units, ordered_ids_hash, npz_sha256, runtime}`. Ordered IDs = unit của decoder `pyav` 4 fps + `per_frame` (= local).
- **Route** `POST /api/v2/sources/{id}/preparations {feature}` + `Idempotency-Key` → `PreparationDTO {source_id, feature, ready, job}`:
  có artifact READY khớp `input_hash` → 200 `ready=true, job=null`; nguồn PRIVATE của owner chưa có → enqueue `PREPARE_DINO|PREPARE_CLIP` (queue GPU) → 202 `job`;
  nguồn SYSTEM thiếu đặc trưng → 409 `system_feature_missing` (người dùng không chạy PREPARE trên nguồn SYSTEM). Nguồn không READY → 409 `source_unavailable`.
  `SourceDTO.dino/clip = {ready, job_id}`: `ready` từ artifact READY (PRIVATE của owner hoặc SYSTEM cho nguồn SYSTEM), `job_id` = job PREPARE đang hoạt động của owner.
- **Payload đóng băng** lúc enqueue: `source_id, core_source_id, raw_sha256, resource_version, feature, field_keys, spec_hash, n_units, input_hash`. Raw key server tự dựng lại khi chạy, không lấy từ client.
- **Ngân sách GPU toàn cục**: supervisor đọc Kaggle SDK `get_accelerator_quota_statistics` định kỳ → snapshot `remaining_ms = total − used − reserved`, `reset_at = quota_refresh_time`,
  `usage_baseline_seq` = seq usage receipt lớn nhất lúc đọc; lỗi đọc → không ghi snapshot. Claim GPU (cùng transaction claim) cần snapshot của account của slot còn tươi
  (`gpu_snapshot_fresh_ms`), `external_session_active=false` và `remaining − Σ GPU usage receipt seq > baseline − Σ gpu_hold OPEN còn lại − gpu_headroom_ms ≥ profiles_by_kind[kind].gpu_ms`;
  thiếu/cũ → job `WAITING_RESOURCE` `gpu_budget_unverified`, không đủ → `gpu_budget_exhausted` (claim xét lại job chờ hai lý do này). Đạt → gpu_hold OPEN theo attempt.
  Settle attempt (publish/finish/reconcile) chốt gpu_hold `FINAL` với `consumed_ms` = GPU actual. CLI operator ghi snapshot `OPERATOR` chỉ dự phòng.
- **Vòng đời Kaggle** (runtime spec §7.1): mỗi slot GPU (`slot_inventory.GPU`, `account_or_host` = account Kaggle) có một notebook private cố định `<account>/rav26-pilot-<slot_key>`.
  `reconcile_launch(slot)` đọc trạng thái session của notebook đó: đang chạy/xếp hàng → `FOUND`, đã kết thúc hoặc chưa từng chạy → `ABSENT_VERIFIED`, lỗi API → `UNKNOWN`.
  Trước khi tạo session: phải `ABSENT_VERIFIED`, rồi ghi attempt `launch_state=INTENT` + `launch_marker`; tạo xong ghi ngay `ATTACHED` + `session_id/operation_name` (không đổi về sau);
  lỗi/timeout khi tạo → `LAUNCH_UNCERTAIN` và worker tách khỏi job (không finish) để lease hết hạn → slot QUARANTINED. Dừng: hủy session rồi chờ trạng thái kết thúc trong
  `stop_deadline_ms` → `STOPPED`, `session_stop_verified=true`; quá hạn → `STOP_PENDING` + tách. GPU actual = thời gian thực từ trước khi tạo session tới khi dừng đã xác minh.
- **Session GPU tương tác (#113)**: `get_kernel_session_status(slug)` chỉ phản ánh lần chạy notebook lưu, không được dùng làm bằng chứng session tương tác đã dừng. Đối soát đúng `session_id` qua SDK `GetKernelSessionLogsStream`: HTTP 200 `text/event-stream` → FOUND, HTTP 200 `application/json` (log lưu sau kết thúc, rỗng hoặc mảng log; không nhận JSON lỗi) → ABSENT_VERIFIED; lỗi/kiểu khác → UNKNOWN. Worker lấy session ID từ attempt gần nhất của slot (`process_group.gpu_slot_key` ghi cùng INTENT) hoặc evidence bootstrap đã kiểm; evidence bootstrap giữ qua đối soát. INTENT/LAUNCH_UNCERTAIN không handle → UNKNOWN, không suy từ quota hay status notebook. Slot mới chỉ bootstrap sau smoke session riêng kết thúc có bằng chứng theo ID.
- **Supervisor slot GPU QUARANTINED**: cần process cũ đã dừng (như CPU) **và** `launch_state ∈ {NONE, STOPPED}` hoặc `reconcile_launch = ABSENT_VERIFIED`. `FOUND` có `session_id` → hủy session,
  xét lại vòng sau; `FOUND` không handle hoặc `UNKNOWN` → giữ QUARANTINED + `reconcile_evidence` (session Kaggle tự kết thúc khi hết giờ thì thành ABSENT). Không đoán ABSENT.
- **Worker PREPARE**: tải raw canonical có giới hạn + SHA đủ → dựng Context core (decoder/unit như local, IDs phải đủ `n_units`) → transport C1/C2 (`remote_s3_embed.compute`)
  với key `<prefix>/jobs/<job>/<attempt>/{progress.json,result.npz}`, raw dùng URL GET của key canonical (không upload lại), asset DINO content-addressed `<prefix>/system/models/dinov2_s/<sha>`.
  Kết quả phải qua `validate_result` (shape/dtype/finite/L2/IDs/pHash/pixel/runtime) → NPZ artifact PUT `private/<owner>/artifacts/<artifact>/<sha>` có object receipt (§21.13) → publish
  tạo artifact FEATURE READY + tất toán dung lượng. Key `jobs/<job>/<attempt>/…` xóa theo tên cụ thể sau publish/cleanup, không quét prefix.
- **Đặc trưng nguồn SYSTEM**: `scripts/pilot_import_demo.py --features` đọc đặc trưng đã tính local của nguồn trong inventory, kiểm ordered IDs/shape/finite/L2 với video, đưa vào kế hoạch +
  `inventory_hash`; `--apply --operator-confirm` mới PUT `<prefix>/system/<seed_version>/features/<core_source_id>/<feature>/<sha>.npz` và tạo artifact SYSTEM READY (idempotent).
- **Test**: transport Kaggle giả + PostgreSQL thật + S3 `moto`; smoke GPU thật để riêng khi người dùng bảo (#94).

### 21.15 DEP5b — đề xuất trên worker cho Studio v2 (#95)
- **Recipe pilot**: `random`, `uniform`, `diversity`, `text_diversity` (registry §7, không features/candidate_stage/reference model).
  `reference_diversity`, `model_guided`, `text_model_guided`, YOLO_BATCH, METRICS: **Hướng tới** DEP5b2 (cần đường đưa model/dataset vào pilot).
- **Migration `006_pilot_curation.sql`**: `rav_pilot_proposal.job_id` (FK job, unique khi khác NULL); worker INSERT proposal. Proposal vẫn bất biến.
- **`GET /api/v2/recipes`** → `RecipeDTO[]` như v1 (§11.2: `name, description, config, params_schema, components, background, requires_training,
  requires_model`) chỉ cho recipe pilot; đọc catalog tĩnh `src/rav/store/pilot_recipes.json` sinh từ registry (test giữ khớp registry) — API không import registry/numpy.
- **`POST /api/v2/sessions/{id}/proposals`** + `Idempotency-Key`, body `{recipe, config?, batch_size (1..200), seed? (0..2³¹−1), return_scores?, expected_state_version}`
  → 202 `JobDTO` (`PROPOSE`, queue CPU). API kiểm: phiên của owner; `expected_state_version` khớp (lệch → 409 `state_version_conflict`); recipe thuộc danh sách
  pilot (khác → 422 `recipe_not_available`); `config` nếu gửi: `name = recipe`, cùng loại component với recipe gốc, không `features`/`candidate_stage`
  (sai → 422 `invalid_config`; tham số chi tiết do worker kiểm); tổng unit pool ≤ `chunk_bounds_by_kind.PROPOSE.max_units` (vượt → 422 `pool_too_large`);
  một job PROPOSE đang hoạt động mỗi phiên (→ 409 `proposal_in_progress`); mọi nguồn pool có đặc trưng recipe cần (DINO cho diversity/text_diversity,
  CLIP cho text_diversity; thiếu → 409 `feature_not_ready`, `details.missing = [{source_id, feature}]`). Seed thiếu → server sinh.
- **Payload đóng băng**: session id, spec decoder/unit, pool `[{source_id, core_source_id, raw_key, raw_sha256, resource_version, n_units, features: {DINO|CLIP: artifact}}]`,
  `state_version`, `decision_seq` (seq lớn nhất), `proposal_count`, `goal`, recipe, config (null = mặc định registry), `batch_size`, `seed`, `return_scores`.
- **Worker PROPOSE**: raw theo SHA (cache) → Context core như local; field DINO/CLIP/pHash chỉ từ artifact FEATURE (cache-only, thiếu → FAILED `feature_not_ready`,
  không tự tính); state = replay decision `seq ≤ decision_seq`; `ctx.proposals` = proposal `n ≤ proposal_count`; config resolve bằng registry (sai → FAILED `invalid_config`);
  `pipeline/propose.py`. Cùng job tính giải thích đợt (như `service/explain.py`) và, nếu `return_scores`, điểm mọi ứng viên (≤ `max_units`).
- **Publish**: khóa phiên; `state_version`, `decision_seq`, `proposal_count` hiện tại khớp payload → INSERT proposal `n = proposal_count + 1`
  (`frozen_input` = input như v1 + `decision_seq`/`state_version`, `picks`, `fields_used`, `evidence = {explain, scores?, picks}`); không đổi `state_version`.
  Lệch → FAILED `proposal_context_changed` (không ghi, vẫn tính CPU). FE khóa thao tác duyệt khi job đề xuất đang chạy.
- **Đọc**: `GET /sessions/{id}/proposals` (n tăng dần) và `GET /sessions/{id}/proposals/{n}` → `{n, round, created_at, job_id, input, picks[{unit_id, source_id, rank,
  combined, breakdown}], relaxed, explain, scores?}` qua RLS owner. Unit không có route riêng: ID `core:t0-t1` + media request §21.12.
- **`GET /jobs/{id}/result`** (owner): `PROPOSE` → `{kind, session_id, proposal_n}`; `PREPARE_*` → `{kind, source_id, feature, ready}`; job chưa SUCCEEDED → 409 `job_not_succeeded`.
  **`POST /jobs/{id}/retry`** + `Idempotency-Key`: chỉ job FAILED/CANCELLED của owner → job mới `retry_of`; PREPARE giữ payload, PROPOSE đóng băng lại theo state
  hiện tại với cùng recipe/config/batch/seed; kind khác → 422 `retry_unsupported`.
- **Test**: parity với local (picks/ID/thứ tự 100%, điểm ≤ 1e-6), ngữ cảnh đổi, một job/phiên, hủy tính CPU, A/B, 429, `feature_not_ready`, A06 (3 tài khoản).

### 21.16 DEP5c — Studio và giao diện pilot trên v2 (#96)
- Entry pilot public/login/app (#104): landing `/` không gọi dữ liệu private; `/login` dùng auth dưới đây; `/app` yêu cầu `me.active` và giữ hash route Studio hiện có. Redirect sau đăng nhập chỉ tới `/app` cùng origin; local không đổi. Chi tiết UI tại UI_DESIGN deployment §1–2b.
- **Chế độ build** `VITE_RAV_MODE=local|pilot` (mặc định `local`): `local` giữ nguyên UI + `/api/v1` (không đăng nhập); `pilot` chỉ gọi `/api/v2`
  (gốc `VITE_API_URL`, mặc định cùng origin). Hook/component dùng chung một interface `api`; bản pilot ánh xạ DTO v2 sang dạng UI đang dùng.
- **Auth pilot**: `@supabase/supabase-js` (`VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY`) giữ session/refresh; FE gắn access token Bearer.
  401 → refresh một lần rồi gửi lại (GET, hoặc write có Idempotency-Key/CAS); vẫn 401 hoặc `account_disabled` → đăng xuất. Đăng xuất/đổi tài khoản tăng
  `auth_generation`, hủy request đang chạy, bỏ response của generation cũ, xóa state/URL rồi tải lại trang. Dev Access Token chỉ có ở `vite dev`.
- **DTO bổ sung (đọc)**: `SourceDTO.core_source_id` (unit ID dạng `core:t0-t1`); `SessionDTO.n_kept`, `n_dropped` (replay quyết định, cho sidebar).
- **Studio pilot**: đề xuất luôn qua job (§21.15) → poll `GET /jobs/{id}` → `GET /jobs/{id}/result` → nạp đợt; thao tác duyệt khóa khi job đề xuất đang chạy;
  hiện `WAITING_RESOURCE` (lý do), `CANCEL_REQUESTED` khác `RUNNING`/`CANCELLED`; Hủy/Thử lại qua route job. `feature_not_ready` → nút chuẩn bị DINO/CLIP
  (§21.14) cho đúng nguồn thiếu. Quyết định/snapshot gửi `expected_state_version`; 409 → tải state mới, báo người dùng.
- **Media pilot**: ảnh/clip qua media request §21.12 (thumbnail khi hiện, frame/context khi mở viewer), URL dùng tới `expires_at`; không suy URL từ unit.
- **Kho dữ liệu pilot**: "Của tôi" (upload trực tiếp presigned POST → complete → chờ READY; xóa qua delete-request, 409 hiện nơi đang dùng) và
  "Hệ thống" (chỉ đọc); trạng thái DINO/CLIP + nút chuẩn bị. Tạo phiên từ nguồn READY. Phiên mẫu: số đếm `counts.*`, fork trả `{session:{id}}`.
- **Ẩn ở pilot tới DEP5b2/DEP6**: YOLO, Phân tích/bản đồ, xuất/tải ZIP, dataset/model, recipe cần model. Tab Mô hình tham chiếu hiện lại theo §21.33.
- **Test**: vitest (không DOM) cho client/adapter với HTTP giả + request log: FE-AUTH (response cũ bị bỏ, 401 refresh một lần, disabled đăng xuất),
  FE-JOB (poll không POST, map trạng thái), FE-TEMPLATE (fork không tự đề xuất, replay cùng phiên); `tsc` + `build`; smoke 3 tài khoản trên stack local
  (API/worker pilot + Postgres portable + S3 `moto server` + token HS256 dev), không chạm cloud thật.

### 21.17 DEP5b2 — YOLO trên đợt, phân tích, recipe cần model (#97)
- **Migration `007_pilot_analysis.sql`**: `rav_pilot_proposal_evidence` (proposal, owner, model, `runtime_fingerprint`, `evidence`; unique proposal/model/fingerprint;
  bất biến) và `rav_pilot_analysis` (phiên, owner, job, `input_hash`, `input`, `decision_seq`, `state_version`, `proposal_count`, `results`; unique theo
  phiên + input_hash + decision_seq + proposal_count; bất biến). Worker INSERT; RLS owner đọc. Export dời `008_pilot_export.sql`.
- **Model & Dataset Catalog**:
  - Model: chỉ model SYSTEM do operator import hoặc admin upload qua UI (§21.33) (`scripts/pilot_import_model.py`: dry-run + `--operator-confirm`, PUT `<prefix>/system/models/yolo/<sha>.pt`,
    `validation=INFERENCE_VERIFIED` khi chạy thử một ảnh thành công, provenance ghi rõ dataset train gốc — COCO nano **không** là model train BDD).
    `GET /api/v2/models` → `ModelDTO[]` (id, name, sha256, size_bytes, task, class_map, validation, created_at).
    `GET /api/v2/models/{id}` → `ModelDetailDTO` (gồm metadata ModelDTO + `declarations[{dataset_version_id, relation, evidence}]`).
  - Dataset: catalog dataset tham chiếu scope SYSTEM/PRIVATE (`rav_pilot_dataset`, `rav_pilot_dataset_version`).
    `GET /api/v2/datasets` → `DatasetDTO[]` (id, name, description, versions[{id, n, classes, manifest_hash, created_at}]).
    `GET /api/v2/datasets/{id}` → `DatasetDTO`.
  - Session Reference Binding:
    `GET /api/v2/sessions/{id}/reference` → `SessionReferenceDTO` (dataset_version_ids, model_id, metadata chi tiết).
    `PATCH /api/v2/sessions/{id}/reference` + body `{expected_state_version, reference: {dataset_version_ids, model_id}}`:
    Kiểm tra CAS `expected_state_version` (lệch → 409 `state_version_conflict`); kiểm tra hợp lệ qua `_resolve_reference`
    (model phải có khai báo TRAIN/FINETUNE với dataset version tham chiếu, lệch → 422 `reference_invalid`);
    giải phóng pin cũ, đặt pin nguồn mới, cập nhật bảng `rav_pilot_session_reference`, tăng `state_version` phiên.
- **YOLO trên đợt** (`YOLO_BATCH`, queue CPU, CONTRACTS §17): `POST /api/v2/sessions/{id}/proposals/{n}/detections {model_id}` + `Idempotency-Key`
  → evidence cùng model/runtime đã có → 200 `{evidence, job: null}`; chưa có → 202 `{evidence: null, job}`. Chỉ model `INFERENCE_VERIFIED`, 1–256 picks.
  `GET …/detections?model_id=` → evidence hoặc 404. Worker: model + raw theo SHA (cache), pixel đúng anchor (`pipeline/batch_yolo.predict_batch`),
  `runtime_fingerprint` = engine + params + model sha + spec unit; publish kiểm proposal/model còn nguyên. Không đổi proposal/state/scoring.
- **Phân tích** (`METRICS`, queue CPU, #95): `POST /api/v2/sessions/{id}/analysis {expected_state_version, metrics?, on?}` + `Idempotency-Key`
  → đã có kết quả cùng input + `decision_seq` + `proposal_count` → 200 `{analysis, job: null}`; chưa → 202 `{analysis: null, job}`.
  Mặc định (`metrics` rỗng): bộ metric mặc định như `service/metrics.py` trên tập kept + view `embedding_map`, `timeline`, `duplicate_pairs` tham số mặc định.
  `GET /api/v2/sessions/{id}/analysis[?input_hash=]` → kết quả gần nhất kèm `current` (còn khớp state hiện tại); không tự tính khi GET.
  Worker dựng Context như PROPOSE (đặc trưng chỉ từ artifact; field dẫn xuất như `proj.pca2` tính trong bộ nhớ), `history` quyết định từ log (review_time).
- **Recipe cần dataset train** (`reference_diversity`, `model_guided`, `text_model_guided`): **Hướng tới** — cần import dataset ảnh + đặc trưng ảnh +
  khai báo model↔dataset thật (BDD + checkpoint train). `GET /recipes` liệt kê cả 7 recipe kèm `available`/`unavailable_reason`;
  3 recipe này `available=false`, `unavailable_reason=missing_model_training_provenance`; POST đề xuất với chúng → 422 `missing_model_training_provenance`.
- **FE pilot**: bật lại panel YOLO (chọn model SYSTEM, chạy = job), tab Phân tích + khối bản đồ/chỉ số ở panel phải đọc kết quả gần nhất, nút
  "Tính phân tích" (job) và ghi chú khi kết quả đã cũ; recipe chưa dùng được hiện mờ kèm lý do.

### 21.18 DEP7-Docker — đóng gói và release FE/API từ image (#100)

- Theo #107 và yêu cầu đổi hostname ngày 2026-10-12, FE và API dùng chung một Docker image và Render Web Service tại `c4-app-026.onrender.com` (URL ứng dụng `/app`); FastAPI pilot phục vụ thêm các file FE đã build, không cần Nginx/Node ở runtime. Worker vẫn riêng trên Contabo, API không nạp model/giải mã video/ZIP/PII.
- FE build `VITE_RAV_MODE=pilot`; cùng image nhận cấu hình public khi container khởi động: `VITE_API_URL`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY` (alias anon key cũ). Chỉ ba giá trị public được xuất vào `/runtime-config.js`; local/static build tiếp tục dùng env Vite. Không đưa credential DB/S3/provider vào FE image/config.
- FE gộp dùng API cùng origin (`VITE_API_URL` rỗng); chỉ các route public `/`, `/landing`, `/login`, `/app`, `/IrishCoffee-T026` trả SPA; `/assets` chỉ phục vụ file build, không cho traversal. API/health/route không tồn tại giữ lỗi JSON, không fallback HTML. `/runtime-config.js` chỉ xuất ba biến public, không secret DB/S3. `RAV_WEB_DIST` bật phục vụ FE, thiếu index/assets thì boot fail; API image riêng không bật biến này.
- API CORS chỉ cho origin FE explicit `RAV_FRONTEND_ORIGIN` (danh sách phân cách dấu phẩy), không wildcard; cho Bearer, JSON, Idempotency-Key và method GET/POST/OPTIONS của v2. Image gộp lắng nghe `0.0.0.0:$PORT`, health liveness tách readiness DB/schema; giữ Cache-Control no-store cho response.
- Release image `linux/amd64` theo tag commit và digest; registry private, Render có credential chỉ đọc; update image cần deploy explicit (dashboard/hook), không dựa auto-sync tag. Không tự apply migration, seed policy/profile hoặc lấy admin credential làm API role.
- Phạm vi hiện tại: đóng gói/publish FE/API trước worker AI. Chưa mở đủ pilot; nghiệp vụ cần worker giữ job lifecycle và lỗi/chờ đúng contract, không fake kết quả. DEP6/DEP8 và gate cloud/PII vẫn chưa đạt.

### 21.19 DEP7-Ops — vận hành chỉ đọc và tracing tập trung (#115)
- Người dùng duyệt màn vận hành chỉ đọc + tracing upload/job. `RAV_PILOT_OPERATOR_IDS` là allowlist UUID ở API server; token JWT/profile active được xác minh như v2, không nhận quyền từ body/query/FE. Rỗng = không operator. `GET /me` thêm `operations_read: bool`; không thêm vai trò vào profile hoặc quyền điều khiển. Operator được đọc metadata vận hành toàn pilot (không raw/video/URL/secret/payload SQL); người khác gọi `/operations/*` → 403.
- `GET /api/v2/operations` → thời gian server, heartbeat API/CPU-MEDIA/GPU dispatcher, slot state, snapshot ngân sách GPU cùng observed_at, quota CPU/GPU từng account; 50 uploads/jobs gần nhất (IDs, owner display, bytes/kind/state/phase/progress, error code, thời gian), lỗi API gần nhất và trạng thái thu log. Worker heartbeat quá 30 s → stale, không có → unknown; API trả được không chứng minh S3/provider sẵn sàng. Snapshot GPU hiển thị tuổi dữ liệu, không gọi provider từ API.
- `GET /operations/traces/{upload|job|request}/{UUID}` → metadata tài nguyên, attempts/usage/cleanup của job liên quan và tối đa 1000 timing events theo UTC; browser được đánh dấu client-reported, không dùng làm bằng chứng quota/quyền. Upload nối validation job; job nối upload nếu có; request nối bằng request_id. Giới hạn/retention/mất log phải hiển thị, không cộng span lồng nhau hoặc suy wall−CPU thành mạng.
- **Nhật ký admin (theo yêu cầu tích hợp PR Nam 2026-10-17)**: `GET /api/v2/operations/logs?hours=1&source=api|worker|gpu|browser&errors_only=false&limit=50&cursor=...` dùng cùng quyền operator/profile active của operations. Đọc telemetry đã sanitize, không raw stdout/exception hay proxy LogQL/PromQL. `hours` 1..24, `limit` 1..100; không COUNT/toàn bộ bảng, dùng index received_at và cursor `(received_at,id)` giảm dần với cửa sổ thời gian đóng băng từ trang đầu. Cursor giữ nguyên bộ lọc, sai/đổi bộ lọc → 422; không gửi URL/SQL/payload/filename/secrets. Response `{items,next_cursor,window_start,window_end,retention_days:7,coverage:"partial"}`; mỗi item chỉ id/UTC received_at/observed_at/source/stage/resource IDs và data allowlist §21.19, không trả raw data JSON. Lỗi = HTTP status >=400, error_code/error_type có giá trị, hoặc outcome error/failed; không suy từ message tự do.
  UI Vận hành có bảng Nhật ký hệ thống, lọc nguồn/lỗi/khoảng giờ, Làm mới và Trang sau (thay trang, không nối vô hạn); click resource ID mở trace hiện có. Không poll bổ sung, không truy vấn khi chưa mở bảng; đổi bộ lọc/refresh làm lại trang đầu, bỏ response cũ/unmount/logout. Giữ thông báo log có thể thiếu/retention/tracing tắt; không biến log thành metric chất lượng. Không thêm backend observability hoặc dependency vào API nhẹ.
- Migration `009_pilot_operations.sql` thêm bảng telemetry riêng `rav_pilot_timing_event`, `rav_pilot_heartbeat`, RLS không cho authenticated/anon truy cập trực tiếp, API/worker chỉ quyền telemetry cần thiết. Không đổi job/receipt/quota/schema các bảng nghiệp vụ. Event chỉ chứa stage/source/UTC/IDs/wall_ms/process_cpu_ms/bytes/queue_ms/status/outcome/error_type và cờ cache; không SQL/params/URL/filename/secret/thông báo exception. Timing lưu 7 ngày, tối đa 20.000 events/ngày và 2000 browser events/upload; UI nói thiếu dữ liệu khi hết hạn hoặc bị giới hạn.
- Export frame-load, PII inference, preview PUT và cache hit phát span `worker.export.*` cùng job_id/attempt_id để đi qua allowlist collector và mở từ timeline; chỉ timing/cache flag, không unit payload hay URL. Lỗi telemetry vẫn không làm fail export.
- `RAV_TRACE_ENABLED=true` bật collector API/worker: logging rav_timing → queue bounded 2048 → flush theo batch vào DB ngoài thread xử lý; lỗi telemetry không làm fail thao tác nghiệp vụ, báo drop/flush failures trong heartbeat. Không persist lại log của collector (chặn vòng lặp); chỉ business spans có upload/job, API response lỗi và API request có resource ID, không lưu mọi poll DB idle. Worker flush heartbeat mỗi 10 s, purge telemetry quá 7 ngày; đây là retention log, không backup/restore nghiệp vụ.
- Browser `POST /api/v2/uploads/{id}/timings`: JWT/profile active + upload của owner, batch 1..30, extra forbid, stage browser allowlist, timestamp có timezone, durations hữu hạn 0..24h, bytes không vượt declared, event UUID idempotent. Tối đa 2000 events/upload; không đổi upload/job state; không nhận job_id/owner_id/request_id từ client. Gửi best-effort timing đã có, lỗi gửi không làm fail upload; không có upload ID thì giữ Console. Client reported không thể giả source api/worker. API/worker không cần browser log để xử lý.
- FE pilot `#operations`, chỉ hiện navigation khi me.operations_read; manual refresh + auto mỗi 10 s khi tab visible, không gọi endpoint điều khiển. Có tìm UUID upload/job/request, bảng bước/mốc/thời gian/lỗi, sao chép ID qua chọn text, cảnh báo dữ liệu thiếu/cũ. Local không đổi; hết phiên/đổi account xóa state như §21.16.
- AI thêm spans load model/đọc exact anchor/inference YOLO CPU, raw/model cache. GPU chỉ hiển thị mốc provider mà worker thực sự biết; không bịa span inference remote. Kiểm thử: 401/403, operator active, spoofing/owner A-B/browser schema/caps/idempotence, collector không recursion/drop, timeline joins và heartbeat stale trên PG thật; FE auth boundary/tsc/build; smoke cloud trước tick lát.

### 21.20 DEP7-Frames — ảnh lưu sẵn trên S3, tách process worker, FE polling (#117, #119)
Thiết kế ở `docs/archived/2026-10-13/docs/backlog/FRAME_MATERIALIZATION_PLAN.md` §2–5; người dùng chốt 10 điểm ngày 2026-10-12 (#119). Lát chia F1 (schema + upload có ảnh + media lookup
+ xóa ảnh khi xóa nguồn), F2 (chuẩn bị nguồn cũ/SYSTEM), F3 (tách process + cache liên process), F4 (FE polling/ưu tiên + số đo). Nguồn cũ chỉ có ảnh sau F2
nên release cloud theo cả nhóm; thứ tự release/rollback theo §7 của plan.

- **Bất biến.** Giữ `DEFAULT_SAMPLE_SPEC` (pyav 4 fps + per_frame, `spec_hash` hiện có), core/unit IDs, FieldKey, feature/snapshot đã có. JPEG chỉ để hiển thị;
  DINO/CLIP/YOLO/export tiếp tục đọc pixel exact từ raw. Raw giữ nguyên; `context` ±3 s vẫn render từ raw qua `RENDER_MEDIA` + cache §21.12.
  Thumbnail/frame không decode video lúc phục vụ: thiếu ảnh trả trạng thái chưa chuẩn bị, không enqueue `RENDER_MEDIA`, không fallback.
- **Encoding** `FRAME_ENCODING` (hằng số code; `encoding_version` = 16 hex đầu canonical hash của dict): mỗi unit trên lưới decoder (ordered frame indices, đọc tuần tự
  `read_exact`, không suy index từ timestamp) có hai variant encode từ **cùng** RGB exact anchor: `FRAME` JPEG đủ phân giải quality 85; `THUMBNAIL` Pillow
  `thumbnail((320, 320))` mặc định rồi JPEG quality 85 — không resize từ JPEG đã nén. Đổi tham số encode = `encoding_version` mới, bộ ảnh mới, không ghi đè.
- **Migration `010_pilot_frames.sql`** (additive; 008 vẫn để export):
  `rav_pilot_frame_set(id, scope PRIVATE|SYSTEM, owner_id NULL khi SYSTEM, source_id → source RESTRICT, raw_sha256, sample_spec_hash, encoding_version, n_units,
  object_count, total_bytes, job_id, attempt_id, state READY|DELETE_PENDING|DELETED, created_at, updated_at)`, unique `(source_id, raw_sha256, sample_spec_hash,
  encoding_version) WHERE state = 'READY'`; chỉ INSERT khi publish **đủ bộ** (mọi unit × 2 variant có receipt), không có partial READY.
  `rav_pilot_frame(id, frame_set_id → frame_set RESTRICT, variant THUMBNAIL|FRAME, frame_idx, unit_id, object_key, sha256, size_bytes, width, height)`,
  unique `(frame_set_id, variant, frame_idx)` và `object_key`. Không lưu URL ký sẵn/bytes ảnh. RLS/grant như artifact (§21.12): revoke `PUBLIC/anon/authenticated`,
  policy `p_backend_all`; API `SELECT`, worker `SELECT, INSERT, UPDATE`; API tự lọc owner/scope. Thêm `PREPARE_FRAMES` vào check `rav_pilot_job.kind` (queue CPU).
- **Policy.** Migration không chèn policy; operator thêm version mới (runbook §5) với `chunk_bounds_by_kind.VALIDATE_UPLOAD` và `.PREPARE_FRAMES` =
  `{frame_batch_size, frame_put_concurrency, max_frame_bytes}` (số nguyên > 0) và `run_deadline_ms_by_kind` tương ứng; giá trị lấy từ số đo, không đoán.
  Thiếu khóa → 503 `policy_unconfigured` lúc `complete`/chuẩn bị (kiểm khi enqueue, đóng băng vào payload); đường khác của policy cũ không đổi.
- **Key S3** (server dựng; `frame_set_id` mới mỗi attempt nên retry không ghi đè object đã được nhận):
  `<prefix>/private/<owner>/frames/<frame_set_id>/<frame_idx>/<variant>/<sha256>.jpg`; SYSTEM `<prefix>/system/<seed_version>/frames/<core_source_id>/<frame_set_id>/<frame_idx>/<variant>/<sha256>.jpg`.
- **Upload (F1).** `VALIDATE_UPLOAD` sau kiểm SHA/probe/giới hạn và dedup owner/content: nguồn đích (mới hoặc nguồn READY cùng nội dung) đã có frame set READY cùng
  spec/encoding → không tách ảnh. Ngược lại tách tuần tự theo lô `frame_batch_size`: RAM giữ tối đa một lô; file tạm trong workspace attempt ≤ `worker_temp_bytes`,
  kiểm đĩa trống trước mỗi lô; tổng byte ảnh > `max_frame_bytes` → FAILED `upload_invalid`. Mỗi lô **một** transaction kiểm attempt/fence/cancel, giữ thêm đúng tổng
  byte lô trên `USER:<owner>` + `GLOBAL` (hold của job, ngoài `2 × declared` của raw) và ghi receipt INTENT từng object; PUT `ChecksumSHA256` + HEAD tối đa
  `frame_put_concurrency` đồng thời; **một** transaction đánh WRITTEN cả lô. Hủy/stop kiểm giữa các lô; lỗi để receipt cho cleanup §21.13, không xóa prefix.
  Publish cùng transaction fenced với nguồn: chèn frame set + toàn bộ frame rows, ACCEPT receipt raw + ảnh (`accept_selected_in_tx` nhận receipt ảnh của frame set
  được chọn), ghi persisted đúng tổng byte ảnh **một lần**; hold dư trả khi không còn receipt chờ. Upload chỉ `READY` khi raw và đủ bộ ảnh đã xác minh.
  Trùng nội dung khi publish (race): chỉ một frame set READY; bộ thua không được nhận, chờ cleanup receipt rồi mới trả dung lượng.
- **Media (F1).** `POST /media/requests` giữ body/quyền/kiểm unit §21.12; `thumbnail`/`frame` lookup frame set READY của nguồn (đúng `raw_sha256`, `spec_hash`,
  `encoding_version`) theo `frame_idx`: có → `MediaDTO{id = frame.id, state READY, url, expires_at, sha256}`, không ghi DB, không job. `GET /media/requests/{id}` nhận
  id media cache hoặc id frame (kiểm owner/scope/nguồn READY như cũ). Chưa có bộ ảnh → HTTP 200 `state NOT_PREPARED`, `id null`, `job_id` = `PREPARE_FRAMES` đang chạy nếu có;
  client không poll id. `max_side`: `thumbnail` trống hoặc ≤ 320 → `THUMBNAIL`, > 320 → `FRAME` (client tự thu nhỏ); `frame` luôn `FRAME`; không sinh kích thước khác.
  `context` không đổi. `SourceDTO` thêm `frames: PrepState` như `dino`/`clip` (`ready` = có frame set READY; `job_id` = `PREPARE_FRAMES` đang chạy).
  Quy ước casing: DTO API giữ `thumbnail/frame/context` chữ thường; DB/frame manifest dùng `THUMBNAIL/FRAME` (media clip `CONTEXT`);
  đoạn variant trong key S3 dùng chữ thường. Helper `stored_variant` tự chuẩn hóa chữ hoa/thường cho hai loại ảnh,
  áp dụng max_side rồi trả variant DB; từ chối giá trị khác thay vì ngầm chọn FRAME. Lookup dùng cùng chuẩn hóa.
  Không nới validation DTO API hoặc normalize manifest producer sai; bộ ảnh publish sai variant vẫn bị từ chối.
  Worker vẫn xử lý job `RENDER_MEDIA` thumbnail/frame đã có trong hàng đợi (API cũ lúc rollout); API mới không enqueue loại đó.
- **Nguồn cũ và SYSTEM (F2).** `PREPARE_FRAMES` explicit do người dùng bấm, dedup một job active mỗi source/raw/spec/encoding; GET không enqueue. Giữ source ID,
  `resource_version`, feature refs; không làm phiên/feature stale. SYSTEM do operator chuẩn bị bằng CLI dry-run/inventory rồi apply (như import #90), dung lượng chỉ
  tính `GLOBAL`; user không ghi ảnh vào nguồn SYSTEM. Người dùng chuẩn bị bằng endpoint có sẵn `POST /api/v2/sources/{id}/preparations`
  với `feature: "FRAMES"` (cùng Idempotency-Key/receipt/`PreparationDTO` như DINO/CLIP §21.14), không thêm endpoint mới.
- **Xóa nguồn (kéo vào F1 theo người dùng, #119).** `DELETE_SOURCE` inventory gồm raw, artifact và mọi ảnh của frame set chưa DELETED của nguồn. Xóa theo lô
  (100 key/lô, tối đa 8 DELETE+HEAD đồng thời — hằng số code): từ DEP7-Basic-Stability/#121, transaction ngắn kiểm fence/lease/nguồn/pin trước lô,
  commit trước DELETE + HEAD S3 để heartbeat không bị khóa; kiểm stop trước từng I/O và không gửi thêm khi stop. Sau lô transaction mới kiểm lại
  fence/lease/cancel/nguồn/pin rồi mới ghi `confirmed` bền. Lệnh S3 đã gửi không thể thu hồi bởi DB; nếu lease mất/hủy không ghi confirmed/tombstone/quota,
  giữ DELETE_PENDING và đối soát attempt cũ trước retry; keys đóng băng thuộc nguồn bất biến, không tái dùng cho nguồn mới. Lỗi giữa lô giữ `DELETE_PENDING`,
  lần sau xóa lại idempotent. Publish đối chiếu inventory = confirmed, trừ persisted một lần
  (raw + artifact READY + byte ảnh), đánh frame set `DELETED` cùng tombstone nguồn. Object ảnh chưa được nhận do receipt cleanup §21.13 xử lý.
- **Worker (F3).** Process chính chỉ chạy supervisor/reconciliation (+ snapshot ngân sách GPU); CPU, MEDIA và GPU (khi có token, §21.14) mỗi queue một process
  `spawn` chạy `_run_queue(queue, stop, host_boot_id)`. Mỗi process con DB pool/collector riêng, ghi PID/start token của chính nó vào attempt, dùng đúng
  `host_boot_id` do process chính truyền xuống; bỏ qua SIGINT/SIGTERM (dừng theo stop event chung) và tự dừng khi process cha mất. Stop/deadline không trả slot sớm.
  **Process con chết (người dùng chọn phương án tự khởi động lại, #119):** process chính khởi động lại riêng queue đó, chờ tăng dần 1 s → 60 s (×2 mỗi lần);
  quá 5 lần trong 10 phút → process chính dừng mọi process con và thoát mã 3 để Docker `restart: unless-stopped` khởi động lại container. Job của process chết
  phục hồi qua lease/fence/supervisor như cũ (chỉ trả slot khi chứng minh process cũ đã dừng). `process.json` ghi process chính + PID/start token từng process con;
  healthcheck chỉ đạt khi process chính và mọi process con đang cần đều sống. Shutdown của process chính có thời hạn ở từng bước: stop event → join có timeout →
  terminate + join có timeout → kill + join có timeout. Không chờ join vô hạn (Linux child bỏ qua SIGTERM); nếu child vẫn sống phải báo lỗi, hard-exit process chính
  với mã lỗi (không đi qua multiprocessing atexit join vô hạn) và giữ process.json,
  không báo shutdown thành công hoặc nhả slot/hold/usage. Reconciliation vẫn cần bằng chứng PID/start token đúng process đã dừng.
  Cache file theo SHA (`raw-cache`, `model-cache`, `feature-cache`) dùng chung một helper:
  tải vào `.part` riêng, kiểm SHA rồi đổi tên nguyên tử; bản khác đã đổi tên trước thì bỏ `.part` và dùng bản có sẵn; không lock liên process (chấp nhận tải trùng).
- **FE (F4).** Tối đa 4 HTTP media đồng thời; suất chỉ giữ trong lúc gọi HTTP, không giữ khi chờ timer; hàng chờ theo ưu tiên: ảnh/clip đang mở trong viewer
  (0) > ảnh trong viewport (1). Mỗi tham chiếu một request đang bay (dedup); PENDING poll GET receipt với backoff 500 ms → 2 s; `NOT_PREPARED` có `job_id` thì
  POST lại (lookup rẻ, không job) mỗi 2 s → 10 s; ngừng poll khi không còn component dùng tham chiếu đó. Logout/đổi tài khoản (`clearMediaCache`) tăng thế hệ: response
  của thế hệ cũ bị bỏ, không ghi cache (§21.16). `NOT_PREPARED` không phải lỗi tải: ô ảnh hiện "Chưa chuẩn bị ảnh" + nút chuẩn bị (hoặc "Đang chuẩn bị…" khi có job);
  Kho dữ liệu có huy hiệu/nút "Chuẩn bị ảnh" cạnh DINO/CLIP từ `SourceDTO.frames` (người dùng chọn cả hai chỗ, #119). Nguồn SYSTEM chưa có ảnh: chỉ hiện trạng thái,
  operator chuẩn bị.
- **Dọn cache đĩa worker (F4, người dùng chọn làm trong DEP7-Frames).** Cache theo SHA trong `RAV_PILOT_WORKDIR` (`raw-cache`, `model-cache`, `feature-cache`)
  có trần tổng `RAV_PILOT_CACHE_MAX_BYTES` (env worker, số nguyên > 0; không đặt = không dọn, giữ hành vi cũ). Cache hit cập nhật mtime (LRU); sau mỗi lần
  tải mới, nếu tổng vượt trần thì xóa file cũ nhất theo mtime cho tới khi dưới trần, bỏ qua file vừa dùng và file có mtime trong 10 phút gần nhất (job khác có thể
  sắp mở), bỏ qua file không xóa được (Windows: đang mở). `.part` bỏ dở quá 24 giờ bị xóa. Không đụng workspace attempt hoặc thư mục khác.
- **Hướng tới (người dùng chọn giữ, #119).** Artifact MEDIA thumbnail/frame do `RENDER_MEDIA` tạo trước DEP7-Frames không còn được dùng nhưng vẫn tính dung lượng;
  chưa dọn trong lát này (sẽ dọn bằng job/CLI có dry-run sau); xóa nguồn vẫn xóa chúng như artifact khác.
- **Số đo (F4, chỉ local theo người dùng).** Cold/warm, nhiều lượt, p50/p95 trên PostgreSQL local + moto server + process worker spawn thật; ghi rõ không phải số
  cloud và không suy ra mức tăng tốc trên cloud.
- **Kiểm thử.** CFR/VFR/fps thấp/EOF khớp ordered IDs; dedup/race, hold theo lô, hết disk/storage, cancel/crash/lease lost/retry — không publish partial, không trả
  hold sớm, không xóa ảnh đã nhận, không tính byte hai lần; media A/B owner, nguồn DELETE_PENDING, thiếu ảnh không decode; PostgreSQL thật cho transaction/race/RLS.

### 21.21 DEP7-Basic-Stability — luồng cơ bản trước mở rộng tham chiếu (#121)

- Người dùng duyệt cơ chế sau giải thích: S3 delete không giữ khóa transaction suốt I/O; SYSTEM import có reservation/receipt trước PUT.
  Không đổi sampling/core/cache semantics, ownership, raw, quota hoặc PII. Lát này không thêm dataset/model management.
- **SYSTEM frame import:** CLI dry-run/hash giữ nguyên. Apply kiểm policy version có thật/enabled, source SYSTEM READY/raw/spec, bounds/disk trước PUT.
  Encode local có giới hạn bằng exact RGB, đếm byte thực; không ghi S3 khi policy/nguồn/bounds sai. Mỗi source/raw/spec/encoding một receipt idempotent trong
  `rav_pilot_import_receipt`, dùng mapping/counts/hashes hiện có; không thêm queue/service/bảng. Session advisory lock theo receipt/source ngăn importer đồng thời.
  Receipt STAGING lưu source/raw/spec/encoding/frame_set_id, danh sách key/sha/byte chính xác và process identity/host/start token trước I/O;
  reservation GLOBAL đúng tổng byte phải commit cùng receipt trước PUT. Không tạo USER account hoặc tính SYSTEM vào USER quota.
  Publish đủ bộ cùng transaction: kiểm source/policy/receipt/object SHA/size, insert frame_set/frame, GLOBAL reserved→persisted đúng một lần, receipt COMMITTED.
  Nguồn đã có bộ READY hợp lệ → no-op, không PUT lại hoặc tính byte trùng.
- **SYSTEM lỗi/phục hồi:** PUT lỗi hoặc DB publish lỗi không xóa ledger/nhả reservation trước cleanup DELETE+HEAD theo đúng key chưa nhận.
  Receipt FAILED/STAGING giữ inventory và reserved bytes khi cleanup chưa xong; chạy lại inventory được operator-confirm trước hết đối soát receipt cũ.
  Không overwrite/cleanup khi process trước còn sống hoặc chưa chứng minh dừng; không coi mất advisory connection là bằng chứng process đã dừng.
  Nếu đã COMMITTED/READY, không cleanup ảnh đã nhận. Partial cleanup có thể chạy lại; reserved nhả đúng một lần sau mọi key đã xác nhận vắng.
  Recovery có thể yêu cầu operator xác nhận bằng chứng cho process ở host khác; không tự nới quyền hoặc quét/xóa prefix.
  Dry-run bao gồm receipt chưa cleanup, kể cả khi source/raw/policy hiện không còn cho phép import. Operator-confirm khóa inventory recovery;
  `--cleanup-only` chỉ đối soát receipt cũ, không cần policy enabled và không PUT/publish mới. Giữ advisory lock, proof writer dừng và guard READY;
  receipt thay đổi sau dry-run phải từ chối. Apply mới vẫn cần policy/source hợp lệ. Kiểm raw ≤ temp budget trước download và byte encode trước ghi file tạm.
- Test bắt buộc: heartbeat cập nhật lúc S3 bị chặn; cancel/lease lost giữa I/O và ghi confirmed; missing/disabled policy và hết GLOBAL không PUT;
  PUT/DB lỗi, process crash/recovery, hai importer, idempotency/reserved/persisted đúng và READY không bị cleanup.
  Shutdown Linux thật (SIGTERM ignored → SIGKILL), Windows spawn, PostgreSQL thật + S3 mock, E2E local và đo cold/warm; không claim cloud speed.

### 21.22 Compute runtime — model owners và CPU lanes (#127)

- Phần cứng Contabo đã xác minh: 4 vCPU/~8 GiB RAM; container live cũ giới hạn 3 vCPU/4 GiB, không là capacity đầy đủ.
  Lát runtime triển khai CPU lanes opt-in và warm model ownership; PII/export còn Hướng tới §21.8, không tạo handler giả.
- `RAV_PILOT_CPU_LANES` là JSON list `{name, kinds, slots, threads, rss_bytes}`: name unique uppercase không trùng CPU/MEDIA/GPU;
  kinds là partition đầy đủ của CPU job kinds hiện có, slots CPU không trùng giữa lane; threads/rss dương.
  `RAV_PILOT_RUNTIME_CPU_THREADS`/`RAV_PILOT_RUNTIME_RSS_BYTES`/`RAV_PILOT_RUNTIME_AUX_RSS_BYTES` là budget explicit;
  tổng threads lane + một thread MEDIA không vượt CPU budget, tổng RSS lane + aux không vượt RSS budget.
  Không đặt lanes giữ runtime cũ tương thích. Không tự thêm slot/policy trên DB production.
  Opt-in preflight kiểm CPU/memory cgroup-v2 khi có; budget vượt container phải fail trước claim. Native deployment cần
  systemd slice/cgroup hard cap tương đương; budget env không tự tạo giới hạn OS hoặc thay admission của DB.
- Mỗi lane là process spawn sống lâu, một attempt/lane; DB queue vẫn CPU, claim chỉ kind+slot của lane và inventory policy job.
  CAS/fairness/cursor/lease/fence/quota/cleanup không đổi. Slot/policy thiếu → preflight fail hoặc không claim job policy chưa cho slot đó.
  Claim CPU/GPU kiểm `heavy_active_per_user` từ policy snapshot dưới khóa profile; HELD/QUARANTINED non-MEDIA slots của owner
  đều tính vào giới hạn, kể cả job WAITING_RESOURCE/UNKNOWN chưa chứng minh dừng. MEDIA vẫn giới hạn riêng.
  process.json/health/restart/shutdown theo identity lane; MEDIA/GPU vẫn process riêng. Config sai phải lỗi trước claim.
- Dispatcher claim hỗ trợ kind/slot allowlist tùy chọn, chỉ subset của queue; không cho CPU claim GPU/MEDIA.
  Watch RSS process (gồm model resident) theo min(policy worker_rss_bytes, lane rss_bytes), stop cooperative rồi shutdown deadline như trước;
  không refund/release slot từ việc chỉ gửi tín hiệu. Aux budget chừa supervisor/MEDIA/GPU và cache/native overhead;
  container/cgroup tổng vẫn là hard cap, RSS watcher không thay capacity benchmark hoặc hard memory control.
  Aux RSS phân supervisor 1/6, MEDIA 1/3, GPU 1/2; quá RSS cả khi idle phải restart để nhả resident cache,
  vẫn shutdown/reconcile đúng identity, không âm thầm giữ model vượt budget.
- Trước ML imports, lane đặt OMP/MKL/OpenBLAS/decoder thread budget. Text model owner là process PROPOSE lane;
  YOLO owner là lane chứa YOLO_BATCH. Cache model YOLO một entry/process keyed checkpoint digest/runtime;
  raw SHA phải kiểm khi cache lookup, không dùng object đã mutable/replaced. Calls cùng process serialize model use,
  không share mutable model giữa process; idle resident cache chịu RSS budget.
- CLIP text dùng nhánh text+projection cùng checkpoint/revision/tokenizer, inference float32 + L2 như trước;
  vision encoder local/image prepare giữ nguyên. Chỉ chuyển khi parity test thật đạt; không trộn CLIP image embedding với SigLIP text.
- SigLIP2/Gemma4 là research/benchmark, chưa đổi model mặc định/FieldKey/threshold/artifact hoặc tự chạy GPU.
  Thay model cần paired text+vision version mới, re-prepare và đánh giá semantic; PII concurrency thật chỉ đạt sau handler/model/quality gate.
- Docker Linux không thêm VM mỗi lane; native systemd có thể chạy cùng entry/supervisor với identity/permissions/budgets/venv pin tương đương.
  Candidate test namespace riêng trên Contabo trước rollout; không tự thay service live hoặc push Git.

### 21.23 N1-DB — batch commands, read paths và SQL pipeline (#129)

- Người dùng duyệt implement và test local trước quyết định deploy. Một lát gồm draft/submit, đọc/poll job, batch ảnh và SQL publish;
  không tự mở PII/reference, đổi quota/sampling hoặc thêm DB replica/stored function/cache service.
- Pilot Studio keep/drop/clear/undo là draft RAM phiên, không request ghi; reload/rời phiên/logout xóa draft.
  Counts/tập đã lưu/snapshot phản ánh committed state; có draft phải chặn snapshot/export, không autosave khi chuyển tab.
  Trong submit/job active khóa quyết định; local v1 giữ autosave cũ. Draft lỗi validation/quota/CAS còn để sửa/retry.
- POST proposals thêm `changes: list[DecisionChange]` optional (tối đa 500); một transaction khóa profile→sources→session,
  kiểm input/features/CAS → batch append decisions, tăng version một lần nếu đổi → freeze seq/version → holds/pins/enqueue.
  Enqueue lỗi rollback cả decisions; enqueue đã commit thì decisions giữ dù worker lỗi. Không job phụ để lưu decisions.
  Replay kiểm trước CAS, cùng key/body trả cùng job, body khác 409; compatibility job cũ thiếu submission hash giữ replay cũ cùng phiên.
  JobDTO thêm `accepted_state_version` nullable từ frozen PROPOSE payload để client cập nhật version đã commit.
- GET job/list/result và GET media/lookup ảnh không FOR UPDATE profile/source. Vẫn kiểm active/owner/scope/READY;
  authorization read theo committed snapshot lúc kiểm, disable đồng thời có thể hoàn tất request đã được kiểm trước commit disable,
  request sau commit disable bị từ chối. TTL capability ảnh đã ký vẫn theo policy; không thu hồi bytes/URL đã cấp bằng bỏ row lock.
- GET jobs thêm filters kind/resource_id; discovery phiên lọc server + phân trang, sau enqueue poll GET đúng job id,
  một chain có backoff 1→2→3 s, dừng terminal/unmount/logout; result lấy một lần khi SUCCEEDED, không fetch toàn list mỗi poll.
- POST `/api/v2/media/batch` body `{items: list[MediaRequestIn]}` 1–200, chỉ thumbnail/frame, có cap max_media_requests policy.
  Một batch đọc profile/policy/nguồn/ảnh READY theo lô, kiểm exact grid, owner/SYSTEM/source status/spec/SHA/encoding như từng request.
  Response list MediaDTO cùng thứ tự input, NOT_PREPARED kèm preparation job nếu có; không ghi/enqueue/decode, không trả object_key.
  Invalid/unauthorized item từ chối cả batch, không trả partial URL. Browser coalesce ảnh trong viewport, refresh TTL qua batch;
  context giữ endpoint riêng. Cache/dedup/generation/cancel rules §21.20 giữ nguyên.
- Pipeline chỉ gom lệnh chưa cần fetch intermediate; gom read aggregates/checks khi giữ cùng lock/snapshot semantics.
  Không suy số SQL thành round trips; trace db.execute/db.enqueue/db.pipeline và wall/CPU riêng, không payload/SQL/secrets.
  Publish vẫn atomic/idempotent: fence/lease/cancel/context + usage/receipt/refund/hold/pins/close; UNKNOWN/pending cleanup không release sớm.
  Tests PostgreSQL thật lock-read/A-B/inactive/CAS/replay/body mismatch/rollback/quota/fault; UI zero writes→one submit,
  poll chain/batch refresh/expired URLs/logout + local regression. Benchmark local cùng input nhiều lượt ghi rõ không cloud RTT/SLA.

### 21.24 N1-Publish-RPC — PROPOSE normal-path publication (#131)

- Người dùng duyệt mục 1 gom publish/tất toán; không đổi quota CPU, UI hoặc thiết kế phiên tạm trong lát này.
- Migration 011 thêm `public.rav_pilot_publish_propose(jsonb,jsonb,jsonb,jsonb)` SECURITY INVOKER, search_path cố định;
  EXECUTE chỉ worker (owner/operator giữ quyền). API/anon/authenticated không được gọi.
- Worker phát hiện capability một lần mỗi runtime; DB chưa có 011 dùng đường Python đầy đủ hiện tại.
  011 staged additive: readiness chấp nhận thiếu 011 khi fallback còn hỗ trợ, checksum sai vẫn chặn.
- Đường chuẩn PROPOSE (reservation đủ, không GPU hold/storage hold/object chưa hoàn tất) dùng một SELECT gọi hàm;
  DB giữ thứ tự profile → job → attempt → slot → session; kiểm worker/fence/generation/lease, cancel và frozen basis.
  Ghi proposal, CPU/GPU receipts (kể cả zero), consume hold theo parts/FIFO, trả dư đúng window/bonus, pins,
  job SUCCEEDED/attempt CLOSED/slot FREE hoặc QUARANTINED theo cleanup flag trong cùng transaction.
- Deficit cần reserve bổ sung, GPU/storage hold hoặc object inventory dùng đường legacy đầy đủ trong cùng transaction;
  hàm trả fallback trước mutation. Không bỏ quota, UNKNOWN reconciliation hoặc pending cleanup.
- Replay kiểm trước context/output validation; response lost/concurrency cùng attempt vẫn một proposal/receipt.
  Lỗi context/result/constraint rollback cả accounting và publish. Worker chuẩn hóa proposal/hash ngoài DB như hook cũ;
  DB xác minh frozen basis và source mapping với payload. Không ML/S3/network trong hàm.
- Đếm lời gọi client khác số SQL bên server. Kiểm local trước deploy, không claim cloud 1–3 s trước số đo.

### 21.25 Claim RPC và bật CPU lanes (#132)

- Người dùng yêu cầu mục 2 + bật lanes. CPU/MEDIA claim có additive migration 012, worker-only SECURITY INVOKER
  `rav_pilot_claim_local(jsonb,text,text[],text[],jsonb)`; GPU admission vẫn legacy đầy đủ.
- Giữ cursor round-robin theo queue, SKIP LOCKED profile/job/slot, frozen policy kind/slot allowlist,
  owner heavy limit gồm HELD/QUARANTINED, deadline/UNKNOWN/source checks, attempt fence/generation.
  Source không READY trả fallback để terminate/quota/storage hooks cũ xử lý, không publish giả.
- Claim ghi identity process và trả policy/queue timing trong cùng transaction. Lease có metadata nội bộ optional;
  không trộn vào payload job/DTO FE. DB chưa 012 dùng legacy tương thích. Capability kiểm một lần/runtime.
- Dispatcher không đọc lại policy hoặc record_process khi claim đã cung cấp; handler_stopped+usage vẫn ghi riêng
  trước publish/finish để supervisor có proof kể cả publish rollback/lease lost. Không bỏ bước recovery để giảm query.
- 012 staged additive như 011: thiếu thì readiness/fallback vẫn được, checksum lệch chặn. Operator apply riêng sau backup.
- Rollout lanes: test cả PROPOSE/YOLO_BATCH/VALIDATE_UPLOAD và MEDIA dưới budget trên namespace DB/S3 riêng;
  cấu hình policy version mới, CPU slots TEXT/YOLO/BULK, drain/compatibility và rollback giữ dữ liệu/receipts.
  Giữ heavy_active_per_user hiện có trừ khi capacity chứng minh cần đổi; không mở PII/export hoặc tự chạy Kaggle.
  Model vẫn lazy resident; startup warm-up là mục 4 riêng. User đã duyệt bật lanes, numeric budget chọn theo số đo.

### 21.26 Interactive PROPOSE và CPU không quota (#133, thay phần tương tác §21.15/23)

- User duyệt Render → Contabo HTTP synchronous; PROPOSE không job/slot/quota hold/usage receipt.
  Render xác minh JWT, profile active cache tối đa 15 s riêng interactive; Contabo nhận owner từ service credential,
  không owner do browser cung cấp. HTTPS certificate verify + token riêng; HTTP chỉ loopback cho test/dev.
- TEXT process trở thành FastAPI interactive owner khi bật env; model text warm-up một lần, RAM cache phiên
  và artifact immutable có trần bytes/LRU/TTL. Không nhân bản owner model, giữ tổng cgroup4CPU/6GiB.
  Warm proposal không đọc DB/S3/raw/probe; ctx units từ metadata đã validate + artifact ids/keys/version.
  Cache source/session/profile revalidate tối đa 15 s; disable/delete/CAS từ DB có hiệu lực sau cửa sổ này.
- Browser giữ full kept/drop draft, history proposal tạm; Contabo kiểm unit/targetK/config, 1 request/user,
  tối đa 2 active requests toàn service, bận trả429 Retry-After. Cache request_id+payload hash bounded TTL,
  retry cùng input trả cùng batch; key khác input409. Cache hết/restart dựng lại checkpoint, không durable retry SLA.
- POST /sessions/{id}/interactive/open; POST .../interactive/propose trả proposal+batch_id+URLs ảnh sẵn;
  POST .../checkpoint theo CAS/idempotency lưu decisions + current proposal do service xác nhận + recipe/config/seed/basis.
  Browser không ghi proposal/history do tự sửa vào DB. Save kiểm source version và actor/session hiện tại trong một transaction.
  DB lịch sử proposal cũ giữ, ephemeral rounds có thể mất. YOLO/analysis/snapshot yêu cầu lưu current batch trước.
- CPU accounting_enabled=false trong policy mới: không reserve/consume/receipt CPU cho mọi kind;
  usage CPU vẫn telemetry/recovery, không hạn mức. GPU holds/receipts/storage/fence/cleanup giữ.
  Jobs có policy cũ tiếp tục tất toán theo snapshot cũ; default true khi thiếu flag. Policy config không sửa tại chỗ.
- Upload/prepare/delete/YOLO/GPU jobs vẫn durable; interactive độc lập polling, không đổi thuật toán/embedding space.
  Giữ mode legacy khi chưa cấu hình service để rollout/backward compatibility; không âm thầm fallback sau lỗi HTTP.
  Checkpoint mutation tự động khi đề xuất tiếp hoặc trước tính năng durable; local v1 giữ như cũ.
- Gate: owner/secret/CAS/retry/cache eviction/restart/CPU-free/GPU regression, FE không poll job/ghi từng quyết định;
  focused cuối implementation, full suite một lần trước release, local/VPS warm p95 đo riêng cold, không hứa SLA trước đo.

Điều chỉnh đã duyệt: bỏ nút Save khỏi luồng chính. POST interactive/propose có previous_batch_id;
Render lưu batch hiện tại + full decisions bằng checkpoint RPC một client call/transaction, rồi tính batch mới.
Cache nhận checkpoint version mới trong request compute, không invalidate/reload embeddings. Retry request_id replay
checkpoint trước CAS; lỗi compute sau Save trả checkpoint trong error để FE áp dụng phần đã lưu. Batch mới còn tạm
cho tới lượt đề xuất kế; YOLO/analysis/snapshot lưu current batch như prerequisite tự động. GPU/storage giữ nguyên.

Điều chỉnh payload theo người dùng 2026-10-16: response public proposal (interactive, queued, đọc/list/reload và local API)
chỉ trả scores/evidence của picks trong batch, không trả bảng điểm hay danh sách ID của toàn bộ candidates.
`return_scores` nghĩa là các cột điểm của batch; pipeline có thể tính/lưu toàn bộ candidates nội bộ để audit/retry.
Trace/execution public giữ summary/count, không gửi candidate_ids hoặc source_hashes toàn pool; frozen input gốc ở backend không bị sửa.
Checkpoint trong response interactive chỉ mang batch đã chốt cần đồng bộ UI và review state, không kèm bảng scores;
evidence chỉ per-pick, không trả envelope nội bộ (scores/interactive_basis). CAS/idempotency/proof dùng bản đầy đủ nội bộ.
Review state kept/dropped là dữ liệu đã duyệt của phiên, không là danh sách mọi unit chưa duyệt của pool.
Timeline ghi rõ điểm chỉ thuộc batch; không diễn giải mẫu không có điểm là score0.


### 21.27 Upload lớn và giới hạn dữ liệu đồng bộ (#134)

- User duyệt multipart cho file ≥100MiB khi policy multipart_upload_enabled=true; mặc định thiếu flag giữ POST cũ.
  API tạo upload/USER+GLOBAL reservation trước mọi S3 write. Staging key do server dựng; frontend không nhận AWS credential.
  Migration additive015 thêm transfer_method POST|MULTIPART và multipart_id; upload state machine giữ §21.12.
- POST /uploads/{id}/multipart khởi tạo idempotent; GET cùng route trả parts đã ghi trên S3; POST .../multipart/parts
  nhận part_numbers (tối đa16), trả signed PUT từng part. Part16MiB (tăng nếu cần để≤10000 parts), browser4 PUT đồng thời,
  retry tối đa3 lần/part, timeout120s; retry chỉ part hỏng. Chọn lại cùng file sau reload tiếp tục parts đã xong;
  localStorage chỉ owner+fingerprint(file name/size/mtime)+upload/idempotency id, không file/token/URL ký.
- API xác thực active owner/state/expiry trên mỗi command. Session advisory lock riêng upload serialize init/sign/complete/abort,
  S3 I/O ngoài transaction/khóa profile; lock cùng connection autocommit, try-lock bận429. MPU id lưu DB;
  init bị crash giữa S3 và DB phục hồi bằng ListMultipartUploads đúng staging key, không tạo lặp hoặc xóa prefix.
  Complete lấy ListParts từ S3 (không tin ETag/part list client), kiểm đủ đúng part và byte, assemble→HEAD exact declared→enqueue như cũ.
  Retry sau assemble/lost response HEAD thấy object và tiếp tục enqueue, không tạo job trùng.
- Abort/expired staging: abort mọi MPU **đúng key** rồi DELETE/HEAD; chỉ settle reservation khi sạch và capability đã hết hạn+grace.
  Multipart start/sign/complete và sweeper dùng cùng advisory lock để không cấp URL/assemble sau cleanup.
  Quyền S3 cần PutObject, AbortMultipartUpload, ListMultipartUploadParts và ListBucketMultipartUploads giới hạn namespace;
  CORS thêm PUT cho origin FE đang có, giữ cấu hình khác. URL ký ngắn theo capability; tăng capability policy cho file lớn.
- Canonical file≥100MiB dùng bounded SDK multipart16MiB/4 threads, checksum parts + metadata full SHA256 đã hash stream;
  HEAD size/metadata đối soát; fullSHA lại khi đọc cache/artifact vẫn giữ. Cleanup receipt lớn abort MPU đúng key trước DELETE;
  không nhận ETag multipart làm SHA256. SHA/core IDs/4fps exact anchors/encoding version không đổi.
- Tăng policy đồng bộ max_video_bytes, duration, total decoded pixels (budget CPU, không phải RAM giữ đồng thời),
  storage_user/global, run_deadline, max_frame_bytes, PROPOSE.max_units và feature output reservation theo phép đo.
  Mục tiêu capacity file tới5GiB/60phút, pool tới20000 unit; số live chỉ provision sau test/đo, không tăng vô hạn.
  Raw/video decode streaming, JPEG theo lô, giới hạn RAM/cgroup4CPU/6GiB, disk/cache giữ. Upload chỉ READY khi raw+đủ ảnh.
- UI phân biệt truyền bytes/preparing/READY, không báo FAILED khi hết vòng polling mà job vẫn chạy;
  refresh/resume theo upload ID, không đọc toàn file vào ArrayBuffer. Lỗi hiển thị giới hạn dễ hiểu.
- Gate: multipart owner/replay/part missing/size/cancel/orphan cleanup; POST compatibility; real hai video đầy đủ không cắt,
  CPU/RSS/frame counts/SHA và timings, interrupt/resume browser; kiểm ký/part count sizeGiB riêng không coi là fullGiB ingest nếu chưa truyền.
  Feature/GPU semantic quality và dataset/model catalog vẫn là lát khác.

#134 cleanup receipt: process-stopped proof và chuyển INTENT/WRITTEN→DELETE_PENDING trước I/O;
state này không thể được accept/publish. DELETE/HEAD exact keys ngoài transaction, tối đa4 I/O và64 receipt/lô;
ghi DELETED chỉ sau xác minh sạch, giữ partial receipt khi lỗi; release canonical khi hết pending. Không giữ profile lock chờ S3.

#134 cleanup advisory lock theo attempt serialize hai supervisor (chờ tối đa5s, bận giữ pending để retry); dùng lại connection autocommit, không chặn profile trong S3 I/O.


### 21.28 Tiến độ xử lý chi tiết và ETA thực tế (#135)

- **Mục tiêu**: Thay thế trạng thái xử lý tĩnh (đang tiền xử lý video) bằng telemetry minh bạch cho các tác vụ worker chạy dài (đặc biệt `VALIDATE_UPLOAD` và `PREPARE_FRAMES`), cung cấp pha xử lý chuẩn hóa, tiến độ frame/unit cụ thể, tốc độ xử lý fps tức thời và thời gian còn lại ước tính (ETA) dựa trên số đo thực tế.
- **Kế toán CPU**: Theo §21.26, policy mới (`accounting_enabled=false`) không áp quota/hạn mức CPU. Tiến độ và thời gian xử lý phục vụ trực quan hóa cho người dùng và giám sát tải; quota/hold chỉ áp dụng cho GPU và Storage.
- **Pha xử lý chuẩn hóa (Phase)**:
  - `DOWNLOADING`: Tải video raw từ vùng lưu trữ tạm (S3/staging) về worker.
  - `PROBING`: Xác thực định dạng, SHA-256, codec, độ phân giải và giới hạn pixel giải mã.
  - `SAVING_CANONICAL`: Ghi video canonical vào kho lưu trữ riêng tư của người dùng trên S3.
  - `EXTRACTING_FRAMES`: Giải mã tuần tự, trích xuất lưới unit 4fps, encode JPEG FRAME và THUMBNAIL, và upload S3 theo batch.
  - `FINALIZING`: Hoàn tất bộ ảnh và xác nhận trạng thái READY trong cơ sở dữ liệu.
- **Công thức tính tốc độ (fps) và ETA**:
  - Trong pha `EXTRACTING_FRAMES`:
    - `elapsed_s = now - extraction_start_time`
    - `fps = round(units_done / max(elapsed_s, 0.001), 2)`
    - `remaining_units = max(0, total_units - units_done)`
    - `eta_s = round(remaining_units / max(fps, 0.1), 1)` khi `units_done > 0`; khi chưa đủ dữ liệu hoặc đang khởi động thì `eta_s = None`.
  - Trong các pha khác: `eta_s = None` (giao diện hiển thị "Đang xử lý..." hoặc tên pha tương ứng).
- **Cơ chế telemetry và Database**:
  - Migration additive `016_pilot_progress.sql`: Bổ sung cột `progress_detail JSONB NOT NULL DEFAULT '{}'::jsonb` vào bảng `rav_pilot_job`.
  - Cập nhật định kỳ: Handler worker cập nhật tiến độ (`phase`, `done`, `total`, `fps`, `elapsed_s`, `eta_s`) theo từng lô frame thông qua liveness heartbeat hoặc callback tiến độ.
  - Bảo vệ liveness heartbeat: Heartbeat ping định kỳ không làm giảm `done` hoặc ghi đè lùi tiến độ nếu handler đã báo cáo tiến độ cao hơn.
- **API & DTO Contract**:
  - `JobDTO.progress`: `{ "done": int, "total": int, "fps": float | null, "elapsed_s": float | null, "eta_s": float | null }`.
  - `UploadDTO`: Bổ sung trường `progress: dict[str, Any] | null = None`. Khi upload ở trạng thái `VALIDATING`, API `GET /api/v2/uploads/{id}` tự động làm giàu trường `progress` từ validation job để frontend chỉ cần poll một endpoint duy nhất.
- **Giao diện người dùng (Frontend)**:
  - Hiển thị thanh tiến độ thực tế theo tỷ lệ `done / total` trong pha trích xuất ảnh.
  - Hiển thị tên pha bằng tiếng Việt rõ ràng cùng thông số tốc độ (`fps`) và thời gian còn lại định dạng dễ đọc (vd: "~45s", "~1m 20s").
  - Quota GPU và Storage hiển thị độc lập, không gộp với thời gian chờ xử lý video.

### 21.29 Bỏ chặn Storage Quota và Kế toán Dung lượng Độc lập (Lát 1)

- **Cờ cấu hình chính sách (Policy Config)**:
  - Bổ sung cờ `storage_accounting_enabled: boolean` vào quota policy config. Mặc định `true` khi thiếu flag để tương thích ngược 100% với các policy hiện hành.
  - Khi `storage_accounting_enabled=false`:
    - Cho phép `storage_user_bytes: 0` và `storage_global_bytes: 0` trong cấu hình quota (không bắt buộc số nguyên dương).
    - Tắt hoàn toàn việc kiểm tra hạn mức dung lượng USER và GLOBAL. Không bao giờ phát sinh mã lỗi HTTP 429 `storage_exceeded` từ việc kiểm tra quota tài khoản.
    - Byte mới ghi theo policy này không cập nhật `reserved_bytes` hay `persisted_bytes` trên bảng `rav_pilot_storage_account`; khi xóa byte cũ đã tính quota vẫn khấu trừ theo receipt gốc bên dưới.
- **Bảo toàn Hold, Biên nhận và Quản lý vòng đời (Lifecycle & Tracking)**:
  - Vẫn tạo các bản ghi `rav_pilot_storage_hold` (`reserved_bytes`, `receipt_key`, `upload_id`/`job_id`) gắn kèm cờ `storage_accounting_enabled=false`.
  - Mục đích giữ hold: duy trì tính idempotent của upload/job, theo dõi dung lượng thực tế của file để giám sát hạ tầng và phục vụ cơ chế dọn dẹp (cleanup/abort).
  - Khi hoàn tất upload hoặc kết thúc job (`settle`, `release_job_holds`), bản ghi hold được cập nhật `persisted_bytes` và chuyển trạng thái sang `FINAL` bình thường mà không gây ảnh hưởng đến tài khoản dung lượng.
- **Tương thích ngược và Đóng băng Job Snapshot (Immutability)**:
  - Migration additive `019_pilot_storage_optional.sql`: Thêm cột `storage_accounting_enabled boolean NOT NULL DEFAULT true` vào bảng `rav_pilot_job` và `rav_pilot_storage_hold`, kèm trigger bảo vệ tính bất biến sau khi tạo.
  - Các job và upload mang policy cũ (`storage_accounting_enabled=true`) tiếp tục áp dụng đầy đủ quy tắc kế toán dung lượng nghiêm ngặt theo snapshot lúc tạo.
- **Xóa nguồn độc lập với Accounting (`pilot_delete`)**:
  - Khi xóa nguồn (`DELETE_SOURCE`), xác định byte đã tính quota từ receipt gốc, không dùng policy của job xóa: raw theo hold của upload đã tạo nguồn (`reserved_source_id = source_id`), artifact và frame set theo snapshot cờ của job đã tạo chúng. Dữ liệu legacy/import không có job/upload gốc giữ semantics đã tính quota (`true`).
  - Chỉ khấu trừ tổng byte đã tính quota của đúng raw/artifact READY/frame set chưa DELETED trong inventory; không dùng `min(account.persisted_bytes, physical_bytes)`. Một nguồn có thể chứa cả phần được tính và không được tính quota.
  - Tổng byte đã tính bằng 0: không đọc/khóa/cập nhật tài khoản USER/GLOBAL. Tổng dương: khóa và trừ đúng số đã tính trên hai tài khoản, thiếu ledger vẫn trả `409 storage_reconcile_required`. Đổi policy bật/tắt sau khi ghi không thay nghĩa receipt cũ.
  - Receipt xóa giữ `released_bytes` là byte vật lý; thêm `accounted_released_bytes` là byte thực tế khấu trừ ledger. Replay không trừ lần hai. Gom tra cứu receipt gốc trong một SQL tổng hợp, không truy vấn DB từng object.
  - Cơ chế dọn dẹp staging (`sweep_staging`), abort multipart và thu hồi pin nguồn tiếp tục hoạt động độc lập, không phụ thuộc vào trạng thái quota.

### 21.32 Strategy chọn mẫu — orchestration do thuật toán sở hữu (#141)

Thiết kế được người dùng duyệt 2026-10-15: Strategy là thuật toán hoàn chỉnh, không bị ép theo khuôn
reference/objectives/combiner/selector. Recipe/component cũ là implementation có thể tái sử dụng.
Strategy nhận pool hợp lệ, context tài nguyên đã xác minh, state Giữ/Bỏ, batch size, seed và parameters có schema;
trả proposal, per-pick evidence, điểm tùy chọn và trace các bước. Strategy tự quyết định thứ tự/bước lọc/tính field,
không đọc DB/HTTP hoặc tự cấp quyền/tài nguyên. Hạ tầng giữ owner/RLS/CAS, snapshots, queue/lease/cancel và publication.

- Registry Strategy có name/version, title/description, steps, inputs (model/reference_dataset), requirements về
  prepared fields và execution mode, schema/defaults của parameters công khai. API nhẹ đọc catalog sinh sẵn;
  worker dispatch theo registry. Không rẽ nhánh theo tên strategy để gắn model/route job/check feature requirements.
- Giữ tên wire `recipe` và config của phiên cũ để tương thích; bổ sung `strategy_params` optional, validate server-side,
  đóng băng parameters đã resolve cùng strategy version và model/source identity. Retry/reload dùng snapshot đó.
  Không xóa dữ liệu phiên hoặc di chuyển decisions. Recipe cũ được adapter thành strategy, giữ hành vi đang có.
- Metadata API thêm `strategy: {name, version, title, steps: string[], inputs: {model: boolean,
  reference_dataset: boolean}, parameters_schema: object, defaults: object}`. DTO cũ vẫn đọc được.
- Lát #141 thêm strategy `context_uncertainty`: CLIP relevance + DINO diversity trên pool−kept−dropped →
  bounded candidates (candidate_multiplier 2 hoặc 3, trần 96) → YOLO original/flip requested-only →
  chọn batch giữ ngữ cảnh, instability và diversity. Public params gồm câu tìm/loại trừ và candidate_multiplier;
  model detect INFERENCE_VERIFIED bắt buộc, dataset train không bắt buộc. Không suy mục tiêu phiên thành câu CLIP.
  Không inference trong HTTP interactive, không tự prepare DINO/CLIP; không nới các training-provenance gates.
- Chưa đo/không detection là thiếu tín hiệu, không là model đúng; không tạo mAP/uplift. Bảo toàn evidence gốc/lật,
  progress/cache/cancel hiện có. Trace nói rõ phạm vi candidates; không claim bất định lớn nhất toàn video.
- UI chính: strategy title/steps, inputs, public parameters, batch size; JSON/internal component config chỉ ở
  technical details. Phân biệt cấu hình cho đợt tiếp theo với immutable strategy/config của batch đang xem.
  Các raw field như det.yolo_consistency/combiner không là ô người dùng phải sửa.
- Verification: native custom Strategy không cần RecipeConfig; adapter regression, stage order/requested-only,
  exclusion Giữ/Bỏ, schema validation, snapshots/retry/cache, pilot routing, frontend restore và browser acceptance.
  Không migration, không tự push/deploy; acceptance semantic quality/active-learning uplift là gate độc lập.
- Browser acceptance local đạt 2026-10-16 (Chromium headless, video dashcam thật, YOLO11n): chọn strategy/model/params ở sidebar,
  job nền, loại Giữ/Bỏ, khôi phục cấu hình sau reload. Hai bước chọn của `context_uncertainty` dùng `greedy_nms` relax mặc định
  như candidate stage cũ: chỉ nới khoảng cách thời gian/embedding, không nới mask (cổng CLIP, YOLO observed); `relaxed` được ghi.
  Màn Phiên mới vẫn chỉ liệt kê recipe cũ; strategy native chọn trong Studio. Chưa deploy (người dùng quyết định).

### 21.31 Chọn Mẫu theo Độ Thiếu Ổn Định của Model Perception (Perception Instability)

- **Mục tiêu & Bản chất**:
  - Tuyển chọn frame từ video dựa trên độ thiếu ổn định suy luận (uncertainty proxy / TTA consistency) của mô hình perception (YOLO detection) mà không cần dataset train tham chiếu và không cần quan hệ provenance.
  - Tách biệt hoàn toàn với `model_guided`: không dùng `training_kept` hay `reference_novelty`.
- **Cấu hình Recipe `perception_instability`**:
  - `name`: `"perception_instability"`
  - `reference`: `{"type": "session_kept"}` (so sánh độ mới với các frame đã duyệt trong phiên).
  - `features`: `{"det.yolo_consistency": {"type": "yolo_consistency", "params": {"model_id": "$perception_model"}}}`
  - `candidate_stage`:
    - `limit`: Khởi điểm `max(32, min(96, batch_size * 2))`.
    - `objectives`: `[{"type": "novelty", "strength": 1.0, "params": {"field": "emb.dinov2_s"}}]`
    - `combiner`: `{"type": "product_strength"}`
    - `selector`: `{"type": "greedy_nms", "params": {"field": "emb.dinov2_s", "nms_s": 1.0, "min_dist": 0.1}}`
  - `objectives`:
    - `{"type": "novelty", "strength": 0.6, "params": {"field": "emb.dinov2_s"}}`
    - `{"type": "detection_instability", "strength": 0.4, "params": {"field": "det.yolo_consistency"}}`
  - `combiner`: `{"type": "product_strength"}`
  - `selector`: `{"type": "greedy_nms", "params": {"field": "emb.dinov2_s", "nms_s": 1.0, "min_dist": 0.1}}`
- **Đầu vào Model độc lập**:
  - Yêu cầu `ProposeIn` hỗ trợ `model_id: UUID | str | None = None`.
  - Server xác minh model tồn tại trong `rav_pilot_model_asset` với `validation = 'INFERENCE_VERIFIED'`. Đóng băng `model` `{id, name, sha256, size_bytes, raw_key}` vào payload của job `PROPOSE`.
  - Không đòi hỏi session phải có reference binding với dataset version nào.
- **Xử lý nền & Tiến trình thực tế**:
  - Khi TEXT phục vụ Interactive HTTP, dispatcher YOLO nhận thêm job `PROPOSE` nền trên chính slot/threads/RSS YOLO;
    không thêm tiến trình/slot hay tăng ngân sách. TEXT không claim queue trong chế độ này.
  - Chuyển từ batch Interactive tạm sang job nền phải checkpoint batch và draft trước enqueue; retry không mất model/config đã chọn.
  - Khi mở lại Studio hoặc job nền hoàn tất, gọi `POST /sessions/{id}/interactive/open?refresh=true` để nạp lại lịch sử
    đã persist vào RAM Interactive, tránh trùng số đợt khi chuyển qua lại hai chế độ. Chỉ làm mới cache của owner/session đó.
  - Tách biệt với Interactive mode: Khi interactive bật (`direct.enabled()`), các recipe chạy nền như `perception_instability` (hoặc có `candidate_stage`/`features`) được API bỏ qua chặn `interactive_required` và frontend đẩy vào job nền qua hàng đợi, đồng thời poll tiến trình job cho đến khi hoàn tất.
  - Job nền `PROPOSE` (queue CPU):
    1. Chạy `select_candidates` lọc ra `candidate_ids` từ pool frame còn hợp lệ (loại frame đã kept/dropped).
    2. Nạp model checkpoint vào cache đĩa theo SHA, nạp vào `ctx.model_files`.
    3. Giải mã exact pixel và suy luận TTA (ảnh gốc + ảnh lật) trên các candidate units (có cache bền vững trên đĩa).
       Đọc candidate theo thứ tự nguồn/thời gian để không reset decoder liên tục, rồi khôi phục thứ tự FieldTable đầu vào.
    4. Cập nhật tiến trình: `runtime.update_progress(lease, Progress(phase="INFERRING_UNCERTAINTY", done=i, total=N))` theo từng frame.
    5. Tính `instability` score và ghi nhận evidence matching: `{n_original, n_matched, n_unmatched, class_changes}`.
    6. Chọn batch cuối cùng qua combiner và selector: candidate chưa có tín hiệu (`observed = False`) bị mask loại bỏ hoàn toàn.
- **Xử lý trường hợp biên & Thiếu tín hiệu**:
  - Khung hình cả 2 lượt không có detection: `observed = False`, `instability = 0.0`, nhãn `"Chưa có tín hiệu"`, bị mask loại khỏi tập chọn (`mask = False`).
  - Nếu số frame có tín hiệu ít hơn `batch_size`: trả đúng số lượng frame tìm được (batch < `batch_size`) kèm `uncertainty_summary`, không tự bù bằng random và không biến điểm 0 thành dự đoán tin cậy.
- **Cache key đa thành phần & Bền vững qua các job**:
  - Trường `det.yolo_consistency` lưu cache đĩa tại `inference-cache/yolo-consistency` theo SHA đa thành phần:
    `hash(model_sha256, unit_id, pixel_identity, engine_version, transform="horizontal_flip", inference_params, consistency_version)`.
  - Tồn tại qua các job và các lần đề xuất / retry của worker, không bị mất khi context kết thúc.
  - Tận dụng metadata frame / unit sẵn có, không giải mã toàn bộ video để tính cache key.
- **Bảo toàn Evidence qua API DTO**:
  - `ProposalV2DTO` và DTO response chuyển giao đầy đủ `evidence.units` và `uncertainty_summary` về UI để hiển thị badge, matching tooltip và overlay box đối chiếu TTA.
- **Kiểm soát khả dụng (Availability Gate)**:
  - Chỉ chuyển `available = true` trong `catalog` khi toàn bộ chuỗi API → Job → Worker → UI đã được xác nhận hoạt động và qua kiểm thử.


### 21.30 Hợp đồng VPS Upload → Ingest S3 → READY (VPS-Upload-Ingest #139)

Làm rõ release: Render không đọc/xóa filesystem VPS. Complete gọi HTTPS VPS để seal file đủ bytes;
abort/sweep gọi control HTTPS ký HMAC với purpose riêng, thời hạn ngắn; browser capability không có quyền control.
VPS dùng shared secret explicit tối thiểu 32 ký tự, không fallback dev/JWT. API và worker dùng cùng secret;
TLS verify bắt buộc (CA pin riêng khi cần). Router VPS chạy trên listener Contabo với CORS frontend explicit.
Chỉ một upload VPS CREATED/VALIDATING được cấp tại một thời điểm. Chunk tối đa 16 MiB,
có thể spool tạm tối đa một chunk/request trước khi commit dưới khóa liên tiến trình; không giữ file lớn trong RAM.
Seal/revoke chặn ghi lại, kể cả capability chưa hết hạn. Xóa bytes sau DB commit thành công;
rollback publication giữ file để retry. Abort RUNNING chờ handler dừng trước cleanup; sweeper phục hồi cleanup bị ngắt.
Worker resolve part từ upload UUID tại thư mục cấu hình, không tin đường dẫn tuyệt đối của API Render.

- **Mục tiêu**: Hỗ trợ tải các tệp video lớn (tới hàng GiB) từ trình duyệt một cách tin cậy thông qua dịch vụ nhận file trực tiếp trên đĩa VPS Contabo, sau đó worker stream trực tiếp lên S3 canonical và trích xuất frame READY; đồng thời giữ nguyên 100% hai transport cũ (`POST` và `MULTIPART` S3) để có thể rollback tức thì khi cần.
- **Transport mới `VPS_DISK`**:
  - Hỗ trợ giá trị `transfer_method = 'VPS_DISK'` trên bảng `rav_pilot_upload` (nới lỏng constraint qua migration additive `020_pilot_vps_upload.sql`).
  - Được kích hoạt khi cấu hình quota policy có `vps_upload_enabled = true` hoặc client yêu cầu qua `preferred_transport = 'VPS_DISK'`. Khi tắt flag hoặc fallback, hệ thống sử dụng transport `MULTIPART` hoặc `POST` như hợp đồng §21.7 và §21.27.
- **Khởi tạo và Capability Token**:
  - `POST /api/v2/uploads` trả về DTO `transfer`:
    - `method: "VPS_DISK"`
    - `url: "<RAV_VPS_UPLOAD_URL>/api/v2/vps-uploads/{upload_id}"`
    - `part_size: 16777216` (16 MiB)
    - `capability: "<token>"` (token bảo mật HMAC-SHA256 ký với `RAV_VPS_UPLOAD_SECRET`, chứa `upload_id`, `owner_id`, `declared_bytes`, `exp`).
  - Capability chỉ cho phép thao tác ghi chunk vào đúng file của upload đó; không cấp quyền truy cập S3 hay DB cho trình duyệt.
- **Ghi đĩa tuần tự, Buffer Stream 1 MiB, Fsync và Metadata Atomic**:
  - Đường dẫn lưu trữ: `RAV_VPS_UPLOAD_DIR` (mặc định `data/uploads/`).
  - Mỗi upload lưu 2 tệp: `<upload_id>.part` và `<upload_id>.meta`.
  - Endpoint `PUT /api/v2/vps-uploads/{upload_id}/chunks`:
    - Header: `Authorization: Bearer <capability>`, `Upload-Offset: <int>` (hoặc query param `offset`).
    - Body: nhị phân chunk tối đa 16 MiB, server đọc stream qua buffer 1 MiB và ghi nối (append) vào duy nhất file `.part`. Không lưu nhiều file part nhỏ rồi ghép lại làm tốn gấp đôi dung lượng đĩa.
    - Kiểm tra offset: Bắt buộc `offset == current_durable_offset`. Nếu lệch (do mất gói hoặc retry sai lệch), trả về mã lỗi 409 `offset_mismatch` kèm `{ "expected_offset": current_durable_offset }`.
    - Tính bền vững (Durability): Sau mỗi chunk, thực hiện `f.flush()` và `os.fsync(f.fileno())`, sau đó cập nhật `.meta.tmp` rồi atomic rename sang `.meta` ghi nhận `offset` bền vững.
    - Trả về: `{"status": "ok", "offset": <new_durable_offset>}`.
- **Phục hồi sau sự cố và Resume (Crash Recovery & Restart)**:
  - Endpoint `GET /api/v2/vps-uploads/{upload_id}`:
    - Đọc `offset` bền vững từ `.meta`.
    - Kiểm tra kích thước file `.part` vật lý: nếu `file_size > durable_offset` (do sự cố đứt điện/crash giữa chừng trước khi fsync/ack chunk), server tự động truncate file `.part` về đúng `durable_offset`.
    - Trả về: `{"upload_id": ..., "offset": durable_offset, "declared_bytes": ..., "completed": bool}`.
    - Trình duyệt truy vấn endpoint này khi khởi động lại hoặc sau gián đoạn mạng để resume chính xác từ byte bền vững.
- **Hoàn tất và Enqueue Ingest S3**:
  - Khi client gửi đủ toàn bộ bytes (`offset == declared_bytes`), client gọi `POST /api/v2/uploads/{upload_id}/complete`.
  - API xác nhận file `.part` đủ dung lượng trên đĩa VPS, sau đó enqueue job `VALIDATE_UPLOAD` kèm thông tin `staging_source: "VPS_DISK"` và `vps_part_path` vào hàng đợi, chuyển upload sang trạng thái `VALIDATING`.
- **Worker Ingest S3 → READY**:
  - Worker xử lý `VALIDATE_UPLOAD` từ `staging_source = "VPS_DISK"`:
    - Bỏ qua bước download từ S3 staging (tiết kiệm băng thông tải về).
    - Pha `PROBING`: Xác thực định dạng, codec, SHA-256 và pixel limit trực tiếp từ file `.part` cục bộ.
    - Pha `SAVING_CANONICAL`: Stream tải trực tiếp file `.part` lên S3 canonical key bằng S3 multipart upload streaming (chunk 16 MiB), giữ RSS worker thấp (~140 MiB).
    - Pha `EXTRACTING_FRAMES`: Trích xuất frame và thumbnail 4fps (`materialize`), upload lên S3.
    - Pha `FINALIZING`: Hoàn tất publish asset READY trong DB transaction.
    - Sau khi publish thành công: **Xóa ngay file `.part` và `.meta` trên VPS disk** để giải phóng 100% dung lượng đĩa.
- **Xử lý lỗi S3 & Retry**:
  - Nếu quá trình stream lên S3 hoặc trích xuất frame gặp lỗi mạng/S3 tạm thời: attempt thất bại, dispatcher thực hiện retry theo chính sách. File `.part` trên VPS được giữ nguyên trong suốt quá trình retry để attempt tiếp theo tiếp tục xử lý mà không yêu cầu client upload lại.
  - Nếu job thất bại vĩnh viễn (hết lượt retry hoặc lỗi dữ liệu không thể phục hồi): dọn dẹp file `.part` và `.meta`, đóng upload `FAILED`.
- **Dọn dẹp và Abort (Cleanup)**:
  - `POST /api/v2/uploads/{upload_id}/abort`: Xóa ngay file `.part` và `.meta` trên VPS disk, giải phóng hold và đánh dấu upload `ABORTED`.
  - `sweep_staging`: Các upload `VPS_DISK` ở trạng thái `CREATED` quá hạn `capability_expires_at` + `staging_grace_ms` sẽ được sweeper dọn dẹp sạch file `.part` và `.meta` trên VPS disk.
- **Rollback và Tương thích ngược**:
  - Cả hai transport cũ (`DIRECT_S3` presigned POST và `MULTIPART` S3) tiếp tục được giữ nguyên vẹn trong codebase.
  - Nếu cần rollback, chỉ cần tắt flag `vps_upload_enabled = false` trong quota policy hoặc cấu hình môi trường mà không cần rollback cơ sở dữ liệu.


### 21.33 Admin upload mô hình tham chiếu SYSTEM (#142)

Người dùng duyệt 2026-10-16: chỉ admin upload checkpoint; mọi người dùng chọn từ danh sách model SYSTEM (§21.17, §21.32).
Upload model riêng của từng người: **Hướng tới** — `.pt` là pickle, nạp là chạy code trên worker dùng chung; cần sandbox hoặc chỉ nhận ONNX.

- **Admin** = user_id trong `RAV_PILOT_OPERATOR_IDS` (danh sách đã dùng cho vận hành §21.19); không thêm cột vai trò (§21.11 giữ nguyên).
  `GET /api/v2/me` thêm `models_write: bool`. Không phải admin gọi endpoint admin → 403 `forbidden`.
- `POST /api/v2/admin/models/uploads {filename, declared_bytes}` → `{upload_id, transfer}`: presigned POST vào
  `<prefix>/staging/system-models/<upload_id>/model.pt`, `1 ≤ declared_bytes ≤ 1 GiB`, TTL 1 giờ, tên file phải `.pt`.
  Không ghi DB, không giữ dung lượng (model SYSTEM không tính quota người dùng).
- `POST /api/v2/admin/models/uploads/{upload_id}/complete {filename, name, train_dataset}` → HEAD staging (thiếu → 409 `upload_invalid`,
  lớn hơn 1 GiB → 409) → enqueue `MODEL_VERIFY` (queue CPU, idempotency `model-upload:<upload_id>`, owner = admin) → 202 JobDTO.
  Gọi lại cùng body trả cùng job. Trạng thái qua `GET /api/v2/jobs/{id}`; SUCCEEDED → model có trong `GET /api/v2/models` (id = uuid5(sha), `result_ref` để trống vì chỉ trỏ artifact).
- **Worker `MODEL_VERIFY`**: tải staging có giới hạn + SHA-256 → nạp bằng Ultralytics pin, chạy thử một ảnh xám (dùng chung code
  với `scripts/pilot_import_model.py`), chỉ task `detect` → PUT canonical `<prefix>/system/models/yolo/<sha>.pt` kèm ChecksumSHA256
  → xóa staging (cả khi lỗi). Lỗi nạp/không phải detect → FAILED `unsupported_yolo_model`, không tạo model.
  Publish: INSERT `rav_pilot_model_asset` SYSTEM/protected, id = uuid5(sha) như script, `validation=INFERENCE_VERIFIED`,
  provenance `{train_dataset, file, uploaded_by}`. Cùng bytes đã có → giữ bản cũ, không đổi tên/metadata.
- **Migration additive `021_pilot_model_verify.sql`**: kind `MODEL_VERIFY` trong check job + claim CPU; GRANT INSERT model asset cho worker.
  Policy cần `profiles_by_kind.MODEL_VERIFY`; `RAV_PILOT_CPU_LANES` phải gán `MODEL_VERIFY` cho một lane (thiếu → worker từ chối khởi động).
  Thứ tự deploy: migration → policy mới → worker env + image → FE/API.
- **UI pilot**: tab Mô hình tham chiếu hiện cho mọi người dùng (chỉ đọc: tên, task, số lớp, dataset train đã khai, SHA, kích thước).
  Form upload chỉ khi `models_write`: file `.pt`, tên, dataset train gốc (bắt buộc, ghi đúng sự thật); trạng thái tải lên → kiểm tra → xong/lỗi.
  Không xóa/sửa model qua UI; không đổi phiên/Studio.
- Staging bỏ dở (không gọi complete) không có sweeper: admin hiếm khi bỏ dở. **Hướng tới**: lifecycle rule S3 cho `staging/system-models/`.

### 21.34 Đăng ký tự phục vụ và khởi tạo hồ sơ tự động (Lát 1, 2026-10-17)

Người dùng duyệt 2026-10-17: mở đăng ký tự phục vụ trên `/login` và tự động khởi tạo hồ sơ + tài khoản dung lượng trong lần xác thực đầu tiên.

- **UI `/login`**:
  - Chuyển đổi giữa hai chế độ **Đăng nhập** và **Đăng ký**.
  - Đăng ký nhận `email`, `password` (≥ 6 ký tự) và `display_name` tùy chọn (gửi qua `options.data.display_name` của Supabase Auth `signUp`).
  - Nếu Supabase trả ngay session (`data.session != null`), chuyển tới `/app`. Nếu Supabase yêu cầu xác nhận email (`data.user != null && data.session == null`), hiển thị thông báo rõ ràng hướng dẫn người dùng kiểm tra hộp thư rồi đăng nhập.
  - Sau đăng nhập lần đầu (tài khoản chưa có phiên và chưa có video riêng), giao diện hiển thị thẻ bắt đầu nhanh với hai hướng rõ ràng: **Dùng dữ liệu mẫu** và **Upload dữ liệu của bạn**.
- **Bootstrap một đường duy nhất trong xác thực (`extract_actor_context` → `PilotDB.ensure_profile`)**:
  - Không thêm endpoint tạo profile riêng. Khi JWT hợp lệ (`sub` là UUID) nhưng chưa có dòng trong `rav_pilot_profile`, `ensure_profile` mở một transaction `backend()` để tạo đồng thời:
    1. `rav_pilot_profile(user_id, active=true, display_name, policy_id)` với `ON CONFLICT (user_id) DO NOTHING`.
    2. `rav_pilot_storage_account(account_key, limit_bytes)` cho `USER:<user_id>` và `GLOBAL` với `ON CONFLICT (account_key) DO NOTHING`.
  - **Idempotent dưới truy cập đồng thời**: nhiều request đầu tiên (ví dụ `/me`, `/sources`, `/sessions`) đến cùng lúc không sinh lỗi duplicate key, không ghi đè `display_name`/`policy_id`/`active`/`authz_version` của profile đã tồn tại, và không mở lại tài khoản đã bị vô hiệu hóa (`active = false` vẫn trả `401 account_disabled`).
  - `display_name` lấy từ `user_metadata.display_name` / `user_metadata.full_name` trong JWT claims (hoặc tiền tố email, cắt tối đa 80 ký tự in được, fallback `user-<6 hex>`), chỉ là dữ liệu hiển thị, không cấp quyền.
  - Không tạo sẵn bản sao video hay phiên cho mỗi tài khoản mới. Quyền admin giữ nguyên cơ chế `RAV_PILOT_OPERATOR_IDS`.
- **Chính sách đăng ký tường minh (`signup_default`)**:
  - Tuyệt đối không tự chọn `enabled = true ORDER BY created_at DESC`.
  - Migration additive `023_pilot_signup_bootstrap.sql` thêm cột `signup_default BOOLEAN NOT NULL DEFAULT false` trên `rav_pilot_quota_policy` cùng partial unique index `WHERE signup_default = true`, và cấp `GRANT INSERT ON rav_pilot_profile TO rav_pilot_api` (giữ nguyên giới hạn `UPDATE (display_name)` để `rav_pilot_api` không thể tự đổi `active` hay `policy_id` của hồ sơ đã có).
  - Bootstrap chọn policy theo thứ tự: biến môi trường `RAV_PILOT_SIGNUP_POLICY_VERSION` (nếu đặt, phải khớp policy `enabled = true`), ngược lại lấy policy có `signup_default = true AND enabled = true`.
  - Nếu không tìm thấy policy đăng ký hoặc `policy_problem(config)` khác `None` → trả `503 policy_unconfigured` (`retryable = true`), không tạo profile.

### 21.35 Kho dữ liệu mẫu, phân tách quyền đọc khi ẩn và giữ tiến trình upload cấp ứng dụng (Lát 2, 2026-10-17)

Người dùng duyệt 2026-10-17: hợp nhất Kho dữ liệu (**Dữ liệu của tôi / Dữ liệu mẫu**), tách độc lập trạng thái xuất bản `published` với trạng thái xử lý, siết quyền đọc tài nguyên `SYSTEM` khi bị ẩn ở cả RLS và mọi đường `backend()`, đồng thời đưa bộ theo dõi tiến trình upload lên cấp ứng dụng kèm khôi phục sau reload.

- **Migration additive `024_pilot_sample_catalog.sql`**:
  - Bổ sung `description TEXT NOT NULL DEFAULT ''` và `published BOOLEAN NOT NULL DEFAULT true` trên `rav_pilot_source_asset` và `rav_pilot_model_asset` (các tài nguyên `SYSTEM` hiện có giữ `published = true`; tài nguyên mới có thể đặt `published = false` khi ẩn hoặc đang chuẩn bị).
  - Thay trigger bất biến toàn phần của `rav_pilot_model_asset` bằng `rav_pilot_model_identity_guard()`: cấm `DELETE`, giữ bất biến tuyệt đối các cột danh tính/hash/raw_key/validation (`core_model_id, scope, protected, raw_key, sha256, size_bytes, checkpoint_revision, task, class_map, runtime_manifest, validation`), chỉ cho phép cập nhật `(name, description, published)`.
  - Cập nhật `GRANT SELECT (description, published)` trên `rav_pilot_source_asset` và `rav_pilot_model_asset` cho `authenticated`, cùng `GRANT UPDATE` tương ứng cho `rav_pilot_api`.
- **Phân tách quyền "xuất hiện trong kho / chọn tạo phiên mới" và "được đọc trong phiên/snapshot đã tạo"**:
  - **Kho & chọn mới (`GET /api/v2/sources`, `GET /api/v2/models`, `POST /api/v2/sessions`, `PATCH /api/v2/sessions/{id}/reference`)**:
    - Người dùng thường chỉ thấy các nguồn/model `SYSTEM` có `published = true` trong danh sách Kho (`GET /api/v2/sources`, `GET /api/v2/models`). Admin (`operations_read = true`) có thể truyền `include_unpublished=true` để xem cả tài nguyên đang chuẩn bị hoặc bị ẩn.
    - Khi tạo phiên mới hoặc cập nhật tham chiếu phiên, nguồn/model `SYSTEM` bắt buộc phải có `published = true`. Nếu tài nguyên đang ẩn (`published = false`): trả `404 not_found` cho người dùng chưa từng có phiên/snapshot tham chiếu, hoặc `409 source_unavailable` / `409 model_unavailable` nếu người dùng từng có phiên cũ nhưng thử gắn vào phiên mới.
  - **Đọc trong phiên/snapshot đã tạo (`GET /api/v2/sources/{id}`, `GET /api/v2/models/{id}`, `POST /api/v2/media/requests`, `POST /api/v2/media/batch`, `GET /api/v2/media/requests/{id}`, `PROPOSE`, `Interactive`, `YOLO_BATCH`, `METRICS`, `Export`)**:
    - Cập nhật cả RLS (`p_source_read`, `p_model_read`) và các hàm kiểm quyền ở `backend()` (`PilotDB.can_read_source_in_tx`, `PilotDB.can_read_model_in_tx`):
      - Cho phép đọc tài nguyên `SYSTEM` nếu `published = true`, **HOẶC** người dùng là admin (`operations_read`), **HOẶC** người dùng có ít nhất một phiên/snapshot/proposal đã gắn tài nguyên đó (`rav_pilot_session_source`, `rav_pilot_snapshot_member`, `rav_pilot_session_reference`, `rav_pilot_session.spec`, `rav_pilot_proposal.frozen_input`).
      - Người dùng chưa từng được cấp quyền qua phiên/snapshot không thể đoán UUID của nguồn, frame hay model đang ẩn (`published = false`) để đọc metadata hoặc lấy presigned URL (trả `404 not_found`).
- **Tái sử dụng artifact nguồn `SYSTEM`**:
  - Tạo phiên từ nguồn `SYSTEM` đã sẵn sàng dùng lại trực tiếp `rav_pilot_frame_set` (`FRAMES`) và `rav_pilot_artifact` (`DINO`, `CLIP`) ở `scope = 'SYSTEM'`; nhiều tài khoản cùng tạo phiên trên một video mẫu không phát sinh chạy lại trích xuất ảnh hay tính DINO/CLIP, trong khi quyết định duyệt (`rav_pilot_decision`) và snapshot của từng tài khoản hoàn toàn độc lập.
- **Giữ tiến trình upload cấp ứng dụng và khôi phục sau reload (`GET /api/v2/uploads`)**:
  - `GET /api/v2/uploads?limit=20` trả về danh sách `UploadDTO` gần đây của chính `owner_id` (gồm các upload đang `VALIDATING` kèm `progress` chi tiết từ job `VALIDATE_UPLOAD`, các upload `CREATED` còn hạn `capability_expires_at > now()`, và các upload `FAILED` trong 2 giờ gần nhất).
  - Bộ quản lý upload trên frontend đặt ở cấp ứng dụng (`App.tsx`) kèm thanh chỉ báo nhỏ luôn hiển thị (`GlobalUploadIndicator`):
    - Chuyển qua lại giữa Kho dữ liệu (`#videos`) và Phiên (`#s=<sid>`) không làm mất danh sách tiến trình; quay lại Kho dữ liệu vẫn thấy đầy đủ các mục đang tải, đang tiền xử lý, lỗi hoặc hoàn tất.
    - Sau khi reload trang (F5), frontend gọi `GET /api/v2/uploads`: tự động nối lại vòng poll 2 giây/lần cho các upload đang `VALIDATING` trên worker; đối với các upload `CREATED` chưa truyền đủ file trước khi reload, hiển thị trạng thái **"Chọn lại file để tiếp tục"** để người dùng chọn lại đúng file và resume từ offset/part dở dang.

### 21.36 Admin quản lý tài nguyên mẫu SYSTEM, vận hành/log và seed dữ liệu mẫu thật (Lát 3, 2026-10-17)

Người dùng duyệt 2026-10-17: bỏ hạng mục "Admin tạo/cấu hình phiên mẫu (`CONFIGURED`)" trên UI (**Hướng tới** — vì người dùng đã có nút **"Tạo phiên từ dữ liệu này"** trực tiếp trên video mẫu sẵn sàng ở Kho dữ liệu; giữ nguyên cơ chế phiên mẫu lịch sử `IMPORTED_HISTORY` hiện có) và gộp Lát 3 + Lát 5 thành một lát hoàn chỉnh.

- **Migration additive `025_pilot_system_ingest.sql`**:
  - Bổ sung `target_scope TEXT NOT NULL DEFAULT 'PRIVATE' CHECK (target_scope IN ('PRIVATE', 'SYSTEM'))` trên `rav_pilot_upload` (bất biến sau khi tạo).
  - Bổ sung `account_scope TEXT NOT NULL DEFAULT 'USER_AND_GLOBAL' CHECK (account_scope IN ('USER_AND_GLOBAL', 'GLOBAL_ONLY'))` trên `rav_pilot_storage_hold` (bất biến sau khi tạo).
  - Cấp quyền `GRANT UPDATE (availability, reason_code, version, updated_at) ON rav_pilot_demo_template_status TO rav_pilot_api` để API admin đồng bộ trạng thái `BLOCKED` / `VERIFIED` của các phiên mẫu lịch sử khi bật/tắt xuất bản nguồn `SYSTEM`.
- **Luồng Upload & Prepare nguồn `SYSTEM` xuyên suốt (API → S3 → Worker → Publish)**:
  - `POST /api/v2/uploads` nhận tùy chọn `target_scope: "PRIVATE" | "SYSTEM" = "PRIVATE"`. Chỉ admin (`operations_read = true`) được truyền `target_scope = "SYSTEM"` (người dùng thường → `403 forbidden`).
  - **Accounting & Ownership**:
    - `rav_pilot_upload.owner_id` và `rav_pilot_job.owner_id` giữ UUID của admin để theo dõi tiến trình, audit và hủy/thử lại.
    - Storage hold của upload/job có `target_scope = 'SYSTEM'` đặt `account_scope = 'GLOBAL_ONLY'`: khi `storage_accounting_enabled = true`, chỉ giữ và quyết toán trên tài khoản `GLOBAL`, không trừ hạn mức `USER:<admin_uid>`.
  - **Worker `VALIDATE_UPLOAD` cho `target_scope = 'SYSTEM'`**:
    - Dedup nguồn `SYSTEM` theo `core_source_id` (`WHERE scope = 'SYSTEM' AND availability = 'READY'`).
    - Canonical raw key: `<prefix>/system/uploads/sources/<core_source_id>/raw/<sha256>/video.<ext>`.
    - Frame key: `<prefix>/system/uploads/frames/<core_source_id>/<frame_set_id>/<frame_idx>/<variant>/<sha256>.jpg`.
    - `publish_in_tx` tạo `rav_pilot_source_asset` với `scope = 'SYSTEM', owner_id = NULL, protected = true, published = false` (mặc định ẩn khi mới tải lên để admin kiểm tra/chuẩn bị xong mới xuất bản) và `rav_pilot_frame_set` với `scope = 'SYSTEM', owner_id = NULL`.
  - **Prepare `FRAMES` / `DINO` / `CLIP` cho nguồn `SYSTEM`**:
    - Admin (`operations_read = true`) gọi `POST /api/v2/sources/{id}/preparations` trên nguồn `SYSTEM` để enqueue `PREPARE_FRAMES`, `PREPARE_DINO`, `PREPARE_CLIP` với `target_scope = 'SYSTEM'` trong payload (người dùng thường gọi trên nguồn `SYSTEM` thiếu đặc trưng vẫn nhận `409 system_feature_missing` / `409 system_frames_missing`).
    - Worker lưu NPZ đặc trưng tại `<prefix>/system/uploads/features/<core_source_id>/<feature>/<sha256>.npz` và `publish_in_tx` tạo `rav_pilot_artifact` với `scope = 'SYSTEM', owner_id = NULL`.
- **API & UI Quản lý danh mục dữ liệu mẫu của Admin**:
  - `GET /api/v2/admin/sources?cursor=...&limit=50` và `GET /api/v2/admin/models?cursor=...&limit=50`: chỉ admin (`403 forbidden` nếu thường), phân trang cursor, trả về danh sách nguồn/model `SYSTEM` kèm `publishable: bool`, `unpublishable_reasons: list[str]`, `dependent_template_count: int`, `dependent_templates: list[dict]`.
  - `PATCH /api/v2/admin/sources/{id}` và `PATCH /api/v2/admin/models/{id}` (`{name?, description?, published?}`):
    - Bật `published = true` yêu cầu video phải `availability == 'READY'` và có `FRAMES` `READY` (thiếu → `409 source_not_publishable`), model phải `validation == 'INFERENCE_VERIFIED'` (thiếu → `409 model_not_publishable`).
    - Khi tắt `published = false` trên nguồn `SYSTEM`: tự động chuyển các `rav_pilot_demo_template_status` có `source_mapping` chứa nguồn đó sang `availability = 'BLOCKED', reason_code = 'source_unpublished'`; khi bật lại `published = true`, nếu toàn bộ nguồn trong `source_mapping` đều `READY` và `published = true` thì khôi phục `availability = 'VERIFIED', reason_code = NULL`. Đồng thời `POST /api/v2/demo-templates/{id}/fork` kiểm tra `require_published_system = true`.
- **Trang Admin Vận hành (`#operations`) & Nhật ký**:
  - Bố cục 4 khu vực trên `#operations`: **Tổng quan**, **Dữ liệu mẫu**, **Tác vụ**, **Nhật ký**; chống chồng request khi auto-refresh 10 giây.
  - `GET /api/v2/operations` trả thêm `job_counts` tổng hợp trực tiếp từ DB (`queued`, `waiting_resource`, `running`, `cancel_requested`, `failed_24h`, `succeeded_24h`) và bổ sung `attempt_n`, `queue_wait_ms` (từ `job.created_at` đến `attempt_1.started_at` hoặc `now()`), `run_ms` (thời gian chạy của attempt hiện tại) trên danh sách `jobs`.
  - `GET /api/v2/operations/logs` hỗ trợ thêm bộ lọc `upload_id`, `job_id`, `request_id` (UUID) kết hợp cùng `hours`, `source`, `errors_only` và keyset cursor `(received_at, id)`.
- **Seed dữ liệu mẫu thật (`scripts/pilot_seed_sample_catalog.py`)**:
  - Script idempotent chuẩn bị ít nhất 1 video mẫu thật (`data/videos/commaai-sample.mp4`) ở `scope = 'SYSTEM'`, hoàn tất `FRAMES` / `DINO` / `CLIP`, bật `published = true`, đảm bảo hai tài khoản mới tạo phiên độc lập và dùng lại cùng artifact mà không chạy lại prepare.



#### Signup callback clarification (2026-10-17)

Email confirmation returns to the deployment origin `/login` (Supabase Site URL must match production). Only `/login` consumes the implicit auth callback through Supabase SDK, which persists the session and removes token fragments before navigating into the app. Signup explicitly supplies the same-origin login redirect; no tokens/passwords in telemetry. Existing email verification remains enabled.


#### Signup policy rollout clarification (2026-10-17)

User confirms new accounts receive50 GPU minutes per fixed4h UTC window, CPU accounting disabled. Existing profiles are not reassigned. Signup default is a versioned policy selected explicitly; the legacy dashboard auth trigger must not create profiles using arbitrary enabled policy. Migration026 retires its profile insertion; only verified API bootstrap creates profile+storage accounts. Admin policy editor remains future scope; changing the default does not change existing accounts.

### 21.37 ODD coverage strategy (approved 2026-10-17)

- Add native background strategy `odd_coverage`, independent of RecipeConfig; preserve legacy strategies.
  Inputs: prepared DINO/CLIP; optional frozen verified detection model. No training dataset required.
- Public text arrays `odd_conditions`, `objects`, `scenarios` each max8 lines, nonblank max200 chars;
  require at least one query. Generate deterministic prompts `a dashcam photo of {object}, {odd}`,
  object-only `a dashcam photo of {object}`, ODD-only `a dashcam photo, {odd}`. Scenarios are independent
  complete prompts, never implicitly constrained by the other arrays. Union max72 branches, preserve memberships.
  Explicit English prompts; no inferred translation/goal expansion or claim of verified ODD labels.
- Contrast prompts default `a dashcam photo of a road scene`, editable under advanced settings.
  For each frame/branch: positive=max cosine over its prompt (one initially), contrast=max cosine across
  contrast prompts; margin=positive-contrast. Gate `positive >= min_similarity AND margin >= min_margin`.
  Defaults .2/.01 are uncalibrated heuristics, not universal thresholds. Relevance=sigmoid(margin/temperature),
  temperature default .05. Never softmax competing ODD branches or min-max the pool; empty is valid.
- Bound scoring with chunked CLIP matrices; cap union shortlist to512 (public candidate_limit72..2048)
  by deterministic round-robin of per-branch ranking, not global topK. Trace counts full qualifying union
  and bounded shortlist separately. No all-pool pairwise similarity matrix or DB query per frame.
- Greedy dynamic batch: distance=(1-cos DINO)/2; novelty=min distance to kept+current picks.
  When neither exists, initialize from proximity to candidate centroid (representative start).
  Branch coverage gain=mean(1/(1+kept_count+selected_count)) over matching branches.
  Gain=.35 relevance + .40 novelty + .25 coverage; recompute novelty/coverage after each pick.
  Hard temporal exclusion1s within a video and hard DINO distance1-cos>=.1 against current batch;
  do not relax to fillK. Kept affect novelty/coverage; dropped only remove exact frames, not negative labels.
- Optional model selection enables requested-only original/flip consistency on at most min(96,3K)
  candidates selected by the cheap greedy pass. Final gain=.8*cheap_gain+.2*instability; unobserved
  (both detections empty) are excluded in this mode. Cache/cancel/frozen model gates remain unchanged.
- Metadata adds `inputs.model_optional` only for strategies supporting optional binding. API binds a
  supplied optional model through the same owner/verification/frozen SHA checks as required models.
- Evidence for selected frames: qualifying branch prompts, raw cosine/margin, relevance, selection-time
  novelty/coverage gain, instability if measured. Bounded trace includes per-branch eligible/kept/selected
  counts and selection stages; public response still strips candidate IDs/scores outside selected batch.
  UI: three multiline query boxes, optional model, advanced contrast/gates/budget, coverage summary and
  per-frame reasons. Frozen batch summary must not reflect next-batch edits. Retry/reload preserve params.
- Validate with synthetic math/gating/dynamic coverage tests, no-match and overlap cases, optional model
  shortlist/cancel, lightweight API schema/model/receipt tests, frontend persistence and pilot build.
  Real-model demo evidence is separate from semantic evaluation; no mAP/uplift/probability claims.

### 21.38 Admin chọn video riêng làm dữ liệu mẫu (2026-10-17)

**Luồng sao chép vật lý dưới đây là legacy; yêu cầu tạo mẫu mới được thay bằng §21.39 theo quyết định người dùng.**

Người dùng duyệt tạo bản sao SYSTEM độc lập, không chuyển owner/scope của nguồn gốc.
- `POST /api/v2/admin/sources/{id}/copy-to-samples` (Idempotency-Key): chỉ operator, nguồn VIDEO
  PRIVATE của chính operator, READY, FRAMES đúng raw/spec/encoding và DINO/CLIP đúng input hash.
  Trả `{source_id, job, existing}`; SYSTEM cùng core đã tồn tại thì trả ID đó, không copy lại/không tự xuất bản.
- Dùng queue/lease/receipt hiện có: `PREPARE_FRAMES` với payload `sample_copy=true`, `target_scope=SYSTEM`;
  đây là chế độ materialize từ ảnh đã có thay vì decoder. Không thêm job kind hoặc nới policy/CPU lanes.
  Freeze source/version, frame_set_id và feature identities; pin nguồn gốc qua job. Metadata frame đọc theo
  bộ bất biến tại worker; không nhét toàn frame list vào request/payload từ browser.
- Worker sao chép raw/ảnh/NPZ bằng S3 server-side copy có điều kiện ETag, kiểm kích thước/checksum khi có;
  không download video về browser/VPS, không decode hoặc tính embedding. Copy theo batch/concurrency có giới hạn,
  hỗ trợ progress/cancel. Destination thuộc namespace SYSTEM và attempt riêng, INTENT trước COPY, WRITTEN sau HEAD;
  hold GLOBAL_ONLY kể cả khi accounting tắt, publish fenced atomic, lỗi/cancel cleanup DELETE+HEAD trước nhả hold.
- Publish tạo source SYSTEM/protected/owner NULL/published=false, frame set và FEATURE artifacts SYSTEM.
  Giữ nguyên byte/SHA/exact unit IDs/FieldKey; chỉ sửa source_id trong manifest sang nguồn mới.
  Bản riêng/phiên/snapshot cũ không đổi; xóa bản riêng không xóa object bản SYSTEM.
  Duplicate publish race không ghi đè nguồn mẫu đã có; object không nhận phải được cleanup theo receipt.
- UI admin: hai cách Upload mới / Chọn từ kho của tôi; phân trang danh sách VIDEO PRIVATE, hiển thị readiness,
  chỉ cho tạo bản sao khi đủ FRAMES/DINO/CLIP; theo dõi job và tải lại catalog khi hoàn tất. Người thường không có quyền.
  Nút chọn file upload có nền/viền rõ, hover/focus/disabled theo theme, giữ native input và keyboard accessibility.
- Test PostgreSQL/S3 cô lập: quyền operator/owner, readiness, idempotency/dedup, byte/identity reuse,
  state cũ không đổi, GLOBAL accounting, failure/cancel cleanup và khả năng dùng sample sau khi xóa nguồn riêng.
  Không tự deploy/push hay xuất bản dữ liệu thật của admin trong kiểm thử.

### 21.39 Mẫu chỉ đọc tham chiếu artifact admin (2026-10-17)

Người dùng duyệt thay cơ chế copy tại §21.38 và yêu cầu hủy job copy đang chạy. Admin chọn VIDEO PRIVATE
của chính mình, READY và đủ FRAMES/DINO/CLIP đúng phiên bản, tạo một nguồn SYSTEM protected/published=false.
Nguồn SYSTEM chỉ là metadata và các hàng tra cứu tham chiếu cùng raw/object keys đã chuẩn bị: không COPY S3,
không tải video, không AI, không tạo job và không nhân bản nguồn/file cho mỗi người dùng.

- `POST /api/v2/admin/sources/{id}/reference-as-sample` (Idempotency-Key): operator-only, own source;
  `{source_id, existing}`. Idempotent/dedup theo source core + full SHA; không tự xuất bản khi retry.
- Migration027 thêm bản ghi liên kết bất biến source mẫu → nguồn gốc/frame-set/DINO/CLIP, pin REFERENCE nguồn
  gốc trong cùng transaction. RPC backend hẹp tạo metadata SYSTEM và alias frame/artifact bằng INSERT SELECT,
  không cấp quyền INSERT rộng cho API. Cho phép alias metadata cùng object_key, không cho ghi đè object vật lý.
- Không đổi owner/scope nguồn gốc, phiên/snapshot cũ. Nguồn gốc không được xóa khi mẫu còn tham chiếu, kể cả
  mẫu đang ẩn. Đây là thay đổi so với tính độc lập xóa của copy vật lý: pilot không có thao tác gỡ pin/xóa mẫu.
  Guard DB bảo vệ source/frames/feature đã liên kết; cleanup attempt cũ chỉ được xóa key riêng của attempt,
  không nhận key dùng chung vào worker receipts hay tạo storage hold/persisted bytes lần nữa.
- Mỗi source SYSTEM có alias metadata/IDs riêng để mọi đường media/feature/export kiểm quyền theo source mẫu;
  không cấp quyền đọc nguồn PRIVATE hoặc các artifact riêng khác của admin cho người dùng.
  Published=true mới được chọn mới; hide vẫn giữ quyền đọc qua phiên/snapshot đã tạo như §21.35–36.
  Người dùng không sửa/xóa/prepare mẫu; admin chỉ quản lý tên/mô tả/ẩn/hiện trên mẫu.
- FE nói rõ "Thêm vào kho mẫu", tham chiếu chỉ đọc và không sao chép file; giữ thumbnail/readiness. Endpoint
  copy-to-samples và retry job sample_copy legacy không nhận yêu cầu copy mới; handler legacy vẫn giữ để
  hoàn tất dừng/cleanup job đã tồn tại. Hủy qua API owner/CAS, không kill worker hoặc sửa trực tiếp job state.
- Kiểm thử PG/S3 cô lập: không S3 COPY/PUT/job mới, byte/identity reuse, dedup/auth/idempotency, pin và xóa,
  media/feature/proposal của người khác qua mẫu nhưng không qua nguồn riêng, hide/existing-session, cleanup
  legacy không xóa file dùng chung. Không tự migrate production/push/deploy khi người dùng còn test local.

### 21.40 ODD không phụ thuộc raw và tiến trình có thời gian (2026-10-17)

Người dùng yêu cầu làm rõ/cache CLIP text và cho xem các bước thực thi. ODD không chọn model chỉ dùng
embedding: payload đóng băng fps/n_frames/duration_ms; worker dựng đúng VideoRef/Frame/Unit từ metadata,
kiểm spec/n_units/NPZ ordered IDs, không download/probe/hash raw hay decode ảnh. Payload cũ thiếu metadata
giữ fallback có nhãn rõ; ODD có detector giữ raw path cũ trong lát này. Không đổi scoring/selection.

Vector text cache độc lập từng prompt chính xác + model + revision + phiên bản chuẩn hóa; giữ thứ tự input,
bounded RAM/disk, atomic write và kiểm vector hữu hạn/chuẩn hóa khi đọc, cache hỏng tính lại. Chỉ prompt miss
mới cần nạp CLIP text model/mã hóa. Không cache theo phiên hay đổi embedding ảnh; không đưa prompt/key/token vào log.

PROPOSE ghi danh sách timing bounded tối đa24 phase trong progress_detail JSON hiện có: tên phase, started_at,
elapsed_ms đo monotonic, state, done/total và cache_hit nếu đo được. Worker logs cùng phase phục vụ admin;
UI tên tiếng Việt và thời gian/thực trạng cache, không % hay ETA giả. Chuẩn bị context, tải/nạp feature,
đọc state, cache text/nạp model/mã hóa, CLIP gate, chọn DINO/coverage, detector tùy chọn, giải thích và lưu kết quả
phải có trạng thái phù hợp. Counter PROPOSE được reset khi chuyển phase; job khác giữ monotonic cũ.

Check cancel trước context, giữa sources/artifacts và chunk download; không coi CANCEL_REQUESTED là chứng minh
có file phải xóa. FE nói "đang chờ tiến trình dừng an toàn", vẫn cho xem bước vừa chạy. Giữ durable stop/cleanup,
không kill/sửa state trực tiếp. Không thêm migration/service; kiểm isolated PG/S3, parity units/selection,
cache prompt/revision/corruption, cancel, phase persistence/reset và UI. Không tự push/deploy.

### 21.41 Tài khoản demo dùng chung và nạp thông tin đăng nhập (2026-10-17)

Người dùng yêu cầu tài khoản công khai `demo123@aiia.com`, mật khẩu `demo@123`. `/login` có nút
"Nạp tài khoản demo": chuyển về đăng nhập email/mật khẩu, điền thông tin, xóa lỗi cũ; người dùng bấm
Đăng nhập để xác thực Supabase và `/me` như bình thường. Không tự signup, không bỏ auth/email confirmation
cho tài khoản khác, không token/service key trong FE. Chỉ cặp thông tin demo được duyệt là công khai.

Tạo một Auth user demo đã xác nhận bằng thao tác operator, profile/storage bootstrap theo §21.34 và policy
signup hiện có: GPU50phút mỗi4h, CPU không hạn mức tại thời điểm này, không bonus/override riêng. Không
cấp operator/model-write. Quota, phiên và dữ liệu trong tài khoản dùng chung giữa những người đăng nhập;
UI ghi rõ dùng chung và dùng dữ liệu mẫu. Không ghi đè tài khoản cùng email đã tồn tại không rõ nguồn gốc.
Không thêm migration/API/policy; không tự push/deploy.
