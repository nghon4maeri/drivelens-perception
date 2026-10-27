/**
 * Khung app (UI_DESIGN §2; pilot: UI_DESIGN deploy §1–2b, CONTRACTS §21.16): sidebar trái + nội dung theo route hash
 * (`#videos` mặc định, `#exports`, `#new`, `#templates`, `#s=<sid>&t=<tab>`). Giữ dữ liệu dùng chung: danh sách video/nguồn + phiên, theme, phím tắt.
 * - Local (`VITE_RAV_MODE` mặc định): không đăng nhập, `/api/v1`.
 * - Pilot: CHECKING → SIGNED_OUT | SIGNED_IN theo `GET /api/v2/me`; đăng xuất/hết hạn/khóa → tải lại trang (xóa state, cache media, URL trong bộ nhớ),
 *   màn đăng nhập hiện lý do. Màn chỉ có ở pilot: Phiên mẫu, Kho dữ liệu pilot, Phiên mới pilot; dataset/model/xuất ẩn tới DEP5b2/DEP6.
 */
import { useCallback, useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { api, errorText, type SessionDTO, type VideoDTO } from './api'
import { apiV2, StaleResponseError, type ProfileDTO } from './api_v2'
import { accessToken, onAuthExpired, signOut } from './auth'
import { PILOT } from './mode'
import { AppSidebar } from './components/AppSidebar'
import { GlobalUploadIndicator } from './components/GlobalUploadIndicator'
import { KeyboardHelpModal } from './components/KeyboardHelpModal'
import { isBusy, clipBusy } from './components/VideoLibrary'
import { SessionView } from './SessionView'
import { DatasetsScreen } from './screens/DatasetsScreen'
import { ModelsScreen, PilotModelsScreen } from './screens/ModelsScreen'
import { VideosScreen } from './screens/VideosScreen'
import { ExportsScreen } from './screens/ExportsScreen'
import { NewSessionScreen } from './screens/NewSessionScreen'
import { LoginScreen } from './screens/LoginScreen'
import { DemoTemplatesScreen } from './screens/DemoTemplatesScreen'
import { PilotLibraryScreen } from './screens/PilotLibraryScreen'
import { PilotNewSessionScreen } from './screens/PilotNewSessionScreen'
import { OperationsScreen } from './screens/OperationsScreen'
import { useHotkeys } from './hooks/useHotkeys'
import { parseHash, type Route, type ThemeMode } from './types/navigation'
import { ErrorBanner } from './ui'
import { loginHref, nextAppHref } from './site_navigation'
import { onUploadReady, recoverUploads, resetUploadManager } from './upload_manager'

type AuthStatus = 'CHECKING' | 'SIGNED_OUT' | 'SIGNED_IN'
const NOTICE_KEY = 'rav_auth_notice'
const NOTICE: Record<string, string> = { expired: 'Phiên đăng nhập đã hết hạn — vui lòng đăng nhập lại.', disabled: 'Tài khoản đã bị khóa. Liên hệ operator.',
                                         logout: 'Đã đăng xuất.' }

function takeNotice(): string | null {
  try { const n = sessionStorage.getItem(NOTICE_KEY); sessionStorage.removeItem(NOTICE_KEY); return n ? NOTICE[n] ?? null : null } catch { return null }
}

/** Xóa sạch state trong bộ nhớ (catalog, job, media URL) bằng cách tải lại trang (UI-export spec §2). */
function resetApp(reason: 'expired' | 'disabled' | 'logout') {
  resetUploadManager()
  try { sessionStorage.setItem(NOTICE_KEY, reason) } catch { /* bỏ qua */ }
  if (PILOT) location.replace('/login')
  else { location.hash = ''; location.reload() }
}

export default function App({ entry = 'app' }: { entry?: 'login' | 'app' }) {
  const [authStatus, setAuthStatus] = useState<AuthStatus>(PILOT ? 'CHECKING' : 'SIGNED_IN')
  const [notice] = useState(takeNotice)
  const [profile, setProfile] = useState<ProfileDTO | null>(null)
  const [route, setRoute] = useState<Route>(() => parseHash(location.hash))
  const [sessions, setSessions] = useState<SessionDTO[]>([])
  const [videos, setVideos] = useState<VideoDTO[]>([])
  const [error, setError] = useState('')
  const [theme, setTheme] = useState<ThemeMode>('warm')
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [helpOpen, setHelpOpen] = useState(false)

  useEffect(() => {
    if (!PILOT || authStatus === 'CHECKING') return
    if (authStatus === 'SIGNED_OUT' && entry === 'app') location.replace(loginHref(location.hash))
    if (authStatus === 'SIGNED_IN' && entry === 'login') location.replace(nextAppHref(location.search))
  }, [authStatus, entry])

  useEffect(() => {
    if (!PILOT) return
    onAuthExpired(reason => resetApp(reason))
    let live = true
    accessToken().then(async token => {
      if (!token) { if (live) setAuthStatus('SIGNED_OUT'); return }
      const p = await apiV2.me()
      if (!live) return
      if (p.active) { setProfile(p); setAuthStatus('SIGNED_IN') } else { await signOut(); setAuthStatus('SIGNED_OUT') }
    }).catch(async e => {
      if (e instanceof StaleResponseError || !live) return
      await signOut()
      setAuthStatus('SIGNED_OUT')
    })
    return () => { live = false; onAuthExpired(null) }
  }, [])

  const handleLogout = useCallback(async () => { await signOut(); resetApp('logout') }, [])

  const quiet = (e: unknown) => { if (!(e instanceof StaleResponseError)) setError(`Không gọi được backend: ${errorText(e)}`) }
  const load = useCallback(() => api.videos().then(v => { setVideos(v); return api.sessions() })
    .then(s => { setSessions(s); setError('') }).catch(quiet), [])
  const loadVideos = useCallback(() => api.videos().then(setVideos).catch(quiet), [])
  useEffect(() => {
    if (authStatus === 'SIGNED_IN' && (!PILOT || entry === 'app')) load()
  }, [authStatus, entry, load])
  useEffect(() => {
    if (!PILOT || authStatus !== 'SIGNED_IN' || entry !== 'app') return
    void recoverUploads().catch(() => { /* bỏ qua lỗi mạng tạm thời khi khôi phục */ })
    return onUploadReady(load)
  }, [authStatus, entry, load])
  const preparing = videos.some(v => isBusy(v) || clipBusy(v))
  useEffect(() => {
    if (!preparing || authStatus !== 'SIGNED_IN') return
    const t = setInterval(loadVideos, PILOT ? 5000 : 2000)
    return () => clearInterval(t)
  }, [preparing, loadVideos, authStatus])

  useEffect(() => {
    const on = () => setRoute(parseHash(location.hash))
    addEventListener('hashchange', on)
    return () => removeEventListener('hashchange', on)
  }, [])
  useEffect(() => { document.documentElement.setAttribute('data-theme', theme) }, [theme])

  const cycleTheme = () => setTheme(t => (t === 'warm' ? 'dark' : t === 'dark' ? 'paper' : 'warm'))
  useHotkeys(e => {
    if (e.ctrlKey || e.metaKey || e.altKey) return
    if (e.key === 't' || e.key === 'T') { cycleTheme(); return true }
    if (e.key === '?') { setHelpOpen(v => !v); return true }
  }, authStatus === 'SIGNED_IN')

  const onSessionChanged = useCallback((s: SessionDTO) => setSessions(list => list.map(x => (x.id === s.id ? s : x))), [])

  if (authStatus === 'CHECKING' || (PILOT && ((authStatus === 'SIGNED_OUT' && entry === 'app') || (authStatus === 'SIGNED_IN' && entry === 'login')))) {
    return (
      <div className="h-screen w-screen flex flex-col items-center justify-center bg-[var(--canvas-bg)] text-[var(--text-muted)] gap-3">
        <Loader2 className="w-8 h-8 animate-spin text-[var(--accent)]" />
        <span className="text-sm">Đang kiểm tra phiên đăng nhập…</span>
      </div>
    )
  }

  if (authStatus === 'SIGNED_OUT') {
    return <LoginScreen notice={notice} onLoginSuccess={p => { setProfile(p); setAuthStatus('SIGNED_IN') }} />
  }

  const screen = (PILOT && ['datasets', 'exports'].includes(route.screen)) || (!PILOT && route.screen === 'operations') ? 'videos' : route.screen
  return (
    <div className="h-screen w-screen flex overflow-hidden bg-[var(--canvas-bg)] text-[var(--text-primary)]">
      <AppSidebar open={sidebarOpen} onToggle={() => setSidebarOpen(v => !v)} route={route} sessions={sessions} videos={videos}
        theme={theme} onCycleTheme={cycleTheme} onHelp={() => setHelpOpen(true)} profile={profile} onLogout={PILOT ? handleLogout : undefined} />
      <main className="flex-1 min-w-0 flex flex-col overflow-hidden">
        {error && <div className="px-6 pt-3"><ErrorBanner message={error} onClose={() => setError('')} /></div>}
        {PILOT && screen === 'operations' && (profile?.operations_read ? <OperationsScreen /> : <ErrorBanner message="Tài khoản không có quyền xem vận hành." />)}
        {PILOT && screen === 'templates' && <DemoTemplatesScreen onForked={load} />}
        {PILOT && screen === 'videos' && <PilotLibraryScreen onChanged={load} hasSessions={sessions.length > 0} canUploadModel={!!profile?.models_write} />}
        {PILOT && screen === 'new' && route.screen === 'new' && <PilotNewSessionScreen preselect={route.videoIds} onCreated={load} />}
        {!PILOT && screen === 'datasets' && <DatasetsScreen />}
        {!PILOT && screen === 'models' && <ModelsScreen />}
        {PILOT && screen === 'models' && <PilotModelsScreen canUpload={!!profile?.models_write} />}
        {!PILOT && screen === 'videos' && <VideosScreen videos={videos} sessions={sessions} onVideosChanged={loadVideos} onSessionChanged={onSessionChanged} />}
        {!PILOT && screen === 'new' && route.screen === 'new' && <NewSessionScreen preselect={route.videoIds} videos={videos} onVideosChanged={loadVideos}
          onCreated={s => setSessions(l => [...l.filter(x => x.id !== s.id), s])} />}
        {!PILOT && screen === 'exports' && <ExportsScreen sessions={sessions} />}
        {route.screen === 'session' && (
          <SessionView key={route.sid} sid={route.sid} tab={route.tab} videos={videos} keysDisabled={helpOpen}
            onVideosChanged={loadVideos} onSessionChanged={onSessionChanged} />
        )}
      </main>

      {PILOT && <GlobalUploadIndicator />}
      <KeyboardHelpModal open={helpOpen} onClose={() => setHelpOpen(false)} />
    </div>
  )
}
