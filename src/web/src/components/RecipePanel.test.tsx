/** Strategy sidebar, native null config and historical replay regression checks. */
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { RecipeDTO, ProposalDTO } from '../api'
import { RecipePanel, proposalRequest, recipeSelFor, restoreRecipeSel } from './RecipePanel'
const legacy: RecipeDTO = { name: 'text_diversity', description: 'Tìm cảnh đa dạng', config: { name: 'text_diversity', reference: { type: 'session_kept' }, objectives: [{ type: 'text_match', strength: 1, params: { texts: ['night road'] } }], combiner: { type: 'product_strength' }, selector: { type: 'greedy_nms' } }, params_schema: { reference: {}, objectives: [], combiner: {}, selector: {} }, components: {}, background: false, requires_model: false, requires_training: false }
const native: RecipeDTO = { ...legacy, name: 'context_uncertainty', config: null, background: true, requires_model: true, strategy: { name: 'context_uncertainty', version: '1', title: 'Ngữ cảnh và thiếu ổn định', steps: ['Tìm ngữ cảnh bằng CLIP', 'Giữ cảnh đa dạng', 'Đo thiếu ổn định trên ứng viên'], inputs: { model: true, reference_dataset: false }, parameters_schema: { type: 'object', properties: { texts: { type: 'array', items: { type: 'string' } }, negative: { type: 'array', items: { type: 'string' } }, candidate_multiplier: { type: 'integer', minimum: 2, maximum: 3, default: 2 } } }, defaults: { texts: ['pedestrian'], negative: [], candidate_multiplier: 2 } } }
const render = (r: RecipeDTO) => renderToStaticMarkup(<RecipePanel recipes={[r]} sel={recipeSelFor(r)} onChange={() => {}} />)
describe('strategy sidebar', () => {
  it('renders metadata steps, actual public defaults and bounds without internal editable inputs', () => {
    const html = render(native)
    for (const text of [native.strategy!.title, ...native.strategy!.steps, 'pedestrian']) expect(html).toContain(text)
    expect(html).toMatch(/min="2"[^>]*max="3"|max="3"[^>]*min="2"/)
    expect(html).not.toContain('<details open')
    expect(html).not.toMatch(/<textarea[^>]*>\s*\{/)   // config JSON is read-only <pre>, never editable
  })
  it('keeps explicit legacy CLIP queries useful with no public metadata', () => {
    const html = render(legacy)
    expect(html).toContain('night road')
    expect(html).not.toContain('>strength<')
    expect(html).not.toContain('>combiner<')
  })
  it('prepares native params and selected model without fabricating legacy config', () => {
    const sel = { ...recipeSelFor(native), modelId: 'selected-model' }
    expect(proposalRequest(sel, native)).toMatchObject({ recipe: native.name, config: undefined, strategy_params: native.strategy!.defaults, model_id: 'selected-model' })
    expect(() => proposalRequest(recipeSelFor(native), native)).toThrow('Chọn model')
    for (const candidate_multiplier of [1, 4, 2.5]) expect(() => proposalRequest({ ...sel, strategyParams: { candidate_multiplier } }, native)).toThrow('giới hạn')
  })
  it('omits stale model after switching to a strategy that does not use one', () => {
    const previous = { ...recipeSelFor(native), modelId: 'selected-model' }
    for (const name of ['diversity', 'uniform', 'random', 'text_diversity']) {
      const next = { ...legacy, name }
      const request = proposalRequest(recipeSelFor(next, previous), next)
      expect(JSON.parse(JSON.stringify(request))).not.toHaveProperty('model_id')
    }
    expect(proposalRequest(previous, native).model_id).toBe('selected-model')
  })
  it('restores frozen params and model identity rather than current defaults', () => {
    const input = { recipe: native.name, config: null, batch_size: 8, seed: 42, strategy_params: { texts: ['rain'], candidate_multiplier: 3 }, execution: { model_ids: ['frozen-model'] } } as unknown as ProposalDTO['input']
    const sel = restoreRecipeSel(native, input)
    expect(sel).toMatchObject({ config: null, batch: 8, seed: '42', strategyParams: input.strategy_params, modelId: 'frozen-model' })
    expect(proposalRequest(sel, native).strategy_params).toEqual(input.strategy_params)
  })
  it('restores historical legacy config and CLIP text, with or without strategy metadata', () => {
    const input = { recipe: legacy.name, config: legacy.config, batch_size: 5, seed: 1 } as unknown as ProposalDTO['input']
    expect(restoreRecipeSel({ ...legacy, strategy: native.strategy }, input).strategyParams?.texts).toEqual(['night road'])
    expect(restoreRecipeSel(legacy, input)).toMatchObject({ config: input.config, batch: 5, strategyParams: undefined })
  })
})
