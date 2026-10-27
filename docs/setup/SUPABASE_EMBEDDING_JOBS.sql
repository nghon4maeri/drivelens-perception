-- LEGACY C1 / local v1 only (CONTRACTS sections 19-20, RAV_JOB_BACKEND=supabase).
-- NOT a pilot v2 migration. Pilot operators use src/rav/store/migrations/
-- and docs/setup/PILOT_RUNTIME_RUNBOOK.md; do not run this for pilot setup.
-- C1: run once in Supabase SQL Editor. Backend secret only; no public table access.
create table if not exists public.rav_embedding_jobs (
  id text primary key,
  input_key text not null,
  video_id text not null,
  state text not null check (state in ('queued','running','done','error')),
  payload jsonb not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  lease_owner uuid,
  lease_until timestamptz
);
create unique index if not exists rav_embedding_jobs_active_input
  on public.rav_embedding_jobs(input_key) where state in ('queued','running');
alter table public.rav_embedding_jobs enable row level security;
revoke all on public.rav_embedding_jobs from anon, authenticated;
grant select, insert, update on public.rav_embedding_jobs to service_role;

create or replace function public.rav_claim_embedding_job(p_id text, p_owner uuid, p_seconds integer)
returns setof public.rav_embedding_jobs language sql security invoker set search_path = '' as $$
  update public.rav_embedding_jobs set lease_owner=p_owner,
    lease_until=now()+make_interval(secs=>least(greatest(p_seconds,30),600))
  where id=p_id and state in ('queued','running')
    and (lease_until is null or lease_until<now() or lease_owner=p_owner)
  returning *;
$$;
create or replace function public.rav_update_embedding_job(p_id text, p_owner uuid, p_state text, p_payload jsonb)
returns setof public.rav_embedding_jobs language sql security invoker set search_path = '' as $$
  update public.rav_embedding_jobs set state=p_state, payload=p_payload, updated_at=now(),
    lease_until=case when p_state in ('done','error') then null else now()+interval '120 seconds' end
  where id=p_id and lease_owner=p_owner and lease_until>now() returning *;
$$;
revoke all on function public.rav_claim_embedding_job(text,uuid,integer) from public, anon, authenticated;
revoke all on function public.rav_update_embedding_job(text,uuid,text,jsonb) from public, anon, authenticated;
grant execute on function public.rav_claim_embedding_job(text,uuid,integer) to service_role;
grant execute on function public.rav_update_embedding_job(text,uuid,text,jsonb) to service_role;
notify pgrst, 'reload schema';
