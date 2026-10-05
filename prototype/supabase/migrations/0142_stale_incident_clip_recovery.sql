-- 0142 — Recover incident-footage requests abandoned by a stalled/crashed Agent.
--
-- A clip request moves pending -> processing when an Agent claims it. Before this
-- migration, if that worker blocked or died after the claim, nothing server-side
-- ever finalized the row. The customer could therefore see "retrieving" forever
-- even after the Agent itself recovered.
--
-- The 5.0.27 worker has a <=60 second extraction budget. The server gives it a
-- conservative 180 second lease, then fails the abandoned request and removes any
-- partial chunks. This is evidence-state cleanup only; it never changes recorder
-- configuration and never fabricates a successful clip.

create or replace function public.wl_finalize_stale_incident_clips(
  p_stale_seconds integer default 180
) returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_stale_seconds integer := least(greatest(coalesce(p_stale_seconds, 180), 120), 3600);
  v_count integer := 0;
begin
  with stale as (
    select r.id
      from public.incident_clip_requests r
     where r.status = 'processing'
       and r.started_at is not null
       and r.started_at < now() - make_interval(secs => v_stale_seconds)
     for update skip locked
  ), removed_chunks as (
    delete from public.incident_clip_chunks c
     using stale s
     where c.request_id = s.id
    returning c.request_id
  ), finalized as (
    update public.incident_clip_requests r
       set status = 'failed',
           error_message = 'Footage retrieval did not complete. Please retry.',
           completed_at = now(),
           claimed_by_agent_id = null
      from stale s
     where r.id = s.id
    returning r.id
  )
  select count(*) into v_count from finalized;

  return v_count;
end
$$;

comment on function public.wl_finalize_stale_incident_clips(integer) is
  'WatchLog 0142: fail abandoned processing clip requests after a bounded lease and delete partial bytes';

revoke all on function public.wl_finalize_stale_incident_clips(integer)
  from public, anon, authenticated;
grant execute on function public.wl_finalize_stale_incident_clips(integer)
  to service_role;

-- Independent server-side cleanup. Agent polling cannot be the sole recovery
-- path because the footage worker itself may be the component that stalled.
do $$
begin
  if exists (select 1 from pg_available_extensions where name = 'pg_cron') then
    create extension if not exists pg_cron;
    perform cron.unschedule(jobid)
      from cron.job
     where jobname = 'watchlog-finalize-stale-incident-clips';
    perform cron.schedule(
      'watchlog-finalize-stale-incident-clips',
      '*/2 * * * *',
      'select public.wl_finalize_stale_incident_clips(180)'
    );
  end if;
exception when others then
  raise notice 'stale incident clip scheduling skipped: %', sqlerrm;
end $$;
