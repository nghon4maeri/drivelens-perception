/**
 * Kho dữ liệu pilot thống nhất (UI_DESIGN deploy §1, §3; CONTRACTS §21.12, §21.14, §21.16, §21.35):
 * - Tab "Dữ liệu của tôi" và "Dữ liệu mẫu" (video + model SYSTEM đã xuất bản), kèm bộ lọc Tất cả / Video / Mô hình.
 * - Thẻ video (thumbnail, thời lượng, số frame, trạng thái Ảnh/DINO/CLIP, nút "Tạo phiên từ dữ liệu này" trên video mẫu sẵn sàng)
 *   và thẻ model (tên, loại tác vụ, số lớp, dataset train gốc, tình trạng xác minh) dùng chung bố cục.
 * - Tiến trình upload giữ ở cấp ứng dụng (`upload_manager.ts`): chuyển màn không mất trạng thái; sau reload cho phép
 *   "Chọn lại file để tiếp tục" đối với upload `CREATED` chưa truyền đủ.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Box, Check, Film, FolderOpen, Loader2, Play, Plus, ShieldCheck, Trash2, Upload, X } from 'lucide-react'
import { all, apiV2, StaleResponseError, V2Error, type ModelV2, type SourceV2 } from '../api_v2'
import { send } from '../upload_file'
import { MediaImg } from '../components/MediaImg'
import { mediaRef, rememberSource } from '../pilot_api'
import { newSessionHref } from '../types/navigation'
import { Badge, Button, EmptyState, ErrorBanner, Field, Panel, ProgressBar, ScreenHeader, fmtDateTime, fmtDuration, inputClass } from '../ui'
import { cancelNeedsFileUpload, dismissUpload, onUploadReady, startUpload, useUploads } from '../upload_manager'

type TabKind = 'mine' | 'system'
type ResourceFilter = 'all' | 'video' | 'model'

export function PilotLibraryScreen({
  onChanged,
  hasSessions = false,
  canUploadModel = false,
}: {
  onChanged: () => void
  hasSessions?: boolean
  canUploadModel?: boolean
}) {
  const [sources, setSources] = useState<SourceV2[]>([])
  const [models, setModels] = useState<ModelV2[]>([])
  const [tab, setTab] = useState<TabKind>('mine')
  const [filter, setFilter] = useState<ResourceFilter>('all')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [error, setError] = useState('')
  const [deleting, setDeleting] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(true)

  // Khu vực upload thống nhất (video cho mọi người; model .pt cho admin có quyền models_write)
  const [showModelUpload, setShowModelUpload] = useState(false)
  const [modelFile, setModelFile] = useState<File>()
  const [modelName, setModelName] = useState('')
  const [modelDataset, setModelDataset] = useState('')
  const [modelFileKey, setModelFileKey] = useState(0)
  const [modelStatus, setModelStatus] = useState('')
  const [modelBusy, setModelBusy] = useState(false)

  const uploads = useUploads()
  const fileInput = useRef<HTMLInputElement>(null)
  const resumeInput = useRef<HTMLInputElement>(null)
  const [resumeTargetKey, setResumeTargetKey] = useState<string | undefined>()

  const report = (e: unknown) => {
    if (e instanceof StaleResponseError) return
    if (e instanceof V2Error && ['delete_in_progress', 'delete_reconcile_pending'].includes(e.code)) {
      setError(
        e.code === 'delete_in_progress'
          ? 'Yêu cầu xóa đang được xử lý. Kho dữ liệu sẽ tự cập nhật.'
          : 'Job xóa trước đang đối soát quota và slot. Đợi đối soát xong rồi thử lại.',
      )
      return
    }
    if (e instanceof V2Error && e.code === 'resource_in_use') {
      const deps = (e.details.dependencies as { kind: string }[] | undefined) ?? []
      const count = (k: string) => deps.filter(d => d.kind === k).length
      setError(
        `Không xóa được: nguồn đang được dùng bởi ${count('SESSION')} phiên, ${count('SNAPSHOT')} snapshot, ${count('JOB')} job. Xóa/đợi các nơi dùng trước.`,
      )
      return
    }
    setError(e instanceof Error ? e.message : String(e))
  }

  const load = useCallback(
    () =>
      Promise.all([all(apiV2.sources), apiV2.models().catch(() => [] as ModelV2[])])
        .then(([srcList, modelList]) => {
          srcList.forEach(rememberSource)
          setSources(srcList.filter(s => s.kind === 'VIDEO'))
          setModels(modelList)
        })
        .catch(report)
        .finally(() => setLoading(false)),
    [],
  )

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    return onUploadReady(() => {
      void load()
      onChanged()
    })
  }, [load, onChanged])

  const busy = sources.some(
    s => s.dino.job_id || s.clip.job_id || s.frames?.job_id || !['READY', 'UNAVAILABLE'].includes(s.availability),
  )
  useEffect(() => {
    if (!busy) return
    const t = setInterval(() => void load(), 5000)
    return () => clearInterval(t)
  }, [busy, load])

  const prepare = async (s: SourceV2, feature: 'DINO' | 'CLIP' | 'FRAMES') => {
    try {
      await apiV2.prepare(s.id, feature)
      await load()
    } catch (e) {
      report(e)
    }
  }

  const remove = async (s: SourceV2) => {
    if (!confirm(`Xóa video "${s.name}"? Không hoàn tác được.`)) return
    setDeleting(ids => new Set(ids).add(s.id))
    setError('')
    try {
      let receipt = await apiV2.deleteSource(s.id, s.resource_version)
      setSelected(ids => {
        const next = new Set(ids)
        next.delete(s.id)
        return next
      })
      await load()
      for (let i = 0; i < 300 && !['SUCCEEDED', 'FAILED'].includes(receipt.state); i++) {
        await new Promise(r => setTimeout(r, 2000))
        receipt = await apiV2.deleteReceipt(receipt.id)
      }
      if (receipt.state === 'FAILED') {
        setError(`Xóa chưa xong: ${receipt.error?.message ?? 'job thất bại'}. Bạn có thể thử lại sau khi đối soát.`)
      }
      if (!['SUCCEEDED', 'FAILED'].includes(receipt.state)) {
        setError('Video vẫn đang chờ xóa. Trạng thái sẽ tiếp tục cập nhật.')
      }
      await load()
      onChanged()
    } catch (e) {
      report(e)
      await load()
    } finally {
      setDeleting(ids => {
        const next = new Set(ids)
        next.delete(s.id)
        return next
      })
    }
  }

  const uploadModel = async () => {
    if (!modelFile) return
    setModelBusy(true)
    setError('')
    setModelStatus('Đang tải mô hình lên…')
    try {
      const up = await apiV2.createModelUpload(modelFile.name, modelFile.size)
      const form = new FormData()
      for (const [k, v] of Object.entries(up.transfer.fields)) form.append(k, v)
      form.append('file', modelFile)
      await send('POST', up.transfer.url, form, n => setModelStatus(`Đang tải lên… ${Math.round((100 * n) / modelFile.size)}%`), 0)
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
      setModelStatus('Đã thêm mô hình mẫu; có thể dùng ngay trong Studio.')
      setModelFile(undefined)
      setModelName('')
      setModelDataset('')
      setModelFileKey(k => k + 1)
      await load()
    } catch (e) {
      setModelStatus('')
      report(e)
    } finally {
      setModelBusy(false)
    }
  }

  const mineVideos = sources.filter(s => s.scope === 'PRIVATE')
  const sampleVideos = sources.filter(s => s.scope === 'SYSTEM')
  const mineCount = mineVideos.length
  const sampleTotalCount = sampleVideos.length + models.length

  const shownVideos = (tab === 'mine' ? mineVideos : sampleVideos).filter(() => filter !== 'model')
  const shownModels = tab === 'system' && filter !== 'video' ? models : []

  const toggle = (id: string) =>
    setSelected(s => {
      const n = new Set(s)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })

  const showFirstLoginGuide = !loading && !hasSessions && mineCount === 0 && uploads.length === 0

  return (
    <div className="flex-1 overflow-y-auto p-6 max-w-6xl mx-auto w-full space-y-4">
      <ScreenHeader
        icon={<Film className="w-5 h-5" />}
        title="Kho dữ liệu"
        subtitle="Dữ liệu của bạn (riêng tư) và dữ liệu mẫu dùng chung (video và mô hình tham chiếu đã xuất bản)."
        actions={
          <>
            {selected.size > 0 && (
              <Button
                variant="primary"
                icon={<Plus className="w-4 h-4" />}
                onClick={() => {
                  location.hash = newSessionHref([...selected])
                }}
              >
                Tạo phiên với {selected.size} video
              </Button>
            )}
            {canUploadModel && (
              <Button
                icon={<Box className="w-4 h-4" />}
                onClick={() => {
                  setTab('system')
                  setShowModelUpload(v => !v)
                }}
              >
                {showModelUpload ? 'Ẩn form mô hình' : 'Tải mô hình mẫu'}
              </Button>
            )}
            <Button icon={<Upload className="w-4 h-4" />} onClick={() => fileInput.current?.click()}>
              Tải video lên
            </Button>
            <input
              ref={fileInput}
              type="file"
              accept="video/*"
              multiple
              hidden
              onChange={e => {
                ;[...(e.target.files ?? [])].forEach(f => void startUpload(f))
                e.target.value = ''
              }}
            />
            <input
              ref={resumeInput}
              type="file"
              accept="video/*"
              hidden
              onChange={e => {
                const f = e.target.files?.[0]
                const target = resumeTargetKey
                setResumeTargetKey(undefined)
                e.target.value = ''
                if (f) void startUpload(f, target)
              }}
            />
          </>
        }
      />
      <ErrorBanner message={error} onClose={() => setError('')} />

      {showFirstLoginGuide && (
        <div
          className="rounded-xl border border-[var(--border-subtle)] bg-[var(--card-bg)] p-4 sm:p-5 space-y-3"
          data-testid="first-login-onboarding"
        >
          <div>
            <div className="text-sm font-semibold text-[var(--text-primary)]">Bắt đầu phiên làm việc đầu tiên</div>
            <div className="text-xs text-[var(--text-secondary)] mt-0.5">
              Chọn dùng ngay dữ liệu mẫu đã chuẩn bị sẵn hoặc tải video riêng của bạn lên kho dữ liệu.
            </div>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <button
              type="button"
              onClick={() => setTab('system')}
              className="text-left p-3.5 rounded-xl border border-[var(--border-subtle)] hover:border-[var(--border-focus)] bg-[var(--canvas-bg)] transition-all cursor-pointer flex items-start justify-between gap-3"
            >
              <div className="space-y-1">
                <div className="text-xs font-semibold text-[var(--text-primary)] flex items-center gap-1.5">
                  <ShieldCheck className="w-4 h-4 text-emerald-600" />
                  <span>Dùng dữ liệu mẫu</span>
                </div>
                <div className="text-[11px] text-[var(--text-secondary)]">
                  Trải nghiệm ngay trên dữ liệu mẫu ({sampleVideos.length} video, {models.length} mô hình) hoặc mở{' '}
                  <span
                    className="underline"
                    onClick={e => {
                      e.stopPropagation()
                      location.hash = '#templates'
                    }}
                  >
                    Phiên mẫu
                  </span>{' '}
                  mà không cần chờ trích xuất.
                </div>
              </div>
            </button>
            <button
              type="button"
              onClick={() => {
                setTab('mine')
                fileInput.current?.click()
              }}
              className="text-left p-3.5 rounded-xl border border-[var(--border-subtle)] hover:border-[var(--border-focus)] bg-[var(--canvas-bg)] transition-all cursor-pointer flex items-start justify-between gap-3"
            >
              <div className="space-y-1">
                <div className="text-xs font-semibold text-[var(--text-primary)] flex items-center gap-1.5">
                  <Upload className="w-4 h-4 text-[var(--accent)]" />
                  <span>Upload dữ liệu của bạn</span>
                </div>
                <div className="text-[11px] text-[var(--text-secondary)]">
                  Tải video riêng tư (.mp4) lên S3, trích xuất khung hình 4 fps và chuẩn bị đặc trưng DINO/CLIP.
                </div>
              </div>
            </button>
          </div>
        </div>
      )}

      {/* Khu vực tiến trình upload video & form upload model đồng nhất */}
      {(uploads.length > 0 || (canUploadModel && showModelUpload)) && (
        <Panel className="p-4 space-y-3" data-testid="library-upload-panel">
          {uploads.length > 0 && (
            <div className="space-y-2">
              <div className="text-xs font-semibold text-[var(--text-primary)]">Tiến trình tải video</div>
              {uploads.map(u => (
                <div key={u.key} className="text-xs flex flex-wrap items-center gap-3">
                  <span className="w-56 truncate font-medium" title={u.name}>
                    {u.name}
                  </span>
                  <ProgressBar value={100 * u.progress} className="w-40" />
                  <span
                    className={
                      u.status === 'error'
                        ? 'text-red-600'
                        : u.status === 'needs_file'
                          ? 'text-amber-600 font-medium'
                          : 'text-[var(--text-secondary)]'
                    }
                  >
                    {u.state}
                    {u.error ? `: ${u.error}` : ''}
                  </span>
                  {u.status === 'needs_file' && (
                    <Button
                      size="sm"
                      icon={<FolderOpen className="w-3.5 h-3.5" />}
                      onClick={() => {
                        setResumeTargetKey(u.key)
                        resumeInput.current?.click()
                      }}
                    >
                      Chọn lại file để tiếp tục
                    </Button>
                  )}
                  {u.status !== 'running' && (
                    <button
                      type="button"
                      onClick={() => (u.status === 'needs_file' ? void cancelNeedsFileUpload(u.key) : dismissUpload(u.key))}
                      className="p-1 rounded text-[var(--text-muted)] hover:text-[var(--text-primary)] cursor-pointer"
                      title={u.status === 'needs_file' ? 'Hủy upload dở' : 'Ẩn khỏi danh sách'}
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}

          {canUploadModel && showModelUpload && (
            <div className="space-y-3 pt-2 border-t border-[var(--border-subtle)] first:border-t-0 first:pt-0">
              <div className="text-xs font-semibold text-[var(--text-primary)]">Tải mô hình tham chiếu (.pt)</div>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <Field label="File mô hình (.pt)">
                  <input
                    key={modelFileKey}
                    aria-label="File mô hình (.pt)"
                    type="file"
                    accept=".pt"
                    disabled={modelBusy}
                    className={inputClass}
                    onChange={e => setModelFile(e.target.files?.[0])}
                  />
                </Field>
                <Field label="Tên hiển thị">
                  <input
                    aria-label="Tên hiển thị"
                    maxLength={200}
                    className={inputClass}
                    disabled={modelBusy}
                    value={modelName}
                    onChange={e => setModelName(e.target.value)}
                    placeholder="Ví dụ: YOLO11n COCO"
                  />
                </Field>
                <Field label="Dataset train gốc">
                  <input
                    aria-label="Dataset train gốc"
                    maxLength={200}
                    className={inputClass}
                    disabled={modelBusy}
                    value={modelDataset}
                    onChange={e => setModelDataset(e.target.value)}
                    placeholder="Ví dụ: COCO, BDD100K"
                  />
                </Field>
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <Button variant="primary" size="sm" disabled={!modelFile || !modelDataset.trim() || modelBusy} onClick={uploadModel}>
                  {modelBusy ? 'Đang xử lý…' : 'Upload mô hình'}
                </Button>
                <span className="text-[11px] text-[var(--text-secondary)]">
                  Worker nạp checkpoint và chạy thử một ảnh để xác minh detection trước khi cho phép chọn trong Studio.
                </span>
              </div>
              {modelStatus && (
                <p role="status" className="text-xs text-[var(--text-secondary)]">
                  {modelStatus}
                </p>
              )}
            </div>
          )}
        </Panel>
      )}

      {/* Thanh chuyển tab Dữ liệu của tôi / Dữ liệu mẫu và bộ lọc loại tài nguyên */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex gap-1 p-0.5 rounded-lg bg-[var(--canvas-subtle)] w-fit text-xs" role="tablist">
          {(['mine', 'system'] as const).map(t => (
            <button
              key={t}
              type="button"
              role="tab"
              aria-selected={tab === t}
              onClick={() => {
                setTab(t)
                if (t === 'mine' && filter === 'model') setFilter('all')
              }}
              className={`px-3 py-1 rounded-md cursor-pointer ${
                tab === t ? 'bg-[var(--card-bg)] font-semibold shadow-sm' : 'text-[var(--text-secondary)]'
              }`}
            >
              {t === 'mine' ? `Dữ liệu của tôi (${mineCount})` : `Dữ liệu mẫu (${sampleTotalCount})`}
            </button>
          ))}
        </div>

        {tab === 'system' && (
          <div className="flex gap-1 p-0.5 rounded-lg bg-[var(--canvas-subtle)] w-fit text-xs" data-testid="library-resource-filter">
            {(
              [
                ['all', `Tất cả (${sampleTotalCount})`],
                ['video', `Video (${sampleVideos.length})`],
                ['model', `Mô hình (${models.length})`],
              ] as const
            ).map(([k, label]) => (
              <button
                key={k}
                type="button"
                onClick={() => setFilter(k)}
                className={`px-2.5 py-1 rounded-md cursor-pointer ${
                  filter === k ? 'bg-[var(--card-bg)] font-semibold shadow-sm' : 'text-[var(--text-secondary)]'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        )}
      </div>

      {loading ? (
        <div className="py-10 flex justify-center">
          <Loader2 className="w-6 h-6 animate-spin" />
        </div>
      ) : shownVideos.length === 0 && shownModels.length === 0 ? (
        <EmptyState
          icon={<Film className="w-8 h-8" />}
          title={tab === 'mine' ? 'Chưa có dữ liệu của bạn' : 'Chưa có dữ liệu mẫu phù hợp'}
        >
          {tab === 'mine'
            ? 'Chọn "Dùng dữ liệu mẫu" hoặc bấm "Tải video lên" ở trên để bắt đầu.'
            : 'Operator chưa xuất bản tài nguyên mẫu ở mục này.'}
        </EmptyState>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {shownVideos.map(s => {
            const ready = s.availability === 'READY'
            const isSample = s.scope === 'SYSTEM'
            return (
              <div
                key={s.id}
                data-testid={`source-card-${s.id}`}
                className={`rounded-xl bg-[var(--card-bg)] overflow-hidden border flex flex-col ${
                  selected.has(s.id) ? 'border-[var(--border-focus)]' : 'border-[var(--border-subtle)]'
                }`}
              >
                <button
                  type="button"
                  disabled={!ready}
                  onClick={() => toggle(s.id)}
                  className="relative block w-full aspect-video bg-[#181B1F] cursor-pointer disabled:cursor-default"
                >
                  {ready && (
                    <MediaImg
                      key={`${s.id}:${s.frames?.ready ? 'ready' : (s.frames?.job_id ?? 'none')}`}
                      src={mediaRef('thumbnail', s.id, `${s.core_source_id}:0.000-0.000`)}
                      alt={s.name}
                      className="w-full h-full object-cover"
                    />
                  )}
                  <span className="absolute top-1.5 left-1.5 px-1.5 py-0.5 rounded bg-black/70 text-[10px] font-medium text-white">
                    Video
                  </span>
                  {selected.has(s.id) && (
                    <span className="absolute top-1.5 right-1.5 p-1 rounded-full bg-[var(--border-focus)] text-white">
                      <Check className="w-3 h-3" />
                    </span>
                  )}
                </button>
                <div className="p-3 space-y-2 text-xs flex-1 flex flex-col">
                  <div>
                    <div className="font-medium truncate" title={s.name}>
                      {s.name}
                    </div>
                    {s.description && (
                      <div className="text-[11px] text-[var(--text-secondary)] line-clamp-2 mt-0.5" title={s.description}>
                        {s.description}
                      </div>
                    )}
                  </div>
                  <div className="text-[11px] text-[var(--text-muted)] font-mono">
                    {fmtDuration((s.metadata.duration_ms ?? 0) / 1000)} · {s.metadata.n_units ?? '?'} frame ·{' '}
                    {s.availability === 'DELETE_PENDING'
                      ? 'đang chờ xóa'
                      : s.availability !== 'READY'
                        ? s.availability
                        : isSample
                          ? 'dữ liệu mẫu'
                          : 'riêng tư'}
                  </div>
                  <div className="flex flex-wrap items-center gap-1.5 mt-auto pt-1">
                    {(['DINO', 'CLIP'] as const).map(f => {
                      const st = f === 'DINO' ? s.dino : s.clip
                      if (st.ready) return <Badge key={f} tone="ok" icon={<Check className="w-3 h-3" />}>{f}</Badge>
                      if (st.job_id) return <Badge key={f} tone="warn" icon={<Loader2 className="w-3 h-3 animate-spin" />}>{f} đang chuẩn bị</Badge>
                      if (isSample) return <Badge key={f}>{f} chưa có</Badge>
                      return (
                        <Button key={f} size="sm" disabled={!ready} onClick={() => prepare(s, f)} title={`Chuẩn bị ${f} trên GPU (tính quota GPU)`}>
                          Chuẩn bị {f}
                        </Button>
                      )
                    })}
                    {s.frames?.ready ? (
                      <Badge tone="ok" icon={<Check className="w-3 h-3" />}>
                        Ảnh
                      </Badge>
                    ) : s.frames?.job_id ? (
                      <Badge tone="warn" icon={<Loader2 className="w-3 h-3 animate-spin" />}>
                        Ảnh đang chuẩn bị
                      </Badge>
                    ) : isSample ? (
                      <Badge>Ảnh chưa có</Badge>
                    ) : (
                      <Button
                        size="sm"
                        disabled={!ready}
                        onClick={() => prepare(s, 'FRAMES')}
                        title="Tách và lưu sẵn thumbnail/frame trên worker CPU (không tính quota GPU)"
                      >
                        Chuẩn bị ảnh
                      </Button>
                    )}
                    {isSample ? (
                      <span className="ml-auto text-[var(--text-muted)]" title="Nguồn mẫu dùng chung">
                        <ShieldCheck className="w-3.5 h-3.5" />
                      </span>
                    ) : (
                      <button
                        type="button"
                        disabled={deleting.has(s.id)}
                        onClick={() => remove(s)}
                        title={s.availability === 'DELETE_PENDING' ? 'Thử lại xóa (khi job cũ đã kết thúc)' : 'Xóa video'}
                        className="ml-auto p-1 rounded text-[var(--text-muted)] hover:text-red-500 cursor-pointer disabled:opacity-50"
                      >
                        {deleting.has(s.id) ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                      </button>
                    )}
                  </div>
                  {isSample && ready && (
                    <Button
                      size="sm"
                      variant="primary"
                      icon={<Play className="w-3.5 h-3.5" />}
                      className="w-full justify-center mt-1"
                      onClick={() => {
                        location.hash = newSessionHref([s.id])
                      }}
                    >
                      Tạo phiên từ dữ liệu này
                    </Button>
                  )}
                </div>
              </div>
            )
          })}

          {shownModels.map(m => {
            const verified = m.validation === 'INFERENCE_VERIFIED'
            const classCount = Object.keys(m.class_map ?? {}).length
            const trainDataset = String(m.provenance?.train_dataset ?? '—')
            return (
              <div
                key={m.id}
                data-testid={`model-card-${m.id}`}
                className="rounded-xl bg-[var(--card-bg)] overflow-hidden border border-[var(--border-subtle)] flex flex-col"
              >
                <div className="relative w-full aspect-video bg-[var(--canvas-subtle)] flex flex-col items-center justify-center p-4 text-center gap-1.5">
                  <Box className="w-8 h-8 text-[var(--accent)] opacity-80" />
                  <span className="text-[11px] font-mono text-[var(--text-secondary)]">
                    {m.task.toUpperCase()} · {classCount} lớp
                  </span>
                  <span className="absolute top-1.5 left-1.5 px-1.5 py-0.5 rounded bg-black/70 text-[10px] font-medium text-white">
                    Mô hình
                  </span>
                </div>
                <div className="p-3 space-y-2 text-xs flex-1 flex flex-col">
                  <div>
                    <div className="font-medium truncate" title={m.name}>
                      {m.name}
                    </div>
                    {m.description && (
                      <div className="text-[11px] text-[var(--text-secondary)] line-clamp-2 mt-0.5" title={m.description}>
                        {m.description}
                      </div>
                    )}
                  </div>
                  <div className="text-[11px] text-[var(--text-muted)] font-mono">
                    Train: {trainDataset} · {(m.size_bytes / (1 << 20)).toFixed(1)} MB · {fmtDateTime(m.created_at)}
                  </div>
                  <div className="text-[11px] text-[var(--text-muted)] font-mono truncate" title={m.sha256}>
                    SHA-256: {m.sha256.slice(0, 16)}…
                  </div>
                  <div className="flex flex-wrap items-center gap-1.5 mt-auto pt-1">
                    {verified ? (
                      <Badge tone="ok" icon={<Check className="w-3 h-3" />}>
                        Đã xác minh
                      </Badge>
                    ) : (
                      <Badge tone="warn">Chưa xác minh</Badge>
                    )}
                    <span className="ml-auto text-[var(--text-muted)]" title="Mô hình mẫu dùng chung trong Studio">
                      <ShieldCheck className="w-3.5 h-3.5" />
                    </span>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
