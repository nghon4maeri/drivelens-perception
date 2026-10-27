/** Route bằng hash (UI_DESIGN §2): `#videos` (mặc định), `#exports`, `#new` (+ `&v=id,id`), `#s=<sid>&t=<tab>`. */
export type SessionTab = 'studio' | 'archive' | 'analysis' | 'export'
export type Route =
  | { screen: 'videos' }
  | { screen: 'datasets' }
  | { screen: 'models' }
  | { screen: 'exports' }
  | { screen: 'templates' }
  | { screen: 'operations' }
  | { screen: 'new'; videoIds: string[] }
  | { screen: 'session'; sid: string; tab: SessionTab }

export type ThemeMode = 'warm' | 'dark' | 'paper'

const TABS: SessionTab[] = ['studio', 'archive', 'analysis', 'export']

export function parseHash(hash: string): Route {
  const q = new URLSearchParams(hash.replace(/^#/, ''))
  const sid = q.get('s')
  if (sid) {
    const t = q.get('t') as SessionTab
    return { screen: 'session', sid, tab: TABS.includes(t) ? t : 'studio' }
  }
  if (q.has('new')) return { screen: 'new', videoIds: (q.get('v') ?? '').split(',').filter(Boolean) }
  if (q.has('templates') || q.has('demo')) return { screen: 'templates' }
  if (q.has('operations')) return { screen: 'operations' }
  if (q.has('datasets')) return { screen: 'datasets' }
  if (q.has('models')) return { screen: 'models' }
  return q.has('exports') ? { screen: 'exports' } : { screen: 'videos' }
}

export const sessionHref = (sid: string, tab: SessionTab = 'studio') => `#s=${sid}${tab === 'studio' ? '' : `&t=${tab}`}`
export const newSessionHref = (videoIds: string[] = []) => `#new${videoIds.length ? `&v=${videoIds.join(',')}` : ''}`
