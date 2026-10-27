/**
 * Giải thích cấp đợt (UI_DESIGN §3c, #61, #62): mặc định chỉ là nút "?" cạnh "Đợt n"; bấm mở khung nổi, Esc / bấm ra ngoài để đóng.
 * Nội dung từ `explain_proposal` — chỉ dữ liệu thật, ghi phạm vi: (a) vì sao đợt này được đề xuất, (b) bổ sung gì so với tập đã giữ lúc đề xuất.
 * Không suy novelty / tương đồng thành lỗi model hay uplift. Nhận xét AI (sau MVP) sẽ là khối riêng có nhãn riêng.
 */
import React, { useEffect, useRef, useState } from 'react'
import { HelpCircle } from 'lucide-react'
import type { ProposalExplainDTO } from '../api'
import { Badge, fmtTime } from '../ui'

const median = (xs: number[]) => { const s = [...xs].sort((a, b) => a - b); return s.length ? s[Math.floor((s.length - 1) / 2)] : 0 }

export type CarryOver = { left: number; repeated: number }

export const BatchExplain: React.FC<{ explain?: ProposalExplainDTO; videoName: (id: string) => string; carryOver?: CarryOver; uncertaintySummary?: { signaled_count: number; total_candidates: number; note: string } }> = ({ explain: e, videoName, carryOver, uncertaintySummary }) => {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (ev: Event) => { if (ev instanceof KeyboardEvent ? ev.key === 'Escape' : !ref.current?.contains(ev.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', close); document.addEventListener('keydown', close)
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', close) }
  }, [open])
  if (!e && !uncertaintySummary) return null
  const sims = e?.nearest_kept.map(n => n.similarity) ?? []

  return (
    <div ref={ref} className="relative">
      <button onClick={() => setOpen(v => !v)} title="Vì sao đợt này được đề xuất?" aria-label="Vì sao đợt này được đề xuất?"
        className={`p-1 rounded-full cursor-pointer ${open ? 'text-[var(--text-primary)] bg-[var(--canvas-subtle)]' : 'text-[var(--text-muted)] hover:text-[var(--text-primary)]'}`}>
        <HelpCircle className="w-4 h-4" />
      </button>
      {open && (
        <div className="absolute left-0 top-8 z-30 w-[420px] rounded-lg bg-[var(--card-bg)] shadow-xl p-4 space-y-3 text-xs leading-relaxed">
          {e && (
            <section>
              <div className="font-semibold mb-0.5">Vì sao đợt {e.n} được đề xuất? <span className="font-normal text-[var(--text-muted)]">({e.n_picks} frame)</span></div>
              <p>Recipe <span className="font-mono">{e.recipe}</span>{e.description ? `: ${e.description}` : ''}</p>
              {e.semantic_query && <div className="space-y-1"><p>Câu tìm CLIP: {e.semantic_query.texts.join(' · ')}</p>
                {!!e.semantic_query.negative.length && <p>Loại trừ: {e.semantic_query.negative.join(' · ')}</p>}
                <p>Ngưỡng cosine ≥ {e.semantic_query.min_similarity}; không phải xác suất. Không tự suy/dịch mục tiêu phiên.</p></div>}
              {!e.uses_goal && !e.semantic_query && <p className="text-amber-800 dark:text-amber-300">Recipe này chưa dùng mục tiêu phiên.</p>}
              {e.relaxed && <p>Đã nới bộ lọc để đủ số frame.</p>}
            </section>
          )}
          {uncertaintySummary && (
            <section className="p-2.5 rounded-lg bg-amber-500/10 border border-amber-500/20 space-y-1">
              <div className="font-semibold text-amber-800 dark:text-amber-300">Độ ổn định perception (TTA Consistency)</div>
              <p className="text-amber-900 dark:text-amber-200">{uncertaintySummary.note}</p>
              <p className="text-[11px] text-[var(--text-muted)]">
                {uncertaintySummary.signaled_count} / {uncertaintySummary.total_candidates} ứng viên có box phát hiện để so sánh.
              </p>
            </section>
          )}
          {e && (
            <>
              <section>
                <div className="font-semibold mb-0.5 flex items-center gap-2">So với {e.n_kept_at_proposal} frame đã giữ lúc đề xuất
                  <Badge title="Số đo từ dữ liệu, không phải nhận xét AI">Dữ liệu đo</Badge></div>
                {e.n_kept_at_proposal === 0
                  ? <p>Chưa có frame nào được giữ, chưa so được.</p>
                  : <p>Tương đồng với frame đã giữ gần nhất: trung vị <b>{median(sims).toFixed(2)}</b>, cao nhất {Math.max(...sims).toFixed(2)}
                    <span className="text-[var(--text-muted)]"> (cos, {e.similarity_field}; thấp = khác nhiều hơn{!e.field_used_by_recipe && '; recipe không dùng embedding này'})</span></p>}
                <ul className="mt-1 text-[var(--text-secondary)]">
                  {e.spread.map(s => <li key={s.video_id}>{videoName(s.video_id)} · {fmtTime(s.t_min).slice(0, 5)}–{fmtTime(s.t_max).slice(0, 5)} · {s.n} frame</li>)}
                </ul>
              </section>
              {!!carryOver?.left && (
                <p className="text-[var(--text-secondary)]">Đợt trước còn {carryOver.left} frame chưa duyệt — vẫn ở pool, có thể được đề xuất lại (không tính là Bỏ).
                  {!!carryOver.repeated && ` ${carryOver.repeated} / ${e.n_picks} frame của đợt này đã có ở đợt trước.`}</p>
              )}
              <details className="text-[11px] text-[var(--text-secondary)]">
                <summary className="cursor-pointer">Cách chấm</summary>
                <ul className="mt-1 space-y-1">{e.components.map(c => <li key={c.role + c.type}><span className="font-mono">{c.role} · {c.type}</span>: {c.doc}</li>)}</ul>
              </details>
            </>
          )}
        </div>
      )}
    </div>
  )
}
