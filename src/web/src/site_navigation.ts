/** Entry pilot public/login/app; input là URL trình duyệt, output chỉ đường nội bộ an toàn. */
export function sitePage(path: string): 'landing' | 'login' | 'app' | 'legacy' | 'missing' {
  const clean = path.replace(/\/$/, '') || '/'
  if (clean === '/' || clean === '/landing') return 'landing'
  if (clean === '/login') return 'login'
  if (clean === '/app') return 'app'
  if (clean === '/IrishCoffee-T026') return 'legacy'
  return 'missing'
}

export function loginHref(hash = ''): string {
  return `/login?next=${encodeURIComponent(`/app${hash}`)}`
}

export function nextAppHref(search: string): string {
  const next = new URLSearchParams(search).get('next')
  if (!next || /[\\\u0000-\u0020]/.test(next)) return '/app'
  try {
    const url = new URL(next, 'https://rav.invalid')
    if (!next.startsWith('/') || url.origin !== 'https://rav.invalid' || url.pathname !== '/app') return '/app'
    return `${url.pathname}${url.search}${url.hash}`
  } catch { return '/app' }
}
