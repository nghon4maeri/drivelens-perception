/**
 * Một phiên đang mở (architecture §6, UI_DESIGN §2–3c; #61): thanh tiêu đề (tên phiên + 4 tab theo hash `t=`), Studio ưu tiên ra quyết định
 * (giải thích đợt → thanh đợt có trạng thái lưu → lưới; panel phải gập được), Mẫu đã lưu, Phân tích, Xuất.
 * Dữ liệu và thao tác ở `useSessionData` (lưu ngay, hàng đợi tuần tự); phím tắt Studio ở `useHotkeys`.
 * Pilot (§21.16–21.17): không có thêm video; YOLO và phân tích chạy bằng job; đề xuất là job (trạng thái + Hủy/Thử lại, khóa duyệt khi chạy);
 * thiếu đặc trưng → nút chuẩn bị; clip ngữ cảnh ±3 s qua media request.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertCircle, Box, Check, CheckCircle2, Download, Film, LayoutGrid, LineChart, Loader2, PanelRight, Undo2, X } from 'lucide-react'
import { api, type DatasetDTO, type SessionDTO, type VideoDTO } from './api'
import { useSessionData } from './hooks/useSessionData'
import { useBatchYolo } from './hooks/useBatchYolo'
import { useHotkeys } from './hooks/useHotkeys'
import { ChessboardGrid } from './components/ChessboardGrid'
import { BatchExplain } from './components/BatchExplain'
import { ODDCoverageSummary } from './components/ODDCoverageSummary'
import { ProposalProgress } from './components/ProposalProgress'
import { BatchYoloPanel } from './components/BatchYoloPanel'
import { CurationSidebar } from './components/CurationSidebar'
import { VideoContextLightbox } from './components/VideoContextLightbox'
import { AddVideosModal } from './components/VideoLibrary'
import { ArchiveScreen } from './screens/ArchiveScreen'
import { AnalysisScreen } from './screens/AnalysisScreen'
import { ExportScreen } from './screens/ExportScreen'
import { sessionHref, type SessionTab } from './types/navigation'
import { PILOT } from './mode'
import { contextRef, coreOfSource } from './pilot_api'
import { Button, ErrorBanner, IconButton, type Status } from './ui'

interface Props {
  sid: string
  tab: SessionTab
  videos: VideoDTO[]
  keysDisabled: boolean
  onVideosChanged: () => Promise<unknown>
  onSessionChanged: (s: SessionDTO) => void
}

/** Di chuyển focus trên lưới `cols` cột (lưới có thể ít ô hơn khi pool sắp hết). */
function stepGrid(prev: number, key: string, n: number, cols: number): number {
  if (n <= 0) return 0
  const col = prev % cols
  if (key === 'ArrowRight') return (prev + 1) % n
  if (key === 'ArrowLeft') return (prev - 1 + n) % n
  if (key === 'ArrowDown') return prev + cols < n ? prev + cols : col
  if (key === 'ArrowUp') return prev - cols >= 0 ? prev - cols : col + cols * Math.floor((n - 1 - col) / cols)
  return prev
}

const TABS: { id: SessionTab; label: string; icon: React.ReactNode }[] = [
  { id: 'studio', label: 'Studio duyệt', icon: <LayoutGrid className="w-4 h-4" /> },
  { id: 'archive', label: 'Mẫu đã lưu', icon: <CheckCircle2 className="w-4 h-4" /> },
  { id: 'analysis', label: 'Phân tích', icon: <LineChart className="w-4 h-4" /> },
  { id: 'export', label: PILOT ? 'Snapshot' : 'Xuất', icon: <Download className="w-4 h-4" /> },
]

export function SessionView({ sid, tab, videos, keysDisabled, onVideosChanged, onSessionChanged }: Props) {
  const [datasets, setDatasets] = useState<DatasetDTO[]>([])
  useEffect(() => { let live = true; api.datasets().then(r => { if (live) setDatasets(r) }).catch(() => undefined); return () => { live = false } }, [sid])
  const d = useSessionData(sid, videos, onSessionChanged)
  const [focused, setFocused] = useState(0)
  const [cols, setCols] = useState(4)
  const [showBoxes, setShowBoxes] = useState(false)
  const [panelOpen, setPanelOpen] = useState(true)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [lightbox, setLightbox] = useState<string>()
  const [addOpen, setAddOpen] = useState(false)
  const [carryOver, setCarryOver] = useState({ left: 0, repeated: 0 })   // đợt trước còn bao nhiêu chưa duyệt; đợt mới lặp lại bao nhiêu
  const onColumns = useCallback((n: number) => setCols(n), [])

  const { session, items, shown, proposals } = d
  useEffect(() => {
    if (Object.values(shown?.evidence ?? {}).some(es => es.some(e => e.kind === 'detections'))) setShowBoxes(true)
  }, [shown?.n])
  const yolo = useBatchYolo(sid, shown, session ? session.reference.model_id ?? null : undefined, d.saveSession)
  useEffect(() => { if (yolo.result) setShowBoxes(true) }, [yolo.result])
  const checkpointTab = useRef('')
  useEffect(() => {
    const key = `${sid}/${tab}/${shown?.batch_id ?? shown?.n}/${d.draftCount}`
    if (d.direct && (d.draftCount > 0 || shown?.temporary) && (tab === 'analysis' || tab === 'export') && !d.busy && checkpointTab.current !== key) {
      checkpointTab.current = key
      void d.saveSession()
    }
  }, [sid, tab, shown?.batch_id, shown?.temporary, d.draftCount, d.busy, d.direct])
  const yoloDetections = new Map(yolo.result?.items.map(row => [row.unit_id, row.detections]))
  const withYolo = (id: string) => {
    const evidence = d.evidence.get(id)
    const row = shown?.evidence?.[id]?.find(e => e.kind === 'detections')
    const detections = yoloDetections.get(id) ?? (row?.kind === 'detections' ? row.boxes : undefined)
    return detections === undefined ? evidence : { unit_id: id, tags: evidence?.tags ?? null, telemetry: evidence?.telemetry ?? null, detections }
  }
  const displayItems = items.map(item => ({ ...item, evidence: withYolo(item.unit.id) }))
  const batchKept = items.filter(i => i.status === 'kept').length
  const unreviewed = items.filter(i => i.status === 'undecided').length

  // Enter / "Đợt mới" chỉ yêu cầu đề xuất tiếp (#61): không chốt, không hỏi lại; frame chưa duyệt vẫn ở pool
  const advance = async () => {
    if (!session || d.busy) return
    if (!d.draftCount && d.state.kept_ids.length >= session.target_k) { location.hash = sessionHref(sid, 'export'); return }
    const left = unreviewed, prev = new Set(items.map(it => it.unit.id))
    const next = await d.propose()
    if (next) { setFocused(0); setCarryOver({ left, repeated: next.picks.filter(x => prev.has(x.unit_id)).length }) }
  }
  const setAt = (i: number, to: Status) => { const it = items[i]; if (it) d.decide([{ id: it.unit.id, to }]) }
  const toggleAt = (i: number) => { const it = items[i]; if (it) setAt(i, it.status === 'kept' ? 'dropped' : 'kept') }
  const setAll = (to: Status) => d.decide(items.map(it => ({ id: it.unit.id, to })))

  useHotkeys(e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') { d.undo(); return true }
    if (e.ctrlKey || e.metaKey || e.altKey) return
    const k = e.key.length === 1 ? e.key.toLowerCase() : e.key
    const step = () => setFocused(f => stepGrid(f, 'ArrowRight', items.length, cols))
    if (k.startsWith('Arrow')) setFocused(f => stepGrid(f, k, items.length, cols))
    else if (k === 'k' || k === '1') { setAt(focused, 'kept'); step() }
    else if (k === 'd' || k === '2') { setAt(focused, 'dropped'); step() }
    else if (k === 'u') setAt(focused, 'undecided')
    else if (k === 'x') setAll('dropped')
    else if (k === 'a') setAll('kept')
    else if (k === ' ') { if (items[focused]) setLightbox(items[focused].unit.id) }
    else if (k === 'Enter') advance()
    else if (k === 'm') setShowBoxes(v => !v)
    else if (k === 'p') setPanelOpen(v => !v)
    else return
    return true
  }, tab === 'studio' && !keysDisabled && !lightbox && !addOpen)

  const lbUnit = lightbox ? d.unitById.get(lightbox) : undefined
  const lbNearest = lbUnit && d.explain?.nearest_kept.find(n => n.unit_id === lbUnit.id)

  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      {/* thanh tiêu đề phiên: tên + thêm video + 4 tab + công cụ Studio */}
      <header className="flex items-center gap-4 px-6 py-2.5">
        <div className="min-w-0">
          <div className="text-sm font-semibold truncate" title={session?.goal}>{session ? session.name || session.id.slice(0, 8) : 'Đang tải phiên…'}</div>
          {session && <div className="flex items-center gap-2 text-[11px] text-[var(--text-muted)] font-mono">
            <span>giữ {session.n_kept}/{session.target_k} · {session.n_units} frame</span>
            {PILOT ? <span className="inline-flex items-center gap-1 font-sans"><Film className="w-3 h-3" />{session.pool.video_ids?.length ?? 0} video</span>
            : <button onClick={() => setAddOpen(true)} title="Thêm video đã sẵn sàng vào pool của phiên"
              className="inline-flex items-center gap-1 px-1.5 rounded bg-[var(--canvas-subtle)] hover:bg-[var(--border-color)] text-[var(--text-secondary)] font-sans cursor-pointer">
              <Film className="w-3 h-3" />{session.pool.video_ids?.length ?? 0} video · + Thêm</button>}
          </div>}
        </div>
        {!!session?.reference.dataset_version_ids.length && <div className="flex flex-wrap gap-1 max-w-64 text-[11px]">
          {session.reference.dataset_version_ids.map(id => {
            const dataset = datasets.find(d => d.versions.some(v => v.id === id))
            const version = dataset?.versions.find(v => v.id === id)
            return <a key={id} href="#datasets" title="Dataset train cố định; được dùng trong các recipe bổ sung dữ liệu" className="px-2 py-1 rounded bg-[var(--canvas-subtle)]">Tham chiếu: {dataset ? `${dataset.name} · v${version?.n}` : id.slice(0, 8)}</a>
          })}
        </div>}
        {session?.reference_model && <a href="#models" className="max-w-48 text-[11px] px-2 py-1 rounded bg-[var(--canvas-subtle)]" title="Checkpoint cố định; recipe có model dùng consistency original/flip để chọn ứng viên">
          <span className="block truncate">YOLO: {session.reference_model.name}</span>
          <span className="text-[var(--text-muted)]">{session.reference_model.available ? 'Tham chiếu phiên' : 'File không sẵn sàng'}</span>
        </a>}
        <nav className="flex items-center gap-0.5 p-0.5 rounded-lg bg-[var(--canvas-subtle)]">
          {TABS.map(t => (
            <a key={t.id} href={sessionHref(sid, t.id)} className={`flex items-center gap-1.5 px-3 py-1 rounded-md text-xs ${tab === t.id
              ? 'bg-[var(--card-bg)] font-semibold shadow-sm' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'}`}>
              {t.icon}<span className="hidden xl:inline">{t.label}</span>
            </a>
          ))}
        </nav>
        <div className="flex-1" />
        {tab === 'studio' && <>
          <IconButton label="Khung detector trên ảnh (M)" icon={<Box className="w-4 h-4" />} active={showBoxes} onClick={() => setShowBoxes(v => !v)} />
          <IconButton label="Panel phải (P)" icon={<PanelRight className="w-4 h-4" />} active={panelOpen} onClick={() => setPanelOpen(v => !v)} />
        </>}
      </header>
      {d.error && <div className="px-6 pb-2"><ErrorBanner message={d.error} onClose={() => d.setError('')} /></div>}
      {d.proposalJob && ['queued', 'running'].includes(d.proposalJob.state) && <div role="status" className="px-6 pb-2 text-xs text-[var(--text-secondary)] flex items-center gap-2">
        <Loader2 className="w-3.5 h-3.5 animate-spin" />
        {PILOT ? <>Đề xuất: {d.proposalJob.stage}{d.proposalJob.total > 0 ? ` · ${d.proposalJob.done}/${d.proposalJob.total}` : ''} · tạm khóa giữ/bỏ tới khi xong.
          {d.proposalJob.stage !== 'đang chờ tiến trình dừng an toàn' && <Button size="sm" variant="ghost" onClick={d.cancelProposal}>Hủy</Button>}</>
          : <>Đang chuẩn bị đề xuất: {d.proposalJob.stage} · {d.proposalJob.done}/{d.proposalJob.total} · Bạn vẫn có thể duyệt; đổi quyết định sẽ cần đề xuất lại.</>}
      </div>}
      {PILOT && d.proposalJob?.timings && <ProposalProgress stages={d.proposalJob.timings} active={['queued', 'running'].includes(d.proposalJob.state)} />}
      {PILOT && d.proposalJob?.state === 'error' && <div className="px-6 pb-2 text-xs flex items-center gap-2 text-[var(--text-secondary)]">
        Đề xuất gần nhất không thành công: {d.proposalJob.error}
        <Button size="sm" onClick={d.retryProposal}>Thử lại (theo trạng thái hiện tại)</Button>
      </div>}
      {PILOT && d.missing.length > 0 && <div className="px-6 pb-2 text-xs flex flex-wrap items-center gap-2">
        <span>Cần chuẩn bị đặc trưng:</span>
        {d.missing.map(m => <span key={m.source_id + m.feature} className="px-1.5 py-0.5 rounded bg-[var(--canvas-subtle)] font-mono">
          {d.videoName(coreOfSource(m.source_id) ?? m.source_id)} · {m.feature} ·
          {' '}{{ missing: 'chưa có', queued: 'đang chuẩn bị (GPU)', ready: 'xong', error: 'lỗi' }[m.state]}</span>)}
        {d.missing.some(m => m.state === 'missing' || m.state === 'error') && <Button size="sm" variant="primary" onClick={d.prepareMissing}>Chuẩn bị (tính quota GPU)</Button>}
      </div>}

      {session && tab === 'archive' && <ArchiveScreen keptUnits={d.keptUnits} targetK={session.target_k} history={d.state.history} videoName={d.videoName}
        onOpenUnit={setLightbox} onRemoveKept={id => d.decide([{ id, to: 'dropped' }])} />}
      {session && d.direct && (d.draftCount > 0 || shown?.temporary) && (tab === 'analysis' || tab === 'export') &&
        <div className="p-6">{d.savingCheckpoint ? 'Đang lưu lựa chọn…' : 'Cần lưu lựa chọn trước khi tiếp tục.'} {d.error && <Button disabled={d.busy} onClick={() => { checkpointTab.current = ''; void d.saveSession() }}>Thử lại</Button>}</div>}
      {session && tab === 'analysis' && !(d.direct && (d.draftCount > 0 || shown?.temporary)) && <AnalysisScreen sid={sid} version={d.mapVersion + d.metricsVersion} session={session} units={d.unitById} proposals={proposals}
        shownN={shown?.n} scores={d.scores && d.scores.n === proposals.at(-1)?.n ? d.scores.scores : undefined}
        onShowProposal={n => { d.setShownN(n); location.hash = sessionHref(sid) }} onOpenUnit={setLightbox} />}
      {session && tab === 'export' && !(d.direct && (d.draftCount > 0 || shown?.temporary)) && <ExportScreen key={session.id} session={session} keptUnits={d.keptUnits} history={d.state.history} videoName={d.videoName} pending={!!(d.saving || d.failed || d.draftCount)} />}
      {session && tab === 'studio' && (
        <div className="flex-1 min-h-0 flex overflow-hidden">
          <section className="flex-1 flex flex-col items-center px-6 pb-4 overflow-y-auto gap-3">
            <ODDCoverageSummary trace={shown?.trace ?? shown?.strategy_trace} />
            <div className="w-full max-w-[1100px] flex items-center gap-3 text-xs">
              <span className="font-semibold text-sm">{shown ? `Đợt ${shown.n}` : 'Chưa có đề xuất'}</span>
              <BatchExplain explain={d.explain} videoName={d.videoName} carryOver={carryOver} uncertaintySummary={shown?.uncertainty_summary} />
              {shown?.uncertainty_summary && (
                <span className="text-[11px] px-2 py-0.5 rounded bg-amber-500/15 text-amber-800 dark:text-amber-300 font-mono" title={shown.uncertainty_summary.note}>
                  TTA: {shown.uncertainty_summary.signaled_count}/{shown.uncertainty_summary.total_candidates} frame có tín hiệu
                </span>
              )}
              {!!carryOver.repeated && <span className="text-[11px] text-[var(--text-muted)]" title="Recipe đề xuất lại frame chưa duyệt của đợt trước">lặp lại {carryOver.repeated}</span>}
              {shown && shown.n !== proposals.at(-1)?.n && <Button size="sm" onClick={() => d.setShownN(undefined)}>đợt cũ · về mới nhất</Button>}
              {items.length > 0 && <span className="text-[var(--text-secondary)]">giữ <b className="text-[var(--kept-border)]">{batchKept}</b> · chưa duyệt <b>{unreviewed}</b></span>}
              {d.draftCount || d.direct && shown?.temporary ? <span className="text-amber-600">{d.draftCount} lựa chọn chưa lưu · bấm Đề xuất để lưu</span>
                : <SaveStatus saving={d.saving} failed={d.failed} onRetry={d.retry} />}
              <span className="ml-auto flex items-center gap-1">
                <IconButton label="Hoàn tác (Ctrl+Z)" icon={<Undo2 className="w-4 h-4" />} disabled={!d.canUndo} onClick={d.undo} />
                <Button variant="ghost" icon={<Check className="w-4 h-4 text-emerald-600" />} disabled={!items.length} onClick={() => setAll('kept')} title="Giữ cả đợt (A)">Giữ cả</Button>
                <Button variant="ghost" icon={<X className="w-4 h-4 text-red-500" />} disabled={!items.length} onClick={() => setAll('dropped')} title="Bỏ cả đợt (X)">Bỏ cả</Button>
                <Button variant="primary" size="md" className="ml-2" disabled={d.busy || !d.recipeSel} onClick={advance}
                  title="Gửi lựa chọn và đề xuất đợt tiếp theo (Enter); frame chưa duyệt vẫn ở pool">
                  {d.busy ? 'Đang đề xuất…' : items.length ? 'Đợt mới ↵' : 'Đề xuất ↵'}
                </Button>
              </span>
            </div>
            <BatchYoloPanel yolo={yolo} nImages={shown?.picks.length ?? 0} />
            {!yolo.result && Object.values(shown?.evidence ?? {}).some(es => es.some(e => e.kind === 'detections')) &&
              <p className="w-full max-w-[1100px] text-[11px] text-[var(--text-muted)]">Prediction của đề xuất đã lưu · mở chi tiết để xem nguồn model · M bật/tắt khung.</p>}
            <ChessboardGrid items={displayItems} focusedIndex={focused} showBoxes={showBoxes} onColumns={onColumns}
              emptyText={proposals.length ? 'Không có thêm frame qua bộ lọc và chống trùng hiện tại; kiểm tra pool và cấu hình đề xuất.' : 'Chưa có đề xuất — bấm "Đề xuất" (Enter).'}
              onSelectIndex={setFocused} onToggle={toggleAt} onSet={setAt} onOpenVideo={i => { setFocused(i); setLightbox(items[i]?.unit.id) }} />
          </section>
          <CurationSidebar sid={sid} mapVersion={d.mapVersion} metricsVersion={d.metricsVersion} isOpen={panelOpen} onToggle={() => setPanelOpen(v => !v)}
            advancedOpen={advancedOpen} onAdvancedOpen={setAdvancedOpen} goal={session.goal} kept={d.state.kept_ids.length} targetK={session.target_k}
            items={items} statusOf={d.statusOf} focusedIndex={focused} onSelectIndex={setFocused}
            shown={shown} recipes={d.recipes} recipeSel={d.recipeSel} onRecipeSel={d.setRecipeSel} onOpenUnit={setLightbox} />
        </div>
      )}

      {lbUnit && (
        <VideoContextLightbox key={lbUnit.id} unit={lbUnit} mediaUrl={PILOT ? contextRef(lbUnit) : videos.find(v => v.id === lbUnit.video_id)?.media_url}
          clipWindow={PILOT ? 3 : undefined} videoName={d.videoName(lbUnit.video_id)}
          status={d.statusOf(lbUnit.id)} pick={shown?.picks.find(p => p.unit_id === lbUnit.id)} evidence={withYolo(lbUnit.id)} docs={d.docs}
          proposalEvidence={shown?.evidence?.[lbUnit.id]}
          nearestKept={lbNearest ? { unit: d.unitById.get(lbNearest.kept_unit_id), similarity: lbNearest.similarity, field: d.explain!.similarity_field } : undefined}
          onDecide={to => d.decide([{ id: lbUnit.id, to }])} onClose={() => setLightbox(undefined)} />
      )}
      {session && !PILOT && <AddVideosModal open={addOpen} videos={videos} session={session} onVideosChanged={onVideosChanged} onClose={() => setAddOpen(false)} onAdded={d.poolChanged} />}
    </div>
  )
}

/** Trạng thái lưu gọn ở thanh đợt (thay toast): đang lưu / đã lưu / lưu lỗi + Thử lại. */
function SaveStatus({ saving, failed, onRetry }: { saving: number; failed: number; onRetry: () => void }) {
  if (failed) return (
    <span className="flex items-center gap-1.5 text-red-700 dark:text-red-400">
      <AlertCircle className="w-3.5 h-3.5" />{failed} thao tác lưu lỗi
      <Button size="sm" variant="danger" onClick={onRetry}>Thử lại</Button>
    </span>
  )
  if (saving) return <span className="flex items-center gap-1 text-[var(--text-muted)]"><Loader2 className="w-3.5 h-3.5 animate-spin" />Đang lưu…</span>
  return <span className="flex items-center gap-1 text-[var(--text-muted)]"><Check className="w-3.5 h-3.5" />Đã lưu</span>
}
