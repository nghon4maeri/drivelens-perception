/** Vẽ khung detector (CONTRACTS §8.0, box chuẩn hoá 0–1) chồng lên ảnh; đặt trong khối `relative` cùng kích thước ảnh. */
import type { Detection } from '../api'

export function DetectionBoxes({ detections, labels = true, aspectRatio = 16 / 9, stroke = '#f59e0b', strokeDasharray }: {
  detections: Detection[]; labels?: boolean; aspectRatio?: number; stroke?: string; strokeDasharray?: string
}) {
  return (
    <svg viewBox={`0 0 ${aspectRatio} 1`} preserveAspectRatio="xMidYMid meet" className="absolute inset-0 w-full h-full pointer-events-none" aria-hidden>
      {detections.map((d, i) => {
        const [x1, y1, x2, y2] = d.box
        return (
          <g key={i}>
            <rect x={x1 * aspectRatio} y={y1} width={(x2 - x1) * aspectRatio} height={y2 - y1} fill="none" stroke={stroke} strokeDasharray={strokeDasharray} vectorEffect="non-scaling-stroke" style={{ strokeWidth: 2 }} />
            {labels && <text x={x1 * aspectRatio} y={Math.max(0.03, y1 - 0.008)} fontSize={0.035} fill={stroke}>{d.label} {d.score.toFixed(2)}</text>}
          </g>
        )
      })}
    </svg>
  )
}

/** Mẫu telemetry gần thời điểm `t` nhất. */
export function nearestSample<T extends { t: number }>(samples: T[], t: number): T | undefined {
  let best: T | undefined
  for (const s of samples) if (!best || Math.abs(s.t - t) < Math.abs(best.t - t)) best = s
  return best
}
