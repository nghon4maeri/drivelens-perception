/** `duplicate_pairs`: histogram số cặp theo khoảng cách Hamming pHash (chỉ số chẵn có nghĩa, #50) + cặp mẫu a|b theo từng mức. */
import { useEffect, useState } from 'react'
import { api } from '../api'
import type { RendererProps } from '.'

type Pair = { a: string; b: string; distance: number }
type Data = { hist: number[]; pairs: Pair[] }
const W = 720, H = 180, PAD = 30

export function DuplicatePairs({ data, units, onOpen }: RendererProps) {
  const d = data as Data
  const [threshold, setThreshold] = useState<number>()   // ngưỡng mặc định của nd_rate_at_k (#51), chỉ để tô màu
  useEffect(() => {
    api.component('metric', 'nd_rate_at_k').then(c => setThreshold(c.params_schema.properties?.threshold?.default as number)).catch(() => {})
  }, [])
  const even = d.hist.map((n, dist) => [dist, n]).filter(([dist]) => dist % 2 === 0)
  const max = Math.max(1, ...even.map(([, n]) => n))
  const bw = (W - 2 * PAD) / even.length
  const byDist = Map.groupBy(d.pairs, p => p.distance)
  const thumb = (id: string) => (
    <img src={units.get(id)?.thumb_url} alt={id} title={`${id} — mở chi tiết`} className="clickable" onClick={() => onOpen(id)} />
  )
  return (
    <figure>
      <svg viewBox={`0 0 ${W} ${H}`} className="chart">
        <rect width={W} height={H} className="chart-bg" />
        {even.map(([dist, n], i) => {
          const h = (n / max) * (H - 2 * PAD)
          return (
            <g key={dist}>
              <rect x={PAD + i * bw + 1} y={H - PAD - h} width={bw - 2} height={h} fill={threshold !== undefined && dist <= threshold ? '#d64545' : '#2f6fdb'}>
                <title>khoảng cách {dist}: {n} cặp</title>
              </rect>
              {dist % 8 === 0 && <text x={PAD + (i + 0.5) * bw} y={H - PAD + 14} textAnchor="middle" className="axis-text">{dist}</text>}
            </g>
          )
        })}
        <text x={PAD} y={16} className="axis-text">số cặp (max {max}){threshold !== undefined && ` · đỏ = khoảng cách ≤ ${threshold} (gần trùng theo ngưỡng ND-Rate mặc định)`}</text>
      </svg>
      <div className="pairs">
        {[...byDist].sort(([a], [b]) => a - b).map(([dist, ps]) => (
          <div key={dist} className="pair-row">
            <b>d = {dist}</b>
            {ps.map(p => <span key={p.a + p.b} className="pair">{thumb(p.a)}{thumb(p.b)}</span>)}
          </div>
        ))}
        {!d.pairs.length && <p className="muted">Không có cặp nào ở các mức khoảng cách đã chọn.</p>}
      </div>
    </figure>
  )
}
