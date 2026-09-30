-- 0117_site_lifecycle_notifications_camera_identity.sql
-- Fix ONVIF profile duplication at the cloud boundary, add safe site removal,
-- and expose one tenant/user-scoped notification center over existing truth.

alter table public.cameras
  add column if not exists is_canonical boolean not null default true;
alter table public.cameras
  add column if not exists physical_channel text;

comment on column public.cameras.is_canonical is
  'True for the customer-facing physical camera row. False preserves legacy transport/profile rows without exposing them as separate cameras.';
comment on column public.cameras.physical_channel is
  'Customer-facing physical camera number. Legacy ONVIF transport profile numbers may differ.';

update public.cameras
   set is_canonical=coalesce(is_canonical,true),
       physical_channel=case
         when name ~* '^MediaProfile_Channel[0-9]+_(MainStream|SubStream)'
           then substring(name from '(?i)^MediaProfile_Channel([0-9]+)_')
         else coalesce(physical_channel,channel)
       end
 where physical_channel is null
    or name ~* '^MediaProfile_Channel[0-9]+_(MainStream|SubStream)';

-- A legacy ONVIF Agent emits events against the LAST encoding profile seen
-- for a VideoSource token. Pick that same row as the canonical physical
-- camera so current events, rules and snapshots stay aligned until upgrade.
with ranked as (
  select id,
         row_number() over (
           partition by site_id,physical_channel
           order by case when channel ~ '^[0-9]+$' then channel::int else -1 end desc,
                    channel desc
         ) as rn
    from public.cameras
   where name ~* '^MediaProfile_Channel[0-9]+_(MainStream|SubStream)'
     and physical_channel is not null
)
update public.cameras c
   set is_canonical=(r.rn=1),
       is_configured=case when r.rn=1 then c.is_configured else false end,
       analytics_enabled=case when r.rn=1 then c.analytics_enabled else false end,
       name=case
         when r.rn=1 and c.name ~* '^MediaProfile_Channel[0-9]+_(MainStream|SubStream)'
           then 'Camera '||c.physical_channel
         else c.name
       end
  from ranked r
 where c.id=r.id;

-- ---------------------------------------------------------------------
-- Camera sync: a current recorder discovery always refreshes/creates a
-- canonical physical row. Legacy profile rows may remain for historical
-- evidence but are not returned to customer-facing surfaces.
-- ---------------------------------------------------------------------
create or replace function public.wl_sync_cameras(
  p_agent_id uuid,
  p_agent_key text,
  p_cameras jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent agents;
  v_out jsonb := '{}'::jsonb;
  v_row record;
  v_legacy_profiles boolean := false;
begin
  v_agent := wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  select exists(
    select 1
      from jsonb_array_elements(coalesce(p_cameras,'[]'::jsonb)) c
     where coalesce(c->>'name','') ~* '^MediaProfile_Channel[0-9]+_(MainStream|SubStream)'
  ) into v_legacy_profiles;

  if v_legacy_profiles then
    -- Old ONVIF builds send one row per ENCODING PROFILE. Keep the transport
    -- channel numbers because the running Agent emits events using them, but
    -- expose exactly one canonical row per physical VideoSource: the last
    -- profile, which is the same one the old driver's source-token map keeps.
    with parsed as (
      select c,
             c->>'channel' as transport_channel,
             coalesce(
               substring(c->>'name' from '(?i)^MediaProfile_Channel([0-9]+)_'),
               c->>'channel'
             ) as physical_channel,
             row_number() over (
               partition by coalesce(
                 substring(c->>'name' from '(?i)^MediaProfile_Channel([0-9]+)_'),
                 c->>'channel'
               )
               order by case when (c->>'channel') ~ '^[0-9]+$'
                             then (c->>'channel')::int else -1 end desc,
                        c->>'channel' desc
             ) as rn
        from jsonb_array_elements(coalesce(p_cameras,'[]'::jsonb)) c
       where coalesce(c->>'channel','')<>''
    )
    insert into cameras
      (tenant_id,site_id,channel,physical_channel,name,
       is_configured,analytics_enabled,is_canonical)
    select
      v_agent.tenant_id,
      v_agent.site_id,
      transport_channel,
      physical_channel,
      case when rn=1 then 'Camera '||physical_channel
           else coalesce(nullif(c->>'name',''),'Profile '||transport_channel) end,
      coalesce((c->>'is_configured')::boolean,false),
      (rn=1),
      (rn=1)
      from parsed
    on conflict(site_id,channel) do update
      set physical_channel=excluded.physical_channel,
          name=case
            when cameras.is_canonical and cameras.is_configured then cameras.name
            else excluded.name
          end,
          is_canonical=excluded.is_canonical,
          analytics_enabled=case
            when excluded.is_canonical then cameras.analytics_enabled
            else false
          end,
          is_configured=case
            when excluded.is_canonical then cameras.is_configured
            else false
          end;

    -- Return EVERY transport profile to the legacy Agent so its event channel
    -- continues to resolve. Customer reads only expose the canonical row.
    for v_row in
      select channel,id
        from cameras
       where site_id=v_agent.site_id
         and channel in (
           select c->>'channel'
             from jsonb_array_elements(coalesce(p_cameras,'[]'::jsonb)) c
         )
    loop
      v_out:=v_out||jsonb_build_object(v_row.channel,v_row.id);
    end loop;
    return v_out;
  end if;

  -- Fixed Agents report one row per physical source. Reuse the existing
  -- canonical legacy row ID (history/rules/names stay attached), first moving
  -- all transport channels out of the physical namespace, then promoting the
  -- canonical rows onto physical channel numbers.
  update cameras
     set channel='legacy-profile-'||channel
   where site_id=v_agent.site_id
     and physical_channel is not null
     and channel !~ '^legacy-profile-'
     and exists (
       select 1 from cameras x
        where x.id=cameras.id
          and (
            x.name ~* '^Camera [0-9]+$'
            or x.name ~* '^MediaProfile_Channel[0-9]+_(MainStream|SubStream)'
            or exists (
              select 1 from cameras y
               where y.site_id=x.site_id
                 and y.physical_channel=x.physical_channel
                 and y.id<>x.id
            )
          )
     );

  update cameras
     set channel=physical_channel,
         name=case
           when name ~* '^(Legacy )?MediaProfile_Channel[0-9]+_(MainStream|SubStream)'
             then 'Camera '||physical_channel
           else name
         end
   where site_id=v_agent.site_id
     and is_canonical
     and physical_channel is not null
     and channel ~ '^legacy-profile-';

  update cameras
     set is_canonical=false,
         is_configured=false,
         analytics_enabled=false
   where site_id=v_agent.site_id
     and channel ~ '^legacy-profile-';

  insert into cameras
    (tenant_id,site_id,channel,physical_channel,name,is_configured,is_canonical)
  select
    v_agent.tenant_id,v_agent.site_id,
    c->>'channel',c->>'channel',
    coalesce(nullif(c->>'name',''),'Camera '||(c->>'channel')),
    coalesce((c->>'is_configured')::boolean,false),true
  from jsonb_array_elements(coalesce(p_cameras,'[]'::jsonb)) c
  where coalesce(c->>'channel','')<>''
  on conflict(site_id,channel) do update
    set physical_channel=excluded.physical_channel,
        name=case when cameras.is_configured then cameras.name else excluded.name end,
        is_canonical=true;

  for v_row in
    select channel,id
      from cameras
     where site_id=v_agent.site_id and coalesce(is_canonical,true)
  loop
    v_out:=v_out||jsonb_build_object(v_row.channel,v_row.id);
  end loop;
  return v_out;
end $$;
revoke all on function public.wl_sync_cameras(uuid,text,jsonb) from public;
grant execute on function public.wl_sync_cameras(uuid,text,jsonb) to anon,authenticated;

-- ---------------------------------------------------------------------
-- Tenant site list:-- ---------------------------------------------------------------------
-- Tenant site list: camera count is PHYSICAL/canonical scope.
-- ---------------------------------------------------------------------
create or replace function public.wl_sites()
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_my_tenant();
  v_can_manage boolean := coalesce(wl_my_role() in ('owner','admin'), false);
begin
  if v_tenant is null then return '[]'::jsonb; end if;

  return coalesce((
    select jsonb_agg(jsonb_build_object(
             'id', s.id,
             'name', s.name,
             'timezone', s.timezone,
             'agents',  agg.agents,
             'online',  coalesce(agg.online, false),
             'cameras', agg.cameras,
             'events',  agg.events,
             'has_open_code', agg.open_code is not null,
             'open_code', case when v_can_manage then agg.open_code else null end,
             'last_event', agg.last_event,
             'setup_state',
               case
                 when agg.agents = 0 then 'awaiting_agent'
                 when coalesce(agg.online,false) and agg.cameras > 0 and agg.events > 0 then 'ready'
                 when agg.cameras > 0 then 'cameras_discovered'
                 when agg.has_recorder then 'recorder_connected'
                 else 'enrolled'
               end)
             order by s.name)
      from sites s
      cross join lateral (
        select
          (select count(*) from agents a where a.site_id = s.id) as agents,
          (select bool_or(a.last_seen_at > now() - interval '3 min')
             from agents a where a.site_id = s.id) as online,
          (select bool_or(a.device_model is not null or a.device_vendor is not null)
             from agents a where a.site_id = s.id) as has_recorder,
          (select count(*) from cameras c
            where c.site_id = s.id and coalesce(c.is_canonical,true)) as cameras,
          (select count(*) from events e where e.site_id = s.id) as events,
          (select max(e.device_ts) from events e where e.site_id = s.id) as last_event,
          (select ec.code from enrollment_codes ec
            where ec.site_id = s.id
              and ec.tenant_id = v_tenant
              and ec.used_at is null
              and ec.expires_at > now()
            order by ec.created_at desc limit 1) as open_code
      ) agg
     where s.tenant_id = v_tenant), '[]'::jsonb);
end $$;
revoke all on function public.wl_sites() from public, anon;
grant execute on function public.wl_sites() to authenticated;

-- ---------------------------------------------------------------------
-- AI context: never present hidden transport/profile rows as separate cameras.
-- Historical events remain untouched and retain their original provenance.
-- ---------------------------------------------------------------------
create or replace function public.wl_ai_context(p_site_id uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = public, pg_temp
as $$
declare
  v_tenant uuid := wl_assert_my_site(p_site_id);
  v_site sites;
  v_diag jsonb;
  v_ctx jsonb;
  v_onboarding jsonb;
  v_recent jsonb;
  v_camera_rows jsonb;
begin
  select * into v_site from sites where id=p_site_id and tenant_id=v_tenant;
  v_diag := wl_my_site_diagnosis(p_site_id);
  v_ctx := wl_my_site_context(p_site_id);
  v_onboarding := wl_onboarding_status(p_site_id);

  select coalesce(jsonb_agg(to_jsonb(r) order by r.device_ts desc),'[]'::jsonb) into v_recent
    from (
      select e.id as event_id,e.event_type,e.device_ts,e.received_at,
             case
               when c.name ~* '^(Legacy )?MediaProfile_Channel[0-9]+_(MainStream|SubStream)'
                 then 'Camera '||coalesce(c.physical_channel,c.channel)
               else c.name
             end as camera,
             coalesce(c.physical_channel,c.channel) as channel,
             coalesce(e.payload->>'source','live') as source,
             case lower(trim(coalesce(e.payload->>'recovered','false')))
               when 'true' then true when 't' then true when '1' then true when 'yes' then true
               else false
             end as recovered
        from events e
        left join cameras c on c.id=e.camera_id
       where e.site_id=p_site_id
       order by e.device_ts desc
       limit 20
    ) r;

  select coalesce(jsonb_agg(jsonb_build_object(
      'id',c.id,
      'channel',coalesce(c.physical_channel,c.channel),
      'name',case
        when c.name ~* '^(Legacy )?MediaProfile_Channel[0-9]+_(MainStream|SubStream)'
          then 'Camera '||coalesce(c.physical_channel,c.channel)
        else c.name
      end,
      'purpose',c.purpose,
      'monitor',c.is_configured,
      'analytics_enabled',coalesce(c.analytics_enabled,false),
      'health_state',coalesce(h.health_state::text,'unknown'),
      'recording_state',coalesce(h.recording_state::text,'unknown')
    ) order by
      case when coalesce(c.physical_channel,c.channel) ~ '^[0-9]+$'
           then coalesce(c.physical_channel,c.channel)::int else 2147483647 end,
      coalesce(c.physical_channel,c.channel)
    ),'[]'::jsonb) into v_camera_rows
    from cameras c
    left join camera_health h on h.camera_id=c.id
   where c.site_id=p_site_id
     and coalesce(c.is_canonical,true);

  return jsonb_build_object(
    'facts_version','watchlog-ai-context-v3',
    'generated_at',now(),
    'site',jsonb_build_object('id',v_site.id,'name',v_site.name,'timezone',v_site.timezone),
    'business_context',v_ctx,
    'onboarding',v_onboarding,
    'recorder',v_diag->'recorder',
    'connectivity',v_diag->'connectivity',
    'capabilities',v_diag->'capabilities',
    'capability_known',v_diag->'capability_known',
    'cameras',v_camera_rows,
    'faults',v_diag->'faults',
    'coverage',v_diag->'coverage',
    'permissions',v_diag->'tiers',
    'recent_events',v_recent,
    'safety',jsonb_build_object(
      'recorder_credentials_leave_site',false,
      'recorder_writes_require_approval',true,
      'unknown_capability_must_not_be_assumed',true)
  );
end $$;
revoke all on function public.wl_ai_context(uuid) from public, anon;
grant execute on function public.wl_ai_context(uuid) to authenticated, service_role;

-- ---------------------------------------------------------------------
-- Agent analytics config:-- ---------------------------------------------------------------------
-- Agent analytics config: hidden legacy profiles never receive rules/config.
-- ---------------------------------------------------------------------
create or replace function public.wl_agent_analytics_config(
  p_agent_id uuid, p_agent_key text, p_known_version bigint default 0
) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
  v_agent agents;
  v_version bigint;
  v_multi boolean;
  v_config jsonb;
  v_requests jsonb;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then raise exception 'agent not recognised' using errcode='28000'; end if;

  select analytics_config_version,coalesce(multi_agent_enabled,false)
    into v_version,v_multi from sites where id=v_agent.site_id;

  select coalesce(jsonb_agg(jsonb_build_object(
           'request_id',cr.id,'camera_id',cr.camera_id,'channel',c.channel)
           order by cr.requested_at),'[]'::jsonb)
    into v_requests
    from camera_snapshot_requests cr
    join cameras c on c.id=cr.camera_id
   where cr.site_id=v_agent.site_id
     and cr.completed_at is null
     and coalesce(c.is_canonical,true);

  if coalesce(p_known_version,0)=v_version then
    return jsonb_build_object('version',v_version,'changed',false,
      'multi_agent_enabled',v_multi,'snapshot_requests',v_requests);
  end if;

  select jsonb_build_object(
    'site_id',s.id,'site_type',s.site_type,'timezone',s.timezone,
    'version',s.analytics_config_version,
    'multi_agent_enabled',coalesce(s.multi_agent_enabled,false),
    'schedules',coalesce((select jsonb_agg(jsonb_build_object(
        'id',ms.id,'name',ms.name,'timezone',ms.timezone,
        'schedule',ms.schedule_json,'enabled',ms.enabled))
        from monitoring_schedules ms where ms.site_id=s.id and ms.enabled),'[]'::jsonb),
    'cameras',coalesce((select jsonb_agg(jsonb_build_object(
        'id',c.id,'channel',c.channel,'name',c.name,'purpose',c.purpose,
        'analytics_enabled',c.analytics_enabled,
        'rules',coalesce((select jsonb_agg(jsonb_build_object(
            'id',r.id,'analytic_key',r.analytic_key,'name',r.name,
            'rule_type',r.rule_type,'object_classes',r.object_classes,
            'geometry',r.geometry_json,'direction',r.direction_json,
            'schedule_id',r.schedule_id,'dwell_seconds',r.dwell_seconds,
            'sample_seconds',r.sample_seconds,'severity',r.severity,
            'promote_incident',r.promote_incident,
            'occupancy_min',r.occupancy_min,'occupancy_max',r.occupancy_max,
            'confidence_min',r.confidence_min,'cooldown_seconds',r.cooldown_seconds,
            'actions',coalesce(r.actions,'[]'::jsonb),
            'evidence_json',coalesce(r.evidence_json,'{}'::jsonb),
            'sensitive',coalesce(r.sensitive,false),
            'review_required',coalesce(r.review_required,false),
            'rule_version',r.rule_version))
          from monitoring_rules r
          where r.camera_id=c.id and r.enabled and r.rule_type<>'health'),'[]'::jsonb))
        order by case when c.channel ~ '^[0-9]+$' then c.channel::int else 2147483647 end,c.channel)
        from cameras c
        where c.site_id=s.id and c.analytics_enabled and coalesce(c.is_canonical,true)),'[]'::jsonb)
  ) into v_config from sites s where s.id=v_agent.site_id;

  return jsonb_build_object('version',v_version,'changed',true,
    'multi_agent_enabled',v_multi,'config',v_config,'snapshot_requests',v_requests);
end $$;

-- ---------------------------------------------------------------------
-- Site removal: explicit owner/admin operation. Deleting the site cascades
-- Agents and enrollment credentials, which immediately revokes the local
-- Agent's cloud identity. Site-scoped AI chats are explicitly deleted so
-- they do not survive as orphan conversations with site_id=NULL.
-- ---------------------------------------------------------------------
create or replace function public.wl_remove_site(
  p_site_id uuid,
  p_confirm_name text
) returns jsonb
language plpgsql
volatile
security definer
set search_path=public,pg_temp
as $$
declare
  v_tenant uuid := wl_require_role(array['owner','admin']);
  v_site sites;
  v_agents int;
  v_cameras int;
  v_chats int;
begin
  select * into v_site
    from sites
   where id=p_site_id and tenant_id=v_tenant
   for update;
  if v_site.id is null then
    raise exception 'site not found in your account' using errcode='42501';
  end if;
  if trim(coalesce(p_confirm_name,'')) <> v_site.name then
    raise exception 'site name confirmation does not match' using errcode='22023';
  end if;

  select count(*) into v_agents from agents where site_id=v_site.id;
  select count(*) into v_cameras from cameras where site_id=v_site.id and coalesce(is_canonical,true);
  select count(*) into v_chats from ai_conversations where tenant_id=v_tenant and site_id=v_site.id;

  delete from ai_conversations where tenant_id=v_tenant and site_id=v_site.id;
  delete from sites where id=v_site.id and tenant_id=v_tenant;

  return jsonb_build_object(
    'ok',true,'site_id',p_site_id,'site_name',v_site.name,
    'agents_disconnected',v_agents,'cameras_removed',v_cameras,'chats_removed',v_chats
  );
end $$;
revoke all on function public.wl_remove_site(uuid,text) from public,anon;
grant execute on function public.wl_remove_site(uuid,text) to authenticated;

-- ---------------------------------------------------------------------
-- Unified Notification Center: derives notifications from the authoritative
-- incident/report/fault records. Read state is per user; notification truth
-- remains tenant/site scoped.
-- ---------------------------------------------------------------------
create table if not exists public.notification_reads(
  id bigint generated always as identity primary key,
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  user_id uuid not null,
  site_id uuid references public.sites(id) on delete cascade,
  source_kind text not null check(source_kind in ('incident','report','health')),
  source_id text not null,
  read_at timestamptz not null default now(),
  unique(user_id,source_kind,source_id)
);
create index if not exists notification_reads_user_idx
  on public.notification_reads(user_id,read_at desc);
alter table public.notification_reads enable row level security;
revoke all on table public.notification_reads from public,anon,authenticated;

create or replace function public.wl_notifications(
  p_site_id uuid default null,
  p_limit int default 50
) returns jsonb
language plpgsql
stable
security definer
set search_path=public,pg_temp
as $$
declare
  v_tenant uuid := wl_my_tenant();
  v_user uuid := auth.uid();
  v_limit int := least(greatest(coalesce(p_limit,50),1),200);
begin
  if v_tenant is null or v_user is null then
    raise exception 'not authenticated' using errcode='42501';
  end if;
  if p_site_id is not null and not exists(
    select 1 from sites s where s.id=p_site_id and s.tenant_id=v_tenant
  ) then
    raise exception 'site not in your account' using errcode='42501';
  end if;

  return coalesce((
    with feed as (
      select
        'incident'::text as source_kind,i.id::text as source_id,i.site_id,
        s.name as site_name,
        coalesce(i.incident_started_at,i.occurred_at,i.opened_at) as created_at,
        coalesce(nullif(i.severity,''),'attention') as severity,
        initcap(replace(coalesce(nullif(i.incident_type,''),'Incident'),'_',' ')) as title,
        case
          when c.name is not null then 'Activity needs review on '||c.name||'.'
          else 'Activity at this site needs review.'
        end as body,
        '/incidents/?site='||i.site_id::text as href
      from operations_incidents i
      join sites s on s.id=i.site_id
      left join cameras c on c.id=i.camera_id
      where i.tenant_id=v_tenant
        and (p_site_id is null or i.site_id=p_site_id)
        and coalesce(i.incident_started_at,i.occurred_at,i.opened_at) >= now()-interval '90 days'

      union all

      select
        'report',r.id::text,r.site_id,s.name,r.generated_at,'info',
        'Daily report ready',
        'The WatchLog report for '||to_char(r.report_date,'FMDD FMMonth YYYY')||' is ready.',
        '/reports/?view=yesterday&site='||r.site_id::text
      from report_snapshots r
      join sites s on s.id=r.site_id
      where r.tenant_id=v_tenant
        and (p_site_id is null or r.site_id=p_site_id)
        and r.generated_at >= now()-interval '90 days'

      union all

      select
        'health',f.id::text,f.site_id,s.name,f.opened_at,
        coalesce(nullif(f.severity,''),'warning'),
        case
          when f.camera_id is not null and c.name is not null
            then c.name||' needs attention'
          else 'Site health needs attention'
        end,
        initcap(replace(coalesce(nullif(f.fault_type,''),nullif(f.reason_code,''),'Monitoring issue'),'_',' ')),
        '/site-health/?site='||f.site_id::text
      from operational_faults f
      join sites s on s.id=f.site_id
      left join cameras c on c.id=f.camera_id
      where f.tenant_id=v_tenant
        and f.state<>'resolved'
        and (p_site_id is null or f.site_id=p_site_id)
        and f.opened_at >= now()-interval '90 days'
    ),
    ranked as (
      select feed.*,
             (nr.id is not null) as is_read
      from feed
      left join notification_reads nr
        on nr.tenant_id=v_tenant
       and nr.user_id=v_user
       and nr.source_kind=feed.source_kind
       and nr.source_id=feed.source_id
      order by feed.created_at desc
      limit v_limit
    )
    select jsonb_agg(jsonb_build_object(
      'kind',source_kind,'id',source_id,'site_id',site_id,'site_name',site_name,
      'created_at',created_at,'severity',severity,'title',title,'body',body,
      'href',href,'read',is_read
    ) order by created_at desc)
    from ranked
  ),'[]'::jsonb);
end $$;
revoke all on function public.wl_notifications(uuid,int) from public,anon;
grant execute on function public.wl_notifications(uuid,int) to authenticated;

create or replace function public.wl_notification_mark_read(
  p_source_kind text,
  p_source_id text
) returns jsonb
language plpgsql
volatile
security definer
set search_path=public,pg_temp
as $$
declare
  v_tenant uuid := wl_my_tenant();
  v_user uuid := auth.uid();
  v_site uuid;
begin
  if v_tenant is null or v_user is null then
    raise exception 'not authenticated' using errcode='42501';
  end if;
  if p_source_kind='incident' then
    select site_id into v_site from operations_incidents
     where tenant_id=v_tenant and id::text=p_source_id;
  elsif p_source_kind='report' then
    select site_id into v_site from report_snapshots
     where tenant_id=v_tenant and id::text=p_source_id;
  elsif p_source_kind='health' then
    select site_id into v_site from operational_faults
     where tenant_id=v_tenant and id::text=p_source_id;
  else
    raise exception 'unsupported notification type' using errcode='22023';
  end if;
  if v_site is null then
    raise exception 'notification not found in your account' using errcode='42501';
  end if;

  insert into notification_reads(tenant_id,user_id,site_id,source_kind,source_id,read_at)
  values(v_tenant,v_user,v_site,p_source_kind,p_source_id,now())
  on conflict(user_id,source_kind,source_id)
  do update set read_at=excluded.read_at,site_id=excluded.site_id,tenant_id=excluded.tenant_id;

  return jsonb_build_object('ok',true);
end $$;
revoke all on function public.wl_notification_mark_read(text,text) from public,anon;
grant execute on function public.wl_notification_mark_read(text,text) to authenticated;

create or replace function public.wl_notifications_mark_all_read(
  p_site_id uuid default null
) returns jsonb
language plpgsql
volatile
security definer
set search_path=public,pg_temp
as $$
declare
  v_tenant uuid := wl_my_tenant();
  v_user uuid := auth.uid();
  v_rows int := 0;
begin
  if v_tenant is null or v_user is null then
    raise exception 'not authenticated' using errcode='42501';
  end if;
  if p_site_id is not null and not exists(
    select 1 from sites where id=p_site_id and tenant_id=v_tenant
  ) then
    raise exception 'site not in your account' using errcode='42501';
  end if;

  insert into notification_reads(tenant_id,user_id,site_id,source_kind,source_id,read_at)
  select v_tenant,v_user,x.site_id,x.kind,x.id,now()
  from (
    select i.site_id,'incident'::text kind,i.id::text id
      from operations_incidents i
     where i.tenant_id=v_tenant and (p_site_id is null or i.site_id=p_site_id)
       and coalesce(i.incident_started_at,i.occurred_at,i.opened_at)>=now()-interval '90 days'
    union
    select r.site_id,'report',r.id::text
      from report_snapshots r
     where r.tenant_id=v_tenant and (p_site_id is null or r.site_id=p_site_id)
       and r.generated_at>=now()-interval '90 days'
    union
    select f.site_id,'health',f.id::text
      from operational_faults f
     where f.tenant_id=v_tenant and f.state<>'resolved'
       and (p_site_id is null or f.site_id=p_site_id)
       and f.opened_at>=now()-interval '90 days'
  ) x
  on conflict(user_id,source_kind,source_id)
  do update set read_at=excluded.read_at,site_id=excluded.site_id,tenant_id=excluded.tenant_id;

  get diagnostics v_rows=row_count;
  return jsonb_build_object('ok',true,'marked',v_rows);
end $$;
revoke all on function public.wl_notifications_mark_all_read(uuid) from public,anon;
grant execute on function public.wl_notifications_mark_all_read(uuid) to authenticated;
