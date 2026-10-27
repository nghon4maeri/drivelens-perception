/**
 * Client `/api/v2` của pilot (CONTRACTS §21.4, §21.11–21.16; UI-export spec §2–3). Mọi fetch v2 đi qua `v2()`:
 * - gắn Bearer từ `auth.ts`; response của generation cũ (đã đăng xuất/đổi tài khoản) → `StaleResponseError`, không áp dụng;
 * - 401 → refresh một lần rồi gửi lại nếu request lặp được an toàn (GET, có Idempotency-Key hoặc CAS); vẫn 401 / `account_disabled` → đăng xuất;
 * - lỗi chuẩn hóa `V2Error {status, code, message, retryable, details}` từ envelope `{error: {code, message, retryable, ...}}`.
 * Idempotency-Key giữ theo action + input đóng băng tới khi có kết quả (mất response → gửi lại cùng key). DTO viết tay theo backend.
 */
import { accessToken, authGeneration, expire, refreshOnce, trackRequest } from './auth'
import { publicConfig } from './public_config'

export const API_BASE = publicConfig().apiUrl

export class V2Error extends Error {
  constructor(public status: number, public code: string, message: string, public details: Record<string, unknown> = {},
              public retryable = false) { super(message); this.name = 'V2Error' }
}

/** Response tới sau khi đăng xuất/đổi tài khoản: bỏ qua, không hiện lỗi. */
export class StaleResponseError extends Error {
  code = 'stale_response'
  constructor() { super('response của phiên đăng nhập cũ'); this.name = 'StaleResponseError' }
}

type Opts = { method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE'; body?: unknown; key?: string; readOnly?: boolean }

const replayable = (o: Opts) => !o.method || o.method === 'GET' || !!o.key || !!o.readOnly
  || (typeof o.body === 'object' && o.body !== null && 'expected_state_version' in o.body)

export async function v2<T>(path: string, opts: Opts = {}): Promise<T> {
  const gen = authGeneration()
  for (let attempt = 0; ; attempt++) {
    const token = await accessToken()
    if (gen !== authGeneration()) throw new StaleResponseError()
    const headers: Record<string, string> = {}
    if (token) headers.Authorization = `Bearer ${token}`
    if (opts.body !== undefined) headers['Content-Type'] = 'application/json'
    if (opts.key) headers['Idempotency-Key'] = opts.key
    const req = trackRequest()
    let res: Response
    try {
      res = await fetch(API_BASE + path, { method: opts.method ?? 'GET', headers, signal: req.signal, cache: 'no-store',
                                           body: opts.body === undefined ? undefined : JSON.stringify(opts.body) })
    } catch {
      if (gen !== authGeneration()) throw new StaleResponseError()
      throw new V2Error(0, 'network_error', 'Không kết nối được máy chủ; kiểm tra mạng rồi thử lại.', {}, true)
    } finally {
      req.done()
    }
    const data = res.status === 204 ? null : await res.json().catch(() => null)
    if (gen !== authGeneration()) throw new StaleResponseError()
    if (res.ok) return data as T
    const { code = 'http_error', message = `HTTP ${res.status}`, retryable = res.status >= 500 || res.status === 429, ...details } = data?.error ?? {}
    if (res.status === 401 && code !== 'account_disabled' && attempt === 0 && replayable(opts)) {
      const refreshed = await refreshOnce()
      if (gen !== authGeneration()) throw new StaleResponseError()
      if (refreshed) {
        continue
      }
    }
    if (res.status === 401) {
      await expire(code === 'account_disabled' ? 'disabled' : 'expired')
      throw new V2Error(401, code, code === 'account_disabled' ? 'Tài khoản đã bị khóa.' : 'Phiên đăng nhập đã hết hạn.')
    }
    throw new V2Error(res.status, code, message, details, retryable)
  }
}

// ---- Idempotency-Key theo action + input đóng băng ----
const pendingKeys = new Map<string, string>()

export function newKey(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  const b = new Uint8Array(16)
  crypto.getRandomValues(b)
  b[6] = (b[6] & 0x0f) | 0x40; b[8] = (b[8] & 0x3f) | 0x80
  const h = Array.from(b, x => x.toString(16).padStart(2, '0')).join('')
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`
}

/** Gọi write với key ổn định cho `action`: mất kết nối/5xx giữ key để gửi lại; kết quả hoặc lỗi 4xx thì bỏ key. */
export async function keyed<T>(action: string, call: (key: string) => Promise<T>): Promise<T> {
  let key = pendingKeys.get(action)
  if (!key) { key = newKey(); pendingKeys.set(action, key) }
  try {
    const out = await call(key)
    pendingKeys.delete(action)
    return out
  } catch (e) {
    if (!(e instanceof V2Error) || (e.status >= 400 && e.status < 500 && e.status !== 429)) pendingKeys.delete(action)
    throw e
  }
}

export const clearPendingKeys = () => pendingKeys.clear()

// ---- DTO (theo backend pilot) ----
export type ProfileDTO = { user_id: string; display_name: string; active: boolean; authz_version: number; operations_read?: boolean; models_write?: boolean }
export type QuotaUnit = { base_available: number; bonus_available: number; held: number }
export type QuotaDTO = { server_time: string; next_reset_at: string; cpu: QuotaUnit & { enabled?: boolean }; gpu: QuotaUnit; admission: { allowed: boolean; reason: string | null } }
export type PrepState = { ready: boolean; job_id: string | null }
export type SourceV2 = {
  id: string; core_source_id: string; kind: 'VIDEO' | 'IMAGE_SET'; scope: 'SYSTEM' | 'PRIVATE'; protected: boolean; name: string
  description?: string; published?: boolean
  availability: string; resource_version: number
  metadata: { width?: number | null; height?: number | null; duration_ms?: number | null; n_frames?: number | null; n_units?: number | null }
  dino: PrepState; clip: PrepState; frames: PrepState
}
export type Page<T> = { items: T[]; total: number; next_cursor: string | null }
export type SessionV2 = {
  id: string; name: string; goal: string; target_k: number; pool_source_ids: string[]; state_version: number; n_units: number
  n_kept: number; n_dropped: number; created_at: string; started_from_template_id: string | null
  reference: { dataset_version_ids: string[]; model_id: string | null } | null
}
export type StateV2 = { session_id: string; state_version: number; decision_seq: number; kept_ids: string[]; dropped_ids: string[] }
export type JobState = 'QUEUED' | 'WAITING_RESOURCE' | 'RUNNING' | 'CANCEL_REQUESTED' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED'
export type PhaseTimingV2 = { phase: string; started_at: string; elapsed_ms: number; state: 'running' | 'done' | 'error'; done: number; total: number; cache_hit?: boolean | null }
export type JobProgressV2 = {
  done: number; total: number; fps?: number | null; elapsed_s?: number | null; eta_s?: number | null; detail?: string | null
  stages?: PhaseTimingV2[]
}
export type JobV2 = {
  id: string; kind: string; state: JobState; state_version: number; phase: string; progress: JobProgressV2
  attempt_n: number; waiting_reason: string | null; error: { code: string; message: string; retryable: boolean } | null
  resource_id: string | null; result_ref: string | null; accepted_state_version?: number | null
}
export type ProposalV2 = {
  id: string; n: number; round: number; created_at: string; job_id: string | null
  input: Record<string, unknown> & { recipe: string; config: unknown; config_hash: string; kept_ids: string[]; dropped_ids: string[]; batch_size: number; seed: number }
  picks: { unit_id: string; source_id: string; rank: number; combined: number; breakdown: Record<string, number> }[]
  relaxed: boolean; explain: Record<string, unknown> | null; scores?: Record<string, (string | number)[]> | null
  trace?: import('./api').StrategyTrace[]
  evidence?: import('./api').ProposalDTO['evidence']
  uncertainty_summary?: { signaled_count: number; total_candidates: number; note: string } | null
}
export type SnapshotV2 = {
  id: string; session_id: string; n: number; n_members: number; decision_seq: number; proposal_cutoff_n: number
  members_hash: string; context_hash: string; created_at: string; note: string | null
}
export type SnapshotDetailV2 = SnapshotV2 & { context: Record<string, unknown>; members: { ord: number; unit_id: string; source_id: string; core_source_id: string; source_kind: string }[] }
export type TemplateDTO = {
  id: string; template_key: string; name: string; availability: string; reason_code: string | null; goal: string; target_k: number
  source_ids: string[]; counts: { proposals: number; decisions: number; snapshots: number }
}
export type TemplateDetailDTO = TemplateDTO & { history: { ord: number; kind: string; payload: Record<string, unknown> }[] }
export type ForkReceipt = { session: SessionV2; template_id: string; counts: TemplateDTO['counts']; replayed: boolean }
export type MediaV2 = { id: string | null; state: 'PENDING' | 'READY' | 'FAILED' | 'NOT_PREPARED'; job_id: string | null; url: string | null; expires_at: string | null; sha256: string | null; error: Record<string, unknown> | null }
export type UploadProgressV2 = {
  phase: string; done: number; total: number; fps?: number | null; elapsed_s?: number | null; eta_s?: number | null; detail?: string | null
}
export type UploadV2 = {
  declared_bytes?: number; filename?: string; target_scope?: 'PRIVATE' | 'SYSTEM'; id: string; state: string; validation_job_id: string | null
  source_id: string | null; error: Record<string, unknown> | null; progress?: UploadProgressV2 | null
}
export type UploadCreatedV2 = UploadV2 & { transfer: { method: string; url: string; fields: Record<string, string>; expires_at: string; part_size?: number; part_count?: number; capability?: string } }
export type PreparationV2 = { source_id: string; feature: 'DINO' | 'CLIP' | 'FRAMES'; ready: boolean; job: JobV2 | null }
export type ExportV2 = {
  id: string
  snapshot_id: string
  state: 'REQUESTED' | 'PROCESSING' | 'NEEDS_CONFIRMATION' | 'FINALIZING' | 'READY' | 'FAILED' | 'CANCELLED' | 'EXPIRED'
  state_version: number
  build_job_id: string | null
  finalize_job_id: string | null
  payload_hash: string | null
  review_receipt_id: string | null
  review_expires_at: string | null
  n_members: number
  output_size_bytes: number | null
  error: Record<string, unknown> | null
  created_at: string
  updated_at: string
}
export type ReviewMemberV2 = {
  ordinal: number
  unit_id: string
  output_sha256: string
  output_relpath: string
  preview_url: string | null
}
export type ReviewV2 = {
  source_identity_verified?: boolean
  export_id: string
  review_receipt_id: string
  payload_hash: string
  review_expires_at: string
  n_members: number
  report_url: string | null
  members: ReviewMemberV2[]
}
export type DownloadV2 = {
  export_id: string
  payload_hash: string
  sha256: string
  size_bytes: number
  filename: string
  url: string
  expires_at: string
}
export type ModelV2 = { id: string; name: string; description?: string; published?: boolean; sha256: string; size_bytes: number; task: string; class_map: Record<string, string>
  validation: 'CONTAINER_ONLY' | 'INFERENCE_VERIFIED'; checkpoint_revision: string; provenance: Record<string, unknown>; created_at: string }
export type AdminDependentTemplateV2 = { id: string; template_key: string; name: string; availability: string }
export type AdminSourceV2 = SourceV2 & {
  publishable: boolean
  unpublishable_reasons: string[]
  dependent_template_count: number
  dependent_templates: AdminDependentTemplateV2[]
  created_at?: string | null
}
export type AdminModelV2 = ModelV2 & {
  core_model_id: string
  scope: string
  protected: boolean
  publishable: boolean
  unpublishable_reasons: string[]
}
export type AdminCatalogPatchIn = { name?: string; description?: string; published?: boolean }
export type ModelDeclarationV2 = {
  model_id: string
  dataset_version_id: string
  relation: string
  declared_by: string
  evidence: Record<string, unknown>
  created_at: string
}
export type ModelDetailV2 = ModelV2 & {
  core_model_id: string
  scope: string
  protected: boolean
  runtime_manifest: Record<string, unknown>
  license: Record<string, unknown>
  declarations: ModelDeclarationV2[]
}
export type DatasetVersionV2 = {
  id: string
  core_version_id: string
  n: number
  annotation_format: string
  classes: string[]
  manifest_hash: string
  import_report: Record<string, unknown>
  created_at: string
}
export type DatasetV2 = {
  id: string
  core_dataset_id: string
  source_id: string
  name: string
  description: string
  created_at: string
  versions: DatasetVersionV2[]
}
export type SessionReferenceV2 = {
  session_id: string
  dataset_versions: DatasetVersionV2[]
  model: ModelDetailV2 | null
}
export type DetectionsV2 = { evidence: Record<string, unknown> | null; job: JobV2 | null }
export type AnalysisV2 = { id: string; session_id: string; job_id: string; input_hash: string; input: Record<string, unknown>; decision_seq: number
  state_version: number; proposal_count: number; results: { progress: Record<string, number>; on: 'kept' | number
  results: { type: string; params: Record<string, unknown>; values: Record<string, number> }[]
  views: Record<string, { type: string; data?: unknown; error?: string }> }; created_at: string; current: boolean }
export type AnalysisRequestV2 = { analysis: AnalysisV2 | null; job: JobV2 | null }
export type DeleteReceiptV2 = { id: string; source_id: string; state: string; job_id: string | null; error: Record<string, unknown> | null }

const enc = encodeURIComponent

export const apiV2 = {
  interactiveConfig: () => v2<{ enabled: boolean }>('/api/v2/interactive/config'),
  openInteractive: (id: string, refresh = false) => v2<{ ready: boolean; state_version: number }>(`/api/v2/sessions/${enc(id)}/interactive/open${refresh ? '?refresh=true' : ''}`, { method: 'POST', readOnly: true }),
  interactiveProposal: (id: string, body: Record<string, unknown>) =>
    v2<{ proposal: ProposalV2; batch_id: string; state_version: number; checkpoint: { state: StateV2; proposal: ProposalV2 | null } | null; images: { source_id: string; unit_id: string; variant: 'thumbnail' | 'frame'; url: string; expires_at: string }[] }>(
      `/api/v2/sessions/${enc(id)}/interactive/propose`, { method: 'POST', body, key: body.request_id as string }),
  checkpoint: (id: string, body: Record<string, unknown>) => keyed(`checkpoint:${id}:${JSON.stringify(body)}`, key =>
    v2<{ state: StateV2; proposal: ProposalV2 | null }>(`/api/v2/sessions/${enc(id)}/checkpoint`, { method: 'POST', body, key })),
  me: () => v2<ProfileDTO>('/api/v2/me'),
  quota: () => v2<QuotaDTO>('/api/v2/quota'),
  sources: (cursor?: string, includeUnpublished = false) =>
    v2<Page<SourceV2>>(`/api/v2/sources?limit=100${cursor ? `&cursor=${enc(cursor)}` : ''}${includeUnpublished ? '&include_unpublished=true' : ''}`),
  source: (id: string) => v2<SourceV2>(`/api/v2/sources/${enc(id)}`),
  prepare: (id: string, feature: 'DINO' | 'CLIP' | 'FRAMES') =>
    keyed(`prepare:${id}:${feature}`, key => v2<PreparationV2>(`/api/v2/sources/${enc(id)}/preparations`, { method: 'POST', body: { feature }, key })),
  deleteSource: (id: string, expectedVersion: number) =>
    keyed(`delete:${id}:${expectedVersion}`, key => v2<DeleteReceiptV2>(`/api/v2/sources/${enc(id)}/delete-requests`,
      { method: 'POST', body: { expected_resource_version: expectedVersion }, key })),
  deleteReceipt: (id: string) => v2<DeleteReceiptV2>(`/api/v2/delete-requests/${enc(id)}`),
  sessions: (cursor?: string) => v2<Page<SessionV2>>(`/api/v2/sessions?limit=100${cursor ? `&cursor=${enc(cursor)}` : ''}`),
  session: (id: string) => v2<SessionV2>(`/api/v2/sessions/${enc(id)}`),
  createSession: (body: { name: string; goal: string; target_k: number; pool_source_ids: string[] }) =>
    keyed(`session:${JSON.stringify(body)}`, key => v2<SessionV2>('/api/v2/sessions', { method: 'POST', body, key })),
  state: (id: string) => v2<StateV2>(`/api/v2/sessions/${enc(id)}/state`),
  decide: (id: string, expected: number, changes: Record<string, unknown>[]) =>
    v2<StateV2>(`/api/v2/sessions/${enc(id)}/decisions`, { method: 'POST', body: { changes, expected_state_version: expected } }),
  snapshots: (id: string) => v2<Page<SnapshotV2>>(`/api/v2/sessions/${enc(id)}/snapshots?limit=100`),
  snapshot: (id: string) => v2<SnapshotDetailV2>(`/api/v2/snapshots/${enc(id)}`),
  createSnapshot: (id: string, expected: number, note: string | null) =>
    keyed(`snapshot:${id}:${expected}:${note ?? ''}`, key => v2<SnapshotV2>(`/api/v2/sessions/${enc(id)}/snapshots`,
      { method: 'POST', body: { note, expected_state_version: expected }, key })),
  recipes: () => v2<unknown[]>('/api/v2/recipes'),
  propose: (id: string, body: Record<string, unknown> & { expected_state_version: number }) =>
    keyed(`propose:${id}:${JSON.stringify(body)}`, key => v2<JobV2>(`/api/v2/sessions/${enc(id)}/proposals`, { method: 'POST', body, key })),
  proposals: (id: string) => v2<ProposalV2[]>(`/api/v2/sessions/${enc(id)}/proposals`),
  proposal: (id: string, n: number) => v2<ProposalV2>(`/api/v2/sessions/${enc(id)}/proposals/${n}`),
  jobs: (sid?: string, cursor?: string) => v2<Page<JobV2>>(`/api/v2/jobs?limit=100${sid ? `&kind=PROPOSE&resource_id=${enc(sid)}` : ''}${cursor ? `&cursor=${enc(cursor)}` : ''}`),
  job: (id: string) => v2<JobV2>(`/api/v2/jobs/${enc(id)}`),
  jobResult: (id: string) => v2<Record<string, unknown>>(`/api/v2/jobs/${enc(id)}/result`),
  cancelJob: (id: string, expected: number) => v2<JobV2>(`/api/v2/jobs/${enc(id)}/cancel`, { method: 'POST', body: { expected_state_version: expected } }),
  retryJob: (id: string) => keyed(`retry:${id}`, key => v2<JobV2>(`/api/v2/jobs/${enc(id)}/retry`, { method: 'POST', key })),
  requestMedia: (body: { source_id: string; unit_id: string; variant: 'thumbnail' | 'frame' | 'context'; max_side?: number }) =>
    v2<MediaV2>('/api/v2/media/requests', { method: 'POST', body }),
  requestMediaBatch: (items: { source_id: string; unit_id: string; variant: 'thumbnail' | 'frame'; max_side?: number }[]) =>
    v2<MediaV2[]>('/api/v2/media/batch', { method: 'POST', body: { items }, readOnly: true }),
  media: (id: string) => v2<MediaV2>(`/api/v2/media/requests/${enc(id)}`),
  uploads: () => v2<UploadV2[]>('/api/v2/uploads'),
  createUpload: (filename: string, declaredBytes: number, action: string, targetScope: 'PRIVATE' | 'SYSTEM' = 'PRIVATE') =>
    keyed(`upload:${action}:${targetScope}`, key => v2<UploadCreatedV2>('/api/v2/uploads', {
      method: 'POST',
      body: { filename, declared_bytes: declaredBytes, ...(targetScope === 'SYSTEM' ? { target_scope: 'SYSTEM' } : {}) },
      key,
    })),
  startMultipart: (id: string) => v2<{ part_size: number; part_count: number; assembled: boolean; parts: { part_number: number; size_bytes: number }[] }>(`/api/v2/uploads/${enc(id)}/multipart`, { method: 'POST', key: `multipart:${id}` }),
  multipartParts: (id: string, numbers: number[]) => v2<{ parts: { part_number: number; url: string }[] }>(`/api/v2/uploads/${enc(id)}/multipart/parts`, { method: 'POST', body: { part_numbers: numbers }, readOnly: true }),
  completeUpload: (id: string) => v2<UploadV2>(`/api/v2/uploads/${enc(id)}/complete`, { method: 'POST' }),
  abortUpload: (id: string) => v2<UploadV2>(`/api/v2/uploads/${enc(id)}/abort`, { method: 'POST' }),
  upload: (id: string) => v2<UploadV2>(`/api/v2/uploads/${enc(id)}`),
  adminSources: (cursor?: string) => v2<Page<AdminSourceV2>>(`/api/v2/admin/sources?limit=50${cursor ? `&cursor=${enc(cursor)}` : ''}`),
  referenceAsSample: (id: string) => keyed(`reference-sample:${id}`, key =>
    v2<{ source_id: string; existing: boolean }>(`/api/v2/admin/sources/${enc(id)}/reference-as-sample`, { method: 'POST', key })),
  updateAdminSource: (id: string, body: AdminCatalogPatchIn) => v2<AdminSourceV2>(`/api/v2/admin/sources/${enc(id)}`, { method: 'PATCH', body }),
  adminModels: (cursor?: string) => v2<Page<AdminModelV2>>(`/api/v2/admin/models?limit=50${cursor ? `&cursor=${enc(cursor)}` : ''}`),
  updateAdminModel: (id: string, body: AdminCatalogPatchIn) => v2<AdminModelV2>(`/api/v2/admin/models/${enc(id)}`, { method: 'PATCH', body }),
  models: (includeUnpublished = false) => v2<ModelV2[]>(`/api/v2/models${includeUnpublished ? '?include_unpublished=true' : ''}`),
  createModelUpload: (filename: string, declaredBytes: number) =>
    v2<{ upload_id: string; transfer: UploadCreatedV2['transfer'] }>('/api/v2/admin/models/uploads', { method: 'POST', body: { filename, declared_bytes: declaredBytes } }),
  completeModelUpload: (id: string, body: { filename: string; name: string; train_dataset: string }) =>
    v2<JobV2>(`/api/v2/admin/models/uploads/${enc(id)}/complete`, { method: 'POST', body }),
  model: (id: string) => v2<ModelDetailV2>(`/api/v2/models/${enc(id)}`),
  datasets: () => v2<DatasetV2[]>('/api/v2/datasets'),
  dataset: (id: string) => v2<DatasetV2>(`/api/v2/datasets/${enc(id)}`),
  sessionReference: (sid: string) => v2<SessionReferenceV2>(`/api/v2/sessions/${enc(sid)}/reference`),
  updateSessionReference: (sid: string, body: { expected_state_version: number; reference: { dataset_version_ids: string[]; model_id?: string | null } | null }) =>
    v2<SessionV2>(`/api/v2/sessions/${enc(sid)}/reference`, { method: 'PATCH', body }),
  requestDetections: (sid: string, n: number, modelId: string) =>
    keyed(`detect:${sid}:${n}:${modelId}`, key => v2<DetectionsV2>(`/api/v2/sessions/${enc(sid)}/proposals/${n}/detections`,
      { method: 'POST', body: { model_id: modelId }, key })),
  detections: (sid: string, n: number, modelId: string) =>
    v2<Record<string, unknown>>(`/api/v2/sessions/${enc(sid)}/proposals/${n}/detections?model_id=${enc(modelId)}`),
  requestAnalysis: (sid: string, body: { expected_state_version: number; metrics?: unknown[] | null; on?: 'kept' | number }) =>
    keyed(`analysis:${sid}:${JSON.stringify(body)}`, key => v2<AnalysisRequestV2>(`/api/v2/sessions/${enc(sid)}/analysis`, { method: 'POST', body, key })),
  analysis: (sid: string, inputHash?: string) =>
    v2<AnalysisV2>(`/api/v2/sessions/${enc(sid)}/analysis${inputHash ? `?input_hash=${enc(inputHash)}` : ''}`),
  templates: () => v2<TemplateDTO[]>('/api/v2/demo-templates'),
  template: (id: string) => v2<TemplateDetailDTO>(`/api/v2/demo-templates/${enc(id)}`),
  fork: (id: string, name?: string) =>
    keyed(`fork:${id}:${name ?? ''}`, key => v2<ForkReceipt>(`/api/v2/demo-templates/${enc(id)}/fork`,
      { method: 'POST', body: name?.trim() ? { name: name.trim() } : {}, key })),
  createExport: (snapshotId: string) =>
    keyed(`export:${snapshotId}`, key => v2<ExportV2>(`/api/v2/snapshots/${enc(snapshotId)}/exports`, { method: 'POST', key })),
  export: (id: string) => v2<ExportV2>(`/api/v2/exports/${enc(id)}`),
  exports: (sid: string, cursor?: string) => v2<Page<ExportV2>>(`/api/v2/sessions/${enc(sid)}/exports${cursor ? `?cursor=${enc(cursor)}` : ''}`),
  exportReview: (id: string) => v2<ReviewV2>(`/api/v2/exports/${enc(id)}/review`),
  confirmExport: (id: string, payloadHash: string, reviewReceiptId: string) =>
    keyed(`confirm-export:${id}`, key => v2<ExportV2>(`/api/v2/exports/${enc(id)}/confirm`, {
      method: 'POST', body: { payload_hash: payloadHash, review_receipt_id: reviewReceiptId }, key,
    })),
  exportDownload: (id: string) => v2<DownloadV2>(`/api/v2/exports/${enc(id)}/download`),
}

/** Gom mọi trang của một danh sách có `next_cursor`. */
export async function all<T>(page: (cursor?: string) => Promise<Page<T>>): Promise<T[]> {
  const out: T[] = []
  let cursor: string | undefined
  for (let i = 0; i < 50; i++) {
    const p = await page(cursor)
    out.push(...p.items)
    if (!p.next_cursor) break
    cursor = p.next_cursor
  }
  return out
}
