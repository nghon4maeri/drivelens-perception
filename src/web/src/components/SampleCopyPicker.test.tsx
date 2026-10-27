import { renderToStaticMarkup } from 'react-dom/server'
import { expect, it } from 'vitest'
import type { SourceV2 } from '../api_v2'
import { SampleCopyPicker, sampleCopyMissing } from './SampleCopyPicker'

const ready = { id: 'private-ready', core_source_id: 'video-core', metadata: { duration_ms: 21000, n_units: 86 }, name: 'Video đã chuẩn bị', scope: 'PRIVATE', kind: 'VIDEO', availability: 'READY',
  frames: { ready: true }, dino: { ready: true }, clip: { ready: true } } as SourceV2

it('allows only complete ready private videos and explains missing preparation', () => {
  expect(sampleCopyMissing(ready)).toEqual([])
  expect(sampleCopyMissing({ ...ready, clip: { ready: false, job_id: null } })).toEqual(['CLIP'])
  expect(sampleCopyMissing({ ...ready, availability: 'DELETE_PENDING' })).toContain('Video READY trong kho riêng')
  const html = renderToStaticMarkup(<SampleCopyPicker sources={[ready, { ...ready, id: 'incomplete', name: 'Thiếu CLIP', clip: { ready: false, job_id: null } },
    { ...ready, scope: 'SYSTEM', name: 'Không phải kho riêng' }]} busy={false} nextCursor="100" onReference={() => {}} onNext={() => {}} onReload={() => {}} />)
  expect(html).toContain('FRAMES · DINO · CLIP đã sẵn sàng')
  expect(html).toContain('Cần chuẩn bị: CLIP')
  expect(html).toContain('disabled=""')
  expect(html).not.toContain('Không phải kho riêng')
  expect(html).toContain('Trang tiếp của kho')
  expect(html).toContain('trạng thái ẩn')
  expect(html).toContain('không chạy lại DINO/CLIP')
  expect(html).toContain('Thêm vào kho mẫu')
  expect(html).toContain('Không sao chép file')
  expect(html).toContain('Nguồn gốc không thể xóa')
})

it('disables submission during copy and shows an empty page without hiding pagination', () => {
  const html = renderToStaticMarkup(<SampleCopyPicker sources={[ready]} busy nextCursor={null} onReference={() => {}} onNext={() => {}} onReload={() => {}} />)
  expect(html.match(/disabled=""/g)).toHaveLength(2)
  const empty = renderToStaticMarkup(<SampleCopyPicker sources={[]} busy={false} nextCursor="100" onReference={() => {}} onNext={() => {}} onReload={() => {}} />)
  expect(empty).toContain('Trang này chưa có video riêng')
  expect(empty).toContain('Trang tiếp của kho')
})
