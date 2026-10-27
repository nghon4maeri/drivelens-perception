/** Entry theo pathname cho pilot; nghiệp vụ vẫn dùng App/hash, trang public không mount App. */
import { useEffect } from 'react'
import App from './App'
import { LandingScreen } from './screens/LandingScreen'
import { sitePage } from './site_navigation'

export function PilotSite() {
  const page = sitePage(location.pathname)
  useEffect(() => {
    document.title = page === 'landing' ? 'DriveLens — Autonomous Vision & Dashcam Data Curation' : 'DriveLens Studio'
    if (page === 'legacy') location.replace(`/app${location.hash}`)
  }, [page])
  if (page === 'landing') return <LandingScreen />
  if (page === 'legacy') return <p className="p-8">Đang mở ứng dụng…</p>
  if (page === 'missing') return <main className="p-8"><h1 className="text-2xl font-bold">Không tìm thấy trang</h1><a href="/" className="mt-4 inline-block underline">Về trang giới thiệu</a></main>
  return <App entry={page} />
}
