/**
 * Video lưu trước; DINO/CLIP chạy khi bấm nút riêng (#76). UI gọi API và theo dõi hai trạng thái.
 * Dùng ở màn Kho video, hộp Tạo phiên, Studio.
 */
import React, { useEffect, useRef, useState } from 'react'
import { Film, Loader2, Plus, Upload } from 'lucide-react'
import { api, errorText, type SessionDTO, type VideoDTO } from '../api'
import { Badge, Button, ErrorBanner, Modal, ProgressBar, fmtDuration } from '../ui'

export const isBusy = (v: VideoDTO) => v.prepare?.state === 'queued' || v.prepare?.state === 'running'
export const clipBusy = (v: VideoDTO) => v.clip_prepare?.state === 'queued' || v.clip_prepare?.state === 'running'

const FeatureStatusLine: React.FC<{ video: VideoDTO; onChanged: () => void; feature: 'DINO' | 'CLIP' }> = ({ video: v, onChanged, feature }) => {
  const [err, setErr] = useState('')
  const [starting, setStarting] = useState(false)
  const ready = feature === 'DINO' ? v.prepared : v.clip_prepared
  const p = feature === 'DINO' ? v.prepare : v.clip_prepare
  const busy = p?.state === 'queued' || p?.state === 'running'
  const pct = p?.total ? Math.round(100 * p.done / p.total) : 0
  const start = async (e: React.MouseEvent) => {
    e.preventDefault(); e.stopPropagation(); setErr(''); setStarting(true)
    try { await (feature === 'DINO' ? api.queuePrepare(v.id) : api.queueClip(v.id)); onChanged() } catch (x) { setErr(errorText(x)) } finally { setStarting(false) }
  }
  return <div className="space-y-1 text-xs" onClick={e => e.stopPropagation()}>
    {ready ? <Badge tone="ok">{feature} sẵn sàng</Badge>
      : busy ? <div className="space-y-1">
        <span className="flex gap-1 items-center"><Loader2 className="w-3 h-3 animate-spin" />{feature}: {p?.state === 'queued' ? 'đang chờ' : p?.stage}</span>
        {!!p?.total && <><span className="font-mono">{p.done}/{p.total} · {pct}%</span><ProgressBar value={pct} tone="warn" /></>}
      </div>
      : <div className="flex flex-wrap gap-1 items-center"><Badge tone={p?.state === 'error' ? 'error' : undefined}>{feature} {p?.state === 'error' ? 'lỗi' : 'chưa chạy'}</Badge>
        <Button size="sm" disabled={starting} onClick={start}>{starting ? 'Đang xếp job…' : p?.state === 'error' ? `Thử lại ${feature}` : `Chạy ${feature} trên Kaggle`}</Button></div>}
    {p?.state === 'error' && <p className="text-red-700 dark:text-red-300">{p.error}</p>}
    {err && <p className="text-red-700 dark:text-red-300">{err}</p>}
  </div>
}
export const ClipStatusLine: React.FC<{ video: VideoDTO; onChanged: () => void }> = props => <FeatureStatusLine {...props} feature="CLIP" />
export const PrepareStatusLine: React.FC<{ video: VideoDTO; onChanged: () => void }> = props => <FeatureStatusLine {...props} feature="DINO" />
export type PrepareKind = 'ready' | 'busy' | 'error' | 'todo'
export const prepareKind = (v: VideoDTO): PrepareKind => v.prepared ? 'ready' : isBusy(v) ? 'busy' : v.prepare?.state === 'error' ? 'error' : 'todo'

/** Tải lần lượt các file lên BE; upload không tự chạy feature. */
export function useUpload(onChanged: () => Promise<unknown>, onUploaded?: (ids: string[]) => void) {
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const upload = async (files: File[]) => {
    const vids = files.filter(f => f.type.startsWith('video/') || /\.(mp4|mov|mkv|webm|avi|m4v)$/i.test(f.name))
    if (!vids.length) { setError('Không có file video nào trong các file đã chọn.'); return }
    setError('')
    const ids: string[] = []
    try {
      for (const [i, file] of vids.entries()) {
        setBusy(`Đang tải lên ${file.name} (${i + 1}/${vids.length})…`)
        ids.push((await api.upload(file)).id)
      }
    } catch (e) { setError(errorText(e)) } finally { setBusy('') }
    await onChanged()
    if (ids.length) onUploaded?.(ids)
  }
  return { upload, busy, error, setError }
}

/** Nút "Thêm video" mở hộp chọn file. */
export const UploadButton: React.FC<{ onFiles: (files: File[]) => void; busy?: boolean; variant?: 'primary' | 'secondary' }> = ({ onFiles, busy, variant = 'primary' }) => {
  const ref = useRef<HTMLInputElement>(null)
  return (
    <>
      <Button variant={variant} size="md" disabled={busy} icon={busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
        onClick={() => ref.current?.click()}>Thêm video</Button>
      <input ref={ref} type="file" accept="video/*" multiple hidden
        onChange={e => { const f = Array.from(e.target.files ?? []); e.target.value = ''; if (f.length) onFiles(f) }} />
    </>
  )
}

/** Thêm video có DINO vào pool; upload chỉ lưu, người dùng bấm chạy feature khi cần. */
export const AddVideosModal: React.FC<{
  open: boolean; videos: VideoDTO[]; session: SessionDTO; onVideosChanged: () => Promise<unknown>; onClose: () => void; onAdded: (s: SessionDTO) => void
}> = ({ open, videos, session, onVideosChanged, onClose, onAdded }) => {
  const [selected, setSelected] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const up = useUpload(onVideosChanged, ids => setSelected(s => [...new Set([...s, ...ids])]))
  useEffect(() => { if (open) { setSelected([]); setError('') } }, [open])

  const inPool = new Set(session.pool.video_ids ?? [])
  const selectable = (v: VideoDTO) => v.prepared && !inPool.has(v.id)
  const ready = selected.filter(id => { const v = videos.find(x => x.id === id); return v && selectable(v) })
  const add = () => {
    setBusy(true); setError('')
    api.addVideos(session.id, ready).then(s => { onAdded(s); onClose() }).catch(e => setError(errorText(e))).finally(() => setBusy(false))
  }

  return (
    <Modal open={open} onClose={onClose} icon={<Film className="w-4 h-4 text-[var(--border-focus)]" />} title={`Thêm video vào phiên ${session.name || session.id.slice(0, 8)}`}
      footer={<>
        <UploadButton variant="secondary" busy={!!up.busy} onFiles={up.upload} />
        <Button variant="primary" size="md" disabled={!ready.length || busy} icon={<Plus className="w-4 h-4" />} onClick={add}>
          Thêm {ready.length || ''} video vào phiên</Button>
      </>}>
      <p className="text-xs text-[var(--text-secondary)]">Video tải lên chỉ lưu vào kho. Bấm chạy DINO khi cần; DINO sẵn sàng thì chọn được vào phiên.</p>
      {up.busy && <p className="text-xs text-[var(--text-secondary)]">{up.busy}</p>}
      <ErrorBanner message={up.error || error} onClose={() => { up.setError(''); setError('') }} />
      <div className="rounded-lg bg-[var(--canvas-subtle)] divide-y divide-[var(--border-color)]">
        {videos.map(v => (
          <label key={v.id} className={`flex items-center gap-3 px-3 py-2 text-xs ${selectable(v) ? 'cursor-pointer' : ''}`}>
            <input type="checkbox" disabled={!selectable(v)} checked={inPool.has(v.id) || selected.includes(v.id)}
              onChange={() => setSelected(s => s.includes(v.id) ? s.filter(x => x !== v.id) : [...s, v.id])} />
            <span className="w-[220px] shrink-0 truncate font-medium" title={v.name}>{v.name}</span>
            <span className="w-20 shrink-0 font-mono text-[11px] text-[var(--text-muted)]">{fmtDuration(v.duration_s)}</span>
            <div className="flex-1 min-w-0">{inPool.has(v.id) ? <Badge>Đã trong phiên</Badge> : <PrepareStatusLine video={v} onChanged={onVideosChanged} />}</div>
          </label>
        ))}
        {!videos.length && <p className="p-3 text-xs text-[var(--text-muted)]">Kho chưa có video — bấm Thêm video.</p>}
      </div>
    </Modal>
  )
}
