/** §21.41: login shows the approved public demo account fill button; it is not a submit button. */
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { LoginScreen } from './screens/LoginScreen'

describe('shared demo login', () => {
  it('renders a non-submit fill button with the public demo credentials', () => {
    const html = renderToStaticMarkup(<LoginScreen onLoginSuccess={() => {}} />)
    expect(html).toMatch(/<button type="button"[^>]*>Nạp tài khoản demo<\/button>/)
    expect(html).toContain('demo123@aiia.com')
    expect(html).toContain('dùng chung')
  })
})
