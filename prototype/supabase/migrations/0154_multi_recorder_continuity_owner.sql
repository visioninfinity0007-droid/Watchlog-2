-- 0154 - Separate operator primary from immutable legacy continuity ownership.
--
-- STACKED AFTER 0153. REPO ONLY until separately approved.
--
-- Why:
--   Multi-recorder 5.1 originally used recorders.is_primary for two unrelated jobs:
--     1) operator/current preferred primary designation; and
--     2) immutable ownership of pre-multi-recorder spool/health/dedupe continuity.
--   Allowing an operator to promote Recorder B would therefore move Recorder A's
--   historical namespace/state semantics to B. That can duplicate or misattribute
--   evidence.
--
-- Contract:
--   * is_primary MAY move between configured recorders;
--   * continuity_owner is immutable after rollout/adoption;
--   * exactly one recorder per site owns continuity whenever the site has recorders;
--   * legacy event dedupe uses continuity_owner, never is_primary;
--   * Agent recorder sync may update is_primary/is_configured but never continuity_owner.

alter table public.recorders
  add column if not exists continuity_owner boolean not null default false;

-- 0154 is applied before primary reassignment exists in production. The current
-- primary is therefore the only safe deterministic legacy-continuity owner.
with missing as (
  select r.site_id
    from public.recorders r
   group by r.site_id
  having count(*) filter (where r.continuity_owner) = 0
)
update public.recorders r
   set continuity_owner = true,
       updated_at = now()
  from missing m
 where r.site_id = m.site_id
   and r.is_primary;

do $$
begin
  if exists (
    select 1
      from public.recorders r
     group by r.site_id
    having count(*) filter (where r.continuity_owner) <> 1
  ) then
    raise exception '0154 continuity backfill failed: each recorder site needs exactly one continuity owner';
  end if;
end
$$;

create unique index if not exists recorders_one_continuity_owner_per_site_idx
  on public.recorders(site_id)
  where continuity_owner;

alter table public.recorders
  drop constraint if exists recorders_continuity_owner_configured_check;

alter table public.recorders
  add constraint recorders_continuity_owner_configured_check
  check (not continuity_owner or is_configured);

-- Preserve the old event namespace ONLY for the immutable continuity owner.
create or replace function public.wl_recorder_event_dedupe_key(
  p_site_id uuid,
  p_recorder_id uuid,
  p_channel text,
  p_event_id text,
  p_device_ts timestamptz,
  p_event_type text
) returns text
language sql
stable
set search_path = public
as $function$
  select public.wl_dedupe_key(
    p_site_id,
    case
      when exists (
        select 1 from public.recorders r
         where r.id=p_recorder_id
           and r.site_id=p_site_id
           and r.continuity_owner
      ) then p_channel
      else p_recorder_id::text || ':' || coalesce(p_channel,'?')
    end,
    p_event_id,
    p_device_ts,
    p_event_type
  )
$function$;

revoke all on function public.wl_recorder_event_dedupe_key(
  uuid,uuid,text,text,timestamptz,text
) from public,anon,authenticated,service_role;

-- Recorder sync is a desired-state registry sync. A non-empty payload must name
-- exactly one CONFIGURED preferred primary. The function clears old preferred
-- primary flags first, then applies the payload atomically inside the RPC
-- transaction. continuity_owner is never accepted from or modified by the Agent.
create or replace function public.wl_sync_recorders(
  p_agent_id uuid,
  p_agent_key text,
  p_recorders jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
  v_item jsonb;
  v_local_key text;
  v_primary boolean;
  v_configured boolean;
  v_recorder public.recorders;
  v_continuity public.recorders;
  v_out jsonb := '{}'::jsonb;
  v_primary_in_payload integer;
  v_existing integer;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  if public.wl_current_site_agent(v_agent.site_id) is distinct from v_agent.id then
    raise exception 'agent is not current site authority' using errcode='42501';
  end if;

  if p_recorders is null or jsonb_typeof(p_recorders) <> 'array' then
    raise exception 'recorders must be a JSON array' using errcode='22023';
  end if;

  if exists (
    select 1
      from jsonb_array_elements(p_recorders) r
     where btrim(coalesce(r->>'local_key',''))=''
  ) then
    raise exception 'recorder local_key required' using errcode='22023';
  end if;

  if exists (
    select 1
      from jsonb_array_elements(p_recorders) r
     group by btrim(coalesce(r->>'local_key',''))
    having count(*) > 1
  ) then
    raise exception 'duplicate recorder local_key in payload' using errcode='22023';
  end if;

  select count(*) into v_primary_in_payload
    from jsonb_array_elements(p_recorders) r
   where coalesce((r->>'is_primary')::boolean,false);

  if jsonb_array_length(p_recorders)>0 and v_primary_in_payload<>1 then
    raise exception 'recorder registry requires exactly one primary recorder'
      using errcode='22023';
  end if;

  if exists (
    select 1 from jsonb_array_elements(p_recorders) r
     where coalesce((r->>'is_primary')::boolean,false)
       and not coalesce((r->>'is_configured')::boolean,true)
  ) then
    raise exception 'primary recorder must be configured'
      using errcode='22023';
  end if;

  select count(*) into v_existing
    from public.recorders
   where tenant_id=v_agent.tenant_id
     and site_id=v_agent.site_id;

  -- The existing migration-created/adopted continuity row is immutable.
  -- 5.1 also keeps it configured: safe retirement needs a future quiesce+drain
  -- workflow so legacy spool/health evidence cannot be stranded.
  select * into v_continuity
    from public.recorders
   where tenant_id=v_agent.tenant_id
     and site_id=v_agent.site_id
     and continuity_owner
   limit 1;

  -- Remove preferred-primary designation before applying the complete desired
  -- primary state. This is safe inside the same RPC transaction.
  if jsonb_array_length(p_recorders)>0 then
    update public.recorders
       set is_primary=false,
           updated_at=now()
     where tenant_id=v_agent.tenant_id
       and site_id=v_agent.site_id
       and is_primary;
  end if;

  for v_item in select value from jsonb_array_elements(p_recorders)
  loop
    v_local_key := btrim(coalesce(v_item->>'local_key',''));
    v_primary := coalesce((v_item->>'is_primary')::boolean,false);
    v_configured := coalesce((v_item->>'is_configured')::boolean,true);

    select * into v_recorder
      from public.recorders
     where tenant_id=v_agent.tenant_id
       and site_id=v_agent.site_id
       and local_key=v_local_key;

    if v_recorder.id is not null
       and v_continuity.id is not null
       and v_recorder.id=v_continuity.id
       and not v_configured
    then
      raise exception 'continuity recorder cannot be disabled in contract v4'
        using errcode='42501';
    end if;

    -- First upgraded sync adopts the migration-created legacy-default row.
    -- Adoption is keyed by immutable continuity ownership, not current primary.
    if v_recorder.id is null
       and v_continuity.id is not null
       and v_continuity.local_key='legacy-default'
       and v_existing=1
       and not v_configured
    then
      raise exception 'continuity recorder cannot be disabled in contract v4'
        using errcode='42501';
    end if;

    if v_recorder.id is null
       and v_continuity.id is not null
       and v_continuity.local_key='legacy-default'
       and v_existing=1
       and not exists (
         select 1 from public.recorders
          where site_id=v_agent.site_id
            and local_key=v_local_key
       )
    then
      update public.recorders
         set local_key=v_local_key,
             display_name=coalesce(nullif(btrim(v_item->>'display_name'),''),display_name),
             vendor=coalesce(nullif(btrim(v_item->>'vendor'),''),vendor),
             model=coalesce(nullif(btrim(v_item->>'model'),''),model),
             driver=coalesce(nullif(btrim(v_item->>'driver'),''),driver),
             firmware=coalesce(nullif(btrim(v_item->>'firmware'),''),firmware),
             identity_fingerprint=coalesce(
               nullif(btrim(v_item->>'identity_fingerprint'),''),
               identity_fingerprint
             ),
             is_primary=v_primary,
             is_configured=v_configured,
             updated_at=now()
       where id=v_continuity.id
       returning * into v_recorder;
      v_continuity := v_recorder;
    end if;

    if v_recorder.id is null then
      insert into public.recorders(
        tenant_id,site_id,local_key,display_name,
        vendor,model,driver,firmware,identity_fingerprint,
        is_primary,is_configured,continuity_owner
      ) values (
        v_agent.tenant_id,v_agent.site_id,v_local_key,
        coalesce(nullif(btrim(v_item->>'display_name'),''),'Recorder'),
        nullif(btrim(v_item->>'vendor'),''),
        nullif(btrim(v_item->>'model'),''),
        nullif(btrim(v_item->>'driver'),''),
        nullif(btrim(v_item->>'firmware'),''),
        nullif(btrim(v_item->>'identity_fingerprint'),''),
        v_primary,v_configured,false
      )
      returning * into v_recorder;
    else
      update public.recorders
         set display_name=coalesce(nullif(btrim(v_item->>'display_name'),''),display_name),
             vendor=coalesce(nullif(btrim(v_item->>'vendor'),''),vendor),
             model=coalesce(nullif(btrim(v_item->>'model'),''),model),
             driver=coalesce(nullif(btrim(v_item->>'driver'),''),driver),
             firmware=coalesce(nullif(btrim(v_item->>'firmware'),''),firmware),
             identity_fingerprint=coalesce(
               nullif(btrim(v_item->>'identity_fingerprint'),''),
               identity_fingerprint
             ),
             is_primary=v_primary,
             is_configured=v_configured,
             updated_at=now()
       where id=v_recorder.id
       returning * into v_recorder;
    end if;

    v_out := v_out || jsonb_build_object(v_local_key,v_recorder.id);
  end loop;

  if jsonb_array_length(p_recorders)>0 and (
    select count(*)
      from public.recorders
     where tenant_id=v_agent.tenant_id
       and site_id=v_agent.site_id
       and is_primary
       and is_configured
  ) <> 1 then
    raise exception 'site must have exactly one configured primary recorder'
      using errcode='23514';
  end if;

  if exists (
    select 1
      from public.recorders r
     where r.tenant_id=v_agent.tenant_id
       and r.site_id=v_agent.site_id
     group by r.site_id
    having count(*) filter (where r.continuity_owner) <> 1
  ) then
    raise exception 'recorder continuity owner invariant violated'
      using errcode='23514';
  end if;

  return v_out;
end
$function$;

revoke all on function public.wl_sync_recorders(uuid,text,jsonb)
  from public,anon,authenticated,service_role;
grant execute on function public.wl_sync_recorders(uuid,text,jsonb)
  to anon;


-- Contract v4: runtime fan-out must not activate until immutable continuity
-- ownership exists on both DB and Agent.
create or replace function public.wl_multi_recorder_agent_contract(
  p_agent_id uuid,
  p_agent_key text
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  if public.wl_current_site_agent(v_agent.site_id) is distinct from v_agent.id then
    raise exception 'agent is not current site authority' using errcode='42501';
  end if;

  return jsonb_build_object(
    'ok',true,
    'version',4,
    'configured_recorders',(
      select count(*)
        from public.recorders r
       where r.tenant_id=v_agent.tenant_id
         and r.site_id=v_agent.site_id
         and r.is_configured
    ),
    'features',jsonb_build_array(
      'recorders',
      'recorder_cameras',
      'recorder_events',
      'recorder_health',
      'recorder_recovery',
      'recorder_reconciliation',
      'recorder_capabilities',
      'recorder_job_routing',
      'recorder_analytics',
      'recorder_continuity'
    ),
    'server_time',now()
  );
end
$function$;

revoke all on function public.wl_multi_recorder_agent_contract(uuid,text)
  from public,anon,authenticated,service_role;
grant execute on function public.wl_multi_recorder_agent_contract(uuid,text)
  to anon;
