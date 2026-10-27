/**
 * Dữ liệu và thao tác của một phiên (UI_DESIGN §1.5, §6; #61). Quyết định tự lưu ngay qua **hàng đợi tuần tự**: ô đổi trạng thái tức thì
 * (hiển thị tạm, `save: 'saving'`), `PATCH state` lần lượt (kèm proposal_n, shown_at nếu frame thuộc đợt đang xem), phản hồi thay state;
 * lỗi → bỏ trạng thái tạm (ô về trạng thái đã lưu), đánh dấu `save: 'error'`, `retry` gửi lại. Hoàn tác = gửi lại trạng thái trước.
 * `propose` chờ hàng đợi rỗng rồi mới gọi — Enter chỉ yêu cầu đề xuất tiếp, không chốt / lưu batch. `mapVersion` tăng khi có đợt mới / pool đổi;
 * `metricsVersion` tăng trễ ~1 s sau thao tác cuối. Không tải lại session / units / bản đồ sau mỗi quyết định.
 * Pilot (§21.23): keep/drop/undo là draft RAM; một submit decisions+job, direct job poll, no autosave.
 * Khóa giữ/bỏ khi submit/job đang chạy; `feature_not_ready` → danh sách nguồn thiếu + chuẩn bị;
 * unit dựng từ ID trong đề xuất/state (không có route units).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, api, DEFAULT_RECIPE, errorText, type EvidenceDTO, type ProposalDTO, type ProposalExplainDTO, type ProposalJobDTO, type RecipeDTO, type ReviewStateDTO, type SessionDTO, type UnitDTO, type VideoDTO } from '../api'
import { apiV2, type StateV2, type ProposalV2 } from '../api_v2'
import { PILOT } from '../mode'
import { jobOf, pilotUnit, proposalOf, savedVersion, rememberVersion, type PilotJob } from '../pilot_api'
import { seedMediaUrls } from '../media'
import { restoreRecipeSel, proposalRequest, type RecipeSel } from '../components/RecipePanel'
import type { GridItem } from '../components/DashcamThumbnail'
import { ACTION_OF, type Status } from '../ui'

export type Change = { id: string; to: Status }
export type MissingFeature = { source_id: string; feature: 'DINO' | 'CLIP'; state: 'missing' | 'queued' | 'ready' | 'error' }
type Pending = { to: Status; state: 'draft' | 'saving' | 'error'; error?: string }

const EMPTY_STATE: ReviewStateDTO = { kept_ids: [], dropped_ids: [], history: [] }

export function useSessionData(sid: string, videos: VideoDTO[], onSessionChanged: (s: SessionDTO) => void) {
  const [session, setSession] = useState<SessionDTO>()
  const [units, setUnits] = useState<UnitDTO[]>([])
  const [recipes, setRecipes] = useState<RecipeDTO[]>([])
  const [state, setState] = useState<ReviewStateDTO>(EMPTY_STATE)
  const [proposals, setProposals] = useState<ProposalDTO[]>([])
  const [shownN, setShownN] = useState<number>()
  const [shownAt, setShownAt] = useState<Record<number, string>>({})   // proposal n → lúc lưới hiện ra (DecisionDTO.shown_at)
  const [scores, setScores] = useState<{ n: number; scores: NonNullable<ProposalDTO['scores']> }>()
  const [recipeSel, setRecipeSel] = useState<RecipeSel>()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [direct, setDirect] = useState<boolean | undefined>(PILOT ? undefined : false)
  const [savingCheckpoint, setSavingCheckpoint] = useState(false)
  const flight = useRef<{ signature: string; body: Record<string, unknown> } | undefined>(undefined)
  const [proposalJob, setProposalJob] = useState<ProposalJobDTO>()
  const [undoStack, setUndoStack] = useState<Change[][]>([])
  const [pending, setPending] = useState<Map<string, Pending>>(new Map())
  const [mapVersion, setMapVersion] = useState(0)
  const [metricsVersion, setMetricsVersion] = useState(0)
  const [evidence, setEvidence] = useState<Map<string, EvidenceDTO>>(new Map())
  const [explain, setExplain] = useState<ProposalExplainDTO>()
  const [missing, setMissing] = useState<MissingFeature[]>([])
  const metricsTimer = useRef<ReturnType<typeof setTimeout>>(undefined)
  const draft = useRef(new Map<string, Record<string, unknown>>())
  const submitFlight = useRef(false)
  const alive = useRef(true)
  const activeSid = useRef(sid)
  activeSid.current = sid
  useEffect(() => {
    alive.current = true
    draft.current.clear()
    setPending(new Map()); setUndoStack([])
    return () => { alive.current = false; draft.current.clear() }
  }, [sid])
  const queue = useRef<Promise<unknown>>(Promise.resolve())
  const sessionRef = useRef<SessionDTO>(undefined)
  sessionRef.current = session

  const fail = useCallback((e: unknown) => setError(e instanceof ApiError && e.code === 'target_k_reached'
    ? `Đã đủ ngân sách K, không giữ thêm được — bỏ bớt frame hoặc sang Xuất dữ liệu. (${e.message})` : errorText(e)), [])

  useEffect(() => {
    if (!PILOT) return
    let live = true
    apiV2.interactiveConfig().then(async r => {
      if (!live) return
      if (r.enabled) await apiV2.openInteractive(sid, true)
      if (live) setDirect(r.enabled)
    }).catch(e => { if (live) { setDirect(false); fail(e) } })
    return () => { live = false; flight.current = undefined }
  }, [sid, fail])

  useEffect(() => {
    let live = true
    api.proposalJobs(sid).then(list => {
      if (live) {
        const last = list.at(-1)
        if (last && ['queued', 'running'].includes(last.state)) {
          setProposalJob(current => current ?? last)
        } else if (!direct) {
          setProposalJob(current => current ?? last)
        }
      }
    }).catch(fail)
    return () => { live = false }
  }, [sid, fail, direct])
  useEffect(() => {
    if (!proposalJob || !['queued', 'running', 'done'].includes(proposalJob.state)) return
    let live = true
    let polls = 0, failures = 0
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        let job = PILOT ? jobOf(await apiV2.job(proposalJob.id), sid)
          : (await api.proposalJobs(sid)).find(j => j.id === proposalJob.id)
        failures = 0
        if (live && PILOT && job?.state === 'done') {
          const n = proposalJob.proposal_n ?? (await apiV2.jobResult(job.id)).proposal_n as number | null
          job = { ...job, proposal_n: n }
        }
        if (!live || !job) return
        if (job.state === 'done') {
          const [ps, s, st] = await Promise.all([
            PILOT && job.proposal_n != null ? api.proposal(sid, job.proposal_n).then(p => [p]) : api.proposals(sid),
            api.session(sid), api.state(sid),
          ])
          if (!live) return
          if (PILOT && direct) await apiV2.openInteractive(sid, true)
          if (!live) return
          setState(st)
          setProposals(previous => PILOT && job.proposal_n != null
            ? [...previous.filter(p => p.n !== job.proposal_n), ...ps].sort((a, b) => a.n - b.n) : ps)
          setShownN(job.proposal_n ?? undefined); setScores(undefined)
          setSession(s); onSessionChanged(s); setMapVersion(v => v + 1); setMetricsVersion(v => v + 1)
        }
        setProposalJob(job)
        if (job.state === 'error') setError(job.error ?? 'Job đề xuất lỗi')
        if (['queued', 'running'].includes(job.state)) timer = setTimeout(poll, Math.min(1000 * ++polls, 3000))
      } catch (e) { if (live) { fail(e); if (++failures < 5) timer = setTimeout(poll, 3000) } }
    }
    poll()
    return () => { live = false; clearTimeout(timer) }
  }, [sid, proposalJob?.id, fail, direct])

  useEffect(() => {
    let live = true
    Promise.all([api.session(sid), api.units(sid), api.recipes(), api.state(sid), api.proposals(sid)])
      .then(([s, u, r, st, p]) => {
        if (!live) return
        setSession(s); setUnits(u); setRecipes(r); setState(st); setProposals(p)
        // tiếp tục recipe + config của đợt gần nhất (nếu còn), không thì recipe đầu tiên
        const last = p.at(-1), r0 = r.find(x => x.name === last?.input.recipe) ?? r.find(x => x.name === DEFAULT_RECIPE) ?? r[0]
        if (r0) { const restored = restoreRecipeSel(r0, last?.input); setRecipeSel({ ...restored, modelId: restored.modelId ?? s.reference.model_id ?? undefined }) }
      })
      .catch(fail)
    return () => { live = false; clearTimeout(metricsTimer.current) }
  }, [sid, fail])

  const unitById = useMemo(() => {
    const m = new Map(units.map(u => [u.id, u]))
    if (PILOT) for (const id of [...proposals.flatMap(p => p.picks.map(k => k.unit_id)), ...state.kept_ids, ...state.dropped_ids])
      if (!m.has(id)) m.set(id, pilotUnit(id))
    return m
  }, [units, proposals, state])
  const kept = useMemo(() => new Set(state.kept_ids), [state])
  const dropped = useMemo(() => new Set(state.dropped_ids), [state])
  const savedStatus = useCallback((id: string): Status => kept.has(id) ? 'kept' : dropped.has(id) ? 'dropped' : 'undecided', [kept, dropped])
  /** Trạng thái hiển thị: đang lưu → trạng thái tạm; còn lại (kể cả lỗi) → trạng thái đã lưu ở backend. */
  const statusOf = useCallback((id: string): Status => {
    const p = pending.get(id)
    return p && (p.state === 'saving' || p.state === 'draft') ? p.to : savedStatus(id)
  }, [pending, savedStatus])
  const videoName = useCallback((id: string) => videos.find(v => v.id === id)?.name ?? id.slice(0, 8), [videos])
  const shown = proposals.find(p => p.n === shownN) ?? proposals.at(-1)
  const items: GridItem[] = useMemo(() => (shown?.picks ?? []).flatMap(pick => {
    const unit = unitById.get(pick.unit_id)
    return unit ? [{ unit, pick, status: statusOf(pick.unit_id), videoName: videoName(unit.video_id), evidence: evidence.get(pick.unit_id),
                     proposalEvidence: shown?.evidence?.[pick.unit_id],
                     save: PILOT ? undefined : pending.get(pick.unit_id)?.state as 'saving' | 'error' | undefined }] : []
  }), [shown, unitById, statusOf, videoName, evidence, pending])
  const keptUnits = useMemo(() => state.kept_ids.flatMap(id => unitById.get(id) ?? []), [state, unitById])
  const docs = recipes.find(r => r.name === shown?.input.recipe)?.components ?? {}

  useEffect(() => {
    if (shown?.temporary || !shown?.input.execution || !recipeSel?.withScores) return
    let live = true
    api.proposal(sid, shown.n).then(p => { if (live && p.scores) setScores({ n: p.n, scores: p.scores }) }).catch(e => { if (live) fail(e) })
    return () => { live = false }
  }, [sid, shown?.n, recipeSel?.withScores, fail])

  // bằng chứng (#59) + giải thích cấp đợt (#61) cho đợt đang xem
  useEffect(() => {
    if (!shown) return
    if (shown.temporary) return
    setExplain(undefined)
    api.explain(sid, shown.n).then(setExplain).catch(() => undefined)
    const ids = shown.picks.map(p => p.unit_id)
    if (ids.length) api.evidence(sid, ids).then(list => setEvidence(m => new Map([...m, ...list.map(e => [e.unit_id, e] as const)]))).catch(() => undefined)
  }, [sid, shown?.n])

  // lưới của đợt n hiện ra lần đầu → ghi lúc hiện (shown_at); đổi lưới → không hoàn tác về lưới cũ
  useEffect(() => {
    if (shown) setShownAt(m => m[shown.n] ? m : { ...m, [shown.n]: new Date().toISOString() })
    setUndoStack([])
  }, [shown?.n])

  const setPendingFor = (changes: Change[], value: Pending | null, onlyIfTo = false) => setPending(m => {
    const next = new Map(m)
    for (const c of changes) {
      if (onlyIfTo && next.get(c.id)?.to !== c.to) continue   // đã có thao tác mới hơn trên ô này → giữ
      if (value) next.set(c.id, { ...value, to: c.to })
      else next.delete(c.id)
    }
    return next
  })

  /** Xếp một lô thay đổi vào hàng đợi lưu; hiển thị tạm ngay. */
  const enqueue = (changes: Change[]) => {
    if (!changes.length) return
    const ctxShown = shown, at = shown ? shownAt[shown.n] : undefined
    const body = changes.map(c => {
      const inShown = ctxShown?.picks.some(p => p.unit_id === c.id)   // quyết định ngoài đợt đang xem → không proposal_n
      return { unit_id: c.id, action: ACTION_OF[c.to], ...(inShown && ctxShown ? { proposal_n: ctxShown.n, shown_at: at } : {}) }
    })
    setError('')
    if (PILOT) {
      for (let i = 0; i < changes.length; i++) {
        if (savedStatus(changes[i].id) === changes[i].to) draft.current.delete(changes[i].id)
        else draft.current.set(changes[i].id, body[i])
      }
      setPending(m => {
        const next = new Map(m)
        for (const c of changes) {
          if (draft.current.has(c.id)) next.set(c.id, { to: c.to, state: 'draft' })
          else next.delete(c.id)
        }
        return next
      })
      return
    }
    setPendingFor(changes, { to: 'undecided', state: 'saving' })
    queue.current = queue.current.then(async () => {
      try {
        const st = await api.updateState(sid, body)
        setState(st)
        setPendingFor(changes, null, true)
        const s = sessionRef.current
        if (s) { const upd = { ...s, n_kept: st.kept_ids.length, n_dropped: st.dropped_ids.length }; setSession(upd); onSessionChanged(upd) }
        clearTimeout(metricsTimer.current)
        metricsTimer.current = setTimeout(() => setMetricsVersion(v => v + 1), 1000)
      } catch (e) {
        setPendingFor(changes, { to: 'undecided', state: 'error', error: errorText(e) }, true)
        fail(e)
      }
    })
  }

  const jobActive = !!proposalJob && ['queued', 'running'].includes(proposalJob.state)
  const applyCheckpoint = (r: { state: StateV2; proposal: ProposalV2 | null }) => {
    rememberVersion(sid, r.state.state_version)
    setState({ kept_ids: r.state.kept_ids, dropped_ids: r.state.dropped_ids, history: state.history })
    draft.current.clear(); setPending(new Map()); setUndoStack([])
    const p = r.proposal ? proposalOf(r.proposal) : undefined
    if (p) {
      setProposals(ps => [...ps.filter(x => !x.temporary && x.n !== p.n), p])
      setShownN(p.n)
    }
    const current = sessionRef.current
    if (current) { const upd = { ...current, n_kept: r.state.kept_ids.length, n_dropped: r.state.dropped_ids.length }; setSession(upd); onSessionChanged(upd) }
    setMapVersion(v => v + 1); setMetricsVersion(v => v + 1)
    return p
  }
  const decide = (changes: Change[]) => {
    if (PILOT && (jobActive || busy || submitFlight.current)) { setError('Đang đề xuất — chờ đợt mới xong (hoặc Hủy) rồi duyệt tiếp; quyết định lúc này sẽ làm đề xuất bị hủy.'); return }
    const real = changes.filter(c => statusOf(c.id) !== c.to)
    if (!real.length) return
    setUndoStack(s => [...s.slice(-49), real.map(c => ({ id: c.id, to: statusOf(c.id) }))])   // ghi ngay: Ctrl+Z nhanh vẫn đúng
    enqueue(real)
  }

  const undo = () => {
    if (PILOT && (jobActive || busy || submitFlight.current)) return
    const last = undoStack.at(-1)
    if (!last) return
    setUndoStack(s => s.slice(0, -1))
    enqueue(last.filter(c => statusOf(c.id) !== c.to))
  }

  /** Gửi lại các thao tác lưu lỗi. */
  const retry = () => {
    const failed = [...pending].filter(([, p]) => p.state === 'error').map(([id, p]) => ({ id, to: p.to }))
    enqueue(failed.filter(c => savedStatus(c.id) !== c.to))
  }

  /** Đề xuất đợt tiếp theo (chờ lưu xong); trả số frame chưa duyệt của đợt trước để UI ghi chú. */
  const propose = async (): Promise<ProposalDTO | undefined> => {
    if (!recipeSel || submitFlight.current) return
    submitFlight.current = true
    setBusy(true); setError('')
    try {
      await queue.current
      const selectedRecipe = recipes.find(r => r.name === recipeSel.name)
      const request = { ...proposalRequest(recipeSel, selectedRecipe), ...(PILOT ? { changes: [...draft.current.values()] } : {}) }
      const isBackground = !!selectedRecipe?.background
      if (PILOT && direct && isBackground && (shown?.temporary || draft.current.size)) {
        const kept = new Set(state.kept_ids), dropped = new Set(state.dropped_ids)
        for (const [id, change] of draft.current) {
          kept.delete(id); dropped.delete(id)
          if (change.action === 'keep') kept.add(id)
          if (change.action === 'drop') dropped.add(id)
        }
        const checkpoint = await apiV2.checkpoint(sid, {
          batch_id: shown?.temporary ? shown.batch_id : null, expected_state_version: savedVersion(sid),
          kept_ids: [...kept].sort(), dropped_ids: [...dropped].sort(),
        })
        if (!alive.current || activeSid.current !== sid) return
        applyCheckpoint(checkpoint)
        request.changes = []
      }
      if (PILOT && direct && !isBackground) {
        const kept = new Set(state.kept_ids), dropped = new Set(state.dropped_ids)
        for (const [id, change] of draft.current) {
          kept.delete(id); dropped.delete(id)
          if (change.action === 'keep') kept.add(id)
          if (change.action === 'drop') dropped.add(id)
        }
        const body = { recipe: request.recipe, config: request.config, batch_size: request.batch_size,
          seed: request.seed, return_scores: request.return_scores, strategy_params: request.strategy_params, expected_state_version: savedVersion(sid),
          previous_batch_id: shown?.temporary ? shown.batch_id : null, save_current: draft.current.size > 0,
          kept_ids: [...kept].sort(), dropped_ids: [...dropped].sort() }
        const signature = JSON.stringify(body)
        if (!flight.current || flight.current.signature !== signature) flight.current = {
          signature, body: { ...body, seed: body.seed ?? Math.floor(Math.random() * 2 ** 31), request_id: crypto.randomUUID() },
        }
        const result = await apiV2.interactiveProposal(sid, flight.current.body)
        if (!alive.current || activeSid.current !== sid) return
        flight.current = undefined
        if (result.checkpoint) applyCheckpoint(result.checkpoint)
        seedMediaUrls(result.images)
        const p = { ...proposalOf(result.proposal), temporary: true, batch_id: result.batch_id }
        setProposals(ps => [...ps.slice(-31), p]); setShownN(p.n)
        setScores(p.scores ? { n: p.n, scores: p.scores } : undefined)
        setExplain(result.proposal.explain as unknown as ProposalExplainDTO | undefined)
        return p
      }
      if (PILOT || isBackground) {
        const accepted = await api.queueProposal(sid, request)
        if (!alive.current) return
        setProposalJob(accepted)
        if (PILOT) {
          draft.current.clear(); setPending(new Map()); setUndoStack([])
          const committed = await api.state(sid)
          if (alive.current) {
            setState(committed)
            const s = sessionRef.current
            if (s) {
              const updated = { ...s, n_kept: committed.kept_ids.length, n_dropped: committed.dropped_ids.length }
              setSession(updated); onSessionChanged(updated)
            }
          }
        }
        return
      }
      const p = await api.propose(sid, request)
      setProposals(ps => [...ps, p]); setShownN(p.n)
      setScores(p.scores ? { n: p.n, scores: p.scores } : undefined)
      const s = await api.session(sid); setSession(s); onSessionChanged(s)
      setMapVersion(v => v + 1); setMetricsVersion(v => v + 1)
      return p
    } catch (e) {
      if (activeSid.current === sid && e instanceof ApiError && e.details.checkpoint) {
        applyCheckpoint(e.details.checkpoint as { state: StateV2; proposal: ProposalV2 | null })
      }
      if (e instanceof ApiError && e.code === 'feature_not_ready') {
        const list = (e.details.missing ?? []) as { source_id: string; feature: 'DINO' | 'CLIP' }[]
        setMissing(list.map(m => ({ ...m, state: 'missing' })))
        setError('Một số video trong phiên chưa có đặc trưng recipe cần — bấm "Chuẩn bị" rồi đề xuất lại khi xong.')
      } else fail(e)
    } finally { if (activeSid.current === sid) { submitFlight.current = false; if (alive.current) setBusy(false) } }
  }

  const saveSession = async () => {
    if (!direct || savingCheckpoint || busy || submitFlight.current) return
    submitFlight.current = true
    setSavingCheckpoint(true); setError('')
    try {
      const kept = new Set(state.kept_ids), dropped = new Set(state.dropped_ids)
      for (const [id, c] of draft.current) {
        kept.delete(id); dropped.delete(id)
        if (c.action === 'keep') kept.add(id)
        if (c.action === 'drop') dropped.add(id)
      }
      const r = await apiV2.checkpoint(sid, { batch_id: shown?.temporary ? shown.batch_id : null,
        expected_state_version: savedVersion(sid), kept_ids: [...kept].sort(), dropped_ids: [...dropped].sort() })
      if (!alive.current || activeSid.current !== sid) return
      return applyCheckpoint(r) ?? shown
    } catch (e) { fail(e) }
    finally { if (activeSid.current === sid) { submitFlight.current = false; if (alive.current) setSavingCheckpoint(false) } }
  }

  /** Sau khi thêm video vào pool: tải lại units, bản đồ — đợt đang xem giữ nguyên. */
  const poolChanged = async (s: SessionDTO) => {
    setSession(s); onSessionChanged(s)
    setUnits(await api.units(sid))
    setMapVersion(v => v + 1); setMetricsVersion(v => v + 1)
  }

  /** Pilot: xếp job chuẩn bị DINO/CLIP cho nguồn còn thiếu, theo dõi tới khi sẵn sàng. */
  const prepareMissing = async () => {
    for (const m of missing.filter(x => x.state === 'missing' || x.state === 'error')) {
      try {
        const r = await apiV2.prepare(m.source_id, m.feature)
        setMissing(list => list.map(x => x === m ? { ...x, state: r.ready ? 'ready' : 'queued' } : x))
      } catch (e) { setMissing(list => list.map(x => x === m ? { ...x, state: 'error' } : x)); fail(e) }
    }
  }
  useEffect(() => {
    if (!PILOT || !missing.some(m => m.state === 'queued')) return
    const t = setInterval(async () => {
      const done = await Promise.all(missing.map(async m => m.state !== 'queued' ? m
        : (await apiV2.source(m.source_id).catch(() => null))?.[m.feature === 'DINO' ? 'dino' : 'clip'].ready ? { ...m, state: 'ready' as const } : m))
      setMissing(done.every(m => m.state === 'ready') ? [] : done)
    }, 5000)
    return () => clearInterval(t)
  }, [missing])

  /** Pilot: hủy / thử lại job đề xuất đang hiện. */
  const cancelProposal = async () => {
    const job = proposalJob as PilotJob | undefined
    if (!job) return
    try { await apiV2.cancelJob(job.id, job.state_version); setProposalJob({ ...job, state: 'running', stage: 'đang chờ tiến trình dừng an toàn' }) } catch (e) { fail(e) }
  }
  const retryProposal = async () => {
    const job = proposalJob as PilotJob | undefined
    if (!job) return
    setError('')
    try { setProposalJob(jobOf(await apiV2.retryJob(job.id), sid)) }
    catch (e) { fail(e) }
  }

  const draftCount = PILOT ? [...pending.values()].filter(p => p.state === 'draft').length : 0
  const saving = [...pending.values()].filter(p => p.state === 'saving').length
  const failed = [...pending.values()].filter(p => p.state === 'error').length

  return {
    session, units, unitById, recipes, state, proposals, shown, setShownN, scores, recipeSel, setRecipeSel, error, setError,
    busy: busy || savingCheckpoint || direct === undefined || !!proposalJob && ['queued', 'running'].includes(proposalJob.state), proposalJob,
    items, keptUnits, docs, statusOf, evidence, explain, videoName, canUndo: undoStack.length > 0, saving, failed, draftCount,
    mapVersion, metricsVersion, decide, undo, retry, propose, poolChanged,
    jobActive, missing, prepareMissing, cancelProposal, retryProposal,
    direct, savingCheckpoint, saveSession,
  }
}
