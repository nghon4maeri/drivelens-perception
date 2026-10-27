/**
 * Màn "Phiên mới" (`#new`, UI_DESIGN §3, #58): trái chọn video (Sẵn sàng chọn được; đang tiền xử lý hiện tiến độ; tải lên tại chỗ),
 * phải mục tiêu (mẫu bấm điền) / ngân sách K (gợi ý theo số frame ước tính = thời lượng × 4 fps) / tên (tự điền) / tóm tắt.
 * "Tạo & bắt đầu duyệt" = POST /sessions → đề xuất bằng recipe/config đã chọn → vào Studio; lỗi giữ form để thử lại.
 */
import React, { useEffect, useState } from 'react'
import { Check, Loader2, Plus } from 'lucide-react'
import { api, DEFAULT_RECIPE, RECIPE_LABELS, datasetBusy, datasetReady, errorText, type DatasetDTO, type ModelDTO, type RecipeDTO, type SessionDTO, type VideoDTO } from '../api'
import { PrepareStatusLine, ClipStatusLine, UploadButton, useUpload } from '../components/VideoLibrary'
import { sessionHref } from '../types/navigation'
import { Button, EmptyState, ErrorBanner, Field, Label, Panel, ScreenHeader, fmtDuration, inputClass } from '../ui'

const FPS = 4   // decoder mặc định (pipeline/prepare.py DEFAULT_SPEC) — chỉ để ước tính số frame
const GOALS = [
  'Giữ frame khác biệt về cảnh, ánh sáng, đối tượng; bỏ frame mờ hoặc gần trùng frame đã giữ.',
  'Người đi bộ ban đêm hoặc thiếu sáng, nhìn đủ rõ để gán nhãn.',
  'Xe máy cắt ngang hoặc áp sát xe, đối tượng không bị che khuất quá nhiều.',
]
const K_PRESETS = [50, 100, 200]

interface Props {
  preselect: string[]
  videos: VideoDTO[]
  onVideosChanged: () => Promise<unknown>
  onCreated: (s: SessionDTO) => void
}

export const NewSessionScreen: React.FC<Props> = ({ preselect, videos, onVideosChanged, onCreated }) => {
  const [datasets, setDatasets] = useState<DatasetDTO[]>([])
  const [models, setModels] = useState<ModelDTO[]>([])
  const [modelId, setModelId] = useState('')
  const [modelError, setModelError] = useState('')
  useEffect(() => { let live = true; api.models().then(r => { if (live) setModels(r) }).catch(e => { if (live) setModelError(errorText(e)) }); return () => { live = false } }, [])
  const [refs, setRefs] = useState<Record<string, string>>({})
  const [refError, setRefError] = useState('')
  const preparing = datasets.some(d => d.versions.some(datasetBusy))
  useEffect(() => {
    let live = true
    const load = () => api.datasets().then(d => { if (live) { setDatasets(d); setRefError('') } }).catch(e => { if (live) setRefError(errorText(e)) })
    load()
    const timer = preparing ? setInterval(load, 2000) : undefined
    return () => { live = false; clearInterval(timer) }
  }, [preparing])
  const refsReady = Object.entries(refs).every(([did, vid]) => datasets.find(d => d.id === did)?.versions.some(v => v.id === vid && datasetReady(v)))
  const [selected, setSelected] = useState<string[]>(preselect)
  const [goal, setGoal] = useState(GOALS[0])
  const [recipeName, setRecipeName] = useState(DEFAULT_RECIPE)
  const [recipes, setRecipes] = useState<RecipeDTO[]>([])
  useEffect(() => { api.recipes().then(setRecipes).catch(e => setError(errorText(e))) }, [])
  const [texts, setTexts] = useState('cars on a rainy street')
  const [negative, setNegative] = useState('')
  const [targetK, setTargetK] = useState(100)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [pending, setPending] = useState<{ signature: string; session: SessionDTO } | null>(null)
  const up = useUpload(onVideosChanged, ids => setSelected(s => [...new Set([...s, ...ids])]))
  useEffect(() => setSelected(preselect), [preselect.join(',')])

  const shown = videos
  const picked = videos.filter(v => selected.includes(v.id))
  const ready = picked.filter(v => v.prepared)
  const frames = Math.round(picked.reduce((a, v) => a + v.duration_s * FPS, 0))
  const autoName = picked.map(v => v.name.replace(/\.[^.]+$/, '')).join(' + ').slice(0, 60)
  const selectedRecipe = recipes.find(r => r.name === recipeName)
  const clip = [...(selectedRecipe?.config?.objectives ?? []), ...(selectedRecipe?.config?.candidate_stage?.objectives ?? [])].some(o => o.type === 'text_match')
  const valid = !!selectedRecipe && refsReady && picked.length > 0 && ready.length === picked.length && goal.trim() !== '' && targetK >= 1
    && (!modelId || models.some(m => m.id === modelId && m.available) && Object.keys(refs).length > 0)
    && (!clip || texts.trim() !== '' && picked.every(v => v.clip_prepared))
    && (!selectedRecipe?.requires_training || Object.keys(refs).length > 0)
    && (!selectedRecipe?.requires_model || !!modelId)
  const toggle = (v: VideoDTO) => setSelected(s => s.includes(v.id) ? s.filter(x => x !== v.id) : [...s, v.id])

  const create = async () => {
    setError('')
    try {
      setBusy('Đang tạo phiên…')
      const input = { pool: { video_ids: selected }, reference: { dataset_version_ids: Object.values(refs), ...(modelId ? { model_id: modelId } : {}) }, goal: goal.trim(), target_k: targetK, name: (name.trim() || autoName) || undefined }
      const signature = JSON.stringify(input)
      const s = pending?.signature === signature ? pending.session : await api.createSession(input)
      setPending({ signature, session: s })
      setBusy('Đang đề xuất đợt đầu…')
      const recipe = (await api.recipes()).find(r => r.name === recipeName)
      if (!recipe) throw new Error('Không tìm thấy cách đề xuất đã chọn; tải lại trang và thử lại.')
      {
        const config = structuredClone(recipe.config)
        if (clip && config) {
          const query = [...config.objectives, ...(config.candidate_stage?.objectives ?? [])].find(o => o.type === 'text_match')!
          query.params = { ...query.params, texts: texts.split('\n').map(x => x.trim()).filter(Boolean), negative: negative.split('\n').map(x => x.trim()).filter(Boolean) }
        }
        const request = { recipe: recipe.name, config: config ?? undefined, batch_size: 16, return_scores: true }
        if (recipe.background) await api.queueProposal(s.id, request)
        else await api.propose(s.id, request)
      }
      onCreated(await api.session(s.id))
      location.hash = sessionHref(s.id)
    } catch (e) { setError(errorText(e)); setBusy('') }
  }

  return (
    <div className="flex-1 flex overflow-hidden">
      <div className="flex-1 overflow-y-auto px-8 py-6 space-y-5">
        <ScreenHeader icon={<Plus className="w-5 h-5 text-[var(--kept-border)]" />} title="Phiên mới"
          subtitle="Chọn video cho pool, đặt mục tiêu và ngân sách K. Thêm video vào phiên sau cũng được."
          actions={<UploadButton variant="secondary" busy={!!up.busy} onFiles={up.upload} />} />
        {up.busy && <p className="text-xs text-[var(--text-secondary)] flex items-center gap-2"><Loader2 className="w-3.5 h-3.5 animate-spin" />{up.busy}</p>}
        <ErrorBanner message={up.error} onClose={() => up.setError('')} />
        <Label>Video ({shown.length}) — chọn video Sẵn sàng</Label>
        {!shown.length
          ? <EmptyState title="Chưa có video">Tải video lên hoặc vào <a href="#videos" className="underline">Kho video</a>. Bấm chạy DINO khi cần; xong thì chọn được vào phiên.</EmptyState>
          : (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(190px,1fr))] gap-3">
              {shown.map(v => {
                const on = selected.includes(v.id)
                return (
                  <div key={v.id} role="checkbox" aria-checked={on} tabIndex={0} onClick={() => v.prepared && toggle(v)}
                    onKeyDown={e => { if ((e.key === ' ' || e.key === 'Enter') && v.prepared) { e.preventDefault(); toggle(v) } }}
                    className={`rounded-xl overflow-hidden bg-[var(--canvas-subtle)] ${v.prepared ? 'cursor-pointer' : 'opacity-70'} ${on ? 'ring-2 ring-[var(--kept-border)]' : ''}`}>
                    <div className="relative aspect-video bg-[#181B1F]">
                      <img src={v.thumb_url} alt="" loading="lazy" className="w-full h-full object-cover" />
                      <span className={`absolute top-1.5 left-1.5 w-5 h-5 rounded-md flex items-center justify-center ${on ? 'bg-[var(--kept-border)] text-white' : 'bg-black/50 text-transparent'}`}>
                        <Check className="w-3.5 h-3.5" /></span>
                      <span className="absolute bottom-1.5 right-1.5 px-1.5 py-0.5 rounded bg-black/70 text-[11px] font-mono text-white">{fmtDuration(v.duration_s)}</span>
                    </div>
                    <div className="p-2.5 space-y-1.5">
                      <div className="text-xs font-medium truncate" title={v.name}>{v.name}</div>
                      {!v.prepared && <PrepareStatusLine video={v} onChanged={onVideosChanged} />}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
      </div>

      <aside className="w-[400px] shrink-0 h-full overflow-y-auto bg-[var(--canvas-subtle)] p-6 space-y-5">
        <Field label="Cách đề xuất">
          <select aria-label="Cách đề xuất" className={inputClass} value={recipeName} onChange={e => setRecipeName(e.target.value)}>
            {Object.entries(RECIPE_LABELS).map(([name, label]) => <option key={name} value={name}>{label}</option>)}
          </select>
        </Field>
        {clip && <div className="space-y-3 text-xs">
          <Field label="Câu tìm CLIP — mỗi dòng một câu"><textarea aria-label="Câu tìm CLIP" className={inputClass} rows={3} value={texts} onChange={e => setTexts(e.target.value)} /></Field>
          <Field label="Câu loại trừ (tuỳ chọn)"><textarea aria-label="Câu loại trừ CLIP" className={inputClass} rows={2} value={negative} onChange={e => setNegative(e.target.value)} /></Field>
          <p>Gợi ý câu ngắn tiếng Anh. CLIP dùng câu tìm này, chưa tự suy/dịch mục tiêu phiên; tương đồng không phải xác suất.</p>
          {picked.map(v => <div key={v.id}><p>{v.name}</p><ClipStatusLine video={v} onChanged={onVideosChanged} /></div>)}
        </div>}
        <Field label="Mục tiêu — thế nào là đáng giữ">
          <textarea className={`${inputClass} min-h-[80px] text-sm`} value={goal} onChange={e => setGoal(e.target.value)} />
        </Field>
        <div className="flex flex-wrap gap-1.5 -mt-3">
          {GOALS.map((g, i) => <Button key={i} size="sm" variant={goal === g ? 'primary' : 'secondary'} onClick={() => setGoal(g)} title={g}>{['Đa dạng cảnh', 'Người đi bộ ban đêm', 'Xe máy cắt ngang'][i]}</Button>)}
        </div>

        <Field label={`Ngân sách K — số frame muốn chọn${frames ? ` (pool ≈ ${frames.toLocaleString('vi-VN')} frame)` : ''}`}>
          <input className={`${inputClass} text-sm`} type="number" min={1} value={targetK} onChange={e => setTargetK(Number(e.target.value))} />
        </Field>
        <div className="flex flex-wrap gap-1.5 -mt-3">
          {K_PRESETS.map(k => (
            <Button key={k} size="sm" variant={targetK === k ? 'primary' : 'secondary'} onClick={() => setTargetK(k)}>
              {k}{frames ? ` · ${((100 * k) / frames).toFixed(k / frames < 0.1 ? 1 : 0)}%` : ''}</Button>
          ))}
        </div>

        <Field label="Tên phiên">
          <input className={`${inputClass} text-sm`} value={name} onChange={e => setName(e.target.value)} placeholder={autoName || 'tự đặt theo tên video'} />
        </Field>

        <div className="space-y-2 text-xs">
          <Field label="YOLO tham chiếu (tuỳ chọn)">
            <select aria-label="YOLO tham chiếu" className={inputClass} value={modelId} disabled={!!busy} onChange={e => setModelId(e.target.value)}>
              <option value="">Chưa chọn model</option>
              {models.map(m => <option key={m.id} value={m.id} disabled={!m.available}>{m.name}{m.available ? '' : ' · file không sẵn sàng'}</option>)}
            </select>
          </Field>
          <ErrorBanner message={modelError} />
          <a href="#models" className="underline">Upload mô hình tham chiếu</a>
          <Label>Dataset tham chiếu {modelId ? '(bắt buộc khi chọn model)' : '(tuỳ chọn)'}</Label>
          <p className="text-[var(--text-muted)]">Chọn dữ liệu đã dùng train/finetune YOLO này. Liên kết do bạn xác nhận. Recipe bổ sung dữ liệu dùng dataset train và consistency YOLO để chọn ứng viên; trong Studio vẫn có thể chạy YOLO riêng từng đợt.</p>
          <ErrorBanner message={refError} />
          {!datasets.length && <a href="#datasets" className="underline">Import dataset trong Kho dữ liệu</a>}
          {datasets.map(d => {
            const readyVersions = d.versions.filter(datasetReady)
            return <div key={d.id} className="flex gap-2 items-center">
              <label className="flex-1 flex gap-2 items-center"><input type="checkbox" disabled={!readyVersions.length || !!busy} checked={!!refs[d.id]} onChange={e => setRefs(r => {
                const next = { ...r }; if (e.target.checked) next[d.id] = readyVersions[0].id; else delete next[d.id]; return next
              })} />{d.name}{!readyVersions.length && <span className="text-[var(--text-muted)]">chưa sẵn sàng</span>}</label>
              {refs[d.id] && <select aria-label={`Phiên bản tham chiếu ${d.name}`} className={inputClass} value={refs[d.id]} disabled={!!busy} onChange={e => setRefs(r => ({ ...r, [d.id]: e.target.value }))}>
                {d.versions.filter(v => datasetReady(v) || v.id === refs[d.id]).map(v => <option key={v.id} value={v.id} disabled={!datasetReady(v)}>v{v.n} · {v.n_images} ảnh{datasetReady(v) ? '' : ' · chưa sẵn sàng'}</option>)}
              </select>}
            </div>
          })}
          <p className="text-[var(--text-muted)]">Phiên bản tham chiếu được cố định khi tạo phiên.</p>
        </div>

        <Panel className="!bg-[var(--card-bg)] p-4 space-y-1 text-xs">
          <Label>Tóm tắt</Label>
          <p>{picked.length} video · ≈ {frames.toLocaleString('vi-VN')} frame · K = {targetK}{frames ? ` (${Math.min(100, (100 * targetK) / frames).toFixed(1)}% pool)` : ''}</p>
          {picked.length > ready.length && <p className="text-amber-700 dark:text-amber-300">{picked.length - ready.length} video chưa tiền xử lý xong — chờ xong mới tạo được.</p>}
          <p className="text-[var(--text-muted)]">Tạo xong hệ thống tự đề xuất đợt 16 frame đầu tiên.</p>
        </Panel>
        <ErrorBanner message={error} onClose={() => setError('')} />
        <Button variant="primary" size="md" className="w-full !py-3" disabled={!valid || !!busy} onClick={create}
          icon={busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}>
          {busy || 'Tạo & bắt đầu duyệt'}</Button>
      </aside>
    </div>
  )
}
