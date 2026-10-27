-- 017_pilot_export.sql — Pipeline xuất dữ liệu đã che PII & Snapshot Export (CONTRACTS §21.8)

CREATE TABLE IF NOT EXISTS rav_pilot_export (
    id UUID PRIMARY KEY,
    owner_id UUID NOT NULL REFERENCES rav_pilot_profile(user_id) ON DELETE RESTRICT,
    snapshot_id UUID NOT NULL,
    snapshot_decision_seq BIGINT NOT NULL,
    snapshot_proposal_cutoff_n INT NOT NULL,
    input_hash CHAR(64) NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('REQUESTED', 'PROCESSING', 'NEEDS_CONFIRMATION', 'FINALIZING', 'READY', 'FAILED', 'CANCELLED', 'EXPIRED')),
    state_version BIGINT NOT NULL DEFAULT 1 CHECK (state_version >= 1),
    idempotency_key TEXT,
    build_job_id UUID REFERENCES rav_pilot_job(id) ON DELETE RESTRICT,
    finalize_job_id UUID REFERENCES rav_pilot_job(id) ON DELETE RESTRICT,
    payload_hash CHAR(64),
    review_receipt_id UUID,
    review_expires_at TIMESTAMPTZ,
    payload_ref UUID REFERENCES rav_pilot_artifact(id) ON DELETE RESTRICT,
    output_receipt_ref UUID REFERENCES rav_pilot_artifact(id) ON DELETE RESTRICT,
    output_key TEXT,
    output_size_bytes BIGINT CHECK (output_size_bytes IS NULL OR output_size_bytes >= 0),
    output_sha256 CHAR(64),
    error JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (id, owner_id),
    UNIQUE (owner_id, idempotency_key),
    FOREIGN KEY (snapshot_id, owner_id) REFERENCES rav_pilot_snapshot(id, owner_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS rav_pilot_export_review_receipt (
    id UUID PRIMARY KEY,
    export_id UUID NOT NULL,
    owner_id UUID NOT NULL REFERENCES rav_pilot_profile(user_id) ON DELETE RESTRICT,
    payload_hash CHAR(64) NOT NULL,
    report_core_hash CHAR(64) NOT NULL,
    manifest_hash CHAR(64) NOT NULL,
    artifact_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    n_members INT NOT NULL DEFAULT 0 CHECK (n_members >= 0),
    issued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (export_id, owner_id) REFERENCES rav_pilot_export(id, owner_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS rav_pilot_export_confirmation (
    id UUID PRIMARY KEY,
    export_id UUID NOT NULL,
    owner_id UUID NOT NULL REFERENCES rav_pilot_profile(user_id) ON DELETE RESTRICT,
    payload_hash CHAR(64) NOT NULL,
    review_receipt_id UUID NOT NULL REFERENCES rav_pilot_export_review_receipt(id) ON DELETE RESTRICT,
    actor_id UUID NOT NULL REFERENCES rav_pilot_profile(user_id) ON DELETE RESTRICT,
    confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    idempotency_key TEXT,
    FOREIGN KEY (export_id, owner_id) REFERENCES rav_pilot_export(id, owner_id) ON DELETE RESTRICT,
    UNIQUE (export_id, payload_hash),
    UNIQUE (owner_id, idempotency_key)
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_export_review_receipt') THEN
        ALTER TABLE rav_pilot_export ADD CONSTRAINT fk_export_review_receipt
            FOREIGN KEY (review_receipt_id) REFERENCES rav_pilot_export_review_receipt(id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED;
    END IF;
END $$;

ALTER TABLE rav_pilot_export ENABLE ROW LEVEL SECURITY;
ALTER TABLE rav_pilot_export_review_receipt ENABLE ROW LEVEL SECURITY;
ALTER TABLE rav_pilot_export_confirmation ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS p_backend_all ON rav_pilot_export;
CREATE POLICY p_backend_all ON rav_pilot_export TO rav_pilot_api, rav_pilot_worker USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS p_backend_all ON rav_pilot_export_review_receipt;
CREATE POLICY p_backend_all ON rav_pilot_export_review_receipt TO rav_pilot_api, rav_pilot_worker USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS p_backend_all ON rav_pilot_export_confirmation;
CREATE POLICY p_backend_all ON rav_pilot_export_confirmation TO rav_pilot_api, rav_pilot_worker USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS p_export_owner ON rav_pilot_export;
CREATE POLICY p_export_owner ON rav_pilot_export FOR SELECT TO authenticated
    USING (owner_id = rav_pilot_uid() AND rav_pilot_is_active(rav_pilot_uid()));

DROP POLICY IF EXISTS p_export_review_owner ON rav_pilot_export_review_receipt;
CREATE POLICY p_export_review_owner ON rav_pilot_export_review_receipt FOR SELECT TO authenticated
    USING (owner_id = rav_pilot_uid() AND rav_pilot_is_active(rav_pilot_uid()));

DROP POLICY IF EXISTS p_export_confirm_owner ON rav_pilot_export_confirmation;
CREATE POLICY p_export_confirm_owner ON rav_pilot_export_confirmation FOR SELECT TO authenticated
    USING (owner_id = rav_pilot_uid() AND rav_pilot_is_active(rav_pilot_uid()));

GRANT SELECT ON rav_pilot_export, rav_pilot_export_review_receipt, rav_pilot_export_confirmation TO authenticated;
GRANT SELECT, INSERT, UPDATE ON rav_pilot_export TO rav_pilot_api, rav_pilot_worker;
GRANT SELECT, INSERT ON rav_pilot_export_review_receipt TO rav_pilot_api, rav_pilot_worker;
GRANT SELECT, INSERT ON rav_pilot_export_confirmation TO rav_pilot_api, rav_pilot_worker;
