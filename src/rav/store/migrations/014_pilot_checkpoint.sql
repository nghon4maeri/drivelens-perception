-- Interactive checkpoint: one API call, no CPU quota/job/slot. Service proof never comes from browser.
CREATE FUNCTION public.rav_pilot_checkpoint(uid uuid,sid uuid,body jsonb,proof jsonb,ikey text,ihash text,actor_text text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=public,pg_temp AS $$
DECLARE
  sess rav_pilot_session%ROWTYPE;
  src rav_pilot_source_asset%ROWTYPE;
  old_receipt rav_pilot_command_receipt%ROWTYPE;
  rec jsonb; frozen jsonb; result jsonb; saved jsonb := NULL;
  source_count int := 0; n int; pid uuid; seq bigint; active_user boolean;
BEGIN
  SELECT p.active INTO active_user FROM rav_pilot_profile p WHERE p.user_id=uid FOR UPDATE;
  IF NOT FOUND OR NOT active_user THEN RETURN jsonb_build_object('error','account_disabled'); END IF;
  SELECT * INTO old_receipt FROM rav_pilot_command_receipt r WHERE r.owner_id=uid AND r.action='INTERACTIVE_SAVE' AND r.idempotency_key=ikey;
  IF FOUND THEN
    IF old_receipt.input_hash<>ihash THEN RETURN jsonb_build_object('error','idempotency_conflict'); END IF;
    RETURN old_receipt.safe_output;
  END IF;
  IF proof IS NULL THEN RETURN jsonb_build_object('error','batch_expired'); END IF;
  IF jsonb_typeof(proof->'unit_ids') IS DISTINCT FROM 'array' THEN RETURN jsonb_build_object('error','invalid_state'); END IF;
  IF proof->'basis'->>'owner_id'<>uid::text OR proof->'basis'->>'session_id'<>sid::text THEN
    RETURN jsonb_build_object('error','not_found');
  END IF;
  FOR src IN SELECT a.* FROM rav_pilot_session_source ss JOIN rav_pilot_source_asset a ON a.id=ss.source_id
             WHERE ss.session_id=sid ORDER BY a.id FOR UPDATE OF a
  LOOP
    source_count := source_count+1;
    IF src.availability<>'READY' OR (src.scope<>'SYSTEM' AND src.owner_id<>uid) OR NOT EXISTS(
       SELECT 1 FROM jsonb_array_elements(proof->'basis'->'pool') p
       WHERE p->>'source_id'=src.id::text AND (p->>'resource_version')::bigint=src.resource_version) THEN
      RETURN jsonb_build_object('error','source_unavailable');
    END IF;
  END LOOP;
  SELECT * INTO sess FROM rav_pilot_session s WHERE s.id=sid AND s.owner_id=uid FOR UPDATE;
  IF NOT FOUND THEN RETURN jsonb_build_object('error','not_found'); END IF;
  IF source_count<>jsonb_array_length(proof->'basis'->'pool') THEN RETURN jsonb_build_object('error','source_unavailable'); END IF;
  IF sess.state_version<>(body->>'expected_state_version')::bigint OR sess.state_version<>(proof->'basis'->>'state_version')::bigint THEN
    RETURN jsonb_build_object('error','state_version_conflict');
  END IF;
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(proof->'basis'->'pool') p,
       jsonb_each(p->'features') f WHERE NOT EXISTS(SELECT 1 FROM rav_pilot_artifact a
       WHERE a.id=(f.value->>'artifact_id')::uuid AND a.state='READY' AND a.sha256=f.value->>'sha256')) THEN
    RETURN jsonb_build_object('error','feature_not_ready');
  END IF;
  IF jsonb_array_length(body->'kept_ids')>sess.target_k OR EXISTS(
      SELECT 1 FROM jsonb_array_elements_text(body->'kept_ids') k WHERE body->'dropped_ids' ? k.value) OR EXISTS(
      SELECT 1 FROM jsonb_array_elements_text((body->'kept_ids') || (body->'dropped_ids')) i WHERE NOT (proof->'unit_ids' ? i.value)) THEN
    RETURN jsonb_build_object('error','invalid_state');
  END IF;
  WITH current_state AS (
    SELECT DISTINCT ON (d.unit_id) d.unit_id,d.action FROM rav_pilot_decision d WHERE d.session_id=sid ORDER BY d.unit_id,d.seq DESC
  ), wanted AS (
    SELECT k.value AS unit_id,'keep'::text AS action FROM jsonb_array_elements_text(body->'kept_ids') k
    UNION SELECT d.value,'drop' FROM jsonb_array_elements_text(body->'dropped_ids') d
  ), delta AS (
    SELECT coalesce(w.unit_id,c.unit_id) AS unit_id,coalesce(w.action,'clear') AS action FROM wanted w
    FULL JOIN (SELECT * FROM current_state WHERE action<>'clear') c USING(unit_id)
    WHERE coalesce(w.action,'clear') IS DISTINCT FROM coalesce(c.action,'clear')
  )
  INSERT INTO rav_pilot_decision(session_id,owner_id,unit_id,action,actor,goal)
    SELECT sid,uid,d.unit_id,d.action,actor_text,sess.goal FROM delta d ORDER BY d.unit_id;
  SELECT coalesce(max(d.seq),0) INTO seq FROM rav_pilot_decision d WHERE d.session_id=sid;
  rec := proof->'proposal';
  IF rec IS NOT NULL AND rec<>'null'::jsonb THEN
    SELECT coalesce(max(p.n),0)+1 INTO n FROM rav_pilot_proposal p WHERE p.session_id=sid;
    pid := gen_random_uuid();
    frozen := (rec->'input') || jsonb_build_object('state_version',sess.state_version,'decision_seq',seq);
    INSERT INTO rav_pilot_proposal(id,session_id,owner_id,n,frozen_input,input_hash,picks,evidence,fields_used,state_version)
      VALUES(pid,sid,uid,n,frozen,ihash,rec->'picks',jsonb_build_object('relaxed',rec->'relaxed','explain',rec->'explain',
             'scores',rec->'scores','interactive_basis',proof->'basis'),rec->'input'->'fields_used',sess.state_version);
    saved := rec || jsonb_build_object('id',pid,'n',n,'round',n,'job_id',NULL);
  END IF;
  UPDATE rav_pilot_session SET state_version=state_version+1 WHERE id=sid RETURNING * INTO sess;
  result := jsonb_build_object('state',jsonb_build_object('session_id',sid,'state_version',sess.state_version,'decision_seq',seq,
                               'kept_ids',body->'kept_ids','dropped_ids',body->'dropped_ids'),'proposal',saved);
  INSERT INTO rav_pilot_command_receipt(id,owner_id,action,idempotency_key,input_hash,safe_output)
    VALUES(gen_random_uuid(),uid,'INTERACTIVE_SAVE',ikey,ihash,result);
  RETURN result;
END $$;
REVOKE ALL ON FUNCTION public.rav_pilot_checkpoint(uuid,uuid,jsonb,jsonb,text,text,text) FROM PUBLIC,anon,authenticated,rav_pilot_worker;
GRANT EXECUTE ON FUNCTION public.rav_pilot_checkpoint(uuid,uuid,jsonb,jsonb,text,text,text) TO rav_pilot_api;
