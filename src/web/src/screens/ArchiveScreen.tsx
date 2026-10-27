/**
 * Màn "Mẫu đã lưu" của phiên: frame đang giữ (state ở BE) — tìm, lọc theo đợt (proposal_n của quyết định giữ) / video, xem clip ±3 s,
 * bỏ khỏi K.
 */
import React, { useMemo, useState } from 'react'
import { CheckCircle2, Play, Search, Trash2, X } from 'lucide-react'
import type { DecisionDTO, UnitDTO } from '../api'
import { Button, EmptyState, IconButton, Label, Panel, ProgressBar, ScreenHeader, fmtTime, inputClass } from '../ui'
import { MediaImg } from '../components/MediaImg'

interface Props {
  keptUnits: UnitDTO[]
  targetK: number
  history: DecisionDTO[]
  videoName: (videoId: string) => string
  onOpenUnit: (unitId: string) => void
  onRemoveKept: (unitId: string) => void
}

const ALL = '__all__'
const OUTSIDE = 'ngoài đợt'

export const ArchiveScreen: React.FC<Props> = ({ keptUnits, targetK, history, videoName, onOpenUnit, onRemoveKept }) => {
  const [query, setQuery] = useState('')
  const [round, setRound] = useState(ALL)
  const [video, setVideo] = useState(ALL)

  // đợt của frame = proposal_n của quyết định "keep" cuối cùng
  const roundOf = useMemo(() => {
    const m = new Map<string, string>()
    for (const d of history) if (d.action === 'keep') m.set(d.unit_id, d.proposal_n == null ? OUTSIDE : String(d.proposal_n))
    return m
  }, [history])
  const rounds = useMemo(() => [...new Set(keptUnits.map(u => roundOf.get(u.id) ?? OUTSIDE))].sort((a, b) => Number(a) - Number(b)), [keptUnits, roundOf])
  const videos = useMemo(() => [...new Set(keptUnits.map(u => u.video_id))], [keptUnits])

  const q = query.trim().toLowerCase()
  const visible = keptUnits.filter(u =>
    (round === ALL || (roundOf.get(u.id) ?? OUTSIDE) === round) && (video === ALL || u.video_id === video) &&
    (!q || u.id.toLowerCase().includes(q) || videoName(u.video_id).toLowerCase().includes(q) || fmtTime(u.t0).includes(q))).reverse()
  const filtered = q !== '' || round !== ALL || video !== ALL

  return (
    <div className="flex-1 overflow-y-auto px-8 py-5 space-y-5">
      <ScreenHeader icon={<CheckCircle2 className="w-5 h-5 text-[var(--kept-border)]" />} title="Mẫu đã lưu"
        subtitle="Frame đã giữ trong phiên: rà lại, xem clip ±3 s, bỏ frame không cần." />

      <div className="grid grid-cols-3 gap-3">
        <Panel className="p-4 space-y-2">
          <Label>Đã giữ / ngân sách K</Label>
          <div className="font-mono text-xl font-bold">{keptUnits.length} <span className="text-sm text-[var(--text-muted)]">/ {targetK}</span></div>
          <ProgressBar value={(100 * keptUnits.length) / Math.max(1, targetK)} />
        </Panel>
        <Panel className="p-4 space-y-2"><Label>Số đợt có frame giữ</Label><div className="font-mono text-xl font-bold">{rounds.length}</div></Panel>
        <Panel className="p-4 space-y-2">
          <Label>Video nguồn</Label><div className="font-mono text-xl font-bold">{videos.length}</div>
          <div className="text-[11px] text-[var(--text-muted)] truncate">{videos.map(videoName).join(', ') || '—'}</div>
        </Panel>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-md bg-[var(--canvas-subtle)] min-w-[240px]">
          <Search className="w-4 h-4 text-[var(--text-muted)]" />
          <input value={query} onChange={e => setQuery(e.target.value)} placeholder="Tìm theo mã frame, video, thời điểm" className="flex-1 bg-transparent text-sm focus:outline-hidden" />
          {query && <IconButton label="Xoá tìm kiếm" icon={<X className="w-3.5 h-3.5" />} onClick={() => setQuery('')} className="!p-0.5" />}
        </div>
        <select value={round} onChange={e => setRound(e.target.value)} className={inputClass} aria-label="Lọc theo đợt">
          <option value={ALL}>Mọi đợt</option>
          {rounds.map(r => <option key={r} value={r}>{r === OUTSIDE ? 'Giữ ngoài đợt' : `Đợt ${r}`}</option>)}
        </select>
        <select value={video} onChange={e => setVideo(e.target.value)} className={`${inputClass} max-w-[220px]`} aria-label="Lọc theo video">
          <option value={ALL}>Mọi video</option>
          {videos.map(v => <option key={v} value={v}>{videoName(v)}</option>)}
        </select>
        {filtered && <Button variant="ghost" onClick={() => { setQuery(''); setRound(ALL); setVideo(ALL) }}>Bỏ lọc</Button>}
        <span className="ml-auto text-xs font-mono text-[var(--text-secondary)]">{visible.length} / {keptUnits.length}</span>
      </div>

      {!visible.length
        ? <EmptyState title={keptUnits.length ? 'Không có frame khớp bộ lọc' : 'Chưa giữ frame nào'}>{!keptUnits.length && 'Duyệt ở Studio rồi quay lại đây.'}</EmptyState>
        : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-3">
            {visible.map(u => {
              const r = roundOf.get(u.id) ?? OUTSIDE
              return (
                <div key={u.id} className="group rounded-xl overflow-hidden bg-[var(--canvas-subtle)]" title={`${u.id}\n${videoName(u.video_id)}`}>
                  <div className="relative aspect-video bg-[#181B1F]">
                    <MediaImg src={u.thumb_url} alt={u.id} loading="lazy" className="w-full h-full object-cover" />
                    <span className="absolute top-1.5 left-1.5 px-1.5 py-0.5 rounded bg-black/70 text-[11px] font-mono text-white">{fmtTime(u.t0)}</span>
                    <span className="absolute bottom-1.5 left-1.5 px-1.5 py-0.5 rounded bg-emerald-700/90 text-[11px] text-white">{r === OUTSIDE ? 'ngoài đợt' : `Đợt ${r}`}</span>
                    <div className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center gap-2">
                      <IconButton label="Xem clip ±3 s" icon={<Play className="w-4 h-4 fill-current" />} onClick={() => onOpenUnit(u.id)} className="!bg-black/75 !text-amber-300" />
                      <IconButton label="Bỏ khỏi K (chuyển sang Bỏ)" icon={<Trash2 className="w-4 h-4" />} onClick={() => onRemoveKept(u.id)} className="!bg-black/75 !text-red-300" />
                    </div>
                  </div>
                  <div className="px-2.5 py-1.5 text-[11px] text-[var(--text-secondary)] truncate">{videoName(u.video_id)}</div>
                </div>
              )
            })}
          </div>
        )}
    </div>
  )
}
