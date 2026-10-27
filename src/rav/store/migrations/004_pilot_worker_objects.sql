-- DEP4F: durable receipts for worker PUT; never enumerate an S3 prefix.
CREATE TABLE rav_pilot_worker_object (
    id UUID PRIMARY KEY,
    owner_id UUID NOT NULL REFERENCES rav_pilot_profile(user_id) ON DELETE RESTRICT,
    job_id UUID NOT NULL REFERENCES rav_pilot_job(id) ON DELETE RESTRICT,
    attempt_id UUID NOT NULL REFERENCES rav_pilot_job_attempt(id) ON DELETE RESTRICT,
    object_key TEXT NOT NULL,
    sha256 CHAR(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    size_bytes BIGINT NOT NULL CHECK (size_bytes > 0),
    state TEXT NOT NULL CHECK (state IN ('INTENT', 'WRITTEN', 'ACCEPTED', 'DELETE_PENDING', 'DELETED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (attempt_id, object_key)
);
CREATE UNIQUE INDEX ON rav_pilot_worker_object (object_key) WHERE state <> 'DELETED';
ALTER TABLE rav_pilot_worker_object ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON rav_pilot_worker_object FROM PUBLIC, anon, authenticated;
CREATE POLICY p_backend_read ON rav_pilot_worker_object FOR SELECT TO rav_pilot_api, rav_pilot_worker USING (true);
CREATE POLICY p_worker_write ON rav_pilot_worker_object TO rav_pilot_worker USING (true) WITH CHECK (true);
GRANT SELECT ON rav_pilot_worker_object TO rav_pilot_api;
GRANT SELECT, INSERT, UPDATE ON rav_pilot_worker_object TO rav_pilot_worker;
-- Worker locks profiles before quota/storage account locks; FOR UPDATE requires a column grant.
GRANT UPDATE (display_name) ON rav_pilot_profile TO rav_pilot_worker;
CREATE INDEX ON rav_pilot_worker_object (attempt_id) WHERE state NOT IN ('ACCEPTED', 'DELETED');
CREATE FUNCTION rav_pilot_worker_object_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'object receipt cannot be deleted' USING ERRCODE = 'check_violation';
    END IF;
    IF (NEW.id, NEW.owner_id, NEW.job_id, NEW.attempt_id, NEW.object_key, NEW.sha256, NEW.size_bytes)
       IS DISTINCT FROM
       (OLD.id, OLD.owner_id, OLD.job_id, OLD.attempt_id, OLD.object_key, OLD.sha256, OLD.size_bytes)
       OR (NEW.state <> OLD.state AND NOT (
           (OLD.state='INTENT' AND NEW.state IN ('WRITTEN','ACCEPTED','DELETE_PENDING')) OR
           (OLD.state='WRITTEN' AND NEW.state IN ('ACCEPTED','DELETE_PENDING')) OR
           (OLD.state='DELETE_PENDING' AND NEW.state='DELETED'))) THEN
        RAISE EXCEPTION 'object receipt identity/state is immutable' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_worker_object_guard BEFORE UPDATE OR DELETE ON rav_pilot_worker_object
    FOR EACH ROW EXECUTE FUNCTION rav_pilot_worker_object_guard();
