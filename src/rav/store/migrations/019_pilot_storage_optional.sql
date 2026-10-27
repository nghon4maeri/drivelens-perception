-- 019_pilot_storage_optional.sql: Storage quota is optional per immutable job/hold snapshot (§21.12, §21.28).

ALTER TABLE rav_pilot_job ADD COLUMN IF NOT EXISTS storage_accounting_enabled boolean NOT NULL DEFAULT true;

CREATE OR REPLACE FUNCTION rav_pilot_storage_job_flag_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.storage_accounting_enabled IS DISTINCT FROM OLD.storage_accounting_enabled THEN
    RAISE EXCEPTION 'storage accounting job snapshot immutable' USING ERRCODE='check_violation';
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_storage_job_flag_guard ON rav_pilot_job;
CREATE TRIGGER trg_storage_job_flag_guard BEFORE UPDATE ON rav_pilot_job
  FOR EACH ROW EXECUTE FUNCTION rav_pilot_storage_job_flag_guard();

ALTER TABLE rav_pilot_storage_hold ADD COLUMN IF NOT EXISTS storage_accounting_enabled boolean NOT NULL DEFAULT true;

CREATE OR REPLACE FUNCTION rav_pilot_storage_hold_flag_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.storage_accounting_enabled IS DISTINCT FROM OLD.storage_accounting_enabled THEN
    RAISE EXCEPTION 'storage accounting hold snapshot immutable' USING ERRCODE='check_violation';
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_storage_hold_flag_guard ON rav_pilot_storage_hold;
CREATE TRIGGER trg_storage_hold_flag_guard BEFORE UPDATE ON rav_pilot_storage_hold
  FOR EACH ROW EXECUTE FUNCTION rav_pilot_storage_hold_flag_guard();
