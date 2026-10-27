/** `embedding_map`: scatter 2D (PCA) màu theo status. Chỉ để khám phá — khoảng cách 2D bị méo. */
import { STATUS_COLOR, scale, type RendererProps } from '.'

type Data = { x: number[]; y: number[]; unit_id: string[]; status: string[] }
const ORDER = ['pool', 'picked', 'dropped', 'kept']   // vẽ pool trước để điểm đã quyết nằm trên
const W = 640, H = 420, PAD = 12

export function EmbeddingMap({ data, onOpen }: RendererProps) {
  const d = data as Data
  const sx = scale(d.x), sy = scale(d.y)
  const idx = d.x.map((_, i) => i).sort((a, b) => ORDER.indexOf(d.status[a]) - ORDER.indexOf(d.status[b]))
  const count = (s: string) => d.status.filter(x => x === s).length
  return (
    <figure>
      <p className="warn">Chỉ để khám phá: khoảng cách trên bản đồ 2D bị méo, không dùng làm căn cứ chọn hay đánh giá.</p>
      <svg viewBox={`0 0 ${W} ${H}`} className="chart">
        <rect width={W} height={H} className="chart-bg" />
        {idx.map(i => (
          <circle key={d.unit_id[i]} cx={PAD + sx(d.x[i]) * (W - 2 * PAD)} cy={H - PAD - sy(d.y[i]) * (H - 2 * PAD)}
            r={d.status[i] === 'pool' ? 3 : 5} fill={STATUS_COLOR[d.status[i]]} className="clickable" onClick={() => onOpen(d.unit_id[i])}>
            <title>{d.unit_id[i]} · {d.status[i]}</title>
          </circle>
        ))}
      </svg>
      <figcaption className="legend">
        {ORDER.map(s => <span key={s}><i style={{ background: STATUS_COLOR[s] }} />{s} ({count(s)})</span>)}
      </figcaption>
    </figure>
  )
}
