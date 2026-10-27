-- Telemetry only (CONTRACTS §21.19); no change to business receipts or privileges.
CREATE TABLE rav_pilot_timing_event (
    id UUID PRIMARY KEY,
    observed_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source TEXT NOT NULL CHECK (source IN ('browser','api','worker','gpu')),
    upload_id UUID,
    job_id UUID,
    request_id UUID,
    attempt_id UUID,
    stage TEXT NOT NULL,
    data JSONB NOT NULL
);
CREATE INDEX idx_timing_upload ON rav_pilot_timing_event(upload_id, observed_at);
CREATE INDEX idx_timing_job ON rav_pilot_timing_event(job_id, observed_at);
CREATE INDEX idx_timing_request ON rav_pilot_timing_event(request_id, observed_at);
CREATE INDEX idx_timing_retention ON rav_pilot_timing_event(received_at);
CREATE TABLE rav_pilot_heartbeat (
    component TEXT PRIMARY KEY CHECK (component IN ('api','worker','gpu')),
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    details JSONB NOT NULL DEFAULT '{}'
);
ALTER TABLE rav_pilot_timing_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE rav_pilot_heartbeat ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON rav_pilot_timing_event, rav_pilot_heartbeat FROM PUBLIC, anon, authenticated;
CREATE POLICY p_backend_all ON rav_pilot_timing_event TO rav_pilot_api, rav_pilot_worker USING (true) WITH CHECK (true);
CREATE POLICY p_backend_all ON rav_pilot_heartbeat TO rav_pilot_api, rav_pilot_worker USING (true) WITH CHECK (true);
GRANT SELECT,INSERT ON rav_pilot_timing_event TO rav_pilot_api,rav_pilot_worker;
GRANT DELETE ON rav_pilot_timing_event TO rav_pilot_worker;
GRANT SELECT,INSERT,UPDATE ON rav_pilot_heartbeat TO rav_pilot_api,rav_pilot_worker;
