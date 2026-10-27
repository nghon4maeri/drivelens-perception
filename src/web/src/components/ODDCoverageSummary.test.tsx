import { renderToStaticMarkup } from 'react-dom/server'
import { expect, it } from 'vitest'
import type { ProposalDTO, RecipeDTO, StrategyTrace } from '../api'
import { proposalOf } from '../pilot_api'
import type { ProposalV2 } from '../api_v2'
import { ODDCoverageSummary } from './ODDCoverageSummary'
import { RecipePanel, proposalRequest, recipeSelFor, restoreRecipeSel } from './RecipePanel'

const recipe: RecipeDTO = {
  name: 'odd_coverage', description: 'Coverage', config: null, params_schema: { reference: {}, objectives: [], combiner: {}, selector: {} },
  components: {}, background: true, requires_model: false, requires_training: false,
  strategy: { name: 'odd_coverage', title: 'Tìm tình huống và bổ sung coverage', version: '1', steps: ['CLIP', 'DINO'],
    inputs: { model: false, model_optional: true, reference_dataset: false },
    defaults: { objects: ['truck'], odd_conditions: ['rain'], scenarios: [], contrast_texts: ['road scene'] },
    parameters_schema: { properties: Object.fromEntries(['objects', 'odd_conditions', 'scenarios', 'contrast_texts'].map(k => [k, { type: 'array', items: { type: 'string' } }])) },
  },
}
const trace: StrategyTrace[] = [
  { step: 'odd_gate', pool_count: 50, count: 12, shortlist_count: 10, branches: [{ id: 'b1', query: 'truck in rain', kind: 'object_odd' }], eligible_counts: [12], kept_counts: [2] },
  { step: 'odd_selection', count: 3, requested: 4, selected_counts: [3] },
]

it('renders three multiline query inputs and explains union, independent scenarios and optional model', () => {
  const html = renderToStaticMarkup(<RecipePanel recipes={[recipe]} sel={recipeSelFor(recipe)} onChange={() => {}} />)
  for (const text of ['Điều kiện ODD', 'Đối tượng cần tìm', 'Scenario độc lập', 'Không chạy model', 'chưa xác nhận nhãn ODD']) expect(html).toContain(text)
  expect(html.match(/<textarea/g)?.length).toBe(4) // Three public fields, contrast under advanced settings.
  expect(proposalRequest(recipeSelFor(recipe), recipe).model_id).toBeUndefined()
  expect(proposalRequest({ ...recipeSelFor(recipe), modelId: 'verified-model' }, recipe).model_id).toBe('verified-model')
})

it('rejects an empty query and restores the frozen optional model/parameters', () => {
  const sel = recipeSelFor(recipe)
  expect(() => proposalRequest({ ...sel, strategyParams: { objects: [], odd_conditions: [], scenarios: [] } }, recipe)).toThrow('Nhập ít nhất')
  const input = { recipe: recipe.name, config: null, model_id: 'frozen-sha', strategy_params: { objects: ['bus'], scenarios: ['school crossing'], odd_conditions: [] }, batch_size: 4, seed: 7 } as unknown as ProposalDTO['input']
  const restored = restoreRecipeSel(recipe, input)
  expect(proposalRequest(restored, recipe)).toMatchObject({ model_id: 'frozen-sha', strategy_params: input.strategy_params })
})

it('preserves the actual batch trace through the pilot DTO and shows frozen counts including short batches', () => {
  const proposal = proposalOf({ n: 1, round: 1, created_at: '', input: {}, picks: [], trace, relaxed: false } as unknown as ProposalV2)
  const html = renderToStaticMarkup(<ODDCoverageSummary trace={proposal.trace} />)
  expect(html).toContain('truck in rain')
  expect(html).toContain('3/4')
  expect(html).toContain('Không ép đủ batch')
  expect(proposal.trace).toEqual(trace)
  expect(renderToStaticMarkup(<ODDCoverageSummary />)).toBe('')
})

it('explains an empty gate rather than presenting forced top results', () => {
  const html = renderToStaticMarkup(<ODDCoverageSummary trace={[{ ...trace[0], count: 0 }, { ...trace[1], count: 0 }]} />)
  expect(html).toContain('Không có ứng viên đạt ngưỡng')
})

it('explains the optional model evidence funnel when detections are unobserved', () => {
  const html = renderToStaticMarkup(<ODDCoverageSummary trace={[...trace, { step: 'odd_uncertainty', count: 9, observed_count: 4 }]} />)
  expect(html).toContain('Đã đo model trên 9 ảnh; 4 ảnh có tín hiệu detection')
  expect(html).toContain('không được tính là model tự tin')
})
