/** Frozen branch coverage of the displayed proposal, never next-batch settings. */
import type { StrategyTrace } from '../api'

export function ODDCoverageSummary({ trace }: { trace?: StrategyTrace[] }) {
  const gate = trace?.find(t => t.step === 'odd_gate')
  const selected = trace?.find(t => t.step === 'odd_selection')
  const measured = trace?.find(t => t.step === 'odd_uncertainty')
  if (!gate?.branches) return null
  return <details className="w-full max-w-[1100px] rounded border border-[var(--border-subtle)] p-3 text-xs" open>
    <summary className="cursor-pointer font-semibold">Coverage của đợt đang xem · {selected?.count ?? 0}/{selected?.requested ?? 0} ảnh</summary>
    <p className="my-2">{gate.pool_count ?? 0} frame chưa duyệt → {gate.count ?? 0} qua CLIP → {gate.shortlist_count ?? 0} trong shortlist.
      {' '}Coverage theo truy vấn CLIP, chưa phải nhãn ODD xác nhận. Mở chi tiết ảnh để xem cosine, margin và lý do chọn.</p>
    {measured && <p className="my-2">Đã đo model trên {measured.count ?? 0} ảnh; {measured.observed_count ?? 0} ảnh có tín hiệu detection. Ảnh không có box ở cả hai lượt không được tính là model tự tin và được loại khỏi lựa chọn theo uncertainty.</p>}
    <div className="max-h-48 overflow-auto">
      <table className="w-full text-left"><thead><tr><th>Nhánh truy vấn</th><th>Ứng viên</th><th>Đã giữ</th><th>Đợt này</th></tr></thead>
        <tbody>{gate.branches.map((branch, i) => <tr key={branch.id} className="border-t border-[var(--border-subtle)]">
          <td className="py-1 pr-2">{branch.query}</td><td>{gate.eligible_counts?.[i] ?? 0}</td>
          <td>{gate.kept_counts?.[i] ?? 0}</td><td>{selected?.selected_counts?.[i] ?? 0}</td>
        </tr>)}</tbody>
      </table>
    </div>
    {gate.count === 0 && <p role="status" className="mt-2">Không có ứng viên đạt ngưỡng. Kiểm tra truy vấn/đối chứng hoặc ngưỡng; không tự lấy ảnh ngoài điều kiện.</p>}
    {!!selected && (selected.count ?? 0) < (selected.requested ?? 0) && <p className="mt-2">Không ép đủ batch bằng cách nới điều kiện chống trùng hoặc gate CLIP.</p>}
  </details>
}
