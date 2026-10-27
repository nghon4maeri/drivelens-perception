/** Bounded admin timing log viewer (§21.19, §21.36); one page per explicit read, no extra polling. */
import { useEffect, useRef, useState } from 'react'
import { StaleResponseError } from '../api_v2'
import { operationLogs, type LogPage, type LogSource } from '../operations'
import { Button, ErrorBanner } from '../ui'

export function OperationsLogs({ inspect }: { inspect: (kind: string, id: string) => void }) {
  const [source, setSource] = useState<LogSource | ''>('')
  const [hours, setHours] = useState(1)
  const [errorsOnly, setErrorsOnly] = useState(false)
  const [uploadId, setUploadId] = useState('')
  const [jobId, setJobId] = useState('')
  const [requestId, setRequestId] = useState('')
  const [page, setPage] = useState<LogPage | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const seq = useRef(0)
  const load = async (cursor?: string) => {
    const n = ++seq.current
    setBusy(true); setError('')
    try {
      const result = await operationLogs({
        source: source || undefined,
        hours,
        errorsOnly,
        cursor,
        uploadId: uploadId.trim() || undefined,
        jobId: jobId.trim() || undefined,
        requestId: requestId.trim() || undefined,
      })
      if (n === seq.current) setPage(result)
    } catch (e) {
      if (n === seq.current && !(e instanceof StaleResponseError)) setError(e instanceof Error ? e.message : String(e))
    } finally { if (n === seq.current) setBusy(false) }
  }
  useEffect(() => {
    setPage(null); void load()
    return () => { seq.current++ }
  }, [source, hours, errorsOnly])
  return <div className="space-y-3 mt-3">
    <p className="text-sm text-[var(--text-muted)]">Log tracing đã lọc thông tin nhạy cảm, có thể thiếu; lưu tối đa 7 ngày.
      Chỉ đọc khi mở bảng, đổi bộ lọc hoặc bấm làm mới; tối đa 50 sự kiện/trang. Không phải raw log của máy chủ.</p>
    <div className="flex flex-wrap items-center gap-3">
      <label>Nguồn <select aria-label="Nguồn log" disabled={busy} value={source} onChange={e => setSource(e.target.value as LogSource | '')} className="border rounded p-2 bg-[var(--card-bg)]">
        <option value="">Tất cả</option><option value="api">API</option><option value="worker">Worker</option><option value="gpu">GPU</option><option value="browser">Trình duyệt (tự báo)</option>
      </select></label>
      <label>Khoảng thời gian <select aria-label="Khoảng thời gian log" disabled={busy} value={hours} onChange={e => setHours(Number(e.target.value))} className="border rounded p-2 bg-[var(--card-bg)]">
        {[1, 6, 24].map(n => <option key={n} value={n}>{n} giờ gần nhất</option>)}
      </select></label>
      <label className="flex gap-2"><input type="checkbox" checked={errorsOnly} disabled={busy} onChange={e => setErrorsOnly(e.target.checked)} />Chỉ lỗi</label>
      <input aria-label="Lọc theo Upload ID" disabled={busy} value={uploadId} onChange={e => setUploadId(e.target.value)} placeholder="Upload ID" className="border rounded p-2 text-xs font-mono bg-[var(--card-bg)] w-44" />
      <input aria-label="Lọc theo Job ID" disabled={busy} value={jobId} onChange={e => setJobId(e.target.value)} placeholder="Job ID" className="border rounded p-2 text-xs font-mono bg-[var(--card-bg)] w-44" />
      <input aria-label="Lọc theo Request ID" disabled={busy} value={requestId} onChange={e => setRequestId(e.target.value)} placeholder="Request ID" className="border rounded p-2 text-xs font-mono bg-[var(--card-bg)] w-44" />
      <Button disabled={busy} onClick={() => void load()}>Làm mới log</Button>
    </div>
    {error && <ErrorBanner message={`${error} — trang log chưa được cập nhật.`} />}
    {busy && <p role="status">Đang đọc log…</p>}
    {page && <>
      <p className="text-xs">Cửa sổ UTC: {page.window_start} → {page.window_end} · {page.items.length} sự kiện trên trang này.</p>
      <div className="overflow-x-auto"><table className="w-full text-sm text-left"><thead><tr><th>Thời gian UTC</th><th>Nguồn / bước</th><th>Kết quả</th><th>Thời gian chạy</th><th>Tra cứu</th></tr></thead>
        <tbody>{page.items.map(e => <tr key={e.id} className="border-t border-[var(--border-subtle)]">
          <td className="py-2 whitespace-nowrap">{e.observed_at}<div className="text-xs text-[var(--text-muted)]">Nhận: {e.received_at}</div></td>
          <td>{e.source}{e.source === 'browser' && ' (tự báo)'}<div className="font-mono text-xs">{e.stage}</div></td>
          <td>{e.data.status ?? '—'} {e.data.outcome} {e.data.error_code} {e.data.error_type}
            {typeof e.data.cache_hit === 'boolean' && <div>{e.data.cache_hit ? 'Dùng cache' : 'Tính mới'}</div>}</td>
          <td>{typeof e.data.wall_ms === 'number' ? `${e.data.wall_ms.toFixed(1)} ms` : '—'}</td>
          <td>{([['job', e.job_id], ['upload', e.upload_id], ['request', e.request_id]] as const).map(([kind, id]) => id &&
            <button key={kind} className="block underline text-xs font-mono" onClick={() => inspect(kind, id)}>{kind}: {id}</button>)}</td>
        </tr>)}</tbody></table></div>
      {!page.items.length && <p>Không có sự kiện phù hợp. Tracing có thể tắt, log đã hết hạn hoặc bị giới hạn.</p>}
      <Button disabled={busy || !page.next_cursor} onClick={() => void load(page.next_cursor ?? undefined)}>Trang log sau</Button>
    </>}
  </div>
}
