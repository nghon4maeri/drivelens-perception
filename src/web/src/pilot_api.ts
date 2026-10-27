/**
 * Bản `api` cho chế độ pilot (CONTRACTS §21.16, #96): cùng interface với client v1 để hook/component giữ nguyên, nhưng chỉ gọi `/api/v2`
 * và ánh xạ DTO v2 sang dạng UI đang dùng.
 * - ID video phía UI = `core_source_id` (unit ID là `core:t0-t1`); bảng nhớ core ↔ source UUID để gọi media/chuẩn bị đặc trưng.
 * - Quyết định/snapshot/đề xuất gửi `expected_state_version` nhớ theo phiên; 409 → tải state mới, báo người dùng.
 * - Đề xuất luôn là job: `queueProposal` → poll `proposalJobs` (chỉ GET) → `GET /jobs/{id}/result`.
 * - Ảnh/clip là tham chiếu `rav-media:<variant>:<source>:<unit>`, `media.ts` đổi sang URL qua media request.
 * - Chức năng chưa có ở pilot (YOLO, phân tích, xuất ZIP, dataset/model) → `not_available_in_pilot`.
 */
import type {
  Api, Change, ComponentDTO, MetricsDTO, ModelDTO, On, ProposalDTO, ProposalExplainDTO, ProposalJobDTO, ProposeRequest, RecipeDTO, ReviewStateDTO, SessionDTO,
  SnapshotDTO, SnapshotDetailDTO, UnitDTO, VideoDTO, ViewSpec, YoloBatchDTO,
} from './api'
import { ApiError } from './api_error'
import { phaseLabel } from './proposal_phases'
import { all, apiV2, V2Error, type AnalysisV2, type JobV2, type ProposalV2, type SessionV2, type SnapshotV2, type SourceV2 } from './api_v2'

const coreToSource = new Map<string, string>()
const sourceToCore = new Map<string, string>()
const stateVersion = new Map<string, number>()
export const savedVersion = (sid: string) => stateVersion.get(sid) ?? 1
export const rememberVersion = (sid: string, n: number) => stateVersion.set(sid, n)

const sourceScope = new Map<string, string>()
export function rememberSource(s: { id: string; core_source_id: string; scope?: string }) {
  coreToSource.set(s.core_source_id, s.id)
  sourceToCore.set(s.id, s.core_source_id)
  if (s.scope) sourceScope.set(s.id, s.scope)
}
export const sourceOfCore = (core: string) => coreToSource.get(core)
export const coreOfSource = (id: string) => sourceToCore.get(id)
/** Nguồn SYSTEM chưa có ảnh: operator chuẩn bị, UI không hiện nút (§21.20). */
export const isSystemSource = (id: string) => sourceScope.get(id) === 'SYSTEM'

export const mediaRef = (variant: 'thumbnail' | 'frame' | 'context', sourceId: string, unitId: string) =>
  `rav-media:${variant}:${sourceId}:${unitId}`

export function parseMediaRef(ref: string): { variant: 'thumbnail' | 'frame' | 'context'; source_id: string; unit_id: string } | null {
  const m = /^rav-media:(thumbnail|frame|context):([0-9a-f-]{36}):(.+)$/.exec(ref)
  return m ? { variant: m[1] as 'thumbnail' | 'frame' | 'context', source_id: m[2], unit_id: m[3] } : null
}

/** Unit từ ID `core:t0-t1` (không cần route units); ảnh qua media request của nguồn tương ứng. */
export function pilotUnit(id: string): UnitDTO {
  const i = id.lastIndexOf(':')
  const core = id.slice(0, i)
  const [t0, t1] = id.slice(i + 1).split('-').map(Number)
  const src = coreToSource.get(core)
  return { id, video_id: core, t0, t1: Number.isFinite(t1) ? t1 : t0,
           thumb_url: src ? mediaRef('thumbnail', src, id) : '', frame_url: src ? mediaRef('frame', src, id) : '' }
}

/** Clip ngữ cảnh ±3 s quanh unit (mốc 0 của clip = max(0, t0 − 3), §21.12–21.13). */
export function contextRef(unit: UnitDTO): string | undefined {
  const src = coreToSource.get(unit.video_id)
  return src ? mediaRef('context', src, unit.id) : undefined
}

function fail(e: unknown): never {
  if (e instanceof V2Error) throw new ApiError(e.code, e.message, [], e.details, e.status)
  throw e
}
const w = <T>(p: Promise<T>): Promise<T> => p.catch(fail)
const notAvailable = (what: string) => async (..._args: unknown[]): Promise<never> => {
  throw new ApiError('not_available_in_pilot', `${what} chưa có ở bản pilot.`)
}

export function videoOf(s: SourceV2): VideoDTO {
  rememberSource(s)
  const busy = (ready: boolean, job: string | null, label: string) => !ready && job
    ? { state: 'running' as const, stage: `Đang chuẩn bị ${label} (GPU)`, done: 0, total: 0, job_id: job } : null
  return {
    id: s.core_source_id, name: s.name, duration_s: (s.metadata.duration_ms ?? 0) / 1000, n_sessions: 0, prepared: s.dino.ready,
    media_url: '', thumb_url: mediaRef('thumbnail', s.id, `${s.core_source_id}:0.000-0.000`),
    width: s.metadata.width ?? 0, height: s.metadata.height ?? 0, fps: 0, size_bytes: 0,
    prepare: busy(s.dino.ready, s.dino.job_id, 'DINO'), clip_prepared: s.clip.ready, clip_prepare: busy(s.clip.ready, s.clip.job_id, 'CLIP'),
  }
}

async function ensureSources(ids: string[]) {
  const missing = ids.filter(id => !sourceToCore.has(id))
  await Promise.all(missing.map(id => apiV2.source(id).then(rememberSource)))
}

function sessionOf(s: SessionV2): SessionDTO {
  stateVersion.set(s.id, s.state_version)
  return {
    id: s.id, name: s.name, created_at: s.created_at, target_k: s.target_k, goal: s.goal, n_units: s.n_units,
    pool: { video_ids: s.pool_source_ids.map(id => sourceToCore.get(id) ?? id) }, n_kept: s.n_kept, n_dropped: s.n_dropped,
    n_proposals: 0, reference: { dataset_version_ids: [], model_id: null }, reference_model: null, latest_snapshot: null,
  }
}

export function proposalOf(p: ProposalV2): ProposalDTO {
  for (const k of p.picks) rememberSource({ id: k.source_id, core_source_id: k.unit_id.slice(0, k.unit_id.lastIndexOf(':')) })
  const ev = p.evidence as Record<string, unknown> | undefined
  // Historical interactive checkpoints contain only envelope metadata, without `units`.
  const units = ev && Object.hasOwn(ev, 'units') ? ev.units : ev
  const evUnits: NonNullable<ProposalDTO['evidence']> = {}
  if (units && typeof units === 'object' && !Array.isArray(units)) {
    for (const { unit_id } of p.picks) {
      const rows = (units as Record<string, unknown>)[unit_id]
      if (Array.isArray(rows)) evUnits[unit_id] = rows
    }
  }
  const uncertaintySummary = (p.uncertainty_summary ?? ev?.uncertainty_summary) as ProposalDTO['uncertainty_summary']
  return {
    n: p.n, round: p.round, created_at: p.created_at, input: p.input as unknown as ProposalDTO['input'], relaxed: p.relaxed,
    picks: p.picks.map(({ unit_id, rank, combined, breakdown }) => ({ unit_id, rank, combined, breakdown })), scores: p.scores ?? null,
    evidence: evUnits, uncertainty_summary: uncertaintySummary, trace: p.trace,
  }
}

const WAIT: Record<string, string> = {
  gpu_budget_unverified: 'chưa xác minh được ngân sách GPU', gpu_budget_exhausted: 'hết ngân sách GPU chung',
  lease_lost: 'đang đối soát lượt chạy trước', quota_reconcile: 'đang đối soát quota',
}

export type PilotJob = ProposalJobDTO & { state_version: number; raw_state: JobV2['state']; retryable: boolean }

export function jobOf(j: JobV2, sid: string, proposalN: number | null = null): PilotJob {
  const state = ({ QUEUED: 'queued', WAITING_RESOURCE: 'queued', RUNNING: 'running', CANCEL_REQUESTED: 'running', SUCCEEDED: 'done',
                   FAILED: 'error', CANCELLED: 'error' } as const)[j.state]
  const stage = { QUEUED: 'đang xếp hàng', WAITING_RESOURCE: `chờ tài nguyên: ${WAIT[j.waiting_reason ?? ''] ?? j.waiting_reason ?? 'chưa rõ'}`,
                  RUNNING: j.phase === 'claimed' ? 'worker đã nhận' : phaseLabel(j.phase), CANCEL_REQUESTED: 'đang chờ tiến trình dừng an toàn',
                  SUCCEEDED: 'xong', FAILED: 'lỗi', CANCELLED: 'đã hủy' }[j.state]
  const error = j.state === 'FAILED' ? `${j.error?.message ?? 'Đề xuất lỗi'} (${j.error?.code ?? 'error'})`
    : j.state === 'CANCELLED' ? 'Đã hủy đề xuất.' : null
  return { id: j.id, session_id: sid, state, stage, done: j.progress?.done ?? 0, total: j.progress?.total ?? 0, error, proposal_n: proposalN,
           timings: j.progress?.stages, created_at: '', state_version: j.state_version, raw_state: j.state, retryable: j.state === 'CANCELLED' || !!j.error?.retryable
             || j.error?.code === 'proposal_context_changed' }
}

async function currentVersion(sid: string): Promise<number> {
  const known = stateVersion.get(sid)
  if (known !== undefined) return known
  const s = await apiV2.state(sid)
  stateVersion.set(sid, s.state_version)
  return s.state_version
}

async function onConflict(sid: string, e: unknown): Promise<never> {
  if (e instanceof V2Error && e.code === 'state_version_conflict') {
    const s = await apiV2.state(sid)
    stateVersion.set(sid, s.state_version)
    throw new ApiError('state_version_conflict', 'Phiên vừa đổi ở nơi khác; đã tải trạng thái mới. Thao tác này chưa lưu — bấm Thử lại.', [], {}, 409)
  }
  return fail(e)
}

async function snapshotOf(s: SnapshotV2, decisionSeq: number): Promise<SnapshotDTO> {
  return { id: s.id, session_id: s.session_id, n: s.n, created_at: s.created_at, created_by: '', note: s.note, n_members: s.n_members,
           members_hash: s.members_hash, context_hash: s.context_hash, decision_seq: s.decision_seq,
           is_current: s.decision_seq === decisionSeq, decisions_after: Math.max(0, decisionSeq - s.decision_seq) }
}

/** Chờ job tới trạng thái kết thúc (chỉ GET); hết giờ → lỗi, job vẫn chạy ở server. */
export async function waitJob(id: string, timeoutMs = 600_000): Promise<JobV2> {
  const deadline = Date.now() + timeoutMs
  for (let i = 0; ; i++) {
    const j = await apiV2.job(id)
    if (['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(j.state)) return j
    if (Date.now() > deadline) throw new ApiError('job_timeout', 'Job chưa xong; xem lại sau.')
    await new Promise(r => setTimeout(r, Math.min(1000 + 250 * i, 3000)))
  }
}

function jobFailed(j: JobV2): never {
  throw new ApiError(j.error?.code ?? j.state.toLowerCase(), j.state === 'CANCELLED' ? 'Đã hủy.' : j.error?.message ?? 'Job lỗi.')
}

const yoloOf = (ev: Record<string, unknown>, cached: boolean): YoloBatchDTO =>
  ({ created_at: '', ...ev, cached } as unknown as YoloBatchDTO)

const metricsOf = (a: AnalysisV2): MetricsDTO =>
  ({ progress: a.results.progress as MetricsDTO['progress'], on: a.results.on as On, results: a.results.results })

/** Pilot: xếp job phân tích mặc định cho state hiện tại (bấm "Tính phân tích"); đã có kết quả cùng ngữ cảnh → trả luôn. */
export async function requestAnalysis(sid: string): Promise<{ analysis: AnalysisV2 | null; job: JobV2 | null }> {
  const expected = await w(currentVersion(sid))
  try { return await apiV2.requestAnalysis(sid, { expected_state_version: expected }) } catch (e) { return onConflict(sid, e) }
}

export const pilotApi: Api = {
  // ---- kho nguồn / phiên ----
  videos: () => w(all(apiV2.sources).then(list => list.filter(s => s.kind === 'VIDEO').map(videoOf))),
  sessions: () => w(all(apiV2.sessions).then(async list => {
    await ensureSources([...new Set(list.flatMap(s => s.pool_source_ids))])
    return list.map(sessionOf).reverse()   // UI v1 nhận cũ → mới
  })),
  session: (sid: string) => w(apiV2.session(sid).then(async s => { await ensureSources(s.pool_source_ids); return sessionOf(s) })),
  createSession: (body: { pool: { video_ids?: string[] }; goal: string; target_k: number; name?: string }) => w((async () => {
    const ids = (body.pool.video_ids ?? []).map(core => {
      const id = coreToSource.get(core)
      if (!id) throw new ApiError('source_unknown', `không tìm thấy nguồn ${core}`)
      return id
    })
    return sessionOf(await apiV2.createSession({ name: body.name?.trim() || 'Phiên mới', goal: body.goal, target_k: body.target_k, pool_source_ids: ids }))
  })()),
  units: async (_sid: string) => [] as UnitDTO[],   // unit dựng từ ID trong đề xuất/state (pilotUnit), không có route units
  queuePrepare: (core: string) => w(apiV2.prepare(coreToSource.get(core) ?? core, 'DINO').then(r => apiV2.source(r.source_id)).then(videoOf)),
  queueClip: (core: string) => w(apiV2.prepare(coreToSource.get(core) ?? core, 'CLIP').then(r => apiV2.source(r.source_id)).then(videoOf)),
  // ---- duyệt ----
  recipes: () => w(apiV2.recipes() as Promise<RecipeDTO[]>),
  state: (sid: string) => w(apiV2.state(sid).then(s => {
    stateVersion.set(sid, s.state_version)
    return { kept_ids: s.kept_ids, dropped_ids: s.dropped_ids, history: [] } as ReviewStateDTO
  })),
  updateState: async (sid: string, changes: Change[]) => {
    const expected = await w(currentVersion(sid))
    try {
      const s = await apiV2.decide(sid, expected, changes as unknown as Record<string, unknown>[])
      stateVersion.set(sid, s.state_version)
      return { kept_ids: s.kept_ids, dropped_ids: s.dropped_ids, history: [] }
    } catch (e) { return onConflict(sid, e) }
  },
  proposals: (sid: string) => w(apiV2.proposals(sid).then(list => list.map(proposalOf))),
  proposal: (sid: string, n: number) => w(apiV2.proposal(sid, n).then(proposalOf)),
  explain: (sid: string, n: number) => w(apiV2.proposal(sid, n).then(p => {
    if (!p.explain) throw new ApiError('explain_unavailable', 'Đợt này không có giải thích.')
    return p.explain as unknown as ProposalExplainDTO
  })),
  evidence: async () => [],
  propose: notAvailable('Đề xuất đồng bộ'),
  queueProposal: async (sid: string, req: ProposeRequest) => {
    const expected = await w(currentVersion(sid))
    try {
      const job = await apiV2.propose(sid, { recipe: req.recipe, config: req.config, batch_size: req.batch_size, seed: req.seed,
                                             return_scores: !!req.return_scores, expected_state_version: expected, changes: req.changes ?? [],
                                             model_id: req.model_id, strategy_params: req.strategy_params })
      if (job.accepted_state_version != null) stateVersion.set(sid, job.accepted_state_version)
      return jobOf(job, sid)
    } catch (e) { return onConflict(sid, e) }
  },
  proposalJobs: (sid: string) => w((async () => {
    const jobs = []
    let cursor: string | undefined
    do {
      const page = await apiV2.jobs(sid, cursor)
      jobs.push(...page.items)
      cursor = page.next_cursor ?? undefined
    } while (cursor)
    jobs.reverse()
    const last = jobs.at(-1)
    const n = last?.state === 'SUCCEEDED' ? (await apiV2.jobResult(last.id)).proposal_n as number | null : null
    return jobs.map(j => jobOf(j, sid, j === last ? n : null))
  })()),
  snapshots: (sid: string) => w((async () => {
    const [page, st] = await Promise.all([apiV2.snapshots(sid), apiV2.state(sid)])
    return Promise.all(page.items.map(s => snapshotOf(s, st.decision_seq)))
  })()),
  createSnapshot: async (sid: string, note: string) => {
    const expected = await w(currentVersion(sid))
    try {
      const s = await apiV2.createSnapshot(sid, expected, note.trim() || null)
      return { ...(await snapshotOf(s, s.decision_seq)), created: true }
    } catch (e) { return onConflict(sid, e) }
  },
  snapshot: (id: string) => w(apiV2.snapshot(id).then(async s => ({
    ...(await snapshotOf(s, s.decision_seq)), context: s.context,
    members: s.members.map(m => ({ ord: m.ord, unit_id: m.unit_id, source_kind: m.source_kind as 'video', source_id: m.core_source_id,
                                    provenance: { t0: pilotUnit(m.unit_id).t0 } })),
  }) as SnapshotDetailDTO)),
  downloadSnapshot: notAvailable('Tải ZIP snapshot (xuất có kiểm PII ở DEP6)'),
  // ---- YOLO trên đợt + phân tích (DEP5b2 §21.17) ----
  models: () => w(apiV2.models().then(list => list.map(m => ({
    id: m.id, name: m.name, filename: m.checkpoint_revision, sha256: m.sha256, size_bytes: m.size_bytes, created_at: m.created_at,
    format: 'pytorch_zip', available: m.validation === 'INFERENCE_VERIFIED' && m.task === 'detect', validation: 'container_only', inference_ready: false,
  }) as ModelDTO))),
  batchYolo: async (sid: string, n: number, modelId: string) => {
    try { return yoloOf(await apiV2.detections(sid, n, modelId), true) } catch (e) {
      if (e instanceof V2Error && e.status === 404) return null
      return fail(e)
    }
  },
  runBatchYolo: (sid: string, n: number, model_id: string) => w((async () => {
    const r = await apiV2.requestDetections(sid, n, model_id)
    if (r.evidence) return yoloOf(r.evidence, true)
    const j = await waitJob(r.job!.id)
    if (j.state !== 'SUCCEEDED') jobFailed(j)
    return yoloOf(await apiV2.detections(sid, n, model_id), false)
  })()),
  metrics: (sid: string) => w(apiV2.analysis(sid).then(metricsOf)),
  metricsCustom: (sid: string, body: { metrics?: { type: string; params?: Record<string, unknown> }[]; on?: On }) => w((async () => {
    const expected = await currentVersion(sid)
    const r = await apiV2.requestAnalysis(sid, { expected_state_version: expected, metrics: (body.metrics ?? []).map(m => ({ type: m.type, params: m.params ?? {} })),
                                                 on: body.on ?? 'kept' })
    if (r.analysis) return metricsOf(r.analysis)
    const j = await waitJob(r.job!.id)
    if (j.state !== 'SUCCEEDED') jobFailed(j)
    const res = await apiV2.jobResult(j.id)
    return metricsOf(await apiV2.analysis(sid, res.input_hash as string))
  })()),
  view: (sid: string, name: string) => w(apiV2.analysis(sid).then(a => {
    const v = a.results.views[name]
    if (!v || v.error) throw new ApiError('view_unavailable', v?.error ?? `Chưa có dữ liệu ${name}; bấm Tính phân tích.`)
    return { type: v.type, data: v.data } as ViewSpec
  })),
  component: async (kind: string, name: string) =>
    ({ kind, name, version: '', doc: 'Pilot dùng tham số mặc định; dữ liệu từ lần Tính phân tích gần nhất.', params_schema: {} }) as ComponentDTO,
  // ---- chưa có ở pilot (DEP6 / Hướng tới) ----
  datasets: async () => [],
  exports: async () => [],
  upload: notAvailable('Upload kiểu cũ'),
  addVideos: notAvailable('Thêm video vào phiên'),
  export: notAvailable('Xuất dữ liệu'),
  uploadModel: notAvailable('Model'),
  verifyModel: notAvailable('Model'),
  importFolders: async () => [],
  importDataset: notAvailable('Dataset'),
  datasetVersion: notAvailable('Dataset'),
  datasetSamples: notAvailable('Dataset'),
  prepareDataset: notAvailable('Dataset'),
  verifyDataset: notAvailable('Dataset'),
}

export const _test = { coreToSource, sourceToCore, stateVersion }
