/**
 * Quản lý tiến trình upload cấp ứng dụng (CONTRACTS §21.35):
 * - Giữ danh sách upload (`entries`) ngoài vòng đời màn Kho dữ liệu để chuyển `#videos` ↔ `#s=...` không mất trạng thái.
 * - `recoverUploads`: gọi `GET /api/v2/uploads` sau khi vào app/reload; tự nối lại vòng poll cho `VALIDATING`,
 *   và đánh dấu `needs_file` ("Chọn lại file để tiếp tục") cho `CREATED` chưa truyền xong.
 * - `startUpload`: tạo mới hoặc gắn file vào mục `needs_file` trùng tên/kích thước, cập nhật tiến trình và gọi callback khi READY.
 */
import { useEffect, useState } from 'react'
import { authGeneration } from './auth'
import { apiV2, newKey, StaleResponseError, type UploadProgressV2, type UploadV2 } from './api_v2'
import { clearUploadResume, createOrResume, transferFile } from './upload_file'
import { sendUploadTimings, type BrowserTiming } from './operations'

export type UploadStatus = 'running' | 'needs_file' | 'done' | 'error'

export type UploadEntry = {
  key: string
  uploadId?: string
  name: string
  declaredBytes?: number
  targetScope?: 'PRIVATE' | 'SYSTEM'
  progress: number
  state: string
  status: UploadStatus
  error?: string
}

let entries: UploadEntry[] = []
const listeners = new Set<() => void>()
const activePolls = new Set<string>()
const readyCallbacks = new Set<() => void>()
let pollIntervalMs = 2000

function emit() {
  for (const fn of listeners) fn()
}

export function getUploads(): UploadEntry[] {
  return entries
}

export function subscribeUploads(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

export function onUploadReady(cb: () => void): () => void {
  readyCallbacks.add(cb)
  return () => { readyCallbacks.delete(cb) }
}

function notifyReady() {
  for (const cb of readyCallbacks) {
    try { cb() } catch { /* ignore */ }
  }
}

export function setUploadPollIntervalForTest(ms: number) {
  pollIntervalMs = ms
}

export function resetUploadManager() {
  entries = []
  activePolls.clear()
  pollIntervalMs = 2000
  emit()
}

function updateEntry(key: string, patch: Partial<UploadEntry>) {
  entries = entries.map(e => (e.key === key ? { ...e, ...patch } : e))
  emit()
}

export function dismissUpload(key: string) {
  entries = entries.filter(e => e.key !== key)
  emit()
}

export async function cancelNeedsFileUpload(key: string): Promise<void> {
  const item = entries.find(e => e.key === key)
  if (!item) return
  if (item.uploadId) {
    try { await apiV2.abortUpload(item.uploadId) } catch { /* ignore */ }
  }
  dismissUpload(key)
}

export function fmtEta(s: number): string {
  if (s < 60) return `${Math.round(s)}s`
  const m = Math.floor(s / 60)
  const rem = Math.round(s % 60)
  return rem > 0 ? `${m}m ${rem}s` : `${m}m`
}

export function formatUploadProgress(p?: UploadProgressV2 | null): { progress: number; state: string } {
  if (!p) return { progress: 1, state: 'đang tiền xử lý video' }
  let progFraction = 1
  let phaseText = 'đang tiền xử lý video'
  if (p.phase === 'DOWNLOADING') {
    phaseText = 'đang tải video'
    progFraction = 0.1
  } else if (p.phase === 'PROBING') {
    phaseText = 'đang kiểm tra'
    progFraction = 0.2
  } else if (p.phase === 'SAVING_CANONICAL') {
    phaseText = 'đang lưu video'
    progFraction = 0.3
  } else if (p.phase === 'EXTRACTING_FRAMES') {
    const frac = p.total > 0 ? p.done / p.total : 0
    progFraction = 0.3 + 0.68 * frac
    const fpsText = p.fps ? ` · ${p.fps.toFixed(1)} fps` : ''
    const etaText = p.eta_s ? ` · còn ~${fmtEta(p.eta_s)}` : ''
    phaseText = `đang trích xuất ảnh ${p.done}/${p.total}${fpsText}${etaText}`
  } else if (p.phase === 'FINALIZING') {
    phaseText = 'đang hoàn tất'
    progFraction = 0.98
  }
  return { progress: progFraction, state: phaseText }
}

async function pollValidating(uploadId: string, entryKey: string, initial?: UploadV2): Promise<UploadV2> {
  if (activePolls.has(uploadId)) {
    return initial ?? { id: uploadId, state: 'VALIDATING', validation_job_id: null, source_id: null, error: null }
  }
  activePolls.add(uploadId)
  const generation = authGeneration()
  let u: UploadV2 = initial ?? (await apiV2.upload(uploadId))
  try {
    if (u.progress) {
      updateEntry(entryKey, { ...formatUploadProgress(u.progress), status: 'running' })
    }
    while (!['READY', 'FAILED', 'ABORTED'].includes(u.state)) {
      await new Promise(r => setTimeout(r, pollIntervalMs))
      if (generation !== authGeneration()) throw new StaleResponseError()
      u = await apiV2.upload(uploadId)
      if (u.progress) {
        updateEntry(entryKey, { ...formatUploadProgress(u.progress), status: 'running' })
      }
    }
    if (u.state === 'READY') {
      updateEntry(entryKey, { progress: 1, state: 'xong', status: 'done', error: undefined })
      notifyReady()
    } else {
      const msg = String((u.error as { message?: string } | null)?.message ?? u.state)
      updateEntry(entryKey, { state: 'lỗi', status: 'error', error: msg })
    }
    return u
  } finally {
    activePolls.delete(uploadId)
  }
}

/** Khôi phục danh sách upload từ server sau khi tải lại trang (§21.35). */
export async function recoverUploads(): Promise<UploadEntry[]> {
  const list = await apiV2.uploads()
  for (const u of list) {
    const existing = entries.find(e => e.uploadId === u.id)
    const name = u.filename || existing?.name || 'video.mp4'
    const targetScope = u.target_scope ?? existing?.targetScope ?? 'PRIVATE'
    if (u.state === 'VALIDATING') {
      const { progress, state } = formatUploadProgress(u.progress)
      const key = existing?.key ?? `upload:${u.id}`
      if (existing) {
        updateEntry(key, { name, declaredBytes: u.declared_bytes, targetScope, progress, state, status: 'running', error: undefined })
      } else {
        entries = [...entries, { key, uploadId: u.id, name, declaredBytes: u.declared_bytes, targetScope, progress, state, status: 'running' }]
        emit()
      }
      void pollValidating(u.id, key, u).catch(e => {
        if (!(e instanceof StaleResponseError)) {
          updateEntry(key, { state: 'lỗi', status: 'error', error: e instanceof Error ? e.message : String(e) })
        }
      })
    } else if (u.state === 'CREATED') {
      const key = existing?.key ?? `upload:${u.id}`
      if (!existing) {
        entries = [
          ...entries,
          {
            key,
            uploadId: u.id,
            name,
            declaredBytes: u.declared_bytes,
            targetScope,
            progress: 0,
            state: 'Chọn lại file để tiếp tục',
            status: 'needs_file',
          },
        ]
        emit()
      }
    } else if (u.state === 'FAILED') {
      const key = existing?.key ?? `upload:${u.id}`
      const msg = String((u.error as { message?: string } | null)?.message ?? 'Upload thất bại')
      if (!existing) {
        entries = [
          ...entries,
          { key, uploadId: u.id, name, declaredBytes: u.declared_bytes, targetScope, progress: 0, state: 'lỗi', status: 'error', error: msg },
        ]
        emit()
      }
    }
  }
  return entries
}

/** Bắt đầu hoặc tiếp tục upload một file ở cấp ứng dụng (§21.35, §21.36). */
export async function startUpload(file: File, targetKey?: string, targetScope?: 'PRIVATE' | 'SYSTEM'): Promise<void> {
  const totalStart = performance.now()
  const spans: BrowserTiming[] = []
  let resumeKey: string | undefined
  let uploadId: string | undefined
  let jobId: string | undefined
  let stage = 'browser.create'
  let stageStart = totalStart
  const record = (outcome = 'ok') => {
    const wall = performance.now() - stageStart
    spans.push({ id: newKey(), at: new Date().toISOString(), stage, wall_ms: Math.round(wall), bytes: file.size, outcome })
    console.info('rav_timing', JSON.stringify({
      event: 'rav_timing', at: new Date().toISOString(),
      stage, upload_id: uploadId, job_id: jobId, bytes: file.size, outcome, wall_ms: Math.round(wall),
      ...(stage === 'browser.s3.transfer' && outcome === 'ok' && wall > 0 ? { bytes_per_second: Math.round(file.size * 1000 / wall) } : {}),
    }))
  }
  const nextStage = (name: string) => { record(); stage = name; stageStart = performance.now() }

  const matched = targetKey
    ? entries.find(e => e.key === targetKey)
    : entries.find(e => e.status === 'needs_file' && e.name === file.name && (!e.declaredBytes || e.declaredBytes === file.size) && (!targetScope || (e.targetScope ?? 'PRIVATE') === targetScope))
  const effectiveScope: 'PRIVATE' | 'SYSTEM' = targetScope ?? matched?.targetScope ?? 'PRIVATE'
  const previousUploadId = matched?.uploadId
  const key = matched?.key ?? `local:${newKey()}`

  if (matched) {
    updateEntry(key, { name: file.name, declaredBytes: file.size, targetScope: effectiveScope, progress: 0, state: 'đang tạo', status: 'running', error: undefined })
  } else {
    entries = [...entries, { key, name: file.name, declaredBytes: file.size, targetScope: effectiveScope, progress: 0, state: 'đang tạo', status: 'running' }]
    emit()
  }

  let finalOutcome = 'incomplete'
  try {
    const started = await createOrResume(file, effectiveScope)
    const up = started.upload
    resumeKey = started.storageKey
    uploadId = up.id
    if (previousUploadId && previousUploadId !== up.id) {
      void apiV2.abortUpload(previousUploadId).catch(() => {})
    }
    updateEntry(key, { uploadId: up.id, targetScope: up.target_scope ?? effectiveScope })
    let u: UploadV2 = up
    if (up.state === 'CREATED' && 'transfer' in up) {
      nextStage('browser.s3.transfer')
      updateEntry(key, { state: 'đang tải lên', status: 'running' })
      await transferFile(file, up, progress => updateEntry(key, { progress, status: 'running' }))
      nextStage('browser.complete')
      u = await apiV2.completeUpload(up.id)
    }
    updateEntry(key, { progress: 1, state: 'đang tiền xử lý video', status: 'running' })
    jobId = u.validation_job_id ?? undefined
    nextStage('browser.ready_wait')
    u = await pollValidating(up.id, key, u)
    record(u.state === 'READY' ? 'ok' : u.state)
    stage = 'browser.refresh_library'; stageStart = performance.now()
    if (resumeKey && u.state === 'READY') clearUploadResume(resumeKey)
    finalOutcome = u.state === 'READY' ? 'ok' : 'incomplete'
  } catch (e) {
    record('error')
    if (!(e instanceof StaleResponseError)) {
      updateEntry(key, { state: 'lỗi', status: 'error', error: e instanceof Error ? e.message : String(e) })
    }
  } finally {
    spans.push({
      id: newKey(), at: new Date().toISOString(), stage: 'browser.upload_total', bytes: file.size,
      outcome: finalOutcome, wall_ms: Math.round(performance.now() - totalStart),
    })
    if (uploadId) {
      void sendUploadTimings(uploadId, spans).catch(() => {
        console.info('rav_timing: không gửi được timing; xem Console cho bản cục bộ')
      })
    }
    console.info('rav_timing', JSON.stringify({
      event: 'rav_timing', at: new Date().toISOString(),
      stage: 'browser.upload_total', upload_id: uploadId, job_id: jobId, bytes: file.size,
      outcome: finalOutcome, wall_ms: Math.round(performance.now() - totalStart),
    }))
  }
}

export function useUploads(): UploadEntry[] {
  const [list, setList] = useState<UploadEntry[]>(() => getUploads())
  useEffect(() => {
    setList(getUploads())
    return subscribeUploads(() => setList(getUploads()))
  }, [])
  return list
}
