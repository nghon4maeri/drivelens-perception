/**
 * Màn "Dữ liệu đã xuất" (#54, UI_DESIGN §3): mọi lần xuất của mọi phiên (GET /exports), lọc theo phiên, tải .zip (CSV + ảnh).
 * `ExportList` dùng lại ở màn Xuất của một phiên.
 */
import React, { useEffect, useState } from 'react'
import { Archive, Download, Loader2 } from 'lucide-react'
import { api, errorText, type ExportDTO, type SessionDTO } from '../api'
import { sessionHref } from '../types/navigation'
import { EmptyState, ErrorBanner, ScreenHeader, fmtDateTime, inputClass } from '../ui'

export const ExportList: React.FC<{ items: ExportDTO[]; showSession?: boolean; highlight?: string }> = ({ items, showSession, highlight }) => {
  if (!items.length) return <EmptyState title="Chưa có lần xuất nào" />
  return (
    <div className="rounded-xl bg-[var(--canvas-subtle)] overflow-hidden">
      <table className="w-full text-xs">
        <thead className="text-[var(--text-muted)]">
          <tr>{['Lúc xuất', ...(showSession ? ['Phiên', 'Mục tiêu'] : []), 'Số frame', 'Định dạng', ''].map(h => <th key={h} className="text-left px-4 py-2.5 font-semibold">{h}</th>)}</tr>
        </thead>
        <tbody className="divide-y divide-[var(--border-color)]">
          {items.map(e => (
            <tr key={`${e.session_id}/${e.id}`} className={e.id === highlight ? 'bg-[var(--kept-bg)]' : ''}>
              <td className="px-4 py-2.5 font-mono">{fmtDateTime(e.created_at)}</td>
              {showSession && <>
                <td className="px-4 py-2.5"><a href={sessionHref(e.session_id)} className="font-medium text-[var(--kept-border)] hover:underline">{e.session_name || e.session_id.slice(0, 8)}</a></td>
                <td className="px-4 py-2.5 max-w-[340px] truncate text-[var(--text-secondary)]" title={e.goal}>{e.goal}</td>
              </>}
              <td className="px-4 py-2.5 font-mono font-bold">{e.n_frames}</td>
              <td className="px-4 py-2.5 font-mono text-[var(--text-secondary)]">{e.format}</td>
              <td className="px-4 py-2.5 text-right">
                <a href={e.download_url} download className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-[var(--kept-border)] hover:brightness-110 text-white font-semibold">
                  <Download className="w-3.5 h-3.5" />Tải .zip</a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export const ExportsScreen: React.FC<{ sessions: SessionDTO[] }> = ({ sessions }) => {
  const [items, setItems] = useState<ExportDTO[]>()
  const [error, setError] = useState('')
  const [sid, setSid] = useState('')
  useEffect(() => { api.exports().then(setItems).catch(e => setError(errorText(e))) }, [])
  const shown = (items ?? []).filter(e => !sid || e.session_id === sid)
  const withExports = sessions.filter(s => items?.some(e => e.session_id === s.id))

  return (
    <div className="flex-1 overflow-y-auto px-8 py-6 space-y-5">
      <ScreenHeader icon={<Archive className="w-5 h-5 text-[var(--kept-border)]" />} title="Dữ liệu đã xuất"
        subtitle="Mọi lần xuất của mọi phiên (mỗi lần một bản, không ghi đè). Tải .zip gồm export.csv + thư mục ảnh để đưa vào CVAT / Label Studio."
        actions={<select value={sid} onChange={e => setSid(e.target.value)} className={inputClass} aria-label="Lọc theo phiên">
          <option value="">Mọi phiên</option>
          {withExports.map(s => <option key={s.id} value={s.id}>{s.name || s.id.slice(0, 8)}</option>)}
        </select>} />
      <ErrorBanner message={error} onClose={() => setError('')} />
      {items ? <ExportList items={shown} showSession />
        : !error && <p className="flex items-center gap-2 text-xs text-[var(--text-secondary)]"><Loader2 className="w-4 h-4 animate-spin" />Đang tải…</p>}
    </div>
  )
}
