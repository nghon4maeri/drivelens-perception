/** Perception boxes come from proposal evidence, independently of a separate YOLO batch. */
import { renderToStaticMarkup } from 'react-dom/server'
import { expect, it, vi } from 'vitest'
import { DashcamThumbnail, type GridItem } from './DashcamThumbnail'

vi.mock('./MediaImg', () => ({ MediaImg: () => null }))

it('renders original proposal boxes and badge while leaving comparison boxes hidden', () => {
  const box = { box: [.1, .1, .4, .6] as [number, number, number, number], label: 'car', score: .9, source: 'ym-test' }
  const item: GridItem = {
    unit: { id: 'v:1-1', video_id: 'v', t0: 1, t1: 1, thumb_url: '', frame_url: '' },
    pick: { unit_id: 'v:1-1', combined: .8, breakdown: {}, rank: 1 }, status: 'undecided', videoName: 'QA',
    proposalEvidence: [
      { kind: 'scalar', label: 'YOLO thiếu ổn định', value: .8, observed: true },
      { kind: 'detections', label: 'Prediction ảnh gốc', boxes: [box] },
      { kind: 'detections', label: 'Comparison', boxes: [box, box], comparison: true },
    ],
  }
  const render = (showBoxes: boolean) => renderToStaticMarkup(<DashcamThumbnail item={item} index={0} isFocused={false}
    showBoxes={showBoxes} onFocus={() => {}} onToggle={() => {}} onSet={() => {}} onOpenVideo={() => {}} />)
  expect(render(true).match(/<rect /g)).toHaveLength(1)
  expect(render(true)).toContain('Thiếu ổn định: 0.80')
  expect(render(false)).not.toContain('<rect ')
})
