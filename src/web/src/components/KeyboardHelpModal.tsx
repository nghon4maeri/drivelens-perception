/** Bảng phím tắt (UI_DESIGN §7), mở bằng phím ? hoặc nút ở chân sidebar. */
import { Keyboard } from 'lucide-react'
import { Modal } from '../ui'

const SHORTCUTS: [string, string][] = [
  ['← ↑ ↓ →', 'Di chuyển giữa các ô (theo số cột đang hiện)'],
  ['K', 'Giữ frame đang chọn, sang ô kế — tự lưu'],
  ['D', 'Bỏ: không giữ, frame ra khỏi pool và không được đề xuất lại — tự lưu'],
  ['U', 'Huỷ quyết định: xoá Giữ / Bỏ, frame về Chưa duyệt, có thể được đề xuất lại'],
  ['A / X', 'Giữ cả đợt / Bỏ cả đợt'],
  ['Ctrl + Z', 'Hoàn tác thao tác gần nhất trong đợt'],
  ['Space', 'Chi tiết frame: điểm, frame đã giữ gần nhất, clip ±3 s'],
  ['Enter', 'Yêu cầu đợt mới (không chốt — quyết định đã tự lưu; frame chưa duyệt vẫn ở pool)'],
  ['M', 'Bật / tắt khung detector trên ảnh (khi có)'],
  ['P', 'Mở / đóng panel phải'],
  ['T', 'Đổi theme'],
  ['Esc', 'Đóng hộp thoại / clip'],
]

export function KeyboardHelpModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Modal open={open} onClose={onClose} width={480} title="Phím tắt" icon={<Keyboard className="w-4 h-4 text-[var(--border-focus)]" />}>
      <div className="divide-y divide-[var(--border-color)] text-xs">
        {SHORTCUTS.map(([key, desc]) => (
          <div key={key} className="py-2 flex items-center justify-between gap-4">
            <span className="text-[var(--text-secondary)]">{desc}</span>
            <kbd className="px-2 py-0.5 rounded bg-[var(--canvas-subtle)] font-mono text-[11px] font-semibold shrink-0">{key}</kbd>
          </div>
        ))}
      </div>
    </Modal>
  )
}
