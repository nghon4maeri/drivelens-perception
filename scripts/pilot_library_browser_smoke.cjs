/** Local toolbar/thumbnail regression. Needs the isolated ui-local-stack.py + Vite.
 * Install test-only Playwright under output/basic-stability/ui-tests; use fresh Edge.
 * Input: output/basic-stability/ui-session.json (test JWT only), RAV_TEST_UI_ORIGIN.
 * Output: screenshots/result/log under output/basic-stability/browser-<case>.
 * Run: node scripts/pilot_library_browser_smoke.cjs. Blocks all non-local requests.
 */
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('../output/basic-stability/ui-tests/node_modules/playwright');
const base = path.resolve(__dirname, '../output/basic-stability');
const folder = path.join(base, `browser-${process.env.RAV_TEST_UI_CASE || 'green'}`);
const origin = process.env.RAV_TEST_UI_ORIGIN || 'http://127.0.0.1:3001';
fs.mkdirSync(folder, { recursive: true });

async function main() {
  const session = JSON.parse(fs.readFileSync(path.join(base, 'ui-session.json'), 'utf8'));
  if (!session.test_only || !session.api_base.startsWith('http://127.0.0.1:') || !origin.startsWith('http://127.0.0.1:')) {
    throw new Error('Requires isolated localhost test stack');
  }
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await context.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname)
      ? route.continue() : route.abort());
    const page = await context.newPage();
    page.setDefaultTimeout(60000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin + '/login');
    await page.getByRole('button', { name: 'Access Token (dev)', exact: true }).click();
    await page.locator('textarea').fill(session.token_a);
    await page.getByRole('button', { name: 'Xác thực & Vào App', exact: true }).click();
    await page.getByRole('heading', { name: 'Kho dữ liệu', exact: true }).waitFor();
    await page.getByRole('button', { name: 'Chuẩn bị ảnh', exact: true }).first().click();
    let expected = 0;
    for (let attempt = 0; attempt < 180; attempt++) {
      const response = await context.request.get(session.api_base + '/sources', {
        headers: { Authorization: 'Bearer ' + session.token_a },
      });
      if (!response.ok()) throw new Error(`Source poll HTTP ${response.status()}`);
      const body = await response.json();
      const sources = (body.items || body.sources || body).filter(source => source.kind === 'VIDEO');
      if (sources.length && sources.every(source => source.frames?.ready)) { expected = sources.length; break; }
      await page.waitForTimeout(500);
    }
    if (!expected) throw new Error('Backend frames did not become READY');
    await page.waitForTimeout(6500);
    const loaded = await page.locator('img').evaluateAll(images => images.filter(img => img.complete && img.naturalWidth > 0).length);
    await page.screenshot({ path: path.join(folder, 'after-ready.png'), fullPage: true });
    fs.writeFileSync(path.join(folder, 'result.json'), JSON.stringify({ local_only: true, backend_ready: true,
      expected_thumbnails: expected, loaded_thumbnails: loaded, page_errors: errors }, null, 2));
    if (loaded !== expected) throw new Error(`Backend READY but thumbnails ${loaded}/${expected}; no page reload occurred`);
    if (errors.length) throw new Error(errors.join('; '));
    console.log(`LOCAL_BROWSER_GREEN: thumbnails ${loaded}/${expected} without reload`);
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
