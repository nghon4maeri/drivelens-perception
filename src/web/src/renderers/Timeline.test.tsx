/** Batch-only scores must leave other samples blank, never invent zero-valued points. */
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { expect, it } from 'vitest'
import { Timeline } from './Timeline'

it('plots only supplied batch scores and labels their scope', () => {
  const html = renderToStaticMarkup(<Timeline units={new Map()} data={{ video_id: 'v', t: [0, 1, 2], unit_id: ['a', 'b', 'c'], series: {}, picks: [1], kept: [], dropped: [] }} scores={{ unit_id: ['b'], combined: [0.5] }} onOpen={() => {}} />)
  expect(html).toContain('mẫu trong batch mới nhất')
  expect(html.match(/<circle/g)).toHaveLength(1)
  expect(html).not.toContain('<polyline')
  expect(html).not.toContain('trả điểm mọi candidate')
})
