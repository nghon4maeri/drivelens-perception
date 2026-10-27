/**
 * Màn Vận hành & Quản lý dữ liệu mẫu của Admin (CONTRACTS §21.19, §21.36):
 * - 4 khu vực: Tổng quan (`overview`), Dữ liệu mẫu (`catalog`), Tác vụ (`jobs`), Nhật ký (`logs`).
 * - Chống chồng request khi tự làm mới (`refreshingRef`).
 * - Quản lý nguồn & model `SYSTEM`: upload nguồn/model mẫu, chuẩn bị `FRAMES`/`DINO`/`CLIP`, sửa tên/mô tả, xuất bản/ẩn.
 */
import { useEffect, useRef, useState } from 'react'
import { FolderOpen, Upload } from 'lucide-react'
import {
  apiV2,
  V2Error,
  StaleResponseError,
  type AdminModelV2,
  type AdminSourceV2,
  type SourceV2,
  type JobV2,
} from '../api_v2'
import { operationTrace, operations, type Operations, type Trace, type ResourceRow } from '../operations'
import { send } from '../upload_file'
import { onUploadReady, startUpload, useUploads } from '../upload_manager'
import { Badge, Button, ErrorBanner, Field, Panel, inputClass } from '../ui'
import { OperationsLogs } from './OperationsLogs'
import { SampleCopyPicker } from '../components/SampleCopyPicker'

export type OperationsTab = 'overview' | 'catalog' | 'jobs' | 'logs'

const stamp = (value: string) => new Date(value).toLocaleString('vi-VN')
const seconds = (ms?: number | string | null) => ms == null ? '—' : `${(Number(ms) / 1000).toFixed(3)} s`

const REASON_LABELS: Record<string, string> = {
  source_not_ready: 'Nguồn chưa READY',
  frames_not_ready: 'Chưa trích xuất ảnh (FRAMES)',
  model_not_verified: 'Model chưa xác minh inference (INFERENCE_VERIFIED)',
}

export function OperationsScreen() {
  const [tab, setTab] = useState<OperationsTab>('overview')
  const [data, setData] = useState<Operations | null>(null)
  const [trace, setTrace] = useState<Trace | null>(null)
  const [error, setError] = useState('')
  const [traceError, setTraceError] = useState('')
  const [kind, setKind] = useState('upload')
  const [id, setId] = useState('')
  const [busy, setBusy] = useState(false)

  // Catalog state
  const [sources, setSources] = useState<AdminSourceV2[]>([])
  const [models, setModels] = useState<AdminModelV2[]>([])
  const [catalogBusy, setCatalogBusy] = useState(false)
  const [catalogMsg, setCatalogMsg] = useState('')
  const [catalogErr, setCatalogErr] = useState('')
  const [edits, setEdits] = useState<Record<string, { name: string; description: string }>>({})
  const [confirmHideSourceId, setConfirmHideSourceId] = useState<string | null>(null)

  // Model upload state
  const [modelFile, setModelFile] = useState<File>()
  const [modelName, setModelName] = useState('')
  const [modelDataset, setModelDataset] = useState('')
  const [modelFileKey, setModelFileKey] = useState(0)
  const [modelStatus, setModelStatus] = useState('')
  const [sampleMode, setSampleMode] = useState<'upload' | 'library'>('upload')
  const [privateSources, setPrivateSources] = useState<SourceV2[]>([])
  const [privateCursor, setPrivateCursor] = useState<string | null>(null)
  const [copyJob, setCopyJob] = useState<JobV2 | null>(null)
  const seenCopyCompletions = useRef(new Set<string>())

  const uploads = useUploads()
  const systemUploads = uploads.filter(u => u.targetScope === 'SYSTEM')

  const alive = useRef(true)
  const refreshingRef = useRef(false)
  const seq = useRef(0)

  const refresh = async () => {
    if (refreshingRef.current) return
    refreshingRef.current = true
    try {
      const out = await operations()
      if (alive.current) { setData(out); setError('') }
    } catch (e) {
      if (alive.current && !(e instanceof StaleResponseError)) setError(e instanceof Error ? e.message : String(e))
    } finally {
      refreshingRef.current = false
    }
  }

  const loadCatalog = async () => {
    setCatalogBusy(true); setCatalogErr('')
    try {
      const [sPage, mPage] = await Promise.all([apiV2.adminSources(), apiV2.adminModels()])
      if (!alive.current) return
      setSources(sPage.items)
      setModels(mPage.items)
    } catch (e) {
      if (alive.current && !(e instanceof StaleResponseError)) {
        setCatalogErr(e instanceof Error ? e.message : String(e))
      }
    } finally {
      if (alive.current) setCatalogBusy(false)
    }
  }

  const loadPrivateSources = async (cursor?: string) => {
    setCatalogBusy(true); setCatalogErr('')
    try {
      const page = await apiV2.sources(cursor)
      if (alive.current) { setPrivateSources(page.items); setPrivateCursor(page.next_cursor) }
    } catch (e) {
      if (alive.current && !(e instanceof StaleResponseError)) setCatalogErr(e instanceof Error ? e.message : String(e))
    } finally { if (alive.current) setCatalogBusy(false) }
  }

  useEffect(() => {
    const completed = (data?.jobs ?? []).filter(j => j.sample_copy && j.state === 'SUCCEEDED')
    if (completed.some(j => !seenCopyCompletions.current.has(j.id))) {
      completed.forEach(j => seenCopyCompletions.current.add(j.id))
      void loadCatalog()
    }
  }, [data])

  const referenceSource = async (source: SourceV2) => {
    setCatalogBusy(true); setCatalogErr(''); setCatalogMsg('')
    try {
      const result = await apiV2.referenceAsSample(source.id)
      if (!alive.current) return
      if (result.existing) { setCatalogMsg('Video này đã có bản SYSTEM. Kiểm tra trong danh mục mẫu bên dưới.'); await loadCatalog() }
      else { setCatalogMsg('Đã thêm tham chiếu chỉ đọc vào kho mẫu ở trạng thái ẩn. Không sao chép file. Kiểm tra rồi bấm Xuất bản.'); await loadCatalog() }
    } catch (e) {
      if (alive.current && !(e instanceof StaleResponseError)) setCatalogErr(e instanceof V2Error && e.status === 404
        ? 'Máy chủ đang kết nối chưa hỗ trợ thêm mẫu bằng tham chiếu. Cập nhật máy chủ trước khi thử lại.'
        : e instanceof Error ? e.message : String(e))
    } finally { if (alive.current) setCatalogBusy(false) }
  }

  useEffect(() => {
    if (!copyJob || ['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(copyJob.state)) return
    let live = true
    let timer: ReturnType<typeof setTimeout>
    const poll = () => {
      void apiV2.job(copyJob.id).then(job => {
        if (!live || !alive.current) return
        setCopyJob(job)
        if (job.state === 'SUCCEEDED') {
          setCatalogMsg('Đã tạo bản mẫu, đang ẩn. Kiểm tra danh mục rồi bấm Xuất bản.'); void loadCatalog(); void refresh()
        } else if (['FAILED', 'CANCELLED'].includes(job.state)) setCatalogErr(job.error?.message || `Sao chép ${job.state}`)
      }).catch(e => { if (live && !(e instanceof StaleResponseError)) { setCatalogErr(String(e)); timer = setTimeout(poll, 5000) } })
    }
    timer = setTimeout(poll, 2000)
    return () => { live = false; clearTimeout(timer) }
  }, [copyJob])

  useEffect(() => {
    alive.current = true
    void refresh()
    void loadCatalog()
    const offReady = onUploadReady(() => { void loadCatalog(); void refresh() })
    const timer = setInterval(() => {
      if (!document.hidden) {
        void refresh()
      }
    }, 10_000)
    return () => { alive.current = false; offReady(); clearInterval(timer) }
  }, [])

  const inspect = async (k: string, value: string) => {
    const n = ++seq.current
    setKind(k); setId(value); setTrace(null); setTraceError(''); setBusy(true)
    try {
      const out = await operationTrace(k, value)
      if (alive.current && n === seq.current) setTrace(out)
    } catch (e) {
      if (alive.current && n === seq.current && !(e instanceof StaleResponseError)) {
        setTraceError(e instanceof Error ? e.message : String(e))
      }
    } finally {
      if (alive.current && n === seq.current) setBusy(false)
    }
  }

  const editOf = (item: { id: string; name: string; description?: string }) =>
    edits[item.id] ?? { name: item.name, description: item.description ?? '' }

  const setEditField = (item: { id: string; name: string; description?: string }, field: 'name' | 'description', val: string) => {
    const cur = editOf(item)
    setEdits(prev => ({ ...prev, [item.id]: { ...cur, [field]: val } }))
  }

  const saveSourceMeta = async (s: AdminSourceV2) => {
    const cur = editOf(s)
    setCatalogBusy(true); setCatalogErr(''); setCatalogMsg('')
    try {
      const updated = await apiV2.updateAdminSource(s.id, { name: cur.name.trim(), description: cur.description.trim() })
      setSources(list => list.map(x => x.id === s.id ? updated : x))
      setCatalogMsg(`Đã lưu thông tin nguồn "${updated.name}".`)
    } catch (e) {
      setCatalogErr(e instanceof Error ? e.message : String(e))
    } finally {
      setCatalogBusy(false)
    }
  }

  const toggleSourcePublished = async (s: AdminSourceV2, nextPublished: boolean) => {
    setCatalogBusy(true); setCatalogErr(''); setCatalogMsg('')
    try {
      const updated = await apiV2.updateAdminSource(s.id, { published: nextPublished })
      setSources(list => list.map(x => x.id === s.id ? updated : x))
      setConfirmHideSourceId(null)
      setCatalogMsg(nextPublished ? `Đã xuất bản nguồn "${updated.name}" vào Kho mẫu.` : `Đã ẩn nguồn "${updated.name}" khỏi Kho mẫu.`)
    } catch (e) {
      setCatalogErr(e instanceof Error ? e.message : String(e))
    } finally {
      setCatalogBusy(false)
    }
  }

  const triggerPrepare = async (s: AdminSourceV2, feature: 'FRAMES' | 'DINO' | 'CLIP') => {
    setCatalogBusy(true); setCatalogErr(''); setCatalogMsg('')
    try {
      await apiV2.prepare(s.id, feature)
      setCatalogMsg(`Đã xếp hàng chuẩn bị ${feature} cho "${s.name}".`)
      await loadCatalog()
      await refresh()
    } catch (e) {
      setCatalogErr(e instanceof Error ? e.message : String(e))
    } finally {
      setCatalogBusy(false)
    }
  }

  const triggerRetryJob = async (jobId: string) => {
    setCatalogBusy(true); setCatalogErr(''); setCatalogMsg('')
    try {
      await apiV2.retryJob(jobId)
      setCatalogMsg(`Đã xếp hàng thử lại job ${jobId}.`)
      await loadCatalog()
      await refresh()
    } catch (e) {
      setCatalogErr(e instanceof Error ? e.message : String(e))
    } finally {
      setCatalogBusy(false)
    }
  }

  const saveModelMeta = async (m: AdminModelV2) => {
    const cur = editOf(m)
    setCatalogBusy(true); setCatalogErr(''); setCatalogMsg('')
    try {
      const updated = await apiV2.updateAdminModel(m.id, { name: cur.name.trim(), description: cur.description.trim() })
      setModels(list => list.map(x => x.id === m.id ? updated : x))
      setCatalogMsg(`Đã lưu thông tin mô hình "${updated.name}".`)
    } catch (e) {
      setCatalogErr(e instanceof Error ? e.message : String(e))
    } finally {
      setCatalogBusy(false)
    }
  }

  const toggleModelPublished = async (m: AdminModelV2, nextPublished: boolean) => {
    setCatalogBusy(true); setCatalogErr(''); setCatalogMsg('')
    try {
      const updated = await apiV2.updateAdminModel(m.id, { published: nextPublished })
      setModels(list => list.map(x => x.id === m.id ? updated : x))
      setCatalogMsg(nextPublished ? `Đã xuất bản mô hình "${updated.name}" vào Kho mẫu.` : `Đã ẩn mô hình "${updated.name}" khỏi Kho mẫu.`)
    } catch (e) {
      setCatalogErr(e instanceof Error ? e.message : String(e))
    } finally {
      setCatalogBusy(false)
    }
  }

  const uploadSystemModel = async () => {
    if (!modelFile || !modelDataset.trim()) return
    setCatalogBusy(true); setCatalogErr(''); setModelStatus('Đang tải lên checkpoint mẫu…')
    try {
      const up = await apiV2.createModelUpload(modelFile.name, modelFile.size)
      const form = new FormData()
      for (const [k, v] of Object.entries(up.transfer.fields)) form.append(k, v)
      form.append('file', modelFile)
      await send('POST', up.transfer.url, form, n => setModelStatus(`Đang tải lên… ${Math.round(100 * n / modelFile.size)}%`), 0)
      let job = await apiV2.completeModelUpload(up.upload_id, {
        filename: modelFile.name,
        name: modelName.trim() || modelFile.name,
        train_dataset: modelDataset.trim(),
      })
      while (!['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(job.state)) {
        setModelStatus('Worker đang kiểm checkpoint (tải, SHA-256, chạy thử một ảnh)…')
        await new Promise(r => setTimeout(r, 2000))
        job = await apiV2.job(job.id)
      }
      if (job.state !== 'SUCCEEDED') throw new Error(job.error?.message || `Kiểm model ${job.state}`)
      setModelStatus('Đã thêm mô hình SYSTEM (mặc định đang ẩn; bấm Xuất bản khi sẵn sàng).')
      setModelFile(undefined); setModelName(''); setModelDataset(''); setModelFileKey(k => k + 1)
      await loadCatalog()
    } catch (e) {
      setModelStatus('')
      setCatalogErr(e instanceof Error ? e.message : String(e))
    } finally {
      setCatalogBusy(false)
    }
  }

  const rows = (items: ResourceRow[], k: string) => (
    <div className="overflow-x-auto">
      <table className="w-full text-sm text-left">
        <thead>
          <tr>
            <th>ID / tài khoản</th>
            <th>Trạng thái</th>
            <th>Loại / tiến độ</th>
            <th>Cập nhật</th>
          </tr>
        </thead>
        <tbody>
          {items.map(r => (
            <tr key={r.id} className="border-t border-[var(--border-subtle)]">
              <td className="py-2">
                <button className="underline font-mono text-xs" onClick={() => void inspect(k, r.id)}>{r.id}</button>
                <div>{r.display_name}{r.target_scope === 'SYSTEM' && <span className="ml-1 text-xs font-mono text-[var(--kept-border)]">[SYSTEM]</span>}</div>
              </td>
              <td>
                {r.state}
                {r.attempt_n != null && r.attempt_n > 1 && <span className="ml-1 text-xs text-[var(--text-muted)]">(lần {r.attempt_n})</span>}
                {r.error_code && <div className="text-red-500">{r.error_code}</div>}
                {k === 'job' && !r.sample_copy && r.state === 'FAILED' && r.kind?.startsWith('PREPARE_') && (
                  <div className="mt-1">
                    <Button size="sm" disabled={catalogBusy} onClick={() => void triggerRetryJob(r.id)}>Thử lại</Button>
                  </div>
                )}
              </td>
              <td>
                {r.kind ?? `${((r.declared_bytes ?? 0) / 1e6).toFixed(1)} MB`}{r.filename ? ` · ${r.filename}` : ''}
                {r.phase && <div>{r.phase} · {r.done}/{r.total}</div>}
                {(r.queue_wait_ms != null || r.run_ms != null) && (
                  <div className="text-xs text-[var(--text-muted)]">
                    {r.queue_wait_ms != null && <span>Chờ {seconds(r.queue_wait_ms)} </span>}
                    {r.run_ms != null && <span>· Chạy {seconds(r.run_ms)}</span>}
                  </div>
                )}
              </td>
              <td>{stamp(r.updated_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!items.length && <p>Chưa có tài nguyên.</p>}
    </div>
  )

  const counts = data?.job_counts

  return (
    <div className="p-6 space-y-5 max-w-7xl mx-auto w-full overflow-y-auto">
      <div className="flex justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold">Vận hành</h1>
          <p className="text-sm text-[var(--text-muted)]">
            Tự cập nhật mỗi 10 giây · {data ? `Số liệu ${stamp(data.server_time)}` : 'Đang đọc số liệu…'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button onClick={() => { void refresh(); if (tab === 'catalog') void loadCatalog() }}>Làm mới</Button>
        </div>
      </div>

      <nav className="flex gap-2 border-b border-[var(--border-subtle)] pb-2 text-sm" aria-label="Khu vực vận hành">
        {([
          ['overview', 'Tổng quan'],
          ['catalog', 'Dữ liệu mẫu'],
          ['jobs', 'Tác vụ'],
          ['logs', 'Nhật ký'],
        ] as const).map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            className={`px-3 py-1.5 rounded-md cursor-pointer ${tab === key ? 'bg-[var(--canvas-subtle)] font-semibold text-[var(--text-primary)]' : 'text-[var(--text-secondary)] hover:bg-[var(--canvas-subtle)]'}`}
          >
            {label}
          </button>
        ))}
      </nav>

      {error && <ErrorBanner message={`${error} — số liệu đang hiển thị có thể cũ.`} />}

      {tab === 'overview' && data && (
        <div className="space-y-5">
          {counts && (
            <div className="grid grid-cols-2 md:grid-cols-6 gap-3" aria-label="Tổng hợp trạng thái tác vụ">
              <div className="p-3 rounded border border-[var(--border-subtle)] bg-[var(--card-bg)]">
                <div className="text-xs text-[var(--text-muted)]">Đang chờ</div>
                <div className="text-lg font-semibold font-mono">{counts.queued}</div>
              </div>
              <div className="p-3 rounded border border-[var(--border-subtle)] bg-[var(--card-bg)]">
                <div className="text-xs text-[var(--text-muted)]">Chờ tài nguyên</div>
                <div className="text-lg font-semibold font-mono">{counts.waiting_resource}</div>
              </div>
              <div className="p-3 rounded border border-[var(--border-subtle)] bg-[var(--card-bg)]">
                <div className="text-xs text-[var(--text-muted)]">Đang chạy</div>
                <div className="text-lg font-semibold font-mono">{counts.running}</div>
              </div>
              <div className="p-3 rounded border border-[var(--border-subtle)] bg-[var(--card-bg)]">
                <div className="text-xs text-[var(--text-muted)]">Đang hủy</div>
                <div className="text-lg font-semibold font-mono">{counts.cancel_requested}</div>
              </div>
              <div className="p-3 rounded border border-[var(--border-subtle)] bg-[var(--card-bg)]">
                <div className="text-xs text-[var(--text-muted)]">Lỗi 24h</div>
                <div className="text-lg font-semibold font-mono text-red-500">{counts.failed_24h}</div>
              </div>
              <div className="p-3 rounded border border-[var(--border-subtle)] bg-[var(--card-bg)]">
                <div className="text-xs text-[var(--text-muted)]">Hoàn tất 24h</div>
                <div className="text-lg font-semibold font-mono text-[var(--kept-border)]">{counts.succeeded_24h}</div>
              </div>
            </div>
          )}

          <div className="grid md:grid-cols-3 gap-3">
            {['api', 'worker', 'gpu'].map(c => {
              const h = data.heartbeats.find(x => x.component === c)
              return (
                <div key={c} className="p-4 rounded border border-[var(--border-subtle)] bg-[var(--card-bg)]">
                  <b>{c === 'worker' ? 'Worker CPU / MEDIA' : c === 'gpu' ? 'Dispatcher GPU' : 'API / DB'}</b>
                  <div>{h ? h.stale ? 'Heartbeat cũ' : 'Có heartbeat gần đây' : 'Chưa có heartbeat'}</div>
                  {h && <p className="text-xs mt-1">{stamp(h.observed_at)} · mất log {h.details.dropped} · lỗi ghi {h.details.flush_failures}</p>}
                </div>
              )
            })}
          </div>
          <p className="text-sm">
            Thu tracing: {data.trace_enabled ? 'Bật' : 'Tắt'} · lưu {data.retention_days} ngày · giới hạn {data.daily_cap.toLocaleString()} sự kiện/ngày.
            Heartbeat không chứng minh S3 hoặc phiên Kaggle đang sẵn sàng.
          </p>
          <div className="flex gap-3 flex-wrap">
            {data.slots.map(s => (
              <span key={s.slot_key} className="border rounded px-3 py-2">{s.kind} / {s.slot_key}: <b>{s.state}</b></span>
            ))}
          </div>
          {data.gpu_budget[0] ? (
            <p>Ngân sách GPU snapshot: {(data.gpu_budget[0].remaining_ms / 3_600_000).toFixed(2)} giờ · ghi lúc {stamp(data.gpu_budget[0].observed_at)} · nguồn {data.gpu_budget[0].source}. Đây là snapshot, không phải probe trực tiếp.</p>
          ) : (
            <p>Ngân sách GPU: chưa có snapshot.</p>
          )}
          <details>
            <summary className="cursor-pointer font-semibold">Quota CPU/GPU các tài khoản</summary>
            {[...data.quota.map(q => ({ ...q, label: 'cơ bản' })), ...data.bonus.map(q => ({ ...q, label: 'bonus' }))].map((q, i) => (
              <p key={i}>{q.display_name} · {q.resource} {q.label}: còn {seconds(q.available_ms)}, giữ {seconds(q.held_ms)}</p>
            ))}
          </details>
          <section>
            <h2 className="font-semibold">Lỗi API gần nhất</h2>
            {data.errors.map((e, i) => (
              <p key={i}>
                <button className="underline" onClick={() => e.request_id && void inspect('request', e.request_id)}>{e.request_id}</button>
                {' '}· HTTP {e.data.status} {e.data.error_code} · {stamp(e.observed_at)}
              </p>
            ))}
          </section>
        </div>
      )}

      {tab === 'catalog' && (
        <div className="space-y-6">
          {catalogErr && <ErrorBanner message={catalogErr} onClose={() => setCatalogErr('')} />}
          {catalogMsg && <p role="status" className="text-sm text-[var(--kept-border)]">{catalogMsg}</p>}

          <div className="grid md:grid-cols-2 gap-4">
            <Panel className="p-4 space-y-3">
              <h2 className="font-semibold text-sm">Thêm video mẫu (SYSTEM)</h2>
              <div className="flex flex-wrap gap-2" role="group" aria-label="Cách thêm video mẫu">
                <Button variant={sampleMode === 'upload' ? 'primary' : 'secondary'} className="border border-[var(--border-strong)]" icon={<Upload className="w-4 h-4" />} disabled={catalogBusy} onClick={() => setSampleMode('upload')} aria-pressed={sampleMode === 'upload'}>Upload mới</Button>
                <Button variant={sampleMode === 'library' ? 'primary' : 'secondary'} className="border border-[var(--border-strong)]" icon={<FolderOpen className="w-4 h-4" />} disabled={catalogBusy} onClick={() => { setSampleMode('library'); void loadPrivateSources() }} aria-pressed={sampleMode === 'library'}>Chọn từ kho của tôi</Button>
              </div>
              {sampleMode === 'library' ? <SampleCopyPicker sources={privateSources} busy={catalogBusy || !!copyJob && !['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(copyJob.state)}
                nextCursor={privateCursor} onReference={s => void referenceSource(s)} onReload={() => void loadPrivateSources()}
                onNext={() => { if (privateCursor) void loadPrivateSources(privateCursor) }} /> : <>
              <p className="text-xs text-[var(--text-secondary)]">
                Video tải lên ở phạm vi SYSTEM chỉ tính vào dung lượng chung, mặc định ở trạng thái <b>Đang ẩn</b> cho tới khi hoàn tất FRAMES/DINO/CLIP và được admin bấm <b>Xuất bản</b>.
              </p>
              <label className="inline-block">
                <input
                  aria-label="Chọn file video mẫu SYSTEM"
                  type="file"
                  accept="video/mp4,video/quicktime,video/x-matroska,video/webm"
                  className="text-xs"
                  onChange={e => {
                    const files = Array.from(e.target.files ?? [])
                    for (const f of files) void startUpload(f, undefined, 'SYSTEM')
                    e.target.value = ''
                  }}
                />
              </label>
              </>}
              {copyJob && <p role="status" className="text-xs">Sao chép mẫu: {copyJob.state} · {copyJob.progress.done}/{copyJob.progress.total} object · job {copyJob.id}</p>}
              {copyJob && ['QUEUED', 'WAITING_RESOURCE', 'RUNNING'].includes(copyJob.state) && <Button onClick={() => {
                void apiV2.cancelJob(copyJob.id, copyJob.state_version).then(setCopyJob).catch(e => setCatalogErr(String(e)))
              }}>Hủy sao chép</Button>}
              {(data?.jobs ?? []).filter(j => j.sample_copy && j.id !== copyJob?.id).map(j => <p className="text-xs" key={j.id}>
                Sao chép mẫu · {j.state} · {j.done ?? 0}/{j.total ?? 0} object · <button className="underline" onClick={() => void inspect('job', j.id)}>Xem job {j.id}</button>
              </p>)}
              {systemUploads.length > 0 && (
                <div className="space-y-1 text-xs">
                  {systemUploads.map(u => (
                    <div key={u.key} className="flex justify-between gap-2 border-t border-[var(--border-subtle)] pt-1">
                      <span className="truncate font-medium">{u.name}</span>
                      <span>{u.state} ({Math.round(u.progress * 100)}%)</span>
                    </div>
                  ))}
                </div>
              )}
            </Panel>

            <Panel className="p-4 space-y-3">
              <h2 className="font-semibold text-sm">Upload mô hình mẫu (.pt SYSTEM)</h2>
              <Field label="File mô hình (.pt)">
                <input
                  key={modelFileKey}
                  aria-label="File mô hình mẫu (.pt)"
                  type="file"
                  accept=".pt"
                  disabled={catalogBusy}
                  className={inputClass}
                  onChange={e => setModelFile(e.target.files?.[0])}
                />
              </Field>
              <div className="grid grid-cols-2 gap-2">
                <Field label="Tên hiển thị">
                  <input
                    aria-label="Tên hiển thị mô hình mẫu"
                    maxLength={200}
                    className={inputClass}
                    disabled={catalogBusy}
                    value={modelName}
                    onChange={e => setModelName(e.target.value)}
                    placeholder="Ví dụ: YOLO11n COCO"
                  />
                </Field>
                <Field label="Dataset train gốc">
                  <input
                    aria-label="Dataset train gốc mô hình mẫu"
                    maxLength={200}
                    className={inputClass}
                    disabled={catalogBusy}
                    value={modelDataset}
                    onChange={e => setModelDataset(e.target.value)}
                    placeholder="Ví dụ: COCO"
                  />
                </Field>
              </div>
              <Button variant="primary" disabled={!modelFile || !modelDataset.trim() || catalogBusy} onClick={() => void uploadSystemModel()}>
                {catalogBusy ? 'Đang xử lý…' : 'Upload mô hình mẫu'}
              </Button>
              {modelStatus && <p role="status" className="text-xs">{modelStatus}</p>}
            </Panel>
          </div>

          <section className="space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="font-semibold">Danh mục video mẫu SYSTEM ({sources.length})</h2>
              <Button size="sm" disabled={catalogBusy} onClick={() => void loadCatalog()}>Tải lại danh mục</Button>
            </div>
            {!sources.length && <p className="text-sm text-[var(--text-muted)]">Chưa có nguồn SYSTEM nào.</p>}
            <div className="space-y-3">
              {sources.map(s => {
                const ed = editOf(s)
                const dirty = ed.name.trim() !== s.name || ed.description.trim() !== (s.description ?? '')
                return (
                  <Panel key={s.id} className="p-4 space-y-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-semibold text-sm">{s.name}</span>
                        <Badge tone={s.published ? 'ok' : 'warn'}>{s.published ? 'Đã xuất bản' : 'Đang ẩn'}</Badge>
                        <Badge tone={s.availability === 'READY' ? 'ok' : 'warn'}>{s.availability}</Badge>
                        <span className="text-xs font-mono text-[var(--text-muted)]">{s.id}</span>
                      </div>
                      <div className="flex items-center gap-2">
                        {s.published ? (
                          confirmHideSourceId === s.id ? (
                            <div className="flex items-center gap-2 text-xs">
                              <span className="text-amber-600">
                                Ẩn nguồn sẽ tạm khóa {s.dependent_template_count} phiên mẫu đang gắn. Xác nhận?
                              </span>
                              <Button size="sm" disabled={catalogBusy} onClick={() => void toggleSourcePublished(s, false)}>Xác nhận ẩn</Button>
                              <Button size="sm" variant="ghost" onClick={() => setConfirmHideSourceId(null)}>Hủy</Button>
                            </div>
                          ) : (
                            <Button
                              size="sm"
                              disabled={catalogBusy}
                              onClick={() => {
                                if (s.dependent_template_count > 0) setConfirmHideSourceId(s.id)
                                else void toggleSourcePublished(s, false)
                              }}
                            >
                              Ẩn khỏi kho mẫu
                            </Button>
                          )
                        ) : (
                          <Button
                            size="sm"
                            variant="primary"
                            disabled={catalogBusy || !s.publishable}
                            title={s.publishable ? '' : s.unpublishable_reasons.map(r => REASON_LABELS[r] ?? r).join(', ')}
                            onClick={() => void toggleSourcePublished(s, true)}
                          >
                            Xuất bản
                          </Button>
                        )}
                      </div>
                    </div>

                    {!s.publishable && s.unpublishable_reasons.length > 0 && (
                      <p className="text-xs text-amber-600">
                        Chưa đủ điều kiện xuất bản: {s.unpublishable_reasons.map(r => REASON_LABELS[r] ?? r).join(' · ')}
                      </p>
                    )}

                    <div className="grid md:grid-cols-2 gap-2">
                      <Field label="Tên hiển thị">
                        <input
                          aria-label={`Tên nguồn ${s.id}`}
                          className={inputClass}
                          value={ed.name}
                          onChange={e => setEditField(s, 'name', e.target.value)}
                        />
                      </Field>
                      <Field label="Mô tả">
                        <input
                          aria-label={`Mô tả nguồn ${s.id}`}
                          className={inputClass}
                          value={ed.description}
                          onChange={e => setEditField(s, 'description', e.target.value)}
                          placeholder="Mô tả ngắn cho người dùng mẫu"
                        />
                      </Field>
                    </div>
                    {dirty && (
                      <div>
                        <Button size="sm" disabled={catalogBusy || !ed.name.trim()} onClick={() => void saveSourceMeta(s)}>
                          Lưu thông tin
                        </Button>
                      </div>
                    )}

                    <div className="flex flex-wrap items-center gap-3 text-xs">
                      {(['FRAMES', 'DINO', 'CLIP'] as const).map(feat => {
                        const st = feat === 'FRAMES' ? s.frames : feat === 'DINO' ? s.dino : s.clip
                        return (
                          <div key={feat} className="flex items-center gap-1.5 border rounded px-2.5 py-1 border-[var(--border-subtle)]">
                            <b>{feat}:</b>
                            <span>{st.ready ? 'Sẵn sàng' : st.job_id ? 'Đang chạy / chờ' : 'Chưa chuẩn bị'}</span>
                            {!st.ready && !st.job_id && (
                              <Button size="sm" disabled={catalogBusy} onClick={() => void triggerPrepare(s, feat)}>
                                Chuẩn bị {feat}
                              </Button>
                            )}
                            {st.job_id && (
                              <button className="underline font-mono" onClick={() => void inspect('job', st.job_id!)}>
                                {st.job_id.slice(0, 8)}
                              </button>
                            )}
                          </div>
                        )
                      })}
                      {s.dependent_template_count > 0 && (
                        <span className="text-[var(--text-muted)]">
                          Đang gắn với {s.dependent_template_count} phiên mẫu ({s.dependent_templates.map(t => t.name).join(', ')})
                        </span>
                      )}
                    </div>
                  </Panel>
                )
              })}
            </div>
          </section>

          <section className="space-y-3">
            <h2 className="font-semibold">Danh mục mô hình mẫu SYSTEM ({models.length})</h2>
            {!models.length && <p className="text-sm text-[var(--text-muted)]">Chưa có mô hình SYSTEM nào.</p>}
            <div className="space-y-3">
              {models.map(m => {
                const ed = editOf(m)
                const dirty = ed.name.trim() !== m.name || ed.description.trim() !== (m.description ?? '')
                return (
                  <Panel key={m.id} className="p-4 space-y-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-semibold text-sm">{m.name}</span>
                        <Badge tone={m.published ? 'ok' : 'warn'}>{m.published ? 'Đã xuất bản' : 'Đang ẩn'}</Badge>
                        <Badge tone={m.validation === 'INFERENCE_VERIFIED' ? 'ok' : 'warn'}>{m.validation}</Badge>
                        <span className="text-xs font-mono text-[var(--text-muted)]">{m.id}</span>
                      </div>
                      <Button
                        size="sm"
                        variant={m.published ? 'secondary' : 'primary'}
                        disabled={catalogBusy || (!m.published && !m.publishable)}
                        onClick={() => void toggleModelPublished(m, !m.published)}
                      >
                        {m.published ? 'Ẩn khỏi kho mẫu' : 'Xuất bản'}
                      </Button>
                    </div>
                    <div className="grid md:grid-cols-2 gap-2">
                      <Field label="Tên mô hình">
                        <input
                          aria-label={`Tên mô hình ${m.id}`}
                          className={inputClass}
                          value={ed.name}
                          onChange={e => setEditField(m, 'name', e.target.value)}
                        />
                      </Field>
                      <Field label="Mô tả">
                        <input
                          aria-label={`Mô tả mô hình ${m.id}`}
                          className={inputClass}
                          value={ed.description}
                          onChange={e => setEditField(m, 'description', e.target.value)}
                          placeholder="Mô tả mô hình mẫu"
                        />
                      </Field>
                    </div>
                    {dirty && (
                      <div>
                        <Button size="sm" disabled={catalogBusy || !ed.name.trim()} onClick={() => void saveModelMeta(m)}>
                          Lưu thông tin
                        </Button>
                      </div>
                    )}
                  </Panel>
                )
              })}
            </div>
          </section>
        </div>
      )}

      {tab === 'jobs' && data && (
        <div className="space-y-5">
          <section>
            <h2 className="font-semibold mb-2">Uploads gần nhất</h2>
            {rows(data.uploads, 'upload')}
          </section>
          <section>
            <h2 className="font-semibold mb-2">Jobs gần nhất</h2>
            {rows(data.jobs, 'job')}
          </section>
        </div>
      )}

      {tab === 'logs' && (
        <section className="p-4 border rounded border-[var(--border-subtle)]">
          <h2 className="font-semibold">Nhật ký hệ thống</h2>
          <OperationsLogs inspect={(k, value) => { void inspect(k, value) }} />
        </section>
      )}

      <section className="p-4 border rounded border-[var(--border-subtle)] space-y-3">
        <h2 className="font-semibold">Tra upload / job / lỗi request</h2>
        <form className="flex gap-2 flex-wrap" onSubmit={e => { e.preventDefault(); void inspect(kind, id.trim()) }}>
          <select aria-label="Loại mã tra cứu" value={kind} onChange={e => setKind(e.target.value)} className="border rounded p-2 bg-[var(--card-bg)]">
            <option value="upload">Upload</option>
            <option value="job">Job</option>
            <option value="request">Request</option>
          </select>
          <input
            aria-label="UUID tra cứu"
            value={id}
            onChange={e => setId(e.target.value)}
            required
            placeholder="Dán UUID hoặc bấm ID trong bảng"
            className="border rounded p-2 flex-1 min-w-64 bg-[var(--card-bg)]"
          />
          <Button type="submit" disabled={busy}>{busy ? 'Đang đọc…' : 'Xem timeline'}</Button>
        </form>
        {traceError && <ErrorBanner message={traceError} />}
        {trace && (
          <>
            <p>{trace.resource?.kind ?? trace.kind}: <b>{trace.resource?.state ?? 'Request'}</b> · {trace.id}</p>
            <p className="text-sm text-[var(--text-muted)]">{trace.note} {trace.truncated && 'Danh sách đã bị giới hạn 1000 sự kiện.'}</p>
            {!trace.events.length && <p>Chưa có log cho mã này; log cũ trước khi bật tracing không có timeline.</p>}
            {trace.attempts.map(a => (
              <p key={a.id} className="text-sm">
                Attempt {a.id}: {a.state} / usage {a.usage_state} / cleanup {String(a.cleanup_done)} · CPU {seconds(a.cpu_ms)} · GPU {seconds(a.gpu_ms)}
              </p>
            ))}
            <div className="overflow-x-auto">
              <table className="w-full text-sm text-left">
                <thead>
                  <tr>
                    <th>Mốc thời gian</th>
                    <th>Nguồn / bước</th>
                    <th>Thời gian</th>
                    <th>CPU</th>
                    <th>Kết quả</th>
                  </tr>
                </thead>
                <tbody>
                  {trace.events.map((e, i) => (
                    <tr key={i} className="border-t border-[var(--border-subtle)]">
                      <td className="py-2 whitespace-nowrap">{stamp(e.observed_at)}</td>
                      <td>{e.source}{e.source === 'browser' && ' (client báo)'}<div className="font-mono text-xs">{e.stage}</div></td>
                      <td>
                        {seconds(e.data.wall_ms)}
                        {e.stage === 'browser.s3.transfer' && e.data.bytes && e.data.wall_ms ? (
                          <div>{(e.data.bytes / (e.data.wall_ms * 1000)).toFixed(2)} MB/s</div>
                        ) : null}
                        {e.data.queue_ms != null && <div>Chờ {seconds(e.data.queue_ms)}</div>}
                      </td>
                      <td>{seconds(e.data.process_cpu_ms)}</td>
                      <td>{e.data.outcome ?? '—'} {e.data.status} {e.data.error_code} {e.data.error_type}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>
    </div>
  )
}
