-- 007_pilot_analysis.sql — YOLO trên đợt đề xuất + kết quả phân tích (DEP5b2, CONTRACTS §21.17, #97)

-- Detection của một model SYSTEM trên picks của một proposal (CONTRACTS §17); bằng chứng hiển thị, không đổi proposal/state/scoring.
CREATE TABLE IF NOT EXISTS rav_pilot_proposal_evidence (
    id UUID PRIMARY KEY,
    proposal_id UUID NOT NULL REFERENCES rav_pilot_proposal(id) ON DELETE RESTRICT,
    session_id UUID NOT NULL,
    owner_id UUID NOT NULL,
    model_id UUID NOT NULL REFERENCES rav_pilot_model_asset(id) ON DELETE RESTRICT,
    job_id UUID NOT NULL UNIQUE REFERENCES rav_pilot_job(id) ON DELETE RESTRICT,
    runtime_fingerprint CHAR(64) NOT NULL,
    evidence JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (proposal_id, model_id, runtime_fingerprint),
    FOREIGN KEY (session_id, owner_id) REFERENCES rav_pilot_session(id, owner_id) ON DELETE RESTRICT
);

-- Kết quả một lần phân tích (metric + dữ liệu view) theo input và ngữ cảnh phiên lúc tính.
CREATE TABLE IF NOT EXISTS rav_pilot_analysis (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL,
    owner_id UUID NOT NULL,
    job_id UUID NOT NULL UNIQUE REFERENCES rav_pilot_job(id) ON DELETE RESTRICT,
    input_hash CHAR(64) NOT NULL,
    input JSONB NOT NULL,
    decision_seq BIGINT NOT NULL CHECK (decision_seq >= 0),
    state_version BIGINT NOT NULL CHECK (state_version >= 1),
    proposal_count INT NOT NULL CHECK (proposal_count >= 0),
    results JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, input_hash, decision_seq, proposal_count),
    FOREIGN KEY (session_id, owner_id) REFERENCES rav_pilot_session(id, owner_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_analysis_session ON rav_pilot_analysis(session_id, input_hash, created_at DESC);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['rav_pilot_proposal_evidence', 'rav_pilot_analysis'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_immutable ON %I', t, t);
        EXECUTE format('CREATE TRIGGER trg_%s_immutable BEFORE UPDATE OR DELETE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION rav_pilot_reject_mutation()', t, t);
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('REVOKE ALL ON %I FROM PUBLIC, anon, authenticated', t);
        EXECUTE format('DROP POLICY IF EXISTS p_backend_all ON %I', t);
        EXECUTE format('CREATE POLICY p_backend_all ON %I TO rav_pilot_api, rav_pilot_worker USING (true) WITH CHECK (true)', t);
        EXECUTE format('DROP POLICY IF EXISTS p_owner_read ON %I', t);
        EXECUTE format('CREATE POLICY p_owner_read ON %I FOR SELECT TO authenticated '
                       'USING (owner_id = rav_pilot_uid() AND rav_pilot_is_active(rav_pilot_uid()))', t);
        EXECUTE format('GRANT SELECT ON %I TO rav_pilot_api, authenticated', t);
        EXECUTE format('GRANT SELECT, INSERT ON %I TO rav_pilot_worker', t);
    END LOOP;
END $$;
