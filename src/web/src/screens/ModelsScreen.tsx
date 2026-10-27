/** Kho checkpoint tham chiếu: upload .pt, metadata/checksum; chưa nạp hay chạy YOLO (CONTRACTS §16). */
import { useEffect, useState } from 'react'
import { api, errorText, type ModelDTO } from '../api'
import { apiV2, type ModelV2 } from '../api_v2'
import { send } from '../upload_file'
import { Button, EmptyState, ErrorBanner, Field, Panel, ScreenHeader, fmtDateTime, inputClass } from '../ui'
import { LibraryTabs } from './DatasetsScreen'

export function ModelsScreen() {
  const [models, setModels] = useState<ModelDTO[]>([])
  const [file, setFile] = useState<File>()
  const [name, setName] = useState('')
  const [fileKey, setFileKey] = useState(0)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  useEffect(() => { let live = true; api.models().then(r => { if (live) setModels(r) }).catch(e => { if (live) setError(errorText(e)) }); return () => { live = false } }, [])
  const upload = async () => {
    if (!file) return
    setBusy('upload'); setError(''); setMessage('')
    try {
      const r = await api.uploadModel(file, name)
      setModels(list => [r.model, ...list.filter(m => m.id !== r.model.id)])
      setMessage(`${r.created ? 'Đã lưu checkpoint' : 'Nội dung đã có trong kho, dùng lại checkpoint'}: ${r.model.name}. Chọn model trong Studio để chạy thử trên một đợt.`)
      setFile(undefined); setName(''); setFileKey(k => k + 1)
    } catch (e) { setError(errorText(e)) } finally { setBusy('') }
  }
  const verify = async (model: ModelDTO) => {
    setBusy(model.id); setError(''); setMessage('')
    try {
      await api.verifyModel(model.id)
      setMessage(`Checksum ${model.name} khớp file đã upload.`)
    } catch (e) { setError(errorText(e)) }
    finally {
      try { setModels(await api.models()) } catch (e) { setError(errorText(e)) }
      setBusy('')
    }
  }
  return <div className="flex-1 overflow-y-auto px-8 py-6 space-y-5">
    <LibraryTabs models />
    <ScreenHeader title="Mô hình tham chiếu" subtitle="Checkpoint YOLO đã train/finetune trên dataset tham chiếu, cần cải thiện trên dữ liệu mới." />
    <Panel className="p-5 space-y-4 max-w-3xl">
      <h2 className="font-semibold text-sm">Upload checkpoint</h2>
      <Field label="File mô hình (.pt)"><input key={fileKey} aria-label="File mô hình (.pt)" type="file" accept=".pt" disabled={!!busy} className={inputClass} onChange={e => setFile(e.target.files?.[0])} /></Field>
      <Field label="Tên mô hình (tuỳ chọn)"><input aria-label="Tên mô hình" maxLength={200} className={inputClass} disabled={!!busy} value={name} onChange={e => setName(e.target.value)} placeholder="Ví dụ: YOLO đường phố · best.pt" /></Field>
      <p className="text-xs text-[var(--text-secondary)]">Nhận .pt dạng PyTorch ZIP (mặc định tối đa 500 MB). Upload kiểm cấu trúc file và SHA-256. Chạy YOLO trên riêng đợt đề xuất tại Studio để xem box/nhãn/confidence.</p>
      <Button variant="primary" disabled={!file || !!busy} onClick={upload}>{busy === 'upload' ? 'Đang upload…' : 'Upload mô hình'}</Button>
    </Panel>
    <ErrorBanner message={error} onClose={() => setError('')} />
    {message && <p role="status" className="text-sm">{message}</p>}
    <p className="text-sm">Sau khi upload, chọn model và dataset đã dùng để train/finetune trong <a href="#new" className="underline">Phiên mới</a>. Có thể <a href="#datasets" className="underline">import dataset tham chiếu</a> trước hoặc sau.</p>
    {!models.length && <EmptyState title="Chưa có mô hình tham chiếu">Upload checkpoint .pt của bạn để lưu và liên kết với phiên.</EmptyState>}
    <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-3">
      {models.map(m => <Panel key={m.id} className="p-4 space-y-2 min-w-0">
        <h2 className="font-semibold text-sm break-words">{m.name}</h2>
        <p className="text-xs break-words">{m.filename} · {(m.size_bytes / (1 << 20)).toFixed(2)} MB · {fmtDateTime(m.created_at)}</p>
        <p className="text-xs font-mono truncate" title={m.sha256}>SHA-256: {m.sha256}</p>
        <p className={`text-xs ${m.available ? 'text-[var(--text-secondary)]' : 'text-red-700'}`}>{m.available ? 'Đã lưu checkpoint · chạy theo đợt ở Studio' : 'File bị thiếu hoặc đổi kích thước; cần kiểm tra kho model.'}</p>
        <Button disabled={!!busy} onClick={() => verify(m)}>{busy === m.id ? 'Đang kiểm tra…' : 'Kiểm tra checksum'}</Button>
      </Panel>)}
    </div>
  </div>
}

const errMsg = (e: unknown) => e instanceof Error ? e.message : String(e)

/** Pilot (§21.33): mọi người xem danh sách model SYSTEM; chỉ admin upload → job MODEL_VERIFY trên worker. */
export function PilotModelsScreen({ canUpload }: { canUpload: boolean }) {
  const [models, setModels] = useState<ModelV2[]>([])
  const [file, setFile] = useState<File>()
  const [name, setName] = useState('')
  const [dataset, setDataset] = useState('')
  const [fileKey, setFileKey] = useState(0)
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const reload = () => apiV2.models().then(setModels).catch(e => setError(errMsg(e)))
  useEffect(() => { reload() }, [])
  const upload = async () => {
    if (!file) return
    setBusy(true); setError(''); setStatus('Đang tải lên…')
    try {
      const up = await apiV2.createModelUpload(file.name, file.size)
      const form = new FormData()
      for (const [k, v] of Object.entries(up.transfer.fields)) form.append(k, v)
      form.append('file', file)
      await send('POST', up.transfer.url, form, n => setStatus(`Đang tải lên… ${Math.round(100 * n / file.size)}%`), 0)   // một POST cho cả file: không giới hạn thời gian
      let job = await apiV2.completeModelUpload(up.upload_id, { filename: file.name, name: name.trim() || file.name, train_dataset: dataset.trim() })
      while (!['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(job.state)) {
        setStatus('Worker đang kiểm checkpoint (tải, SHA-256, chạy thử một ảnh)…')
        await new Promise(r => setTimeout(r, 2000))
        job = await apiV2.job(job.id)
      }
      if (job.state !== 'SUCCEEDED') throw new Error(job.error?.message || `Kiểm model ${job.state}`)
      setStatus('Đã thêm mô hình; người dùng chọn được trong Studio.')
      setFile(undefined); setName(''); setDataset(''); setFileKey(k => k + 1)
      await reload()
    } catch (e) { setStatus(''); setError(errMsg(e)) } finally { setBusy(false) }
  }
  return <div className="flex-1 overflow-y-auto p-6 max-w-6xl mx-auto w-full space-y-4">
    <p className="text-sm"><a href="#videos" className="underline">← Kho dữ liệu</a></p>
    <ScreenHeader title="Mô hình tham chiếu" subtitle="Checkpoint YOLO detection của hệ thống, chọn trong Studio để chạy YOLO hoặc strategy cần model." />
    {canUpload && <Panel className="p-5 space-y-4 max-w-3xl">
      <h2 className="font-semibold text-sm">Upload checkpoint (admin)</h2>
      <Field label="File mô hình (.pt)"><input key={fileKey} aria-label="File mô hình (.pt)" type="file" accept=".pt" disabled={busy} className={inputClass} onChange={e => setFile(e.target.files?.[0])} /></Field>
      <Field label="Tên hiển thị"><input aria-label="Tên hiển thị" maxLength={200} className={inputClass} disabled={busy} value={name} onChange={e => setName(e.target.value)} placeholder="Ví dụ: YOLO11n COCO" /></Field>
      <Field label="Dataset train gốc"><input aria-label="Dataset train gốc" maxLength={200} className={inputClass} disabled={busy} value={dataset} onChange={e => setDataset(e.target.value)} placeholder="Ví dụ: COCO, BDD100K" /></Field>
      <p className="text-xs text-[var(--text-secondary)]">Tối đa 1 GiB. Worker nạp checkpoint và chạy thử một ảnh; chỉ nhận YOLO detection. Ghi đúng dataset đã train, không suy đoán.</p>
      <Button variant="primary" disabled={!file || !dataset.trim() || busy} onClick={upload}>{busy ? 'Đang xử lý…' : 'Upload mô hình'}</Button>
      {status && <p role="status" className="text-sm">{status}</p>}
    </Panel>}
    <ErrorBanner message={error} onClose={() => setError('')} />
    {!models.length && <EmptyState title="Chưa có mô hình tham chiếu">{canUpload ? 'Upload checkpoint .pt ở trên.' : 'Admin chưa thêm mô hình nào.'}</EmptyState>}
    <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-3">
      {models.map(m => <Panel key={m.id} className="p-4 space-y-2 min-w-0">
        <h2 className="font-semibold text-sm break-words">{m.name}</h2>
        <p className="text-xs">{m.task} · {Object.keys(m.class_map).length} lớp · train: {String(m.provenance.train_dataset ?? '—')}</p>
        <p className="text-xs">{(m.size_bytes / (1 << 20)).toFixed(2)} MB · {fmtDateTime(m.created_at)}</p>
        <p className="text-xs font-mono truncate" title={m.sha256}>SHA-256: {m.sha256}</p>
      </Panel>)}
    </div>
  </div>
}
