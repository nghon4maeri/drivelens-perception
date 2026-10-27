-- ==============================================================================
-- 025_pilot_system_ingest.sql — upload/prepare SYSTEM & trạng thái mẫu phụ thuộc (CONTRACTS §21.36)
-- - Bổ sung target_scope ('PRIVATE' | 'SYSTEM') bất biến trên rav_pilot_upload.
-- - Bổ sung account_scope ('USER_AND_GLOBAL' | 'GLOBAL_ONLY') bất biến trên rav_pilot_storage_hold.
-- - Cấp quyền UPDATE rav_pilot_demo_template_status cho rav_pilot_api để đồng bộ trạng thái
--   VERIFIED / BLOCKED khi bật/tắt xuất bản nguồn SYSTEM.
-- ==============================================================================

ALTER TABLE rav_pilot_upload
    ADD COLUMN IF NOT EXISTS target_scope TEXT NOT NULL DEFAULT 'PRIVATE'
    CHECK (target_scope IN ('PRIVATE', 'SYSTEM'));

CREATE OR REPLACE FUNCTION rav_pilot_upload_target_scope_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.target_scope IS DISTINCT FROM OLD.target_scope THEN
        RAISE EXCEPTION 'target_scope của upload là bất biến sau khi tạo (CONTRACTS §21.36)'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_upload_target_scope_guard ON rav_pilot_upload;
CREATE TRIGGER trg_upload_target_scope_guard BEFORE UPDATE ON rav_pilot_upload
    FOR EACH ROW EXECUTE FUNCTION rav_pilot_upload_target_scope_guard();

ALTER TABLE rav_pilot_storage_hold
    ADD COLUMN IF NOT EXISTS account_scope TEXT NOT NULL DEFAULT 'USER_AND_GLOBAL'
    CHECK (account_scope IN ('USER_AND_GLOBAL', 'GLOBAL_ONLY'));

CREATE OR REPLACE FUNCTION rav_pilot_storage_hold_flag_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.storage_accounting_enabled IS DISTINCT FROM OLD.storage_accounting_enabled
       OR NEW.account_scope IS DISTINCT FROM OLD.account_scope THEN
        RAISE EXCEPTION 'storage accounting hold snapshot immutable' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

GRANT UPDATE (availability, reason_code, version, updated_at) ON rav_pilot_demo_template_status TO rav_pilot_api;
