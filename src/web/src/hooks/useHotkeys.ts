/** Phím tắt toàn trang (UI_DESIGN §6): đăng ký một lần, luôn gọi handler mới nhất qua ref; bỏ qua khi đang gõ ô nhập hoặc `enabled` = false. */
import { useEffect, useRef } from 'react'

export function useHotkeys(handler: (e: KeyboardEvent) => boolean | void, enabled = true) {
  const ref = useRef(handler)
  ref.current = handler
  const on = useRef(enabled)
  on.current = enabled
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target
      if (!on.current || t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement || t instanceof HTMLSelectElement) return
      if (ref.current(e)) e.preventDefault()   // handler trả true = đã xử lý phím
    }
    addEventListener('keydown', onKey)
    return () => removeEventListener('keydown', onKey)
  }, [])
}
