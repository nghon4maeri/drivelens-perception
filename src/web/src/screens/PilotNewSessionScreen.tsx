/**
 * Tạo phiên mới ở pilot (UI_DESIGN deploy §1, CONTRACTS §21.16): chọn video READY (của tôi hoặc hệ thống), mục tiêu, ngân sách K → `POST /sessions`.
 * Không tự đề xuất sau khi tạo; báo trước video nào chưa có DINO/CLIP (recipe Đa dạng/CLIP cần, Ngẫu nhiên/Rải đều không cần).
 */
import { useEffect, useState } from 'react'
import { Loader2, Plus } from 'lucide-react'
import { all, apiV2, StaleResponseError, type SourceV2 } from '../api_v2'
import { rememberSource } from '../pilot_api'
import { sessionHref } from '../types/navigation'
import { Button, ErrorBanner, Field, Panel, ScreenHeader, fmtDuration, inputClass } from '../ui'

const GOALS = [
  'Giữ frame khác biệt về cảnh, ánh sáng, đối tượng; bỏ frame mờ hoặc gần trùng frame đã giữ.',
  'Người đi bộ ban đêm hoặc thiếu sáng, nhìn đủ rõ để gán nhãn.',
  'Xe máy cắt ngang hoặc áp sát xe, đối tượng không bị che khuất quá nhiều.',
]

export function PilotNewSessionScreen({ preselect, onCreated }: { preselect: string[]; onCreated: () => void }) {
  const [sources, setSources] = useState<SourceV2[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set(preselect))
  const [name, setName] = useState('')
  const [goal, setGoal] = useState(GOALS[0])
  const [targetK, setTargetK] = useState(50)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    all(apiV2.sources).then(list => { list.forEach(rememberSource); setSources(list.filter(s => s.kind === 'VIDEO' && s.availability === 'READY')) })
      .catch(e => { if (!(e instanceof StaleResponseError)) setError(e.message) })
  }, [])

  const chosen = sources.filter(s => selected.has(s.id))
  const units = chosen.reduce((n, s) => n + (s.metadata.n_units ?? 0), 0)
  const noDino = chosen.filter(s => !s.dino.ready)
  const create = async () => {
    setBusy(true); setError('')
    try {
      const s = await apiV2.createSession({ name: name.trim() || `Phiên ${new Date().toLocaleString('vi-VN')}`, goal: goal.trim(), target_k: targetK,
                                            pool_source_ids: chosen.map(c => c.id) })
      onCreated()
      location.hash = sessionHref(s.id)
    } catch (e) { if (!(e instanceof StaleResponseError)) setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) }
  }

  return (
    <div className="flex-1 overflow-y-auto p-6 max-w-4xl mx-auto w-full space-y-4">
      <ScreenHeader icon={<Plus className="w-5 h-5" />} title="Phiên mới" subtitle="Chọn video, mục tiêu và số frame cần giữ. Đề xuất đợt đầu do bạn chủ động bấm." />
      <ErrorBanner message={error} onClose={() => setError('')} />
      <Panel className="p-4 space-y-2">
        <div className="text-xs font-semibold">Video ({chosen.length} chọn · {units} frame)</div>
        <div className="max-h-64 overflow-y-auto space-y-1">
          {sources.map(s => <label key={s.id} className="flex items-center gap-2 text-xs cursor-pointer">
            <input type="checkbox" checked={selected.has(s.id)} onChange={() => setSelected(x => { const n = new Set(x); if (n.has(s.id)) n.delete(s.id); else n.add(s.id); return n })} />
            <span className="flex-1 truncate">{s.name}</span>
            <span className="font-mono text-[var(--text-muted)]">{fmtDuration((s.metadata.duration_ms ?? 0) / 1000)} · {s.scope === 'SYSTEM' ? 'hệ thống' : 'của tôi'}
              {' '}· {s.dino.ready ? 'DINO' : 'chưa DINO'}{s.clip.ready ? ' · CLIP' : ''}</span>
          </label>)}
          {!sources.length && <div className="text-xs text-[var(--text-muted)] flex items-center gap-2"><Loader2 className="w-3 h-3 animate-spin" />Đang tải…</div>}
        </div>
        {noDino.length > 0 && <p className="text-[11px] text-amber-700 dark:text-amber-300">{noDino.length} video chưa có DINO: recipe "Đa dạng cảnh" cần chuẩn bị trước
          (Kho dữ liệu); "Ngẫu nhiên"/"Rải đều" vẫn chạy được.</p>}
      </Panel>
      <Panel className="p-4 space-y-3">
        <Field label="Tên phiên"><input className={inputClass} value={name} onChange={e => setName(e.target.value)} placeholder="tùy chọn" /></Field>
        <Field label="Mục tiêu"><textarea className={`${inputClass} w-full`} rows={2} value={goal} onChange={e => setGoal(e.target.value)} /></Field>
        <div className="flex flex-wrap gap-1">{GOALS.map(g => <Button key={g} size="sm" variant="ghost" onClick={() => setGoal(g)}>{g.slice(0, 40)}…</Button>)}</div>
        <Field label="Ngân sách K (số frame cần giữ)"><input type="number" min={1} className={`${inputClass} w-24`} value={targetK} onChange={e => setTargetK(Math.max(1, Number(e.target.value)))} /></Field>
        <Button variant="primary" disabled={busy || !chosen.length || !goal.trim()} onClick={create}>{busy ? 'Đang tạo…' : 'Tạo phiên'}</Button>
      </Panel>
    </div>
  )
}
