/**
 * Chỉ báo upload nhỏ luôn hiển thị ở cấp ứng dụng (CONTRACTS §21.35):
 * - Luôn thấy được khi chuyển giữa Kho dữ liệu, Phiên và các màn khác.
 * - Bấm vào để mở/thu gọn danh sách tiến trình chi tiết; cho phép chọn lại file đối với upload bị gián đoạn sau reload.
 */
import { useRef, useState } from 'react'
import { AlertCircle, CheckCircle2, ChevronDown, ChevronUp, FolderOpen, Loader2, X } from 'lucide-react'
import { Button, ProgressBar } from '../ui'
import { cancelNeedsFileUpload, dismissUpload, startUpload, useUploads } from '../upload_manager'

export function GlobalUploadIndicator() {
  const uploads = useUploads()
  const [expanded, setExpanded] = useState(false)
  const [resumeTargetKey, setResumeTargetKey] = useState<string | undefined>()
  const fileInputRef = useRef<HTMLInputElement>(null)

  if (uploads.length === 0) return null

  const runningCount = uploads.filter(u => u.status === 'running').length
  const needsFileCount = uploads.filter(u => u.status === 'needs_file').length
  const errorCount = uploads.filter(u => u.status === 'error').length
  const doneCount = uploads.filter(u => u.status === 'done').length

  const summaryText = runningCount > 0
    ? `Đang xử lý ${runningCount} upload`
    : needsFileCount > 0
      ? `${needsFileCount} upload cần chọn lại file`
      : errorCount > 0
        ? `${errorCount} upload gặp lỗi`
        : `Đã hoàn tất ${doneCount} upload`

  const triggerResumeFile = (key: string) => {
    setResumeTargetKey(key)
    fileInputRef.current?.click()
  }

  return (
    <div
      className="fixed bottom-4 right-4 z-40 w-80 sm:w-96 rounded-xl border border-[var(--border-subtle)] bg-[var(--card-bg)] shadow-lg overflow-hidden text-xs"
      data-testid="global-upload-indicator"
    >
      <input
        ref={fileInputRef}
        type="file"
        accept="video/*"
        hidden
        onChange={e => {
          const f = e.target.files?.[0]
          const target = resumeTargetKey
          setResumeTargetKey(undefined)
          e.target.value = ''
          if (f) void startUpload(f, target)
        }}
      />
      <div className="flex items-center justify-between gap-2 px-3.5 py-2.5 bg-[var(--canvas-subtle)]">
        <button
          type="button"
          onClick={() => setExpanded(v => !v)}
          className="flex items-center gap-2 font-medium text-[var(--text-primary)] cursor-pointer flex-1 text-left"
          data-testid="global-upload-toggle"
        >
          {runningCount > 0 ? (
            <Loader2 className="w-4 h-4 animate-spin text-[var(--accent)] shrink-0" />
          ) : needsFileCount > 0 || errorCount > 0 ? (
            <AlertCircle className="w-4 h-4 text-amber-500 shrink-0" />
          ) : (
            <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
          )}
          <span className="truncate">{summaryText}</span>
          {expanded ? <ChevronDown className="w-4 h-4 ml-auto shrink-0" /> : <ChevronUp className="w-4 h-4 ml-auto shrink-0" />}
        </button>
        <a
          href="#videos"
          className="px-2 py-0.5 rounded text-[11px] text-[var(--text-secondary)] hover:text-[var(--text-primary)] underline shrink-0"
          title="Mở Kho dữ liệu"
        >
          Kho
        </a>
      </div>

      {expanded && (
        <div className="max-h-64 overflow-y-auto divide-y divide-[var(--border-subtle)] p-2.5 space-y-2" data-testid="global-upload-list">
          {uploads.map(u => (
            <div key={u.key} className="pt-2 first:pt-0 space-y-1.5">
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium truncate text-[var(--text-primary)]" title={u.name}>{u.name}</span>
                {u.status !== 'running' && (
                  <button
                    type="button"
                    onClick={() => (u.status === 'needs_file' ? void cancelNeedsFileUpload(u.key) : dismissUpload(u.key))}
                    className="p-0.5 rounded text-[var(--text-muted)] hover:text-[var(--text-primary)] cursor-pointer"
                    title={u.status === 'needs_file' ? 'Hủy upload dở' : 'Ẩn khỏi danh sách'}
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
              <ProgressBar value={100 * u.progress} className="w-full" />
              <div className="flex items-center justify-between gap-2">
                <span className={u.status === 'error' ? 'text-red-600' : u.status === 'needs_file' ? 'text-amber-600 font-medium' : 'text-[var(--text-secondary)]'}>
                  {u.state}{u.error ? `: ${u.error}` : ''}
                </span>
                {u.status === 'needs_file' && (
                  <Button size="sm" icon={<FolderOpen className="w-3 h-3" />} onClick={() => triggerResumeFile(u.key)}>
                    Chọn lại file để tiếp tục
                  </Button>
                )}
                {u.status === 'running' && (
                  <span className="text-[11px] text-[var(--text-muted)] font-mono">{Math.round(100 * u.progress)}%</span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
