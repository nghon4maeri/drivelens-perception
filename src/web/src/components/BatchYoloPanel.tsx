/** Explicit YOLO button/model picker; shows only this proposal's result (CONTRACTS §17). */
import type { useBatchYolo } from '../hooks/useBatchYolo'
import { Button, ErrorBanner, inputClass } from '../ui'
import { PILOT } from '../mode'

export function BatchYoloPanel({ yolo, nImages }: { yolo: ReturnType<typeof useBatchYolo>; nImages: number }) {
  const totalBoxes = yolo.result?.items.reduce((n, row) => n + row.detections.length, 0) ?? 0
  return <div className="w-full max-w-[1100px] space-y-1 text-xs">
    <p>Kiểm tra sau chọn mẫu · chạy detector trên đợt đang xem.</p>
    <div className="flex flex-wrap items-center gap-2">
      <select aria-label="Model kiểm tra sau chọn mẫu" className={`${inputClass} !w-auto max-w-72 !py-1.5`} value={yolo.modelId} disabled={yolo.busy} onChange={e => yolo.setModelId(e.target.value)}>
        <option value="">Chọn model chạy YOLO</option>
        {yolo.models.map(m => <option key={m.id} value={m.id} disabled={!m.available}>{m.name}{m.available ? '' : PILOT ? ' · chưa xác minh' : ' · file không sẵn sàng'}</option>)}
      </select>
      <Button size="sm" disabled={!yolo.modelId || !nImages || yolo.busy || yolo.loading || !yolo.models.some(m => m.id === yolo.modelId && m.available)}
        title={`Chỉ chạy YOLO trên ${nImages} ảnh của đợt đang xem; giữ nguyên đề xuất và quyết định.`} onClick={yolo.run}>
        {yolo.busy ? (PILOT ? 'YOLO đang chạy trên worker…' : 'Đang chạy YOLO…') : `Chạy YOLO · ${nImages} ảnh`}
      </Button>
      {PILOT ? !yolo.models.length && <span className="text-[var(--text-muted)]">Chưa có model hệ thống — operator import.</span>
        : <a href="#models" className="underline text-[var(--text-secondary)]">Upload model</a>}
      {yolo.loading && <span>Đang đọc kết quả đã lưu…</span>}
      {yolo.result && <span role="status" className="text-[var(--text-secondary)]" title={`Model: ${yolo.result.model_name} · conf ≥ ${yolo.result.params.conf} · IoU ${yolo.result.params.iou} · CPU · Confidence không phải accuracy.`}>
        YOLO: {yolo.result.total_images} ảnh · {totalBoxes} đối tượng{yolo.result.cached ? ' · đã lưu' : ''} · M bật/tắt box · Space xem nhãn
      </span>}
    </div>
    <ErrorBanner message={yolo.error} />
  </div>
}
