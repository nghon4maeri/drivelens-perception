/** Strategy inputs for the next batch; historical technical config remains read-only. */
import React, { useEffect, useState } from 'react'
import { api, RECIPE_LABELS, type RecipeConfig, type RecipeDTO, type ProposalDTO, type ProposeRequest } from '../api'
import { SchemaForm } from '../SchemaForm'
import { inputClass } from '../ui'
import { PILOT } from '../mode'
import { apiV2 } from '../api_v2'

export type RecipeSel = { name: string; config: RecipeConfig | null; batch: number; seed: string; withScores: boolean; modelId?: string; strategyParams?: Record<string, unknown> }
export const recipeSelFor = (r: RecipeDTO, prev?: RecipeSel): RecipeSel => ({ name: r.name, config: structuredClone(r.config), batch: prev?.batch ?? 16, seed: prev?.seed ?? '', withScores: prev?.withScores ?? true, modelId: prev?.modelId, strategyParams: r.strategy ? structuredClone(r.strategy.defaults) : undefined })
export function restoreRecipeSel(r: RecipeDTO, input?: ProposalDTO['input']): RecipeSel {
  const sel = recipeSelFor(r)
  if (!input || input.recipe !== r.name) return sel
  const ids = input.execution?.model_ids
  const query = [...(input.config?.objectives ?? []), ...(input.config?.candidate_stage?.objectives ?? [])].find(o => o.type === 'text_match')
  const legacyParams = r.strategy && query ? Object.fromEntries(Object.entries(query.params ?? {}).filter(([key]) => key in (r.strategy?.parameters_schema.properties ?? {}))) : {}
  return { ...sel, config: input.config ?? sel.config, batch: input.batch_size, seed: String(input.seed), strategyParams: input.strategy_params ?? (sel.strategyParams ? { ...sel.strategyParams, ...legacyParams } : undefined),
    modelId: input.model_id ?? (Array.isArray(ids) && typeof ids[0] === 'string' ? ids[0] : undefined) }
}
export function proposalRequest(sel: RecipeSel, recipe?: RecipeDTO): ProposeRequest {
  const needsModel = recipe?.strategy?.inputs.model ?? recipe?.requires_model ?? false
  if (needsModel && !sel.modelId) throw new Error('Chọn model đầu vào strategy trước khi đề xuất.')
  if (recipe?.strategy?.parameters_schema.properties?.odd_conditions && !['odd_conditions', 'objects', 'scenarios'].some(k => { const v = sel.strategyParams?.[k] ?? recipe.strategy?.defaults[k]; return Array.isArray(v) && v.some(s => typeof s === 'string' && s.trim()) })) throw new Error('Nhập ít nhất một ODD, đối tượng hoặc scenario.')
  if (!Number.isInteger(sel.batch) || sel.batch < 1) throw new Error('Số ảnh mỗi đợt phải là số nguyên dương.')
  for (const [key, schema] of Object.entries(recipe?.strategy?.parameters_schema.properties ?? {})) {
    const value = sel.strategyParams?.[key] ?? recipe?.strategy?.defaults[key] ?? schema.default
    if (schema.type === 'integer' || schema.type === 'number') {
      if (typeof value !== 'number' || !Number.isFinite(value) || (schema.type === 'integer' && !Number.isInteger(value)) || (schema.minimum !== undefined && value < schema.minimum) || (schema.maximum !== undefined && value > schema.maximum)) throw new Error(`Tham số ${schema.title ?? key} nằm ngoài giới hạn.`)
    }
  }
  return { recipe: sel.name, config: sel.config ? { ...sel.config, name: sel.name } : undefined, batch_size: sel.batch, seed: sel.seed === '' ? undefined : Number(sel.seed), return_scores: sel.withScores, model_id: needsModel || recipe?.strategy?.inputs.model_optional ? sel.modelId : undefined, strategy_params: sel.strategyParams }
}
const labels: Record<string, string> = { odd_conditions: 'Điều kiện ODD (hợp các dòng)', objects: 'Đối tượng cần tìm', scenarios: 'Scenario độc lập (câu đầy đủ)', contrast_texts: 'Câu đối chứng CLIP', min_similarity: 'Cosine tối thiểu', min_margin: 'Margin tối thiểu', temperature: 'Độ giãn điểm tương phản', candidate_limit: 'Giới hạn shortlist', texts: 'Tình huống cần tìm', negative: 'Loại trừ', candidate_multiplier: 'Phạm vi ứng viên' }
export const RecipePanel: React.FC<{ recipes: RecipeDTO[]; sel: RecipeSel; onChange: (s: RecipeSel) => void }> = ({ recipes, sel, onChange }) => {
  const recipe = recipes.find(r => r.name === sel.name)
  const needsModel = recipe?.strategy?.inputs.model ?? recipe?.requires_model ?? false
  const acceptsModel = needsModel || !!recipe?.strategy?.inputs.model_optional
  const [models, setModels] = useState<{ id: string; name: string; sha256: string }[]>([])
  const [modelStatus, setModelStatus] = useState('Đang tải model…')
  useEffect(() => {
    if (!acceptsModel) return
    let live = true
    setModels([]); setModelStatus('Đang tải model…');
    (PILOT ? apiV2.models().then(list => list.filter(m => m.validation === 'INFERENCE_VERIFIED' && m.task === 'detect')) : api.models().then(list => list.filter(m => m.available))).then(list => { if (live) { setModels(list); setModelStatus('') } }).catch(() => { if (live) setModelStatus('Không tải được danh sách model. Mở lại bảng để thử lại.') })
    return () => { live = false }
  }, [acceptsModel])
  if (!recipe) return <p role="alert">Không tìm thấy strategy trong catalog.</p>
  const properties = Object.fromEntries(Object.entries(recipe.strategy?.parameters_schema.properties ?? {}).map(([k, v]) => [k, { ...v, title: labels[k] ?? v.title }]))
  const oddQueries = !!properties.odd_conditions
  const queryKeys = ['odd_conditions', 'objects', 'scenarios']
  const publicSchema = { ...recipe.strategy?.parameters_schema, properties }
  const value = { ...recipe.strategy?.defaults, ...sel.strategyParams }
  const setParams = (strategyParams: Record<string, unknown>) => onChange({ ...sel, strategyParams })
  const textObjectives = [...(sel.config?.objectives ?? []), ...(sel.config?.candidate_stage?.objectives ?? [])].filter(o => o.type === 'text_match')
  return <div className="space-y-3 text-xs">
    <h3 className="font-semibold">Cấu hình đợt tiếp theo</h3>
    <select aria-label="Cách đề xuất" className={inputClass} value={sel.name} onChange={e => { const next = recipes.find(r => r.name === e.target.value); if (next) onChange(recipeSelFor(next, sel)) }}>
      {recipes.map(r => <option key={r.name} value={r.name} disabled={r.available === false}>{r.strategy?.title ?? RECIPE_LABELS[r.name] ?? r.name}{r.available === false ? ' — chưa khả dụng' : ''}</option>)}
    </select>
    <p>{recipe.description}</p>
    <ol className="list-decimal pl-4 space-y-1">{(recipe.strategy?.steps ?? [recipe.description || 'Chọn ảnh theo cấu hình đã lưu của phiên.']).map((step, i) => <li key={i}>{step}</li>)}</ol>
    <p>Đầu vào: video đã sẵn sàng{needsModel ? ' · model detection đã xác minh' : ''}{(recipe.strategy?.inputs.reference_dataset ?? recipe.requires_training) ? ' · dataset tham chiếu của phiên' : ''}.</p>
    {acceptsModel && <div className="space-y-1">
      <label>Model đầu vào strategy<select aria-label="Model đầu vào strategy" className={inputClass} value={sel.modelId ?? ''} onChange={e => onChange({ ...sel, modelId: e.target.value || undefined })}>
        <option value="">{needsModel ? 'Chọn model để chọn mẫu' : 'Không chạy model · coverage và diversity'}</option>
        {sel.modelId && !models.some(m => m.id === sel.modelId) && <option value={sel.modelId}>Model đã lưu: {sel.modelId}</option>}
        {models.map(m => <option key={m.id} value={m.id}>{m.name} ({m.sha256.slice(0, 8)})</option>)}
      </select></label>
      {modelStatus ? <p role="status">{modelStatus}</p> : !models.length && <p>Chưa có model detection INFERENCE_VERIFIED.</p>}
    </div>}
    {recipe.strategy && (oddQueries ? <>
      <p>Mỗi dòng ODD là một nhánh; kết hợp từng đối tượng × từng ODD rồi hợp kết quả. Scenario bổ sung độc lập, không tự bị ràng buộc bởi hai ô trên.</p>
      <SchemaForm schema={{ properties: Object.fromEntries(Object.entries(properties).filter(([key]) => queryKeys.includes(key))) }} value={value} onChange={setParams} />
      <p>Dùng câu tiếng Anh. CLIP chỉ tìm ứng viên, chưa xác nhận nhãn ODD. Giữ bổ sung coverage; Bỏ chỉ loại đúng frame. Model là tùy chọn để đo thiếu ổn định trên shortlist.</p>
      <details><summary>Đối chứng, ngưỡng và ngân sách ứng viên</summary>
        <p>Ngưỡng heuristic, chưa hiệu chỉnh. Không có ứng viên phù hợp có thể trả batch rỗng; score không phải xác suất.</p>
        <SchemaForm schema={{ properties: Object.fromEntries(Object.entries(properties).filter(([key]) => !queryKeys.includes(key))) }} value={value} onChange={setParams} />
      </details>
    </> : <SchemaForm schema={publicSchema} value={value} onChange={setParams} />)}
    {!recipe.strategy && sel.config && textObjectives.map((o, index) => <SchemaForm key={index} schema={{ type: 'object', properties: { texts: { type: 'array', title: labels.texts, items: { type: 'string' } }, negative: { type: 'array', title: labels.negative, items: { type: 'string' } } } }} value={o.params ?? {}} onChange={params => onChange({ ...sel, config: { ...sel.config!, objectives: sel.config!.objectives.map(x => x === o ? { ...x, params } : x), candidate_stage: sel.config!.candidate_stage ? { ...sel.config!.candidate_stage, objectives: sel.config!.candidate_stage.objectives.map(x => x === o ? { ...x, params } : x) } : undefined } })} />)}
    {(textObjectives.length > 0 || recipe.strategy?.parameters_schema.properties?.texts) && <p>CLIP dùng câu tìm được nhập rõ ràng; không tự suy hoặc dịch mục tiêu phiên. Điểm tương đồng không phải xác suất.</p>}
    <label>Số ảnh mỗi đợt<input aria-label="Số ảnh mỗi đợt" className={inputClass} type="number" min={1} step={1} value={sel.batch} onChange={e => onChange({ ...sel, batch: Number(e.target.value) })} /></label>
    <details><summary>Chi tiết kỹ thuật (chỉ đọc)</summary><pre className="text-[10px] whitespace-pre-wrap break-all">{JSON.stringify(sel.config, null, 2)}</pre></details>
    <button className="underline" onClick={() => onChange(recipeSelFor(recipe, sel))}>Khôi phục mặc định strategy</button>
  </div>
}
