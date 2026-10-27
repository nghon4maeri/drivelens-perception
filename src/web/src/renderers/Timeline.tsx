/** `timeline`: series theo t (+ điểm combined từ ProposalDTO.scores nếu có), hàng đánh dấu picks / kept / dropped (index vào t). */
import { STATUS_COLOR, scale, type RendererProps } from '.'

type Data = { video_id: string; t: number[]; unit_id: string[]; series: Record<string, number[]>
              picks: number[]; kept: number[]; dropped: number[] }
const W = 720, PAD = 40, PLOT_H = 160, ROW = 18
const LINE_COLORS = ['#7a4fd6', '#d9822b', '#0f8b8d', '#9c6b30']

export function Timeline({ data, scores, onOpen }: RendererProps) {
  const d = data as Data
  if (!d.t.length) return <p className="muted">Video không có unit.</p>
  const st = scale(d.t)
  const x = (i: number) => PAD + st(d.t[i]) * (W - 2 * PAD)
  const series: [string, (number | null)[]][] = Object.entries(d.series)
  if (scores?.combined) {   // Only selected batch samples have scores; other samples stay blank.
    const byId = new Map(scores.unit_id.map((u, i) => [u as string, scores.combined[i] as number]))
    series.push(['combined (mẫu trong batch mới nhất)', d.unit_id.map(u => byId.get(u) ?? null)])
  }
  const rows: [string, number[]][] = [['picks', d.picks], ['kept', d.kept], ['dropped', d.dropped]]
  const H = PLOT_H + rows.length * ROW + 30
  return (
    <figure>
      <svg viewBox={`0 0 ${W} ${H}`} className="chart">
        <rect width={W} height={H} className="chart-bg" />
        {series.map(([name, ys], k) => {
          const vals = ys.filter((v): v is number => v !== null)
          const sy = scale(vals)
          const segs: string[][] = [[]]   // đứt nét chỗ không có giá trị
          ys.forEach((v, i) => v === null ? segs.push([]) : segs.at(-1)!.push(`${x(i)},${10 + (1 - sy(v)) * (PLOT_H - 20)}`))
          return segs.filter(g => g.length).map((g, j) => {
            if (g.length === 1) {
              const [cx, cy] = g[0].split(',')
              return <circle key={`${name}${j}`} cx={cx} cy={cy} r={3} fill={LINE_COLORS[k % LINE_COLORS.length]} />
            }
            return <polyline key={`${name}${j}`} points={g.join(' ')} fill="none" stroke={LINE_COLORS[k % LINE_COLORS.length]} strokeWidth={1.5} />
          })
        })}
        {!series.length && <text x={W / 2} y={PLOT_H / 2} textAnchor="middle" className="muted-text">
          không có series — thêm field scalar ở tham số view hoặc xem điểm của batch đã đề xuất</text>}
        {rows.map(([name, ids], r) => {
          const y = PLOT_H + r * ROW + ROW / 2
          const color = STATUS_COLOR[name === 'picks' ? 'picked' : name]
          return (
            <g key={name}>
              <text x={4} y={y + 4} className="axis-text">{name}</text>
              {ids.map(i => (
                <rect key={i} x={x(i) - 3} y={y - 6} width={6} height={12} fill={color} className="clickable" onClick={() => onOpen(d.unit_id[i])}>
                  <title>{d.unit_id[i]} · t = {d.t[i]} s</title>
                </rect>
              ))}
            </g>
          )
        })}
        <text x={PAD} y={H - 6} className="axis-text">{d.t[0].toFixed(1)} s</text>
        <text x={W - PAD} y={H - 6} textAnchor="end" className="axis-text">{d.t.at(-1)!.toFixed(1)} s · video {d.video_id}</text>
      </svg>
      <figcaption className="legend">
        {series.map(([name], k) => <span key={name}><i style={{ background: LINE_COLORS[k % LINE_COLORS.length] }} />{name}</span>)}
        <span className="muted">mỗi series chuẩn hoá riêng về [min, max]</span>
      </figcaption>
    </figure>
  )
}
