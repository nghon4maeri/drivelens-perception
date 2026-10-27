import { Component, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { PilotSite } from './PilotSite'
import { PILOT } from './mode'
import './index.css'

/** Lỗi khi render một màn → hiện thông báo + nút tải lại thay vì trang trắng (UI_DESIGN §5). */
class ErrorBoundary extends Component<{ children: ReactNode }, { error?: Error }> {
  state: { error?: Error } = {}
  static getDerivedStateFromError(error: Error) { return { error } }
  render() {
    if (!this.state.error) return this.props.children
    return (
      <div style={{ padding: 32, fontFamily: 'system-ui' }}>
        <h1 style={{ fontSize: 20 }}>Giao diện gặp lỗi</h1>
        <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, opacity: 0.7 }}>{this.state.error.message}</pre>
        <button onClick={() => location.reload()} style={{ marginTop: 12, padding: '6px 14px' }}>Tải lại trang</button>
      </div>
    )
  }
}

createRoot(document.getElementById('root')!).render(<ErrorBoundary>{PILOT ? <PilotSite /> : <App />}</ErrorBoundary>)
