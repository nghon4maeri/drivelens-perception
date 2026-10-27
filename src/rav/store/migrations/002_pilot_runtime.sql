-- ==============================================================================
-- 002_pilot_runtime.sql — hàng đợi bền, quota 4h, slot, source pin (DEP3, sửa ở DEP2F #89)
-- Nguồn sự thật: CONTRACTS §21.5–21.6 (+ §21.11), DEPLOYMENT_RUNTIME_STORAGE_SPEC §3.1–3.2, §3.3 (source_pin).
-- Không seed số quota hay slot: policy/slot do operator khai báo sau đo DEP1; thiếu → 503 policy_unconfigured.
-- Storage account/hold → DEP4; GPU budget/admission/cost snapshot → DEP5/DEP7 (#89).
-- ==============================================================================

CREATE TABLE IF NOT EXISTS rav_pilot_quota_policy (
    id UUID PRIMARY KEY,
    version TEXT NOT NULL UNIQUE,
    config JSONB NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Policy bất biến: muốn đổi số thì tạo version mới; chỉ cờ enabled đổi được.
CREATE OR REPLACE FUNCTION rav_pilot_policy_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' OR (NEW.id, NEW.version, NEW.config) IS DISTINCT FROM (OLD.id, OLD.version, OLD.config) THEN
        RAISE EXCEPTION 'quota policy bất biến: tạo version mới thay vì sửa/xóa' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_policy_guard ON rav_pilot_quota_policy;
CREATE TRIGGER trg_policy_guard BEFORE UPDATE OR DELETE ON rav_pilot_quota_policy
    FOR EACH ROW EXECUTE FUNCTION rav_pilot_policy_guard();

ALTER TABLE rav_pilot_profile ADD COLUMN IF NOT EXISTS policy_id UUID REFERENCES rav_pilot_quota_policy(id) ON DELETE RESTRICT;

CREATE TABLE IF NOT EXISTS rav_pilot_quota_window (
    id UUID PRIMARY KEY,
    owner_id UUID NOT NULL REFERENCES rav_pilot_profile(user_id) ON DELETE RESTRICT,
    resource TEXT NOT NULL CHECK (resource IN ('CPU', 'GPU')),
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,
    base_ms BIGINT NOT NULL CHECK (base_ms >= 0),
    held_ms BIGINT NOT NULL DEFAULT 0 CHECK (held_ms >= 0),
    consumed_ms BIGINT NOT NULL DEFAULT 0 CHECK (consumed_ms >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_id, resource, window_start),
    CHECK (held_ms + consumed_ms <= base_ms),
    CHECK (window_end > window_start)
);

CREATE TABLE IF NOT EXISTS rav_pilot_quota_bonus (
    id UUID PRIMARY KEY,
    owner_id UUID NOT NULL REFERENCES rav_pilot_profile(user_id) ON DELETE RESTRICT,
    resource TEXT NOT NULL CHECK (resource IN ('CPU', 'GPU')),
    granted_ms BIGINT NOT NULL CHECK (granted_ms >= 0),
    held_ms BIGINT NOT NULL DEFAULT 0 CHECK (held_ms >= 0),
    consumed_ms BIGINT NOT NULL DEFAULT 0 CHECK (consumed_ms >= 0),
    operator_id UUID NOT NULL REFERENCES rav_pilot_profile(user_id) ON DELETE RESTRICT,
    reason TEXT NOT NULL,
    adjustment_key TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (held_ms + consumed_ms <= granted_ms)
);

CREATE TABLE IF NOT EXISTS rav_pilot_job (
    id UUID PRIMARY KEY,
    owner_id UUID NOT NULL REFERENCES rav_pilot_profile(user_id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK (kind IN ('VALIDATE_UPLOAD', 'RENDER_MEDIA', 'PREPARE_DINO', 'PREPARE_CLIP', 'PROPOSE',
                                       'YOLO_BATCH', 'METRICS', 'EXPORT_BUILD', 'EXPORT_FINALIZE', 'DELETE_SOURCE')),
    resource_id UUID,
    retry_of UUID REFERENCES rav_pilot_job(id) ON DELETE RESTRICT,
    input_hash CHAR(64) NOT NULL,
    payload_version TEXT NOT NULL,
    payload JSONB NOT NULL,
    idempotency_key TEXT NOT NULL,
    policy_version TEXT NOT NULL REFERENCES rav_pilot_quota_policy(version) ON DELETE RESTRICT,
    state TEXT NOT NULL CHECK (state IN ('QUEUED', 'WAITING_RESOURCE', 'RUNNING', 'CANCEL_REQUESTED', 'SUCCEEDED', 'FAILED', 'CANCELLED')),
    state_version BIGINT NOT NULL DEFAULT 1 CHECK (state_version >= 1),
    attempt_n INT NOT NULL DEFAULT 0 CHECK (attempt_n >= 0),
    phase TEXT NOT NULL DEFAULT 'queued',
    done BIGINT NOT NULL DEFAULT 0 CHECK (done >= 0),
    total BIGINT NOT NULL DEFAULT 0 CHECK (total >= 0),
    waiting_reason TEXT,
    queue_deadline TIMESTAMPTZ NOT NULL,
    error JSONB,
    result_ref UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_id, kind, idempotency_key),
    CHECK (done <= total)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_job_active_input ON rav_pilot_job(owner_id, kind, resource_id, input_hash)
    WHERE state IN ('QUEUED', 'WAITING_RESOURCE', 'RUNNING', 'CANCEL_REQUESTED');
CREATE INDEX IF NOT EXISTS idx_job_state_deadline ON rav_pilot_job(state, queue_deadline, created_at);
CREATE INDEX IF NOT EXISTS idx_job_owner_state ON rav_pilot_job(owner_id, state, created_at);

CREATE TABLE IF NOT EXISTS rav_pilot_job_attempt (
    id UUID PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES rav_pilot_job(id) ON DELETE RESTRICT,
    attempt_n INT NOT NULL CHECK (attempt_n > 0),
    fence BIGINT NOT NULL CHECK (fence > 0),
    worker_id UUID NOT NULL,
    host_boot_id UUID NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('CLAIMED', 'WORKING', 'STOPPING', 'CLOSED', 'LOST')),
    lease_until TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    process_group JSONB,
    launch_state TEXT NOT NULL DEFAULT 'NONE'
        CHECK (launch_state IN ('NONE', 'INTENT', 'LAUNCH_UNCERTAIN', 'ATTACHED', 'STOP_PENDING', 'STOPPED')),
    launch_marker TEXT UNIQUE,
    provider_account TEXT,
    session_id TEXT,
    operation_name TEXT,
    cleanup_done BOOLEAN NOT NULL DEFAULT false,
    session_stop_verified BOOLEAN NOT NULL DEFAULT false,
    usage_state TEXT NOT NULL DEFAULT 'OPEN' CHECK (usage_state IN ('OPEN', 'UNKNOWN', 'FINAL')),
    usage JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (job_id, attempt_n),
    UNIQUE (job_id, fence)
);
CREATE INDEX IF NOT EXISTS idx_attempt_lease ON rav_pilot_job_attempt(lease_until, state);

CREATE TABLE IF NOT EXISTS rav_pilot_quota_hold (
    id UUID PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES rav_pilot_job(id) ON DELETE RESTRICT,
    resource TEXT NOT NULL CHECK (resource IN ('CPU', 'GPU')),
    generation INT NOT NULL DEFAULT 1,
    reserved_ms BIGINT NOT NULL CHECK (reserved_ms >= 0),
    consumed_ms BIGINT NOT NULL DEFAULT 0 CHECK (consumed_ms >= 0),
    released_ms BIGINT NOT NULL DEFAULT 0 CHECK (released_ms >= 0),
    state TEXT NOT NULL DEFAULT 'OPEN' CHECK (state IN ('OPEN', 'FINAL')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (job_id, resource, generation),
    CHECK (consumed_ms + released_ms <= reserved_ms)
);

CREATE TABLE IF NOT EXISTS rav_pilot_quota_hold_part (
    hold_id UUID NOT NULL REFERENCES rav_pilot_quota_hold(id) ON DELETE RESTRICT,
    part_n INT NOT NULL CHECK (part_n >= 1),
    window_id UUID REFERENCES rav_pilot_quota_window(id) ON DELETE RESTRICT,
    bonus_id UUID REFERENCES rav_pilot_quota_bonus(id) ON DELETE RESTRICT,
    reserved_ms BIGINT NOT NULL CHECK (reserved_ms >= 0),
    consumed_ms BIGINT NOT NULL DEFAULT 0 CHECK (consumed_ms >= 0),
    released_ms BIGINT NOT NULL DEFAULT 0 CHECK (released_ms >= 0),
    PRIMARY KEY (hold_id, part_n),
    CHECK ((window_id IS NOT NULL) <> (bonus_id IS NOT NULL)),
    CHECK (consumed_ms + released_ms <= reserved_ms)
);

CREATE TABLE IF NOT EXISTS rav_pilot_usage_receipt (
    id UUID PRIMARY KEY,
    seq BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
    job_id UUID NOT NULL REFERENCES rav_pilot_job(id) ON DELETE RESTRICT,
    attempt_id UUID NOT NULL REFERENCES rav_pilot_job_attempt(id) ON DELETE RESTRICT,
    resource TEXT NOT NULL CHECK (resource IN ('CPU', 'GPU')),
    settlement_key TEXT NOT NULL UNIQUE,
    actual_ms BIGINT NOT NULL CHECK (actual_ms >= 0),
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    closed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (attempt_id, resource)
);
DROP TRIGGER IF EXISTS trg_rav_pilot_usage_receipt_immutable ON rav_pilot_usage_receipt;
CREATE TRIGGER trg_rav_pilot_usage_receipt_immutable BEFORE UPDATE OR DELETE ON rav_pilot_usage_receipt
    FOR EACH ROW EXECUTE FUNCTION rav_pilot_reject_mutation();

CREATE TABLE IF NOT EXISTS rav_pilot_resource_slot (
    slot_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('CPU', 'GPU', 'MEDIA')),
    account_or_host TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('FREE', 'HELD', 'QUARANTINED')),
    generation BIGINT NOT NULL DEFAULT 0 CHECK (generation >= 0),
    attempt_id UUID REFERENCES rav_pilot_job_attempt(id) ON DELETE RESTRICT,
    lease_until TIMESTAMPTZ,
    reconcile_evidence JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((state = 'FREE') = (attempt_id IS NULL))
);

CREATE TABLE IF NOT EXISTS rav_pilot_source_pin (
    id UUID PRIMARY KEY,
    source_id UUID NOT NULL REFERENCES rav_pilot_source_asset(id) ON DELETE RESTRICT,
    consumer_kind TEXT NOT NULL CHECK (consumer_kind IN ('SESSION', 'SNAPSHOT', 'REFERENCE', 'JOB', 'EXPORT')),
    consumer_id UUID NOT NULL,
    input_role TEXT NOT NULL CHECK (input_role IN ('POOL', 'REFERENCE', 'INPUT', 'EXPORT_SOURCE')),
    source_version BIGINT NOT NULL CHECK (source_version >= 1),
    released_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, consumer_kind, consumer_id, input_role)
);
CREATE INDEX IF NOT EXISTS idx_source_pin_active ON rav_pilot_source_pin(source_id) WHERE released_at IS NULL;

-- Pin SESSION/SNAPSHOT/REFERENCE/EXPORT không được release trong pilot; chỉ JOB pin release qua runtime (§21.9).
CREATE OR REPLACE FUNCTION rav_pilot_pin_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'không DELETE source pin' USING ERRCODE = 'check_violation';
    END IF;
    IF (NEW.id, NEW.source_id, NEW.consumer_kind, NEW.consumer_id, NEW.input_role, NEW.source_version)
       IS DISTINCT FROM (OLD.id, OLD.source_id, OLD.consumer_kind, OLD.consumer_id, OLD.input_role, OLD.source_version)
       OR OLD.released_at IS NOT NULL
       OR (NEW.released_at IS NOT NULL AND OLD.consumer_kind <> 'JOB') THEN
        RAISE EXCEPTION 'source pin chỉ được release một lần và chỉ với consumer JOB' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_pin_guard ON rav_pilot_source_pin;
CREATE TRIGGER trg_pin_guard BEFORE UPDATE OR DELETE ON rav_pilot_source_pin
    FOR EACH ROW EXECUTE FUNCTION rav_pilot_pin_guard();

CREATE TABLE IF NOT EXISTS rav_pilot_dispatch_cursor (
    queue TEXT PRIMARY KEY CHECK (queue IN ('CPU', 'GPU', 'MEDIA')),
    last_owner_id UUID,
    revision BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------------------------
-- RLS + grants: bảng runtime chỉ backend role; người dùng xem job/quota qua DTO của API.
-- ------------------------------------------------------------------------------
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['rav_pilot_quota_policy', 'rav_pilot_quota_window', 'rav_pilot_quota_bonus', 'rav_pilot_job',
                             'rav_pilot_job_attempt', 'rav_pilot_quota_hold', 'rav_pilot_quota_hold_part',
                             'rav_pilot_usage_receipt', 'rav_pilot_resource_slot', 'rav_pilot_source_pin',
                             'rav_pilot_dispatch_cursor'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('REVOKE ALL ON %I FROM PUBLIC, anon, authenticated', t);
        EXECUTE format('GRANT SELECT, INSERT, UPDATE ON %I TO rav_pilot_api, rav_pilot_worker', t);
        EXECUTE format('DROP POLICY IF EXISTS p_backend_all ON %I', t);
        EXECUTE format('CREATE POLICY p_backend_all ON %I TO rav_pilot_api, rav_pilot_worker USING (true) WITH CHECK (true)', t);
    END LOOP;
END $$;
-- Policy/bonus/slot inventory là việc của operator: backend chỉ đọc (bonus held/consumed cập nhật khi giữ/tất toán).
REVOKE INSERT, UPDATE ON rav_pilot_quota_policy FROM rav_pilot_api, rav_pilot_worker;
REVOKE INSERT ON rav_pilot_quota_bonus, rav_pilot_resource_slot FROM rav_pilot_api, rav_pilot_worker;
