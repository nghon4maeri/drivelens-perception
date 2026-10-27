-- ==============================================================================
-- 001_pilot_identity.sql — identity, catalog, phiên, quyết định, snapshot (DEP2, sửa ở DEP2F #89)
-- Nguồn sự thật: CONTRACTS §21 (+ §21.11), DEPLOYMENT_IDENTITY_DATA_SPEC §5–6.
-- Chạy bởi operator: `python -m scripts.pilot_migrate --database-url ...` (không chạy khi API boot).
-- Mọi FK nghiệp vụ ON DELETE RESTRICT; bảng sự kiện/snapshot bất biến bằng trigger; RLS bật trên mọi bảng.
-- ==============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Role: Supabase đã có authenticated/anon; Postgres thường thì tạo NOLOGIN. Operator bật LOGIN cho api/worker.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN CREATE ROLE anon NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rav_pilot_api') THEN CREATE ROLE rav_pilot_api NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rav_pilot_worker') THEN CREATE ROLE rav_pilot_worker NOLOGIN; END IF;
END $$;
GRANT authenticated TO rav_pilot_api;
GRANT USAGE ON SCHEMA public TO authenticated, rav_pilot_api, rav_pilot_worker;

CREATE TABLE IF NOT EXISTS rav_pilot_schema_migration (
    version TEXT PRIMARY KEY,
    checksum CHAR(64) NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------------------------
-- Hàm dùng chung
-- ------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION rav_pilot_uid() RETURNS uuid
LANGUAGE sql STABLE AS $$
    SELECT NULLIF(NULLIF(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub', '')::uuid
$$;

CREATE OR REPLACE FUNCTION rav_pilot_reject_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% là bảng bất biến: cấm UPDATE/DELETE (CONTRACTS §21.3)', TG_TABLE_NAME
        USING ERRCODE = 'check_violation';
END $$;

-- ------------------------------------------------------------------------------
-- Profile (operator tạo; không có cột vai trò — §21.1, §21.11)
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rav_pilot_profile (
    user_id UUID PRIMARY KEY,
    active BOOLEAN NOT NULL DEFAULT true,
    display_name TEXT NOT NULL DEFAULT '',
    authz_version BIGINT NOT NULL DEFAULT 1 CHECK (authz_version >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Trên Supabase profile tham chiếu auth.users; Postgres thường (test) không có schema auth.
DO $$
BEGIN
    IF to_regclass('auth.users') IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_profile_auth_user') THEN
        ALTER TABLE rav_pilot_profile ADD CONSTRAINT fk_profile_auth_user
            FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE RESTRICT;
    END IF;
END $$;

CREATE OR REPLACE FUNCTION rav_pilot_is_active(p_user uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT COALESCE((SELECT active FROM public.rav_pilot_profile WHERE user_id = p_user), false)
$$;
REVOKE ALL ON FUNCTION rav_pilot_is_active(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rav_pilot_is_active(uuid) TO authenticated, rav_pilot_api, rav_pilot_worker;

-- ------------------------------------------------------------------------------
-- Nguồn (video / bộ ảnh). SYSTEM: owner NULL + protected. Core ID giữ baseline (§21.2).
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rav_pilot_source_asset (
    id UUID PRIMARY KEY,
    core_source_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('VIDEO', 'IMAGE_SET')),
    scope TEXT NOT NULL CHECK (scope IN ('PRIVATE', 'SYSTEM')),
    owner_id UUID REFERENCES rav_pilot_profile(user_id) ON DELETE RESTRICT,
    protected BOOLEAN NOT NULL DEFAULT false,
    name TEXT NOT NULL,
    raw_key TEXT NOT NULL,
    raw_sha256 CHAR(64) NOT NULL CHECK (raw_sha256 ~ '^[0-9a-f]{64}$'),
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    availability TEXT NOT NULL CHECK (availability IN ('READY', 'UNAVAILABLE', 'DELETE_PENDING', 'DELETED')),
    resource_version BIGINT NOT NULL DEFAULT 1 CHECK (resource_version >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_source_scope_owner CHECK (
        (scope = 'PRIVATE' AND owner_id IS NOT NULL) OR
        (scope = 'SYSTEM' AND owner_id IS NULL AND protected))
);
-- Nguồn đã DELETED (tombstone) không chặn upload lại cùng nội dung (#91).
CREATE UNIQUE INDEX IF NOT EXISTS uq_source_private_core ON rav_pilot_source_asset(owner_id, core_source_id)
    WHERE scope = 'PRIVATE' AND availability <> 'DELETED';
CREATE UNIQUE INDEX IF NOT EXISTS uq_source_system_core ON rav_pilot_source_asset(core_source_id)
    WHERE scope = 'SYSTEM' AND availability <> 'DELETED';
CREATE INDEX IF NOT EXISTS idx_source_owner ON rav_pilot_source_asset(owner_id);

-- Danh tính nguồn bất biến sau publish; chỉ name/metadata/availability/resource_version đổi được (identity spec §5).
CREATE OR REPLACE FUNCTION rav_pilot_source_identity_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'không DELETE hàng nguồn; dùng tombstone DELETE_PENDING/DELETED (§21.9)' USING ERRCODE = 'check_violation';
    END IF;
    IF (NEW.core_source_id, NEW.kind, NEW.scope, NEW.owner_id, NEW.protected, NEW.raw_key, NEW.raw_sha256, NEW.size_bytes)
       IS DISTINCT FROM
       (OLD.core_source_id, OLD.kind, OLD.scope, OLD.owner_id, OLD.protected, OLD.raw_key, OLD.raw_sha256, OLD.size_bytes) THEN
        RAISE EXCEPTION 'danh tính nguồn bất biến (core/kind/scope/owner/protected/raw)' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_source_identity_guard ON rav_pilot_source_asset;
CREATE TRIGGER trg_source_identity_guard BEFORE UPDATE OR DELETE ON rav_pilot_source_asset
    FOR EACH ROW EXECUTE FUNCTION rav_pilot_source_identity_guard();

-- ------------------------------------------------------------------------------
-- Dataset / model tham chiếu (SYSTEM, operator seed — DEP4). Chỉ schema ở lát này.
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rav_pilot_dataset (
    id UUID PRIMARY KEY,
    core_dataset_id TEXT NOT NULL UNIQUE,
    source_id UUID NOT NULL REFERENCES rav_pilot_source_asset(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rav_pilot_dataset_version (
    id UUID PRIMARY KEY,
    core_version_id TEXT NOT NULL UNIQUE,
    dataset_id UUID NOT NULL REFERENCES rav_pilot_dataset(id) ON DELETE RESTRICT,
    n INT NOT NULL CHECK (n >= 1),
    manifest JSONB NOT NULL,
    manifest_hash CHAR(64) NOT NULL,
    annotation_format TEXT NOT NULL CHECK (annotation_format IN ('none', 'yolo')),
    classes JSONB NOT NULL DEFAULT '[]'::jsonb,
    import_report JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (dataset_id, n),
    UNIQUE (dataset_id, manifest_hash)
);

CREATE TABLE IF NOT EXISTS rav_pilot_dataset_member (
    version_id UUID NOT NULL REFERENCES rav_pilot_dataset_version(id) ON DELETE RESTRICT,
    ord INT NOT NULL CHECK (ord >= 0),
    sample_id TEXT NOT NULL,
    key TEXT NOT NULL,
    relpath TEXT NOT NULL,
    sha256 CHAR(64) NOT NULL,
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    raw_key TEXT NOT NULL,
    label_key TEXT,
    label_sha256 CHAR(64),
    labels JSONB,
    PRIMARY KEY (version_id, sample_id),
    UNIQUE (version_id, ord),
    UNIQUE (version_id, key),
    CHECK ((label_key IS NULL) = (label_sha256 IS NULL))
);

CREATE TABLE IF NOT EXISTS rav_pilot_model_asset (
    id UUID PRIMARY KEY,
    core_model_id TEXT NOT NULL UNIQUE,
    scope TEXT NOT NULL DEFAULT 'SYSTEM' CHECK (scope = 'SYSTEM'),
    protected BOOLEAN NOT NULL DEFAULT true CHECK (protected),
    name TEXT NOT NULL,
    raw_key TEXT NOT NULL,
    sha256 CHAR(64) NOT NULL,
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    checkpoint_revision TEXT NOT NULL,
    task TEXT NOT NULL,
    class_map JSONB NOT NULL DEFAULT '{}'::jsonb,
    runtime_manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
    license JSONB NOT NULL DEFAULT '{}'::jsonb,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    validation TEXT NOT NULL CHECK (validation IN ('CONTAINER_ONLY', 'INFERENCE_VERIFIED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rav_pilot_model_dataset_declaration (
    model_id UUID NOT NULL REFERENCES rav_pilot_model_asset(id) ON DELETE RESTRICT,
    dataset_version_id UUID NOT NULL REFERENCES rav_pilot_dataset_version(id) ON DELETE RESTRICT,
    relation TEXT NOT NULL CHECK (relation IN ('TRAIN', 'FINETUNE')),
    declared_by UUID NOT NULL REFERENCES rav_pilot_profile(user_id) ON DELETE RESTRICT,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (model_id, dataset_version_id)
);

-- ------------------------------------------------------------------------------
-- Phiên (PRIVATE). state_version tăng mỗi lô quyết định; review state = replay decision.
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rav_pilot_session (
    id UUID PRIMARY KEY,
    owner_id UUID NOT NULL REFERENCES rav_pilot_profile(user_id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    goal TEXT NOT NULL,
    target_k INT NOT NULL CHECK (target_k > 0),
    spec JSONB NOT NULL,
    n_units INT NOT NULL CHECK (n_units >= 0),
    state_version BIGINT NOT NULL DEFAULT 1 CHECK (state_version >= 1),
    started_from_template_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (id, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_session_owner ON rav_pilot_session(owner_id, created_at);

-- owner/spec/goal/target_k/nguồn khởi tạo bất biến; chỉ name và state_version đổi qua command.
CREATE OR REPLACE FUNCTION rav_pilot_session_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'không DELETE phiên trong pilot' USING ERRCODE = 'check_violation';
    END IF;
    IF (NEW.owner_id, NEW.goal, NEW.target_k, NEW.spec, NEW.started_from_template_id)
       IS DISTINCT FROM (OLD.owner_id, OLD.goal, OLD.target_k, OLD.spec, OLD.started_from_template_id)
       OR NEW.state_version < OLD.state_version THEN
        RAISE EXCEPTION 'phiên: owner/goal/target_k/spec bất biến, state_version không giảm' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_session_guard ON rav_pilot_session;
CREATE TRIGGER trg_session_guard BEFORE UPDATE OR DELETE ON rav_pilot_session
    FOR EACH ROW EXECUTE FUNCTION rav_pilot_session_guard();

CREATE TABLE IF NOT EXISTS rav_pilot_session_source (
    session_id UUID NOT NULL,
    owner_id UUID NOT NULL,
    source_id UUID NOT NULL REFERENCES rav_pilot_source_asset(id) ON DELETE RESTRICT,
    ord INT NOT NULL CHECK (ord >= 0),
    PRIMARY KEY (session_id, source_id),
    UNIQUE (session_id, ord),
    FOREIGN KEY (session_id, owner_id) REFERENCES rav_pilot_session(id, owner_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS rav_pilot_session_reference (
    session_id UUID NOT NULL,
    owner_id UUID NOT NULL,
    dataset_version_id UUID NOT NULL REFERENCES rav_pilot_dataset_version(id) ON DELETE RESTRICT,
    model_id UUID REFERENCES rav_pilot_model_asset(id) ON DELETE RESTRICT,
    PRIMARY KEY (session_id, dataset_version_id),
    FOREIGN KEY (session_id, owner_id) REFERENCES rav_pilot_session(id, owner_id) ON DELETE RESTRICT
);

-- Nguồn của phiên phải là VIDEO, PRIVATE cùng owner hoặc SYSTEM (RLS không thay FK integrity — identity spec §6).
CREATE OR REPLACE FUNCTION rav_pilot_session_source_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE s record;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'nguồn của phiên chỉ thêm, không sửa/xóa' USING ERRCODE = 'check_violation';
    END IF;
    SELECT kind, scope, owner_id INTO s FROM rav_pilot_source_asset WHERE id = NEW.source_id;
    IF s.kind <> 'VIDEO' OR NOT (s.scope = 'SYSTEM' OR s.owner_id = NEW.owner_id) THEN
        RAISE EXCEPTION 'nguồn % không dùng được cho phiên của owner %', NEW.source_id, NEW.owner_id USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_session_source_guard ON rav_pilot_session_source;
CREATE TRIGGER trg_session_source_guard BEFORE INSERT OR UPDATE OR DELETE ON rav_pilot_session_source
    FOR EACH ROW EXECUTE FUNCTION rav_pilot_session_source_guard();

-- ------------------------------------------------------------------------------
-- Proposal / decision / snapshot — bất biến
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rav_pilot_proposal (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL,
    owner_id UUID NOT NULL,
    n INT NOT NULL CHECK (n >= 1),
    frozen_input JSONB NOT NULL,
    input_hash CHAR(64) NOT NULL,
    picks JSONB NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    fields_used JSONB NOT NULL DEFAULT '[]'::jsonb,
    state_version BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, n),
    FOREIGN KEY (session_id, owner_id) REFERENCES rav_pilot_session(id, owner_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS rav_pilot_decision (
    seq BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id UUID NOT NULL,
    owner_id UUID NOT NULL,
    unit_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('keep', 'drop', 'clear')),
    actor TEXT NOT NULL,
    goal TEXT NOT NULL,
    proposal_n INT,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    shown_at TIMESTAMPTZ,
    reason TEXT,
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    extra JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (session_id, seq),
    FOREIGN KEY (session_id, owner_id) REFERENCES rav_pilot_session(id, owner_id) ON DELETE RESTRICT,
    FOREIGN KEY (session_id, proposal_n) REFERENCES rav_pilot_proposal(session_id, n) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_decision_session ON rav_pilot_decision(session_id, seq);

CREATE TABLE IF NOT EXISTS rav_pilot_snapshot (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL,
    owner_id UUID NOT NULL,
    n INT NOT NULL CHECK (n >= 1),
    created_by UUID NOT NULL REFERENCES rav_pilot_profile(user_id) ON DELETE RESTRICT,
    note TEXT,
    decision_seq BIGINT NOT NULL CHECK (decision_seq >= 0),
    proposal_cutoff_n INT NOT NULL CHECK (proposal_cutoff_n >= 0),
    report_basis JSONB NOT NULL DEFAULT '{}'::jsonb,
    members_hash CHAR(64) NOT NULL,
    n_members INT NOT NULL CHECK (n_members > 0),
    context JSONB NOT NULL,
    context_hash CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, n),
    UNIQUE (id, owner_id),
    FOREIGN KEY (session_id, owner_id) REFERENCES rav_pilot_session(id, owner_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS rav_pilot_snapshot_member (
    snapshot_id UUID NOT NULL,
    owner_id UUID NOT NULL,
    ord INT NOT NULL CHECK (ord >= 0),
    unit_id TEXT NOT NULL,
    source_id UUID NOT NULL REFERENCES rav_pilot_source_asset(id) ON DELETE RESTRICT,
    core_source_id TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('video', 'image_set')),
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (snapshot_id, ord),
    UNIQUE (snapshot_id, unit_id),
    FOREIGN KEY (snapshot_id, owner_id) REFERENCES rav_pilot_snapshot(id, owner_id) ON DELETE RESTRICT
);

-- ------------------------------------------------------------------------------
-- Receipt lệnh idempotent và yêu cầu xóa nguồn
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rav_pilot_command_receipt (
    id UUID PRIMARY KEY,
    owner_id UUID NOT NULL REFERENCES rav_pilot_profile(user_id) ON DELETE RESTRICT,
    action TEXT NOT NULL CHECK (action IN ('CREATE_SESSION', 'CREATE_SNAPSHOT', 'FORK_TEMPLATE')),
    idempotency_key TEXT NOT NULL,
    input_hash CHAR(64) NOT NULL,
    safe_output JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_id, action, idempotency_key)
);

CREATE TABLE IF NOT EXISTS rav_pilot_source_delete (
    id UUID PRIMARY KEY,
    source_id UUID NOT NULL REFERENCES rav_pilot_source_asset(id) ON DELETE RESTRICT,
    owner_id UUID NOT NULL REFERENCES rav_pilot_profile(user_id) ON DELETE RESTRICT,
    idempotency_key TEXT NOT NULL,
    expected_resource_version BIGINT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('REQUESTED', 'RUNNING', 'SUCCEEDED', 'FAILED')),
    receipt JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_id, idempotency_key)
);

-- ------------------------------------------------------------------------------
-- Trigger bất biến (§21.3)
-- ------------------------------------------------------------------------------
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['rav_pilot_dataset_version', 'rav_pilot_dataset_member', 'rav_pilot_model_asset',
                             'rav_pilot_model_dataset_declaration', 'rav_pilot_proposal', 'rav_pilot_decision',
                             'rav_pilot_snapshot', 'rav_pilot_snapshot_member', 'rav_pilot_command_receipt'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_immutable ON %I', t, t);
        EXECUTE format('CREATE TRIGGER trg_%s_immutable BEFORE UPDATE OR DELETE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION rav_pilot_reject_mutation()', t, t);
    END LOOP;
END $$;

-- ------------------------------------------------------------------------------
-- RLS: authenticated chỉ SELECT phạm vi của mình (+ SYSTEM); backend role ghi với authz tường minh trong adapter.
-- ------------------------------------------------------------------------------
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['rav_pilot_schema_migration', 'rav_pilot_profile', 'rav_pilot_source_asset', 'rav_pilot_dataset',
                             'rav_pilot_dataset_version', 'rav_pilot_dataset_member', 'rav_pilot_model_asset',
                             'rav_pilot_model_dataset_declaration', 'rav_pilot_session', 'rav_pilot_session_source',
                             'rav_pilot_session_reference', 'rav_pilot_proposal', 'rav_pilot_decision', 'rav_pilot_snapshot',
                             'rav_pilot_snapshot_member', 'rav_pilot_command_receipt', 'rav_pilot_source_delete'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('REVOKE ALL ON %I FROM PUBLIC, anon, authenticated', t);
        EXECUTE format('GRANT SELECT, INSERT, UPDATE ON %I TO rav_pilot_api', t);
        EXECUTE format('GRANT SELECT ON %I TO rav_pilot_worker', t);
        EXECUTE format('DROP POLICY IF EXISTS p_backend_all ON %I', t);
        EXECUTE format('CREATE POLICY p_backend_all ON %I TO rav_pilot_api, rav_pilot_worker USING (true) WITH CHECK (true)', t);
    END LOOP;
END $$;
-- Profile, catalog SYSTEM và nguồn do operator/worker tạo; API chỉ đổi trạng thái được phép qua adapter.
REVOKE INSERT, UPDATE ON rav_pilot_schema_migration, rav_pilot_profile, rav_pilot_dataset, rav_pilot_dataset_version,
    rav_pilot_dataset_member, rav_pilot_model_asset, rav_pilot_model_dataset_declaration FROM rav_pilot_api;
REVOKE INSERT ON rav_pilot_source_asset FROM rav_pilot_api;
REVOKE UPDATE ON rav_pilot_source_asset FROM rav_pilot_api;
GRANT UPDATE (availability, resource_version, name) ON rav_pilot_source_asset TO rav_pilot_api;
GRANT UPDATE (availability, resource_version, metadata) ON rav_pilot_source_asset TO rav_pilot_worker;
-- SELECT … FOR UPDATE cần quyền UPDATE ít nhất một cột: API khóa profile (thứ tự §21.3) nhưng không đổi được active/policy.
GRANT UPDATE (display_name) ON rav_pilot_profile TO rav_pilot_api;
REVOKE UPDATE ON rav_pilot_session, rav_pilot_source_delete FROM rav_pilot_api;
GRANT UPDATE (name, state_version) ON rav_pilot_session TO rav_pilot_api;
GRANT UPDATE (state, receipt) ON rav_pilot_source_delete TO rav_pilot_worker;

-- SELECT cho authenticated: không cấp cột raw_key/raw_sha256 (identity spec §6).
GRANT SELECT (user_id, active, display_name, authz_version, created_at) ON rav_pilot_profile TO authenticated;
GRANT SELECT (id, core_source_id, kind, scope, owner_id, protected, name, size_bytes, metadata, availability,
              resource_version, created_at) ON rav_pilot_source_asset TO authenticated;
GRANT SELECT ON rav_pilot_dataset, rav_pilot_dataset_version, rav_pilot_session, rav_pilot_session_source,
              rav_pilot_session_reference, rav_pilot_proposal, rav_pilot_decision, rav_pilot_snapshot,
              rav_pilot_snapshot_member, rav_pilot_command_receipt, rav_pilot_source_delete TO authenticated;
GRANT SELECT (id, core_model_id, scope, protected, name, sha256, size_bytes, checkpoint_revision, task, class_map,
              license, validation, created_at) ON rav_pilot_model_asset TO authenticated;

DROP POLICY IF EXISTS p_profile_self ON rav_pilot_profile;
CREATE POLICY p_profile_self ON rav_pilot_profile FOR SELECT TO authenticated USING (user_id = rav_pilot_uid());

DROP POLICY IF EXISTS p_source_read ON rav_pilot_source_asset;
CREATE POLICY p_source_read ON rav_pilot_source_asset FOR SELECT TO authenticated
    USING (rav_pilot_is_active(rav_pilot_uid()) AND (owner_id = rav_pilot_uid() OR scope = 'SYSTEM'));

DROP POLICY IF EXISTS p_dataset_read ON rav_pilot_dataset;
CREATE POLICY p_dataset_read ON rav_pilot_dataset FOR SELECT TO authenticated USING (rav_pilot_is_active(rav_pilot_uid()));
DROP POLICY IF EXISTS p_dataset_version_read ON rav_pilot_dataset_version;
CREATE POLICY p_dataset_version_read ON rav_pilot_dataset_version FOR SELECT TO authenticated USING (rav_pilot_is_active(rav_pilot_uid()));
DROP POLICY IF EXISTS p_model_read ON rav_pilot_model_asset;
CREATE POLICY p_model_read ON rav_pilot_model_asset FOR SELECT TO authenticated USING (rav_pilot_is_active(rav_pilot_uid()));

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['rav_pilot_session', 'rav_pilot_session_source', 'rav_pilot_session_reference', 'rav_pilot_proposal',
                             'rav_pilot_decision', 'rav_pilot_snapshot', 'rav_pilot_snapshot_member',
                             'rav_pilot_command_receipt', 'rav_pilot_source_delete'] LOOP
        EXECUTE format('DROP POLICY IF EXISTS p_owner_read ON %I', t);
        EXECUTE format('CREATE POLICY p_owner_read ON %I FOR SELECT TO authenticated '
                       'USING (owner_id = rav_pilot_uid() AND rav_pilot_is_active(rav_pilot_uid()))', t);
    END LOOP;
END $$;
