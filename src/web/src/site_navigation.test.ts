/** Kiểm route public và redirect auth, đặc biệt URL ngoài origin; chạy npm test. */
import { describe, expect, it } from 'vitest'
import { loginHref, nextAppHref, sitePage } from './site_navigation'

describe('pilot site entry', () => {
  it('phân biệt landing, login, app, alias cũ và đường không tồn tại', () => {
    expect(['/', '/landing', '/login/', '/app', '/IrishCoffee-T026', '/other'].map(sitePage))
      .toEqual(['landing', 'landing', 'login', 'app', 'legacy', 'missing'])
  })
  it('giữ Studio hash qua login và dùng app mặc định khi không có next', () => {
    const href = loginHref('#s=session-1&t=analysis')
    expect(nextAppHref(href.slice(href.indexOf('?')))).toBe('/app#s=session-1&t=analysis')
    expect(nextAppHref('')).toBe('/app')
  })
  it('không cho redirect ra ngoài origin hoặc tới path không phải app', () => {
    for (const next of ['https://evil.test/app', '//evil.test/app', '/\\evil.test/app', '/login', '/app/../login', '/app\n', 'javascript:alert(1)']) {
      expect(nextAppHref(`?next=${encodeURIComponent(next)}`)).toBe('/app')
    }
  })
})
