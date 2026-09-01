-- =====================================================================
-- 0024 — Analytics Studio v1
--
-- Turns a camera from "channel 4" into a business monitoring source:
-- site type -> camera purpose -> monitoring rules -> analytic events.
--
-- Important separation:
--   analytic_events = measurements (counts, crossings, dwell, activity)
--   events          = incidents worth human review
-- A rule may explicitly promote a measurement to an incident, but most
-- visitor/vehicle counts never enter the incident feed.
--
-- Agent security remains outbound-only. The agent polls its versioned
-- configuration and uploads measurements through authenticated RPCs.
-- Recorder credentials and continuous video never enter this schema.
-- =====================================================================

alter table public.sites
  add column if not exists site_type text not null default 'custom',
  add column if not exists analytics_config_version bigint not null default 1;

alter table public.cameras
  add column if not exists purpose text not null default 'custom',
  add column if not exists analytics_enabled boolean not null default true;

create table if not exists public.monitoring_schedules (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references public.tenants(id) on delete cascade,
  site_id       uuid not null references public.sites(id) on delete cascade,
  name          text not null,
  timezone      text not null default 'Asia/Karachi',
  schedule_json jsonb not null default '{}'::jsonb,
  enabled       boolean not null default true,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index if not exists monitoring_schedules_site_idx
  on public.monitoring_schedules(site_id, enabled);

create table if not exists public.monitoring_rules (
  id                uuid primary key default gen_random_uuid(),
  tenant_id         uuid not null references public.tenants(id) on delete cascade,
  site_id           uuid not null references public.sites(id) on delete cascade,
  camera_id         uuid not null references public.cameras(id) on delete cascade,
  name              text not null,
  rule_type         text not null,
  object_classes    text[] not null default array['person']::text[],
  geometry_json     jsonb not null default '{}'::jsonb,
  direction_json    jsonb not null default '{}'::jsonb,
  schedule_id       uuid references public.monitoring_schedules(id) on delete set null,
  dwell_seconds     integer,
  sample_seconds    numeric(5,2) not null default 2.0,
  enabled           boolean not null default true,
  severity          text not null default 'measurement',
  promote_incident  boolean not null default false,
  config_version    bigint not null default 1,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  constraint monitoring_rules_type_chk check (
    rule_type in ('line_crossing','zone_entry','zone_dwell','occupancy','schedule_activity','health')
  ),
  constraint monitoring_rules_classes_chk check (
    object_classes <@ array['person','car','motorcycle']::text[]
  ),
  constraint monitoring_rules_dwell_chk check (
    dwell_seconds is null or dwell_seconds between 1 and 86400
  ),
  constraint monitoring_rules_sample_chk check (
    sample_seconds between 0.5 and 60
  ),
  constraint monitoring_rules_severity_chk check (
    severity in ('measurement','info','attention','incident')
  )
);
create index if not exists monitoring_rules_camera_idx
  on public.monitoring_rules(camera_id, enabled);
create index if not exists monitoring_rules_site_idx
  on public.monitoring_rules(site_id, enabled);

-- A customer-triggered single still used only for drawing lines/zones.
-- This is deliberately separate from incident snapshots.
create table if not exists public.camera_config_snapshots (
  camera_id     uuid primary key references public.cameras(id) on delete cascade,
  tenant_id     uuid not null references public.tenants(id) on delete cascade,
  site_id       uuid not null references public.sites(id) on delete cascade,
  image         bytea not null,
  bytes         integer not null,
  content_type  text not null default 'image/jpeg',
  captured_at   timestamptz not null default now(),
  constraint camera_config_snapshot_size_chk check (bytes between 1 and 3145728)
);

create table if not exists public.camera_snapshot_requests (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references public.tenants(id) on delete cascade,
  site_id      uuid not null references public.sites(id) on delete cascade,
  camera_id    uuid not null references public.cameras(id) on delete cascade,
  requested_by uuid references auth.users(id) on delete set null,
  requested_at timestamptz not null default now(),
  completed_at timestamptz,
  constraint camera_snapshot_one_open unique nulls not distinct (camera_id, completed_at)
);
create index if not exists camera_snapshot_requests_site_idx
  on public.camera_snapshot_requests(site_id, requested_at desc);

create table if not exists public.analytic_events (
  id                 bigint generated always as identity primary key,
  tenant_id          uuid not null references public.tenants(id) on delete cascade,
  site_id            uuid not null references public.sites(id) on delete cascade,
  camera_id          uuid not null references public.cameras(id) on delete cascade,
  agent_id           uuid references public.agents(id) on delete set null,
  monitoring_rule_id uuid references public.monitoring_rules(id) on delete set null,
  event_type         text not null,
  object_class       text,
  track_key          text,
  direction          text,
  occurred_at        timestamptz not null,
  duration_seconds   numeric(10,2),
  dedupe_key         text not null,
  metadata_json      jsonb not null default '{}'::jsonb,
  received_at        timestamptz not null default now(),
  constraint analytic_events_class_chk check (
    object_class is null or object_class in ('person','car','motorcycle')
  ),
  constraint analytic_events_dedupe_uniq unique (tenant_id, dedupe_key)
);
create index if not exists analytic_events_site_time_idx
  on public.analytic_events(site_id, occurred_at desc);
create index if not exists analytic_events_camera_time_idx
  on public.analytic_events(camera_id, occurred_at desc);
create index if not exists analytic_events_rule_time_idx
  on public.analytic_events(monitoring_rule_id, occurred_at desc);

alter table public.monitoring_schedules      enable row level security;
alter table public.monitoring_rules          enable row level security;
alter table public.camera_config_snapshots   enable row level security;
alter table public.camera_snapshot_requests  enable row level security;
alter table public.analytic_events            enable row level security;

-- Portal users may read their own tenant analytics. Writes go through RPCs.
drop policy if exists portal_read_monitoring_schedules on public.monitoring_schedules;
create policy portal_read_monitoring_schedules on public.monitoring_schedules
  for select to authenticated using (public.wl_is_member(tenant_id));

drop policy if exists portal_read_monitoring_rules on public.monitoring_rules;
create policy portal_read_monitoring_rules on public.monitoring_rules
  for select to authenticated using (public.wl_is_member(tenant_id));

drop policy if exists portal_read_analytic_events on public.analytic_events;
create policy portal_read_analytic_events on public.analytic_events
  for select to authenticated using (public.wl_is_member(tenant_id));

-- ---------------------------------------------------------------------
-- Internal helpers
-- ---------------------------------------------------------------------
create or replace function public.wl_analytics_bump_site(p_site uuid)
returns bigint
language plpgsql
security definer
set search_path = public
as $$
declare v bigint;
begin
  update sites
     set analytics_config_version = analytics_config_version + 1
   where id = p_site
   returning analytics_config_version into v;
  return v;
end $$;
revoke all on function public.wl_analytics_bump_site(uuid) from public, anon, authenticated;

create or replace function public.wl_analytics_valid_site_type(p text)
returns boolean language sql immutable as $$
  select p in ('retail','warehouse_logistics','manufacturing','office_commercial',
               'school_campus','parking_yard','residential_community','custom')
$$;

create or replace function public.wl_analytics_valid_purpose(p text)
returns boolean language sql immutable as $$
  select p in ('entrance_exit','main_gate','reception','checkout_till','loading_bay',
               'warehouse_floor','perimeter','restricted_area','parking','office_floor',
               'school_gate','corridor','custom')
$$;

-- ---------------------------------------------------------------------
-- Catalog: business language and recommended packs. No customer data.
-- ---------------------------------------------------------------------
create or replace function public.wl_analytics_catalog()
returns jsonb
language sql
stable
security invoker
set search_path = public
as $$
  select jsonb_build_object(
    'site_types', jsonb_build_array(
      jsonb_build_object('key','retail','label','Retail store'),
      jsonb_build_object('key','warehouse_logistics','label','Warehouse / logistics'),
      jsonb_build_object('key','manufacturing','label','Factory / manufacturing'),
      jsonb_build_object('key','office_commercial','label','Office / commercial building'),
      jsonb_build_object('key','school_campus','label','School / campus'),
      jsonb_build_object('key','parking_yard','label','Parking / yard'),
      jsonb_build_object('key','residential_community','label','Residential / gated community'),
      jsonb_build_object('key','custom','label','Custom')
    ),
    'purposes', jsonb_build_array(
      jsonb_build_object('key','entrance_exit','label','Entrance / Exit'),
      jsonb_build_object('key','main_gate','label','Main Gate'),
      jsonb_build_object('key','reception','label','Reception'),
      jsonb_build_object('key','checkout_till','label','Checkout / Till'),
      jsonb_build_object('key','loading_bay','label','Loading Bay'),
      jsonb_build_object('key','warehouse_floor','label','Warehouse Floor'),
      jsonb_build_object('key','perimeter','label','Perimeter'),
      jsonb_build_object('key','restricted_area','label','Restricted Area'),
      jsonb_build_object('key','parking','label','Parking'),
      jsonb_build_object('key','office_floor','label','Office Floor'),
      jsonb_build_object('key','school_gate','label','School Gate'),
      jsonb_build_object('key','corridor','label','Corridor'),
      jsonb_build_object('key','custom','label','Custom')
    ),
    'analytics', jsonb_build_array(
      jsonb_build_object('key','visitor_flow','label','Visitor Flow','rule_type','line_crossing','classes',jsonb_build_array('person')),
      jsonb_build_object('key','vehicle_flow','label','Vehicle Flow','rule_type','line_crossing','classes',jsonb_build_array('car','motorcycle')),
      jsonb_build_object('key','boundary_monitoring','label','Boundary Monitoring','rule_type','line_crossing','classes',jsonb_build_array('person','car','motorcycle')),
      jsonb_build_object('key','zone_activity','label','Zone Activity','rule_type','zone_entry','classes',jsonb_build_array('person','car','motorcycle')),
      jsonb_build_object('key','dwell','label','Dwell / Time in Zone','rule_type','zone_dwell','classes',jsonb_build_array('person')),
      jsonb_build_object('key','after_hours','label','After-Hours Activity','rule_type','schedule_activity','classes',jsonb_build_array('person','car','motorcycle')),
      jsonb_build_object('key','site_health','label','Site Health','rule_type','health','classes','[]'::jsonb)
    ),
    'packs', jsonb_build_object(
      'retail', jsonb_build_array('visitor_flow','after_hours','site_health'),
      'warehouse_logistics', jsonb_build_array('visitor_flow','vehicle_flow','boundary_monitoring','zone_activity','after_hours','site_health'),
      'manufacturing', jsonb_build_array('visitor_flow','vehicle_flow','boundary_monitoring','zone_activity','after_hours','site_health'),
      'office_commercial', jsonb_build_array('visitor_flow','after_hours','site_health'),
      'school_campus', jsonb_build_array('visitor_flow','vehicle_flow','boundary_monitoring','after_hours','site_health'),
      'parking_yard', jsonb_build_array('vehicle_flow','boundary_monitoring','after_hours','site_health'),
      'residential_community', jsonb_build_array('visitor_flow','vehicle_flow','boundary_monitoring','after_hours','site_health'),
      'custom', '[]'::jsonb
    )
  )
$$;
revoke all on function public.wl_analytics_catalog() from public, anon;
grant execute on function public.wl_analytics_catalog() to authenticated;

-- ---------------------------------------------------------------------
-- Portal write APIs
-- ---------------------------------------------------------------------
create or replace function public.wl_set_site_type(p_site_id uuid, p_site_type text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_tenant uuid; v_version bigint;
begin
  select tenant_id into v_tenant from sites where id = p_site_id;
  if v_tenant is null or not wl_is_member(v_tenant) then
    raise exception 'not your site' using errcode='42501';
  end if;
  if not wl_analytics_valid_site_type(p_site_type) then
    raise exception 'invalid site type';
  end if;
  update sites set site_type = p_site_type where id = p_site_id;
  v_version := wl_analytics_bump_site(p_site_id);
  return jsonb_build_object('ok',true,'version',v_version,'site_type',p_site_type);
end $$;

create or replace function public.wl_set_camera_profile(
  p_camera_id uuid,
  p_name text default null,
  p_purpose text default 'custom',
  p_enabled boolean default true
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_tenant uuid; v_site uuid; v_version bigint;
begin
  select tenant_id, site_id into v_tenant, v_site from cameras where id = p_camera_id;
  if v_tenant is null or not wl_is_member(v_tenant) then
    raise exception 'not your camera' using errcode='42501';
  end if;
  if not wl_analytics_valid_purpose(p_purpose) then
    raise exception 'invalid camera purpose';
  end if;
  update cameras
     set name = coalesce(nullif(trim(p_name),''), name),
         purpose = p_purpose,
         analytics_enabled = coalesce(p_enabled,true)
   where id = p_camera_id;
  v_version := wl_analytics_bump_site(v_site);
  return jsonb_build_object('ok',true,'version',v_version);
end $$;

create or replace function public.wl_upsert_monitoring_schedule(
  p_id uuid,
  p_site_id uuid,
  p_name text,
  p_timezone text,
  p_schedule jsonb,
  p_enabled boolean default true
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_tenant uuid; v_id uuid; v_version bigint;
begin
  select tenant_id into v_tenant from sites where id = p_site_id;
  if v_tenant is null or not wl_is_member(v_tenant) then
    raise exception 'not your site' using errcode='42501';
  end if;
  if p_id is null then
    insert into monitoring_schedules(tenant_id,site_id,name,timezone,schedule_json,enabled)
    values(v_tenant,p_site_id,coalesce(nullif(trim(p_name),''),'Schedule'),
           coalesce(nullif(trim(p_timezone),''),'Asia/Karachi'),coalesce(p_schedule,'{}'::jsonb),coalesce(p_enabled,true))
    returning id into v_id;
  else
    update monitoring_schedules
       set name=coalesce(nullif(trim(p_name),''),name),
           timezone=coalesce(nullif(trim(p_timezone),''),timezone),
           schedule_json=coalesce(p_schedule,schedule_json), enabled=coalesce(p_enabled,enabled), updated_at=now()
     where id=p_id and site_id=p_site_id and tenant_id=v_tenant
     returning id into v_id;
    if v_id is null then raise exception 'schedule not found' using errcode='42501'; end if;
  end if;
  v_version := wl_analytics_bump_site(p_site_id);
  return jsonb_build_object('id',v_id,'version',v_version);
end $$;

create or replace function public.wl_upsert_monitoring_rule(
  p_id uuid,
  p_camera_id uuid,
  p_name text,
  p_rule_type text,
  p_object_classes text[],
  p_geometry jsonb,
  p_direction jsonb default '{}'::jsonb,
  p_schedule_id uuid default null,
  p_dwell_seconds integer default null,
  p_sample_seconds numeric default 2.0,
  p_enabled boolean default true,
  p_severity text default 'measurement',
  p_promote_incident boolean default false
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_tenant uuid; v_site uuid; v_id uuid; v_version bigint; v_rule_version bigint;
begin
  select tenant_id,site_id into v_tenant,v_site from cameras where id=p_camera_id;
  if v_tenant is null or not wl_is_member(v_tenant) then
    raise exception 'not your camera' using errcode='42501';
  end if;
  if p_rule_type not in ('line_crossing','zone_entry','zone_dwell','occupancy','schedule_activity','health') then
    raise exception 'invalid rule type';
  end if;
  if coalesce(p_object_classes,array[]::text[]) && array(select unnest(coalesce(p_object_classes,array[]::text[])) except select unnest(array['person','car','motorcycle']::text[])) then
    raise exception 'invalid object class';
  end if;
  if p_schedule_id is not null and not exists(
      select 1 from monitoring_schedules where id=p_schedule_id and site_id=v_site and tenant_id=v_tenant) then
    raise exception 'schedule not in this site' using errcode='42501';
  end if;

  v_version := wl_analytics_bump_site(v_site);
  if p_id is null then
    insert into monitoring_rules(tenant_id,site_id,camera_id,name,rule_type,object_classes,
      geometry_json,direction_json,schedule_id,dwell_seconds,sample_seconds,enabled,severity,promote_incident,config_version)
    values(v_tenant,v_site,p_camera_id,coalesce(nullif(trim(p_name),''),'Monitoring rule'),p_rule_type,
      coalesce(p_object_classes,array['person']::text[]),coalesce(p_geometry,'{}'::jsonb),coalesce(p_direction,'{}'::jsonb),
      p_schedule_id,p_dwell_seconds,coalesce(p_sample_seconds,2.0),coalesce(p_enabled,true),coalesce(p_severity,'measurement'),
      coalesce(p_promote_incident,false),v_version)
    returning id,config_version into v_id,v_rule_version;
  else
    update monitoring_rules set
      name=coalesce(nullif(trim(p_name),''),name), rule_type=p_rule_type,
      object_classes=coalesce(p_object_classes,object_classes), geometry_json=coalesce(p_geometry,geometry_json),
      direction_json=coalesce(p_direction,direction_json), schedule_id=p_schedule_id,
      dwell_seconds=p_dwell_seconds, sample_seconds=coalesce(p_sample_seconds,sample_seconds),
      enabled=coalesce(p_enabled,enabled), severity=coalesce(p_severity,severity),
      promote_incident=coalesce(p_promote_incident,promote_incident), config_version=v_version, updated_at=now()
    where id=p_id and camera_id=p_camera_id and tenant_id=v_tenant
    returning id,config_version into v_id,v_rule_version;
    if v_id is null then raise exception 'rule not found' using errcode='42501'; end if;
  end if;
  return jsonb_build_object('id',v_id,'version',v_rule_version);
end $$;

create or replace function public.wl_delete_monitoring_rule(p_rule_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_tenant uuid; v_site uuid; v_version bigint;
begin
  select tenant_id,site_id into v_tenant,v_site from monitoring_rules where id=p_rule_id;
  if v_tenant is null or not wl_is_member(v_tenant) then
    raise exception 'rule not found' using errcode='42501';
  end if;
  delete from monitoring_rules where id=p_rule_id;
  v_version := wl_analytics_bump_site(v_site);
  return jsonb_build_object('ok',true,'version',v_version);
end $$;

create or replace function public.wl_request_config_snapshot(p_camera_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_tenant uuid; v_site uuid; v_id uuid;
begin
  select tenant_id,site_id into v_tenant,v_site from cameras where id=p_camera_id;
  if v_tenant is null or not wl_is_member(v_tenant) then
    raise exception 'not your camera' using errcode='42501';
  end if;
  select id into v_id from camera_snapshot_requests where camera_id=p_camera_id and completed_at is null;
  if v_id is null then
    insert into camera_snapshot_requests(tenant_id,site_id,camera_id,requested_by)
    values(v_tenant,v_site,p_camera_id,auth.uid()) returning id into v_id;
  end if;
  return jsonb_build_object('request_id',v_id,'status','pending');
end $$;

create or replace function public.wl_camera_config_snapshot(p_camera_id uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare v_tenant uuid; v jsonb;
begin
  select tenant_id into v_tenant from cameras where id=p_camera_id;
  if v_tenant is null or not wl_is_member(v_tenant) then return null; end if;
  select jsonb_build_object('camera_id',camera_id,'content_type',content_type,'bytes',bytes,
         'captured_at',captured_at,'image_b64',encode(image,'base64')) into v
    from camera_config_snapshots where camera_id=p_camera_id;
  return v;
end $$;

-- Complete portal payload for Analytics Studio.
create or replace function public.wl_analytics_studio()
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare v_tenant uuid := wl_my_tenant();
begin
  if v_tenant is null then return jsonb_build_object('sites','[]'::jsonb); end if;
  return jsonb_build_object(
    'sites', coalesce((select jsonb_agg(jsonb_build_object(
      'id',s.id,'name',s.name,'timezone',s.timezone,'site_type',s.site_type,
      'config_version',s.analytics_config_version,
      'cameras',coalesce((select jsonb_agg(jsonb_build_object(
        'id',c.id,'channel',c.channel,'name',c.name,'purpose',c.purpose,
        'analytics_enabled',c.analytics_enabled,
        'has_config_snapshot',exists(select 1 from camera_config_snapshots cs where cs.camera_id=c.id),
        'snapshot_requested',exists(select 1 from camera_snapshot_requests cr where cr.camera_id=c.id and cr.completed_at is null),
        'rules',coalesce((select jsonb_agg(jsonb_build_object(
          'id',r.id,'name',r.name,'rule_type',r.rule_type,'object_classes',r.object_classes,
          'geometry',r.geometry_json,'direction',r.direction_json,'schedule_id',r.schedule_id,
          'dwell_seconds',r.dwell_seconds,'sample_seconds',r.sample_seconds,'enabled',r.enabled,
          'severity',r.severity,'promote_incident',r.promote_incident,'config_version',r.config_version)
          order by r.created_at) from monitoring_rules r where r.camera_id=c.id),'[]'::jsonb)
      ) order by c.channel) from cameras c where c.site_id=s.id),'[]'::jsonb),
      'schedules',coalesce((select jsonb_agg(jsonb_build_object(
        'id',ms.id,'name',ms.name,'timezone',ms.timezone,'schedule',ms.schedule_json,'enabled',ms.enabled)
        order by ms.name) from monitoring_schedules ms where ms.site_id=s.id),'[]'::jsonb)
    ) order by s.name) from sites s where s.tenant_id=v_tenant),'[]'::jsonb)
  );
end $$;

-- ---------------------------------------------------------------------
-- Agent configuration + config snapshot upload
-- ---------------------------------------------------------------------
create or replace function public.wl_agent_analytics_config(
  p_agent_id uuid, p_agent_key text, p_known_version bigint default 0
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare v_agent agents; v_version bigint; v_config jsonb; v_requests jsonb;
begin
  v_agent := wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then raise exception 'agent not recognised' using errcode='28000'; end if;
  select analytics_config_version into v_version from sites where id=v_agent.site_id;
  select coalesce(jsonb_agg(jsonb_build_object('request_id',cr.id,'camera_id',cr.camera_id,'channel',c.channel)
         order by cr.requested_at),'[]'::jsonb)
    into v_requests
    from camera_snapshot_requests cr join cameras c on c.id=cr.camera_id
   where cr.site_id=v_agent.site_id and cr.completed_at is null;
  if coalesce(p_known_version,0)=v_version then
    return jsonb_build_object('version',v_version,'changed',false,'snapshot_requests',v_requests);
  end if;

  select jsonb_build_object(
    'site_id',s.id,'site_type',s.site_type,'timezone',s.timezone,'version',s.analytics_config_version,
    'schedules',coalesce((select jsonb_agg(jsonb_build_object('id',ms.id,'name',ms.name,'timezone',ms.timezone,
       'schedule',ms.schedule_json,'enabled',ms.enabled)) from monitoring_schedules ms where ms.site_id=s.id and ms.enabled),'[]'::jsonb),
    'cameras',coalesce((select jsonb_agg(jsonb_build_object(
       'id',c.id,'channel',c.channel,'name',c.name,'purpose',c.purpose,'analytics_enabled',c.analytics_enabled,
       'rules',coalesce((select jsonb_agg(jsonb_build_object('id',r.id,'name',r.name,'rule_type',r.rule_type,
          'object_classes',r.object_classes,'geometry',r.geometry_json,'direction',r.direction_json,'schedule_id',r.schedule_id,
          'dwell_seconds',r.dwell_seconds,'sample_seconds',r.sample_seconds,'severity',r.severity,
          'promote_incident',r.promote_incident)) from monitoring_rules r where r.camera_id=c.id and r.enabled),'[]'::jsonb)
       ) order by c.channel) from cameras c where c.site_id=s.id and c.analytics_enabled),'[]'::jsonb)
  ) into v_config from sites s where s.id=v_agent.site_id;

  return jsonb_build_object('version',v_version,'changed',true,'config',v_config,'snapshot_requests',v_requests);
end $$;

create or replace function public.wl_upload_config_snapshot(
  p_agent_id uuid, p_agent_key text, p_camera_id uuid, p_image_b64 text,
  p_content_type text default 'image/jpeg'
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_agent agents; v_cam cameras; v_img bytea;
begin
  v_agent := wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then raise exception 'agent not recognised' using errcode='28000'; end if;
  select * into v_cam from cameras where id=p_camera_id and site_id=v_agent.site_id;
  if v_cam.id is null then raise exception 'camera not in this site' using errcode='42501'; end if;
  v_img := decode(p_image_b64,'base64');
  if octet_length(v_img) not between 1 and 3145728 then raise exception 'snapshot size invalid'; end if;
  insert into camera_config_snapshots(camera_id,tenant_id,site_id,image,bytes,content_type,captured_at)
  values(v_cam.id,v_agent.tenant_id,v_agent.site_id,v_img,octet_length(v_img),coalesce(nullif(p_content_type,''),'image/jpeg'),now())
  on conflict(camera_id) do update set image=excluded.image,bytes=excluded.bytes,content_type=excluded.content_type,captured_at=excluded.captured_at;
  update camera_snapshot_requests set completed_at=now()
   where camera_id=v_cam.id and completed_at is null;
  return jsonb_build_object('ok',true,'bytes',octet_length(v_img));
end $$;

-- ---------------------------------------------------------------------
-- Agent measurement ingest. Dedupe is server enforced.
-- Optional incident promotion is controlled by the server-side rule, not
-- by the distributed agent.
-- ---------------------------------------------------------------------
create or replace function public.wl_ingest_analytic_events(
  p_agent_id uuid, p_agent_key text, p_events jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_agent agents; v_received int; v_inserted int; v_promoted int := 0;
begin
  v_agent := wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then raise exception 'agent not recognised' using errcode='28000'; end if;
  select count(*) into v_received from jsonb_array_elements(coalesce(p_events,'[]'::jsonb));

  with incoming as (
    select e,
      nullif(e->>'rule_id','')::uuid as rule_id,
      e->>'channel' as channel,
      coalesce(nullif(e->>'event_type',''),'measurement') as event_type,
      nullif(e->>'object_class','') as object_class,
      nullif(e->>'track_key','') as track_key,
      nullif(e->>'direction','') as direction,
      (e->>'occurred_at')::timestamptz as occurred_at,
      nullif(e->>'duration_seconds','')::numeric as duration_seconds,
      coalesce(nullif(e->>'dedupe_key',''), md5(e::text)) as dedupe_key,
      coalesce(e->'metadata','{}'::jsonb) as metadata
    from jsonb_array_elements(coalesce(p_events,'[]'::jsonb)) e
    where e->>'occurred_at' is not null and e->>'channel' is not null
  ), valid as (
    select i.*,c.id as camera_id,r.promote_incident,r.severity,r.name as rule_name
      from incoming i
      join cameras c on c.site_id=v_agent.site_id and c.channel=i.channel
      join monitoring_rules r on r.id=i.rule_id and r.camera_id=c.id and r.site_id=v_agent.site_id and r.enabled
     where i.object_class is null or i.object_class in ('person','car','motorcycle')
  ), ins as (
    insert into analytic_events(tenant_id,site_id,camera_id,agent_id,monitoring_rule_id,event_type,
      object_class,track_key,direction,occurred_at,duration_seconds,dedupe_key,metadata_json)
    select v_agent.tenant_id,v_agent.site_id,v.camera_id,v_agent.id,v.rule_id,v.event_type,
      v.object_class,v.track_key,v.direction,v.occurred_at,v.duration_seconds,v.dedupe_key,v.metadata
    from valid v
    on conflict(tenant_id,dedupe_key) do nothing
    returning id, monitoring_rule_id, camera_id, event_type, object_class, track_key, direction, occurred_at, duration_seconds, dedupe_key, metadata_json
  )
  select count(*) into v_inserted from ins;

  -- Promote only rules explicitly configured server-side as incidents.
  with candidates as (
    select ae.*,r.name as rule_name,r.severity
      from analytic_events ae join monitoring_rules r on r.id=ae.monitoring_rule_id
     where ae.agent_id=v_agent.id and ae.received_at > now()-interval '2 minutes'
       and r.promote_incident
  ), put as (
    insert into events(tenant_id,site_id,camera_id,agent_id,event_type,device_ts,agent_ts,dedupe_key,payload)
    select v_agent.tenant_id,v_agent.site_id,c.camera_id,v_agent.id,
           'analytic_'||c.event_type,c.occurred_at,now(),'analytics:'||c.dedupe_key,
           jsonb_build_object('analytic_event_id',c.id,'rule_id',c.monitoring_rule_id,'rule',c.rule_name,
             'severity',c.severity,'object_class',c.object_class,'direction',c.direction,
             'duration_seconds',c.duration_seconds,'metadata',c.metadata_json)
      from candidates c
    on conflict(tenant_id,dedupe_key) do nothing
    returning 1
  ) select count(*) into v_promoted from put;

  update agents set last_seen_at=now() where id=v_agent.id;
  return jsonb_build_object('received',v_received,'inserted',v_inserted,
    'skipped',v_received-v_inserted,'promoted_incidents',v_promoted,'server_time',now());
end $$;

-- ---------------------------------------------------------------------
-- Portal analytics overview. Raw measurements stay queryable; this v1
-- aggregate is computed on demand for up to 90 days, which is appropriate
-- for crossing/zone events and avoids an extra rollup scheduler dependency.
-- ---------------------------------------------------------------------
create or replace function public.wl_analytics_overview(
  p_days int default 7, p_site_id uuid default null
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare v_tenant uuid := wl_my_tenant(); v_days int := least(greatest(coalesce(p_days,7),1),90);
begin
  if v_tenant is null then return jsonb_build_object('summary','{}'::jsonb,'daily','[]'::jsonb); end if;
  if p_site_id is not null and not exists(select 1 from sites where id=p_site_id and tenant_id=v_tenant) then
    raise exception 'not your site' using errcode='42501';
  end if;
  return jsonb_build_object(
    'window_days',v_days,
    'summary',jsonb_build_object(
      'visitor_in', (select count(*) from analytic_events ae where ae.tenant_id=v_tenant and (p_site_id is null or ae.site_id=p_site_id)
        and ae.occurred_at>=now()-make_interval(days=>v_days) and ae.object_class='person' and ae.event_type='line_crossing' and ae.direction='in'),
      'visitor_out',(select count(*) from analytic_events ae where ae.tenant_id=v_tenant and (p_site_id is null or ae.site_id=p_site_id)
        and ae.occurred_at>=now()-make_interval(days=>v_days) and ae.object_class='person' and ae.event_type='line_crossing' and ae.direction='out'),
      'vehicles_in',(select count(*) from analytic_events ae where ae.tenant_id=v_tenant and (p_site_id is null or ae.site_id=p_site_id)
        and ae.occurred_at>=now()-make_interval(days=>v_days) and ae.object_class in ('car','motorcycle') and ae.event_type='line_crossing' and ae.direction='in'),
      'vehicles_out',(select count(*) from analytic_events ae where ae.tenant_id=v_tenant and (p_site_id is null or ae.site_id=p_site_id)
        and ae.occurred_at>=now()-make_interval(days=>v_days) and ae.object_class in ('car','motorcycle') and ae.event_type='line_crossing' and ae.direction='out'),
      'zone_entries',(select count(*) from analytic_events ae where ae.tenant_id=v_tenant and (p_site_id is null or ae.site_id=p_site_id)
        and ae.occurred_at>=now()-make_interval(days=>v_days) and ae.event_type='zone_entry'),
      'after_hours',(select count(*) from analytic_events ae where ae.tenant_id=v_tenant and (p_site_id is null or ae.site_id=p_site_id)
        and ae.occurred_at>=now()-make_interval(days=>v_days) and ae.event_type='schedule_activity')
    ),
    'daily',coalesce((select jsonb_agg(to_jsonb(x) order by x.day) from (
      select (ae.occurred_at at time zone coalesce(s.timezone,'Asia/Karachi'))::date as day,
        count(*) filter(where ae.object_class='person' and ae.event_type='line_crossing' and ae.direction='in') as visitor_in,
        count(*) filter(where ae.object_class='person' and ae.event_type='line_crossing' and ae.direction='out') as visitor_out,
        count(*) filter(where ae.object_class in ('car','motorcycle') and ae.event_type='line_crossing' and ae.direction='in') as vehicles_in,
        count(*) filter(where ae.event_type='zone_entry') as zone_entries,
        count(*) filter(where ae.event_type='schedule_activity') as after_hours
      from analytic_events ae join sites s on s.id=ae.site_id
      where ae.tenant_id=v_tenant and (p_site_id is null or ae.site_id=p_site_id)
        and ae.occurred_at>=now()-make_interval(days=>v_days)
      group by 1 order by 1
    ) x),'[]'::jsonb),
    'by_rule',coalesce((select jsonb_agg(to_jsonb(x) order by x.count desc) from (
      select r.id as rule_id,r.name,r.rule_type,c.name as camera,s.name as site,count(*) as count
      from analytic_events ae join monitoring_rules r on r.id=ae.monitoring_rule_id
      join cameras c on c.id=ae.camera_id join sites s on s.id=ae.site_id
      where ae.tenant_id=v_tenant and (p_site_id is null or ae.site_id=p_site_id)
        and ae.occurred_at>=now()-make_interval(days=>v_days)
      group by r.id,r.name,r.rule_type,c.name,s.name order by count(*) desc limit 20
    ) x),'[]'::jsonb)
  );
end $$;

-- Privilege normalization.
revoke all on function public.wl_set_site_type(uuid,text) from public,anon;
revoke all on function public.wl_set_camera_profile(uuid,text,text,boolean) from public,anon;
revoke all on function public.wl_upsert_monitoring_schedule(uuid,uuid,text,text,jsonb,boolean) from public,anon;
revoke all on function public.wl_upsert_monitoring_rule(uuid,uuid,text,text,text[],jsonb,jsonb,uuid,integer,numeric,boolean,text,boolean) from public,anon;
revoke all on function public.wl_delete_monitoring_rule(uuid) from public,anon;
revoke all on function public.wl_request_config_snapshot(uuid) from public,anon;
revoke all on function public.wl_camera_config_snapshot(uuid) from public,anon;
revoke all on function public.wl_analytics_studio() from public,anon;
revoke all on function public.wl_analytics_overview(integer,uuid) from public,anon;

grant execute on function public.wl_set_site_type(uuid,text) to authenticated;
grant execute on function public.wl_set_camera_profile(uuid,text,text,boolean) to authenticated;
grant execute on function public.wl_upsert_monitoring_schedule(uuid,uuid,text,text,jsonb,boolean) to authenticated;
grant execute on function public.wl_upsert_monitoring_rule(uuid,uuid,text,text,text[],jsonb,jsonb,uuid,integer,numeric,boolean,text,boolean) to authenticated;
grant execute on function public.wl_delete_monitoring_rule(uuid) to authenticated;
grant execute on function public.wl_request_config_snapshot(uuid) to authenticated;
grant execute on function public.wl_camera_config_snapshot(uuid) to authenticated;
grant execute on function public.wl_analytics_studio() to authenticated;
grant execute on function public.wl_analytics_overview(integer,uuid) to authenticated;

revoke all on function public.wl_agent_analytics_config(uuid,text,bigint) from public;
revoke all on function public.wl_upload_config_snapshot(uuid,text,uuid,text,text) from public;
revoke all on function public.wl_ingest_analytic_events(uuid,text,jsonb) from public;
grant execute on function public.wl_agent_analytics_config(uuid,text,bigint) to anon,authenticated;
grant execute on function public.wl_upload_config_snapshot(uuid,text,uuid,text,text) to anon,authenticated;
grant execute on function public.wl_ingest_analytic_events(uuid,text,jsonb) to anon,authenticated;
