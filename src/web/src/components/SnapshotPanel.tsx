/** Chốt và xem manifest snapshot bất biến trong tab Xuất; không thay thế export tập kept (UI_DESIGN §8). */
import { useCallback, useEffect, useState } from 'react'
import { api, errorText, type SnapshotDTO, type SnapshotDetailDTO } from '../api'
import { PILOT } from '../mode'
import { Badge, Button, ErrorBanner, Field, Modal, Panel, fmtDateTime, fmtTime, inputClass } from '../ui'

export function SnapshotPanel({ sid, revision, kept, pending, onCreated }: { sid: string; revision: number; kept: number; pending: boolean; onCreated?: (id: string) => void }) {
  const [snapshots, setSnapshots] = useState<SnapshotDTO[]>([])
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [detail, setDetail] = useState<SnapshotDetailDTO>()
  const load = useCallback(() => api.snapshots(sid).then(setSnapshots), [sid])
  useEffect(() => { let live = true; api.snapshots(sid).then(r => { if (live) setSnapshots(r) }).catch(e => { if (live) setError(errorText(e)) }); return () => { live = false } }, [sid, revision])
  const create = async () => {
    setBusy(true); setError(''); setMessage('')
    try {
      const r = await api.createSnapshot(sid, note)
      setMessage(r.created ? `Đã chốt snapshot #${r.n}.` : `Tập giữ và ngữ cảnh không đổi; dùng lại snapshot #${r.n}.`)
      setNote(''); await load(); onCreated?.(r.id)
    } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  const open = async (id: string) => {
    setBusy(true); setError('')
    try { setDetail(await api.snapshot(id)) } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  const download = async (s: SnapshotDTO) => {
    setBusy(true); setError(''); setMessage(`Đang kiểm tra nguồn và tạo ZIP snapshot #${s.n}…`)
    try { await api.downloadSnapshot(s); setMessage(`Đã mở tải ZIP snapshot #${s.n} trong trình duyệt.`) }
    catch (e) { setError(errorText(e)); setMessage('') } finally { setBusy(false) }
  }
  return <Panel className="p-5 space-y-3">
    <div className="flex items-center justify-between"><h2 className="font-semibold">Snapshot</h2><Button variant="primary" disabled={busy || !kept || pending} onClick={create}>Chốt {kept} frame đang giữ</Button></div>
    <p className="text-xs text-[var(--text-muted)]">Chốt tập giữ và ngữ cảnh thành bản bất biến. Duyệt tiếp không sửa các snapshot đã chốt.</p>
    <Field label="Ghi chú snapshot"><input className={inputClass} value={note} onChange={e => setNote(e.target.value)} placeholder="Ví dụ: vòng rà soát đầu" /></Field>
    {pending && <p className="text-xs">{PILOT ? 'Có lựa chọn chưa gửi. Trở về Studio và bấm Đề xuất trước khi chốt snapshot.'
      : 'Lưu xong quyết định trước khi chốt snapshot.'}</p>}
    <ErrorBanner message={error} />
    {message && <p role="status" className="text-xs text-[var(--kept-border)]">{message}</p>}
    {!snapshots.length && <p className="text-xs text-[var(--text-muted)]">Chưa có snapshot.</p>}
    {snapshots.map(s => <div key={s.id} className="flex flex-wrap gap-3 items-center text-xs">
      <Button disabled={busy} onClick={() => open(s.id)}>Snapshot #{s.n}</Button>
      {!PILOT && <Button disabled={busy} onClick={() => download(s)}>Tải ZIP snapshot #{s.n}</Button>}
      <span>{fmtDateTime(s.created_at)} · {s.n_members} frame</span>
      <Badge tone={s.is_current ? 'ok' : 'warn'}>{s.is_current ? 'Khớp tập hiện tại' : 'Tập hoặc ngữ cảnh đã đổi'}</Badge>
      <span>{s.note}</span>
    </div>)}
    <Modal open={!!detail} title={`Snapshot #${detail?.n ?? ''} · ${detail?.n_members ?? 0} frame`} onClose={() => setDetail(undefined)} width={900}>
      {detail && <>
        <p className="text-xs">{detail.note} · {fmtDateTime(detail.created_at)} · {detail.is_current ? 'Khớp tập hiện tại' : 'Tập hoặc ngữ cảnh đã đổi'}</p>
        <p className="text-xs text-[var(--text-muted)]">Danh sách lấy từ manifest đã chốt. Khi tải ZIP snapshot, hệ thống kiểm hash nguồn và trích đúng các mẫu này.</p>
        <div className="max-h-80 overflow-auto"><table className="w-full text-xs"><thead><tr>{['Mẫu', 'Nguồn', 'Vị trí', 'Giữ lúc'].map(h => <th key={h} className="text-left py-2">{h}</th>)}</tr></thead><tbody>
          {detail.members.map(m => <tr key={m.unit_id}><td className="py-2 font-mono">{m.unit_id}</td><td>{m.provenance.video?.name || m.source_id}</td><td>{m.provenance.t0 == null ? '—' : fmtTime(m.provenance.t0)} · frame {m.provenance.frame_idx ?? '—'}</td><td>{m.provenance.decision?.decided_at ? fmtDateTime(m.provenance.decision.decided_at) : '—'}</td></tr>)}
        </tbody></table></div>
        <details className="text-xs"><summary className="cursor-pointer">Ngữ cảnh đã chốt</summary><pre className="overflow-auto whitespace-pre-wrap">{JSON.stringify(detail.context, null, 2)}</pre></details>
      </>}
    </Modal>
  </Panel>
}
