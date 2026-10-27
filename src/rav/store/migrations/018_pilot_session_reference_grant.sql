-- 018_pilot_session_reference_grant.sql: Cấp quyền DELETE trên rav_pilot_session_reference và cho phép giải phóng pin REFERENCE (§21.17)
GRANT DELETE ON rav_pilot_session_reference TO rav_pilot_api;

-- Cho phép giải phóng REFERENCE pin khi thay đổi hoặc gỡ liên kết reference phiên
CREATE OR REPLACE FUNCTION rav_pilot_pin_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'không DELETE source pin' USING ERRCODE = 'check_violation';
    END IF;
    IF (NEW.id, NEW.source_id, NEW.consumer_kind, NEW.consumer_id, NEW.input_role, NEW.source_version)
       IS DISTINCT FROM (OLD.id, OLD.source_id, OLD.consumer_kind, OLD.consumer_id, OLD.input_role, OLD.source_version)
       OR OLD.released_at IS NOT NULL
       OR (NEW.released_at IS NOT NULL AND OLD.consumer_kind NOT IN ('JOB', 'REFERENCE')) THEN
        RAISE EXCEPTION 'source pin chỉ được release một lần và chỉ với consumer JOB hoặc REFERENCE' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

-- Thay thế ràng buộc UNIQUE tuyệt đối bằng partial unique index chỉ áp dụng cho pin đang active
ALTER TABLE rav_pilot_source_pin DROP CONSTRAINT IF EXISTS rav_pilot_source_pin_source_id_consumer_kind_consumer_id_in_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_source_pin_active_consumer
    ON rav_pilot_source_pin(source_id, consumer_kind, consumer_id, input_role)
    WHERE released_at IS NULL;
