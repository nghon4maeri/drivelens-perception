/**
 * Sidebar trái kiểu web chat (UI_DESIGN §2): trên = điều hướng màn global (Kho video, Dữ liệu đã xuất); dưới = danh sách phiên
 * (mới nhất trước) + "Phiên mới"; chân = theme, phím tắt. Gập thành dải icon.
 */
import React from 'react'
import { Archive, ChevronLeft, ChevronRight, FileText, Film, HelpCircle, LogOut, Moon, Plus, Sparkles, Sun, User } from 'lucide-react'
import type { SessionDTO, VideoDTO } from '../api'
import type { ProfileDTO } from '../api_v2'
import type { Route, ThemeMode } from '../types/navigation'
import { IconButton, Label, fmtDateTime } from '../ui'
import { isBusy } from './VideoLibrary'
import { QuotaBadge } from './QuotaBadge'
import { PILOT } from '../mode'

interface Props {
  open: boolean
  onToggle: () => void
  route: Route
  sessions: SessionDTO[]
  videos: VideoDTO[]
  theme: ThemeMode
  onCycleTheme: () => void
  onHelp: () => void
  profile?: ProfileDTO | null
  onLogout?: () => void
}

const THEME_ICON = { warm: <Sun className="w-4 h-4" />, dark: <Moon className="w-4 h-4" />, paper: <FileText className="w-4 h-4" /> }

export const AppSidebar: React.FC<Props> = ({ open, onToggle, route, sessions, videos, theme, onCycleTheme, onHelp, profile, onLogout }) => {
  const preparing = videos.filter(isBusy).length
  // Pilot: Phiên mẫu + Kho dữ liệu (xuất ở DEP6); local: kho video/dataset/model + dữ liệu đã xuất (§21.16)
  const nav = [
    ...(PILOT && profile?.operations_read ? [{ href: '#operations', active: route.screen === 'operations', icon: <FileText className="w-4 h-4" />, label: 'Vận hành', extra: 'Chỉ đọc' }] : []),
    ...(PILOT ? [{ href: '#templates', active: route.screen === 'templates', icon: <Sparkles className="w-4 h-4" />, label: 'Phiên mẫu', extra: 'SYSTEM' }] : []),
    { href: '#videos', active: ['videos', 'datasets', 'models'].includes(route.screen), icon: <Film className="w-4 h-4" />, label: 'Kho dữ liệu', extra: preparing ? `${preparing} đang xử lý` : `${videos.length}` },
    ...(PILOT ? [] : [{ href: '#exports', active: route.screen === 'exports', icon: <Archive className="w-4 h-4" />, label: 'Dữ liệu đã xuất', extra: '' }]),
  ]
  const videoName = (id: string) => videos.find(v => v.id === id)?.name ?? id.slice(0, 8)
  const currentSid = route.screen === 'session' ? route.sid : null

  if (!open) {
    return (
      <aside className="w-14 shrink-0 h-full bg-[var(--canvas-subtle)] flex flex-col items-center gap-1 py-3">
        <IconButton label="Mở thanh bên" icon={<ChevronRight className="w-4 h-4" />} onClick={onToggle} />
        {nav.map(n => <a key={n.href} href={n.href} title={n.label} aria-label={n.label}
          className={`p-2 rounded-md ${n.active ? 'bg-[var(--card-bg)] text-[var(--text-primary)]' : 'text-[var(--text-secondary)] hover:bg-[var(--card-bg)]'}`}>{n.icon}</a>)}
        <a href="#new" title="Phiên mới" aria-label="Phiên mới" className="mt-2 p-2 rounded-md text-[var(--kept-border)] hover:bg-[var(--card-bg)]"><Plus className="w-4 h-4" /></a>
        <div className="flex-1" />
        {onLogout && (
          <IconButton label="Đăng xuất" icon={<LogOut className="w-4 h-4 text-red-400" />} onClick={onLogout} />
        )}
        <IconButton label={`Đổi theme (T) — ${theme}`} icon={THEME_ICON[theme]} onClick={onCycleTheme} />
        <IconButton label="Phím tắt (?)" icon={<HelpCircle className="w-4 h-4" />} onClick={onHelp} />
      </aside>
    )
  }

  return (
    <aside className="w-[260px] shrink-0 h-full bg-[var(--canvas-subtle)] flex flex-col">
      <div className="flex items-center justify-between px-4 pt-4 pb-3">
        <a href="#videos" className="text-sm font-bold tracking-tight">DriveLens Studio</a>
        <IconButton label="Thu gọn thanh bên" icon={<ChevronLeft className="w-4 h-4" />} onClick={onToggle} />
      </div>

      <nav className="px-2 space-y-0.5">
        {nav.map(n => (
          <a key={n.href} href={n.href} className={`flex items-center gap-2.5 px-2.5 py-2 rounded-md text-sm ${n.active
            ? 'bg-[var(--card-bg)] font-semibold text-[var(--text-primary)]' : 'text-[var(--text-secondary)] hover:bg-[var(--card-bg)] hover:text-[var(--text-primary)]'}`}>
            {n.icon}<span className="flex-1">{n.label}</span>
            {n.extra && <span className={`text-[11px] font-mono ${preparing && n.href === '#videos' ? 'text-amber-700 dark:text-amber-300' : 'text-[var(--text-muted)]'}`}>{n.extra}</span>}
          </a>
        ))}
      </nav>

      <div className="mt-5 px-4 flex items-center justify-between">
        <Label>Phiên</Label>
        <span className="text-[11px] font-mono text-[var(--text-muted)]">{sessions.length}</span>
      </div>
      <div className="px-2 pt-2">
        <a href="#new" className={`w-full flex items-center gap-2 px-2.5 py-2 rounded-md text-sm font-semibold text-[var(--kept-border)] ${route.screen === 'new' ? 'bg-[var(--card-bg)]' : 'hover:bg-[var(--card-bg)]'}`}>
          <Plus className="w-4 h-4" />Phiên mới
        </a>
      </div>
      <div className="flex-1 overflow-y-auto px-2 pb-2 space-y-0.5">
        {[...sessions].reverse().map(s => (
          <a key={s.id} href={`#s=${s.id}`} title={`Mục tiêu: ${s.goal}`}
            className={`block px-2.5 py-2 rounded-md ${s.id === currentSid ? 'bg-[var(--card-bg)]' : 'hover:bg-[var(--card-bg)]'}`}>
            <div className={`text-[13px] truncate ${s.id === currentSid ? 'font-semibold' : ''}`}>{s.name || s.id.slice(0, 8)}</div>
            <div className="flex items-center gap-1.5 text-[11px] text-[var(--text-muted)]">
              <span className="truncate">{(s.pool.video_ids ?? []).map(videoName).join(', ')}</span>
              <span className="shrink-0 font-mono">· {s.n_kept}/{s.target_k}</span>
            </div>
            <div className="text-[11px] text-[var(--text-muted)]">{fmtDateTime(s.created_at)}</div>
          </a>
        ))}
        {!sessions.length && <p className="px-2.5 py-2 text-xs text-[var(--text-muted)]">Chưa có phiên nào.</p>}
      </div>

      <div className="px-2 pt-2 border-t border-[var(--border-subtle)] space-y-2">
        {PILOT && <QuotaBadge />}

        {profile && (
          <div className="flex items-center justify-between px-2 py-1.5 rounded-lg bg-[var(--surface-muted)] text-xs">
            <div className="flex items-center gap-2 truncate">
              <div className="w-5 h-5 rounded-full bg-[var(--accent)]/20 text-[var(--accent)] flex items-center justify-center shrink-0">
                <User className="w-3 h-3" />
              </div>
              <span className="truncate font-medium text-[var(--text-primary)]" title={profile.display_name}>
                {profile.display_name}
              </span>
            </div>
            {onLogout && (
              <button
                type="button"
                onClick={onLogout}
                title="Đăng xuất"
                className="text-[var(--text-muted)] hover:text-red-400 p-1 rounded transition-colors cursor-pointer"
              >
                <LogOut className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        )}

        <div className="flex items-center justify-between px-1 py-1">
          <div className="flex items-center gap-1">
            <IconButton label={`Đổi theme (T) — ${theme}`} icon={THEME_ICON[theme]} onClick={onCycleTheme} />
            <IconButton label="Phím tắt (?)" icon={<HelpCircle className="w-4 h-4" />} onClick={onHelp} />
          </div>
          <span className="text-[10px] font-mono text-[var(--text-subtle)]">{PILOT ? 'pilot' : 'local'}</span>
        </div>
      </div>
    </aside>
  )
}
