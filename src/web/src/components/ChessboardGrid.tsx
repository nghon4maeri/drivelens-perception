/**
 * Lưới picks của đợt đang xem (UI_DESIGN §2; #61): tối đa 4 cột, mỗi ô ≥ 220 px — màn hẹp thì tự giảm cột thay vì thu nhỏ ảnh.
 * Báo số cột thật lên trên để phím mũi tên đi đúng hàng.
 */
import React, { useEffect, useRef, useState } from 'react'
import { DashcamThumbnail, type GridItem } from './DashcamThumbnail'
import type { Status } from '../ui'

const MIN_CELL = 220, GAP = 12, MAX_COLS = 4

interface Props {
  items: GridItem[]
  focusedIndex: number
  showBoxes: boolean
  emptyText: string
  onColumns: (n: number) => void
  onSelectIndex: (index: number) => void
  onToggle: (index: number) => void
  onSet: (index: number, s: Status) => void
  onOpenVideo: (index: number) => void
}

export const ChessboardGrid: React.FC<Props> = ({ items, focusedIndex, showBoxes, emptyText, onColumns, onSelectIndex, onToggle, onSet, onOpenVideo }) => {
  const ref = useRef<HTMLDivElement>(null)
  const [cols, setCols] = useState(MAX_COLS)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const obs = new ResizeObserver(([e]) => {
      const n = Math.max(1, Math.min(MAX_COLS, Math.floor((e.contentRect.width + GAP) / (MIN_CELL + GAP))))
      setCols(n); onColumns(n)
    })
    obs.observe(el)
    return () => obs.disconnect()
  }, [onColumns])

  return (
    <div ref={ref} className="w-full max-w-[1100px]">
      {items.length === 0
        ? <div className="py-20 text-center text-sm text-[var(--text-secondary)] rounded-xl bg-[var(--canvas-subtle)]">{emptyText}</div>
        : (
          <div className="grid gap-3 p-1" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
            {items.map((item, i) => (
              <DashcamThumbnail key={item.unit.id} item={item} index={i} isFocused={focusedIndex === i} showBoxes={showBoxes}
                onFocus={() => onSelectIndex(i)} onToggle={() => onToggle(i)} onSet={s => onSet(i, s)} onOpenVideo={() => onOpenVideo(i)} />
            ))}
          </div>
        )}
    </div>
  )
}
