-- ==============================================================================
-- 024_pilot_sample_catalog.sql — trạng thái xuất bản kho dữ liệu mẫu & RLS (CONTRACTS §21.35)
-- - Bổ sung description, published trên rav_pilot_source_asset và rav_pilot_model_asset.
-- - Bảo vệ danh tính model bằng trigger rav_pilot_model_identity_guard (cho phép sửa name/description/published).
-- - Cập nhật RLS p_source_read và p_model_read: nguồn/model SYSTEM ẩn (published = false) chỉ đọc được
--   nếu tài khoản đã có phiên/snapshot/proposal tham chiếu tới tài nguyên đó.
-- ==============================================================================

ALTER TABLE rav_pilot_source_asset
    ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT '';

ALTER TABLE rav_pilot_source_asset
    ADD COLUMN IF NOT EXISTS published BOOLEAN NOT NULL DEFAULT true;

ALTER TABLE rav_pilot_model_asset
    ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT '';

ALTER TABLE rav_pilot_model_asset
    ADD COLUMN IF NOT EXISTS published BOOLEAN NOT NULL DEFAULT true;

CREATE OR REPLACE FUNCTION rav_pilot_model_identity_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'rav_pilot_model_asset là bảng danh mục bất biến: cấm DELETE (CONTRACTS §21.3, §21.35)'
            USING ERRCODE = 'check_violation';
    END IF;
    IF (NEW.core_model_id, NEW.scope, NEW.protected, NEW.raw_key, NEW.sha256, NEW.size_bytes,
        NEW.checkpoint_revision, NEW.task, NEW.class_map, NEW.runtime_manifest, NEW.validation)
       IS DISTINCT FROM
       (OLD.core_model_id, OLD.scope, OLD.protected, OLD.raw_key, OLD.sha256, OLD.size_bytes,
        OLD.checkpoint_revision, OLD.task, OLD.class_map, OLD.runtime_manifest, OLD.validation) THEN
        RAISE EXCEPTION 'danh tính model bất biến (core/scope/protected/raw/sha256/revision/task/validation)'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_rav_pilot_model_asset_immutable ON rav_pilot_model_asset;
DROP TRIGGER IF EXISTS trg_model_identity_guard ON rav_pilot_model_asset;
CREATE TRIGGER trg_model_identity_guard BEFORE UPDATE OR DELETE ON rav_pilot_model_asset
    FOR EACH ROW EXECUTE FUNCTION rav_pilot_model_identity_guard();

GRANT SELECT (description, published) ON rav_pilot_source_asset TO authenticated;
GRANT UPDATE (availability, resource_version, name, description, published) ON rav_pilot_source_asset TO rav_pilot_api;

GRANT SELECT (description, published) ON rav_pilot_model_asset TO authenticated;
GRANT UPDATE (name, description, published) ON rav_pilot_model_asset TO rav_pilot_api;

DROP POLICY IF EXISTS p_source_read ON rav_pilot_source_asset;
CREATE POLICY p_source_read ON rav_pilot_source_asset FOR SELECT TO authenticated
    USING (
        rav_pilot_is_active(rav_pilot_uid()) AND (
            owner_id = rav_pilot_uid()
            OR (
                scope = 'SYSTEM' AND (
                    published = true
                    OR EXISTS (
                        SELECT 1 FROM rav_pilot_session_source ss
                        WHERE ss.source_id = rav_pilot_source_asset.id AND ss.owner_id = rav_pilot_uid()
                    )
                    OR EXISTS (
                        SELECT 1 FROM rav_pilot_snapshot_member sm
                        WHERE sm.source_id = rav_pilot_source_asset.id AND sm.owner_id = rav_pilot_uid()
                    )
                    OR EXISTS (
                        SELECT 1 FROM rav_pilot_session_reference sr
                        JOIN rav_pilot_dataset_version dv ON dv.id = sr.dataset_version_id
                        JOIN rav_pilot_dataset d ON d.id = dv.dataset_id
                        WHERE sr.owner_id = rav_pilot_uid() AND d.source_id = rav_pilot_source_asset.id
                    )
                )
            )
        )
    );

DROP POLICY IF EXISTS p_model_read ON rav_pilot_model_asset;
CREATE POLICY p_model_read ON rav_pilot_model_asset FOR SELECT TO authenticated
    USING (
        rav_pilot_is_active(rav_pilot_uid()) AND (
            published = true
            OR EXISTS (
                SELECT 1 FROM rav_pilot_session_reference sr
                WHERE sr.model_id = rav_pilot_model_asset.id AND sr.owner_id = rav_pilot_uid()
            )
            OR EXISTS (
                SELECT 1 FROM rav_pilot_proposal p
                WHERE p.owner_id = rav_pilot_uid()
                  AND (p.frozen_input -> 'model' ->> 'id' = rav_pilot_model_asset.id::text
                       OR p.frozen_input -> 'model' ->> 'core_model_id' = rav_pilot_model_asset.core_model_id)
            )
            OR EXISTS (
                SELECT 1 FROM rav_pilot_session s
                WHERE s.owner_id = rav_pilot_uid()
                  AND (s.spec -> 'initial_strategy' -> 'model' ->> 'id' = rav_pilot_model_asset.id::text
                       OR s.spec -> 'initial_strategy' -> 'model' ->> 'core_model_id' = rav_pilot_model_asset.core_model_id
                       OR s.spec -> 'reference' ->> 'model_id' = rav_pilot_model_asset.core_model_id)
            )
        )
    );
