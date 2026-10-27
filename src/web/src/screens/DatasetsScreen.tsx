/** Kho dataset tham chiếu (UI_DESIGN §8): import thư mục, version bất biến, verify/prepare và mẫu phân trang. */
import { useCallback, useEffect, useState } from 'react'
import { api, datasetBusy, datasetReady, errorText, type DatasetDTO, type DatasetSampleDTO, type ImportFolderDTO, type LabelStatus } from '../api'
import { Badge, Button, EmptyState, ErrorBanner, Field, Modal, Panel, ProgressBar, ScreenHeader, fmtDateTime, inputClass } from '../ui'

const LABELS: Record<LabelStatus, string> = { none: 'Không nhãn', labeled: 'Có nhãn', empty: 'Nhãn rỗng', missing: 'Thiếu nhãn', invalid: 'Nhãn lỗi', conflict: 'Xung đột' }

export function LibraryTabs({ datasets = false, models = false }: { datasets?: boolean; models?: boolean }) {
  return <nav className="flex gap-2 text-sm" aria-label="Kho dữ liệu">
    <a href="#videos" className={`px-3 py-2 rounded-md ${!datasets && !models ? 'bg-[var(--canvas-subtle)] font-semibold' : ''}`}>Video</a>
    <a href="#datasets" className={`px-3 py-2 rounded-md ${datasets ? 'bg-[var(--canvas-subtle)] font-semibold' : ''}`}>Dataset tham chiếu</a>
    <a href="#models" className={`px-3 py-2 rounded-md ${models ? 'bg-[var(--canvas-subtle)] font-semibold' : ''}`}>Mô hình tham chiếu</a>
  </nav>
}

export function DatasetsScreen() {
  const [datasets, setDatasets] = useState<DatasetDTO[]>([])
  const [versionId, setVersionId] = useState('')
  const [samples, setSamples] = useState<{ total: number; items: DatasetSampleDTO[] }>({ total: 0, items: [] })
  const [offset, setOffset] = useState(0)
  const [filter, setFilter] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [importing, setImporting] = useState<{ dataset?: DatasetDTO }>()
  const [report, setReport] = useState<Record<string, unknown>>()
  const load = useCallback(() => api.datasets().then(setDatasets).catch(e => setError(errorText(e))), [])
  useEffect(() => { load() }, [load])
  const preparing = datasets.some(d => d.versions.some(datasetBusy))
  useEffect(() => {
    if (!preparing) return
    const timer = setInterval(load, 2000)
    return () => clearInterval(timer)
  }, [preparing, load])
  useEffect(() => {
    let live = true
    setSamples({ total: 0, items: [] }); setReport(undefined); setMessage('')
    if (versionId) {
      api.datasetSamples(versionId, offset, filter).then(r => { if (live) setSamples(r) }).catch(e => { if (live) setError(errorText(e)) })
      api.datasetVersion(versionId).then(r => { if (live) setReport(r.report) }).catch(e => { if (live) setError(errorText(e)) })
    }
    return () => { live = false }
  }, [versionId, offset, filter])
  const dataset = datasets.find(d => d.versions.some(v => v.id === versionId))
  const version = dataset?.versions.find(v => v.id === versionId)
  const choose = (id: string) => { setVersionId(id); setOffset(0); setFilter(''); setError('') }
  const act = async (verify: boolean) => {
    if (!version) return
    setBusy(true); setError(''); setMessage('')
    try {
      if (verify) {
        const r = await api.verifyDataset(version.id)
        setMessage(r.ok ? 'Nguồn ảnh và nhãn khớp phiên bản.' : `Thiếu: ${r.missing.join(', ') || 'không'} · Đã đổi: ${r.changed.join(', ') || 'không'}`)
      } else { await api.prepareDataset(version.id); await load() }
    } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  return <div className="flex-1 overflow-y-auto px-8 py-6 space-y-5">
    <LibraryTabs datasets />
    <ScreenHeader title="Dataset tham chiếu" subtitle="Ảnh và nhãn được lưu theo phiên bản. Import từ thư mục trên máy backend; chuẩn bị xong mới chọn làm tham chiếu."
      actions={<Button variant="primary" size="md" onClick={() => setImporting({})}>Import dataset</Button>} />
    <ErrorBanner message={error} onClose={() => setError('')} />
    {!datasets.length && <EmptyState title="Chưa có dataset">Đặt thư mục ảnh trong data/datasets (hoặc gốc RAV_DATASETS_DIR), rồi bấm Import dataset.</EmptyState>}
    <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-3">
      {datasets.map(d => <button key={d.id} onClick={() => choose(d.versions[0].id)} className={`rounded-lg text-left p-4 space-y-2 bg-[var(--canvas-subtle)] cursor-pointer ${dataset?.id === d.id ? 'ring-2 ring-[var(--border-focus)]' : ''}`}>
        <h2 className="font-semibold text-sm">{d.name}</h2>
        <p className="text-xs text-[var(--text-secondary)]">{d.versions.length} phiên bản · {d.versions[0].n_images} ảnh ở v{d.versions[0].n}</p>
        <Badge tone={datasetReady(d.versions[0]) ? 'ok' : d.versions[0].prepare?.state === 'error' ? 'error' : 'warn'}>{datasetReady(d.versions[0]) ? 'Sẵn sàng' : datasetBusy(d.versions[0]) ? 'Đang chuẩn bị' : d.versions[0].prepare?.state === 'error' ? 'Lỗi chuẩn bị' : 'Chưa sẵn sàng'}</Badge>
      </button>)}
    </div>
    {version && dataset && <Panel className="p-5 space-y-4">
      <div className="flex flex-wrap gap-3 items-center">
        <h2 className="font-semibold">{dataset.name}</h2>
        <select className={inputClass} aria-label="Phiên bản dataset" value={versionId} onChange={e => choose(e.target.value)}>
          {dataset.versions.map(v => <option key={v.id} value={v.id}>v{v.n} · {v.n_images} ảnh · {fmtDateTime(v.created_at)}</option>)}
        </select>
        <Button onClick={() => setImporting({ dataset })}>Import phiên bản mới</Button>
        <Button disabled={busy} onClick={() => act(true)}>Kiểm tra nguồn</Button>
        <Button disabled={busy || datasetBusy(version)} onClick={() => act(false)}>{version.prepare?.state === 'error' ? 'Thử lại chuẩn bị' : version.prepared ? 'Kiểm tra & chuẩn bị lại' : 'Chuẩn bị feature'}</Button>
      </div>
      <p className="text-xs text-[var(--text-secondary)]">{version.folder} · {version.annotation_format} · {version.n_images} ảnh · {version.n_sessions_as_reference} phiên tham chiếu</p>
      <p className="text-xs">Lớp: {version.classes?.map(c => `${c} (${version.class_counts[c] ?? 0} box)`).join(', ') || 'không có nhãn lớp'}</p>
      <div className="flex gap-2 flex-wrap">{Object.entries(version.label_counts).map(([s, n]) => <Badge key={s}>{LABELS[s as LabelStatus] ?? s}: {n}</Badge>)}</div>
      {version.prepare && <div className="space-y-2 text-xs">
        <p>{version.prepare.state === 'error' ? 'Chuẩn bị lỗi' : version.prepare.state === 'done' ? 'Đã chuẩn bị' : `Đang chuẩn bị · ${version.prepare.stage}`} · {version.prepare.done}/{version.prepare.total}</p>
        {datasetBusy(version) && <ProgressBar value={version.prepare.total ? version.prepare.done * 100 / version.prepare.total : 0} />}
        <ErrorBanner message={version.prepare.error || ''} />
      </div>}
      {message && <p role="status" className="text-xs">{message}</p>}
      {report && <details className="text-xs"><summary className="cursor-pointer">Báo cáo import</summary><pre className="overflow-auto max-h-52 p-3 whitespace-pre-wrap">{JSON.stringify(report, null, 2)}</pre></details>}
      <div className="flex gap-3 items-center">
        <select className={inputClass} aria-label="Trạng thái nhãn" value={filter} onChange={e => { setFilter(e.target.value); setOffset(0) }}>
          <option value="">Tất cả trạng thái nhãn</option>{Object.entries(LABELS).map(([s, l]) => <option key={s} value={s}>{l}</option>)}
        </select>
        <span className="text-xs">{samples.total} ảnh · trang {Math.floor(offset / 24) + 1}</span>
        <Button disabled={!offset} onClick={() => setOffset(o => o - 24)}>Trang trước</Button>
        <Button disabled={offset + 24 >= samples.total} onClick={() => setOffset(o => o + 24)}>Trang sau</Button>
      </div>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3">
        {samples.items.map(s => <div key={s.key} className="rounded-lg overflow-hidden bg-[var(--card-bg)]">
          <img src={s.thumb_url} alt={s.relpath} loading="lazy" className="w-full aspect-video object-contain" />
          <div className="p-2 text-xs space-y-1"><p className="truncate" title={s.relpath}>{s.relpath}</p><Badge>{LABELS[s.label_status]}</Badge><p>{s.width} × {s.height}</p></div>
        </div>)}
      </div>
    </Panel>}
    {importing && <ImportDialog dataset={importing.dataset} onClose={() => setImporting(undefined)} onImported={async id => { await load(); choose(id); setImporting(undefined) }} />}
  </div>
}

function ImportDialog({ dataset, onClose, onImported }: { dataset?: DatasetDTO; onClose: () => void; onImported: (id: string) => Promise<void> }) {
  const [folders, setFolders] = useState<ImportFolderDTO[]>([])
  const [folder, setFolder] = useState('')
  const [name, setName] = useState('')
  const [format, setFormat] = useState<'auto' | 'none' | 'yolo'>('auto')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<Awaited<ReturnType<typeof api.importDataset>>>()
  useEffect(() => { api.importFolders().then(setFolders).catch(e => setError(errorText(e))) }, [])
  const preview = folders.find(f => f.folder === folder)
  const run = async () => {
    setBusy(true); setError('')
    try { setResult(await api.importDataset({ folder, annotation_format: format, ...(dataset ? { dataset_id: dataset.id } : { name: name.trim() }) })) }
    catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  return <Modal open title={dataset ? `Import phiên bản mới · ${dataset.name}` : 'Import dataset'} onClose={() => { if (!busy) onClose() }} footer={<>
    <span className="text-xs">Nguồn raw được giữ ở thư mục đã chọn.</span>
    {result ? <Button variant="primary" onClick={() => onImported(result.version.id)}>Xem phiên bản</Button> : <Button variant="primary" disabled={busy || !folder || (!dataset && !name.trim())} onClick={run}>{busy ? 'Đang import…' : 'Import'}</Button>}
  </>}>
    {!result && <>
      <Field label="Thư mục (tương đối với gốc dataset)"><input list="dataset-folders" className={inputClass} value={folder} onChange={e => setFolder(e.target.value)} placeholder="Ví dụ: road/images" /></Field>
      <datalist id="dataset-folders">{folders.map(f => <option key={f.folder} value={f.folder}>{f.n_images_hint} ảnh · {f.detected}</option>)}</datalist>
      {!dataset && <Field label="Tên dataset"><input className={inputClass} value={name} onChange={e => setName(e.target.value)} /></Field>}
      <Field label="Định dạng nhãn"><select className={inputClass} value={format} onChange={e => setFormat(e.target.value as typeof format)}><option value="auto">Tự nhận diện</option><option value="none">Không nhãn</option><option value="yolo">YOLO</option></select></Field>
      {preview && <p className="text-xs">Khoảng {preview.n_images_hint} ảnh · nhận diện {preview.detected} · lớp: {preview.classes?.join(', ') || 'không có'}</p>}
    </>}
    <ErrorBanner message={error} />
    {result && <div className="space-y-2 text-xs"><p role="status">{result.created ? 'Đã import phiên bản mới' : 'Nội dung không đổi, dùng lại phiên bản'} v{result.version.n} · {result.version.n_images} ảnh.</p><p>Lớp: {result.version.classes?.join(', ') || 'không có'}</p><p>Bỏ qua: {Number(result.report.n_skipped ?? 0)} · Bản trùng: {Number(result.report.n_duplicates ?? 0)} · Xung đột: {Number(result.report.n_conflicts ?? 0)}</p><div className="flex gap-2 flex-wrap">{Object.entries(result.version.label_counts).map(([s, n]) => <Badge key={s}>{LABELS[s as LabelStatus] ?? s}: {n}</Badge>)}</div><details><summary className="cursor-pointer">Chi tiết báo cáo import</summary><pre className="overflow-auto max-h-64 whitespace-pre-wrap">{JSON.stringify(result.report, null, 2)}</pre></details></div>}
  </Modal>
}
