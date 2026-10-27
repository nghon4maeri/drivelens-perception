/**
 * Ảnh dùng được cả hai chế độ: URL thường (local) hiển thị ngay; tham chiếu media pilot chỉ xin khi ảnh vào viewport (lazy),
 * PENDING hiện khung chờ, lỗi hiện biểu tượng an toàn (UI-export spec §2 Media state). `eager` = ảnh đang mở trong viewer (ưu tiên 0).
 * Ảnh lưu sẵn chưa có (`NOT_PREPARED`, CONTRACTS §21.20): "Chưa chuẩn bị ảnh" + nút chuẩn bị, hoặc "Đang chuẩn bị…" khi đã có job;
 * nguồn SYSTEM chỉ hiện trạng thái (operator chuẩn bị).
 */
import React, { useEffect, useRef, useState } from 'react'
import { ImageOff, Loader2 } from 'lucide-react'
import { isMediaRef, prepareFrames, useMediaUrl } from '../media'
import { isSystemSource } from '../pilot_api'

type Props = Omit<React.ImgHTMLAttributes<HTMLImageElement>, 'src'> & { src?: string; eager?: boolean }

export function MediaImg({ src, eager, className, alt, ...rest }: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const [visible, setVisible] = useState(!!eager || !isMediaRef(src))
  const [asking, setAsking] = useState(false)
  const [prepError, setPrepError] = useState('')
  useEffect(() => {
    if (visible || !ref.current || typeof IntersectionObserver === 'undefined') { if (!visible) setVisible(true); return }
    const io = new IntersectionObserver(es => { if (es.some(e => e.isIntersecting)) { setVisible(true); io.disconnect() } }, { rootMargin: '200px' })
    io.observe(ref.current)
    return () => io.disconnect()
  }, [visible])
  const { url, failed, notPrepared, retry } = useMediaUrl(src, visible, eager ? 0 : 1)
  if (url) return <img src={url} alt={alt} className={className} {...rest} />
  const box = `${className ?? ''} flex flex-col items-center justify-center gap-1 bg-[#181B1F] text-white/50 text-[10px] text-center p-1`
  if (notPrepared) {
    const prepare = async (e: React.MouseEvent) => {
      e.stopPropagation()
      setAsking(true)
      setPrepError('')
      try { await prepareFrames(notPrepared.sourceId); retry() }
      catch (err) { setPrepError(err instanceof Error ? err.message : String(err)) }
      finally { setAsking(false) }
    }
    return (
      <div ref={ref} className={box} title={prepError || alt}>
        {notPrepared.jobId
          ? <><Loader2 className="w-4 h-4 animate-spin" aria-label="Đang chuẩn bị ảnh" /><span>Đang chuẩn bị ảnh…</span></>
          : <><ImageOff className="w-4 h-4" /><span>Chưa chuẩn bị ảnh</span>
            {!isSystemSource(notPrepared.sourceId) && (
              <button type="button" disabled={asking} onClick={prepare}
                      className="px-1.5 py-0.5 rounded bg-white/10 hover:bg-white/20 text-white/80 cursor-pointer disabled:opacity-50">
                {asking ? 'Đang gửi…' : 'Chuẩn bị'}
              </button>)}
            {prepError && <span className="text-red-400">{prepError}</span>}</>}
      </div>
    )
  }
  return (
    <div ref={ref} className={box} title={alt}>
      {failed ? <ImageOff className="w-5 h-5" aria-label="Không tải được ảnh" /> : src ? <Loader2 className="w-4 h-4 animate-spin" aria-label="Đang tải ảnh" /> : null}
    </div>
  )
}
