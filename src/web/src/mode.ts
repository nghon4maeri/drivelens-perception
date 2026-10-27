/**
 * Chế độ build (CONTRACTS §21.16, #96): `VITE_RAV_MODE=pilot` → chỉ `/api/v2` + đăng nhập Supabase; mặc định `local` → UI cũ trên `/api/v1`, không đăng nhập.
 * Đọc một lần lúc build; không đổi lúc chạy.
 */
export const PILOT = import.meta.env.VITE_RAV_MODE === 'pilot'
