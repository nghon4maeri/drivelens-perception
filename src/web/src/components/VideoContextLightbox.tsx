/**
 * Xem ảnh trước; Play mới tải/phát clip t0 ± ctx giây. Giữ ảnh khi clip tải/lỗi, Giữ/Bỏ ngay tại đây.
 * Phím: Esc đóng, Space phát/dừng, ←/→ tua ±0.5 s, K giữ, D bỏ, U bỏ chọn.
 */
import React, { useEffect, useRef, useState } from 'react'
import { Check, Image, Loader2, Pause, Play, RotateCcw, X } from 'lucide-react'
import type { EvidenceDTO, PickDTO, ProposalEvidence as Evidence, UnitDTO } from '../api'
import { ProposalEvidence } from './ProposalEvidence'
import { MediaImg } from './MediaImg'
import { useMediaUrl } from '../media'
import { DetectionBoxes, nearestSample } from './Detections'
import { Button, IconButton, fmtNum, fmtTime, inputClass, type Status } from '../ui'

interface Props {
  unit: UnitDTO
  mediaUrl?: string
  clipWindow?: number   // pilot: clip ngữ cảnh ±clipWindow s, mốc 0 = max(0, t0 − clipWindow) (§21.12)
  videoName: string
  status: Status
  pick?: PickDTO
  evidence?: EvidenceDTO   // bằng chứng (#59): khung detector trên ảnh gốc, nhãn, telemetry theo thời điểm đang phát
  proposalEvidence?: Evidence[]
  nearestKept?: { unit?: UnitDTO; similarity: number; field: string }   // frame đã giữ gần nhất lúc đề xuất (explain_proposal, #61)
  docs: Record<string, string>
  onDecide: (s: Status) => void
  onClose: () => void
}

export const VideoContextLightbox: React.FC<Props> = ({ unit, mediaUrl, clipWindow, videoName, status, pick, evidence, proposalEvidence, nearestKept, docs, onDecide, onClose }) => {
  const video = useRef<HTMLVideoElement>(null)
  const [ctx, setCtx] = useState(3)
  const original = proposalEvidence?.find((e): e is Extract<Evidence, { kind: 'detections' }> => e.kind === 'detections' && !e.comparison)
  const detections = original?.boxes ?? evidence?.detections
  const base = clipWindow === undefined ? 0 : Math.max(0, unit.t0 - clipWindow)   // giây nguồn ứng với 0 s của file video
  const [clipRequested, setClipRequested] = useState(false)
  const { url: videoSrc, failed: mediaFailed, retry: retryMedia } = useMediaUrl(mediaUrl, clipRequested, 0)
  const [playback, setPlayback] = useState<{ src?: string; state: 'loading' | 'ready' | 'failed' }>({ state: 'loading' })
  const [now, setNow] = useState(unit.t0)
  const [playing, setPlaying] = useState(false)
  const [showFrame, setShowFrame] = useState(true)
  const [frameLoaded, setFrameLoaded] = useState(false)
  const [videoPresented, setVideoPresented] = useState(false)
  const [showComparisonBoxes, setShowComparisonBoxes] = useState(false)
  const [aspectRatio, setAspectRatio] = useState(16 / 9)
  const comparisonItem = proposalEvidence?.find(e => e.kind === 'detections' && (e as any).comparison) as ({ kind: 'detections'; label: string; boxes: import('../api').Detection[]; comparison?: boolean }) | undefined
  const start = Math.max(0, unit.t0 - ctx), end = unit.t0 + ctx
  const playbackState = playback.src === videoSrc ? playback.state : 'loading'
  const videoReady = !!videoSrc && !mediaFailed && playbackState === 'ready'
  const videoFailed = mediaFailed || playbackState === 'failed'
  const showVideo = !showFrame && videoReady && videoPresented

  const requestPlayback = () => {
    setClipRequested(true); setShowFrame(false)
    if (mediaFailed) retryMedia()
    const v = video.current
    if (v) {
      if (playbackState === 'failed') v.load()
      v.play().catch(() => { if (v.paused) setPlaying(false) })
    }
  }
  const viewFrame = () => { setShowFrame(true); video.current?.pause() }

  const seek = (t: number) => { if (video.current && video.current.readyState > 0) video.current.currentTime = Math.min(end, Math.max(start, t)) - base }
  const toggle = () => {
    if (showFrame || !clipRequested || videoFailed) { requestPlayback(); return }
    const v = video.current
    if (!v || !videoReady) return
    if (v.paused) v.play().catch(() => setPlaying(false))
    else v.pause()
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement) return
      const k = e.key.toLowerCase()
      if (k === 'escape') onClose()
      else if (k === ' ') toggle()
      else if (k === 'arrowleft') seek(now - 0.5)
      else if (k === 'arrowright') seek(now + 0.5)
      else if (k === 'k') onDecide('kept')
      else if (k === 'd') onDecide('dropped')
      else if (k === 'u') onDecide('undecided')
      else return
      e.preventDefault()
    }
    addEventListener('keydown', onKey)   // phím tắt của Studio tự tắt khi lightbox mở
    return () => removeEventListener('keydown', onKey)
  })

  const displayTime = showVideo ? now : unit.t0
  const offset = displayTime - unit.t0
  const sample = evidence?.telemetry ? nearestSample(evidence.telemetry, displayTime) : undefined
  const telemetryLine = sample && [sample.speed_kmh != null && `${Math.round(sample.speed_kmh)} km/h`, sample.brake && 'phanh',
    sample.steer_deg != null && `lái ${sample.steer_deg.toFixed(0)}°`].filter(Boolean).join(' · ')
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-xs select-none" onClick={onClose}>
      <div role="dialog" aria-label="Clip ngữ cảnh" className="relative w-full max-w-[760px] max-h-[95vh] overflow-y-auto bg-[var(--card-bg)] rounded-xl shadow-2xl text-[var(--text-primary)]" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 py-2.5">
          <div className="flex items-center gap-2 text-xs font-mono text-[var(--text-secondary)] min-w-0">
            <span className="font-semibold text-[var(--text-primary)] truncate">{unit.id}</span><span>·</span>
            <span>Clip ±{ctx}s</span><span>·</span><span className="truncate">{videoName}</span>
          </div>
          <IconButton label="Đóng (Esc)" icon={<X className="w-4 h-4" />} onClick={onClose} />
        </div>

        <div className="relative aspect-video w-full bg-[#111317]">
          <div className={`absolute inset-0 ${showVideo ? 'invisible' : ''}`} aria-hidden={showVideo}>
                <MediaImg eager src={unit.thumb_url} alt={unit.id} className="absolute inset-0 w-full h-full object-contain" />
                <MediaImg eager src={unit.frame_url} alt={`Ảnh gốc ${unit.id}`} className={`absolute inset-0 w-full h-full object-contain ${frameLoaded ? '' : 'opacity-0'}`} onLoad={e => { setFrameLoaded(true); setAspectRatio(e.currentTarget.naturalWidth / e.currentTarget.naturalHeight) }} onError={() => setFrameLoaded(false)} />
                {detections && <DetectionBoxes detections={detections} aspectRatio={aspectRatio} />}
                {showComparisonBoxes && comparisonItem && <DetectionBoxes detections={comparisonItem.boxes} stroke="#a855f7" strokeDasharray="4 2" aspectRatio={aspectRatio} />}
              </div>
          {clipRequested && videoSrc && <video ref={video} src={videoSrc} muted autoPlay playsInline preload="auto" className={`absolute inset-0 w-full h-full object-contain ${showVideo ? '' : 'invisible'}`}
                onLoadedMetadata={() => seek(start)}
                onLoadStart={() => { setPlayback({ src: videoSrc, state: 'loading' }); setPlaying(false); setVideoPresented(false) }}
                onLoadedData={() => setPlayback({ src: videoSrc, state: 'ready' })}
                onPlaying={() => { setPlayback({ src: videoSrc, state: 'ready' }); setVideoPresented(true); setPlaying(true) }}
                onWaiting={() => setVideoPresented(false)}
                onError={() => { setPlayback({ src: videoSrc, state: 'failed' }); setVideoPresented(false); setPlaying(false) }}
                onPlay={() => { if (showFrame) video.current?.pause(); else setPlaying(true) }} onPause={() => setPlaying(false)}
                onEnded={() => { if (!showFrame) { seek(start); video.current?.play().catch(() => setPlaying(false)) } }}
                onTimeUpdate={e => { const t = e.currentTarget.currentTime + base; setNow(t); if (t >= end) seek(start) }} />}
          {!showFrame && !showVideo && <div role={videoFailed ? 'alert' : 'status'} className="absolute bottom-2 left-2 right-2 flex items-center justify-center gap-2 p-2 rounded text-center text-white text-xs bg-black/60">
            {videoFailed ? <p>Không tải hoặc phát được clip. Ảnh vẫn được giữ; bấm Thử lại clip để thử lại.</p>
              : <><Loader2 className="w-6 h-6 animate-spin" /><p>{videoSrc ? 'Đang tải clip…' : 'Đang chờ clip ngữ cảnh…'}</p></>}
          </div>}
          <div className="absolute top-2 left-2 px-2 py-1 rounded bg-black/70 text-[11px] font-mono text-white space-y-0.5 pointer-events-none">
            <div>t = {fmtTime(displayTime)} ({offset >= 0 ? '+' : ''}{offset.toFixed(2)} s)</div>
            {telemetryLine && <div>{telemetryLine}</div>}
          </div>
        </div>

        <div className="px-4 pt-2">
          <input type="range" min={start} max={end} step={0.05} value={Math.min(end, Math.max(start, displayTime))} onChange={e => seek(Number(e.target.value))}
            className="w-full accent-[var(--border-focus)] cursor-pointer" disabled={showFrame || !videoReady} />
          <div className="flex justify-between text-[11px] font-mono text-[var(--text-muted)]">
            <span>{fmtTime(start)}</span><span className="text-[var(--border-focus)] font-bold">t0 = {fmtTime(unit.t0)}</span><span>{fmtTime(end)}</span>
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-xs">
          <div className="flex items-center gap-1">
            <Button variant="ghost" onClick={toggle} disabled={!showFrame && clipRequested && !videoReady && !videoFailed} icon={playing ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}>{videoFailed && !showFrame ? 'Thử lại clip' : playing ? 'Dừng' : 'Phát'}</Button>
            <Button variant="ghost" onClick={() => seek(unit.t0)} disabled={showFrame || !videoReady} icon={<RotateCcw className="w-3.5 h-3.5" />}>Về t0</Button>
            <Button variant="ghost" onClick={viewFrame} disabled={showFrame} icon={<Image className="w-3.5 h-3.5" />}>Ảnh gốc</Button>
            {comparisonItem && (
              <Button
                variant={showComparisonBoxes ? 'primary' : 'ghost'}
                onClick={() => {
                  if (!showFrame) viewFrame()
                  setShowComparisonBoxes(v => !v)
                }}
                className={showComparisonBoxes ? 'bg-purple-600 hover:bg-purple-700 text-white' : ''}
              >
                Đối chiếu TTA ({comparisonItem.boxes.length} box lật)
              </Button>
            )}
            <label className="flex items-center gap-1.5 ml-1 text-[var(--text-secondary)]">± giây
              <input type="number" min={0.5} max={clipWindow} step={0.5} value={ctx} onChange={e => setCtx(Math.min(clipWindow ?? Infinity, Math.max(0.5, Number(e.target.value))))} className={`${inputClass} w-16`} />
            </label>
          </div>
          <div className="flex items-center gap-1.5">
            <Button variant="ghost" onClick={() => onDecide('undecided')} disabled={status === 'undecided'}>Huỷ quyết định (U)</Button>
            <Button onClick={() => onDecide('dropped')} disabled={status === 'dropped'} icon={<X className="w-3.5 h-3.5 text-red-500" />}>Bỏ (D)</Button>
            <Button variant="primary" onClick={() => onDecide('kept')} disabled={status === 'kept'} icon={<Check className="w-3.5 h-3.5" />}>Giữ (K)</Button>
          </div>
        </div>
        {(evidence?.tags || evidence?.detections) && (
          <div className="px-4 pb-2 flex flex-wrap gap-1.5 text-[11px]">
            {Object.entries(evidence?.tags ?? {}).map(([k, v]) => <span key={k} className="px-1.5 py-0.5 rounded bg-[var(--canvas-subtle)]" title={k}>{v}</span>)}
            {evidence?.detections && <span className="px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-800 dark:text-amber-300" title="Bật Ảnh gốc để xem khung">
              {evidence.detections.length} đối tượng: {[...new Set(evidence.detections.map(d => d.label))].join(', ')}</span>}
          </div>
        )}
        {evidence?.detections && <div className="px-4 pb-2 max-h-32 overflow-y-auto text-[11px] text-[var(--text-secondary)]">
          {evidence.detections.map((d, i) => <div key={i} title={d.source}>{d.label} · confidence {d.score.toFixed(3)} · <span className="font-mono">{d.source}</span></div>)}
        </div>}
        {nearestKept?.unit && (
          <div className="px-4 pb-2 flex items-center gap-3 text-[11px] text-[var(--text-secondary)]">
            <MediaImg src={nearestKept.unit.thumb_url} alt="" className="w-24 aspect-video object-cover rounded" />
            <span>Frame đã giữ gần nhất lúc đề xuất ({fmtTime(nearestKept.unit.t0)}) — tương đồng cos <b>{nearestKept.similarity.toFixed(2)}</b>
              {' '}trên <span className="font-mono">{nearestKept.field}</span>; thấp hơn là khác tập đã giữ nhiều hơn.</span>
          </div>
        )}
        {!!proposalEvidence?.length && <ProposalEvidence rows={proposalEvidence} />}
        {pick && (
          <div className="px-4 pb-3 text-[11px] font-mono text-[var(--text-secondary)] flex flex-wrap gap-x-3">
            <span>hạng #{pick.rank}</span><span>combined {fmtNum(pick.combined)}</span>
            {Object.entries(pick.breakdown).map(([k, v]) => <span key={k} title={docs[k]}>{k} {fmtNum(v)}</span>)}
          </div>
        )}
      </div>
    </div>
  )
}
