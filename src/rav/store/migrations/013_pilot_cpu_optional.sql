-- CPU quota is optional per immutable job snapshot; old jobs retain complete settlement.
ALTER TABLE rav_pilot_job ADD COLUMN cpu_accounting_enabled boolean NOT NULL DEFAULT true;
CREATE FUNCTION rav_pilot_cpu_flag_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.cpu_accounting_enabled IS DISTINCT FROM OLD.cpu_accounting_enabled THEN
    RAISE EXCEPTION 'CPU accounting snapshot immutable' USING ERRCODE='check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_cpu_flag_guard BEFORE UPDATE ON rav_pilot_job FOR EACH ROW EXECUTE FUNCTION rav_pilot_cpu_flag_guard();
ALTER TABLE rav_pilot_command_receipt DROP CONSTRAINT rav_pilot_command_receipt_action_check;
ALTER TABLE rav_pilot_command_receipt ADD CONSTRAINT rav_pilot_command_receipt_action_check
 CHECK (action IN ('CREATE_SESSION','CREATE_SNAPSHOT','FORK_TEMPLATE','INTERACTIVE_SAVE'));
