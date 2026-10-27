import { afterEach, describe, expect, it, vi } from 'vitest'
import type { UploadCreatedV2 } from './api_v2'
import { transferFile } from './upload_file'

const { done } = vi.hoisted(() => ({ done: vi.fn() }))
vi.mock('./auth', () => ({ trackRequest: () => ({ signal: new AbortController().signal, done }) }))
vi.mock('./api_v2', () => ({ apiV2: {}, newKey: vi.fn(), v2: vi.fn(), V2Error: class extends Error {} }))

const upload: UploadCreatedV2 = { id: 'test-upload', state: 'CREATED', validation_job_id: null, source_id: null, error: null,
  transfer: { method: 'VPS_DISK', url: 'https://upload.test/u', part_size: 4, expires_at: '2099-01-01T00:00:00Z',
    fields: { capability: 'test-capability' } } }
const reply = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })

afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks() })

describe('VPS bounded upload', () => {
  it('resumes at durable offset and sends only remaining slices', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(reply({ offset: 3 }))
      .mockResolvedValueOnce(reply({ offset: 7 })).mockResolvedValueOnce(reply({ offset: 10 }))
    vi.stubGlobal('fetch', fetcher)
    await transferFile(new File(['0123456789'], 'a.mp4'), upload, vi.fn())
    expect(fetcher.mock.calls[1][1].headers['Upload-Offset']).toBe('3')
    expect(fetcher.mock.calls[2][1].headers['Upload-Offset']).toBe('7')
    expect(fetcher.mock.calls[1][1].body.size).toBe(4)
    expect(fetcher.mock.calls[2][1].body.size).toBe(3)
    expect(done).toHaveBeenCalledOnce()
  })

  it('rejects an acknowledgement that did not commit the full chunk', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(reply({ offset: 0 })).mockResolvedValueOnce(reply({ offset: 0 })))
    await expect(transferFile(new File(['0123456789'], 'a.mp4'), upload, vi.fn())).rejects.toThrow('chưa xác nhận')
    expect(done).toHaveBeenCalledOnce()
  })

  it('stops repeated offset conflicts instead of looping forever', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(reply({ offset: 0 }))
      .mockImplementation(async () => reply({ error: { expected_offset: 0 } }, 409))
    vi.stubGlobal('fetch', fetcher)
    await expect(transferFile(new File(['0123456789'], 'a.mp4'), upload, vi.fn())).rejects.toThrow('409')
    expect(fetcher).toHaveBeenCalledTimes(5)
    expect(done).toHaveBeenCalledOnce()
  })
})
