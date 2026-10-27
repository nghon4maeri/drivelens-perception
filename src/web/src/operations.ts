/** Read-only operations DTOs and upload telemetry client (CONTRACTS §21.19, §21.36). */
import { v2 } from './api_v2'

export type Timing = { source: string; stage: string; observed_at: string; request_id?: string; job_id?: string; data: {
  wall_ms?: number; process_cpu_ms?: number; queue_ms?: number; bytes?: number; status?: number; outcome?: string; error_type?: string; error_code?: string; cache_hit?: boolean } }
export type ResourceRow = {
  id: string; display_name: string; state: string; kind?: string; phase?: string; done?: number; total?: number
  declared_bytes?: number; filename?: string; target_scope?: 'PRIVATE' | 'SYSTEM'; source_id?: string | null
  resource_id?: string | null; attempt_n?: number; queue_wait_ms?: number | null; run_ms?: number | null
  sample_copy?: boolean
  created_at: string; updated_at: string; error_code?: string
}
export type JobCounts = {
  queued: number; waiting_resource: number; running: number; cancel_requested: number; failed_24h: number; succeeded_24h: number
}
export type Operations = { server_time: string; trace_enabled: boolean; retention_days: number; daily_cap: number;
  heartbeats: { component: string; observed_at: string; stale: boolean; details: { dropped: number; flush_failures: number; pending: number } }[];
  slots: { slot_key: string; kind: string; state: string; attempt_id?: string }[];
  gpu_budget: { observed_at: string; remaining_ms: number; external_session_active: boolean; source: string }[];
  job_counts?: JobCounts;
  uploads: ResourceRow[]; jobs: ResourceRow[]; errors: Timing[];
  quota: { display_name: string; resource: string; available_ms: number; held_ms: number }[];
  bonus: { display_name: string; resource: string; available_ms: number; held_ms: number }[] }
export type Trace = { id: string; kind: string; resource: ResourceRow | null; events: Timing[]; truncated: boolean; note: string;
  attempts: { id: string; job_id: string; state: string; usage_state: string; cleanup_done: boolean; launch_state: string;
    cpu_ms: string | null; gpu_ms: string | null }[] }
export type BrowserTiming = { id: string; at: string; stage: string; wall_ms: number; bytes: number; outcome: string }
export const operations = () => v2<Operations>('/api/v2/operations')
export const operationTrace = (kind: string, id: string) => v2<Trace>(`/api/v2/operations/traces/${kind}/${encodeURIComponent(id)}`)
export type LogSource = 'api' | 'worker' | 'gpu' | 'browser'
export type LogPage = { items: (Timing & { id: string; received_at: string; upload_id?: string | null })[];
  next_cursor: string | null; window_start: string; window_end: string; retention_days: number; coverage: 'partial' }
export function operationLogs(params: {
  hours: number; source?: LogSource; errorsOnly: boolean; cursor?: string
  uploadId?: string; jobId?: string; requestId?: string
}) {
  const q = new URLSearchParams({ hours: String(params.hours), errors_only: String(params.errorsOnly), limit: '50' })
  if (params.source) q.set('source', params.source)
  if (params.cursor) q.set('cursor', params.cursor)
  if (params.uploadId?.trim()) q.set('upload_id', params.uploadId.trim())
  if (params.jobId?.trim()) q.set('job_id', params.jobId.trim())
  if (params.requestId?.trim()) q.set('request_id', params.requestId.trim())
  return v2<LogPage>(`/api/v2/operations/logs?${q}`)
}
export const sendUploadTimings = (id: string, events: BrowserTiming[]) => v2(`/api/v2/uploads/${id}/timings`, { method: 'POST', body: { events } })
