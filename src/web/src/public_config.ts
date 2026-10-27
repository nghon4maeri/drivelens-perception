/** Public runtime settings for prebuilt Docker images; local/static builds retain Vite env. */
type PublicConfig = { apiUrl?: string; supabaseUrl?: string; supabasePublishableKey?: string }

declare global {
  interface Window { __RAV_PUBLIC_CONFIG__?: PublicConfig }
}

export function publicConfig(): Required<PublicConfig> {
  const runtime = typeof window === 'undefined' ? {} : window.__RAV_PUBLIC_CONFIG__ ?? {}
  return {
    apiUrl: (runtime.apiUrl ?? import.meta.env.VITE_API_URL ?? '').trim().replace(/\/+$/, ''),
    supabaseUrl: (runtime.supabaseUrl ?? import.meta.env.VITE_SUPABASE_URL ?? '').trim().replace(/\/+$/, ''),
    supabasePublishableKey: (runtime.supabasePublishableKey ?? import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY
      ?? import.meta.env.VITE_SUPABASE_ANON_KEY ?? '').trim(),
  }
}
