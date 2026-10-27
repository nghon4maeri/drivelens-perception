import { afterEach, expect, it, vi } from 'vitest'
import { publicConfig } from './public_config'

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs() })

it('Docker runtime values override build settings and expose only public configuration', () => {
  vi.stubEnv('VITE_API_URL', 'https://build-api.example')
  vi.stubGlobal('window', { __RAV_PUBLIC_CONFIG__: {
    apiUrl: ' https://runtime-api.example/ ', supabaseUrl: ' https://auth.example/ ',
    supabasePublishableKey: ' public-key ', DATABASE_URL: 'never-export-this',
  } })
  expect(publicConfig()).toEqual({ apiUrl: 'https://runtime-api.example', supabaseUrl: 'https://auth.example',
    supabasePublishableKey: 'public-key' })
})

it('local/static builds retain Vite settings and the anon key alias', () => {
  vi.stubGlobal('window', { __RAV_PUBLIC_CONFIG__: {} })
  vi.stubEnv('VITE_API_URL', 'https://local.example/')
  vi.stubEnv('VITE_SUPABASE_URL', 'https://auth.example')
  vi.stubEnv('VITE_SUPABASE_PUBLISHABLE_KEY', undefined)
  vi.stubEnv('VITE_SUPABASE_ANON_KEY', 'alias-public-key')
  expect(publicConfig()).toEqual({ apiUrl: 'https://local.example', supabaseUrl: 'https://auth.example',
    supabasePublishableKey: 'alias-public-key' })
})
