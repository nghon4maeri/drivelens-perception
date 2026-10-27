import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// FE chỉ gọi /api/v1 và /media (architecture §3); dev server proxy sang backend (mặc định uvicorn cổng 8000, đổi bằng RAV_API).
export default defineConfig(({ mode }) => {
  const backend = loadEnv(mode, '.', 'RAV_').RAV_API || 'http://127.0.0.1:8000'
  return {
    plugins: [react(), tailwindcss()],
    server: { proxy: { '/api': backend, '/media': backend } },
  }
})
