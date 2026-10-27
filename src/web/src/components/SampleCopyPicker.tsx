/** Admin's private prepared videos; shared read-only samples reference existing objects (§21.39). */
import type { SourceV2 } from '../api_v2'
import { Link2, RefreshCw, Video } from 'lucide-react'
import { mediaRef } from '../pilot_api'
import { Button, fmtDuration } from '../ui'
import { MediaImg } from './MediaImg'

export function sampleCopyMissing(s: SourceV2): string[] {
  return [s.scope !== 'PRIVATE' || s.kind !== 'VIDEO' || s.availability !== 'READY' ? 'Video READY trong kho riêng' : '',
    !s.frames.ready ? 'FRAMES' : '', !s.dino.ready ? 'DINO' : '', !s.clip.ready ? 'CLIP' : ''].filter(Boolean)
}

export function SampleCopyPicker({ sources, busy, nextCursor, onReference, onNext, onReload }: {
  sources: SourceV2[]; busy: boolean; nextCursor: string | null; onReference: (s: SourceV2) => void
  onNext: () => void; onReload: () => void
}) {
  const videos = sources.filter(s => s.scope === 'PRIVATE' && s.kind === 'VIDEO')
  return <div className="space-y-3">
    <p className="text-xs">Thêm tham chiếu chỉ đọc từ video đã chuẩn bị trong kho của bạn. Không sao chép file,
      không chạy lại DINO/CLIP. Mẫu mới ở trạng thái ẩn; kiểm tra rồi bấm Xuất bản để mọi người dùng.
      Nguồn gốc không thể xóa khi còn được dùng làm mẫu.</p>
    <Button className="border border-[var(--border-strong)]" icon={<RefreshCw className="w-3.5 h-3.5" />} disabled={busy} onClick={onReload}>Tải lại kho của tôi</Button>
    <div className="max-h-[32rem] overflow-auto space-y-3">
      {videos.map(s => {
        const missing = sampleCopyMissing(s)
        return <div key={s.id} className="rounded-lg border border-[var(--border-subtle)] bg-[var(--card-bg)] p-3 flex flex-col sm:flex-row gap-3">
          <div className="w-full sm:w-36 shrink-0 aspect-video self-start rounded-md overflow-hidden bg-[#181B1F]">
            {s.frames.ready && s.availability === 'READY'
              ? <MediaImg src={mediaRef('thumbnail', s.id, `${s.core_source_id}:0.000-0.000`)} alt={s.name} loading="lazy" className="w-full h-full object-cover" />
              : <div className="w-full h-full flex flex-col items-center justify-center gap-1 text-xs text-white/70"><Video className="w-6 h-6" /><span>Chưa có ảnh xem trước</span></div>}
          </div>
          <div className="min-w-0 flex-1 space-y-2">
            <div className="text-sm font-semibold line-clamp-2 break-all" title={s.name}>{s.name}</div>
            <p className="text-xs text-[var(--text-secondary)]">{s.metadata.duration_ms == null ? 'Chưa rõ thời lượng' : fmtDuration(s.metadata.duration_ms / 1000)} · {s.metadata.n_units ?? '—'} frame</p>
            <p className={`text-xs ${missing.length ? 'text-[var(--text-secondary)]' : 'text-[var(--kept-text)]'}`}>{missing.length ? `Cần chuẩn bị: ${missing.join(', ')}` : 'FRAMES · DINO · CLIP đã sẵn sàng'}</p>
            <Button variant="primary" icon={<Link2 className="w-3.5 h-3.5" />} disabled={busy || missing.length > 0} onClick={() => onReference(s)}>Thêm vào kho mẫu</Button>
          </div>
        </div>
      })}
      {!busy && !videos.length && <p className="text-xs">Trang này chưa có video riêng. Bạn có thể xem trang tiếp hoặc tải video lên Kho dữ liệu.</p>}
    </div>
    {nextCursor && <Button className="border border-[var(--border-strong)]" disabled={busy} onClick={onNext}>Trang tiếp của kho</Button>}
  </div>
}
