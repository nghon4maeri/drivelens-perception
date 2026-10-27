/** YOLO results belong to the exact proposal/model; GET reads cache, only button POST runs (CONTRACTS §17). */
import { useEffect, useRef, useState } from 'react'
import { api, errorText, type ModelDTO, type ProposalDTO, type YoloBatchDTO } from '../api'

export function useBatchYolo(sid: string, shown: ProposalDTO | undefined, referenceId: string | null | undefined, ensureSaved?: () => Promise<ProposalDTO | undefined>) {
  const [models, setModels] = useState<ModelDTO[]>([])
  const [modelId, setModelId] = useState('')
  const [result, setResult] = useState<YoloBatchDTO | null>(null)
  const [busyKey, setBusyKey] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const initialized = useRef(false)
  const key = `${sid}/${shown?.n ?? ''}/${modelId}`
  const active = useRef(key)
  active.current = key
  useEffect(() => {
    let live = true
    api.models().then(r => { if (live) setModels(r) }).catch(e => { if (live) setError(errorText(e)) })
    return () => { live = false }
  }, [sid])
  useEffect(() => {
    if (!initialized.current && referenceId !== undefined) { initialized.current = true; setModelId(referenceId ?? '') }
  }, [referenceId])
  useEffect(() => {
    let live = true
    setResult(null); setError(''); setLoading(false)
    if (!shown?.temporary && shown?.picks.length && modelId) {
      setLoading(true)
      api.batchYolo(sid, shown.n, modelId).then(r => { if (live) setResult(r) }).catch(e => { if (live) setError(errorText(e)) })
        .finally(() => { if (live) setLoading(false) })
    }
    return () => { live = false }
  }, [sid, shown?.n, shown?.temporary, modelId])
  useEffect(() => () => { active.current = '' }, [])
  const run = async () => {
    if (!shown || !modelId) return
    const requestKey = key
    setBusyKey(requestKey); setError('')
    try {
      const durable = shown.temporary ? await ensureSaved?.() : shown
      if (!durable || durable.temporary) return
      const r = await api.runBatchYolo(sid, durable.n, modelId)
      if (active.current === requestKey) setResult(r)
    } catch (e) {
      if (active.current === requestKey) setError(errorText(e))
    }
    finally { setBusyKey(k => k === requestKey ? '' : k) }
  }
  // Gate during render too: effects clear state after paint, so an old model must never flash on a new batch.
  const current = result?.session_id === sid && result.proposal_n === shown?.n && result.model_id === modelId ? result : null
  return { models, modelId, setModelId, result: current, busy: !!busyKey, loading, error, run }
}
