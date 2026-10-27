/** Form sinh từ JSON schema của Params (params_schema của recipe / view). Ô trống = không gửi khoá đó (dùng mặc định ở BE). */
import type { JsonSchema } from './api'
import { inputClass } from './ui'

type Props = { schema: JsonSchema; value: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void }

const base = (s: JsonSchema): JsonSchema => s.anyOf?.find(x => x.type !== 'null') ?? s

export function SchemaForm({ schema, value, onChange }: Props) {
  const props = Object.entries(schema.properties ?? {})
  if (!props.length) return <span className="block text-[11px] text-[var(--text-muted)]">không có tham số</span>
  const set = (k: string, v: unknown) => {
    const next = { ...value }
    if (v === undefined) delete next[k]
    else next[k] = v
    onChange(next)
  }
  return (
    <div className="grid grid-cols-2 gap-2">
      {props.map(([k, raw]) => {
        const s = base(raw)
        const v = value[k] ?? raw.default
        const hint = raw.default === undefined || raw.default === null ? '' : JSON.stringify(raw.default)
        let field
        if (s.enum) {
          field = <select className={inputClass} value={v === undefined ? '' : String(v)} onChange={e => set(k, e.target.value || undefined)}>
            <option value="">(mặc định {hint})</option>
            {s.enum.map(o => <option key={String(o)}>{String(o)}</option>)}
          </select>
        } else if (s.type === 'boolean') {
          field = <input type="checkbox" checked={Boolean(v ?? raw.default)} onChange={e => set(k, e.target.checked)} />
        } else if (s.type === 'integer' || s.type === 'number') {
          field = <input className={inputClass} type="number" min={s.minimum} max={s.maximum} step={s.type === 'integer' ? 1 : 'any'} placeholder={hint} value={v === undefined ? '' : String(v)}
            onChange={e => set(k, e.target.value === '' ? undefined : Number(e.target.value))} />
        } else if (s.type === 'array') {
          const num = s.items?.type === 'integer' || s.items?.type === 'number'
          const multiline = !num && (['texts', 'negative', 'odd_conditions', 'objects', 'scenarios', 'contrast_texts'].includes(k))
          // sửa xong (blur) mới parse, để gõ được dấu phẩy
          field = multiline ? <textarea className={inputClass} rows={3} key={JSON.stringify(v)} placeholder={s.description ?? (k === 'texts' ? 'pedestrian at night (mỗi dòng một câu)' : 'Mỗi dòng một câu tiếng Anh')} defaultValue={Array.isArray(v) ? v.join('\n') : ''}
            onBlur={e => set(k, e.target.value.split('\n').map(x => x.trim()).filter(Boolean))} />
          : <input className={inputClass} key={JSON.stringify(v)} placeholder={hint} defaultValue={Array.isArray(v) ? v.join(', ') : ''}
            onBlur={e => {
              const parts = e.target.value.split(',').map(x => x.trim()).filter(Boolean)
              set(k, parts.length ? parts.map(x => num ? Number(x) : x) : undefined)
            }} />
        } else {
          field = <input className={inputClass} placeholder={hint} value={v === undefined ? '' : String(v)} onChange={e => set(k, e.target.value || undefined)} />
        }
        return (
          <label key={k} title={raw.description} className={`flex flex-col gap-0.5 text-[11px] font-mono text-[var(--text-secondary)] ${['odd_conditions', 'objects', 'scenarios', 'contrast_texts'].includes(k) ? 'col-span-2' : ''}`}>
            {raw.title ?? k}{field}
          </label>
        )
      })}
    </div>
  )
}
