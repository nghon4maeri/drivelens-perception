/** Large pilot upload (§21.27): File.slice, four PUTs, part retry and owner-scoped resume metadata. */
import { apiV2, newKey, v2, V2Error, type UploadCreatedV2, type UploadV2 } from './api_v2'
import { trackRequest } from './auth'

type Resume = { key: string; id?: string }
const storage = {
  get(key: string): Resume | undefined { try { return JSON.parse(localStorage.getItem(key) ?? 'null') ?? undefined } catch { return undefined } },
  set(key: string, value: Resume) { try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* resume unavailable in private storage */ } },
  remove(key: string) { try { localStorage.removeItem(key) } catch { /* optional persistence */ } },
}

export async function createOrResume(file: File, targetScope: 'PRIVATE' | 'SYSTEM' = 'PRIVATE'): Promise<{ upload: UploadV2 | UploadCreatedV2; storageKey: string }> {
  const owner = (await apiV2.me()).user_id
  const scopePrefix = targetScope === 'SYSTEM' ? 'SYSTEM:' : ''
  const storageKey = `rav-upload:${owner}:${scopePrefix}${JSON.stringify([file.name, file.size, file.lastModified])}`
  let saved = storage.get(storageKey)
  if (saved?.id) {
    try {
      const upload = await apiV2.upload(saved.id)
      if (upload.declared_bytes !== file.size || ['ABORTED', 'FAILED'].includes(upload.state)) saved = undefined
      else if (upload.state !== 'CREATED') return { upload, storageKey }
    } catch (e) {
      if (!(e instanceof Error && 'status' in e && e.status === 404)) throw e
      saved = undefined
    }
  }
  saved ??= { key: newKey() }
  storage.set(storageKey, saved) // persist before create, so a lost response/reload keeps the idempotency key
  const create = (key: string) => v2<UploadCreatedV2>('/api/v2/uploads', {
    method: 'POST',
    body: { filename: file.name, declared_bytes: file.size, ...(targetScope === 'SYSTEM' ? { target_scope: 'SYSTEM' } : {}) },
    key,
  })
  let upload: UploadCreatedV2
  try { upload = await create(saved.key) }
  catch (e) {
    if (!(e instanceof V2Error && e.code === 'upload_invalid' && e.status === 409)) throw e
    saved = { key: newKey() }; storage.set(storageKey, saved)
    upload = await create(saved.key)
  }
  storage.set(storageKey, { ...saved, id: upload.id })
  return { upload, storageKey }
}

export const clearUploadResume = (key: string) => storage.remove(key)

export function send(method: string, url: string, body: Blob | FormData, progress: (n: number) => void, timeout = 120000): Promise<void> {
  const request = trackRequest()
  return new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    const abort = () => xhr.abort()
    request.signal.addEventListener('abort', abort, { once: true })
    xhr.open(method, url); xhr.timeout = timeout
    xhr.upload.onprogress = e => { if (e.lengthComputable) progress(e.loaded) }
    xhr.onload = () => xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(new Error(`Tải lên S3 lỗi (HTTP ${xhr.status})`))
    xhr.onerror = () => reject(new Error('Mất kết nối khi tải lên; chọn lại cùng file để tiếp tục.'))
    xhr.ontimeout = () => reject(new Error('Part tải lên quá thời gian; chọn lại cùng file để tiếp tục.'))
    xhr.onabort = () => reject(new Error('Đã dừng tải lên.'))
    xhr.onloadend = () => { request.signal.removeEventListener('abort', abort); request.done() }
    if (request.signal.aborted) { request.done(); reject(new Error('Phiên đăng nhập đã đổi.')); return }
    xhr.send(body)
  })
}

export async function transferFile(file: File, upload: UploadCreatedV2, progress: (fraction: number) => void): Promise<void> {
  if (upload.transfer.method === 'VPS_DISK') {
    const request = trackRequest()
    try {
    const chunkSize = upload.transfer.part_size ?? (16 * 1024 * 1024)
    if (!Number.isInteger(chunkSize) || chunkSize <= 0 || chunkSize > 16 * 1024 * 1024) throw new Error('Chunk size VPS không hợp lệ')
    const token = upload.transfer.fields.capability ?? upload.transfer.capability ?? ''
    const baseUrl = upload.transfer.url
    const statusRes = await fetch(baseUrl, {
      headers: { Authorization: `Bearer ${token}` }, signal: request.signal,
    })
    if (!statusRes.ok) throw new Error(`Lỗi kiểm tra trạng thái upload VPS (${statusRes.status})`)
    const statusData = await statusRes.json()
    let offset = statusData.offset ?? 0
    const validOffset = (value: number) => Number.isInteger(value) && value >= 0 && value <= file.size
    if (!validOffset(offset)) throw new Error('Offset VPS không hợp lệ')
    let conflicts = 0
    progress(Math.min(1, offset / file.size))
    while (offset < file.size) {
      const nextEnd = Math.min(file.size, offset + chunkSize)
      const chunk = file.slice(offset, nextEnd)
      const putRes = await fetch(`${baseUrl}/chunks`, {
        method: 'PUT',
        headers: {
          Authorization: `Bearer ${token}`,
          'Upload-Offset': String(offset),
        },
        body: chunk,
        signal: request.signal,
      })
      if (!putRes.ok) {
        if (putRes.status === 409) {
          const errData = await putRes.json().catch(() => ({}))
          const exp = errData.error?.expected_offset
          if (typeof exp === 'number' && validOffset(exp) && ++conflicts <= 3) {
            offset = exp
            progress(Math.min(1, offset / file.size))
            continue
          }
        }
        throw new Error(`Tải chunk lên VPS lỗi (${putRes.status})`)
      }
      const data = await putRes.json()
      if (!validOffset(data.offset) || data.offset !== nextEnd) throw new Error('VPS chưa xác nhận đủ chunk; chọn lại cùng file để tiếp tục.')
      offset = data.offset
      conflicts = 0
      progress(Math.min(1, offset / file.size))
    }
    return
    } finally { request.done() }
  }
  if (upload.transfer.method !== 'MULTIPART') {
    const form = new FormData()
    for (const [k, v] of Object.entries(upload.transfer.fields)) form.append(k, v)
    form.append('file', file)
    return send('POST', upload.transfer.url, form, n => progress(Math.min(1, n / file.size)))
  }
  const status = await apiV2.startMultipart(upload.id)
  if (status.assembled) { progress(1); return }
  const loaded = new Map<number, number>()
  const pending: number[] = []
  for (let n = 1; n <= status.part_count; n++) {
    const size = Math.min(status.part_size, file.size - (n - 1) * status.part_size)
    if (status.parts.some(p => p.part_number === n && p.size_bytes === size)) loaded.set(n, size)
    else pending.push(n)
  }
  const tick = () => progress([...loaded.values()].reduce((a, b) => a + b, 0) / file.size)
  tick()
  // Four workers consume URLs in bounded groups; do not keep thousands of signed URLs or read the whole video.
  for (let start = 0; start < pending.length; start += 4) {
    const group = pending.slice(start, start + 4)
    const signed = await apiV2.multipartParts(upload.id, group)
    const jobs = signed.parts.map(async part => {
      const blob = file.slice((part.part_number - 1) * status.part_size, Math.min(file.size, part.part_number * status.part_size))
      for (let attempt = 0; ; attempt++) {
        loaded.set(part.part_number, 0); tick()
        try {
          const url = attempt === 0 ? part.url : (await apiV2.multipartParts(upload.id, [part.part_number])).parts[0].url
          await send('PUT', url, blob, n => { loaded.set(part.part_number, n); tick() })
          loaded.set(part.part_number, blob.size); tick(); return
        } catch (e) {
          if (attempt >= 2) throw e
          await new Promise(r => setTimeout(r, 500 * (attempt + 1)))
        }
      }
    })
    // Let every in-flight part settle before reporting an error or starting a retry from the UI.
    const results = await Promise.allSettled(jobs)
    const failed = results.find(r => r.status === 'rejected')
    if (failed?.status === 'rejected') throw failed.reason
  }
}
