-- ==============================================================================
-- 003_pilot_storage.sql — dung lượng, upload trực tiếp, artifact, media riêng tư, phiên mẫu SYSTEM (DEP4, #91)
-- Nguồn sự thật: CONTRACTS §21.7, §21.10, §21.12; runtime spec §3.3, §6; identity spec §5 (template/import), §9.
-- Chạy bởi operator qua scripts/pilot_migrate.py sau 001, 002. Không seed dữ liệu.
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- Dung lượng: tài khoản USER:<uuid> / GLOBAL và hold (receipt idempotent)
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rav_pilot_storage_account (
    account_key TEXT PRIMARY KEY CHECK (account_key = 'GLOBAL' OR account_key ~ '^USER:[0-9a-f-]{36}$'),
    limit_bytes BIGINT NOT NULL CHECK (limit_bytes >= 0),
    persisted_bytes BIGINT NOT NULL DEFAULT 0 CHECK (persisted_bytes >= 0),
    reserved_bytes BIGINT NOT NULL DEFAULT 0 CHECK (reserved_bytes >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (persisted_bytes + reserved_bytes <= limit_bytes)
);

CREATE TABLE IF NOT EXISTS rav_pilot_storage_hold (
    id UUID PRIMARY KEY,
    owner_id UUID NOT NULL REFERENCES rav_pilot_profile(user_id) ON DELETE RESTRICT,
    job_id UUID REFERENCES rav_pilot_job(id) ON DELETE RESTRICT,
    upload_id UUID,
    reserved_bytes BIGINT NOT NULL CHECK (reserved_bytes >= 0),
    staging_observed_bytes BIGINT NOT NULL DEFAULT 0 CHECK (staging_observed_bytes >= 0),
    persisted_bytes BIGINT NOT NULL DEFAULT 0 CHECK (persisted_bytes >= 0),
    released_bytes BIGINT NOT NULL DEFAULT 0 CHECK (released_bytes >= 0),
    state TEXT NOT NULL DEFAULT 'OPEN' CHECK (state IN ('OPEN', 'FINAL')),
    receipt_key TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (persisted_bytes + released_bytes <= reserved_bytes),
    CHECK (job_id IS NOT NULL OR upload_id IS NOT NULL)
);

-- ------------------------------------------------------------------------------
-- Upload trực tiếp (presigned POST vào staging → worker xác minh → canonical)
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rav_pilot_upload (
    id UUID PRIMARY KEY,
    owner_id UUID NOT NULL REFERENCES rav_pilot_profile(user_id) ON DELETE RESTRICT,
    reserved_source_id UUID NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL,
    declared_bytes BIGINT NOT NULL CHECK (declared_bytes > 0),
    filename_display TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('CREATED', 'UPLOADED', 'VALIDATING', 'READY', 'FAILED', 'ABORTED')),
    staging_key TEXT NOT NULL UNIQUE,
    capability_expires_at TIMESTAMPTZ NOT NULL,
    canonical_key TEXT,
    verified_sha256 CHAR(64),
    verified_bytes BIGINT CHECK (verified_bytes IS NULL OR verified_bytes > 0),
    source_id UUID REFERENCES rav_pilot_source_asset(id) ON DELETE RESTRICT,
    storage_hold_id UUID NOT NULL REFERENCES rav_pilot_storage_hold(id) ON DELETE RESTRICT,
    validation_job_id UUID REFERENCES rav_pilot_job(id) ON DELETE RESTRICT,
    error JSONB,
    input_hash CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_id, idempotency_key),
    CHECK ((state = 'READY') = (source_id IS NOT NULL))
);
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_storage_hold_upload') THEN
        ALTER TABLE rav_pilot_storage_hold ADD CONSTRAINT fk_storage_hold_upload
            FOREIGN KEY (upload_id) REFERENCES rav_pilot_upload(id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED;
    END IF;
END $$;

-- ------------------------------------------------------------------------------
-- Artifact (byte do worker tạo) và media riêng tư
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rav_pilot_artifact (
    id UUID PRIMARY KEY,
    owner_id UUID REFERENCES rav_pilot_profile(user_id) ON DELETE RESTRICT,
    scope TEXT NOT NULL CHECK (scope IN ('PRIVATE', 'SYSTEM')),
    source_id UUID REFERENCES rav_pilot_source_asset(id) ON DELETE RESTRICT,
    job_id UUID REFERENCES rav_pilot_job(id) ON DELETE RESTRICT,
    attempt_id UUID REFERENCES rav_pilot_job_attempt(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK (kind IN ('FEATURE', 'MEDIA', 'EXPORT_PREVIEW', 'EXPORT_BUNDLE', 'REPORT', 'BACKUP')),
    variant TEXT NOT NULL,
    input_hash CHAR(64) NOT NULL,
    object_key TEXT NOT NULL UNIQUE,
    sha256 CHAR(64) NOT NULL,
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    state TEXT NOT NULL CHECK (state IN ('STAGED', 'READY', 'DELETED')),
    version INT NOT NULL DEFAULT 1 CHECK (version > 0),
    policy_version TEXT,
    manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((scope = 'PRIVATE' AND owner_id IS NOT NULL) OR (scope = 'SYSTEM' AND owner_id IS NULL))
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_job_result_artifact') THEN
        ALTER TABLE rav_pilot_job ADD CONSTRAINT fk_job_result_artifact
            FOREIGN KEY (result_ref) REFERENCES rav_pilot_artifact(id) ON DELETE RESTRICT;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS rav_pilot_media (
    id UUID PRIMARY KEY,
    owner_id UUID NOT NULL REFERENCES rav_pilot_profile(user_id) ON DELETE RESTRICT,
    source_id UUID NOT NULL REFERENCES rav_pilot_source_asset(id) ON DELETE RESTRICT,
    unit_id TEXT NOT NULL,
    variant TEXT NOT NULL CHECK (variant IN ('THUMBNAIL', 'FRAME', 'CONTEXT')),
    params JSONB NOT NULL DEFAULT '{}'::jsonb,
    input_hash CHAR(64) NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('PENDING', 'READY', 'FAILED')),
    job_id UUID REFERENCES rav_pilot_job(id) ON DELETE RESTRICT,
    artifact_id UUID REFERENCES rav_pilot_artifact(id) ON DELETE RESTRICT,
    error JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_id, input_hash),
    CHECK ((state = 'READY') = (artifact_id IS NOT NULL))
);

-- ------------------------------------------------------------------------------
-- Phiên mẫu SYSTEM (chỉ đọc) và receipt import
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rav_pilot_demo_template (
    id UUID PRIMARY KEY,
    template_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    legacy_session_id TEXT NOT NULL,
    manifest_hash CHAR(64) NOT NULL UNIQUE,
    frozen_spec JSONB NOT NULL,
    source_mapping JSONB NOT NULL,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rav_pilot_demo_template_status (
    template_id UUID PRIMARY KEY REFERENCES rav_pilot_demo_template(id) ON DELETE RESTRICT,
    availability TEXT NOT NULL CHECK (availability IN ('IMPORTED', 'VERIFIED', 'BLOCKED')),
    reason_code TEXT,
    version BIGINT NOT NULL DEFAULT 1 CHECK (version >= 1),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rav_pilot_demo_template_event (
    template_id UUID NOT NULL REFERENCES rav_pilot_demo_template(id) ON DELETE RESTRICT,
    source_event_id TEXT NOT NULL,
    ord INT NOT NULL CHECK (ord >= 0),
    kind TEXT NOT NULL CHECK (kind IN ('PROPOSAL', 'DECISION', 'SNAPSHOT')),
    legacy_payload JSONB NOT NULL,
    payload_hash CHAR(64) NOT NULL,
    PRIMARY KEY (template_id, source_event_id),
    UNIQUE (template_id, ord)
);

CREATE TABLE IF NOT EXISTS rav_pilot_import_receipt (
    id UUID PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    inventory_hash CHAR(64) NOT NULL,
    mapping JSONB NOT NULL,
    counts JSONB NOT NULL,
    hashes JSONB NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('STAGING', 'VERIFIED', 'COMMITTED', 'FAILED')),
    operator_id UUID REFERENCES rav_pilot_profile(user_id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_session_template') THEN
        ALTER TABLE rav_pilot_session ADD CONSTRAINT fk_session_template
            FOREIGN KEY (started_from_template_id) REFERENCES rav_pilot_demo_template(id) ON DELETE RESTRICT;
    END IF;
END $$;

-- Bất biến: template + event (status đổi qua operator, bảng riêng).
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['rav_pilot_demo_template', 'rav_pilot_demo_template_event'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_immutable ON %I', t, t);
        EXECUTE format('CREATE TRIGGER trg_%s_immutable BEFORE UPDATE OR DELETE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION rav_pilot_reject_mutation()', t, t);
    END LOOP;
END $$;

-- Artifact: object/sha/size/owner bất biến; chỉ state chuyển STAGED → READY → DELETED.
CREATE OR REPLACE FUNCTION rav_pilot_artifact_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'không DELETE artifact; dùng state DELETED' USING ERRCODE = 'check_violation';
    END IF;
    IF (NEW.id, NEW.owner_id, NEW.scope, NEW.source_id, NEW.object_key, NEW.sha256, NEW.size_bytes, NEW.kind, NEW.variant, NEW.input_hash)
       IS DISTINCT FROM
       (OLD.id, OLD.owner_id, OLD.scope, OLD.source_id, OLD.object_key, OLD.sha256, OLD.size_bytes, OLD.kind, OLD.variant, OLD.input_hash)
       OR (OLD.state = 'READY' AND NEW.state = 'STAGED') OR OLD.state = 'DELETED' THEN
        RAISE EXCEPTION 'artifact: danh tính bất biến, state chỉ tiến STAGED → READY → DELETED' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_artifact_guard ON rav_pilot_artifact;
CREATE TRIGGER trg_artifact_guard BEFORE UPDATE OR DELETE ON rav_pilot_artifact
    FOR EACH ROW EXECUTE FUNCTION rav_pilot_artifact_guard();

-- ------------------------------------------------------------------------------
-- RLS + grants
-- ------------------------------------------------------------------------------
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['rav_pilot_storage_account', 'rav_pilot_storage_hold', 'rav_pilot_upload', 'rav_pilot_artifact',
                             'rav_pilot_media', 'rav_pilot_demo_template', 'rav_pilot_demo_template_status',
                             'rav_pilot_demo_template_event', 'rav_pilot_import_receipt'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('REVOKE ALL ON %I FROM PUBLIC, anon, authenticated', t);
        EXECUTE format('DROP POLICY IF EXISTS p_backend_all ON %I', t);
        EXECUTE format('CREATE POLICY p_backend_all ON %I TO rav_pilot_api, rav_pilot_worker USING (true) WITH CHECK (true)', t);
    END LOOP;
END $$;
-- API: tạo/đọc upload, media, giữ chỗ dung lượng; đọc mẫu. Worker: publish nguồn/artifact, cập nhật upload/media/dung lượng.
GRANT SELECT, INSERT, UPDATE ON rav_pilot_storage_account, rav_pilot_storage_hold, rav_pilot_upload, rav_pilot_media
    TO rav_pilot_api, rav_pilot_worker;
GRANT SELECT ON rav_pilot_artifact, rav_pilot_demo_template, rav_pilot_demo_template_status, rav_pilot_demo_template_event
    TO rav_pilot_api, rav_pilot_worker;
GRANT INSERT, UPDATE ON rav_pilot_artifact TO rav_pilot_worker;
GRANT INSERT ON rav_pilot_source_asset TO rav_pilot_worker;
GRANT INSERT ON rav_pilot_command_receipt TO rav_pilot_api;
-- FOR SHARE khi tạo phiên từ mẫu cần quyền UPDATE ít nhất một cột; API không đổi được availability.
GRANT UPDATE (updated_at) ON rav_pilot_demo_template_status TO rav_pilot_api;
-- authenticated chỉ đọc mẫu (thành viên active); cột payload thô không cấp, API trả projection.
GRANT SELECT ON rav_pilot_demo_template_status TO authenticated;
GRANT SELECT (id, template_key, name, frozen_spec, created_at) ON rav_pilot_demo_template TO authenticated;
DROP POLICY IF EXISTS p_template_read ON rav_pilot_demo_template;
CREATE POLICY p_template_read ON rav_pilot_demo_template FOR SELECT TO authenticated USING (rav_pilot_is_active(rav_pilot_uid()));
DROP POLICY IF EXISTS p_template_status_read ON rav_pilot_demo_template_status;
CREATE POLICY p_template_status_read ON rav_pilot_demo_template_status FOR SELECT TO authenticated
    USING (rav_pilot_is_active(rav_pilot_uid()));
