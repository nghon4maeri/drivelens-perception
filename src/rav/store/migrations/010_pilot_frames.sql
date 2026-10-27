-- Ảnh lưu sẵn trên S3 cho thumbnail/frame (CONTRACTS §21.20, quyết định #119). Additive; 008 để dành export.
-- Frame set chỉ được chèn khi publish đủ bộ (mọi unit × THUMBNAIL/FRAME); object S3 có receipt rav_pilot_worker_object trước đó.
CREATE TABLE IF NOT EXISTS rav_pilot_frame_set (
    id UUID PRIMARY KEY,
    scope TEXT NOT NULL CHECK (scope IN ('PRIVATE', 'SYSTEM')),
    owner_id UUID REFERENCES rav_pilot_profile(user_id) ON DELETE RESTRICT,
    source_id UUID NOT NULL REFERENCES rav_pilot_source_asset(id) ON DELETE RESTRICT,
    raw_sha256 CHAR(64) NOT NULL CHECK (raw_sha256 ~ '^[0-9a-f]{64}$'),
    sample_spec_hash TEXT NOT NULL CHECK (sample_spec_hash ~ '^[0-9a-f]{16}$'),
    encoding_version TEXT NOT NULL CHECK (encoding_version ~ '^[0-9a-f]{16}$'),
    n_units INT NOT NULL CHECK (n_units > 0),
    object_count INT NOT NULL CHECK (object_count = 2 * n_units),
    total_bytes BIGINT NOT NULL CHECK (total_bytes > 0),
    job_id UUID REFERENCES rav_pilot_job(id) ON DELETE RESTRICT,
    attempt_id UUID REFERENCES rav_pilot_job_attempt(id) ON DELETE RESTRICT,
    state TEXT NOT NULL CHECK (state IN ('READY', 'DELETE_PENDING', 'DELETED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_frame_set_scope_owner CHECK ((scope = 'PRIVATE') = (owner_id IS NOT NULL))
);
-- Mỗi nguồn/nội dung/spec/encoding chỉ một bộ READY; bộ thua race không được chèn.
CREATE UNIQUE INDEX IF NOT EXISTS uq_frame_set_ready ON rav_pilot_frame_set(source_id, raw_sha256, sample_spec_hash, encoding_version)
    WHERE state = 'READY';
CREATE INDEX IF NOT EXISTS idx_frame_set_owner ON rav_pilot_frame_set(owner_id, source_id);

CREATE TABLE IF NOT EXISTS rav_pilot_frame (
    id UUID PRIMARY KEY,
    frame_set_id UUID NOT NULL REFERENCES rav_pilot_frame_set(id) ON DELETE RESTRICT,
    variant TEXT NOT NULL CHECK (variant IN ('THUMBNAIL', 'FRAME')),
    frame_idx INT NOT NULL CHECK (frame_idx >= 0),
    unit_id TEXT NOT NULL,
    object_key TEXT NOT NULL UNIQUE,
    sha256 CHAR(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    size_bytes BIGINT NOT NULL CHECK (size_bytes > 0),
    width INT NOT NULL CHECK (width > 0),
    height INT NOT NULL CHECK (height > 0),
    UNIQUE (frame_set_id, variant, frame_idx)
);

-- Job chuẩn bị ảnh cho nguồn cũ (queue CPU).
ALTER TABLE rav_pilot_job DROP CONSTRAINT IF EXISTS rav_pilot_job_kind_check;
ALTER TABLE rav_pilot_job ADD CONSTRAINT rav_pilot_job_kind_check CHECK (kind IN (
    'VALIDATE_UPLOAD', 'RENDER_MEDIA', 'PREPARE_DINO', 'PREPARE_CLIP', 'PROPOSE', 'YOLO_BATCH', 'METRICS',
    'EXPORT_BUILD', 'EXPORT_FINALIZE', 'DELETE_SOURCE', 'PREPARE_FRAMES'));

-- Như artifact (§21.12): chỉ role backend; API tự lọc owner/scope.
ALTER TABLE rav_pilot_frame_set ENABLE ROW LEVEL SECURITY;
ALTER TABLE rav_pilot_frame ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON rav_pilot_frame_set, rav_pilot_frame FROM PUBLIC, anon, authenticated;
DROP POLICY IF EXISTS p_backend_all ON rav_pilot_frame_set;
CREATE POLICY p_backend_all ON rav_pilot_frame_set TO rav_pilot_api, rav_pilot_worker USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS p_backend_all ON rav_pilot_frame;
CREATE POLICY p_backend_all ON rav_pilot_frame TO rav_pilot_api, rav_pilot_worker USING (true) WITH CHECK (true);
GRANT SELECT ON rav_pilot_frame_set, rav_pilot_frame TO rav_pilot_api, rav_pilot_worker;
GRANT INSERT, UPDATE ON rav_pilot_frame_set, rav_pilot_frame TO rav_pilot_worker;
