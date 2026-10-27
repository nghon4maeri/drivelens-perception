/**
 * Thành phần giao diện dùng chung (docs/design/UI_DESIGN.md §4–5): nút, hộp thoại, khối nền, tiêu đề màn, nhãn trạng thái, thanh tiến độ,
 * danh sách rỗng, thông báo lỗi, ô nhập — cùng các kiểu / hàm định dạng nhỏ. Component mới dùng các thành phần này, không chép chuỗi class.
 */
import React, { useEffect } from 'react'
import { X } from 'lucide-react'
import type { Action } from '../api'

// ---- kiểu và định dạng ----

export type Status = 'kept' | 'dropped' | 'undecided'
export const ACTION_OF: Record<Status, Action> = { kept: 'keep', dropped: 'drop', undecided: 'clear' }

/** Giây → "mm:ss.ss" (hoặc "h:mm:ss.ss"). */
export function fmtTime(t: number): string {
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60
  const ss = s.toFixed(2).padStart(5, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${String(m).padStart(2, '0')}:${ss}`
}

/** Giây → "7 phút 07 s" / "40 s". */
export function fmtDuration(t: number): string {
  const m = Math.floor(t / 60), s = Math.round(t % 60)
  return m ? `${m} phút ${String(s).padStart(2, '0')} s` : `${s} s`
}

export const fmtNum = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(3))
export const fmtDateTime = (iso: string) => new Date(iso).toLocaleString('vi-VN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' })

const cx = (...c: (string | false | undefined)[]) => c.filter(Boolean).join(' ')

// ---- nút ----

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  size?: 'sm' | 'md'
  icon?: React.ReactNode
}

const VARIANT = {
  primary: 'bg-[var(--kept-border)] text-white hover:brightness-110 font-semibold shadow-sm',
  secondary: 'bg-[var(--canvas-subtle)] text-[var(--text-primary)] hover:bg-[var(--border-color)]',
  ghost: 'text-[var(--text-secondary)] hover:bg-[var(--canvas-subtle)] hover:text-[var(--text-primary)]',
  danger: 'text-red-700 dark:text-red-400 hover:bg-red-500/10',
}

export function Button({ variant = 'secondary', size = 'sm', icon, className, children, ...rest }: ButtonProps) {
  return (
    <button type="button" {...rest} className={cx('inline-flex items-center justify-center gap-1.5 rounded-md transition-colors cursor-pointer',
      'disabled:opacity-40 disabled:cursor-not-allowed focus-visible:outline-2 focus-visible:outline-[var(--border-focus)]',
      size === 'sm' ? 'px-2.5 py-1 text-xs' : 'px-4 py-2 text-sm', VARIANT[variant], className)}>
      {icon}{children}
    </button>
  )
}

export function IconButton({ label, icon, active, className, ...rest }: React.ButtonHTMLAttributes<HTMLButtonElement> & { label: string; icon: React.ReactNode; active?: boolean }) {
  return (
    <button type="button" title={label} aria-label={label} {...rest}
      className={cx('p-1.5 rounded-md transition-colors cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed',
        'focus-visible:outline-2 focus-visible:outline-[var(--border-focus)]',
        active ? 'bg-[var(--canvas-subtle)] text-[var(--border-focus)]' : 'text-[var(--text-secondary)] hover:bg-[var(--canvas-subtle)] hover:text-[var(--text-primary)]', className)}>
      {icon}
    </button>
  )
}

// ---- khối ----

export function Panel({ className, children, ...rest }: React.HTMLAttributes<HTMLDivElement>) {
  return <div {...rest} className={cx('rounded-lg bg-[var(--canvas-subtle)]', className)}>{children}</div>
}

export function ScreenHeader({ icon, title, subtitle, actions }: { icon?: React.ReactNode; title: string; subtitle?: string; actions?: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-xl font-bold flex items-center gap-2">{icon}{title}</h1>
        {subtitle && <p className="text-xs text-[var(--text-secondary)] mt-1">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
    </div>
  )
}

/** Nhãn nhỏ đầu khối (chữ hoa, mờ). */
export const Label = ({ children, title }: { children: React.ReactNode; title?: string }) =>
  <div title={title} className="text-[11px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">{children}</div>

const TONE = {
  ok: 'bg-emerald-500/12 text-[var(--kept-border)]',
  warn: 'bg-amber-500/15 text-amber-800 dark:text-amber-300',
  error: 'bg-red-500/12 text-red-700 dark:text-red-400',
  neutral: 'bg-[var(--canvas-subtle)] text-[var(--text-secondary)]',
  accent: 'bg-[var(--border-focus)]/12 text-[var(--border-focus)]',
}

export function Badge({ tone = 'neutral', icon, children, title }: { tone?: keyof typeof TONE; icon?: React.ReactNode; children: React.ReactNode; title?: string }) {
  return <span title={title} className={cx('inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] font-medium whitespace-nowrap', TONE[tone])}>{icon}{children}</span>
}

export function ProgressBar({ value, tone = 'ok', className }: { value: number; tone?: 'ok' | 'warn'; className?: string }) {
  return (
    <div className={cx('h-1.5 rounded-full bg-[var(--border-color)] overflow-hidden', className)}>
      <div className={cx('h-full transition-all duration-300', tone === 'ok' ? 'bg-[var(--kept-border)]' : 'bg-amber-500')}
        style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  )
}

export function EmptyState({ icon, title, children }: { icon?: React.ReactNode; title: string; children?: React.ReactNode }) {
  return (
    <div className="py-14 px-6 text-center rounded-xl bg-[var(--canvas-subtle)] space-y-2">
      {icon && <div className="flex justify-center text-[var(--text-muted)]">{icon}</div>}
      <p className="text-sm font-semibold">{title}</p>
      {children && <div className="text-xs text-[var(--text-secondary)]">{children}</div>}
    </div>
  )
}

export function ErrorBanner({ message, onClose }: { message: string; onClose?: () => void }) {
  if (!message) return null
  return (
    <div role="alert" onClick={onClose} className={cx('px-3 py-2 rounded-lg bg-red-500/10 text-xs text-red-700 dark:text-red-400', onClose && 'cursor-pointer')}>
      {message}{onClose && <span className="opacity-60"> · bấm để ẩn</span>}
    </div>
  )
}

// ---- hộp thoại ----

export function Modal({ open, title, icon, onClose, footer, width = 640, children }: {
  open: boolean; title: string; icon?: React.ReactNode; onClose: () => void; footer?: React.ReactNode; width?: number; children: React.ReactNode
}) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    addEventListener('keydown', onKey)
    return () => removeEventListener('keydown', onKey)
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-xs" onClick={onClose}>
      <div role="dialog" aria-label={title} className="w-full max-h-[90vh] flex flex-col rounded-xl bg-[var(--card-bg)] shadow-2xl text-[var(--text-primary)]"
        style={{ maxWidth: width }} onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between gap-2 px-5 py-3">
          <h2 className="text-base font-semibold flex items-center gap-2 min-w-0 truncate">{icon}{title}</h2>
          <IconButton label="Đóng (Esc)" icon={<X className="w-4 h-4" />} onClick={onClose} />
        </div>
        <div className="px-5 pb-4 overflow-y-auto space-y-3">{children}</div>
        {footer && <div className="flex items-center justify-between gap-2 px-5 py-3 bg-[var(--canvas-subtle)] rounded-b-xl">{footer}</div>}
      </div>
    </div>
  )
}

// ---- ô nhập ----

export const inputClass = 'px-2.5 py-1.5 rounded-md bg-[var(--card-bg)] border border-[var(--border-color)] text-xs text-[var(--text-primary)] focus:outline-hidden focus:border-[var(--border-focus)]'

export function Field({ label, hint, children }: { label: React.ReactNode; hint?: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-xs" title={hint}>
      <span className="text-[var(--text-secondary)]">{label}</span>
      {children}
    </label>
  )
}
