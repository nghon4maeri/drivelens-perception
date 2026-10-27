# Kho du lieu mau va dang ky — review/release 2026-10-17

Nguoi dung yeu cau review Antigravity va deploy; bo Lat 4 admin tao template/fork moi.
Giu compatibility template import cu; nguoi dung tu tao phien rieng tu du lieu SYSTEM.

## Review va sua

- Seed cu goi `_embed_rgb_batch` khong ton tai, am tham fallback vector ngau nhien
  mang nhan DINO/CLIP; bo fallback va co CLI synthetic. Goi component `compute` that,
  giu dung FieldKey/params hash; thieu checkpoint phai fail truoc publish DB.
- Seed cu tu dung unit ID va frame ordinal sai exact-anchor; dung Context/grid/unit
  cua pipeline va `read_exact`, frame_idx goc. Test kiem ID/key tren artifact va frame.
- Guard nguon da bien mat khi publish PREPARE_FRAMES; upload poll cu khong duoc
  tiep tuc gui request sau doi auth generation.

## Kiem thu

- 86 test PostgreSQL/auth/store/API/operations/upload/template dat truoc sua seed.
- 40 test cuoi: seed regression, API, frames/features dat tren PostgreSQL 16 rieng,
  port55433/output/pg-sample-release; S3 moto, khong pytest production.
- Ruff src/tests/script dat; frontend54 test, tsc va pilot build dat sau sua FE.
- Initial local PG socket sandbox errors la loi moi truong; ket qua dat dung
  server test moi khoi tao ngoai sandbox, khong tinh lan loi thanh pass.

## Preflight va rollout

- Production idle: 0 active job/upload; worker healthy; VPS con37.2 GiB.
- DB001–022; can apply023–025 truoc worker + combined API/FE.
- 7 policy enabled; profile active deu dung `admin-pii-20261017-49a3ba8b`.
  Danh dau chinh policy nay lam signup default; khong doi config/limits/GPU lanes.
- Backup schema/data truoc migrate; pin image, giu rollback image/env.
- Release va cloud smoke: chua thuc hien tai checkpoint review nay.

## Gioi han

Seed la operator tool cho video mau nho; S3 ghi truoc transaction DB, retry dung
key tat dinh khi loi, chua thay the job receipt pipeline cho import video lon.
Khong claim chat luong CLIP/ODD hoac benchmark nhieu nguoi tu test chuc nang.

## Live initial release va auth redirect hotfix

- Source431eb9e8, Render dep-db9ksbvavr4c73efa10g live; migration023–025, signup default policy hien hanh. Backup13,375,946 bytes/504 entries pg_restore list dat.
- Browser form dang ky hien that; password sign-in/profile bootstrap/replay va non-admin403 dat bang tai khoan QA moi khong gui email. Admin catalog/operations/logs dat bang operator da cau hinh.
- Seed86 unit/172 anh, DINO va CLIP that duoc publish SYSTEM. CLIP thieu preprocessor_config.json; da tai dung file tu revision3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268, khong doi model.
- User bat loi email ve localhost3000 sau initial release. Da sua Site URL tren dashboard, callback FE56 Vitest/tsc/pilot build dat; can rollout hotfix FE/API. Smoke truoc do khong kiem chung email delivery/callback, khong claim da test email end-to-end.

## Final live verification

- Callback FE/API94c1adc0 live dep-db9l1pe1egvs73fm1va0; worker431eb9e8 unchanged. Health ready200, Supabase Site URL production/login da luu. Link verification QA khong gui email redirect dung production va co token callback; khong claim thu email delivery that.
- User yeu cau GPU50 phut moi4h va CPU khong gioi han quota cho signup moi. Clone policy hien hanh, chi doi gpu_base_ms=3000000/cpu_accounting_enabled=false va signup_default.
- Live QA bat trigger dashboard `on_auth_user_created`/`handle_new_pilot_user` lay enabled LIMIT1, cap default_pilot_v1 (CPU10s/GPU5s, chi cpu-1), bo qua signup default. Migration026 retire insertion, API bootstrap verified la authority.38 profile/auth regression va1 trigger/quota test PostgreSQL dat; Ruff dat.
- Tai khoan cua session6912ffd5-171c-43c5-b4d6-b695b46a05cc bi cap nham da sua policy; current GPU window base nang50phut, giu held/consumed. Job9eac90f3-caed-4a93-a9f4-3bf707688a30 van QUEUED vi policy cu khong khop lane; khong sua frozen job. Huong dan user Huy va de xuat lai.
- QA moi sau026 doc API quota: cpu.enabled=false, gpu.base_available=3000000, next_reset_at theo windowUTC4h. Khong cap lai quota tai khoan cu khac. Admin policy editor chua implement.
