/** Render stable evidence primitives; algorithms supply data, never algorithm-specific UI branches (§18). */
import type { ProposalEvidence as Evidence } from '../api'

export function ProposalEvidence({ rows }: { rows: Evidence[] }) {
  return <div className="px-4 pb-3 space-y-2 text-xs max-h-48 overflow-y-auto">
    {rows.map((e, i) => {
      if (e.kind === 'nearest') return <div key={i} className="flex gap-3 items-center">
        <a href="#datasets" title={`Dataset version ${e.source_id} · ${e.unit_id}`}><img src={e.image_url} alt={e.label} className="w-28 h-20 object-contain rounded bg-[var(--canvas-subtle)]" /></a>
        <span>{e.label} · cosine {e.similarity.toFixed(3)}<br /><span className="text-[var(--text-muted)]">{e.field} · {e.source_id}</span></span>
      </div>
      if (e.kind === 'scalar') return (
        <div key={i} className="p-2 rounded bg-[var(--canvas-subtle)] space-y-1">
          <div className="flex items-center justify-between">
            <span className="font-medium">{e.label}</span>
            <b>{e.observed === false ? 'Chưa có tín hiệu' : (e.value != null ? e.value.toFixed(3) : '—')}</b>
          </div>
          {e.explanation && <p className="text-[11px] text-amber-700 dark:text-amber-300 font-mono">{e.explanation}</p>}
          {e.method && <span className="block text-[11px] text-[var(--text-muted)]">{e.method}</span>}
        </div>
      )
      if (e.kind === 'detections') return (
        <div key={i} className="text-[var(--text-muted)] flex items-center justify-between">
          <span>{e.label}: {e.boxes.length} box</span>
          {e.comparison && <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-700 dark:text-purple-300">đối chiếu TTA</span>}
        </div>
      )
      return null
    })}
  </div>
}
