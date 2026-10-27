/** §21.40: truthful timing/cached status and public phase labels, no invented ETA. */
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ProposalProgress } from './components/ProposalProgress'
import { jobOf } from './pilot_api'
import type { JobV2, PhaseTimingV2 } from './api_v2'

describe('proposal phase timeline', () => {
  const stages: PhaseTimingV2[] = [
    { phase: 'LOAD_FEATURES', started_at: '2026-10-17T00:00:00Z', elapsed_ms: 1234, state: 'done', done: 2, total: 2, cache_hit: true },
    { phase: 'CLIP_MODEL', started_at: '2026-10-17T00:00:02Z', elapsed_ms: 750, state: 'running', done: 0, total: 0 },
  ]
  it('shows measured stages, cache and unfinished terminal stage', () => {
    const html = renderToStaticMarkup(<ProposalProgress stages={stages} active={false} />)
    expect(html).toContain('Tải/nạp embedding DINO và CLIP')
    expect(html).toContain('dùng cache')
    expect(html).toContain('2/2')
    expect(html).toContain('1.2')
    expect(html).toContain('chưa hoàn tất')
    expect(html).not.toContain('ETA')
    expect(html).not.toContain('LOAD_FEATURES')
  })
  it('retains timings after polling/reload and labels current stage', () => {
    const job = jobOf({ id: 'job', state: 'RUNNING', phase: 'CLIP_MODEL', progress: { stages }, payload: {} } as unknown as JobV2, 'session')
    expect(job.timings).toEqual(stages)
    expect(job.stage).toContain('Nạp mô hình CLIP text')
  })
})
