/**
 * DriveLens Landing Page — Public Showcase (UI_DESIGN §1).
 * Ảnh tĩnh đóng gói cùng bundle: ảnh chụp Studio thật và frame từ một snapshot export thật (đã che PII);
 * số liệu frame/vùng PII lấy từ manifest export (`assets/landing/frames.json`). Không gọi API, không dữ liệu private.
 */
import { useState } from 'react'
import {
  ArrowRight,
  CheckCircle2,
  Cpu,
  Database,
  FileCheck,
  Film,
  HardDrive,
  Keyboard,
  Layers,
  LineChart,
  ShieldCheck,
  Sparkles,
  Target,
} from 'lucide-react'
import manifest from '../assets/landing/frames.json'
import studioShot from '../assets/landing/studio.webp'
import architecture from '../assets/landing/architecture.svg'
import logo from '../assets/logo.svg'

const frameUrls = import.meta.glob('../assets/landing/frame-*.webp', { eager: true, import: 'default' }) as Record<string, string>
const frameUrl = (name: string) => frameUrls[`../assets/landing/${name}`]

type Frame = (typeof manifest.frames)[number]
const PII_SHOWCASE = [8, 33]
const gridFrames = manifest.frames.filter(f => !PII_SHOWCASE.includes(f.o))
const piiFrames = manifest.frames.filter(f => PII_SHOWCASE.includes(f.o))

const clock = (t: number) => {
  const m = Math.floor(t / 60)
  return `${String(m).padStart(2, '0')}:${(t - m * 60).toFixed(2).padStart(5, '0')}`
}
const piiLabel = (f: Frame) =>
  f.faces + f.plates === 0 ? 'Không phát hiện vùng PII' : [f.plates && `${f.plates} biển số`, f.faces && `${f.faces} khuôn mặt`].filter(Boolean).join(' · ')

const STRATEGIES = [
  { name: 'odd_coverage', title: 'Tìm tình huống và bổ sung coverage', mode: 'Nền', desc: 'CLIP lọc từng nhánh ODD × đối tượng và scenario, hợp ứng viên rồi chọn theo coverage và DINOv2; tùy chọn đo thiếu ổn định bằng model.' },
  { name: 'perception_instability', title: 'Thiếu ổn định của detector', mode: 'Nền', desc: 'Ưu tiên frame mà model detection dự đoán khác nhau giữa ảnh gốc và ảnh lật; DINOv2 chống trùng lặp.' },
  { name: 'context_uncertainty', title: 'Ngữ cảnh + độ không chắc chắn', mode: 'Nền', desc: 'Lọc ngữ cảnh bằng CLIP, đa dạng bằng DINOv2, rồi kiểm tra nhất quán YOLO trên tập được yêu cầu.' },
  { name: 'text_diversity', title: 'Tìm theo câu mô tả', mode: 'Tương tác', desc: 'CLIP lọc theo câu tìm / loại trừ; DINOv2 ưu tiên frame khác tập đã giữ, không ép đủ đợt.' },
  { name: 'diversity', title: 'Đa dạng thị giác', mode: 'Tương tác', desc: 'Novelty trên DINOv2 so với tập đã giữ, chọn đợt bằng greedy + NMS thời gian để không trùng nhau.' },
  { name: 'random · uniform', title: 'Baseline', mode: 'Tương tác', desc: 'Ngẫu nhiên hoặc rải đều theo thời gian, dùng để so sánh với các strategy thông minh.' },
]

const STEPS = [
  { icon: Database, title: 'Kho dữ liệu', desc: 'Upload video (chia chunk, tiếp tục khi gián đoạn) hoặc dùng Dữ liệu mẫu đã có frame 4 fps và đặc trưng DINOv2/CLIP.' },
  { icon: Target, title: 'Tạo phiên', desc: 'Chọn video, viết mục tiêu phiên và đặt ngân sách K: số frame tối đa muốn giữ để gán nhãn.' },
  { icon: Layers, title: 'Studio duyệt', desc: 'Chọn strategy, nhận từng đợt đề xuất, Giữ / Bỏ trên lưới ảnh; xem bước chạy và thời gian thật của strategy nền.' },
  { icon: LineChart, title: 'Phân tích', desc: 'Chạy YOLO trên đợt đề xuất với model đã xác minh, xem bản đồ ảnh 2D và chỉ số mô tả tập đã giữ.' },
  { icon: FileCheck, title: 'Snapshot & export', desc: 'Đóng băng tập đã giữ, xem trước ảnh che mặt / biển số, xác nhận đúng hash rồi tải ZIP kèm manifest.' },
]

const KEYS: [string, string][] = [
  ['K / 1', 'Giữ frame'], ['D / 2', 'Bỏ frame'], ['U', 'Bỏ quyết định'], ['A / X', 'Giữ / bỏ cả đợt'],
  ['Ctrl+Z', 'Hoàn tác'], ['Space', 'Xem lớn'], ['Enter', 'Đề xuất tiếp'], ['M', 'Bật / tắt box YOLO'],
]

export function LandingScreen() {
  const [selected, setSelected] = useState(gridFrames[0].o)
  const [piiIndex, setPiiIndex] = useState(0)
  const [showBoxes, setShowBoxes] = useState(true)
  const current = gridFrames.find(f => f.o === selected) ?? gridFrames[0]
  const piiFrame = piiFrames[piiIndex]

  return (
    <div className="min-h-screen bg-[var(--canvas-bg)] text-[var(--text-primary)] selection:bg-[var(--accent)] selection:text-white">
      {/* ── TOP BANNER ── */}
      <div className="border-b border-[var(--border-color)] bg-[var(--canvas-subtle)] px-4 py-2 text-center text-xs font-medium text-[var(--text-secondary)]">
        <span className="mr-2 inline-flex items-center gap-1 rounded-full bg-[var(--kept-bg)] px-2.5 py-0.5 text-xs font-semibold text-[var(--kept-text)]">
          <Sparkles className="h-3 w-3" /> Pilot đang chạy
        </span>
        Chọn frame dashcam đáng gán nhãn cho ADAS & Robotics · có tài khoản demo dùng chung
      </div>

      {/* ── HEADER ── */}
      <header className="sticky top-0 z-50 border-b border-[var(--border-color)] bg-[var(--canvas-bg)]/90 backdrop-blur-md">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-3 px-4 py-4 sm:px-10">
          <a href="/" className="flex min-w-0 items-center gap-2 group sm:gap-3">
            <img src={logo} alt="" className="h-10 w-10 shrink-0 shadow-sm rounded-xl transition-transform group-hover:scale-105" />
            <div>
              <span className="text-xl font-bold tracking-tight">DriveLens</span>
              <p className="hidden text-[10px] font-medium text-[var(--text-secondary)] sm:block">Dashcam Data Curation Studio</p>
            </div>
          </a>

          <nav className="hidden items-center gap-5 whitespace-nowrap text-sm font-medium text-[var(--text-secondary)] xl:flex">
            <a href="#studio" className="transition hover:text-[var(--text-primary)]">Studio</a>
            <a href="#strategies" className="transition hover:text-[var(--text-primary)]">Strategy</a>
            <a href="#pii" className="transition hover:text-[var(--text-primary)]">Che PII</a>
            <a href="#workflow" className="transition hover:text-[var(--text-primary)]">Quy trình</a>
            <a href="#architecture" className="transition hover:text-[var(--text-primary)]">Kiến trúc</a>
          </nav>

          <div className="flex shrink-0 items-center gap-3">
            <a href="/login" className="hidden rounded-xl px-4 py-2 text-sm font-semibold text-[var(--text-secondary)] transition hover:text-[var(--text-primary)] sm:inline-block">
              Đăng nhập
            </a>
            <a href="/app" className="inline-flex items-center gap-2 whitespace-nowrap rounded-xl bg-[var(--accent)] px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:opacity-90 active:scale-95">
              Vào Studio <ArrowRight className="h-4 w-4" />
            </a>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 sm:px-10">
        {/* ── HERO ── */}
        <section className="pb-14 pt-14 sm:pt-20">
          <div className="mx-auto max-w-4xl text-center">
            <h1 className="text-4xl font-extrabold tracking-tight sm:text-6xl sm:leading-[1.15]">
              Hàng giờ video hành trình.{' '}
              <span className="bg-gradient-to-r from-[var(--accent)] via-emerald-600 to-teal-500 bg-clip-text text-transparent">
                Chỉ giữ những frame đáng gán nhãn.
              </span>
            </h1>
            <p className="mx-auto mt-6 max-w-2xl text-base leading-relaxed text-[var(--text-secondary)] sm:text-lg">
              Video dashcam lặp lại rất nhiều. <strong className="text-[var(--text-primary)]">DriveLens</strong> tách frame 4 fps,
              dùng DINOv2 và CLIP để đề xuất từng đợt frame đa dạng hoặc đúng tình huống bạn tìm, để kỹ sư Giữ / Bỏ trong
              ngân sách gán nhãn rồi xuất snapshot đã che PII.
            </p>
            <div className="mt-9 flex flex-wrap items-center justify-center gap-4">
              <a href="/app" className="inline-flex items-center gap-2.5 rounded-xl bg-[var(--accent)] px-7 py-3.5 text-base font-semibold text-white shadow-md transition hover:opacity-95 hover:shadow-lg active:scale-95">
                Mở Studio <ArrowRight className="h-5 w-5" />
              </a>
              <a href="/login" className="inline-flex items-center gap-2 rounded-xl border border-[var(--border-color)] bg-[var(--card-bg)] px-6 py-3.5 text-base font-semibold transition hover:bg-[var(--canvas-subtle)]">
                Dùng tài khoản demo
              </a>
            </div>
            <p className="mt-3 text-xs text-[var(--text-secondary)]">Trang đăng nhập có nút “Nạp tài khoản demo”; quota và phiên của tài khoản này dùng chung.</p>
          </div>

          <figure className="mt-12 overflow-hidden rounded-2xl border border-[var(--border-color)] bg-[var(--card-bg)] shadow-xl">
            <img src={studioShot} alt="Studio duyệt của DriveLens: lưới frame của một đợt đề xuất và panel cấu hình strategy" className="w-full" width={1600} height={921} />
            <figcaption className="border-t border-[var(--border-color)] px-4 py-2.5 text-xs text-[var(--text-secondary)]">
              Ảnh chụp Studio thật: đợt đề xuất của strategy “Tìm tình huống và bổ sung coverage”, ngân sách 42 / 50. Biển số đọc được đã làm mờ.
            </figcaption>
          </figure>
        </section>

        {/* ── CAPABILITIES ── */}
        <section className="mb-20 rounded-3xl border border-[var(--border-color)] bg-[var(--card-bg)] p-6 shadow-sm sm:p-8">
          <div className="grid grid-cols-2 gap-6 md:grid-cols-4">
            {[
              ['Người duyệt', 'Kỹ sư quyết định', 'Mỗi đợt chỉ là đề xuất; Giữ / Bỏ / hoàn tác trước khi chốt snapshot.', 'text-[var(--accent)]'],
              ['DINOv2', 'Đa dạng thị giác', 'So đặc trưng ảnh với tập đã giữ để tránh frame trùng lặp.', 'text-teal-600'],
              ['CLIP', 'Tìm theo ngữ nghĩa', 'Tìm bằng câu mô tả hoặc ODD × đối tượng trên nguồn đã có đặc trưng.', 'text-amber-600'],
              ['4 fps', 'Lưới frame cố định', 'Frame chính xác theo mốc thời gian, truy lại được video và vị trí gốc.', 'text-indigo-600'],
            ].map(([big, title, desc, color]) => (
              <div key={big}>
                <p className={`text-2xl font-extrabold tracking-tight sm:text-3xl ${color}`}>{big}</p>
                <p className="mt-2 text-sm font-bold">{title}</p>
                <p className="mt-1 text-xs leading-relaxed text-[var(--text-secondary)]">{desc}</p>
              </div>
            ))}
          </div>
        </section>

        {/* ── REAL SNAPSHOT FRAMES ── */}
        <section id="studio" className="mb-24 scroll-mt-24">
          <div className="mb-8">
            <p className="text-xs font-bold uppercase tracking-widest text-[var(--accent)]">Dữ liệu thật</p>
            <h2 className="mt-2 text-3xl font-bold tracking-tight">Frame từ một snapshot đã export</h2>
            <p className="mt-2 max-w-3xl text-sm text-[var(--text-secondary)]">
              {gridFrames.length} trong {manifest.n_members} frame của một snapshot được giữ trên pilot, lấy từ gói ZIP export (ảnh đã qua bước che PII).
              Mốc thời gian, chỉ số frame và số vùng PII lấy nguyên từ manifest; bấm một ô để xem chi tiết.
            </p>
          </div>

          <div className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
            <div className="overflow-hidden rounded-3xl border border-[var(--border-color)] bg-slate-950 shadow-xl">
              <img src={frameUrl(current.name)} alt={`Frame video ${current.video} tại ${clock(current.t)}`} className="aspect-video w-full object-cover" width={640} height={360} />
              <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-800 px-5 py-3 font-mono text-xs text-slate-300">
                <span>Video {current.video} · {clock(current.t)} · frame #{current.frame}</span>
                <span className="text-emerald-400">{piiLabel(current)}</span>
              </div>
            </div>

            <div className="rounded-3xl border border-[var(--border-color)] bg-[var(--card-bg)] p-5 shadow-sm">
              <div className="flex items-center justify-between border-b border-[var(--border-color)] pb-3">
                <span className="flex items-center gap-2 text-sm font-bold"><Layers className="h-4 w-4 text-[var(--accent)]" /> Lưới frame đã giữ</span>
                <span className="font-mono text-xs text-[var(--text-secondary)]">{gridFrames.length} frame</span>
              </div>
              <div className="mt-4 grid grid-cols-3 gap-2 sm:grid-cols-4">
                {gridFrames.map(f => (
                  <button
                    key={f.o}
                    onClick={() => setSelected(f.o)}
                    aria-pressed={f.o === current.o}
                    aria-label={`Video ${f.video} tại ${clock(f.t)}`}
                    className={`relative overflow-hidden rounded-lg border-2 transition ${f.o === current.o ? 'border-[var(--accent)] shadow-md' : 'border-transparent opacity-80 hover:opacity-100'}`}
                  >
                    <img src={frameUrl(f.name)} alt="" loading="lazy" className="aspect-video w-full object-cover" width={640} height={360} />
                    <span className="absolute bottom-0.5 left-0.5 rounded bg-black/70 px-1 font-mono text-[9px] text-white">{clock(f.t)}</span>
                  </button>
                ))}
              </div>
              <p className="mt-4 text-[11px] leading-relaxed text-[var(--text-secondary)]">
                Trong Studio, lưới hiển thị từng đợt đề xuất kèm trạng thái “Chưa duyệt / Giữ / Bỏ”; ở đây là kết quả cuối cùng sau khi duyệt.
              </p>
            </div>
          </div>

          <div className="mt-6 rounded-2xl border border-[var(--border-color)] bg-[var(--card-bg)] p-5">
            <p className="flex items-center gap-2 text-sm font-bold"><Keyboard className="h-4 w-4 text-[var(--accent)]" /> Phím tắt trong Studio</p>
            <div className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
              {KEYS.map(([k, label]) => (
                <div key={k} className="flex items-center gap-2 text-xs text-[var(--text-secondary)]">
                  <kbd className="rounded border border-[var(--border-color)] bg-[var(--canvas-subtle)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--text-primary)]">{k}</kbd>
                  {label}
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* ── STRATEGIES ── */}
        <section id="strategies" className="mb-24 scroll-mt-24">
          <div className="text-center">
            <p className="text-xs font-bold uppercase tracking-widest text-[var(--accent)]">Cách đề xuất</p>
            <h2 className="mt-2 text-3xl font-bold tracking-tight sm:text-4xl">Chọn strategy theo mục tiêu từng đợt</h2>
            <p className="mx-auto mt-4 max-w-2xl text-sm text-[var(--text-secondary)]">
              Strategy <em>tương tác</em> trả đợt mới gần như ngay khi bấm “Đề xuất tiếp”. Strategy <em>nền</em> chạy trên worker,
              Studio hiện từng bước và thời gian đo được. Kết quả là tín hiệu để người duyệt quyết định, không phải chỉ số độ chính xác model.
            </p>
          </div>
          <div className="mt-10 grid gap-5 md:grid-cols-2 lg:grid-cols-3">
            {STRATEGIES.map(s => (
              <div key={s.name} className="rounded-2xl border border-[var(--border-color)] bg-[var(--card-bg)] p-6 shadow-sm">
                <div className="flex items-center justify-between gap-2">
                  <code className="text-[11px] text-[var(--text-secondary)]">{s.name}</code>
                  <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${s.mode === 'Nền' ? 'bg-amber-500/10 text-amber-700 dark:text-amber-400' : 'bg-[var(--kept-bg)] text-[var(--kept-text)]'}`}>{s.mode}</span>
                </div>
                <h3 className="mt-3 text-lg font-bold">{s.title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-[var(--text-secondary)]">{s.desc}</p>
              </div>
            ))}
          </div>
          <p className="mt-5 text-center text-xs text-[var(--text-secondary)]">
            Strategy cần dataset train tham chiếu (reference / model-guided) đã có trong catalog nhưng chưa mở trên pilot.
          </p>
        </section>

        {/* ── PII ── */}
        <section id="pii" className="mb-24 scroll-mt-24">
          <div className="rounded-3xl border border-[var(--border-color)] bg-[var(--card-bg)] p-6 shadow-sm sm:p-10">
            <div className="grid items-center gap-10 lg:grid-cols-[1fr_1.3fr]">
              <div>
                <div className="inline-flex items-center gap-2 rounded-full border border-emerald-300 bg-emerald-50 px-3.5 py-1 text-xs font-semibold text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300">
                  <ShieldCheck className="h-4 w-4" /> Che PII khi export
                </div>
                <h2 className="mt-4 text-3xl font-bold tracking-tight">Che mặt và biển số trước khi bàn giao</h2>
                <p className="mt-4 text-sm leading-relaxed text-[var(--text-secondary)]">
                  Export đọc lại đúng frame gốc, phát hiện khuôn mặt (YuNet) và biển số (YOLOv9-tiny ONNX), làm mờ vùng phát hiện,
                  rồi tạo ảnh xem trước và báo cáo. Chủ phiên phải xác nhận đúng hash nội dung thì ZIP mới được tạo.
                </p>
                <p className="mt-3 text-sm leading-relaxed text-[var(--text-secondary)]">
                  Hai model rất nhẹ để chạy trên CPU nên vẫn có thể bỏ sót hoặc nhận nhầm. Ví dụ ảnh đầu tiên bên cạnh có vùng
                  “khuôn mặt” thực chất là biển quảng cáo. Người duyệt cần kiểm tra ảnh xem trước trước khi xác nhận.
                </p>
                <div className="mt-6 flex flex-wrap items-center gap-3">
                  <div className="inline-flex rounded-xl border border-[var(--border-color)] bg-[var(--canvas-subtle)] p-1">
                    {piiFrames.map((f, i) => (
                      <button
                        key={f.o}
                        onClick={() => setPiiIndex(i)}
                        aria-pressed={i === piiIndex}
                        className={`rounded-lg px-3.5 py-1.5 text-xs font-semibold transition ${i === piiIndex ? 'bg-[var(--accent)] text-white shadow-sm' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'}`}
                      >
                        Ảnh {i + 1}
                      </button>
                    ))}
                  </div>
                  <label className="flex cursor-pointer items-center gap-2 text-xs font-semibold text-[var(--text-secondary)]">
                    <input type="checkbox" checked={showBoxes} onChange={e => setShowBoxes(e.target.checked)} className="accent-[var(--accent)]" />
                    Hiện vùng đã phát hiện
                  </label>
                </div>
              </div>

              <figure className="overflow-hidden rounded-2xl border border-[var(--border-color)] bg-slate-950 shadow-lg">
                <div className="relative">
                  <img src={frameUrl(piiFrame.name)} alt={`Ảnh export đã che PII: ${piiLabel(piiFrame)}`} className="w-full" width={960} height={540} />
                  {showBoxes && piiFrame.boxes.map((b, i) => (
                    <div
                      key={i}
                      className={`absolute border-2 ${b.kind === 'face' ? 'border-amber-400' : 'border-emerald-400'}`}
                      style={{ left: `${b.box[0] * 100}%`, top: `${b.box[1] * 100}%`, width: `${(b.box[2] - b.box[0]) * 100}%`, height: `${(b.box[3] - b.box[1]) * 100}%` }}
                    >
                      <span className={`absolute -top-4 left-0 whitespace-nowrap rounded px-1 font-mono text-[9px] font-bold text-slate-950 ${b.kind === 'face' ? 'bg-amber-400' : 'bg-emerald-400'}`}>
                        {b.kind === 'face' ? 'mặt' : 'biển số'} {b.conf.toFixed(2)}
                      </span>
                    </div>
                  ))}
                </div>
                <figcaption className="flex flex-wrap justify-between gap-2 border-t border-slate-800 px-4 py-2.5 font-mono text-[11px] text-slate-400">
                  <span>{piiLabel(piiFrame)} · ảnh trong ZIP đã làm mờ</span>
                  <span>{manifest.models.pipeline_version}</span>
                </figcaption>
              </figure>
            </div>
          </div>
        </section>

        {/* ── WORKFLOW ── */}
        <section id="workflow" className="mb-24 scroll-mt-24">
          <div className="text-center">
            <p className="text-xs font-bold uppercase tracking-widest text-[var(--accent)]">Quy trình trong app</p>
            <h2 className="mt-2 text-3xl font-bold tracking-tight sm:text-4xl">Từ video đến tập frame sẵn sàng gán nhãn</h2>
          </div>
          <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
            {STEPS.map(({ icon: Icon, title, desc }, i) => (
              <div key={title} className="rounded-2xl border border-[var(--border-color)] bg-[var(--card-bg)] p-5">
                <div className="flex items-center justify-between">
                  <Icon className="h-5 w-5 text-[var(--accent)]" />
                  <span className="font-mono text-sm font-bold text-[var(--text-secondary)]">0{i + 1}</span>
                </div>
                <h3 className="mt-4 font-bold">{title}</h3>
                <p className="mt-2 text-xs leading-relaxed text-[var(--text-secondary)]">{desc}</p>
              </div>
            ))}
          </div>
          <div className="mt-6 grid gap-4 sm:grid-cols-3">
            {[
              [HardDrive, 'Upload lớn có resume', 'Video đi qua ingress trên VPS theo từng chunk, tiến trình giữ khi chuyển màn hoặc tải lại trang.'],
              [Film, 'Xem lại ngữ cảnh', 'Mở frame lớn và clip ±3 giây quanh mốc thời gian để quyết định chính xác hơn.'],
              [Cpu, 'Kho model', 'Admin tải lên và xác minh checkpoint YOLO; người dùng chọn model đã xuất bản cho phân tích.'],
            ].map(([Icon, title, desc]) => {
              const I = Icon as typeof HardDrive
              return (
                <div key={title as string} className="flex gap-3 rounded-2xl border border-[var(--border-color)] bg-[var(--canvas-subtle)] p-5">
                  <I className="mt-0.5 h-5 w-5 shrink-0 text-[var(--accent)]" />
                  <div>
                    <p className="text-sm font-bold">{title as string}</p>
                    <p className="mt-1 text-xs leading-relaxed text-[var(--text-secondary)]">{desc as string}</p>
                  </div>
                </div>
              )
            })}
          </div>
        </section>

        {/* ── ARCHITECTURE ── */}
        <section id="architecture" className="mb-24 scroll-mt-24">
          <div className="mx-auto max-w-3xl text-center">
            <p className="text-xs font-bold uppercase tracking-widest text-[var(--accent)]">Hạ tầng pilot</p>
            <h2 className="mt-2 text-3xl font-bold tracking-tight">Kiến trúc đang deploy</h2>
            <p className="mt-4 text-sm leading-relaxed text-[var(--text-secondary)]">
              Render phục vụ giao diện và API nhẹ; Supabase giữ đăng nhập và trạng thái; worker trên Contabo nhận upload, trả đề xuất
              tương tác và chạy các job dài; S3 giữ video, frame, đặc trưng và gói export. GPU Kaggle là phần tùy chọn.
            </p>
          </div>
          <div className="mt-8 overflow-x-auto rounded-2xl border border-[var(--border-color)] bg-[#F7F5EE] p-2 shadow-sm">
            <img src={architecture} alt="Sơ đồ kiến trúc DriveLens: web React, Render FastAPI, Supabase Auth và PostgreSQL, Contabo VPS với CLIP/YOLO/YuNet, S3 và Kaggle GPU tùy chọn" className="min-w-[760px] w-full" width={1280} height={660} />
          </div>
        </section>

        {/* ── CTA ── */}
        <section className="mb-16 rounded-3xl bg-gradient-to-tr from-[var(--accent)] to-teal-800 p-8 text-center text-white shadow-xl sm:p-14">
          <h2 className="text-3xl font-extrabold tracking-tight sm:text-4xl">Thử ngay với dữ liệu mẫu</h2>
          <p className="mx-auto mt-4 max-w-xl text-base text-emerald-100">
            Đăng ký bằng email hoặc dùng tài khoản demo, chọn một video trong Dữ liệu mẫu và tạo phiên đầu tiên.
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-4">
            <a href="/app" className="inline-flex items-center gap-2 rounded-xl bg-white px-7 py-3.5 text-base font-bold text-[var(--accent)] shadow-md transition hover:bg-slate-100 active:scale-95">
              Mở Studio <ArrowRight className="h-5 w-5" />
            </a>
            <a href="/login" className="inline-flex items-center gap-2 rounded-xl border border-white/30 bg-white/10 px-6 py-3.5 text-base font-semibold text-white backdrop-blur-md transition hover:bg-white/20">
              <CheckCircle2 className="h-5 w-5" /> Tài khoản demo
            </a>
          </div>
        </section>
      </main>

      {/* ── FOOTER ── */}
      <footer className="border-t border-[var(--border-color)] bg-[var(--canvas-subtle)] px-4 py-10 sm:px-10">
        <div className="mx-auto flex max-w-7xl flex-col gap-6">
          <div className="flex flex-col items-start justify-between gap-4 md:flex-row md:items-center">
            <div className="flex items-center gap-3">
              <img src={logo} alt="" className="h-8 w-8" />
              <span className="font-bold tracking-tight">DriveLens</span>
              <span className="text-xs text-[var(--text-secondary)]">· Video Frame Curation</span>
            </div>
            <div className="flex flex-wrap gap-6 text-xs text-[var(--text-secondary)]">
              <a href="/app" className="hover:text-[var(--text-primary)]">Ứng dụng</a>
              <a href="/login" className="hover:text-[var(--text-primary)]">Đăng nhập</a>
              <a href="#strategies" className="hover:text-[var(--text-primary)]">Strategy</a>
              <a href="#architecture" className="hover:text-[var(--text-primary)]">Kiến trúc</a>
              <a href="https://github.com" className="hover:text-[var(--text-primary)]">GitHub</a>
            </div>
          </div>
          <p className="text-[11px] leading-relaxed text-[var(--text-secondary)]">
            Nguồn video minh họa: frame trích từ video dashcam tổng hợp công khai của{' '}
            <a href="https://dashcamownersaus.com.au" className="underline hover:text-[var(--text-primary)]">Dashcam Owners Australia</a>{' '}
            (watermark giữ nguyên trên ảnh). Dùng cho mục đích minh họa phi thương mại; bản quyền video thuộc chủ sở hữu.
          </p>
        </div>
      </footer>
    </div>
  )
}
