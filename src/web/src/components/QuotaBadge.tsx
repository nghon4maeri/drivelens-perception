import { useEffect, useState } from 'react'
import { apiV2, type QuotaDTO } from '../api_v2'
import { Cpu, Zap, Clock, AlertTriangle } from 'lucide-react'

export function QuotaBadge() {
  const [quota, setQuota] = useState<QuotaDTO | null>(null)
  const [error, setError] = useState(false)

  const fetchQuota = () => {
    apiV2
      .quota()
      .then(q => {
        setQuota(q)
        setError(false)
      })
      .catch(() => {
        setError(true)
      })
  }

  useEffect(() => {
    fetchQuota()
    const timer = setInterval(fetchQuota, 60000)
    return () => clearInterval(timer)
  }, [])

  if (error || !quota) {
    return null
  }

  // Tính thời gian còn lại tới reset
  const resetMs = new Date(quota.next_reset_at).getTime() - Date.now()
  const resetMinutes = Math.max(0, Math.floor(resetMs / (1000 * 60)))
  const resetHours = Math.floor(resetMinutes / 60)
  const resetRemMin = resetMinutes % 60
  const resetText = resetHours > 0 ? `${resetHours}h ${resetRemMin}m` : `${resetRemMin}m`

  const cpuSec = Math.round((quota.cpu.base_available + quota.cpu.bonus_available) / 1000)
  const gpuMin = Math.round((quota.gpu.base_available + quota.gpu.bonus_available) / 60000)

  return (
    <div className="p-2.5 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-muted)] text-xs space-y-1.5">
      <div className="flex items-center justify-between text-[var(--text-muted)] font-medium">
        <span className="flex items-center gap-1.5">
          <Clock className="w-3.5 h-3.5" />
          Hạn mức 4h UTC
        </span>
        <span className="text-[10px] text-[var(--text-subtle)]">Reset sau {resetText}</span>
      </div>

      <div className="grid grid-cols-2 gap-2 pt-0.5">
        <div className="flex items-center gap-1.5">
          <Cpu className="w-3.5 h-3.5 text-blue-400" />
          <div>
            <div className="text-[10px] text-[var(--text-subtle)]">CPU</div>
            <div className="font-semibold text-[var(--text-primary)]">{quota.cpu.enabled === false ? 'Không hạn mức' : `${cpuSec}s`}</div>
          </div>
        </div>

        <div className="flex items-center gap-1.5">
          <Zap className="w-3.5 h-3.5 text-amber-400" />
          <div>
            <div className="text-[10px] text-[var(--text-subtle)]">GPU</div>
            <div className="font-semibold text-[var(--text-primary)]">{gpuMin}m</div>
          </div>
        </div>
      </div>

      {!quota.admission.allowed && (
        <div className="flex items-center gap-1 text-[11px] text-red-400 pt-1 border-t border-[var(--border-subtle)]">
          <AlertTriangle className="w-3 h-3 flex-shrink-0" />
          <span>{quota.admission.reason || 'Đã chạm trần hạn mức'}</span>
        </div>
      )}
    </div>
  )
}
