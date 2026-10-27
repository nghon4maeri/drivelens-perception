-- 005_pilot_features.sql — đặc trưng DINO/CLIP + ngân sách GPU Kaggle toàn cục (DEP5a, CONTRACTS §21.14, #94)

-- Snapshot quota GPU của account provider: worker đọc Kaggle SDK (KAGGLE_SDK) hoặc operator ghi tay (OPERATOR). Chỉ INSERT.
CREATE TABLE IF NOT EXISTS rav_pilot_gpu_budget_snapshot (
    id UUID PRIMARY KEY,
    provider_account TEXT NOT NULL CHECK (provider_account <> ''),
    source TEXT NOT NULL CHECK (source IN ('KAGGLE_SDK', 'OPERATOR')),
    observed_at TIMESTAMPTZ NOT NULL,
    remaining_ms BIGINT NOT NULL CHECK (remaining_ms >= 0),
    reset_at TIMESTAMPTZ,
    usage_baseline_seq BIGINT NOT NULL DEFAULT 0 CHECK (usage_baseline_seq >= 0),
    external_session_active BOOLEAN NOT NULL DEFAULT false,
    operator_id UUID,
    evidence_ref TEXT NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    policy_version TEXT REFERENCES rav_pilot_quota_policy(version) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((source = 'OPERATOR') = (operator_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_gpu_snapshot_account ON rav_pilot_gpu_budget_snapshot(provider_account, observed_at DESC);
DROP TRIGGER IF EXISTS trg_rav_pilot_gpu_snapshot_immutable ON rav_pilot_gpu_budget_snapshot;
CREATE TRIGGER trg_rav_pilot_gpu_snapshot_immutable BEFORE UPDATE OR DELETE ON rav_pilot_gpu_budget_snapshot
    FOR EACH ROW EXECUTE FUNCTION rav_pilot_reject_mutation();

-- Phần ngân sách toàn cục giữ cho một attempt GPU; OPEN tới khi settle (publish/finish/reconcile).
CREATE TABLE IF NOT EXISTS rav_pilot_gpu_hold (
    id UUID PRIMARY KEY,
    attempt_id UUID NOT NULL UNIQUE REFERENCES rav_pilot_job_attempt(id) ON DELETE RESTRICT,
    snapshot_id UUID NOT NULL REFERENCES rav_pilot_gpu_budget_snapshot(id) ON DELETE RESTRICT,
    provider_account TEXT NOT NULL,
    reserved_ms BIGINT NOT NULL CHECK (reserved_ms >= 0),
    consumed_ms BIGINT NOT NULL DEFAULT 0 CHECK (consumed_ms >= 0),
    released_ms BIGINT NOT NULL DEFAULT 0 CHECK (released_ms >= 0),
    state TEXT NOT NULL DEFAULT 'OPEN' CHECK (state IN ('OPEN', 'FINAL')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_gpu_hold_open ON rav_pilot_gpu_hold(provider_account) WHERE state = 'OPEN';
CREATE OR REPLACE FUNCTION rav_pilot_gpu_hold_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'gpu_hold không DELETE' USING ERRCODE = 'check_violation';
    END IF;
    IF (NEW.id, NEW.attempt_id, NEW.snapshot_id, NEW.provider_account, NEW.reserved_ms)
       IS DISTINCT FROM (OLD.id, OLD.attempt_id, OLD.snapshot_id, OLD.provider_account, OLD.reserved_ms)
       OR OLD.state = 'FINAL' THEN
        RAISE EXCEPTION 'gpu_hold: danh tính bất biến, FINAL không sửa' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_gpu_hold_guard ON rav_pilot_gpu_hold;
CREATE TRIGGER trg_gpu_hold_guard BEFORE UPDATE OR DELETE ON rav_pilot_gpu_hold
    FOR EACH ROW EXECUTE FUNCTION rav_pilot_gpu_hold_guard();

-- Một đặc trưng READY cho mỗi (nguồn, DINO|CLIP).
CREATE UNIQUE INDEX IF NOT EXISTS uq_artifact_feature_ready ON rav_pilot_artifact(source_id, variant)
    WHERE kind = 'FEATURE' AND state = 'READY';

ALTER TABLE rav_pilot_gpu_budget_snapshot ENABLE ROW LEVEL SECURITY;
ALTER TABLE rav_pilot_gpu_hold ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON rav_pilot_gpu_budget_snapshot, rav_pilot_gpu_hold FROM PUBLIC, anon, authenticated;
DROP POLICY IF EXISTS p_backend_all ON rav_pilot_gpu_budget_snapshot;
CREATE POLICY p_backend_all ON rav_pilot_gpu_budget_snapshot TO rav_pilot_api, rav_pilot_worker USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS p_backend_all ON rav_pilot_gpu_hold;
CREATE POLICY p_backend_all ON rav_pilot_gpu_hold TO rav_pilot_api, rav_pilot_worker USING (true) WITH CHECK (true);
-- API chỉ đọc (claim/settle do worker); worker ghi snapshot + hold.
GRANT SELECT ON rav_pilot_gpu_budget_snapshot, rav_pilot_gpu_hold TO rav_pilot_api;
GRANT SELECT, INSERT ON rav_pilot_gpu_budget_snapshot TO rav_pilot_worker;
GRANT SELECT, INSERT, UPDATE ON rav_pilot_gpu_hold TO rav_pilot_worker;
