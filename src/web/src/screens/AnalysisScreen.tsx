/**
 * Màn "Phân tích" (UI_DESIGN §3, #52, #61), theo thứ tự tài liệu focus: (1) Đánh giá — tốc độ duyệt (s / frame), chất lượng quyết
 * định và uplift (chưa đo trong hệ thống, ghi rõ); (2) Số mô tả tập đã giữ (`get_metrics` mặc định); (3) Kỹ thuật (gập) — view theo
 * `view.type` + tham số từ `describe_component`, bảng metric thô / tự chọn, lịch sử proposal.
 * Pilot (§21.17): dữ liệu từ lần "Tính phân tích" gần nhất (job METRICS, bấm mới tính); ghi rõ khi kết quả đã cũ so với state hiện tại.
 */
import React, { useEffect, useState } from 'react'
import { ClipboardCheck, History, LineChart, Loader2, RefreshCw, Sigma, Wrench } from 'lucide-react'
import { api, errorText, type ComponentDTO, type MetricsDTO, type On, type ProposalDTO, type SessionDTO, type UnitDTO, type ViewSpec } from '../api'
import { SchemaForm } from '../SchemaForm'
import { RENDERERS } from '../renderers'
import { Badge, Button, ErrorBanner, inputClass } from '../ui'
import { apiV2, StaleResponseError, type AnalysisV2 } from '../api_v2'
import { PILOT } from '../mode'
import { requestAnalysis, waitJob } from '../pilot_api'

interface Props {
  sid: string
  version: number
  session: SessionDTO
  units: Map<string, UnitDTO>
  proposals: ProposalDTO[]
  shownN?: number
  scores?: ProposalDTO['scores']
  onShowProposal: (n: number) => void
  onOpenUnit: (unitId: string) => void
}

const box = 'p-5 rounded-xl bg-[var(--canvas-subtle)] space-y-3'
const h2 = 'text-base font-semibold flex items-center gap-2'

export const AnalysisScreen: React.FC<Props> = props => {
  const [bump, setBump] = useState(0)
  const p = { ...props, version: props.version + bump }
  return (
  <div className="flex-1 overflow-y-auto px-8 py-5 space-y-5">
    {PILOT && <PilotAnalysisBar sid={p.sid} version={p.version} onDone={() => setBump(b => b + 1)} />}
    <Summary sid={p.sid} version={p.version} />
    <details className="group space-y-5">
      <summary className={`${h2} cursor-pointer list-none text-[var(--text-secondary)] hover:text-[var(--text-primary)]`}>
        <Wrench className="w-4 h-4" />Kỹ thuật — view, metric thô, lịch sử đề xuất</summary>
      <div className="space-y-5 mt-3">
        <Views {...p} />
        <Metrics sid={p.sid} version={p.version} proposals={p.proposals} />
        <HistoryTable proposals={p.proposals} shownN={p.shownN} onShow={p.onShowProposal} />
      </div>
    </details>
  </div>
  )
}

/** Pilot: trạng thái lần phân tích gần nhất + nút tính lại (job METRICS); không tự tính khi mở tab. */
function PilotAnalysisBar({ sid, version, onDone }: { sid: string; version: number; onDone: () => void }) {
  const [latest, setLatest] = useState<AnalysisV2 | null>()
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  useEffect(() => {
    let live = true
    apiV2.analysis(sid).then(a => { if (live) setLatest(a) }).catch(() => { if (live) setLatest(null) })
    return () => { live = false }
  }, [sid, version])
  const run = async () => {
    setError(''); setBusy('đang xếp hàng')
    try {
      const r = await requestAnalysis(sid)
      if (r.job) {
        setBusy('worker đang tính')
        const j = await waitJob(r.job.id)
        if (j.state !== 'SUCCEEDED') throw new Error(j.error?.message ?? 'Phân tích lỗi')
      }
      onDone()
    } catch (e) { if (!(e instanceof StaleResponseError)) setError(e instanceof Error ? e.message : String(e)) } finally { setBusy('') }
  }
  return (
    <div className="flex items-center gap-3 text-xs">
      <Button variant="primary" disabled={!!busy} onClick={run} icon={busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}>
        {busy ? `Đang tính (${busy})…` : 'Tính phân tích'}</Button>
      <span className="text-[var(--text-secondary)]">{latest === undefined ? 'Đang đọc…' : latest === null ? 'Chưa tính lần nào cho phiên này.'
        : `Tính lúc ${new Date(latest.created_at).toLocaleString('vi-VN')}${latest.current ? ' · khớp trạng thái hiện tại' : ' · đã cũ (có quyết định/đề xuất mới) — bấm tính lại'}`}</span>
      <ErrorBanner message={error} onClose={() => setError('')} />
    </div>
  )
}

const pct = (v?: number) => v === undefined ? '–' : `${(100 * v).toFixed(1)}%`

/** (1) Đánh giá + (2) Số mô tả, từ bộ metric mặc định. Không suy số mô tả thành chất lượng hay uplift (#61). */
function Summary({ sid, version }: { sid: string; version: number }) {
  const [m, setM] = useState<MetricsDTO>()
  const [error, setError] = useState('')
  useEffect(() => { api.metrics(sid).then(setM).catch(e => { if (!(PILOT && e?.code === 'not_found')) setError(errorText(e)) }) }, [sid, version])
  const v = (type: string) => m?.results.find(r => r.type === type)?.values
  const rt = v('review_time'), nd = v('nd_rate_at_k'), md = v('min_dist'), tc = v('temporal_coverage'), kr = v('keep_rate_by_recipe')
  const cell = (label: string, value: React.ReactNode, note?: React.ReactNode) => (
    <div><div className="text-[11px] text-[var(--text-secondary)]">{label}</div><div className="font-mono text-lg font-bold">{value}</div>
      {note && <div className="text-[11px] text-[var(--text-muted)]">{note}</div>}</div>
  )
  return <>
    <ErrorBanner message={error} onClose={() => setError('')} />
    <section className={box}>
      <h2 className={h2}><ClipboardCheck className="w-4 h-4 text-[var(--kept-border)]" />Đánh giá</h2>
      <div className="grid grid-cols-3 gap-4 text-xs">
        {cell('Tốc độ duyệt', rt && rt['review_time/decisions'] > 0 ? `${rt.review_time.toFixed(1)} s / frame` : '–',
          rt && `trung vị theo đợt · ${rt['review_time/batches']} đợt, ${rt['review_time/decisions']} quyết định`)}
        {cell('Chất lượng quyết định', <span className="text-[var(--text-muted)]">chưa đo</span>, 'cần người thẩm định độc lập chấm lại mẫu (focus §4A)')}
        {cell('Uplift mô hình', <span className="text-[var(--text-muted)]">ngoài hệ thống</span>, 'đo AP / mAP sau khi train trên tập xuất, so với Random (focus §4B)')}
      </div>
    </section>
    <section className={box}>
      <h2 className={h2}><Sigma className="w-4 h-4 text-emerald-600" />Số mô tả tập đã giữ
        {m && <span className="text-xs font-normal text-[var(--text-secondary)]">{m.progress.kept} / {m.progress.target_k} frame</span>}</h2>
      <p className="text-[11px] text-[var(--text-muted)]">Mô tả tập đang giữ — không thay AP / mAP, không chứng minh chất lượng.</p>
      <div className="grid grid-cols-4 gap-4 text-xs">
        {cell('Gần trùng', pct(nd?.nd_rate_at_k), 'pHash; thấp = ít trùng')}
        {cell('Khoảng cách gần nhất', md ? md.min_dist.toFixed(3) : '–',
          md?.independent === 0 ? <Badge title="metric đọc chung field với recipe đã chọn (#44)">không độc lập</Badge> : 'trung bình, DINOv2')}
        {cell('Rải thời gian', pct(tc?.temporal_coverage), 'tỉ lệ khoảng thời gian có frame')}
        {cell('Keep rate', pct(kr?.keep_rate_by_recipe),
          kr && Object.entries(kr).filter(([k]) => k.startsWith('recipe/')).map(([k, x]) => `${k.slice(7)} ${(100 * x).toFixed(0)}%`).join(' · '))}
      </div>
    </section>
  </>
}

const VIEW_NAMES = Object.keys(RENDERERS)

function Views({ sid, version, session, units, scores, onOpenUnit }: Props) {
  const [name, setName] = useState(VIEW_NAMES[0])
  const [comp, setComp] = useState<Record<string, ComponentDTO>>({})
  const [params, setParams] = useState<Record<string, Record<string, unknown>>>({})
  const [spec, setSpec] = useState<ViewSpec>()
  const [error, setError] = useState('')
  const prm = params[name] ?? {}

  useEffect(() => {
    if (!comp[name]) api.component('view', name).then(c => setComp(m => ({ ...m, [name]: c }))).catch(e => setError(errorText(e)))
  }, [name])
  const load = () => { setError(''); api.view(sid, name, prm).then(setSpec).catch(e => { setSpec(undefined); if (!(PILOT && ['not_found', 'view_unavailable'].includes(e?.code))) setError(errorText(e)) }) }
  useEffect(load, [sid, name, version])

  const Renderer = spec && RENDERERS[spec.type]
  return (
    <section className={box}>
      <div className="flex items-center justify-between gap-2">
        <h2 className={h2}><LineChart className="w-4 h-4 text-amber-500" />View</h2>
        <div className="flex gap-1 p-0.5 rounded-lg bg-[var(--card-bg)]">
          {VIEW_NAMES.map(n => (
            <button key={n} onClick={() => { if (n !== name) { setSpec(undefined); setName(n) } }}
              className={`px-2.5 py-1 rounded-md text-xs font-mono cursor-pointer ${n === name ? 'bg-[var(--canvas-subtle)] font-semibold' : 'text-[var(--text-secondary)]'}`}>{n}</button>
          ))}
        </div>
      </div>
      {comp[name] && (
        <details className="text-xs">
          <summary className="cursor-pointer text-[var(--text-secondary)]">Tham số view (gửi qua query)</summary>
          <p className="text-[11px] text-[var(--text-muted)] my-1">{comp[name].doc}</p>
          <SchemaForm schema={comp[name].params_schema} value={prm} onChange={v => setParams(m => ({ ...m, [name]: v }))} />
          {name === 'timeline' && <p className="text-[11px] text-[var(--text-muted)] mt-1">video_id trong pool: {session.pool.video_ids?.join(', ') ?? 'mọi video'}</p>}
          <Button className="mt-2" onClick={load}>Tải lại view</Button>
        </details>
      )}
      <ErrorBanner message={error} onClose={() => setError('')} />
      {spec && (Renderer ? <Renderer data={spec.data} units={units} scores={scores} onOpen={onOpenUnit} />
        : <pre className="text-[11px]">{JSON.stringify(spec, null, 1).slice(0, 4000)}</pre>)}
    </section>
  )
}

const DEFAULT_METRICS = JSON.stringify([{ type: 'nd_rate_at_k', params: { threshold: 8 } }, { type: 'keep_rate_by_recipe', params: {} }])

function Metrics({ sid, version, proposals }: { sid: string; version: number; proposals: ProposalDTO[] }) {
  const [m, setM] = useState<MetricsDTO>()
  const [error, setError] = useState('')
  const [custom, setCustom] = useState(DEFAULT_METRICS)
  const [on, setOn] = useState<On>('kept')
  const run = (f: () => Promise<MetricsDTO>) => { setError(''); f().then(setM).catch(e => setError(errorText(e))) }
  useEffect(() => { api.metrics(sid).then(setM).catch(e => { if (!(PILOT && e?.code === 'not_found')) setError(errorText(e)) }) }, [sid, version])

  return (
    <section className={box}>
      <h2 className={h2}><Sigma className="w-4 h-4 text-emerald-600" />Metric thô</h2>
      <ErrorBanner message={error} onClose={() => setError('')} />
      {m && <>
        <p className="text-xs text-[var(--text-secondary)]">Tiến độ: kept {m.progress.kept} / {m.progress.target_k} · dropped {m.progress.dropped} · {m.progress.proposals} proposal ·
          chấm trên <b>{m.on === 'kept' ? 'tập kept' : `picks proposal #${m.on}`}</b></p>
        <table className="w-full text-xs">
          <thead className="text-[var(--text-secondary)]"><tr>{['Metric', 'params', 'Giá trị', 'Độc lập với recipe?'].map(h => <th key={h} className="text-left px-2 py-1">{h}</th>)}</tr></thead>
          <tbody className="divide-y divide-[var(--border-color)] font-mono">
            {m.results.map((r, i) => (
              <tr key={i}>
                <td className="px-2 py-1">{r.type}</td><td className="px-2 py-1"><code>{JSON.stringify(r.params)}</code></td>
                <td className="px-2 py-1">{Object.entries(r.values).filter(([k]) => k !== 'independent').map(([k, v]) => <div key={k}>{k}: <b>{v.toFixed(4)}</b></div>)}</td>
                <td className="px-2 py-1">{r.values.independent === 0
                  ? <span className="text-amber-700" title="metric đọc chung field với recipe đã chọn các frame này (#44)">không độc lập</span>
                  : r.values.independent === 1 ? 'độc lập' : '–'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </>}
      <details className="text-xs">
        <summary className="cursor-pointer text-[var(--text-secondary)]">Chọn metric tự do</summary>
        <textarea rows={4} className={`${inputClass} w-full font-mono mt-1`} value={custom} onChange={e => setCustom(e.target.value)} />
        <p className="text-[11px] text-[var(--text-muted)]">Danh sách {'{type, params}'}; tham số: GET /api/v1/components/metric/&lt;type&gt;.</p>
        <div className="flex items-center gap-2 mt-1">
          <select className={inputClass} value={String(on)} onChange={e => setOn(e.target.value === 'kept' ? 'kept' : Number(e.target.value))}>
            <option value="kept">tập kept</option>
            {proposals.map(p => <option key={p.n} value={p.n}>picks proposal #{p.n}</option>)}
          </select>
          <Button onClick={() => {
            let metrics
            try { metrics = JSON.parse(custom) } catch (e) { setError(`JSON metrics sai: ${e}`); return }
            run(() => api.metricsCustom(sid, { metrics, on }))
          }}>Tính</Button>
          <Button onClick={() => run(() => api.metrics(sid))}>Về mặc định</Button>
        </div>
      </details>
    </section>
  )
}

function HistoryTable({ proposals, shownN, onShow }: { proposals: ProposalDTO[]; shownN?: number; onShow: (n: number) => void }) {
  return (
    <section className={box}>
      <h2 className={h2}><History className="w-4 h-4 text-indigo-500" />Lịch sử proposal ({proposals.length})</h2>
      <div className="overflow-x-auto">
        <table className="w-full text-[11px] font-mono">
          <thead className="text-[var(--text-secondary)]">
            <tr>{['đợt', 'lúc', 'recipe', 'config_hash', 'seed', 'batch', 'kept / dropped lúc đó', 'fields_used', 'picks', ''].map(h => <th key={h} className="text-left px-2 py-1">{h}</th>)}</tr>
          </thead>
          <tbody className="divide-y divide-[var(--border-color)]">
            {[...proposals].reverse().map(p => (
              <tr key={p.n} className={p.n === shownN ? 'bg-[var(--kept-bg)]' : ''}>
                <td className="px-2 py-1">{p.n}</td><td className="px-2 py-1">{new Date(p.created_at).toLocaleTimeString('vi-VN')}</td><td className="px-2 py-1">{p.input.recipe}</td>
                <td className="px-2 py-1"><code title={JSON.stringify(p.input.config, null, 2)}>{p.input.config_hash.slice(0, 10)}</code></td>
                <td className="px-2 py-1">{p.input.seed}</td><td className="px-2 py-1">{p.input.batch_size}</td>
                <td className="px-2 py-1">{p.input.kept_ids.length} / {p.input.dropped_ids.length}</td>
                <td className="px-2 py-1">{p.input.fields_used.join(', ') || '–'}</td><td className="px-2 py-1">{p.picks.length}{p.relaxed && ' (relaxed)'}</td>
                <td className="px-2 py-1"><Button onClick={() => onShow(p.n)}>Xem lưới</Button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
