/**
 * Gate FE-AUTH / FE-JOB / FE-TEMPLATE (UI-export spec §9, CONTRACTS §21.16): client v2 + adapter pilot với HTTP giả và request log.
 * Không DOM, không mạng thật. Chạy: npm test (thư mục src/web).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './api_error'
import { apiV2, StaleResponseError, v2, type ProposalV2 } from './api_v2'
import { onAuthExpired, setAuthProvider, signIn, signOut, signUp, type AuthProvider } from './auth'
import { _test as mediaTest, clearMediaCache, MediaCancelledError, prepareFrames, resolveMedia } from './media'
import { operations, operationTrace, operationLogs, sendUploadTimings } from './operations'
import { parseHash } from './types/navigation'
import { _test, jobOf, mediaRef, parseMediaRef, pilotApi, pilotUnit, proposalOf, rememberSource } from './pilot_api'

type Req = { method: string; path: string; auth?: string; key?: string; body?: unknown }

describe('proposal evidence compatibility', () => {
  const rows = [{ kind: 'detections', label: 'Original', boxes: [] }]
  const summary = { signaled_count: 1, total_candidates: 32, note: 'proxy' }
  it.each([
    [{ explain: {}, scores: {}, relaxed: false, interactive_basis: {} }, {}],
    [{ units: null, explain: {} }, {}],
    [{ units: {}, explain: {} }, {}],
    [{ 'core:0-0': rows }, { 'core:0-0': rows }],
    [{ units: { 'core:0-0': rows }, uncertainty_summary: summary }, { 'core:0-0': rows }],
  ])('only exposes per-pick arrays for %j', (evidence, expected) => {
    const p = proposalOf({ n: 1, picks: [{ unit_id: 'core:0-0', source_id: 'source' }], evidence } as unknown as ProposalV2)
    expect(p.evidence).toEqual(expected)
    // Same traversal as Studio's detection toggle: opening historical batches must not crash.
    expect(Object.values(p.evidence ?? {}).some(es => es.some(e => e.kind === 'detections'))).toBe(Object.keys(expected).length > 0)
  })
})
type Reply = { status: number; json?: unknown } | Promise<{ status: number; json?: unknown }>
let log: Req[] = []
let handler: (r: Req) => Reply

function installFetch() {
  globalThis.fetch = (async (input: string, init: RequestInit = {}) => {
    const headers = (init.headers ?? {}) as Record<string, string>
    const req: Req = { method: init.method ?? 'GET', path: String(input), auth: headers.Authorization, key: headers['Idempotency-Key'],
                       body: init.body ? JSON.parse(String(init.body)) : undefined }
    log.push(req)
    const aborted = new Promise<never>((_, reject) => init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))))
    const r = await Promise.race([Promise.resolve(handler(req)), aborted])
    return new Response(r.json === undefined ? null : JSON.stringify(r.json), { status: r.status })
  }) as typeof fetch
}

function fakeAuth(tokens: string[], refreshOk = true): AuthProvider & { refreshes: number } {
  let i = 0
  const p = {
    refreshes: 0,
    async getAccessToken() { return tokens[Math.min(i, tokens.length - 1)] },
    async refresh() { p.refreshes++; if (refreshOk) i++; return refreshOk },
    async signInWithPassword() {},
    async signOut() {},
  }
  return p
}

const err = (status: number, code: string, extra: Record<string, unknown> = {}) => ({ status, json: { error: { code, message: code, ...extra } } })
const job = (state: string, extra: Record<string, unknown> = {}) => ({ id: 'j1', kind: 'PROPOSE', state, state_version: 3, phase: 'working',
  progress: { done: 0, total: 0 }, attempt_n: 1, waiting_reason: null, error: null, resource_id: 's1', result_ref: null, ...extra })

beforeEach(() => { log = []; installFetch(); setAuthProvider(fakeAuth(['A'])); onAuthExpired(null); clearMediaCache(); _test.stateVersion.clear() })
afterEach(() => { onAuthExpired(null) })

describe('DEP6a: xóa nguồn', () => {
  it('theo dõi receipt chỉ GET; retry dùng CAS mới và key mới', async () => {
    handler = r => ({ status: r.method === 'POST' ? 202 : 200,
      json: { id: 'delete1', source_id: 'source1', state: r.method === 'POST' ? 'REQUESTED' : 'FAILED', job_id: 'job1', error: null } })
    const receipt = await apiV2.deleteSource('source1', 1)
    await apiV2.deleteReceipt(receipt.id)
    await apiV2.deleteReceipt(receipt.id)
    await apiV2.deleteSource('source1', 2)
    expect(log.map(r => r.method)).toEqual(['POST', 'GET', 'GET', 'POST'])
    expect(log[0].body).toEqual({ expected_resource_version: 1 })
    expect(log[3].body).toEqual({ expected_resource_version: 2 })
    expect(log[0].key).toBeTruthy()
    expect(log[3].key).not.toEqual(log[0].key)
  })
})

describe('FE-AUTH', () => {
  it('operations chỉ GET; telemetry không gửi quyền hoặc owner từ client', async () => {
    handler = () => ({ status: 200, json: { accepted: 1 } })
    expect(parseHash('#operations')).toEqual({ screen: 'operations' })
    await operations()
    await operationTrace('job', '11111111-1111-1111-1111-111111111111')
    await sendUploadTimings('22222222-2222-2222-2222-222222222222', [{ id: '33333333-3333-3333-3333-333333333333',
      at: new Date().toISOString(), stage: 'browser.complete', wall_ms: 100, bytes: 80, outcome: 'ok' }])
    expect(log.slice(-3).map(r => r.method)).toEqual(['GET', 'GET', 'POST'])
    expect(JSON.stringify(log.at(-1)?.body)).not.toMatch(/owner_id|admin|job_id|request_id/)
  })

  it('operations 403 không đăng xuất hoặc retry nâng quyền', async () => {
    handler = () => ({ status: 403, json: { error: { code: 'forbidden', message: 'không có quyền' } } })
    await expect(operations()).rejects.toMatchObject({ status: 403, code: 'forbidden' })
    expect(log).toHaveLength(1)
  })

  it('log admin chỉ đọc một trang với filters và cursor được encode', async () => {
    handler = () => ({ status: 200, json: { items: [], next_cursor: 'next' } })
    await operationLogs({ hours: 6, source: 'worker', errorsOnly: true, cursor: 'a+/=' })
    expect(log).toHaveLength(1)
    expect(log[0].method).toBe('GET')
    const q = new URL(log[0].path, 'http://test').searchParams
    expect(Object.fromEntries(q)).toEqual({ hours: '6', source: 'worker', errors_only: 'true', limit: '50', cursor: 'a+/=' })
    expect(log[0].body).toBeUndefined()
  })
  it('bỏ response của phiên đăng nhập cũ khi đăng xuất giữa chừng', async () => {
    let release!: () => void
    handler = () => new Promise(r => { release = () => r({ status: 200, json: { user_id: 'A' } }) })
    const pending = v2('/api/v2/me')
    await new Promise(r => setTimeout(r, 0))
    await signOut()                       // hủy request + tăng generation
    release()
    await expect(pending).rejects.toBeInstanceOf(StaleResponseError)
  })

  it('401 → refresh đúng một lần rồi gửi lại với token mới', async () => {
    const auth = fakeAuth(['old', 'new'])
    setAuthProvider(auth)
    handler = r => r.auth === 'Bearer old' ? err(401, 'unauthorized') : { status: 200, json: { ok: true } }
    await expect(v2('/api/v2/me')).resolves.toEqual({ ok: true })
    expect(auth.refreshes).toBe(1)
    expect(log.map(r => r.auth)).toEqual(['Bearer old', 'Bearer new'])
  })

  it('refresh thất bại hoặc tài khoản bị khóa → đăng xuất, không lặp vô hạn', async () => {
    const reasons: string[] = []
    onAuthExpired(r => reasons.push(r))
    const auth = fakeAuth(['t'], false)
    setAuthProvider(auth)
    handler = () => err(401, 'unauthorized')
    await expect(v2('/api/v2/me')).rejects.toMatchObject({ status: 401 })
    expect(log).toHaveLength(1)
    handler = () => err(401, 'account_disabled')
    await expect(v2('/api/v2/me')).rejects.toMatchObject({ code: 'account_disabled' })
    expect(reasons).toEqual(['expired', 'disabled'])
    expect(auth.refreshes).toBe(1)                  // khóa tài khoản không thử refresh
  })

  it('write không idempotent/CAS không gửi lại sau refresh', async () => {
    setAuthProvider(fakeAuth(['old', 'new']))
    handler = r => r.auth === 'Bearer old' ? err(401, 'unauthorized') : { status: 202, json: {} }
    await expect(apiV2.completeUpload('u1')).rejects.toMatchObject({ status: 401 })
    expect(log.filter(r => r.method === 'POST')).toHaveLength(1)
  })

  it('refresh cũ thất bại sau khi đổi tài khoản không đăng xuất phiên mới', async () => {
    let release!: (ok: boolean) => void
    const auth = fakeAuth(['old'])
    auth.refresh = () => new Promise<boolean>(resolve => { release = resolve })
    setAuthProvider(auth)
    const reasons: string[] = []
    onAuthExpired(reason => reasons.push(reason))
    handler = () => err(401, 'unauthorized')
    const pending = v2('/api/v2/me')
    await new Promise(resolve => setTimeout(resolve, 0))
    setAuthProvider(fakeAuth(['new']))
    await signIn('new@example.com', 'test')
    release(false)
    await expect(pending).rejects.toBeInstanceOf(StaleResponseError)
    expect(reasons).toEqual([])
  })

  it('signUp chuẩn hóa email, truyền displayName tùy chọn và tăng authGeneration', async () => {
    const calls: { email: string; password: string; displayName?: string }[] = []
    const auth = {
      ...fakeAuth(['signup-token']),
      async signUpWithPassword(email: string, password: string, displayName?: string) {
        calls.push({ email, password, displayName })
        return { requiresEmailConfirmation: true }
      },
    }
    setAuthProvider(auth)
    const res = await signUp('  alice@example.com  ', 'secret123', 'Alice Nguyễn')
    expect(res).toEqual({ requiresEmailConfirmation: true })
    expect(calls).toEqual([{ email: 'alice@example.com', password: 'secret123', displayName: 'Alice Nguyễn' }])
  })
})

describe('FE-JOB', () => {
  it('poll đề xuất chỉ GET, map trạng thái đúng, không POST lại', async () => {
    const states = ['QUEUED', 'WAITING_RESOURCE', 'RUNNING', 'CANCEL_REQUESTED', 'SUCCEEDED']
    for (const st of states) {
      log = []
      handler = r => r.path.endsWith('/result') ? { status: 200, json: { kind: 'PROPOSE', session_id: 's1', proposal_n: 2 } }
        : { status: 200, json: { items: [job(st, { waiting_reason: st === 'WAITING_RESOURCE' ? 'gpu_budget_exhausted' : null }), job('SUCCEEDED', { id: 'j0' })], total: 2, next_cursor: null } }
      const list = await pilotApi.proposalJobs('s1')
      expect(log.every(r => r.method === 'GET')).toBe(true)
      expect(list.at(-1)!.id).toBe('j1')            // API trả mới → cũ; UI nhận cũ → mới
    }
    expect(jobOf(job('WAITING_RESOURCE', { waiting_reason: 'gpu_budget_exhausted' }) as never, 's1')).toMatchObject({ state: 'queued', stage: 'chờ tài nguyên: hết ngân sách GPU chung' })
    expect(jobOf(job('CANCEL_REQUESTED') as never, 's1')).toMatchObject({ state: 'running', stage: 'đang chờ tiến trình dừng an toàn' })
    expect(jobOf(job('RUNNING') as never, 's1')).toMatchObject({ state: 'running', stage: 'đang tính đề xuất' })
    expect(jobOf(job('CANCELLED') as never, 's1')).toMatchObject({ state: 'error', error: 'Đã hủy đề xuất.', retryable: true })
    const failed = jobOf(job('FAILED', { error: { code: 'proposal_context_changed', message: 'đổi', retryable: false } }) as never, 's1')
    expect(failed).toMatchObject({ state: 'error', retryable: true })
  })

  it('xếp đề xuất gửi Idempotency-Key + expected_state_version; mất kết nối gửi lại cùng key', async () => {
    let fail = true
    handler = r => {
      if (r.path.endsWith('/state')) return { status: 200, json: { session_id: 's1', state_version: 5, decision_seq: 2, kept_ids: [], dropped_ids: [] } }
      if (fail) { fail = false; return Promise.reject(new TypeError('network')) as never }
      return { status: 202, json: job('QUEUED') }
    }
    const req = { recipe: 'context_uncertainty', batch_size: 8, seed: 1, strategy_params: { texts: ['night'], candidate_multiplier: 3 }, model_id: 'chosen-model' }
    await expect(pilotApi.queueProposal('s1', req)).rejects.toMatchObject({ code: 'network_error' })
    const ok = await pilotApi.queueProposal('s1', req)
    const posts = log.filter(r => r.method === 'POST')
    expect(posts).toHaveLength(2)
    expect(posts[0].key).toBeTruthy()
    expect(posts[1].key).toBe(posts[0].key)
    expect(posts[1].body).toMatchObject({ recipe: 'context_uncertainty', expected_state_version: 5, strategy_params: req.strategy_params, model_id: 'chosen-model' })
    expect(ok.state).toBe('queued')
  })

  it('409 state_version_conflict → tải state mới, báo lỗi rõ, lần sau dùng version mới', async () => {
    let version = 7
    handler = r => {
      if (r.path.endsWith('/state')) return { status: 200, json: { session_id: 's1', state_version: version, decision_seq: 0, kept_ids: [], dropped_ids: [] } }
      const body = r.body as { expected_state_version: number }
      return body.expected_state_version === version ? { status: 200, json: { session_id: 's1', state_version: version + 1, decision_seq: 1, kept_ids: ['u'], dropped_ids: [] } }
        : err(409, 'state_version_conflict')
    }
    _test.stateVersion.set('s1', 6)
    await expect(pilotApi.updateState('s1', [{ unit_id: 'u', action: 'keep' }])).rejects.toBeInstanceOf(ApiError)
    expect(_test.stateVersion.get('s1')).toBe(7)
    await expect(pilotApi.updateState('s1', [{ unit_id: 'u', action: 'keep' }])).resolves.toMatchObject({ kept_ids: ['u'] })
    version = 8
  })
})

describe('FE-TEMPLATE', () => {
  it('tạo phiên từ mẫu: cùng key khi gửi lại, đọc session.id, mở phiên không tự đề xuất', async () => {
    let calls = 0
    handler = r => {
      if (r.path.includes('/fork')) { calls++; return calls === 1 ? err(503, 'dependency_unavailable') : { status: 201, json: { session: { id: 's9' }, template_id: 't1', counts: { proposals: 0, decisions: 0, snapshots: 0 }, replayed: false } } }
      if (r.path.endsWith('/sessions/s9')) return { status: 200, json: { id: 's9', name: 'n', goal: 'g', target_k: 5, pool_source_ids: ['11111111-1111-1111-1111-111111111111'], state_version: 1, n_units: 4, n_kept: 0, n_dropped: 0, created_at: 'x', started_from_template_id: 't1', reference: null } }
      if (r.path.includes('/sources/')) return { status: 200, json: { id: '11111111-1111-1111-1111-111111111111', core_source_id: 'abc', kind: 'VIDEO', scope: 'SYSTEM', protected: true, name: 'v', availability: 'READY', resource_version: 1, metadata: {}, dino: { ready: true, job_id: null }, clip: { ready: false, job_id: null } } }
      if (r.path.endsWith('/state')) return { status: 200, json: { session_id: 's9', state_version: 1, decision_seq: 0, kept_ids: [], dropped_ids: [] } }
      if (r.path.endsWith('/proposals')) return { status: 200, json: [] }
      if (r.path.startsWith('/api/v2/jobs')) return { status: 200, json: { items: [], total: 0, next_cursor: null } }
      return { status: 200, json: [] }
    }
    await expect(apiV2.fork('t1')).rejects.toMatchObject({ status: 503 })
    const receipt = await apiV2.fork('t1')
    const forks = log.filter(r => r.path.includes('/fork'))
    expect(forks[1].key).toBe(forks[0].key)
    expect(receipt.session.id).toBe('s9')
    // Mở phiên vừa tạo như Studio: session, state, proposals, jobs, recipes
    const s = await pilotApi.session('s9')
    await Promise.all([pilotApi.state('s9'), pilotApi.proposals('s9'), pilotApi.proposalJobs('s9'), pilotApi.recipes()])
    expect(s.pool.video_ids).toEqual(['abc'])
    expect(log.some(r => r.method === 'POST' && r.path.endsWith('/proposals'))).toBe(false)
  })
})

describe('media + unit', () => {
  it('POST media một lần, PENDING thì poll GET, lần sau dùng cache', async () => {
    const sid = '22222222-2222-2222-2222-222222222222'
    let gets = 0
    handler = r => r.method === 'POST' ? { status: 202, json: { id: 'm1', state: 'PENDING', job_id: 'j', url: null, expires_at: null, sha256: null, error: null } }
      : (gets++, { status: 200, json: { id: 'm1', state: 'READY', job_id: null, url: 'https://s3/x', expires_at: new Date(Date.now() + 300_000).toISOString(), sha256: 'a', error: null } })
    const ref = mediaRef('context', sid, 'abc:1.000-1.000')
    expect(parseMediaRef(ref)).toEqual({ variant: 'context', source_id: sid, unit_id: 'abc:1.000-1.000' })
    await expect(resolveMedia(ref)).resolves.toBe('https://s3/x')
    await expect(resolveMedia(ref)).resolves.toBe('https://s3/x')
    expect(log.filter(r => r.method === 'POST')).toHaveLength(1)
    expect(gets).toBe(1)
  })

  it('unit dựng từ ID core:t0-t1 với ảnh qua media request', () => {
    rememberSource({ id: '33333333-3333-3333-3333-333333333333', core_source_id: 'core1' })
    const u = pilotUnit('core1:2.250-2.250')
    expect(u).toMatchObject({ video_id: 'core1', t0: 2.25, t1: 2.25 })
    expect(parseMediaRef(u.thumb_url)?.variant).toBe('thumbnail')
    expect(parseMediaRef(u.frame_url)?.source_id).toBe('33333333-3333-3333-3333-333333333333')
  })
})

describe('DEP5b2: YOLO + phân tích', () => {
  it('chạy YOLO: POST một lần → chỉ GET tới khi xong → đọc evidence; đã có evidence thì không xếp job', async () => {
    let jobPolls = 0
    const ev = { model_id: 'm1', model_name: 'YOLO11n', model_sha256: 'a', engine_version: '8.3.228', params: { conf: 0.25, iou: 0.7 },
                 total_images: 1, items: [{ unit_id: 'abc:0.000-0.000', detections: [] }], session_id: 's1', proposal_n: 1 }
    handler = r => {
      if (r.method === 'POST') return { status: 202, json: { evidence: null, job: job('QUEUED', { kind: 'YOLO_BATCH' }) } }
      if (r.path.startsWith('/api/v2/jobs/')) return { status: 200, json: job(++jobPolls < 2 ? 'RUNNING' : 'SUCCEEDED', { kind: 'YOLO_BATCH' }) }
      return { status: 200, json: ev }
    }
    const r = await pilotApi.runBatchYolo('s1', 1, 'm1')
    expect(r.items).toHaveLength(1)
    expect(r.cached).toBe(false)
    expect(log.filter(x => x.method === 'POST')).toHaveLength(1)
    expect(log.filter(x => x.method === 'POST')[0].key).toBeTruthy()
    log = []
    handler = r => r.method === 'POST' ? { status: 200, json: { evidence: ev, job: null } } : { status: 500 }
    expect((await pilotApi.runBatchYolo('s1', 1, 'm1')).cached).toBe(true)
    handler = () => err(404, 'not_found')
    expect(await pilotApi.batchYolo('s1', 2, 'm1')).toBeNull()
  })

  it('phân tích: đọc kết quả gần nhất (GET không tự tính), view thiếu dữ liệu báo rõ', async () => {
    const analysis = { id: 'a1', session_id: 's1', job_id: 'j', input_hash: 'h', input: {}, decision_seq: 3, state_version: 2, proposal_count: 1,
      created_at: 'x', current: true, results: { progress: { target_k: 5, kept: 1, dropped: 0, proposals: 1 }, on: 'kept',
        results: [{ type: 'nd_rate_at_k', params: {}, values: { nd_rate_at_k: 0 } }],
        views: { embedding_map: { type: 'embedding_map', data: { x: [0], y: [0], unit_id: ['u'] } }, timeline: { type: 'timeline', error: 'thiếu' } } } }
    handler = () => ({ status: 200, json: analysis })
    expect((await pilotApi.metrics('s1')).results[0].type).toBe('nd_rate_at_k')
    expect((await pilotApi.view('s1', 'embedding_map', {})).type).toBe('embedding_map')
    await expect(pilotApi.view('s1', 'timeline', {})).rejects.toMatchObject({ code: 'view_unavailable' })
    expect(log.every(r => r.method === 'GET')).toBe(true)
  })

  it('model chỉ dùng được khi đã xác minh chạy detection', async () => {
    handler = () => ({ status: 200, json: [
      { id: 'm1', name: 'ok', sha256: 'a', size_bytes: 1, task: 'detect', class_map: {}, validation: 'INFERENCE_VERIFIED', checkpoint_revision: 'r', provenance: {}, created_at: 'x' },
      { id: 'm2', name: 'chưa', sha256: 'b', size_bytes: 1, task: 'detect', class_map: {}, validation: 'CONTAINER_ONLY', checkpoint_revision: 'r', provenance: {}, created_at: 'x' }] })
    expect((await pilotApi.models()).map(m => m.available)).toEqual([true, false])
  })
})

describe('media F4: suất HTTP, ưu tiên, NOT_PREPARED, dừng poll, thế hệ (CONTRACTS §21.20)', () => {
  const sid = '44444444-4444-4444-4444-444444444444'
  const ref = (i: number, variant: 'thumbnail' | 'context' = 'thumbnail') => mediaRef(variant, sid, `abc:${i}.000-${i}.000`)
  const ready = (url: string) => ({ status: 200, json: { id: `m-${url}`, state: 'READY', job_id: null, url, expires_at: new Date(Date.now() + 300_000).toISOString(), sha256: 'a', error: null } })
  const pending = (id: string) => ({ status: 202, json: { id, state: 'PENDING', job_id: 'j', url: null, expires_at: null, sha256: null, error: null } })
  const notPrepared = (job: string | null) => ({ status: 200, json: { id: null, state: 'NOT_PREPARED', job_id: job, url: null, expires_at: null, sha256: null, error: null } })
  const flush = () => new Promise(r => setTimeout(r, 0))

  afterEach(() => { vi.useRealTimers() })

  it('tối đa 4 HTTP đồng thời; request ưu tiên 0 (viewer) đi trước ảnh viewport đang chờ', async () => {
    const gates: (() => void)[] = []
    handler = r => new Promise(res => gates.push(() => res(ready(String((r.body as { unit_id: string }).unit_id)))))
    const all = [1, 2, 3, 4, 5, 6].map(i => resolveMedia(ref(i, 'context'), 1))
    const viewer = resolveMedia(ref(9, 'context'), 0)
    await flush()
    expect(log).toHaveLength(4)
    expect(mediaTest.waiting()).toBe(3)
    gates.shift()!()
    await flush()
    expect((log[4].body as { unit_id: string }).unit_id).toBe('abc:9.000-9.000')   // ưu tiên 0 chen trước 5, 6
    while (gates.length) { gates.shift()!(); await flush() }
    await Promise.all([...all, viewer])
    expect(mediaTest.active()).toBe(0)
  })

  it('không giữ suất khi chờ timer poll: 4 media PENDING không chặn request thứ 5', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout'] })
    handler = r => r.method === 'POST' ? pending(`m${(r.body as { unit_id: string }).unit_id}`) : ready('x')
    const waits = [1, 2, 3, 4].map(i => resolveMedia(ref(i, 'context')))
    await vi.advanceTimersByTimeAsync(0)
    const fifth = resolveMedia(ref(5, 'context'))
    await vi.advanceTimersByTimeAsync(0)
    expect(log.filter(r => r.method === 'POST')).toHaveLength(5)   // trước bất kỳ GET poll nào
    expect(mediaTest.active()).toBe(0)
    await vi.advanceTimersByTimeAsync(600)
    await Promise.all([...waits, fifth])
    expect(log.filter(r => r.method === 'GET')).toHaveLength(5)
  })

  it('NOT_PREPARED không job → lỗi riêng để UI hiện nút; có job → POST lại tới READY', async () => {
    handler = () => ({ status: 200, json: [notPrepared(null).json] })
    await expect(resolveMedia(ref(1))).rejects.toMatchObject({ sourceId: sid, jobId: null })
    vi.useFakeTimers({ toFake: ['setTimeout'] })
    let posts = 0
    handler = () => ({ status: 200, json: [(++posts < 3 ? notPrepared('prep-job') : ready('https://s3/frame')).json] })
    const p = resolveMedia(ref(2))
    await vi.advanceTimersByTimeAsync(7000)   // includes coalescing timers between readiness polls
    await expect(p).resolves.toBe('https://s3/frame')
    expect(log.slice(1).every(r => r.method === 'POST' && r.path.endsWith('/media/batch'))).toBe(true)
  })

  it('hook không còn dùng → ngừng poll; lần sau GET tiếp receipt, không POST lại', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout'] })
    handler = r => r.method === 'POST' ? pending('m-stop') : ready('https://s3/late')
    const watched = resolveMedia(ref(3, 'context'), 1, true)   // watched nhưng không có consumer nào
    const caught = watched.catch(e => e)
    await vi.advanceTimersByTimeAsync(600)
    expect(await caught).toBeInstanceOf(MediaCancelledError)
    expect(log.map(r => r.method)).toEqual(['POST'])
    await expect(resolveMedia(ref(3, 'context'))).resolves.toBe('https://s3/late')
    expect(log.map(r => r.method)).toEqual(['POST', 'GET'])
  })

  it('đăng xuất (clearMediaCache) bỏ response của thế hệ cũ', async () => {
    let release: () => void = () => {}
    handler = () => new Promise(res => { release = () => res({status:200, json:[ready('https://s3/old').json]}) })
    const old = resolveMedia(ref(4)).catch(e => e)
    await flush()
    clearMediaCache()
    release()
    expect(await old).toBeInstanceOf(MediaCancelledError)
    handler = () => ({status:200, json:[ready('https://s3/new').json]})
    await expect(resolveMedia(ref(4))).resolves.toBe('https://s3/new')   // không dùng URL của phiên đăng nhập cũ
  })

  it('chuẩn bị ảnh ở ô ảnh gọi preparations FRAMES', async () => {
    handler = () => ({ status: 202, json: { source_id: sid, feature: 'FRAMES', ready: false, job: null } })
    await prepareFrames(sid)
    expect(log[0]).toMatchObject({ method: 'POST', body: { feature: 'FRAMES' } })
    expect(log[0].path).toContain(`/sources/${sid}/preparations`)
  })

  it('16 images use one batch, deduplicate and refresh expired URLs', async () => {
    let batches = 0
    handler = r => {
      const items = (r.body as {items: {unit_id: string}[]}).items
      batches++
      return {status:200, json:items.map(i => ({...ready('https://s3/'+i.unit_id).json,
        expires_at: new Date(Date.now() + (batches === 1 ? 1000 : 300_000)).toISOString()}))}
    }
    const promises = Array.from({length:16}, (_,i) => resolveMedia(ref(i)))
    expect(resolveMedia(ref(0))).toBe(promises[0])
    await Promise.all(promises)
    expect(log).toHaveLength(1)
    expect((log[0].body as {items:unknown[]}).items).toHaveLength(16)
    await resolveMedia(ref(0))
    expect(log).toHaveLength(2)
    expect(log.every(r => r.path.endsWith('/media/batch'))).toBe(true)
  })

  it('image batch respects a smaller policy cap without holding nested HTTP slots', async () => {
    handler = r => {
      const items = (r.body as {items:{unit_id:string}[]}).items
      if (items.length > 3) return err(422, 'validation_error', {max_items:3})
      return {status:200, json:items.map(i => ready('https://s3/'+i.unit_id).json)}
    }
    await Promise.all(Array.from({length:16}, (_,i) => resolveMedia(ref(i))))
    expect(log).toHaveLength(7)  // one discovery failure + six bounded batches
    expect(log.slice(1).every(r => (r.body as {items:unknown[]}).items.length <= 3)).toBe(true)
    expect(mediaTest.active()).toBe(0)
  })

  it('read-only media batch refreshes token once on 401', async () => {
    const auth = fakeAuth(['A', 'B']); setAuthProvider(auth)
    handler = r => r.auth === 'Bearer A' ? err(401,'unauthorized') : {status:200,json:[ready('https://s3/refreshed').json]}
    await expect(resolveMedia(ref(99))).resolves.toBe('https://s3/refreshed')
    expect(auth.refreshes).toBe(1)
  })

  it('cache media: query mới sau NOT_PREPARED có thể resolve READY (browser test kiểm remount)', async () => {
    handler = () => ({ status: 200, json: [notPrepared(null).json] })
    await expect(resolveMedia(ref(10))).rejects.toMatchObject({ sourceId: sid, jobId: null })
    handler = () => ({status:200, json:[ready('https://s3/toolbar-ready').json]})
    await expect(resolveMedia(ref(10))).resolves.toBe('https://s3/toolbar-ready')
  })
})

describe('FE-UPLOAD: giữ tiến trình upload cấp ứng dụng & khôi phục sau reload (CONTRACTS §21.35)', () => {
  it('khôi phục sau reload: VALIDATING tự nối lại poll tới READY; CREATED hiện "Chọn lại file để tiếp tục"', async () => {
    const {
      cancelNeedsFileUpload,
      getUploads,
      onUploadReady,
      recoverUploads,
      resetUploadManager,
      setUploadPollIntervalForTest,
    } = await import('./upload_manager')
    resetUploadManager()
    setUploadPollIntervalForTest(5)

    let polls = 0
    let readyCalls = 0
    const off = onUploadReady(() => { readyCalls++ })

    handler = r => {
      if (r.path === '/api/v2/uploads' && r.method === 'GET') {
        return {
          status: 200,
          json: [
            {
              id: 'u-val',
              state: 'VALIDATING',
              validation_job_id: 'j-val',
              source_id: null,
              error: null,
              filename: 'highway.mp4',
              declared_bytes: 4096,
              progress: { phase: 'EXTRACTING_FRAMES', done: 10, total: 20, fps: 12.0, eta_s: 4 },
            },
            {
              id: 'u-created',
              state: 'CREATED',
              validation_job_id: null,
              source_id: null,
              error: null,
              filename: 'unfinished.mp4',
              declared_bytes: 8192,
              progress: null,
            },
          ],
        }
      }
      if (r.path === '/api/v2/uploads/u-val' && r.method === 'GET') {
        polls++
        return {
          status: 200,
          json:
            polls < 2
              ? {
                  id: 'u-val',
                  state: 'VALIDATING',
                  validation_job_id: 'j-val',
                  source_id: null,
                  error: null,
                  filename: 'highway.mp4',
                  declared_bytes: 4096,
                  progress: { phase: 'FINALIZING', done: 20, total: 20 },
                }
              : {
                  id: 'u-val',
                  state: 'READY',
                  validation_job_id: 'j-val',
                  source_id: 'src-ready',
                  error: null,
                  filename: 'highway.mp4',
                  declared_bytes: 4096,
                },
        }
      }
      if (r.path === '/api/v2/uploads/u-created/abort' && r.method === 'POST') {
        return {
          status: 200,
          json: { id: 'u-created', state: 'ABORTED', validation_job_id: null, source_id: null, error: null },
        }
      }
      return { status: 200, json: {} }
    }

    const initial = await recoverUploads()
    expect(initial).toHaveLength(2)
    expect(initial.find(u => u.uploadId === 'u-created')).toMatchObject({
      name: 'unfinished.mp4',
      status: 'needs_file',
      state: 'Chọn lại file để tiếp tục',
    })
    expect(initial.find(u => u.uploadId === 'u-val')?.state).toContain('đang trích xuất ảnh 10/20')

    // Đợi vòng poll của u-val hoàn tất (ngay cả khi người dùng chuyển màn Kho ↔ Phiên, state vẫn giữ ở upload_manager)
    for (let i = 0; i < 20 && getUploads().find(u => u.uploadId === 'u-val')?.status !== 'done'; i++) {
      await new Promise(r => setTimeout(r, 10))
    }
    expect(getUploads().find(u => u.uploadId === 'u-val')).toMatchObject({
      status: 'done',
      state: 'xong',
      progress: 1,
    })
    expect(readyCalls).toBe(1)

    // Hủy mục CREATED dở dang gọi POST /uploads/{id}/abort và xóa khỏi danh sách
    const needsFileKey = getUploads().find(u => u.uploadId === 'u-created')!.key
    await cancelNeedsFileUpload(needsFileKey)
    expect(getUploads().find(u => u.uploadId === 'u-created')).toBeUndefined()
    expect(log.some(r => r.path === '/api/v2/uploads/u-created/abort' && r.method === 'POST')).toBe(true)

    off()
    resetUploadManager()
  })

  it('Lát 3 (§21.36): admin catalog API, SYSTEM upload và bộ lọc log theo upload_id / job_id / request_id', async () => {
    handler = r => {
      if (r.path.startsWith('/api/v2/admin/sources/') && r.method === 'PATCH') {
        return {
          status: 200,
          json: {
            id: 'sys-1',
            core_source_id: 'core1',
            kind: 'VIDEO',
            scope: 'SYSTEM',
            protected: true,
            name: (r.body as { name?: string })?.name ?? 'Mẫu 1',
            description: (r.body as { description?: string })?.description ?? '',
            published: (r.body as { published?: boolean })?.published ?? true,
            availability: 'READY',
            resource_version: 2,
            metadata: {},
            dino: { ready: true, job_id: null },
            clip: { ready: true, job_id: null },
            frames: { ready: true, job_id: null },
            publishable: true,
            unpublishable_reasons: [],
            dependent_template_count: 1,
            dependent_templates: [{ id: 't1', template_key: 'k1', name: 'Mẫu T1', availability: 'VERIFIED' }],
          },
        }
      }
      if (r.path.startsWith('/api/v2/admin/sources') && r.method === 'GET') {
        return { status: 200, json: { items: [], total: 0, next_cursor: null } }
      }
      if (r.path.startsWith('/api/v2/admin/models/') && r.method === 'PATCH') {
        return {
          status: 200,
          json: {
            id: 'mod-1',
            core_model_id: 'mcore1',
            scope: 'SYSTEM',
            protected: true,
            name: 'YOLO Mẫu',
            description: 'Mô hình mẫu',
            published: true,
            sha256: 'a'.repeat(64),
            size_bytes: 100,
            task: 'detect',
            class_map: {},
            validation: 'INFERENCE_VERIFIED',
            checkpoint_revision: 'r1',
            provenance: {},
            created_at: '2026-10-17T00:00:00Z',
            publishable: true,
            unpublishable_reasons: [],
          },
        }
      }
      if (r.path.startsWith('/api/v2/uploads') && r.method === 'POST') {
        return {
          status: 201,
          json: {
            id: 'up-sys-1',
            state: 'CREATED',
            target_scope: 'SYSTEM',
            validation_job_id: null,
            source_id: null,
            error: null,
            transfer: { method: 'POST', url: 'https://s3/post', fields: {}, expires_at: '2099-01-01T00:00:00Z' },
          },
        }
      }
      return { status: 200, json: { items: [], next_cursor: null } }
    }

    await apiV2.adminSources()
    const patchedSrc = await apiV2.updateAdminSource('sys-1', { name: 'Mẫu Đổi Tên', published: true })
    expect(patchedSrc.name).toBe('Mẫu Đổi Tên')
    expect(patchedSrc.published).toBe(true)

    const patchedMod = await apiV2.updateAdminModel('mod-1', { published: true })
    expect(patchedMod.published).toBe(true)

    const createdSys = await apiV2.createUpload('sample.mp4', 1234, 'sys-act', 'SYSTEM')
    expect(createdSys.target_scope).toBe('SYSTEM')
    expect(log.find(r => r.path === '/api/v2/uploads' && r.method === 'POST')?.body).toEqual({
      filename: 'sample.mp4',
      declared_bytes: 1234,
      target_scope: 'SYSTEM',
    })

    log = []
    await operationLogs({
      hours: 24,
      errorsOnly: false,
      uploadId: '11111111-1111-1111-1111-111111111111',
      jobId: '22222222-2222-2222-2222-222222222222',
      requestId: '33333333-3333-3333-3333-333333333333',
    })
    const q = new URL(log[0].path, 'http://test').searchParams
    expect(Object.fromEntries(q)).toEqual({
      hours: '24',
      errors_only: 'false',
      limit: '50',
      upload_id: '11111111-1111-1111-1111-111111111111',
      job_id: '22222222-2222-2222-2222-222222222222',
      request_id: '33333333-3333-3333-3333-333333333333',
    })
  })
})


