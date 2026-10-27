-- §21.39: metadata aliases share immutable objects; source pin prevents backing deletion.
CREATE TABLE rav_pilot_sample_reference (
    sample_source_id UUID PRIMARY KEY REFERENCES rav_pilot_source_asset(id),
    backing_source_id UUID NOT NULL REFERENCES rav_pilot_source_asset(id),
    frame_set_id UUID NOT NULL REFERENCES rav_pilot_frame_set(id),
    dino_id UUID NOT NULL REFERENCES rav_pilot_artifact(id),
    clip_id UUID NOT NULL REFERENCES rav_pilot_artifact(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (sample_source_id <> backing_source_id)
);
CREATE INDEX ON rav_pilot_sample_reference(backing_source_id);
ALTER TABLE rav_pilot_sample_reference ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON rav_pilot_sample_reference FROM PUBLIC, anon, authenticated;
CREATE POLICY p_backend_all ON rav_pilot_sample_reference TO rav_pilot_api, rav_pilot_worker USING (true);
GRANT SELECT ON rav_pilot_sample_reference TO rav_pilot_api, rav_pilot_worker;
CREATE TRIGGER trg_sample_reference_immutable BEFORE UPDATE OR DELETE ON rav_pilot_sample_reference
    FOR EACH ROW EXECUTE FUNCTION rav_pilot_reject_mutation();

-- Alias rows have distinct IDs/authorization sources but reference the same physical key.
ALTER TABLE rav_pilot_frame DROP CONSTRAINT rav_pilot_frame_object_key_key;
CREATE INDEX ON rav_pilot_frame(object_key);
ALTER TABLE rav_pilot_artifact DROP CONSTRAINT rav_pilot_artifact_object_key_key;
CREATE INDEX ON rav_pilot_artifact(object_key);
ALTER TABLE rav_pilot_command_receipt DROP CONSTRAINT rav_pilot_command_receipt_action_check;
ALTER TABLE rav_pilot_command_receipt ADD CONSTRAINT rav_pilot_command_receipt_action_check
    CHECK (action IN ('CREATE_SESSION','CREATE_SNAPSHOT','FORK_TEMPLATE','INTERACTIVE_SAVE','REFERENCE_SAMPLE'));

CREATE FUNCTION rav_pilot_shared_backing_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME = 'rav_pilot_source_asset' THEN
        IF EXISTS (SELECT 1 FROM rav_pilot_sample_reference WHERE backing_source_id=OLD.id)
           AND (NEW.availability <> 'READY' OR NEW.metadata IS DISTINCT FROM OLD.metadata) THEN
            RAISE EXCEPTION 'source referenced by read-only sample' USING ERRCODE='check_violation';
        END IF;
    ELSIF TG_TABLE_NAME = 'rav_pilot_frame' THEN
        IF EXISTS (SELECT 1 FROM rav_pilot_sample_reference WHERE frame_set_id=OLD.frame_set_id) THEN
            RAISE EXCEPTION 'sample frame is immutable' USING ERRCODE='check_violation';
        END IF;
    ELSIF TG_TABLE_NAME = 'rav_pilot_frame_set' THEN
        IF EXISTS (SELECT 1 FROM rav_pilot_sample_reference WHERE frame_set_id=OLD.id) THEN
            RAISE EXCEPTION 'sample frame set is immutable' USING ERRCODE='check_violation';
        END IF;
    ELSE
        IF EXISTS (SELECT 1 FROM rav_pilot_sample_reference WHERE dino_id=OLD.id OR clip_id=OLD.id) THEN
            RAISE EXCEPTION 'sample feature is immutable' USING ERRCODE='check_violation';
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_shared_backing_source BEFORE UPDATE ON rav_pilot_source_asset
    FOR EACH ROW EXECUTE FUNCTION rav_pilot_shared_backing_guard();
CREATE TRIGGER trg_shared_backing_frame BEFORE UPDATE OR DELETE ON rav_pilot_frame
    FOR EACH ROW EXECUTE FUNCTION rav_pilot_shared_backing_guard();
CREATE TRIGGER trg_shared_backing_set BEFORE UPDATE OR DELETE ON rav_pilot_frame_set
    FOR EACH ROW EXECUTE FUNCTION rav_pilot_shared_backing_guard();
CREATE TRIGGER trg_shared_backing_feature BEFORE UPDATE OR DELETE ON rav_pilot_artifact
    FOR EACH ROW EXECUTE FUNCTION rav_pilot_shared_backing_guard();

-- API validates operator, current readiness/input hashes and idempotency. This RPC grants no broad INSERT.
CREATE FUNCTION rav_pilot_reference_sample(p_uid UUID,p_source UUID,p_target UUID,p_set UUID,p_dino UUID,p_clip UUID)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE s rav_pilot_source_asset; f rav_pilot_frame_set; a rav_pilot_artifact;
        target_set UUID := gen_random_uuid(); existing rav_pilot_source_asset;
BEGIN
    SELECT * INTO STRICT s FROM rav_pilot_source_asset WHERE id=p_source FOR UPDATE;
    IF s.owner_id IS DISTINCT FROM p_uid OR s.scope <> 'PRIVATE' OR s.kind <> 'VIDEO' OR s.availability <> 'READY'
       OR NOT EXISTS (SELECT 1 FROM rav_pilot_profile WHERE user_id=p_uid AND active) THEN
        RAISE EXCEPTION 'invalid sample backing' USING ERRCODE='check_violation';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('rav-sample:'||s.core_source_id,0));
    SELECT * INTO existing FROM rav_pilot_source_asset
        WHERE scope='SYSTEM' AND core_source_id=s.core_source_id AND availability<>'DELETED';
    IF FOUND THEN
        IF existing.raw_sha256 <> s.raw_sha256 THEN
            RAISE EXCEPTION 'sample SHA mismatch' USING ERRCODE='check_violation';
        END IF;
        RETURN existing.id;
    END IF;
    SELECT * INTO STRICT f FROM rav_pilot_frame_set WHERE id=p_set AND source_id=p_source
        AND scope='PRIVATE' AND owner_id=p_uid AND state='READY' AND raw_sha256=s.raw_sha256 FOR UPDATE;
    IF f.n_units <> (s.metadata->>'n_units')::int
       OR (SELECT count(*) FROM rav_pilot_frame WHERE frame_set_id=f.id) <> f.object_count THEN
        RAISE EXCEPTION 'incomplete sample frames' USING ERRCODE='check_violation';
    END IF;
    -- Lock exact feature rows before making aliases/pins visible.
    PERFORM 1 FROM rav_pilot_artifact WHERE id IN (p_dino,p_clip) ORDER BY id FOR UPDATE;
    IF (SELECT count(*) FROM rav_pilot_artifact WHERE source_id=p_source AND owner_id=p_uid AND scope='PRIVATE'
        AND kind='FEATURE' AND state='READY' AND ((id=p_dino AND variant='DINO') OR (id=p_clip AND variant='CLIP'))) <> 2 THEN
        RAISE EXCEPTION 'incomplete sample features' USING ERRCODE='check_violation';
    END IF;
    INSERT INTO rav_pilot_source_asset(id,core_source_id,kind,scope,owner_id,protected,name,description,raw_key,
        raw_sha256,size_bytes,metadata,provenance,availability,published)
    VALUES(p_target,s.core_source_id,'VIDEO','SYSTEM',NULL,true,s.name,s.description,s.raw_key,
        s.raw_sha256,s.size_bytes,s.metadata,jsonb_build_object('referenced_from',p_source),'READY',false);
    INSERT INTO rav_pilot_frame_set(id,scope,owner_id,source_id,raw_sha256,sample_spec_hash,encoding_version,
        n_units,object_count,total_bytes,state)
    VALUES(target_set,'SYSTEM',NULL,p_target,f.raw_sha256,f.sample_spec_hash,f.encoding_version,
        f.n_units,f.object_count,f.total_bytes,'READY');
    INSERT INTO rav_pilot_frame(id,frame_set_id,variant,frame_idx,unit_id,object_key,sha256,size_bytes,width,height)
    SELECT gen_random_uuid(),target_set,variant,frame_idx,unit_id,object_key,sha256,size_bytes,width,height
        FROM rav_pilot_frame WHERE frame_set_id=f.id;
    FOR a IN SELECT * FROM rav_pilot_artifact WHERE id IN (p_dino,p_clip) LOOP
        INSERT INTO rav_pilot_artifact(id,scope,owner_id,source_id,kind,variant,input_hash,object_key,sha256,size_bytes,state,manifest)
        VALUES(gen_random_uuid(),'SYSTEM',NULL,p_target,'FEATURE',a.variant,a.input_hash,a.object_key,a.sha256,a.size_bytes,'READY',
            a.manifest || jsonb_build_object('source_id',p_target,'core_source_id',s.core_source_id));
    END LOOP;
    INSERT INTO rav_pilot_sample_reference(sample_source_id,backing_source_id,frame_set_id,dino_id,clip_id)
        VALUES(p_target,p_source,p_set,p_dino,p_clip);
    INSERT INTO rav_pilot_source_pin(id,source_id,consumer_kind,consumer_id,input_role,source_version)
        VALUES(gen_random_uuid(),p_source,'REFERENCE',p_target,'REFERENCE',s.resource_version);
    RETURN p_target;
END $$;
REVOKE ALL ON FUNCTION rav_pilot_reference_sample(UUID,UUID,UUID,UUID,UUID,UUID) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION rav_pilot_reference_sample(UUID,UUID,UUID,UUID,UUID,UUID) TO rav_pilot_api;
