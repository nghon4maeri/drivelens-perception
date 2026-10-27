-- PROPOSE normal-path publication in one client call; exceptional paths remain in Python.
-- Invoker privileges/RLS apply; no SECURITY DEFINER or public execution.
CREATE FUNCTION public.rav_pilot_publish_propose(l jsonb, o jsonb, u jsonb, prepared jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path = public, pg_temp AS $$
DECLARE
    j rav_pilot_job%ROWTYPE;
    a rav_pilot_job_attempt%ROWTYPE;
    slot rav_pilot_resource_slot%ROWTYPE;
    profile rav_pilot_profile%ROWTYPE;
    sess rav_pilot_session%ROWTYPE;
    h rav_pilot_quota_hold%ROWTYPE;
    part rav_pilot_quota_hold_part%ROWTYPE;
    res text;
    actual bigint;
    left_ms bigint;
    available bigint;
    take_ms bigint;
    rest_ms bigint;
    seq bigint;
    count_n bigint;
    jid uuid := (l->>'job_id')::uuid;
    aid uuid := (l->>'attempt_id')::uuid;
    cleanup boolean := coalesce((o->>'cleanup_done')::boolean, true);
BEGIN
    SELECT * INTO j FROM rav_pilot_job WHERE id=jid;
    IF NOT FOUND OR j.kind <> 'PROPOSE' THEN RETURN jsonb_build_object('error','lease_lost'); END IF;
    -- Serialize replay with publication, preserving the runtime lock order.
    SELECT * INTO profile FROM rav_pilot_profile WHERE user_id=j.owner_id FOR UPDATE;
    SELECT * INTO j FROM rav_pilot_job WHERE id=jid FOR UPDATE;
    SELECT * INTO a FROM rav_pilot_job_attempt WHERE id=aid FOR UPDATE;
    IF a.job_id=jid AND a.fence=(l->>'fence')::bigint AND a.worker_id=(l->>'worker_id')::uuid
       AND a.state='CLOSED' AND j.state='SUCCEEDED' THEN
        RETURN jsonb_build_object('job',to_jsonb(j),'replayed',true);
    END IF;
    IF profile.user_id IS NULL THEN RETURN jsonb_build_object('error','unauthorized'); END IF;
    IF NOT profile.active THEN RETURN jsonb_build_object('error','account_disabled'); END IF;
    SELECT * INTO slot FROM rav_pilot_resource_slot WHERE slot_key=l->>'slot_key' FOR UPDATE;
    IF a.id IS NULL OR a.job_id<>jid OR a.fence<>(l->>'fence')::bigint
       OR a.worker_id<>(l->>'worker_id')::uuid OR a.state NOT IN ('CLAIMED','WORKING')
       OR a.lease_until<=now() OR slot.slot_key IS NULL OR slot.attempt_id IS DISTINCT FROM aid
       OR slot.generation<>(l->>'slot_generation')::bigint THEN
        RETURN jsonb_build_object('error','lease_lost');
    END IF;
    IF j.state='CANCEL_REQUESTED' THEN RETURN jsonb_build_object('error','cancel_requested'); END IF;
    IF j.state<>'RUNNING' THEN RETURN jsonb_build_object('error','lease_lost'); END IF;

    -- Decide fallback before any mutation. Legacy code owns deficit/recovery/storage lifecycles.
    IF EXISTS(SELECT 1 FROM rav_pilot_gpu_hold WHERE attempt_id=aid)
       OR EXISTS(SELECT 1 FROM rav_pilot_storage_hold WHERE job_id=jid AND state='OPEN')
       OR EXISTS(SELECT 1 FROM rav_pilot_worker_object WHERE attempt_id=aid) THEN
        RETURN jsonb_build_object('fallback',true);
    END IF;
    FOREACH res IN ARRAY ARRAY['CPU','GPU'] LOOP
        actual := (u->>(lower(res)||'_ms'))::bigint;
        IF NOT EXISTS(SELECT 1 FROM rav_pilot_usage_receipt WHERE attempt_id=aid AND resource=res) THEN
            SELECT coalesce(sum(reserved_ms-consumed_ms-released_ms),0) INTO available
                FROM rav_pilot_quota_hold WHERE job_id=jid AND resource=res AND state='OPEN';
            IF actual>available THEN RETURN jsonb_build_object('fallback',true); END IF;
        END IF;
    END LOOP;
    SELECT * INTO sess FROM rav_pilot_session WHERE id=(j.payload->>'session_id')::uuid FOR UPDATE;
    SELECT coalesce(max(d.seq),0) INTO seq FROM rav_pilot_decision d WHERE session_id=sess.id;
    SELECT count(*) INTO count_n FROM rav_pilot_proposal WHERE session_id=sess.id;
    IF sess.id IS NULL OR sess.state_version<>(j.payload->>'state_version')::bigint
       OR seq<>(j.payload->>'decision_seq')::bigint OR count_n<>(j.payload->>'proposal_count')::bigint THEN
        RETURN jsonb_build_object('error','proposal_context_changed');
    END IF;
    IF prepared ? 'error' THEN RETURN jsonb_build_object('error',prepared->>'error'); END IF;
    IF (prepared->>'n')::bigint IS DISTINCT FROM count_n+1
       OR (prepared->'frozen'->>'state_version')::bigint IS DISTINCT FROM sess.state_version
       OR (prepared->'frozen'->>'decision_seq')::bigint IS DISTINCT FROM seq
       OR jsonb_array_length(prepared->'picks')>(j.payload->>'batch_size')::int
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(prepared->'picks') k WHERE NOT EXISTS(
           SELECT 1 FROM jsonb_array_elements(j.payload->'pool') s
           WHERE s->>'core_source_id'=regexp_replace(k->>'unit_id', ':[^:]*$', '')
             AND s->>'source_id'=k->>'source_id')) THEN
        RETURN jsonb_build_object('error','result_invalid');
    END IF;

    FOREACH res IN ARRAY ARRAY['CPU','GPU'] LOOP
        actual := (u->>(lower(res)||'_ms'))::bigint;
        IF NOT EXISTS(SELECT 1 FROM rav_pilot_usage_receipt WHERE attempt_id=aid AND resource=res) THEN
            left_ms := actual;
            FOR h IN SELECT * FROM rav_pilot_quota_hold WHERE job_id=jid AND resource=res AND state='OPEN'
                     ORDER BY generation FOR UPDATE LOOP
                FOR part IN SELECT * FROM rav_pilot_quota_hold_part WHERE hold_id=h.id ORDER BY part_n FOR UPDATE LOOP
                    take_ms := least(left_ms,part.reserved_ms-part.consumed_ms-part.released_ms);
                    IF take_ms>0 THEN
                        UPDATE rav_pilot_quota_hold_part SET consumed_ms=consumed_ms+take_ms
                            WHERE hold_id=h.id AND part_n=part.part_n;
                        UPDATE rav_pilot_quota_hold SET consumed_ms=consumed_ms+take_ms WHERE id=h.id;
                        IF part.window_id IS NOT NULL THEN
                            UPDATE rav_pilot_quota_window SET held_ms=held_ms-take_ms,consumed_ms=consumed_ms+take_ms WHERE id=part.window_id;
                        ELSE
                            UPDATE rav_pilot_quota_bonus SET held_ms=held_ms-take_ms,consumed_ms=consumed_ms+take_ms WHERE id=part.bonus_id;
                        END IF;
                        left_ms := left_ms-take_ms;
                    END IF;
                END LOOP;
            END LOOP;
            IF left_ms<>0 THEN RAISE EXCEPTION 'quota hold parts inconsistent' USING ERRCODE='check_violation'; END IF;
            INSERT INTO rav_pilot_usage_receipt(id,job_id,attempt_id,resource,settlement_key,actual_ms,evidence)
                VALUES(gen_random_uuid(),jid,aid,res,aid::text||':'||res,actual,
                       jsonb_build_object('hash',u->>'evidence_hash')||coalesce(u->'evidence','{}'::jsonb));
        END IF;
    END LOOP;
    INSERT INTO rav_pilot_proposal(id,session_id,owner_id,n,frozen_input,input_hash,picks,evidence,fields_used,state_version,job_id)
        VALUES(gen_random_uuid(),sess.id,j.owner_id,(prepared->>'n')::bigint,prepared->'frozen',prepared->>'hash',
               prepared->'picks',prepared->'evidence',prepared->'fields_used',sess.state_version,jid);
    FOR h IN SELECT * FROM rav_pilot_quota_hold WHERE job_id=jid AND state='OPEN' ORDER BY resource,generation FOR UPDATE LOOP
        FOR part IN SELECT * FROM rav_pilot_quota_hold_part WHERE hold_id=h.id ORDER BY part_n FOR UPDATE LOOP
            rest_ms := part.reserved_ms-part.consumed_ms-part.released_ms;
            IF rest_ms>0 THEN
                UPDATE rav_pilot_quota_hold_part SET released_ms=released_ms+rest_ms WHERE hold_id=h.id AND part_n=part.part_n;
                IF part.window_id IS NOT NULL THEN
                    UPDATE rav_pilot_quota_window SET held_ms=held_ms-rest_ms WHERE id=part.window_id;
                ELSE
                    UPDATE rav_pilot_quota_bonus SET held_ms=held_ms-rest_ms WHERE id=part.bonus_id;
                END IF;
            END IF;
        END LOOP;
        UPDATE rav_pilot_quota_hold SET released_ms=reserved_ms-consumed_ms,state='FINAL' WHERE id=h.id;
    END LOOP;
    UPDATE rav_pilot_source_pin SET released_at=now() WHERE consumer_kind='JOB' AND consumer_id=jid AND released_at IS NULL;
    UPDATE rav_pilot_job SET state='SUCCEEDED',state_version=state_version+1,phase='published',done=total,result_ref=NULL,updated_at=now()
        WHERE id=jid RETURNING * INTO j;
    UPDATE rav_pilot_job_attempt SET usage=u,state='CLOSED',finished_at=now(),cleanup_done=cleanup,usage_state='FINAL',updated_at=now() WHERE id=aid;
    IF cleanup THEN
        UPDATE rav_pilot_resource_slot SET state='FREE',attempt_id=NULL,lease_until=NULL WHERE slot_key=slot.slot_key;
    ELSE
        UPDATE rav_pilot_resource_slot SET state='QUARANTINED' WHERE slot_key=slot.slot_key;
    END IF;
    RETURN jsonb_build_object('job',to_jsonb(j),'replayed',false);
END $$;
REVOKE ALL ON FUNCTION public.rav_pilot_publish_propose(jsonb,jsonb,jsonb,jsonb) FROM PUBLIC, anon, authenticated, rav_pilot_api;
GRANT EXECUTE ON FUNCTION public.rav_pilot_publish_propose(jsonb,jsonb,jsonb,jsonb) TO rav_pilot_worker;
