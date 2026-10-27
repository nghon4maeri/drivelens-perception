/**
 * Đăng nhập pilot (CONTRACTS §21.16, UI-export spec §2, #96): session/refresh do `@supabase/supabase-js` quản lý; FE chỉ lấy access token gắn Bearer.
 * - `authGeneration` tăng khi đăng nhập/đăng xuất/hết hạn: request của generation cũ bị hủy (AbortController) và response tới muộn bị bỏ.
 * - Refresh tối đa một lần đồng thời (single-flight); thất bại → `expire()` đăng xuất và báo App.
 * - Dev Access Token chỉ có ở `vite dev` (import.meta.env.DEV), giữ trong sessionStorage — không có ở bản build.
 * Không log token/password. Provider có thể thay bằng bản giả trong test (`setAuthProvider`).
 */
import { createClient } from '@supabase/supabase-js'
import { publicConfig } from './public_config'

export type AuthProvider = {
  getAccessToken(): Promise<string | null>
  refresh(): Promise<boolean>
  signInWithPassword(email: string, password: string): Promise<void>
  signUpWithPassword?(email: string, password: string, displayName?: string): Promise<{ requiresEmailConfirmation: boolean }>
  signOut(): Promise<void>
}

const DEV_KEY = 'rav_pilot_dev_token'
let generation = 0
let provider: AuthProvider | null | undefined
let devToken: string | null = null
let onExpired: ((reason: 'expired' | 'disabled') => void) | null = null
const inflight = new Set<AbortController>()

export const authGeneration = () => generation
export const devTokenAllowed = () => import.meta.env.DEV === true

function session(): Storage | null {
  try { return typeof sessionStorage === 'undefined' ? null : sessionStorage } catch { return null }
}

export function supabaseProvider(): AuthProvider | null {
  const { supabaseUrl: url, supabasePublishableKey: key } = publicConfig()
  if (!url || !key) return null
  const client = createClient(url, key, { auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: typeof window !== 'undefined' && window.location.pathname === '/login' } })
  let refreshing: Promise<boolean> | null = null
  return {
    async getAccessToken() { return (await client.auth.getSession()).data.session?.access_token ?? null },
    refresh() {
      refreshing ??= client.auth.refreshSession().then(r => !r.error && !!r.data.session).catch(() => false).finally(() => { refreshing = null })
      return refreshing
    },
    async signInWithPassword(email, password) {
      const { error } = await client.auth.signInWithPassword({ email, password })
      // không phân biệt tài khoản có tồn tại hay không (UI-export spec §2)
      if (error) throw new Error('Đăng nhập không thành công. Kiểm tra email/mật khẩu hoặc đăng ký tài khoản mới.')
    },
    async signUpWithPassword(email, password, displayName) {
      const cleanName = displayName?.trim()
      const { data, error } = await client.auth.signUp({
        email,
        password,
        options: {
          emailRedirectTo: typeof window !== 'undefined' ? `${window.location.origin}/login` : undefined,
          ...(cleanName ? { data: { display_name: cleanName } } : {}),
        },
      })
      if (error) {
        const msg = (error.message || '').toLowerCase()
        if (msg.includes('already registered') || msg.includes('already exists')) {
          throw new Error('Email này đã được đăng ký. Vui lòng chuyển sang Đăng nhập.')
        }
        if (msg.includes('password')) {
          throw new Error('Mật khẩu chưa đạt yêu cầu (tối thiểu 6 ký tự).')
        }
        throw new Error(error.message || 'Đăng ký không thành công. Vui lòng kiểm tra lại thông tin.')
      }
      return { requiresEmailConfirmation: !data.session }
    },
    async signOut() { await client.auth.signOut().catch(() => undefined) },
  }
}

function getProvider(): AuthProvider | null {
  if (provider === undefined) provider = supabaseProvider()
  return provider
}

/** Chỉ dùng trong test: thay provider Supabase bằng bản giả. */
export function setAuthProvider(p: AuthProvider | null) { provider = p }

export const authConfigured = () => !!getProvider()

function bump() {
  generation++
  for (const c of inflight) c.abort()
  inflight.clear()
}

/** AbortController gắn với generation hiện tại; `done()` khi request xong. */
export function trackRequest(): { signal: AbortSignal; done: () => void } {
  const c = new AbortController()
  inflight.add(c)
  return { signal: c.signal, done: () => { inflight.delete(c) } }
}

export async function accessToken(): Promise<string | null> {
  if (devTokenAllowed()) devToken ??= session()?.getItem(DEV_KEY) ?? null
  if (devToken) return devToken
  return (await getProvider()?.getAccessToken()) ?? null
}

/** Làm mới token một lần; dev token không refresh được. */
export async function refreshOnce(): Promise<boolean> {
  if (devToken) return false
  return (await getProvider()?.refresh()) ?? false
}

export async function signIn(email: string, password: string): Promise<void> {
  const p = getProvider()
  if (!p) throw new Error('Chưa cấu hình đăng nhập (VITE_SUPABASE_URL / VITE_SUPABASE_PUBLISHABLE_KEY).')
  bump()
  await p.signInWithPassword(email.trim(), password)
}

export async function signUp(email: string, password: string, displayName?: string): Promise<{ requiresEmailConfirmation: boolean }> {
  const p = getProvider()
  if (!p || !p.signUpWithPassword) throw new Error('Chưa cấu hình đăng ký (VITE_SUPABASE_URL / VITE_SUPABASE_PUBLISHABLE_KEY).')
  bump()
  return await p.signUpWithPassword(email.trim(), password, displayName)
}

export function signInDevToken(token: string) {
  if (!devTokenAllowed()) throw new Error('Access Token trực tiếp chỉ dùng ở bản dev.')
  const clean = token.trim().replace(/[\r\n\s]/g, '')
  if (!clean) throw new Error('Vui lòng nhập Access Token.')
  bump()
  devToken = clean
  session()?.setItem(DEV_KEY, clean)
}

export async function signOut(): Promise<void> {
  bump()
  devToken = null
  session()?.removeItem(DEV_KEY)
  await getProvider()?.signOut()
}

export function onAuthExpired(cb: ((reason: 'expired' | 'disabled') => void) | null) { onExpired = cb }

/** Token không còn dùng được (refresh thất bại / tài khoản bị khóa): đăng xuất, App xóa state và về màn đăng nhập. */
export async function expire(reason: 'expired' | 'disabled'): Promise<void> {
  await signOut()
  onExpired?.(reason)
}
