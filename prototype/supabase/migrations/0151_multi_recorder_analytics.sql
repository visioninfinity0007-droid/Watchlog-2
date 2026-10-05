-- 0151 - Recorder-aware analytics attribution and contract v3.
--
-- STACKED AFTER 0150. REPO ONLY until separately approved.
--
-- Problem:
--   wl_ingest_analytic_events historically resolved camera identity by
--   site_id + channel. On a multi-recorder site Recorder A ch1 and Recorder B
--   ch1 are both valid, so that lookup is ambiguous and can cross-attribute
--   measurements/rules/incidents.
--
-- This migration:
--   * adds explicit recorder provenance to analytic_events;
--   * keeps the existing RPC signature for deployed Agents;
--   * accepts missing recorder_id only while the site is a true singleton;
--   * resolves camera/rule by recorder + channel;
--   * carries recorder_id into promoted events;
--   * scopes the installer analytics bootstrap (camera name/purpose/analytics)
--     by recorder + channel, failing closed on an ambiguous legacy call;
--   * advances the authenticated multi-recorder Agent contract to v3.

alter table public.analytic_events
  add column if not exists recorder_id uuid;

update public.analytic_events ae
   set recorder_id=c.recorder_id
  from public.cameras c
 where ae.recorder_id is null
   and ae.camera_id=c.id;

do $$
begin
  if exists (
    select 1 from public.analytic_events where recorder_id is null
  ) then
    raise exception '0151 backfill failed: analytic event without deterministic recorder';
  end if;
end
$$;

alter table public.analytic_events
  alter column recorder_id set not null;

alter table public.analytic_events
  drop constraint if exists analytic_events_recorder_lineage_fkey;

alter table public.analytic_events
  add constraint analytic_events_recorder_lineage_fkey
  foreign key (recorder_id,tenant_id,site_id)
  references public.recorders(id,tenant_id,site_id)
  on delete restrict;

create index if not exists analytic_events_recorder_occurred_idx
  on public.analytic_events(recorder_id,occurred_at desc);

-- Compatibility for existing trusted/internal inserts that predate recorder_id
-- (the same posture as the 0146 camera trigger). camera_id is mandatory and
-- every camera belongs to exactly one recorder, so a missing recorder_id is the
-- event's own camera's recorder, never a site+channel guess. A camera outside
-- the row's tenant/site leaves it NULL and the insert fails closed.
create or replace function public.wl_analytic_event_assign_recorder()
returns trigger
language plpgsql
security definer
set search_path = public
as $function$
begin
  select c.recorder_id into new.recorder_id
    from public.cameras c
   where c.id=new.camera_id
     and c.tenant_id=new.tenant_id
     and c.site_id=new.site_id;
  return new;
end
$function$;

revoke all on function public.wl_analytic_event_assign_recorder()
  from public,anon,authenticated,service_role;

drop trigger if exists trg_analytic_event_assign_recorder on public.analytic_events;
create trigger trg_analytic_event_assign_recorder
before insert on public.analytic_events
for each row
when (new.recorder_id is null)
execute function public.wl_analytic_event_assign_recorder();

create or replace function public.wl_ingest_analytic_events(
  p_agent_id uuid,
  p_agent_key text,
  p_events jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
  v_received int;
  v_inserted int;
  v_promoted int := 0;
  v_legacy_recorder_id uuid := null;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  if p_events is null or jsonb_typeof(p_events)<>'array' then
    raise exception 'events must be a JSON array' using errcode='22023';
  end if;

  if exists (
    select 1 from jsonb_array_elements(p_events) e
     where nullif(e->>'recorder_id','') is not null
  ) then
    perform public.wl_assert_current_agent_authority(
      v_agent.id,v_agent.site_id
    );
  end if;

  select count(*) into v_received
    from jsonb_array_elements(p_events);

  -- Backward compatibility: old analytics runtime emits no recorder_id.
  -- That remains safe only while exactly one recorder exists at the site.
  if exists (
    select 1 from jsonb_array_elements(p_events) e
     where nullif(e->>'recorder_id','') is null
  ) then
    v_legacy_recorder_id := public.wl_legacy_recorder_for_agent(v_agent.id);
  end if;

  -- Explicit recorder IDs are structural identity, not best-effort metadata.
  -- Invalid/foreign recorder identity fails closed rather than silently skipping.
  if exists (
    select 1 from jsonb_array_elements(p_events) e
     where nullif(e->>'recorder_id','') is not null
       and (
         public.wl_try_uuid(e->>'recorder_id') is null
         or not exists (
           select 1 from public.recorders r
            where r.id=public.wl_try_uuid(e->>'recorder_id')
              and r.tenant_id=v_agent.tenant_id
              and r.site_id=v_agent.site_id
              and r.is_configured
         )
       )
  ) then
    raise exception 'analytic event recorder not configured for this agent site'
      using errcode='42501';
  end if;

  with incoming as (
    select
      e,
      public.wl_try_uuid(e->>'rule_id') as rule_id,
      coalesce(
        public.wl_try_uuid(e->>'recorder_id'),
        v_legacy_recorder_id
      ) as recorder_id,
      e->>'channel' as channel,
      coalesce(nullif(e->>'event_type',''),'measurement') as event_type,
      nullif(e->>'object_class','') as object_class,
      nullif(e->>'track_key','') as track_key,
      nullif(e->>'direction','') as direction,
      (e->>'occurred_at')::timestamptz as occurred_at,
      nullif(e->>'duration_seconds','')::numeric as duration_seconds,
      coalesce(nullif(e->>'dedupe_key',''),md5(e::text)) as dedupe_key,
      coalesce(e->'metadata','{}'::jsonb) as metadata
    from jsonb_array_elements(p_events) e
    where e->>'occurred_at' is not null
      and e->>'channel' is not null
  ),
  valid as (
    select
      i.*,
      c.id as camera_id,
      r.analytic_key,
      r.promote_incident,
      r.severity,
      r.name as rule_name
    from incoming i
    join public.cameras c
      on c.site_id=v_agent.site_id
     and c.tenant_id=v_agent.tenant_id
     and c.recorder_id=i.recorder_id
     and c.channel=i.channel
    join public.monitoring_rules r
      on r.id=i.rule_id
     and r.camera_id=c.id
     and r.site_id=v_agent.site_id
     and r.tenant_id=v_agent.tenant_id
     and r.enabled
     and r.rule_type<>'health'
    where i.object_class is null
       or i.object_class in ('person','car','motorcycle')
  ),
  ins as (
    insert into public.analytic_events(
      tenant_id,site_id,recorder_id,camera_id,agent_id,
      monitoring_rule_id,analytic_key,event_type,
      object_class,track_key,direction,occurred_at,
      duration_seconds,dedupe_key,metadata_json
    )
    select
      v_agent.tenant_id,v_agent.site_id,v.recorder_id,v.camera_id,v_agent.id,
      v.rule_id,v.analytic_key,v.event_type,
      v.object_class,v.track_key,v.direction,v.occurred_at,
      v.duration_seconds,v.dedupe_key,v.metadata
    from valid v
    on conflict(tenant_id,dedupe_key) do nothing
    returning id
  )
  select count(*) into v_inserted from ins;

  with candidates as (
    select ae.*,r.name as rule_name,r.severity
      from public.analytic_events ae
      join public.monitoring_rules r on r.id=ae.monitoring_rule_id
     where ae.agent_id=v_agent.id
       and ae.received_at>now()-interval '2 minutes'
       and r.promote_incident
  ),
  put as (
    insert into public.events(
      tenant_id,site_id,recorder_id,camera_id,agent_id,
      event_type,device_ts,agent_ts,dedupe_key,payload
    )
    select
      v_agent.tenant_id,v_agent.site_id,c.recorder_id,c.camera_id,v_agent.id,
      'analytic_'||c.event_type,c.occurred_at,now(),
      'analytics:'||c.dedupe_key,
      jsonb_build_object(
        'analytic_event_id',c.id,
        'analytic_key',c.analytic_key,
        'rule_id',c.monitoring_rule_id,
        'rule',c.rule_name,
        'severity',c.severity,
        'object_class',c.object_class,
        'direction',c.direction,
        'duration_seconds',c.duration_seconds,
        'metadata',c.metadata_json
      )
    from candidates c
    on conflict(tenant_id,dedupe_key) do nothing
    returning 1
  )
  select count(*) into v_promoted from put;

  update public.agents set last_seen_at=now() where id=v_agent.id;

  return jsonb_build_object(
    'received',v_received,
    'inserted',v_inserted,
    'skipped',v_received-v_inserted,
    'promoted_incidents',v_promoted,
    'server_time',now()
  );
end
$function$;

-- Preserve the existing Agent RPC ACL.
revoke all on function public.wl_ingest_analytic_events(uuid,text,jsonb)
  from public,anon,authenticated,service_role;
grant execute on function public.wl_ingest_analytic_events(uuid,text,jsonb)
  to anon,authenticated;

-- The installer analytics bootstrap (0025) matched cameras by site+channel.
-- After 0146 a channel number is only unique per recorder, so one profile item
-- renamed and re-purposed every recorder's camera with that channel. A profile
-- item now names its camera by recorder + channel: an explicit recorder_id must
-- be a configured recorder of the Agent's own site and requires current Agent
-- authority (as wl_ingest_analytic_events); an item without one is accepted only
-- while the site is a true singleton (wl_legacy_recorder_for_agent fails closed
-- on a multi-recorder site).
create or replace function public.wl_agent_bootstrap_analytics(
  p_agent_id uuid,
  p_agent_key text,
  p_site_type text,
  p_camera_profiles jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
  v_changed int := 0;
  v_profile jsonb;
  v_purpose text;
  v_version bigint;
  v_profiles jsonb := coalesce(p_camera_profiles,'[]'::jsonb);
  v_legacy_recorder_id uuid := null;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  if jsonb_typeof(v_profiles)<>'array' then
    raise exception 'camera profiles must be a JSON array' using errcode='22023';
  end if;

  if exists (
    select 1 from jsonb_array_elements(v_profiles) e
     where nullif(e->>'recorder_id','') is not null
  ) then
    perform public.wl_assert_current_agent_authority(
      v_agent.id,v_agent.site_id
    );
  end if;

  if exists (
    select 1 from jsonb_array_elements(v_profiles) e
     where nullif(e->>'recorder_id','') is null
  ) then
    v_legacy_recorder_id := public.wl_legacy_recorder_for_agent(v_agent.id);
  end if;

  if exists (
    select 1 from jsonb_array_elements(v_profiles) e
     where nullif(e->>'recorder_id','') is not null
       and (
         public.wl_try_uuid(e->>'recorder_id') is null
         or not exists (
           select 1 from public.recorders r
            where r.id=public.wl_try_uuid(e->>'recorder_id')
              and r.tenant_id=v_agent.tenant_id
              and r.site_id=v_agent.site_id
              and r.is_configured
         )
       )
  ) then
    raise exception 'analytics profile recorder not configured for this agent site'
      using errcode='42501';
  end if;

  if public.wl_analytics_valid_site_type(coalesce(p_site_type,'custom')) then
    update public.sites set site_type=coalesce(p_site_type,'custom') where id=v_agent.site_id;
  end if;

  for v_profile in select * from jsonb_array_elements(v_profiles)
  loop
    v_purpose := coalesce(nullif(v_profile->>'purpose',''),'custom');
    if not public.wl_analytics_valid_purpose(v_purpose) then
      v_purpose := 'custom';
    end if;
    update public.cameras
       set name=coalesce(nullif(trim(v_profile->>'name'),''),name),
           purpose=v_purpose,
           analytics_enabled=coalesce((v_profile->>'analytics_enabled')::boolean,true)
     where tenant_id=v_agent.tenant_id
       and site_id=v_agent.site_id
       and recorder_id=coalesce(
             public.wl_try_uuid(v_profile->>'recorder_id'),
             v_legacy_recorder_id
           )
       and channel=v_profile->>'channel';
    if found then v_changed := v_changed + 1; end if;
  end loop;

  v_version := public.wl_analytics_bump_site(v_agent.site_id);
  return jsonb_build_object('ok',true,'updated_cameras',v_changed,'version',v_version);
end
$function$;

-- Preserve the 0025 Agent RPC ACL.
revoke all on function public.wl_agent_bootstrap_analytics(uuid,text,text,jsonb)
  from public;
grant execute on function public.wl_agent_bootstrap_analytics(uuid,text,text,jsonb)
  to anon,authenticated;

-- Contract v3 is the first backend contract safe for actual worker fan-out:
-- event, health/recovery, capability, camera-job and analytic attribution are
-- all recorder-scoped.
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
    'version',3,
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
      'recorder_analytics'
    ),
    'server_time',now()
  );
end
$function$;

revoke all on function public.wl_multi_recorder_agent_contract(uuid,text)
  from public,anon,authenticated,service_role;
grant execute on function public.wl_multi_recorder_agent_contract(uuid,text)
  to anon;
