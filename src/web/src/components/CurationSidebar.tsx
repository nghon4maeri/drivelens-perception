/**
 * Panel phải của Studio (UI_DESIGN §2, §6; #61): mục tiêu phiên + ngân sách K luôn hiện; bản đồ `embedding_map`, 2 chỉ số mô tả
 * (gần trùng, keep rate) và recipe ("Nâng cao") nằm trong khối gập, mặc định đóng. Thời gian duyệt / đánh giá ở màn Phân tích. Bản đồ chỉ tải khi `mapVersion` đổi (đợt mới / pool đổi) — màu điểm
 * (giữ / bỏ / đợt này) tính ở FE từ state; chỉ số tải theo `metricsVersion` (trễ ~1 s sau thao tác cuối).
 */
import React, { useEffect, useMemo, useState } from 'react'
import { ChevronDown, ChevronLeft, ChevronRight } from 'lucide-react'
import { ApiError, api, errorText, RECIPE_LABELS, type MetricsDTO, type RecipeDTO, type ProposalDTO } from '../api'
import { PILOT } from '../mode'
import { scale } from '../renderers'
import { ErrorBanner, IconButton, Label, ProgressBar, type Status } from '../ui'
import { RecipePanel, type RecipeSel } from './RecipePanel'
import type { GridItem } from './DashcamThumbnail'

interface Props {
  sid: string
  mapVersion: number
  metricsVersion: number
  isOpen: boolean
  onToggle: () => void
  advancedOpen: boolean
  onAdvancedOpen: (open: boolean) => void
  goal: string
  kept: number
  targetK: number
  items: GridItem[]
  statusOf: (id: string) => Status
  focusedIndex: number
  onSelectIndex: (i: number) => void
  recipes: RecipeDTO[]
  shown?: ProposalDTO
  recipeSel?: RecipeSel
  onRecipeSel: (s: RecipeSel) => void
  onOpenUnit: (unitId: string) => void
}

/** Pilot: chưa có kết quả phân tích là bình thường (bấm mới tính) → không hiện lỗi. */
const quiet = (e: unknown, set: (s: string) => void) => {
  if (PILOT && e instanceof ApiError && ['not_found', 'view_unavailable'].includes(e.code)) return
  set(errorText(e))
}

type MapData = { x: number[]; y: number[]; unit_id: string[] }
const DOT = { undecided: 'fill-neutral-400 opacity-40', dropped: 'fill-red-500 opacity-40', kept: 'fill-[var(--kept-border)]' }

export const CurationSidebar: React.FC<Props> = p => {
  const [goalOpen, setGoalOpen] = useState(false)
  const [map, setMap] = useState<MapData>()
  const [metrics, setMetrics] = useState<MetricsDTO>()
  const [error, setError] = useState('')

  useEffect(() => {
    if (p.isOpen) api.view(p.sid, 'embedding_map', {}).then(v => setMap(v.data as MapData)).catch(e => quiet(e, setError))
  }, [p.sid, p.mapVersion, p.isOpen])
  useEffect(() => {
    if (p.isOpen) api.metrics(p.sid).then(setMetrics).catch(e => quiet(e, setError))
  }, [p.sid, p.metricsVersion, p.isOpen])

  const pts = useMemo(() => {
    if (!map) return []
    const sx = scale(map.x), sy = scale(map.y)
    return map.unit_id.map((id, i) => ({ id, cx: 8 + sx(map.x[i]) * 284, cy: 172 - sy(map.y[i]) * 164 }))
  }, [map])
  const batchIndex = useMemo(() => new Map(p.items.map((it, i) => [it.unit.id, i])), [p.items])

  if (!p.isOpen) {
    return (
      <aside className="w-12 h-full bg-[var(--canvas-subtle)] flex flex-col items-center py-3 shrink-0">
        <IconButton label="Mở panel (P)" icon={<ChevronLeft className="w-4 h-4" />} onClick={p.onToggle} />
        <div className="mt-6 [writing-mode:vertical-rl] rotate-180 text-xs font-mono text-[var(--kept-border)] font-bold">{p.kept} / {p.targetK}</div>
      </aside>
    )
  }

  const nd = metrics?.results.find(r => r.type === 'nd_rate_at_k')
  const kr = metrics?.results.find(r => r.type === 'keep_rate_by_recipe')
  // điểm thường vẽ trước (còn lại → bỏ → giữ), điểm của đợt đang xem vẽ sau cùng
  const rank = { undecided: 0, dropped: 1, kept: 2 }
  const background = pts.filter(q => !batchIndex.has(q.id)).sort((a, b) => rank[p.statusOf(a.id)] - rank[p.statusOf(b.id)])

  return (
    <aside className="w-[340px] 2xl:w-[380px] h-full bg-[var(--canvas-subtle)] flex flex-col shrink-0 overflow-y-auto">
      <div className="p-4 space-y-5">
        <div className="flex items-start gap-2">
          <button onClick={() => setGoalOpen(v => !v)} title={p.goal} className="flex-1 min-w-0 text-left cursor-pointer">
            <Label>Mục tiêu phiên</Label>
            <div className={`text-xs leading-snug mt-0.5 ${goalOpen ? '' : 'truncate'}`}>{p.goal}</div>
          </button>
          <IconButton label="Thu gọn (P)" icon={<ChevronRight className="w-4 h-4" />} onClick={p.onToggle} />
        </div>

        <div className="space-y-2">
          <div className="flex items-baseline justify-between">
            <Label>Ngân sách K</Label>
            <span className="font-mono text-sm font-bold text-[var(--kept-border)]">{p.kept} / {p.targetK}</span>
          </div>
          <ProgressBar value={(100 * p.kept) / Math.max(1, p.targetK)} />
        </div>

        <ErrorBanner message={error} onClose={() => setError('')} />

        {PILOT && !map && !metrics && <p className="text-[11px] text-[var(--text-muted)]">Bản đồ và chỉ số: mở tab Phân tích, bấm "Tính phân tích".</p>}
        <><Fold title="Bản đồ ảnh (2D)" hint="PCA 2D của DINOv2 — chỉ để khám phá, khoảng cách 2D bị méo">
          <div className="w-full aspect-[300/180] rounded-lg bg-[var(--card-bg)] overflow-hidden">
            <svg viewBox="0 0 300 180" className="w-full h-full">
              {background.map(q => {
                const st = p.statusOf(q.id)
                return <circle key={q.id} cx={q.cx} cy={q.cy} r={st === 'undecided' ? 1.4 : 2.6} className={DOT[st]} style={{ cursor: 'pointer' }}
                  onClick={() => p.onOpenUnit(q.id)}><title>{q.id}</title></circle>
              })}
              {pts.filter(q => batchIndex.has(q.id)).map(q => {
                const idx = batchIndex.get(q.id)!, it = p.items[idx], foc = idx === p.focusedIndex
                return (
                  <circle key={q.id} cx={q.cx} cy={q.cy} r={foc ? 5 : 3.4} stroke={foc ? 'var(--border-focus)' : '#fff'} strokeWidth={foc ? 2 : 0.8}
                    className={it.status === 'kept' ? 'fill-[var(--kept-border)]' : it.status === 'dropped' ? 'fill-neutral-500 opacity-40' : 'fill-amber-500'}
                    style={{ cursor: 'pointer' }} onClick={() => p.onSelectIndex(idx)}><title>#{idx + 1} · {q.id}</title></circle>
                )
              })}
            </svg>
          </div>
          <div className="flex gap-3 text-[11px] text-[var(--text-secondary)]">
            <span><i className="inline-block w-2 h-2 rounded-full bg-[var(--kept-border)] mr-1" />giữ</span>
            <span><i className="inline-block w-2 h-2 rounded-full bg-amber-500 mr-1" />đợt này</span>
            <span><i className="inline-block w-2 h-2 rounded-full bg-red-500/50 mr-1" />bỏ</span>
            <span><i className="inline-block w-2 h-2 rounded-full bg-neutral-400 mr-1" />còn lại</span>
          </div>
        </Fold>

        <Fold title="Chỉ số mô tả tập đã giữ" hint="Số mô tả, không phải đánh giá chất lượng — xem màn Phân tích">
        <div className="grid grid-cols-2 gap-3">
          <div title={`Tỉ lệ frame đã giữ có frame gần trùng trong tập (pHash, Hamming ≤ ${String(nd?.params.threshold ?? 8)}). Thấp = tốt.`}>
            <Label>Gần trùng</Label>
            <div className="font-mono text-xl font-bold">{nd ? `${(100 * nd.values.nd_rate_at_k).toFixed(1)}%` : '…'}</div>
          </div>
          <div title={kr ? Object.entries(kr.values).filter(([k]) => k.startsWith('recipe/')).map(([k, v]) => `${k.slice(7)}: ${(100 * v).toFixed(0)}%`).join('\n') || 'giữ / (giữ + bỏ)' : ''}>
            <Label>Keep rate</Label>
            <div className="font-mono text-xl font-bold">{kr ? `${(100 * kr.values.keep_rate_by_recipe).toFixed(1)}%` : '…'}</div>
          </div>
        </div>
        </Fold></>

        {p.shown && <section className="space-y-1 text-xs"><h3 className="font-semibold">Strategy của đợt đang xem · {p.shown.n}</h3><p>{p.recipes.find(r => r.name === p.shown?.input.recipe)?.strategy?.title ?? RECIPE_LABELS[p.shown.input.recipe] ?? p.shown.input.recipe}</p><p>Cấu hình đã cố định khi tạo đợt; chỉnh bên dưới chỉ áp dụng đợt tiếp theo.</p><details><summary>Đầu vào đã lưu (chỉ đọc)</summary><pre className="text-[10px] whitespace-pre-wrap break-all">{JSON.stringify(p.shown.input, null, 2)}</pre></details></section>}
        {p.recipeSel && <RecipePanel recipes={p.recipes} sel={p.recipeSel} onChange={p.onRecipeSel} />}

      </div>
    </aside>
  )
}

const Fold = ({ title, hint, children }: { title: string; hint: string; children: React.ReactNode }) => (
  <details className="group">
    <summary title={hint} className="flex items-center justify-between py-1 text-xs font-semibold text-[var(--text-secondary)] hover:text-[var(--text-primary)] cursor-pointer list-none">
      {title}<ChevronDown className="w-3.5 h-3.5 transition-transform group-open:rotate-180" />
    </summary>
    <div className="mt-2 space-y-2">{children}</div>
  </details>
)
