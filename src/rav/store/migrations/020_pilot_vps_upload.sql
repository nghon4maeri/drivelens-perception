-- Migration 020: Ho tro transport VPS_DISK cho upload lon (§21.30)
ALTER TABLE rav_pilot_upload DROP CONSTRAINT IF EXISTS rav_pilot_upload_transfer_method_check;
ALTER TABLE rav_pilot_upload ADD CONSTRAINT rav_pilot_upload_transfer_method_check
  CHECK (transfer_method IN ('POST', 'MULTIPART', 'VPS_DISK'));

ALTER TABLE rav_pilot_upload ADD COLUMN IF NOT EXISTS vps_part_path text;
