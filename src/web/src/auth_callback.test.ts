import { afterEach, expect, it, vi } from 'vitest'

const sdk = vi.hoisted(() => ({
  signup: vi.fn(async () => ({ data: { session: null }, error: null })),
  create: vi.fn(),
}))
vi.mock('@supabase/supabase-js', () => ({
  createClient: sdk.create.mockImplementation(() => ({ auth: { signUp: sdk.signup } })),
}))
vi.mock('./public_config', () => ({
  publicConfig: () => ({ supabaseUrl: 'https://test.supabase.co', supabasePublishableKey: 'public-test-key' }),
}))
import { supabaseProvider } from './auth'

afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks() })

it('consumes confirmation callbacks only at login and explicitly redirects signup to the same origin', async () => {
  vi.stubGlobal('window', { location: new URL('https://c4-app-026.onrender.com/login') })
  const provider = supabaseProvider()!
  expect(sdk.create.mock.calls.at(-1)?.[2].auth.detectSessionInUrl).toBe(true)
  await provider.signUpWithPassword!('qa@example.invalid', 'test-password', 'QA')
  expect(sdk.signup).toHaveBeenCalledWith({
    email: 'qa@example.invalid', password: 'test-password',
    options: { emailRedirectTo: 'https://c4-app-026.onrender.com/login', data: { display_name: 'QA' } },
  })
})

it('does not interpret Studio navigation hashes as authentication callbacks', () => {
  vi.stubGlobal('window', { location: new URL('https://c4-app-026.onrender.com/app#s=session') })
  supabaseProvider()
  expect(sdk.create.mock.calls.at(-1)?.[2].auth.detectSessionInUrl).toBe(false)
})
