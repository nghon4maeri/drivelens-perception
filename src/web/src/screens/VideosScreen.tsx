/**
 * Màn "Kho video" (UI_DESIGN §3, #57): quản lý video kiểu file explorer — lưới thẻ (thumbnail, tên, thời lượng, trạng thái tiền xử lý,
 * số phiên), tìm theo tên, lọc trạng thái, sắp xếp; upload chỉ lưu và nút chạy feature riêng (#76).
 * Bấm thẻ → panel chi tiết: xem video, thông tin, các phiên đang dùng, Tạo phiên từ video này, Chuẩn bị / Thử lại.
 * Chọn nhiều (ô tick trên thẻ, #58) → thanh hành động: Tạo phiên mới (`#new&v=…`), Thêm vào phiên… (`add_videos_to_session`), Bỏ chọn.
 * Xoá / đổi tên: chưa có (cùng đợt quản lý quyền, #55).
 */
import React, { useMemo, useState } from 'react'
import { Check, Film, Loader2, Play, Plus, Search, Upload, X } from 'lucide-react'
import { api, errorText, type SessionDTO, type VideoDTO } from '../api'
import { PrepareStatusLine, ClipStatusLine, UploadButton, prepareKind, useUpload, type PrepareKind } from '../components/VideoLibrary'
import { newSessionHref, sessionHref } from '../types/navigation'
import { Button, EmptyState, ErrorBanner, IconButton, Label, Panel, ScreenHeader, fmtDateTime, fmtDuration, inputClass } from '../ui'

import { LibraryTabs } from './DatasetsScreen'

interface Props {
  videos: VideoDTO[]
  sessions: SessionDTO[]
  onVideosChanged: () => Promise<unknown>
  onSessionChanged: (s: SessionDTO) => void
}

const FILTERS: { id: PrepareKind | 'all'; label: string }[] = [
  { id: 'all', label: 'Tất cả' }, { id: 'ready', label: 'Có DINO' }, { id: 'busy', label: 'DINO đang chạy' }, { id: 'todo', label: 'DINO chưa chạy' }, { id: 'error', label: 'DINO lỗi' },
]
const SORTS = { name: 'Tên', duration: 'Thời lượng', sessions: 'Số phiên dùng' } as const

export const VideosScreen: React.FC<Props> = ({ videos, sessions, onVideosChanged, onSessionChanged }) => {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<PrepareKind | 'all'>('all')
  const [sort, setSort] = useState<keyof typeof SORTS>('name')
  const [selectedId, setSelectedId] = useState<string>()
  const [dragging, setDragging] = useState(false)
  const [checked, setChecked] = useState<string[]>([])
  const toggleCheck = (id: string) => setChecked(c => c.includes(id) ? c.filter(x => x !== id) : [...c, id])
  const up = useUpload(onVideosChanged, ids => setSelectedId(ids.at(-1)))

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: videos.length }
    for (const v of videos) c[prepareKind(v)] = (c[prepareKind(v)] ?? 0) + 1
    return c
  }, [videos])
  const q = query.trim().toLowerCase()
  const shown = videos
    .filter(v => (filter === 'all' || prepareKind(v) === filter) && (!q || v.name.toLowerCase().includes(q)))
    .sort((a, b) => sort === 'name' ? a.name.localeCompare(b.name) : sort === 'duration' ? b.duration_s - a.duration_s : b.n_sessions - a.n_sessions)
  const selected = videos.find(v => v.id === selectedId)

  return (
    <div className="flex-1 flex overflow-hidden relative"
      onDragOver={e => { e.preventDefault(); setDragging(true) }} onDragLeave={e => { if (e.currentTarget === e.target) setDragging(false) }}
      onDrop={e => { e.preventDefault(); setDragging(false); up.upload(Array.from(e.dataTransfer.files)) }}>
      <div className="flex-1 overflow-y-auto px-8 py-6 space-y-5">
        <LibraryTabs />
        <ScreenHeader icon={<Film className="w-5 h-5 text-[var(--kept-border)]" />} title="Kho video"
          subtitle="Tải lên chỉ lưu video. Bấm chạy DINO hoặc CLIP trên Kaggle khi cần; DINO sẵn sàng thì dùng được trong phiên."
          actions={<UploadButton busy={!!up.busy} onFiles={up.upload} />} />
        {up.busy && <p className="text-xs text-[var(--text-secondary)] flex items-center gap-2"><Loader2 className="w-3.5 h-3.5 animate-spin" />{up.busy}</p>}
        <ErrorBanner message={up.error} onClose={() => up.setError('')} />

        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-md bg-[var(--canvas-subtle)] min-w-[240px]">
            <Search className="w-4 h-4 text-[var(--text-muted)]" />
            <input value={query} onChange={e => setQuery(e.target.value)} placeholder="Tìm theo tên video" className="flex-1 bg-transparent text-sm focus:outline-hidden" />
            {query && <IconButton label="Xoá tìm kiếm" icon={<X className="w-3.5 h-3.5" />} onClick={() => setQuery('')} className="!p-0.5" />}
          </div>
          <div className="flex items-center gap-1">
            {FILTERS.map(f => (
              <button key={f.id} onClick={() => setFilter(f.id)} className={`px-2.5 py-1 rounded-md text-xs cursor-pointer ${filter === f.id
                ? 'bg-[var(--card-bg)] shadow-sm font-semibold text-[var(--text-primary)]' : 'text-[var(--text-secondary)] hover:bg-[var(--canvas-subtle)]'}`}>
                {f.label} <span className="font-mono text-[var(--text-muted)]">{counts[f.id] ?? 0}</span>
              </button>
            ))}
          </div>
          <select value={sort} onChange={e => setSort(e.target.value as keyof typeof SORTS)} className={`${inputClass} ml-auto`} aria-label="Sắp xếp">
            {Object.entries(SORTS).map(([k, l]) => <option key={k} value={k}>Sắp xếp: {l}</option>)}
          </select>
        </div>

        {!videos.length
          ? <EmptyState icon={<Upload className="w-8 h-8" />} title="Kho chưa có video">Bấm "Thêm video" hoặc kéo thả file video vào đây.</EmptyState>
          : !shown.length
            ? <EmptyState title="Không có video khớp bộ lọc" />
            : (
              <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-4">
                {shown.map(v => (
                  <div key={v.id} role="button" tabIndex={0} onClick={() => setSelectedId(v.id)} onKeyDown={e => { if (e.key === 'Enter') setSelectedId(v.id) }} title={v.name}
                    className={`text-left rounded-xl overflow-hidden bg-[var(--canvas-subtle)] hover:shadow-md transition-shadow cursor-pointer ${checked.includes(v.id) ? 'ring-2 ring-[var(--kept-border)]' : v.id === selectedId ? 'ring-2 ring-[var(--border-focus)]' : ''}`}>
                    <div className="group relative aspect-video bg-[#181B1F]">
                      <img src={v.thumb_url} alt="" loading="lazy" className="w-full h-full object-cover" />
                      <button type="button" role="checkbox" aria-checked={checked.includes(v.id)} aria-label={`Chọn ${v.name}`}
                        onClick={e => { e.stopPropagation(); toggleCheck(v.id) }}
                        className={`absolute top-1.5 left-1.5 w-6 h-6 rounded-md flex items-center justify-center cursor-pointer transition-opacity ${checked.includes(v.id)
                          ? 'bg-[var(--kept-border)] text-white' : `bg-black/55 text-white/70 hover:text-white ${checked.length ? '' : 'opacity-0 group-hover:opacity-100'}`}`}>
                        <Check className="w-4 h-4" /></button>
                      <span className="absolute bottom-1.5 right-1.5 px-1.5 py-0.5 rounded bg-black/70 text-[11px] font-mono text-white">{fmtDuration(v.duration_s)}</span>
                    </div>
                    <div className="p-3 space-y-2">
                      <div className="text-sm font-medium truncate">{v.name}</div>
                      <div className="space-y-2">
                        <PrepareStatusLine video={v} onChanged={onVideosChanged} />
                        <ClipStatusLine video={v} onChanged={onVideosChanged} />
                        {v.n_sessions > 0 && <span className="text-[11px] text-[var(--text-muted)] shrink-0">{v.n_sessions} phiên</span>}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
      </div>

      {selected && <VideoDetail video={selected} sessions={sessions.filter(s => s.pool.video_ids?.includes(selected.id))}
        onClose={() => setSelectedId(undefined)} onChanged={onVideosChanged} onNewSession={() => { location.hash = newSessionHref([selected.id]) }} />}

      {checked.length > 0 && <SelectionBar videos={videos.filter(v => checked.includes(v.id))} sessions={sessions}
        onClear={() => setChecked([])} onSessionChanged={s => { onSessionChanged(s); onVideosChanged() }} />}

      {dragging && (
        <div className="absolute inset-3 rounded-2xl border-2 border-dashed border-[var(--border-focus)] bg-[var(--canvas-bg)]/85 flex items-center justify-center pointer-events-none">
          <p className="text-base font-semibold flex items-center gap-2"><Upload className="w-5 h-5" />Thả file video để tải lên kho</p>
        </div>
      )}
    </div>
  )
}

const VideoDetail: React.FC<{ video: VideoDTO; sessions: SessionDTO[]; onClose: () => void; onChanged: () => Promise<unknown>; onNewSession: () => void }> =
  ({ video, sessions, onClose, onChanged, onNewSession }) => (
    <aside className="w-[380px] shrink-0 h-full overflow-y-auto bg-[var(--canvas-subtle)] p-5 space-y-5">
      <div className="flex items-start justify-between gap-2">
        <h2 className="text-base font-semibold break-all">{video.name}</h2>
        <IconButton label="Đóng" icon={<X className="w-4 h-4" />} onClick={onClose} />
      </div>
      <video key={video.id} src={video.media_url} poster={video.thumb_url} controls muted preload="metadata" className="w-full rounded-lg bg-black aspect-video" />
      <div className="space-y-2 text-xs">
        <Row label="Thời lượng">{fmtDuration(video.duration_s)}</Row>
        {video.width > 0 && <Row label="Khung hình">{video.width} × {video.height} · {video.fps.toFixed(0)} fps</Row>}
        {video.size_bytes > 0 && <Row label="Dung lượng">{(video.size_bytes / 1048576).toFixed(1)} MB</Row>}
        {video.added_at && <Row label="Thêm lúc">{fmtDateTime(video.added_at)}</Row>}
        <Row label="DINO"><PrepareStatusLine video={video} onChanged={onChanged} /></Row>
        <Row label="CLIP"><ClipStatusLine video={video} onChanged={onChanged} /></Row>
        <Row label="Mã video"><span className="font-mono select-all">{video.id}</span></Row>
      </div>
      <Button variant="primary" size="md" className="w-full" disabled={!video.prepared} icon={<Plus className="w-4 h-4" />} onClick={onNewSession}
        title={video.prepared ? '' : 'Video phải tiền xử lý xong mới tạo phiên được'}>Tạo phiên từ video này</Button>
      <div className="space-y-2">
        <Label>Phiên đang dùng ({sessions.length})</Label>
        {sessions.length ? sessions.map(s => (
          <Panel key={s.id} className="!bg-[var(--card-bg)]">
            <a href={sessionHref(s.id)} className="flex items-center gap-2 px-3 py-2 text-xs hover:opacity-80">
              <Play className="w-3.5 h-3.5 text-[var(--kept-border)]" />
              <span className="flex-1 truncate font-medium">{s.name || s.id.slice(0, 8)}</span>
              <span className="font-mono text-[var(--text-muted)]">{s.n_kept}/{s.target_k}</span>
              <span className="text-[var(--text-muted)]">{fmtDateTime(s.created_at)}</span>
            </a>
          </Panel>
        )) : <p className="text-xs text-[var(--text-muted)]">Chưa có phiên nào dùng video này.</p>}
      </div>
      <p className="text-[11px] text-[var(--text-muted)]">Xoá / đổi tên video sẽ có cùng đợt quản lý quyền truy cập.</p>
    </aside>
  )

/** Thanh hành động khi chọn nhiều video: tạo phiên mới, thêm vào phiên có sẵn, bỏ chọn. Video chưa sẵn sàng bị bỏ qua. */
const SelectionBar: React.FC<{ videos: VideoDTO[]; sessions: SessionDTO[]; onClear: () => void; onSessionChanged: (s: SessionDTO) => void }> =
  ({ videos, sessions, onClear, onSessionChanged }) => {
    const [target, setTarget] = useState('')
    const [msg, setMsg] = useState('')
    const [busy, setBusy] = useState(false)
    const ready = videos.filter(v => v.prepared)
    const skipped = videos.length - ready.length
    const addTo = () => {
      const s = sessions.find(x => x.id === target)
      const ids = ready.map(v => v.id).filter(id => !s?.pool.video_ids?.includes(id))
      if (!s || !ids.length) { setMsg('Các video đã có trong phiên này.'); return }
      setBusy(true); setMsg('')
      api.addVideos(s.id, ids).then(r => { onSessionChanged(r); setMsg(`Đã thêm ${ids.length} video vào ${r.name || r.id.slice(0, 8)}.`); setTarget('') })
        .catch(e => setMsg(errorText(e))).finally(() => setBusy(false))
    }
    return (
      <div className="absolute bottom-5 left-1/2 -translate-x-1/2 flex items-center gap-3 px-4 py-2.5 rounded-xl bg-[var(--card-bg)] shadow-2xl text-xs">
        <span className="font-semibold">Đã chọn {videos.length} video</span>
        {skipped > 0 && <span className="text-amber-700 dark:text-amber-300">({skipped} chưa sẵn sàng, bỏ qua)</span>}
        <Button variant="primary" disabled={!ready.length} icon={<Plus className="w-3.5 h-3.5" />}
          onClick={() => { location.hash = newSessionHref(ready.map(v => v.id)) }}>Tạo phiên mới</Button>
        <select value={target} onChange={e => setTarget(e.target.value)} className={inputClass} aria-label="Chọn phiên để thêm video">
          <option value="">Thêm vào phiên…</option>
          {[...sessions].reverse().map(s => <option key={s.id} value={s.id}>{s.name || s.id.slice(0, 8)}</option>)}
        </select>
        <Button disabled={!target || !ready.length || busy} onClick={addTo}>{busy ? 'Đang thêm…' : 'Thêm'}</Button>
        <Button variant="ghost" onClick={onClear}>Bỏ chọn</Button>
        {msg && <span className="text-[var(--text-secondary)] max-w-[260px]">{msg}</span>}
      </div>
    )
  }

const Row = ({ label, children }: { label: string; children: React.ReactNode }) => (
  <div className="flex items-center gap-3"><span className="w-24 shrink-0 text-[var(--text-muted)]">{label}</span><div className="min-w-0 flex-1">{children}</div></div>
)

