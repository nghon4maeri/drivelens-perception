/**
 * Đăng nhập & Đăng ký pilot (UI_DESIGN deploy §2, CONTRACTS §21.16, §21.34): email/mật khẩu qua Supabase SDK (`auth.ts`).
 * - Hỗ trợ chuyển đổi Đăng nhập / Đăng ký (email, mật khẩu ≥ 6 ký tự, tên hiển thị tùy chọn).
 * - Hiển thị rõ trạng thái tạo tài khoản, lỗi hoặc yêu cầu xác nhận email theo cấu hình auth thực tế.
 * - Chỉ vào app khi `GET /api/v2/me` hợp lệ và active (backend tự động bootstrap hồ sơ + storage account trong lần xác thực đầu tiên).
 * - Tab Access Token chỉ có ở `vite dev` (smoke/dev), không có ở bản build.
 */
import { useState } from 'react'
import { apiV2, type ProfileDTO } from '../api_v2'
import { devTokenAllowed, signIn, signInDevToken, signOut, signUp } from '../auth'
import { KeyRound, Mail, Lock, User, ArrowRight, Loader2, AlertCircle, CheckCircle2, ShieldAlert, Cpu } from 'lucide-react'

interface LoginScreenProps {
  onLoginSuccess: (profile: ProfileDTO) => void
  notice?: string | null
}

export function LoginScreen({ onLoginSuccess, notice }: LoginScreenProps) {
  const [mode, setMode] = useState<'password' | 'token'>('password')
  const [authAction, setAuthAction] = useState<'signin' | 'signup'>('signin')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [devToken, setDevToken] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(notice ?? null)
  const [info, setInfo] = useState<string | null>(null)

  const finish = async () => {
    const profile = await apiV2.me()
    if (!profile.active) throw new Error('Tài khoản đã bị khóa. Liên hệ operator.')
    onLoginSuccess(profile)
  }

  const handleDevTokenLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      setLoading(true); setError(null); setInfo(null)
      signInDevToken(devToken)
      await finish()
    } catch (err) {
      await signOut()
      setError(err instanceof Error ? err.message : 'Token không hợp lệ hoặc đã hết hạn.')
    } finally { setLoading(false) }
  }

  const handlePasswordSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!email.trim() || !password) { setError('Vui lòng điền email và mật khẩu.'); return }
    if (authAction === 'signup' && password.length < 6) {
      setError('Mật khẩu cần tối thiểu 6 ký tự.')
      return
    }
    try {
      setLoading(true); setError(null); setInfo(null)
      if (authAction === 'signup') {
        const res = await signUp(email, password, displayName)
        if (res.requiresEmailConfirmation) {
          setInfo('Tài khoản đã được tạo! Vui lòng kiểm tra hộp thư email để xác nhận trước khi đăng nhập.')
          setAuthAction('signin')
          setPassword('')
          return
        }
      } else {
        await signIn(email, password)
      }
      await finish()
    } catch (err) {
      await signOut()
      setError(err instanceof Error ? err.message : (authAction === 'signup' ? 'Đăng ký không thành công.' : 'Đăng nhập không thành công.'))
    } finally { setLoading(false) }
  }

  return (
    <div className="min-h-screen w-screen flex flex-col items-center justify-center p-6 bg-[var(--canvas-bg)] text-[var(--text-primary)]">
      <div className="w-full max-w-md space-y-6">
        <a href="/" className="block text-center text-sm text-[var(--accent)] hover:underline">← IrishCoffee · T026 — Trang giới thiệu</a>
        {/* Logo & Header */}
        <div className="text-center space-y-2">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-emerald-600/10 text-emerald-700 dark:text-emerald-400 border border-emerald-600/20 shadow-sm mb-1">
            <Cpu className="w-7 h-7" />
          </div>
          <h1 className="text-2xl sm:text-3xl font-bold tracking-tight text-[var(--text-primary)]">
            DriveLens Studio
          </h1>
          <p className="text-xs sm:text-sm text-[var(--text-secondary)]">
            Hệ thống chọn lọc khung hình video thông minh cho ADAS &amp; Robotics
          </p>
        </div>

        {/* Card Form */}
        <div className="rounded-2xl border border-[var(--border-color)] bg-[var(--card-bg)] p-6 sm:p-7 shadow-xl space-y-5">
          {/* Segmented Navigation Tab — Access Token chỉ ở bản dev */}
          {devTokenAllowed() && <div className="flex p-1 bg-[var(--canvas-subtle)] rounded-xl gap-1 text-xs font-semibold">
            <button
              type="button"
              onClick={() => {
                setMode('password')
                setError(null)
              }}
              className={`flex-1 py-2 px-3 rounded-lg transition-all cursor-pointer text-center ${
                mode === 'password'
                  ? 'bg-[var(--card-bg)] text-[var(--text-primary)] shadow-sm'
                  : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
              }`}
            >
              Tài khoản Pilot
            </button>
            <button
              type="button"
              onClick={() => {
                setMode('token')
                setError(null)
              }}
              className={`flex-1 py-2 px-3 rounded-lg transition-all cursor-pointer text-center ${
                mode === 'token'
                  ? 'bg-[var(--card-bg)] text-[var(--text-primary)] shadow-sm'
                  : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
              }`}
            >
              Access Token (dev)
            </button>
          </div>}

          {/* Chuyển đổi Đăng nhập / Đăng ký */}
          {(mode === 'password' || !devTokenAllowed()) && (
            <div className="flex p-1 bg-[var(--canvas-subtle)] rounded-xl gap-1 text-xs font-semibold" role="tablist" aria-label="Chế độ xác thực">
              <button
                type="button"
                role="tab"
                aria-selected={authAction === 'signin'}
                onClick={() => { setAuthAction('signin'); setError(null) }}
                className={`flex-1 py-2 px-3 rounded-lg transition-all cursor-pointer text-center ${
                  authAction === 'signin'
                    ? 'bg-[var(--card-bg)] text-[var(--text-primary)] shadow-sm'
                    : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
              >
                Đăng nhập
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={authAction === 'signup'}
                onClick={() => { setAuthAction('signup'); setError(null); setInfo(null) }}
                className={`flex-1 py-2 px-3 rounded-lg transition-all cursor-pointer text-center ${
                  authAction === 'signup'
                    ? 'bg-[var(--card-bg)] text-[var(--text-primary)] shadow-sm'
                    : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
              >
                Đăng ký tài khoản
              </button>
            </div>
          )}

          <div className="rounded-xl border border-emerald-600/25 bg-emerald-600/5 p-4 space-y-2.5">
            <div className="text-sm font-semibold">Trải nghiệm nhanh</div>
            <p className="text-xs text-[var(--text-secondary)] leading-relaxed">
              Dùng dữ liệu mẫu với tài khoản demo. Phiên và hạn mức được dùng chung; không tải dữ liệu riêng tư lên tài khoản này.
            </p>
            <button
              type="button"
              disabled={loading}
              onClick={() => {
                setMode('password'); setAuthAction('signin')
                setEmail('demo123@aiia.com'); setPassword('demo@123')
                setDevToken(''); setError(null); setInfo(null)
              }}
              className="w-full rounded-lg border border-emerald-700 bg-[var(--card-bg)] px-4 py-2.5 text-sm font-semibold text-emerald-700 hover:bg-emerald-600/10 disabled:opacity-50 cursor-pointer transition-colors"
            >
              Nạp tài khoản demo
            </button>
            <p className="text-[11px] text-[var(--text-muted)]">demo123@aiia.com · Mật khẩu: demo@123 · Nạp rồi bấm Đăng nhập.</p>
          </div>

          {/* Info Message (Email Confirmation) */}
          {info && (
            <div className="p-3.5 rounded-xl border border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 text-xs flex items-start gap-2.5">
              <CheckCircle2 className="w-4 h-4 flex-shrink-0 mt-0.5" />
              <div className="leading-relaxed font-medium">{info}</div>
            </div>
          )}

          {/* Error Message */}
          {error && (
            <div className="p-3.5 rounded-xl border border-red-500/25 bg-red-500/10 text-red-600 dark:text-red-400 text-xs flex items-start gap-2.5">
              <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
              <div className="leading-relaxed font-medium">{error}</div>
            </div>
          )}

          {/* Forms */}
          {mode === 'password' || !devTokenAllowed() ? (
            <form onSubmit={handlePasswordSubmit} className="space-y-4">
              {authAction === 'signup' && (
                <div className="space-y-1.5">
                  <label htmlFor="login-display-name" className="text-xs font-semibold text-[var(--text-primary)] flex items-center gap-1.5">
                    <User className="w-3.5 h-3.5 text-[var(--text-muted)]" />
                    Tên hiển thị <span className="text-[var(--text-muted)] font-normal">(tùy chọn)</span>
                  </label>
                  <input
                    id="login-display-name"
                    aria-label="Tên hiển thị"
                    autoComplete="name"
                    type="text"
                    maxLength={80}
                    placeholder="Nguyễn Văn A"
                    value={displayName}
                    onChange={e => setDisplayName(e.target.value)}
                    className="w-full px-3.5 py-2.5 rounded-xl border border-[var(--border-color)] bg-[var(--canvas-bg)] text-[var(--text-primary)] placeholder-[var(--text-muted)] focus:outline-none focus:ring-2 focus:ring-emerald-600/30 focus:border-emerald-600 text-sm transition-all"
                  />
                </div>
              )}

              <div className="space-y-1.5">
                <label htmlFor="login-email" className="text-xs font-semibold text-[var(--text-primary)] flex items-center gap-1.5">
                  <Mail className="w-3.5 h-3.5 text-[var(--text-muted)]" />
                  Email tài khoản
                </label>
                <input
                  id="login-email"
                  aria-label="Email tài khoản"
                  autoComplete="username"
                  type="email"
                  required
                  placeholder="name@organization.com"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  className="w-full px-3.5 py-2.5 rounded-xl border border-[var(--border-color)] bg-[var(--canvas-bg)] text-[var(--text-primary)] placeholder-[var(--text-muted)] focus:outline-none focus:ring-2 focus:ring-emerald-600/30 focus:border-emerald-600 text-sm transition-all"
                />
              </div>

              <div className="space-y-1.5">
                <label htmlFor="login-password" className="text-xs font-semibold text-[var(--text-primary)] flex items-center gap-1.5">
                  <Lock className="w-3.5 h-3.5 text-[var(--text-muted)]" />
                  Mật khẩu
                </label>
                <input
                  id="login-password"
                  aria-label="Mật khẩu"
                  autoComplete={authAction === 'signup' ? 'new-password' : 'current-password'}
                  type="password"
                  required
                  minLength={authAction === 'signup' ? 6 : undefined}
                  placeholder={authAction === 'signup' ? 'Tối thiểu 6 ký tự' : '••••••••'}
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  className="w-full px-3.5 py-2.5 rounded-xl border border-[var(--border-color)] bg-[var(--canvas-bg)] text-[var(--text-primary)] placeholder-[var(--text-muted)] focus:outline-none focus:ring-2 focus:ring-emerald-600/30 focus:border-emerald-600 text-sm transition-all"
                />
              </div>

              <button
                type="submit"
                disabled={loading}
                className="w-full mt-2 py-3 px-4 rounded-xl bg-emerald-700 hover:bg-emerald-800 text-white font-semibold text-sm shadow-md transition-all cursor-pointer flex items-center justify-center gap-2 disabled:opacity-50"
              >
                {loading ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    <span>{authAction === 'signup' ? 'Đang tạo tài khoản…' : 'Đang đăng nhập…'}</span>
                  </>
                ) : (
                  <>
                    <span>{authAction === 'signup' ? 'Tạo tài khoản & Bắt đầu' : 'Đăng nhập'}</span>
                    <ArrowRight className="w-4 h-4" />
                  </>
                )}
              </button>
            </form>
          ) : (
            <form onSubmit={handleDevTokenLogin} className="space-y-4">
              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <label className="text-xs font-semibold text-[var(--text-primary)] flex items-center gap-1.5">
                    <KeyRound className="w-3.5 h-3.5 text-[var(--text-muted)]" />
                    Access Token (JWT Bearer)
                  </label>
                </div>
                <textarea
                  rows={4}
                  required
                  placeholder="eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9..."
                  value={devToken}
                  onChange={e => setDevToken(e.target.value)}
                  className="w-full p-3 rounded-xl border border-[var(--border-color)] bg-[var(--canvas-bg)] text-[var(--text-primary)] font-mono text-xs placeholder-[var(--text-muted)] focus:outline-none focus:ring-2 focus:ring-emerald-600/30 focus:border-emerald-600 resize-none transition-all"
                />
                <p className="text-[11px] text-[var(--text-muted)] leading-normal">
                  Nhập chuỗi token xác thực do quản trị viên cấp để truy cập phiên làm việc pilot.
                </p>
              </div>

              <button
                type="submit"
                disabled={loading}
                className="w-full mt-2 py-3 px-4 rounded-xl bg-emerald-700 hover:bg-emerald-800 text-white font-semibold text-sm shadow-md transition-all cursor-pointer flex items-center justify-center gap-2 disabled:opacity-50"
              >
                {loading ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <>
                    <span>Xác thực &amp; Vào App</span>
                    <ArrowRight className="w-4 h-4" />
                  </>
                )}
              </button>
            </form>
          )}

          {/* Footer note */}
          <div className="pt-2 border-t border-[var(--border-color)] text-center">
            <div className="text-[11px] text-[var(--text-muted)] flex items-center justify-center gap-1.5">
              <ShieldAlert className="w-3.5 h-3.5 text-amber-600 dark:text-amber-400" />
              <span>Tài khoản mới được tự động gắn hạn mức dùng thử và có thể trải nghiệm ngay dữ liệu mẫu.</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

