-- Migration 023: đăng ký tự phục vụ và bootstrap hồ sơ tự động (CONTRACTS §21.34). Additive.
ALTER TABLE rav_pilot_quota_policy ADD COLUMN IF NOT EXISTS signup_default BOOLEAN NOT NULL DEFAULT false;

CREATE UNIQUE INDEX IF NOT EXISTS uq_quota_policy_signup_default
ON rav_pilot_quota_policy ((true))
WHERE signup_default;

-- Nếu môi trường hiện có đúng 1 policy đang bật và chưa đánh dấu signup_default, gán làm mặc định đăng ký.
UPDATE rav_pilot_quota_policy
SET signup_default = true
WHERE enabled = true
  AND NOT EXISTS (SELECT 1 FROM rav_pilot_quota_policy WHERE signup_default = true)
  AND (SELECT count(*) FROM rav_pilot_quota_policy WHERE enabled = true) = 1;

-- Cho phép API tạo hồ sơ mới trong lần xác thực đầu tiên; giữ nguyên giới hạn UPDATE (display_name) từ 001.
GRANT INSERT ON rav_pilot_profile TO rav_pilot_api;
