/**
 * Media riêng tư ở pilot (CONTRACTS §21.12, §21.16, §21.20 F4; UI-export spec §2 Media state): tham chiếu `rav-media:<variant>:<source>:<unit>` → URL.
 * - Tối đa 4 HTTP media đồng thời; suất chỉ giữ trong lúc gọi HTTP (không giữ khi chờ timer); hàng chờ theo ưu tiên 0 (viewer) > 1 (viewport).
 * - Mỗi tham chiếu một request đang bay (dedup). PENDING → poll GET receipt (500 ms → 2 s); READY dùng URL tới `expires_at`, hết hạn → GET receipt.
 * - `NOT_PREPARED` (ảnh lưu sẵn chưa có): có job chuẩn bị thì POST lại (lookup, không job) 2 s → 10 s; không job → báo để UI hiện nút chuẩn bị.
 * - Poll dừng khi không còn component dùng tham chiếu (hook). `clearMediaCache` (đăng xuất/đổi tài khoản) tăng thế hệ: response cũ bị bỏ.
 */
import { useEffect, useState } from 'react'
import { apiV2, V2Error, type MediaV2 } from './api_v2'
import { parseMediaRef } from './pilot_api'

export type Priority = 0 | 1   // 0: ảnh/clip đang mở trong viewer; 1: ảnh trong viewport

export class MediaNotPreparedError extends Error {
  constructor(readonly sourceId: string, readonly jobId: string | null) {
    super(jobId ? 'Đang chuẩn bị ảnh cho video này.' : 'Ảnh của video này chưa được chuẩn bị.')
  }
}
export class MediaCancelledError extends Error {}

type Entry = { id?: string; url?: string; expires?: number; promise?: Promise<string> }
const cache = new Map<string, Entry>()
/** Signed image URLs returned with an interactive batch; no DB lookup until expiry. */
export function seedMediaUrls(images: { source_id: string; unit_id: string; variant: string; url: string; expires_at: string }[]) {
  for (const m of images) cache.set(`rav-media:${m.variant}:${m.source_id}:${m.unit_id}`, { url: m.url, expires: Date.parse(m.expires_at) })
}
const consumers = new Map<string, number>()
const MAX_ACTIVE = 4
let active = 0
let seq = 0
let generation = 0
const waiting: { priority: number; seq: number; go: () => void }[] = []
type ImageTarget = { source_id: string; unit_id: string; variant: 'thumbnail' | 'frame'; max_side?: number }
type BatchWaiter = { target: ImageTarget; priority: Priority; gen: number; resolve: (m: MediaV2) => void; reject: (e: unknown) => void }
let batch: BatchWaiter[] = []
let batchTimer: ReturnType<typeof setTimeout> | undefined

async function getImages(items: ImageTarget[], priority: Priority, gen: number): Promise<MediaV2[]> {
  try {
    return await http(priority, () => {
      if (gen !== generation) throw new MediaCancelledError('Phiên đăng nhập đã đổi.')
      return apiV2.requestMediaBatch(items)
    })
  } catch (e) {
    const cap = e instanceof V2Error ? e.details.max_items : undefined
    if (e instanceof V2Error && e.status === 422 && Number.isInteger(cap) && Number(cap) > 0 && Number(cap) < items.length) {
      const pieces: Promise<MediaV2[]>[] = []
      for (let i = 0; i < items.length; i += Number(cap)) pieces.push(getImages(items.slice(i, i + Number(cap)), priority, gen))
      return (await Promise.all(pieces)).flat()
    }
    throw e
  }
}

/** Coalesce viewport lookups; bytes still download directly from S3. */
function imageLookup(target: ImageTarget, priority: Priority, gen: number): Promise<MediaV2> {
  return new Promise((resolve, reject) => {
    batch.push({ target, priority, gen, resolve, reject })
    if (batchTimer === undefined) batchTimer = setTimeout(() => {
      batchTimer = undefined
      const queued = batch; batch = []
      const current = queued.filter(q => q.gen === generation)
      for (const q of queued.filter(q => q.gen !== generation)) q.reject(new MediaCancelledError('Phiên đăng nhập đã đổi.'))
      for (let i = 0; i < current.length; i += 16) {
        const group = current.slice(i, i + 16)
        void getImages(group.map(q => q.target), group.some(q => q.priority === 0) ? 0 : 1, gen).then(results => {
          if (results.length !== group.length) throw new Error('Batch media trả thiếu kết quả.')
          group.forEach((q, n) => q.gen === generation ? q.resolve(results[n]) : q.reject(new MediaCancelledError('Phiên đăng nhập đã đổi.')))
        }).catch(e => group.forEach(q => q.reject(e)))
      }
    }, 0)
  })
}

/** Giữ một suất cho đúng một HTTP call; suất trả ngay khi call xong, người chờ ưu tiên thấp hơn đi sau. */
async function http<T>(priority: Priority, fn: () => Promise<T>): Promise<T> {
  if (active >= MAX_ACTIVE) {
    await new Promise<void>(go => {
      waiting.push({ priority, seq: seq++, go })
      waiting.sort((a, b) => a.priority - b.priority || a.seq - b.seq)
    })
  }
  active++
  try { return await fn() } finally { active--; waiting.shift()?.go() }
}

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms))
export const isMediaRef = (src?: string) => !!src && src.startsWith('rav-media:')

async function settle(ref: string, first: MediaV2, priority: Priority, watched: boolean, gen: number,
                      target: NonNullable<ReturnType<typeof parseMediaRef>>): Promise<string> {
  let m = first
  const live = () => gen === generation && (!watched || (consumers.get(ref) ?? 0) > 0)
  for (let i = 0; i < 180 && (m.state === 'PENDING' || (m.state === 'NOT_PREPARED' && m.job_id)); i++) {
    if (m.id && m.state === 'PENDING') cache.set(ref, { ...cache.get(ref), id: m.id })
    await sleep(m.state === 'PENDING' ? Math.min(500 * (i + 1), 2000) : Math.min(2000 * (i + 1), 10_000))
    if (!live()) throw new MediaCancelledError('Không còn ai xem media này.')
    m = target.variant === 'context'
      ? await http(priority, () => m.state === 'PENDING' && m.id ? apiV2.media(m.id) : apiV2.requestMedia(target))
      : await imageLookup({ ...target, variant: target.variant }, priority, gen)
  }
  if (m.state === 'NOT_PREPARED') throw new MediaNotPreparedError(target.source_id, m.job_id)
  if (m.state !== 'READY' || !m.url) throw new Error(m.state === 'PENDING' ? 'Media chưa sẵn sàng; thử mở lại sau.' : 'Không tạo được ảnh/clip cho frame này.')
  if (gen === generation) cache.set(ref, { id: m.id ?? undefined, url: m.url, expires: m.expires_at ? Date.parse(m.expires_at) : Date.now() + 60_000 })
  return m.url
}

/** URL cho tham chiếu media; `watched` = do hook gọi (poll dừng khi hook không còn dùng). */
export function resolveMedia(ref: string, priority: Priority = 1, watched = false): Promise<string> {
  const e = cache.get(ref)
  if (e?.promise) return e.promise
  if (e?.url && (e.expires ?? 0) - Date.now() > 15_000) return Promise.resolve(e.url)
  const target = parseMediaRef(ref)
  if (!target) return Promise.reject(new Error('Tham chiếu media không hợp lệ'))
  const gen = generation
  const start = performance.now()
  const first = target.variant === 'context'
    ? http(priority, () => e?.id ? apiV2.media(e.id) : apiV2.requestMedia(target))
    : imageLookup({ ...target, variant: target.variant }, priority, gen)
  const promise = first
    .then(first => settle(ref, first, priority, watched, gen, target))
  const record = (outcome: string) => console.info('rav_timing', JSON.stringify({ event: 'rav_timing',
    at: new Date().toISOString(), stage: 'browser.media_ready_wait', source_id: target.source_id,
    kind: target.variant, outcome, wall_ms: Math.round(performance.now() - start) }))
  promise.then(() => record('ok'), err => record(err instanceof MediaNotPreparedError ? 'not_prepared' : 'error'))
  if (gen === generation) cache.set(ref, { ...e, promise })
  promise.catch(() => {
    const c = cache.get(ref)
    if (c?.promise === promise) { if (c.id) cache.set(ref, { id: c.id }); else cache.delete(ref) }   // giữ id PENDING để GET tiếp
  }).finally(() => { const c = cache.get(ref); if (c?.promise === promise) delete c.promise })
  return promise
}

export function clearMediaCache() {
  generation++
  cache.clear()
  if (batchTimer !== undefined) clearTimeout(batchTimer)
  batchTimer = undefined
  for (const q of batch) q.reject(new MediaCancelledError('Phiên đăng nhập đã đổi.'))
  batch = []
}

/** Người dùng bấm "Chuẩn bị ảnh" ở ô ảnh: job `PREPARE_FRAMES` explicit (§21.20); gọi lại khi đang chạy trả cùng job. */
export const prepareFrames = (sourceId: string) => apiV2.prepare(sourceId, 'FRAMES')

export type MediaView = { url?: string; failed: boolean; notPrepared?: { sourceId: string; jobId: string | null }; retry: () => void }

/** URL hiển thị cho `src`: URL thường giữ nguyên; tham chiếu pilot → media request khi `enabled`. */
export function useMediaUrl(src: string | undefined, enabled = true, priority: Priority = 1): MediaView {
  const pilot = isMediaRef(src)
  const [nonce, setNonce] = useState(0)
  const [state, setState] = useState<{ src?: string; url?: string; failed: boolean; notPrepared?: MediaView['notPrepared'] }>({ failed: false })
  useEffect(() => {
    if (!pilot || !enabled || !src) return
    let live = true
    consumers.set(src, (consumers.get(src) ?? 0) + 1)
    resolveMedia(src, priority, true).then(url => { if (live) setState({ src, url, failed: false }) })
      .catch(err => {
        if (!live || err instanceof MediaCancelledError) return
        if (err instanceof MediaNotPreparedError) setState({ src, failed: false, notPrepared: { sourceId: err.sourceId, jobId: err.jobId } })
        else setState({ src, failed: true })
      })
    return () => {
      live = false
      const n = (consumers.get(src) ?? 1) - 1
      if (n > 0) consumers.set(src, n); else consumers.delete(src)
    }
  }, [src, pilot, enabled, priority, nonce])
  const retry = () => setNonce(n => n + 1)
  if (!pilot) return { url: src, failed: false, retry }
  return state.src === src ? { url: state.url, failed: state.failed, notPrepared: state.notPrepared, retry } : { failed: false, retry }
}

export const _test = { consumers, waiting: () => waiting.length, active: () => active }
