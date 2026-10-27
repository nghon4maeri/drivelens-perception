/** Opening a frame must show an image and defer clip loading/playback until requested. */
import { renderToStaticMarkup } from 'react-dom/server'
import { beforeEach, expect, it, vi } from 'vitest'
import { VideoContextLightbox } from './VideoContextLightbox'

const media = vi.hoisted(() => ({ url: undefined as string | undefined, failed: false, enabled: [] as boolean[] }))
vi.mock('../media', () => ({
  isMediaRef: () => false,
  useMediaUrl: (src: string, enabled: boolean) => {
    if (src === 'clip') { media.enabled.push(enabled); return { ...media, retry: () => {} } }
    return { url: src, failed: false }
  },
}))
const render = () => renderToStaticMarkup(<VideoContextLightbox
  unit={{ id: 'test:1-1', video_id: 'test', t0: 1, t1: 1, thumb_url: '/thumb.jpg', frame_url: '/frame.jpg' }} mediaUrl="clip"
  videoName="Test" status="undecided" docs={{}} onClose={() => {}} onDecide={() => {}} />)

beforeEach(() => { media.url = undefined; media.failed = false; media.enabled = [] })
it('shows the frame with an enabled Play action and no clip request/player on open', () => {
  const html = render()
  expect(html).toContain('src="/thumb.jpg"')
  expect(html).toContain('src="/frame.jpg"')
  expect(html).not.toContain('<video')
  expect(html).not.toContain('Đang chờ clip ngữ cảnh…')
  const play = html.match(/<button\b[^]*?<\/button>/g)?.find(button => button.includes('Phát</button>'))
  expect(play).toBeTruthy()
  expect(play).not.toContain(' disabled=""')
  expect(media.enabled).toEqual([false])
})
it('keeps the image as initial view even if a cached clip URL exists or previously failed', () => {
  media.url = '/clip.mp4'
  media.failed = true
  const html = render()
  expect(html).toContain('src="/thumb.jpg"')
  expect(html).not.toContain('<video')
  expect(media.enabled).toEqual([false])
})
