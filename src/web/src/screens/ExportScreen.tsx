/**
 * Màn "Xuất" của một phiên:
 * - V1: Gọi command `export` ở BE (mỗi lần một bản mới) -> tải ngay .zip.
 * - Pilot V2 (CONTRACTS §21.8): Xuất từ Snapshot bất biến -> che mờ PII (khuôn mặt và biển số xe)
 *   -> Owner Confirmation Gate kiểm tra ảnh preview & báo cáo -> đóng gói ZIP -> cấp link tải.
 */
import React, { useEffect, useState } from 'react'
import { Archive, CheckCircle, Download, ExternalLink, Eye, FileText, Loader2, ShieldCheck } from 'lucide-react'
import { api, errorText, type DecisionDTO, type ExportDTO, type SessionDTO, type UnitDTO } from '../api'
import { apiV2, type DownloadV2, type ExportV2, type JobV2, type ReviewV2, type SnapshotV2 } from '../api_v2'
import { Button, ErrorBanner, Label, Panel, ScreenHeader, fmtTime } from '../ui'
import { ExportList } from './ExportsScreen'
import { SnapshotPanel } from '../components/SnapshotPanel'
import { MediaImg } from '../components/MediaImg'
import { PILOT } from '../mode'

interface Props {
  session: SessionDTO
  keptUnits: UnitDTO[]
  history: DecisionDTO[]
  videoName: (videoId: string) => string
  pending: boolean
}

export const ExportScreen: React.FC<Props> = ({ session, keptUnits, history, videoName, pending }) => {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<ExportDTO>()
  const [past, setPast] = useState<ExportDTO[]>([])

  // Pilot V2 State
  const [snapshots, setSnapshots] = useState<SnapshotV2[]>([])
  const [selectedSnapshot, setSelectedSnapshot] = useState<string>('')
  const [activeExport, setActiveExport] = useState<ExportV2 | null>(null)
  const [review, setReview] = useState<ReviewV2 | null>(null)
  const [downloadInfo, setDownloadInfo] = useState<DownloadV2 | null>(null)
  const [exports, setExports] = useState<ExportV2[]>([])
  const [exportCursor, setExportCursor] = useState<string | null>(null)
  const [job, setJob] = useState<JobV2 | null>(null)

  const loadPast = () => api.exports(session.id).then(setPast).catch(e => setError(errorText(e)))

  const loadSnapshots = async (createdId?: string) => {
    try {
      const page = await apiV2.snapshots(session.id)
      setSnapshots(page.items)
      setSelectedSnapshot(prev => createdId ?? (page.items.some(s => s.id === prev) ? prev : page.items[0]?.id ?? ''))
    } catch (e) {
      setError(errorText(e))
    }
  }

  const selectExport = (exp: ExportV2) => {
    if (exp.id === activeExport?.id) return
    setReview(null); setDownloadInfo(null); setJob(null); setError('')
    setActiveExport(exp)
  }

  const loadExports = async (cursor?: string) => {
    const page = await apiV2.exports(session.id, cursor)
    setExports(prev => cursor ? [...prev, ...page.items] : page.items)
    setExportCursor(page.next_cursor)
    return page.items
  }

  useEffect(() => {
    if (!PILOT) {
      loadPast()
    } else {
      loadSnapshots()
      loadExports().then(items => { if (items[0]) selectExport(items[0]) }).catch(e => setError(errorText(e)))
    }
  }, [session.id])

  // Polling export status khi đang REQUESTED, PROCESSING, FINALIZING
  useEffect(() => {
    if (!PILOT || !activeExport) return
    let live = true
    let t: ReturnType<typeof setTimeout>
    const terminal = ['READY', 'FAILED', 'CANCELLED', 'EXPIRED', 'NEEDS_CONFIRMATION'].includes(activeExport.state)
    if (terminal) {
      if (['NEEDS_CONFIRMATION', 'READY'].includes(activeExport.state)) {
        apiV2.exportReview(activeExport.id).then(r => { if (live) setReview(r) }).catch(e => { if (live) setError(errorText(e)) })
      }
      if (activeExport.state === 'READY') {
        apiV2.exportDownload(activeExport.id).then(r => { if (live) setDownloadInfo(r) }).catch(e => { if (live) setError(errorText(e)) })
      }
      return () => { live = false }
    }

    const poll = async () => {
      try {
        const cur = await apiV2.export(activeExport.id)
        if (!live) return
        setActiveExport(cur)
        setExports(prev => prev.map(e => e.id === cur.id ? cur : e))
        const jobId = cur.state === 'FINALIZING' ? cur.finalize_job_id : cur.build_job_id
        if (jobId) {
          const progress = await apiV2.job(jobId)
          if (live) setJob(progress)
        }
      } catch (e) {
        if (live) setError(errorText(e))
      }
      if (live) t = setTimeout(poll, 2000)
    }
    void poll()
    return () => { live = false; clearTimeout(t) }
  }, [activeExport?.id, activeExport?.state])

  const startPilotExport = async (snapId: string) => {
    setBusy(true)
    setError('')
    setReview(null)
    setDownloadInfo(null)
    try {
      const exp = await apiV2.createExport(snapId)
      selectExport(exp)
      await loadExports()
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  const confirmPilotExport = async () => {
    if (!activeExport || !review) return
    setBusy(true)
    setError('')
    try {
      const updated = await apiV2.confirmExport(activeExport.id, review.payload_hash, review.review_receipt_id)
      setActiveExport(updated)
      setExports(prev => prev.map(e => e.id === updated.id ? updated : e))
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  const runV1 = () => {
    setBusy(true)
    setError('')
    api.export(session.id)
      .then(r => {
        setResult(r)
        return loadPast()
      })
      .catch(e => setError(errorText(e)))
      .finally(() => setBusy(false))
  }

  if (PILOT) {
    return (
      <div className="flex-1 overflow-y-auto px-8 py-5 space-y-6">
        <SnapshotPanel sid={session.id} revision={keptUnits.length} kept={keptUnits.length} pending={pending} onCreated={loadSnapshots} />

        <ScreenHeader
          icon={<ShieldCheck className="w-5 h-5 text-sky-400" />}
          title="Xuất Dữ liệu Đã Che PII"
          subtitle="Chuẩn bị ảnh đã che PII → kiểm tra và xác nhận → lưu gói dataset ZIP lên S3 để tải lại."
        />

        <ErrorBanner message={error} onClose={() => setError('')} />

        {/* Khối chọn Snapshot */}
        <div className="space-y-3">
          <Label>Chọn Snapshot để xuất ({snapshots.length} snapshot đã chốt)</Label>
          {snapshots.length === 0 ? (
            <Panel className="p-4 text-xs text-[var(--text-muted)]">
              Chưa có snapshot nào. Vui lòng tạo một Snapshot ở trên trước khi yêu cầu xuất dữ liệu.
            </Panel>
          ) : (
            <div className="flex flex-wrap items-center gap-3">
              <select
                className="px-3 py-2 bg-[var(--canvas-subtle)] border border-[var(--border-color)] rounded-md text-sm"
                value={selectedSnapshot}
                onChange={e => setSelectedSnapshot(e.target.value)}
              >
                {snapshots.map(s => (
                  <option key={s.id} value={s.id}>
                    Snapshot #{s.n} ({s.n_members} mẫu) - {new Date(s.created_at).toLocaleDateString('vi-VN')} {s.note ? `[${s.note}]` : ''}
                  </option>
                ))}
              </select>

              <Button
                variant="primary"
                size="md"
                disabled={Boolean(busy || !selectedSnapshot || exports.some(e => ['REQUESTED', 'PROCESSING', 'FINALIZING'].includes(e.state)))}
                onClick={() => startPilotExport(selectedSnapshot)}
                icon={busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <ShieldCheck className="w-4 h-4" />}
              >
                Chuẩn bị dataset (Che PII)
              </Button>
            </div>
          )}
        </div>

        <Panel className="p-4 space-y-2">
          <Label>Lịch sử xuất của phiên</Label>
          {!exports.length && <p className="text-xs text-[var(--text-muted)]">Chưa có lần xuất nào.</p>}
          {exports.map(exp => <div key={exp.id}>
            <Button disabled={busy} onClick={() => selectExport(exp)}>
              {exp.id === activeExport?.id ? 'Đang xem · ' : ''}
              {new Date(exp.created_at).toLocaleString('vi-VN')} · {exp.n_members} ảnh · {exp.state}
            </Button>
          </div>)}
          {exportCursor && <Button disabled={busy} onClick={() => loadExports(exportCursor).catch(e => setError(errorText(e)))}>Xem thêm</Button>}
        </Panel>

        {/* Khối trạng thái & Owner Confirmation Gate */}
        {activeExport && (
          <Panel className="p-5 space-y-4 border border-sky-500/30 bg-sky-950/10">
            <div className="flex items-center justify-between border-b border-[var(--border-color)] pb-3">
              <div>
                <div className="text-sm font-semibold flex items-center gap-2">
                  <span>Trạng thái Export:</span>
                  <span className="font-mono text-xs px-2 py-0.5 rounded bg-sky-900/40 text-sky-300">
                    {activeExport.state}
                  </span>
                </div>
                <div className="text-xs text-[var(--text-muted)] mt-1 font-mono">
                  Export ID: {activeExport.id}
                </div>
              </div>

              {activeExport.state === 'READY' && downloadInfo && (
                <a
                  href={downloadInfo.url}
                  download={downloadInfo.filename}
                  className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-emerald-600 hover:bg-emerald-500 text-white font-medium text-sm transition-colors"
                >
                  <Download className="w-4 h-4" />
                  Tải dataset ZIP ({(downloadInfo.size_bytes / 1024).toFixed(1)} KB)
                </a>
              )}
            </div>

            {activeExport.state === 'NEEDS_CONFIRMATION' && <p className="text-sm" role="status">
              Đã lưu ảnh che PII và kết quả xử lý. Chưa tạo file ZIP: kiểm tra preview bên dưới rồi bấm “Xác nhận & Lưu dataset ZIP”.
            </p>}
            {review && !review.source_identity_verified && <p className="text-sm text-red-600" role="alert">
              Lần xuất cũ chưa xác minh đúng frame nguồn. Không dùng các ảnh này để bàn giao; hãy tạo lần xuất mới từ snapshot.
            </p>}
            {activeExport.state === 'READY' && <p className="text-sm" role="status">Dataset ZIP đã lưu trên S3; bạn có thể mở lại lần xuất này để tải.</p>}

            {job && ['REQUESTED', 'PROCESSING', 'FINALIZING'].includes(activeExport.state) && <div className="text-xs space-y-1" role="status">
              <div>{job.state === 'RUNNING' ? job.phase : `Đang chờ worker · ${job.state}`} · {job.progress.done}/{job.progress.total} ảnh</div>
              {job.progress.total > 0 && <progress className="w-full" max={job.progress.total} value={job.progress.done} />}
            </div>}

            {/* Trạng thái đang xử lý PII */}
            {['REQUESTED', 'PROCESSING'].includes(activeExport.state) && (
              <div className="flex items-center gap-3 py-4 text-sm text-[var(--text-secondary)]">
                <Loader2 className="w-5 h-5 animate-spin text-sky-400" />
                <span>Worker đang giải mã frame, phát hiện và che mờ PII (khuôn mặt, biển số xe)...</span>
              </div>
            )}

            {/* Trạng thái đang đóng gói ZIP */}
            {activeExport.state === 'FAILED' && (
              <div className="p-3 rounded-md bg-red-500/10 border border-red-500/30 text-red-300 text-xs">
                {activeExport.error?.code === 'pii_model_unavailable'
                  ? 'Worker chưa có model che PII nên không xuất ảnh chưa che. Liên hệ vận hành để cài model rồi thử lại.'
                  : String(activeExport.error?.message ?? 'Export lỗi; thử lại.')}
              </div>
            )}

            {activeExport.state === 'FINALIZING' && (
              <div className="flex items-center gap-3 py-4 text-sm text-[var(--text-secondary)]">
                <Loader2 className="w-5 h-5 animate-spin text-emerald-400" />
                <span>Đang đóng gói bundle ZIP và tải lên bộ lưu trữ đám mây...</span>
              </div>
            )}

            {/* Owner Confirmation Gate */}
            {['NEEDS_CONFIRMATION', 'READY'].includes(activeExport.state) && review && (
              <div className="space-y-4 pt-2">
                <div className="p-4 rounded-md bg-amber-500/10 border border-amber-500/30 text-amber-200 text-xs leading-relaxed space-y-1">
                  <div className="font-semibold text-sm flex items-center gap-1.5 text-amber-400">
                    <Eye className="w-4 h-4" />
                    {activeExport.state === 'READY' ? 'Ảnh và báo cáo của lần xuất đã hoàn tất' : 'Cổng xác nhận chủ sở hữu (Owner Confirmation Gate)'}
                  </div>
                  <p>
                    {activeExport.state === 'READY' ? 'Gói ZIP đã được xác nhận và đóng gói. ' : 'Vui lòng kiểm tra kỹ các ảnh đã được che mờ PII và báo cáo trước khi xác nhận. '}
                    Che tự động có thể sót (mặt nhỏ, biển số xa/mờ) hoặc che thừa; nếu thấy sót, đừng xác nhận.
                    {' '}Chế độ mới tìm PII trên ảnh thu nhỏ rồi che trên ảnh gốc; hãy kiểm tra kỹ PII nhỏ hoặc ở xa.
                    {activeExport.state !== 'READY' && ' Sau khi bạn xác nhận, hệ thống sẽ đóng gói bất biến file ZIP có thể tải về.'}
                  </p>
                  <div className="pt-2 font-mono text-[11px] text-slate-300">
                    Payload Hash: <span className="text-amber-300">{review.payload_hash}</span>
                  </div>
                </div>

                <div className="flex items-center justify-between gap-3">
                  {review.report_url && (
                    <a
                      href={review.report_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1.5 px-3 py-2 rounded-md bg-[var(--canvas-subtle)] hover:bg-[var(--border-color)] text-xs text-sky-300"
                    >
                      <FileText className="w-4 h-4" />
                      Xem Báo Cáo HTML
                      <ExternalLink className="w-3 h-3" />
                    </a>
                  )}

                  {activeExport.state === 'NEEDS_CONFIRMATION' && <Button
                    variant="primary"
                    size="md"
                    disabled={busy || !review.source_identity_verified}
                    onClick={confirmPilotExport}
                    icon={busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle className="w-4 h-4" />}
                  >
                    Xác nhận & Lưu dataset ZIP
                  </Button>}
                </div>

                {/* Danh sách ảnh xem trước đã che PII */}
                <div className="space-y-2">
                  <Label>Xem trước các ảnh đã che mờ ({review.members.length} mẫu)</Label>
                  <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3 max-h-96 overflow-y-auto p-2 bg-[var(--canvas-subtle)] rounded-md">
                    {review.members.map(m => (
                      <div key={m.ordinal} className="flex flex-col rounded overflow-hidden border border-[var(--border-color)] bg-slate-900">
                        {m.preview_url ? (
                          <img src={m.preview_url} alt={m.unit_id} loading="lazy" className="w-full aspect-video object-cover" />
                        ) : (
                          <div className="w-full aspect-video flex items-center justify-center text-xs text-[var(--text-muted)]">
                            Không có ảnh
                          </div>
                        )}
                        <div className="p-1.5 text-[10px] font-mono truncate text-[var(--text-secondary)]">
                          #{m.ordinal} {m.unit_id}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </Panel>
        )}
      </div>
    )
  }

  // Chế độ V1 Fallback
  const lastKeep = new Map(history.filter(d => d.action === 'keep').map(d => [d.unit_id, d]))

  return (
    <div className="flex-1 overflow-y-auto px-8 py-5 space-y-5">
      <SnapshotPanel sid={session.id} revision={history.length} kept={keptUnits.length} pending={pending} />
      <ScreenHeader
        icon={<Download className="w-5 h-5 text-[var(--kept-border)]" />}
        title="Xuất dữ liệu"
        subtitle={`Xuất tập đang giữ hiện tại · Mục tiêu: ${session.goal}`}
        actions={
          <>
            {result && (
              <a
                href={result.download_url}
                download
                className="inline-flex items-center gap-1.5 px-3 py-2 rounded-md bg-[var(--canvas-subtle)] text-sm hover:bg-[var(--border-color)]"
              >
                <Download className="w-4 h-4" />
                Tải bản vừa xuất (.zip)
              </a>
            )}
            <Button
              variant="primary"
              size="md"
              disabled={busy || pending || !keptUnits.length}
              onClick={runV1}
              icon={busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
            >
              Xuất {keptUnits.length} frame
            </Button>
          </>
        }
      />
      <ErrorBanner message={error} onClose={() => setError('')} />
      {result && <p className="text-xs text-[var(--kept-border)]">Đã xuất {result.n_frames} frame — bấm "Tải bản vừa xuất" để lấy file.</p>}

      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label>Các lần xuất của phiên ({past.length})</Label>
          <a href="#exports" className="inline-flex items-center gap-1 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)]">
            <Archive className="w-3.5 h-3.5" />
            Mọi phiên
          </a>
        </div>
        <ExportList items={past} highlight={result?.id} />
      </div>

      <div className="space-y-2">
        <Label>Xem trước ({keptUnits.length} frame sẽ xuất)</Label>
        <Panel className="max-h-[50vh] overflow-auto">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-[var(--canvas-subtle)] text-[var(--text-muted)]">
              <tr>
                {['', 'Mã frame', 'Video', 't0', 'Đợt', 'Quyết định lúc'].map(h => (
                  <th key={h} className="text-left px-3 py-2 font-semibold">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border-color)] font-mono">
              {keptUnits.map(u => {
                const d = lastKeep.get(u.id)
                return (
                  <tr key={u.id}>
                    <td className="px-3 py-1.5">
                      <MediaImg src={u.thumb_url} alt="" loading="lazy" className="w-14 aspect-video object-cover rounded" />
                    </td>
                    <td className="px-3 py-1.5 select-text">{u.id}</td>
                    <td className="px-3 py-1.5 font-sans">{videoName(u.video_id)}</td>
                    <td className="px-3 py-1.5">{fmtTime(u.t0)}</td>
                    <td className="px-3 py-1.5">{d?.proposal_n ?? '—'}</td>
                    <td className="px-3 py-1.5">{d ? new Date(d.decided_at).toLocaleString('vi-VN') : '—'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </Panel>
      </div>
    </div>
  )
}
