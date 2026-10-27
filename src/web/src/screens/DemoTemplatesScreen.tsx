/**
 * Phiên mẫu SYSTEM (CONTRACTS §21.10, §21.12, §21.16; UI-export spec §2): danh sách + lịch sử chỉ đọc (đề xuất / quyết định / snapshot của mẫu).
 * "Tạo phiên riêng từ mẫu" gọi fork (Idempotency-Key giữ tới khi có kết quả) → chuyển vào phiên trống của chính người dùng;
 * không tự đề xuất khi mở phiên. Màn mẫu không có Giữ/Bỏ/Đề xuất/Xuất.
 */
import { useEffect, useState } from 'react'
import { AlertCircle, ArrowRight, CheckCircle2, ChevronDown, Film, Layers, Loader2, ShieldCheck, Sparkles } from 'lucide-react'
import { apiV2, StaleResponseError, type TemplateDetailDTO, type TemplateDTO } from '../api_v2'
import { sessionHref } from '../types/navigation'

export function DemoTemplatesScreen({ onForked }: { onForked?: () => void }) {
  const [templates, setTemplates] = useState<TemplateDTO[]>([])
  const [loading, setLoading] = useState(true)
  const [forkingId, setForkingId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [openId, setOpenId] = useState<string | null>(null)
  const [detail, setDetail] = useState<Record<string, TemplateDetailDTO>>({})

  useEffect(() => {
    let live = true
    apiV2.templates().then(items => { if (live) { setTemplates(items); setError(null) } })
      .catch(e => { if (live && !(e instanceof StaleResponseError)) setError(e.message || 'Không tải được danh sách phiên mẫu.') })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [])

  const toggleHistory = async (t: TemplateDTO) => {
    if (openId === t.id) { setOpenId(null); return }
    setOpenId(t.id)
    if (!detail[t.id]) {
      try { const d = await apiV2.template(t.id); setDetail(m => ({ ...m, [t.id]: d })) }
      catch (e) { if (!(e instanceof StaleResponseError)) setError(e instanceof Error ? e.message : 'Không tải được lịch sử mẫu.') }
    }
  }

  const handleFork = async (t: TemplateDTO) => {
    try {
      setForkingId(t.id)
      setError(null)
      const receipt = await apiV2.fork(t.id)
      onForked?.()
      location.hash = sessionHref(receipt.session.id)   // phiên trống của người dùng; không tự đề xuất
    } catch (e) {
      if (!(e instanceof StaleResponseError)) setError(e instanceof Error ? e.message : 'Lỗi khi tạo phiên từ mẫu.')
    } finally {
      setForkingId(null)
    }
  }

  return (
    <div className="flex-1 overflow-y-auto p-6 max-w-6xl mx-auto w-full space-y-6">
      <div>
        <div className="flex items-center gap-2 text-sm text-[var(--accent)] font-medium mb-1">
          <Sparkles className="w-4 h-4" /><span>Phiên mẫu SYSTEM (chỉ đọc)</span>
        </div>
        <h1 className="text-2xl font-bold tracking-tight">Phiên mẫu</h1>
        <p className="text-sm text-[var(--text-muted)] mt-1">
          Xem cách một phiên đã được duyệt (đề xuất, giữ/bỏ, snapshot). Bấm <strong>&ldquo;Tạo phiên riêng từ mẫu&rdquo;</strong> để có phiên trống
          cùng video và cấu hình; bạn tự bắt đầu đề xuất đợt đầu.
        </p>
      </div>

      {error && (
        <div className="p-4 rounded-xl border border-red-500/20 bg-red-500/10 text-red-500 text-sm flex items-start gap-3">
          <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5" /><div>{error}</div>
        </div>
      )}

      {loading ? (
        <div className="py-20 flex flex-col items-center justify-center text-[var(--text-muted)] gap-3">
          <Loader2 className="w-8 h-8 animate-spin text-[var(--accent)]" /><span className="text-sm">Đang tải phiên mẫu…</span>
        </div>
      ) : templates.length === 0 ? (
        <div className="py-16 text-center text-[var(--text-muted)] border border-dashed border-[var(--border-subtle)] rounded-2xl">
          <ShieldCheck className="w-12 h-12 mx-auto mb-3 opacity-40" />
          <div className="text-base font-medium">Chưa có phiên mẫu nào</div>
          <div className="text-xs mt-1">Operator import bằng <code>scripts/pilot_import_demo.py</code>.</div>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {templates.map(t => {
            const blocked = t.availability !== 'VERIFIED'
            return (
              <div key={t.id} className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-6 flex flex-col justify-between shadow-sm">
                <div className="space-y-4">
                  <div>
                    <span className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium mb-2 border ${blocked
                      ? 'bg-amber-500/10 text-amber-600 border-amber-500/20' : 'bg-emerald-500/10 text-emerald-600 border-emerald-500/20'}`}>
                      <ShieldCheck className="w-3 h-3" />{blocked ? `Tạm khóa: ${t.reason_code ?? t.availability}` : 'SYSTEM · chỉ đọc'}
                    </span>
                    <h3 className="text-lg font-semibold">{t.name}</h3>
                  </div>
                  <div className="grid grid-cols-4 gap-2 py-3 px-3.5 rounded-xl bg-[var(--surface-muted)] text-xs">
                    <Stat icon={<Layers className="w-3 h-3" />} label="Đợt đề xuất" value={t.counts.proposals} />
                    <Stat icon={<CheckCircle2 className="w-3 h-3" />} label="Quyết định" value={t.counts.decisions} />
                    <Stat icon={<CheckCircle2 className="w-3 h-3" />} label="Snapshot" value={t.counts.snapshots} />
                    <Stat icon={<Film className="w-3 h-3" />} label="Video · K" value={`${t.source_ids.length} · ${t.target_k}`} />
                  </div>
                  <div className="text-xs text-[var(--text-muted)] bg-[var(--surface-muted)]/50 p-2.5 rounded-lg">
                    <span className="font-medium text-[var(--text-primary)]">Mục tiêu: </span>{t.goal}
                  </div>
                  <button type="button" onClick={() => toggleHistory(t)} className="flex items-center gap-1 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] cursor-pointer">
                    <ChevronDown className={`w-3.5 h-3.5 transition-transform ${openId === t.id ? 'rotate-180' : ''}`} />Lịch sử duyệt của mẫu (chỉ đọc)
                  </button>
                  {openId === t.id && <History detail={detail[t.id]} />}
                </div>
                <div className="pt-6 mt-4 border-t border-[var(--border-subtle)] flex justify-end">
                  <button type="button" disabled={blocked || forkingId === t.id} onClick={() => handleFork(t)}
                    title={blocked ? 'Mẫu đang tạm khóa (nguồn chưa sẵn sàng)' : 'Tạo phiên trống của bạn với cùng video và cấu hình'}
                    className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-emerald-700 hover:bg-emerald-800 text-white text-xs font-semibold disabled:opacity-50 cursor-pointer shadow">
                    {forkingId === t.id ? <><Loader2 className="w-3.5 h-3.5 animate-spin" />Đang tạo phiên…</> : <>Tạo phiên riêng từ mẫu<ArrowRight className="w-3.5 h-3.5" /></>}
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function Stat({ icon, label, value }: { icon: React.ReactNode; label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] text-[var(--text-subtle)] flex items-center gap-1">{icon}{label}</div>
      <div className="font-semibold mt-0.5">{value}</div>
    </div>
  )
}

const KIND: Record<string, string> = { PROPOSAL: 'Đề xuất', DECISION: 'Quyết định', SNAPSHOT: 'Snapshot' }

function History({ detail }: { detail?: TemplateDetailDTO }) {
  if (!detail) return <div className="text-xs text-[var(--text-muted)] flex items-center gap-2"><Loader2 className="w-3 h-3 animate-spin" />Đang tải…</div>
  return (
    <ol className="max-h-64 overflow-y-auto text-[11px] font-mono space-y-0.5 text-[var(--text-secondary)]">
      {detail.history.map(e => {
        const p = e.payload as Record<string, unknown>
        const text = e.kind === 'PROPOSAL' ? `#${p.n} · ${p.recipe ?? ''} · ${(p.picks as unknown[] | undefined)?.length ?? 0} frame`
          : e.kind === 'DECISION' ? `${p.action} ${String(p.unit_id ?? '').split(':').pop()} · ${p.actor ?? ''}${p.reason ? ` · ${p.reason}` : ''}`
          : `#${p.n} · ${p.n_members ?? '?'} frame · ${p.created_by ?? ''}`
        return <li key={e.ord}><span className="text-[var(--text-primary)]">{KIND[e.kind] ?? e.kind}</span> {text}</li>
      })}
      {!detail.history.length && <li>Mẫu chưa có lịch sử.</li>}
    </ol>
  )
}
