/** Client HTTP + type DTO viết tay theo architecture §5.3. Bản local gọi /api/v1 (lỗi {"error": {code, message}}, §5.1);
 * chế độ pilot (`VITE_RAV_MODE=pilot`, CONTRACTS §21.16) dùng `pilotApi` cùng interface, chỉ gọi /api/v2. */
import { ApiError } from './api_error'
import { PILOT } from './mode'
import { pilotApi } from './pilot_api'
import type { PhaseTimingV2 } from './api_v2'

export type PoolFilter = { video_ids?: string[] }
export type PrepareStatus = { state: 'queued' | 'running' | 'done' | 'error'; stage: string; done: number; total: number; started_at?: string | null; error?: string | null; job_id?: string | null; result_key?: string | null }
export type VideoDTO = {
  id: string; name: string; duration_s: number; n_sessions: number; prepared: boolean; media_url: string
  thumb_url: string                // frame giữa video, 320 px (#57)
  width: number; height: number; fps: number; size_bytes: number; added_at?: string | null   // thông tin file (#59)
  prepare?: PrepareStatus | null   // job P1 nền (#56)
  clip_prepared: boolean; clip_prepare?: PrepareStatus | null
}
export type UnitDTO = { id: string; video_id: string; t0: number; t1: number; thumb_url: string; frame_url: string }
export type ModelDTO = {
  id: string; name: string; filename: string; sha256: string; size_bytes: number; created_at: string
  format: 'pytorch_zip'; available: boolean; validation: 'container_only'; inference_ready: false
}
export type SessionDTO = {
  id: string; pool: PoolFilter; name?: string | null; created_at: string; target_k: number; goal: string
  n_units: number; n_kept: number; n_dropped: number; n_proposals: number
  reference: { dataset_version_ids: string[]; model_id?: string | null }
  reference_model: ModelDTO | null
  latest_snapshot: { id: string; n: number; is_current: boolean } | null
}
export const DEFAULT_RECIPE = 'diversity'
export const RECIPE_LABELS: Record<string, string> = { diversity: 'Đa dạng cảnh', text_diversity: 'Tìm theo câu (CLIP)',
  reference_diversity: 'Bổ sung so dataset train', model_guided: 'Bổ sung + YOLO consistency', text_model_guided: 'CLIP + dataset train + YOLO',
  perception_instability: 'Thiếu ổn định perception (TTA Consistency)' }
export type LabelStatus = 'none' | 'labeled' | 'empty' | 'missing' | 'invalid' | 'conflict'
export type DatasetVersionDTO = {
  id: string; dataset_id: string; n: number; created_at: string; folder: string
  annotation_format: 'none' | 'yolo'; classes: string[] | null; n_images: number
  label_counts: Record<string, number>; class_counts: Record<string, number>
  prepared: boolean; prepare: PrepareStatus | null; n_sessions_as_reference: number
}
export type DatasetDTO = { id: string; name: string; description: string | null; created_at: string; versions: DatasetVersionDTO[] }
export type ImportFolderDTO = { folder: string; n_images_hint: number; detected: 'none' | 'yolo'; classes: string[] | null }
export type DatasetSampleDTO = { sample_id: string; key: string; relpath: string; width: number; height: number; label_status: LabelStatus; label_counts: Record<string, number> | null; thumb_url: string }
export type SnapshotDTO = { id: string; session_id: string; n: number; created_at: string; created_by: string; note: string | null; n_members: number; members_hash: string; context_hash: string; decision_seq: number; is_current: boolean; decisions_after: number }
export type SnapshotDetailDTO = SnapshotDTO & { context: Record<string, unknown>; members: { ord: number; unit_id: string; source_kind: 'video' | 'image_set'; source_id: string; provenance: { video?: { name?: string; sha256: string }; t0?: number; frame_idx?: number; decision?: { decided_at: string } } }[] }
export const datasetBusy = (v: DatasetVersionDTO) => v.prepare?.state === 'queued' || v.prepare?.state === 'running'
export const datasetReady = (v: DatasetVersionDTO) => v.prepared && !datasetBusy(v) && v.prepare?.state !== 'error'
export type ComponentConfig = { type: string; params?: Record<string, unknown> }
export type RecipeConfig = {
  name: string; description?: string; reference: ComponentConfig
  objectives: (ComponentConfig & { strength: number })[]; combiner: ComponentConfig; selector: ComponentConfig
  features?: Record<string, ComponentConfig>
  candidate_stage?: { objectives: (ComponentConfig & { strength: number })[]; combiner: ComponentConfig; selector: ComponentConfig; limit: number } | null
}
export type JsonSchema = {
  type?: string; title?: string; default?: unknown; enum?: unknown[]; minimum?: number; maximum?: number
  properties?: Record<string, JsonSchema>; items?: JsonSchema; anyOf?: JsonSchema[]; description?: string
}
export type StrategyMetadata = { name: string; version: string; title: string; steps: string[]; inputs: { model: boolean; model_optional?: boolean; reference_dataset: boolean }; parameters_schema: JsonSchema; defaults: Record<string, unknown> }
export type RecipeDTO = {
  strategy?: StrategyMetadata
  name: string; description: string; config: RecipeConfig | null
  params_schema: { reference: JsonSchema; objectives: JsonSchema[]; combiner: JsonSchema; selector: JsonSchema; candidate_objectives?: JsonSchema[] }
  components: Record<string, string>
  background: boolean; requires_training: boolean; requires_model: boolean
  available?: boolean; unavailable_reason?: string | null   // pilot: recipe cần dataset train chưa dùng được (#97)
}
export type ComponentDTO = { kind: string; name: string; version: string; doc: string; params_schema: JsonSchema }
export type Action = 'keep' | 'drop' | 'clear'
export type DecisionDTO = {
  unit_id: string; action: Action; actor: string; goal: string; proposal_n?: number | null
  shown_at?: string | null; decided_at: string; reason?: string | null; confidence?: number | null
}
export type ReviewStateDTO = { kept_ids: string[]; dropped_ids: string[]; history: DecisionDTO[] }
export type Change = { unit_id: string; action: Action; proposal_n?: number; shown_at?: string; reason?: string }
export type ProposeRequest = { changes?: Record<string, unknown>[]; recipe: string; config?: RecipeConfig; batch_size: number; seed?: number; return_scores?: boolean; model_id?: string; strategy_params?: Record<string, unknown> }
export type PickDTO = { unit_id: string; rank: number; combined: number; breakdown: Record<string, number> }
export type ProposalDTO = {
  temporary?: boolean; batch_id?: string
  n: number; round: number; created_at: string
  input: { recipe: string; config: RecipeConfig | null; strategy_version?: string; config_hash: string; kept_ids: string[]; dropped_ids: string[]
           strategy_params?: Record<string, unknown>; model_id?: string; batch_size: number; seed: number; fields_used: string[]; execution?: Record<string, unknown> | null }
  picks: PickDTO[]; relaxed: boolean
  trace?: StrategyTrace[]
  strategy_trace?: StrategyTrace[]
  evidence?: Record<string, ProposalEvidence[]>
  uncertainty_summary?: { signaled_count: number; total_candidates: number; note: string }
  scores?: Record<string, (string | number)[]> | null
}
export type ProposalJobDTO = { id: string; session_id: string; state: 'queued' | 'running' | 'done' | 'error'; stage: string; done: number; total: number; error: string | null; proposal_n: number | null; created_at: string; timings?: PhaseTimingV2[] }
export type ProposalEvidence =
  | { kind: 'nearest'; label: string; source_id: string; unit_id: string; similarity: number; field: string; image_url: string }
  | { kind: 'scalar'; label: string; value: number | null; method?: string; observed?: boolean; matching?: Record<string, unknown>; explanation?: string }
  | { kind: 'detections'; label: string; boxes: Detection[]; comparison?: boolean }
export type On = 'kept' | number
export type MetricsDTO = {
  progress: { target_k: number; kept: number; dropped: number; proposals: number }
  on: On
  results: { type: string; params: Record<string, unknown>; values: Record<string, number> }[]
}
export type ViewSpec = { type: string; data: any }
/** Bằng chứng hiển thị (CONTRACTS §8.0, #59): null = chưa có nguồn → UI ẩn. box chuẩn hoá 0–1 [x1, y1, x2, y2]. */
export type Detection = { label: string; score: number; box: [number, number, number, number]; source: string }
export type YoloBatchDTO = {
  session_id: string; proposal_n: number; model_id: string; model_name: string; model_sha256: string
  engine_version: string; params: { imgsz: number; conf: number; iou: number; max_det: number; device: string }
  created_at: string; total_images: number; items: { unit_id: string; detections: Detection[] }[]; cached: boolean
}
export type TelemetrySample = { t: number; speed_kmh?: number | null; brake?: boolean | null; steer_deg?: number | null; lat?: number | null; lon?: number | null }
/** Giải thích cấp đợt (#61) — chỉ dữ liệu thật của đề xuất đã lưu. */
export type ProposalExplainDTO = {
  n: number; recipe: string; description: string; goal: string; uses_goal: boolean
  semantic_query?: { texts: string[]; negative: string[]; min_similarity: number; negative_margin: number } | null
  components: { role: string; type: string; doc: string }[]
  n_picks: number; n_kept_at_proposal: number; relaxed: boolean
  similarity_field: string; field_used_by_recipe: boolean
  nearest_kept: { unit_id: string; kept_unit_id: string; similarity: number }[]
  spread: { video_id: string; n: number; t_min: number; t_max: number }[]
}
export type EvidenceDTO = { unit_id: string; detections: Detection[] | null; tags: Record<string, string> | null; telemetry: TelemetrySample[] | null }
export type ExportDTO = {
  id: string; session_id: string; session_name?: string | null; goal: string; format: string; n_frames: number; created_at: string
  download_url: string
}

export { ApiError }

async function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const init: RequestInit = { method }
  if (body instanceof FormData) init.body = body
  else if (body !== undefined) { init.body = JSON.stringify(body); init.headers = { 'Content-Type': 'application/json' } }
  const res = await fetch(path, init)
  const data = await res.json().catch(() => null)
  if (!res.ok) throw new ApiError(data?.error?.code ?? 'http_error', data?.error?.message ?? `HTTP ${res.status}`, data?.error?.sources ?? [])
  return data as T
}

const S = (sid: string) => `/api/v1/sessions/${encodeURIComponent(sid)}`

const localApi = {
  queueProposal: (sid: string, body: ProposeRequest) => request<ProposalJobDTO>(`${S(sid)}/proposal-jobs`, 'POST', body),
  proposalJobs: (sid: string) => request<ProposalJobDTO[]>(`${S(sid)}/proposal-jobs`),
  models: () => request<ModelDTO[]>('/api/v1/models'),
  runBatchYolo: (sid: string, n: number, model_id: string) => request<YoloBatchDTO>(`${S(sid)}/proposals/${n}/yolo`, 'POST', { model_id }),
  batchYolo: (sid: string, n: number, modelId: string) => request<YoloBatchDTO | null>(`${S(sid)}/proposals/${n}/yolo?model_id=${encodeURIComponent(modelId)}`),
  uploadModel: (file: File, name: string) => { const f = new FormData(); f.append('file', file); if (name.trim()) f.append('name', name.trim()); return request<{ model: ModelDTO; created: boolean }>('/api/v1/models', 'POST', f) },
  verifyModel: (id: string) => request<{ ok: true }>(`/api/v1/models/${encodeURIComponent(id)}/verify`, 'POST'),
  videos: () => request<VideoDTO[]>('/api/v1/videos'),
  upload: (file: File) => { const f = new FormData(); f.append('file', file); return request<VideoDTO>('/api/v1/videos', 'POST', f) },
  queuePrepare: (videoId: string) => request<VideoDTO>(`/api/v1/videos/${encodeURIComponent(videoId)}/prepare`, 'POST'),
  queueClip: (videoId: string) => request<VideoDTO>(`/api/v1/videos/${encodeURIComponent(videoId)}/prepare-clip`, 'POST'),
  sessions: () => request<SessionDTO[]>('/api/v1/sessions'),
  createSession: (body: { pool: PoolFilter; goal: string; target_k: number; name?: string; reference?: { dataset_version_ids: string[]; model_id?: string; strategy_params?: Record<string, unknown> } }) =>
    request<SessionDTO>('/api/v1/sessions', 'POST', body),
  session: (sid: string) => request<SessionDTO>(S(sid)),
  addVideos: (sid: string, video_ids: string[]) => request<SessionDTO>(`${S(sid)}/videos`, 'POST', { video_ids }),
  units: (sid: string) => request<UnitDTO[]>(`${S(sid)}/units`),
  recipes: () => request<RecipeDTO[]>('/api/v1/recipes'),
  component: (kind: string, name: string) => request<ComponentDTO>(`/api/v1/components/${kind}/${name}`),
  state: (sid: string) => request<ReviewStateDTO>(`${S(sid)}/state`),
  updateState: (sid: string, changes: Change[]) => request<ReviewStateDTO>(`${S(sid)}/state`, 'PATCH', { changes }),
  propose: (sid: string, body: ProposeRequest) => request<ProposalDTO>(`${S(sid)}/proposals`, 'POST', body),
  proposals: (sid: string) => request<ProposalDTO[]>(`${S(sid)}/proposals`),
  proposal: (sid: string, n: number) => request<ProposalDTO>(`${S(sid)}/proposals/${n}?return_scores=true`),
  /** Tham số view đi qua query; mảng → khoá lặp (architecture §5.2). */
  view: (sid: string, name: string, params: Record<string, unknown>) => {
    const q = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) for (const x of Array.isArray(v) ? v : [v]) q.append(k, String(x))
    return request<ViewSpec>(`${S(sid)}/views/${name}${q.size ? `?${q}` : ''}`)
  },
  metrics: (sid: string) => request<MetricsDTO>(`${S(sid)}/metrics`),
  metricsCustom: (sid: string, body: { metrics?: ComponentConfig[]; on?: On }) => request<MetricsDTO>(`${S(sid)}/metrics`, 'POST', body),
  explain: (sid: string, n: number) => request<ProposalExplainDTO>(`${S(sid)}/proposals/${n}/explain`),
  evidence: (sid: string, unit_ids: string[]) => request<EvidenceDTO[]>(`${S(sid)}/evidence`, 'POST', { unit_ids }),
  export: (sid: string) => request<ExportDTO & { path: string }>(`${S(sid)}/export`, 'POST'),
  exports: (sid?: string) => request<ExportDTO[]>(`/api/v1/exports${sid ? `?session_id=${encodeURIComponent(sid)}` : ''}`),
  datasets: () => request<DatasetDTO[]>('/api/v1/datasets'),
  importFolders: () => request<ImportFolderDTO[]>('/api/v1/datasets/import-folders'),
  importDataset: (body: { folder: string; name?: string; dataset_id?: string; annotation_format: 'auto' | 'none' | 'yolo' }) => request<{ dataset: DatasetDTO; version: DatasetVersionDTO; created: boolean; report: Record<string, unknown> }>('/api/v1/datasets/import', 'POST', body),
  datasetVersion: (id: string) => request<DatasetVersionDTO & { report: Record<string, unknown> }>(`/api/v1/datasets/versions/${encodeURIComponent(id)}`),
  datasetSamples: (id: string, offset: number, status: string) => request<{ total: number; items: DatasetSampleDTO[] }>(`/api/v1/datasets/versions/${encodeURIComponent(id)}/samples?offset=${offset}&limit=24${status ? `&label_status=${encodeURIComponent(status)}` : ''}`),
  prepareDataset: (id: string) => request<DatasetVersionDTO>(`/api/v1/datasets/versions/${encodeURIComponent(id)}/prepare`, 'POST'),
  verifyDataset: (id: string) => request<{ ok: boolean; missing: string[]; changed: string[] }>(`/api/v1/datasets/versions/${encodeURIComponent(id)}/verify`, 'POST'),
  snapshots: (sid: string) => request<SnapshotDTO[]>(`${S(sid)}/snapshots`),
  createSnapshot: (sid: string, note: string) => request<SnapshotDTO & { created: boolean }>(`${S(sid)}/snapshots`, 'POST', { note: note.trim() || null }),
  snapshot: (id: string) => request<SnapshotDetailDTO>(`/api/v1/snapshots/${encodeURIComponent(id)}`),
  downloadSnapshot: async (s: SnapshotDTO) => {
    await request<{ ok: boolean }>(`/api/v1/snapshots/${encodeURIComponent(s.id)}/verify`, 'POST')
    const link = document.createElement('a')
    link.href = `/api/v1/snapshots/${encodeURIComponent(s.id)}/download`
    link.download = `drivelens_snapshot_${s.session_id.slice(0, 8)}_${s.n}.zip`
    document.body.append(link); link.click(); link.remove()
  },
}

export type Api = typeof localApi
/** Interface chung cho hook/component; pilot ánh xạ DTO v2 sang dạng này (pilot_api.ts). */
export const api: Api = PILOT ? pilotApi : localApi

export const errorText = (e: unknown) => e instanceof ApiError ? `${e.message} (${e.code})${e.sources.length ? ' · ' + e.sources.map(s => `${s.source_id}: ${s.problem === 'missing' ? 'thiếu nguồn' : 'nguồn đã đổi'} (${s.n_samples} mẫu)`).join('; ') : ''}` : String(e)

export type StrategyTrace = { step: string; count?: number; pool_count?: number; shortlist_count?: number; observed_count?: number; requested?: number; note?: string; branches?: { id: string; query: string; kind: string }[]; eligible_counts?: number[]; kept_counts?: number[]; selected_counts?: number[] }
