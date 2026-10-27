/** Real bounded worker phase timings; no invented percentage or inference ETA (§21.40). */
import { useEffect, useState } from 'react'
import type { PhaseTimingV2 } from '../api_v2'
import { phaseLabel } from '../proposal_phases'

export function ProposalProgress({ stages, active }: { stages: PhaseTimingV2[]; active: boolean }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!active) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [active])
  if (!stages.length) return null
  return <details open={active} className="mx-6 mb-3 rounded-md border border-[var(--border-subtle)] p-3 text-xs">
    <summary className="cursor-pointer font-medium">Các bước đề xuất và thời gian thực thi</summary>
    <ol className="mt-2 space-y-1.5">
      {stages.map((stage, i) => {
        const running = stage.state === 'running' && active
        const seconds = (running ? Math.max(stage.elapsed_ms, now - Date.parse(stage.started_at)) : stage.elapsed_ms) / 1000
        return <li key={i} className="flex flex-wrap justify-between gap-2">
          <span>{phaseLabel(stage.phase)}{running ? ' · đang chạy' : stage.state === 'error' ? ' · đã dừng/lỗi' : stage.state === 'running' ? ' · chưa hoàn tất' : ''}
            {stage.total > 0 && ` · ${stage.done}/${stage.total}`}
            {stage.cache_hit === true ? ' · dùng cache' : stage.cache_hit === false ? ' · có dữ liệu chưa cache' : ''}</span>
          <span className="font-mono tabular-nums">{seconds.toFixed(1)} s</span>
        </li>
      })}
    </ol>
  </details>
}
