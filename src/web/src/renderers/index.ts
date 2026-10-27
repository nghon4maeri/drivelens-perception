/** Một renderer cho mỗi `view.type` (architecture §7 "Trực quan hoá"). Thêm view = thêm một dòng ở đây. */
import type { ComponentType } from 'react'
import type { ProposalDTO, UnitDTO } from '../api'
import { EmbeddingMap } from './EmbeddingMap'
import { Timeline } from './Timeline'
import { DuplicatePairs } from './DuplicatePairs'

export type RendererProps = {
  data: any
  units: Map<string, UnitDTO>
  scores?: ProposalDTO['scores']          // điểm picks của batch mới nhất — mẫu khác để trống
  onOpen: (unitId: string) => void        // click điểm / cặp → mở chi tiết frame
}

export const RENDERERS: Record<string, ComponentType<RendererProps>> = {
  embedding_map: EmbeddingMap,
  timeline: Timeline,
  duplicate_pairs: DuplicatePairs,
}

export const STATUS_COLOR: Record<string, string> = { pool: '#b8bec7', picked: '#2f6fdb', kept: '#1f9d55', dropped: '#d64545' }

/** Chia giá trị vào [0, 1]; dải hằng → 0.5. */
export const scale = (xs: number[]) => {
  const lo = Math.min(...xs), hi = Math.max(...xs)
  return (x: number) => hi > lo ? (x - lo) / (hi - lo) : 0.5
}
