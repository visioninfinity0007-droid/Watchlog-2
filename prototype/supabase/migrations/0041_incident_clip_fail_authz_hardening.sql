-- =====================================================================
-- 0041 - Incident clip authorization + retention hardening
--
-- 0040 establishes the on-demand request/chunk transport. This forward
-- hardening closes three fail-closed requirements before the feature can ship:
-- 1) validate agent claim ownership BEFORE deleting chunks on failure;
-- 2) physically purge expired footage bytes during the normal authenticated
--    site-agent request poll;
-- 3) independently sweep expired footage even when the originating site agent
--    is offline, so retention is not dependent on the site reconnecting.
-- =====================================================================

create or replace function public.wl_agent_fail_clip(
  p_agent_id uuid,p_agent_key text,p_request_id uuid,p_reason text,p_unsupported boolean default false
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent public.agents;
  v_request public.incident_clip_requests;
  v_status text := case when coalesce(p_unsupported,false) then 'unsupported' else 'failed' end;
begin
  v_agent := wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  select * into v_request
    from public.incident_clip_requests
   where id=p_request_id
     and tenant_id=v_agent.tenant_id
     and site_id=v_agent.site_id
     and claimed_by_agent_id=v_agent.id
     and status='processing';
  if v_request.id is null then
    raise exception 'clip request not claimed by this agent' using errcode='42501';
  end if;

  delete from public.incident_clip_chunks where request_id=v_request.id;
  update public.incident_clip_requests
     set status=v_status,
         error_message=left(coalesce(nullif(btrim(p_reason),''),'Recorder could not provide this footage'),300),
         completed_at=now()
   where id=v_request.id;

  return jsonb_build_object('ok',true,'status',v_status);
end
$$;

comment on function public.wl_agent_fail_clip(uuid,text,uuid,text,boolean)
  is 'WatchLog 0041: validate agent claim before deleting incident clip chunks';

revoke all on function public.wl_agent_fail_clip(uuid,text,uuid,text,boolean) from public;
grant execute on function public.wl_agent_fail_clip(uuid,text,uuid,text,boolean) to anon,authenticated;

-- Re-define claim to enforce storage expiry before returning new work. The
-- authenticated agent can purge only its own tenant/site evidence.
create or replace function public.wl_agent_claim_clip_requests(
  p_agent_id uuid,p_agent_key text,p_limit int default 1
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent public.agents;
  v_limit int := least(greatest(coalesce(p_limit,1),1),2);
begin
  v_agent := wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  -- Physically remove expired media bytes before changing request state.
  delete from public.incident_clip_chunks c
   using public.incident_clip_requests r
   where c.request_id=r.id
     and r.tenant_id=v_agent.tenant_id
     and r.site_id=v_agent.site_id
     and r.expires_at<=now();

  update public.incident_clip_requests
     set status='expired'
   where tenant_id=v_agent.tenant_id
     and site_id=v_agent.site_id
     and expires_at<=now()
     and status in ('pending','processing','ready');

  return coalesce((
    with picked as (
      select r.id
        from public.incident_clip_requests r
       where r.site_id=v_agent.site_id
         and r.tenant_id=v_agent.tenant_id
         and r.status='pending'
         and r.expires_at>now()
       order by r.requested_at
       for update skip locked
       limit v_limit
    ), claimed as (
      update public.incident_clip_requests r
         set status='processing',claimed_by_agent_id=v_agent.id,started_at=now(),error_message=null
        from picked p
       where r.id=p.id
      returning r.*
    )
    select jsonb_agg(jsonb_build_object(
      'request_id',r.id,'event_id',r.event_id,'camera_id',r.camera_id,
      'channel',c.channel,'start_at',r.start_at,'end_at',r.end_at
    ) order by r.requested_at)
      from claimed r
      join public.cameras c on c.id=r.camera_id and c.site_id=v_agent.site_id
  ),'[]'::jsonb);
end
$$;

comment on function public.wl_agent_claim_clip_requests(uuid,text,int)
  is 'WatchLog 0041: claim own-site clip requests and purge own-site expired footage bytes';

revoke all on function public.wl_agent_claim_clip_requests(uuid,text,int) from public;
grant execute on function public.wl_agent_claim_clip_requests(uuid,text,int) to anon,authenticated;

-- ---------------------------------------------------------------------
-- Independent physical-retention sweep.
--
-- Agent polling is a useful opportunistic cleanup path, but cannot be the only
-- deletion mechanism: a site may go offline immediately after uploading a
-- ready clip. Supabase Cron is backed by pg_cron; use a named job so reapplying
-- this pre-release migration definition replaces the same job rather than
-- multiplying schedules. The customer-facing clip becomes unreadable at
-- expires_at; this sweep removes the underlying bytea chunks shortly after.
-- ---------------------------------------------------------------------
-- Guarded exactly like 0014/0043/0048: pg_cron may be absent (e.g. a disposable
-- test Postgres) and this migration must still apply cleanly there. The named job
-- is unscheduled first so re-applying this definition replaces it, never multiplies it.
do $$
begin
  if exists (select 1 from pg_available_extensions where name = 'pg_cron') then
    create extension if not exists pg_cron;
    perform cron.unschedule(jobid) from cron.job where jobname = 'watchlog-prune-incident-clips';
    perform cron.schedule('watchlog-prune-incident-clips', '*/15 * * * *',
                          'select public.wl_prune_incident_clips(24)');
  end if;
exception when others then
  raise notice 'pg_cron scheduling skipped: %', sqlerrm;
end $$;
