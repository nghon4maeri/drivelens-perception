/**
 * Một ô trên lưới (UI_DESIGN §1, §7; #61): ảnh, thời điểm, trạng thái Giữ / Bỏ / Chưa duyệt, trạng thái lưu (đang lưu / lỗi), 1–2 nhãn bằng chứng.
 * Không lặp điểm số hay lời giải thích trên ô — giải thích ở cấp đợt, điểm từng frame ở cửa sổ chi tiết (Space). M bật khung detector.
 * Focus = viền **ngoài** màu nhấn + số thứ tự đổi màu; Giữ = viền **trong** xanh + nhãn "Giữ".
 */
import React, { useState } from 'react'
import { AlertCircle, Check, Loader2, Play, RotateCcw, X } from 'lucide-react'
import type { EvidenceDTO, PickDTO, ProposalEvidence, UnitDTO } from '../api'
import { DetectionBoxes } from './Detections'
import { MediaImg } from './MediaImg'
import { fmtTime, type Status } from '../ui'

export type GridItem = { unit: UnitDTO; pick: PickDTO; status: Status; videoName: string; evidence?: EvidenceDTO; proposalEvidence?: ProposalEvidence[]; save?: 'saving' | 'error' }

interface Props {
  item: GridItem
  index: number
  isFocused: boolean
  showBoxes: boolean
  onFocus: () => void
  onToggle: () => void
  onSet: (s: Status) => void
  onOpenVideo: () => void
}

const BADGE: Record<Status, { label: string; className: string }> = {
  undecided: { label: 'Chưa duyệt', className: 'bg-black/55 text-amber-200' },
  kept: { label: 'Giữ', className: 'bg-[var(--kept-border)] text-white' },
  dropped: { label: 'Bỏ', className: 'bg-black/70 text-neutral-300' },
}

export const DashcamThumbnail: React.FC<Props> = ({ item, index, isFocused, showBoxes, onFocus, onToggle, onSet, onOpenVideo }) => {
  const { unit, status, evidence, save, proposalEvidence } = item
  const [aspectRatio, setAspectRatio] = useState(16 / 9)
  const isKept = status === 'kept', isDropped = status === 'dropped'
  const badge = BADGE[status]
  const original = proposalEvidence?.find((e): e is Extract<ProposalEvidence, { kind: 'detections' }> => e.kind === 'detections' && !e.comparison)
  const detections = original?.boxes ?? evidence?.detections
  const set = (e: React.MouseEvent, s: Status) => { e.stopPropagation(); onFocus(); onSet(s) }
  const chips = [...(detections != null ? [`${detections.length} đối tượng`] : []), ...Object.values(evidence?.tags ?? {})].slice(0, 2)
  const instabilityEv = proposalEvidence?.find((e): e is Extract<ProposalEvidence, { kind: 'scalar' }> => e.kind === 'scalar' && e.label === 'YOLO thiếu ổn định')

  return (
    <div onClick={() => { onFocus(); onToggle() }} title={`#${index + 1} · ${fmtTime(unit.t0)} · ${item.videoName}\nClick: Giữ ↔ Bỏ · Space: chi tiết frame`}
      className={`group relative flex flex-col rounded-lg transition-all duration-150 cursor-pointer select-none bg-[var(--card-bg)]
        ${isFocused ? 'outline-3 outline-offset-2 outline-[var(--border-focus)] z-10' : ''} ${isDropped ? 'opacity-45 saturate-50 hover:opacity-80' : ''}`}>
      <div className={`relative aspect-video w-full overflow-hidden rounded-lg bg-[#181B1F] ${isKept ? 'ring-3 ring-inset ring-[var(--kept-border)]' : ''}`}>
        <MediaImg src={unit.thumb_url} alt={unit.id} loading="lazy" onLoad={e => setAspectRatio(e.currentTarget.naturalWidth / e.currentTarget.naturalHeight)}
          className={`w-full h-full ${showBoxes ? 'object-contain' : 'object-cover'}`} />
        {showBoxes && detections && <DetectionBoxes detections={detections} labels={false} aspectRatio={aspectRatio} />}
        <span className={`absolute top-1.5 left-1.5 px-1 rounded text-[11px] font-mono pointer-events-none ${isFocused
          ? 'bg-[var(--border-focus)] text-white font-bold' : 'text-white/90 drop-shadow-[0_1px_2px_rgba(0,0,0,0.9)]'}`}>
          #{index + 1} · {fmtTime(unit.t0)}
        </span>
        {instabilityEv && (
          <span className="absolute top-1.5 right-1.5 flex items-center pointer-events-none z-10"
            title={instabilityEv.explanation ?? (instabilityEv.observed === false ? 'Chưa có tín hiệu' : `Thiếu ổn định: ${instabilityEv.value?.toFixed(2)}`)}>
            {instabilityEv.observed === false ? (
              <span className="px-1.5 py-0.5 rounded text-[10px] font-mono font-medium bg-neutral-900/85 text-neutral-300 border border-neutral-700/60 shadow">
                Chưa có tín hiệu
              </span>
            ) : (
              <span className="px-1.5 py-0.5 rounded text-[10px] font-mono font-bold bg-amber-500/90 text-black shadow">
                Thiếu ổn định: {instabilityEv.value != null ? instabilityEv.value.toFixed(2) : '—'}
              </span>
            )}
          </span>
        )}
        {save === 'saving' && <Loader2 aria-label="Đang lưu" className="absolute top-1.5 right-1.5 w-3.5 h-3.5 text-white animate-spin drop-shadow pointer-events-none" />}
        {save === 'error' && <span className="absolute top-1.5 right-1.5 flex items-center gap-1 px-1 rounded bg-red-600 text-white text-[11px] pointer-events-none">
          <AlertCircle className="w-3 h-3" />Lưu lỗi</span>}
        {chips.length > 0 && (
          <span className="absolute bottom-1.5 left-1.5 flex gap-1 pointer-events-none">
            {chips.map(c => <span key={c} className="px-1 rounded bg-black/60 text-[11px] text-white">{c}</span>)}
          </span>
        )}
        <span className={`absolute bottom-1.5 right-1.5 flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] font-semibold leading-none pointer-events-none ${badge.className}`}>
          {isKept ? <Check className="w-3 h-3 stroke-[2.5]" /> : isDropped ? <X className="w-3 h-3" /> : null}{badge.label}
        </span>

        <div className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center gap-1.5">
          {!isKept && <HoverButton label="Giữ (K)" onClick={e => set(e, 'kept')} className="bg-emerald-700/90 hover:bg-emerald-600"><Check className="w-4 h-4" /></HoverButton>}
          {!isDropped && <HoverButton label="Bỏ (D) — không giữ, không đề xuất lại" onClick={e => set(e, 'dropped')} className="bg-neutral-900/90 hover:bg-red-700"><X className="w-4 h-4" /></HoverButton>}
          {status !== 'undecided' && <HoverButton label="Huỷ quyết định (U) — về Chưa duyệt" onClick={e => set(e, 'undecided')} className="bg-neutral-700/90 hover:bg-neutral-600"><RotateCcw className="w-4 h-4" /></HoverButton>}
          <HoverButton label="Chi tiết frame + clip ±3 s (Space)" onClick={e => { e.stopPropagation(); onFocus(); onOpenVideo() }} className="bg-black/75 hover:bg-black text-amber-300">
            <Play className="w-4 h-4 fill-current" /></HoverButton>
        </div>
      </div>
    </div>
  )
}

const HoverButton = ({ label, onClick, className, children }: { label: string; onClick: (e: React.MouseEvent) => void; className: string; children: React.ReactNode }) => (
  <button type="button" title={label} aria-label={label} onClick={onClick} className={`p-1.5 rounded-full text-white shadow-md cursor-pointer ${className}`}>{children}</button>
)
