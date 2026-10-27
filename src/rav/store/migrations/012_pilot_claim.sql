-- CPU/MEDIA claim + process identity + frozen policy in one worker call. GPU keeps provider admission.
CREATE FUNCTION public.rav_pilot_claim_local(w jsonb, q text, kinds text[], allowed_slots text[], proc jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=public,pg_temp AS $$
DECLARE
    picked rav_pilot_job%ROWTYPE;
    s rav_pilot_resource_slot%ROWTYPE;
    a rav_pilot_job_attempt%ROWTYPE;
    cfg jsonb;
    last_owner uuid;
    candidate uuid;
    next_fence bigint;
BEGIN
    IF q NOT IN ('CPU','MEDIA') OR cardinality(kinds)=0 OR
       (q='MEDIA' AND NOT kinds <@ ARRAY['RENDER_MEDIA']::text[]) OR
       (q='CPU' AND NOT kinds <@ ARRAY['VALIDATE_UPLOAD','PROPOSE','YOLO_BATCH','METRICS','EXPORT_BUILD','EXPORT_FINALIZE','DELETE_SOURCE','PREPARE_FRAMES']::text[]) THEN
        RAISE EXCEPTION 'invalid queue/kinds' USING ERRCODE='invalid_parameter_value';
    END IF;
    INSERT INTO rav_pilot_dispatch_cursor(queue) VALUES(q) ON CONFLICT DO NOTHING;
    SELECT d.last_owner_id INTO last_owner FROM rav_pilot_dispatch_cursor d WHERE d.queue=q FOR UPDATE;
    FOR candidate IN
        SELECT owners.owner_id FROM (SELECT DISTINCT j.owner_id FROM rav_pilot_job j WHERE (j.state='QUEUED' OR (j.state='WAITING_RESOURCE' AND j.waiting_reason=ANY('{}'::text[])))
        AND j.kind=ANY(kinds) AND j.queue_deadline>now()
        AND (q='MEDIA' OR (SELECT (p.config->>'heavy_active_per_user')::int FROM rav_pilot_quota_policy p WHERE p.version=j.policy_version) >
            (SELECT count(*) FROM rav_pilot_resource_slot rs JOIN rav_pilot_job_attempt aa ON aa.id=rs.attempt_id
             JOIN rav_pilot_job jj ON jj.id=aa.job_id WHERE jj.owner_id=j.owner_id AND rs.kind<>'MEDIA' AND rs.state<>'FREE'))
        AND (allowed_slots IS NULL OR EXISTS(SELECT 1 FROM rav_pilot_quota_policy p WHERE p.version=j.policy_version
             AND coalesce(p.config->'slot_inventory'->q,'[]'::jsonb) ?| allowed_slots))) owners
        ORDER BY CASE WHEN last_owner IS NULL OR owners.owner_id>last_owner THEN 0 ELSE 1 END,owners.owner_id
    LOOP
        PERFORM p.user_id FROM rav_pilot_profile p WHERE p.user_id=candidate FOR UPDATE SKIP LOCKED;
        IF NOT FOUND THEN CONTINUE; END IF;
        SELECT j.* INTO picked FROM rav_pilot_job j WHERE j.owner_id=candidate AND (j.state='QUEUED' OR (j.state='WAITING_RESOURCE' AND j.waiting_reason=ANY('{}'::text[])))
        AND j.kind=ANY(kinds) AND j.queue_deadline>now()
        AND (q='MEDIA' OR (SELECT (p.config->>'heavy_active_per_user')::int FROM rav_pilot_quota_policy p WHERE p.version=j.policy_version) >
            (SELECT count(*) FROM rav_pilot_resource_slot rs JOIN rav_pilot_job_attempt aa ON aa.id=rs.attempt_id
             JOIN rav_pilot_job jj ON jj.id=aa.job_id WHERE jj.owner_id=j.owner_id AND rs.kind<>'MEDIA' AND rs.state<>'FREE'))
        AND (allowed_slots IS NULL OR EXISTS(SELECT 1 FROM rav_pilot_quota_policy p WHERE p.version=j.policy_version
             AND coalesce(p.config->'slot_inventory'->q,'[]'::jsonb) ?| allowed_slots))
            AND NOT EXISTS(SELECT 1 FROM rav_pilot_job_attempt aa WHERE aa.job_id=j.id AND aa.usage_state='UNKNOWN')
            ORDER BY j.created_at,j.id LIMIT 1 FOR UPDATE SKIP LOCKED;
        IF FOUND THEN EXIT; END IF;
    END LOOP;
    IF picked.id IS NULL THEN RETURN jsonb_build_object('lease',NULL); END IF;
    SELECT p.config INTO cfg FROM rav_pilot_quota_policy p WHERE p.version=picked.policy_version;
    SELECT rs.* INTO s FROM rav_pilot_resource_slot rs WHERE rs.kind=q AND rs.state='FREE'
        AND (allowed_slots IS NULL OR (rs.slot_key=ANY(allowed_slots) AND (cfg->'slot_inventory'->q) ? rs.slot_key))
        ORDER BY rs.slot_key LIMIT 1 FOR UPDATE SKIP LOCKED;
    IF NOT FOUND THEN RETURN jsonb_build_object('lease',NULL); END IF;
    IF EXISTS(SELECT 1 FROM rav_pilot_source_pin p JOIN rav_pilot_source_asset src ON src.id=p.source_id
        WHERE p.consumer_kind='JOB' AND p.consumer_id=picked.id AND p.released_at IS NULL AND src.availability<>'READY') THEN
        RETURN jsonb_build_object('fallback',true);
    END IF;
    SELECT coalesce(max(aa.fence),0)+1 INTO next_fence FROM rav_pilot_job_attempt aa WHERE aa.job_id=picked.id;
    INSERT INTO rav_pilot_job_attempt(id,job_id,attempt_n,fence,worker_id,host_boot_id,state,lease_until,started_at,process_group)
        VALUES(gen_random_uuid(),picked.id,picked.attempt_n+1,next_fence,(w->>'worker_id')::uuid,(w->>'host_boot_id')::uuid,'WORKING',
               now()+(cfg->>'lease_ms')::bigint*interval '1 millisecond',now(),coalesce(proc,'{}'::jsonb)) RETURNING * INTO a;
    UPDATE rav_pilot_job SET state='RUNNING',state_version=state_version+1,attempt_n=a.attempt_n,
        phase='claimed',waiting_reason=NULL,updated_at=now() WHERE id=picked.id;
    UPDATE rav_pilot_resource_slot SET state='HELD',attempt_id=a.id,generation=generation+1,lease_until=a.lease_until
        WHERE slot_key=s.slot_key RETURNING * INTO s;
    UPDATE rav_pilot_dispatch_cursor SET last_owner_id=picked.owner_id,revision=revision+1 WHERE queue=q;
    RETURN jsonb_build_object('lease',jsonb_build_object('job_id',picked.id,'kind',picked.kind,'attempt_id',a.id,'attempt_n',a.attempt_n,
        'fence',a.fence,'worker_id',a.worker_id,'slot_key',s.slot_key,'slot_generation',s.generation,'lease_until',a.lease_until,
        'payload',picked.payload,'policy_config',cfg,'queue_ms',extract(epoch FROM (a.started_at-picked.created_at))*1000,
        'process_recorded',proc IS NOT NULL));
END $$;
REVOKE ALL ON FUNCTION public.rav_pilot_claim_local(jsonb,text,text[],text[],jsonb) FROM PUBLIC,anon,authenticated,rav_pilot_api;
GRANT EXECUTE ON FUNCTION public.rav_pilot_claim_local(jsonb,text,text[],text[],jsonb) TO rav_pilot_worker;
