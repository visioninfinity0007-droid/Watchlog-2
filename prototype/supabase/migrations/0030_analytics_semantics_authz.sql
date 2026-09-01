-- =====================================================================
-- 0030 - Analytics semantic identity + configuration authorization
--
-- 0024-0028 established Analytics Studio. This forward migration closes two
-- production-readiness gaps without rewriting already-versioned migrations:
--
-- 1) A technical rule type is not a business metric. Visitor Flow, Vehicle
--    Flow and Boundary Monitoring can all use line crossing. Preserve the
--    analytic_key on the rule and on every measurement so history stays true.
-- 2) Tenant viewers are read-only. Only tenant owner/admin may change site
--    types, camera profiles, schedules, rules or request configuration stills.
--
-- Site Health is platform health and is always on. Legacy `health` rules are
-- retained for audit compatibility but disabled and omitted from agent/Studio
-- configuration. No new health rule can be created.
-- =====================================================================

-- ---------------------------------------------------------------------
-- Semantic identity
-- ---------------------------------------------------------------------
alter table public.monitoring_rules
  add column if not exists analytic_key text;

update public.monitoring_rules r
   set analytic_key = case
     when r.rule_type = 'health' then 'site_health'
     when r.rule_type = 'occupancy' then 'checkout_activity'
     when r.rule_type = 'zone_entry' then 'zone_activity'
     when r.rule_type = 'zone_dwell' then 'dwell'
     when r.rule_type = 'schedule_activity' then 'after_hours'
     when r.rule_type = 'line_crossing'
          and (lower(r.name) like '%boundary%' or lower(r.name) like '%perimeter%')
       then 'boundary_monitoring'
     when r.rule_type = 'line_crossing'
          and r.object_classes <@ array['person']::text[]
       then 'visitor_flow'
     when r.rule_type = 'line_crossing'
          and not ('person' = any(r.object_classes))
       then 'vehicle_flow'
     when r.rule_type = 'line_crossing' then 'boundary_monitoring'
     else 'custom'
   end
 where r.analytic_key is null;

alter table public.monitoring_rules
  alter column analytic_key set not null;

alter table public.monitoring_rules
  drop constraint if exists monitoring_rules_analytic_key_chk;
alter table public.monitoring_rules
  add constraint monitoring_rules_analytic_key_chk check (
    analytic_key in ('visitor_flow','vehicle_flow','boundary_monitoring',
                     'zone_activity','dwell','checkout_activity','after_hours',
                     'site_health','custom')
  );

-- Site Health does not require video inference/configuration.
update public.monitoring_rules
   set enabled = false, updated_at = now()
 where rule_type = 'health' and enabled;

alter table public.analytic_events
  add column if not exists analytic_key text;

-- Prefer the server-side rule identity. For orphaned historical rows use the
-- least-surprising technical fallback and mark anything ambiguous as custom.
update public.analytic_events ae
   set analytic_key = coalesce(
     (select r.analytic_key from public.monitoring_rules r where r.id = ae.monitoring_rule_id),
     case
       when ae.event_type = 'occupancy' then 'checkout_activity'
       when ae.event_type = 'zone_entry' then 'zone_activity'
       when ae.event_type = 'zone_dwell' then 'dwell'
       when ae.event_type = 'schedule_activity' then 'after_hours'
       when ae.event_type = 'line_crossing' and ae.object_class = 'person' then 'visitor_flow'
       when ae.event_type = 'line_crossing' and ae.object_class in ('car','motorcycle') then 'vehicle_flow'
       else 'custom'
     end)
 where ae.analytic_key is null;

alter table public.analytic_events
  alter column analytic_key set not null;

alter table public.analytic_events
  drop constraint if exists analytic_events_analytic_key_chk;
alter table public.analytic_events
  add constraint analytic_events_analytic_key_chk check (
    analytic_key in ('visitor_flow','vehicle_flow','boundary_monitoring',
                     'zone_activity','dwell','checkout_activity','after_hours',
                     'site_health','custom')
  );
create index if not exists analytic_events_key_time_idx
  on public.analytic_events(tenant_id, analytic_key, occurred_at desc);

-- ---------------------------------------------------------------------
-- Authorization + validation helpers
-- ---------------------------------------------------------------------
create or replace function public.wl_analytics_can_manage(p_tenant uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists(
    select 1 from memberships m
     where m.tenant_id = p_tenant
       and m.user_id = auth.uid()
       and m.role in ('owner','admin')
  )
$$;
revoke all on function public.wl_analytics_can_manage(uuid) from public,anon,authenticated;

create or replace function public.wl_analytics_require_manager(p_tenant uuid)
returns void
language plpgsql
stable
security definer
set search_path = public
as $$
begin
  if not public.wl_analytics_can_manage(p_tenant) then
    raise exception 'owner or admin access required' using errcode='42501';
  end if;
end $$;
revoke all on function public.wl_analytics_require_manager(uuid) from public,anon,authenticated;

create or replace function public.wl_analytics_valid_key(p text)
returns boolean
language sql
immutable
as $$
  select p in ('visitor_flow','vehicle_flow','boundary_monitoring',
               'zone_activity','dwell','checkout_activity','after_hours')
$$;
revoke all on function public.wl_analytics_valid_key(text) from public,anon,authenticated;

create or replace function public.wl_analytics_points_valid(
  p_geometry jsonb, p_min integer, p_max integer
) returns boolean
language plpgsql
immutable
as $$
declare p jsonb; x numeric; y numeric; pts jsonb;
begin
  pts := coalesce(p_geometry->'points','[]'::jsonb);
  if jsonb_typeof(pts) <> 'array' then return false; end if;
  if jsonb_array_length(pts) not between p_min and p_max then return false; end if;
  for p in select * from jsonb_array_elements(pts)
  loop
    if jsonb_typeof(p) <> 'array' or jsonb_array_length(p) <> 2 then return false; end if;
    begin
      x := (p->>0)::numeric; y := (p->>1)::numeric;
    exception when others then return false;
    end;
    if x < 0 or x > 1 or y < 0 or y > 1 then return false; end if;
  end loop;
  return true;
end $$;
revoke all on function public.wl_analytics_points_valid(jsonb,integer,integer) from public,anon,authenticated;

create or replace function public.wl_analytics_validate_rule(
  p_analytic_key text,
  p_rule_type text,
  p_classes text[],
  p_geometry jsonb,
  p_schedule_id uuid
) returns void
language plpgsql
stable
security definer
set search_path = public
as $$
declare classes text[] := coalesce(p_classes,array[]::text[]);
begin
  if not public.wl_analytics_valid_key(p_analytic_key) then
    raise exception 'invalid analytics goal';
  end if;
  if exists(select 1 from unnest(classes) c where c not in ('person','car','motorcycle')) then
    raise exception 'invalid object class';
  end if;

  if p_analytic_key = 'visitor_flow' then
    if p_rule_type <> 'line_crossing' or not (classes @> array['person']::text[] and classes <@ array['person']::text[])
       or not public.wl_analytics_points_valid(p_geometry,2,2) then
      raise exception 'Visitor Flow requires a two-point line and People only';
    end if;
  elsif p_analytic_key = 'vehicle_flow' then
    if p_rule_type <> 'line_crossing' or cardinality(classes)=0 or 'person'=any(classes)
       or not (classes <@ array['car','motorcycle']::text[])
       or not public.wl_analytics_points_valid(p_geometry,2,2) then
      raise exception 'Vehicle Flow requires a two-point line and Cars/Motorcycles only';
    end if;
  elsif p_analytic_key = 'boundary_monitoring' then
    if p_rule_type <> 'line_crossing' or cardinality(classes)=0
       or not public.wl_analytics_points_valid(p_geometry,2,2) then
      raise exception 'Boundary Monitoring requires a two-point line and at least one supported object class';
    end if;
  elsif p_analytic_key = 'zone_activity' then
    if p_rule_type <> 'zone_entry' or cardinality(classes)=0
       or not public.wl_analytics_points_valid(p_geometry,3,8) then
      raise exception 'Zone Activity requires a polygon with 3 to 8 points';
    end if;
  elsif p_analytic_key = 'dwell' then
    if p_rule_type <> 'zone_dwell' or not (classes @> array['person']::text[] and classes <@ array['person']::text[])
       or not public.wl_analytics_points_valid(p_geometry,3,8) then
      raise exception 'Dwell requires a 3 to 8 point zone and People only';
    end if;
  elsif p_analytic_key = 'checkout_activity' then
    if p_rule_type <> 'occupancy' or not (classes @> array['person']::text[] and classes <@ array['person']::text[])
       or not public.wl_analytics_points_valid(p_geometry,3,8) then
      raise exception 'Checkout Activity requires a 3 to 8 point checkout zone and People only';
    end if;
  elsif p_analytic_key = 'after_hours' then
    if p_rule_type <> 'schedule_activity' or cardinality(classes)=0 or p_schedule_id is null then
      raise exception 'After-Hours Activity requires a schedule and at least one supported object class';
    end if;
  end if;
end $$;
revoke all on function public.wl_analytics_validate_rule(text,text,text[],jsonb,uuid) from public,anon,authenticated;

-- ---------------------------------------------------------------------
-- Catalog: Site Health is an always-on platform capability, not a rule.
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
      jsonb_build_object('key','visitor_flow','label','Visitor Flow','rule_type','line_crossing','classes',jsonb_build_array('person'),'geometry','line'),
      jsonb_build_object('key','vehicle_flow','label','Vehicle Flow','rule_type','line_crossing','classes',jsonb_build_array('car','motorcycle'),'geometry','line'),
      jsonb_build_object('key','boundary_monitoring','label','Boundary Monitoring','rule_type','line_crossing','classes',jsonb_build_array('person','car','motorcycle'),'geometry','line'),
      jsonb_build_object('key','zone_activity','label','Zone Activity','rule_type','zone_entry','classes',jsonb_build_array('person','car','motorcycle'),'geometry','polygon'),
      jsonb_build_object('key','dwell','label','Dwell / Time in Zone','rule_type','zone_dwell','classes',jsonb_build_array('person'),'geometry','polygon'),
      jsonb_build_object('key','checkout_activity','label','Checkout Activity','rule_type','occupancy','classes',jsonb_build_array('person'),'geometry','polygon','note','Measures people present in the checkout zone, not completed transactions.'),
      jsonb_build_object('key','after_hours','label','After-Hours Activity','rule_type','schedule_activity','classes',jsonb_build_array('person','car','motorcycle'),'geometry','none')
    ),
    'always_on',jsonb_build_array(
      jsonb_build_object('key','site_health','label','Site Health','note','Always on. No monitoring rule is required.')
    ),
    'packs', jsonb_build_object(
      'retail', jsonb_build_array('visitor_flow','checkout_activity','after_hours'),
      'warehouse_logistics', jsonb_build_array('visitor_flow','vehicle_flow','boundary_monitoring','zone_activity','dwell','after_hours'),
      'manufacturing', jsonb_build_array('visitor_flow','vehicle_flow','boundary_monitoring','zone_activity','dwell','after_hours'),
      'office_commercial', jsonb_build_array('visitor_flow','dwell','after_hours'),
      'school_campus', jsonb_build_array('visitor_flow','vehicle_flow','boundary_monitoring','after_hours'),
      'parking_yard', jsonb_build_array('vehicle_flow','boundary_monitoring','zone_activity','after_hours'),
      'residential_community', jsonb_build_array('visitor_flow','vehicle_flow','boundary_monitoring','after_hours'),
      'custom', jsonb_build_array('visitor_flow','vehicle_flow','boundary_monitoring','zone_activity','dwell','after_hours')
    ),
    'purpose_recommendations', jsonb_build_object(
      'entrance_exit',jsonb_build_array('visitor_flow','after_hours'),
      'main_gate',jsonb_build_array('visitor_flow','vehicle_flow','boundary_monitoring','after_hours'),
      'reception',jsonb_build_array('visitor_flow','dwell','after_hours'),
      'checkout_till',jsonb_build_array('checkout_activity','after_hours'),
      'loading_bay',jsonb_build_array('vehicle_flow','zone_activity','dwell','after_hours'),
      'warehouse_floor',jsonb_build_array('zone_activity','dwell','after_hours'),
      'perimeter',jsonb_build_array('boundary_monitoring','after_hours'),
      'restricted_area',jsonb_build_array('zone_activity','dwell','after_hours'),
      'parking',jsonb_build_array('vehicle_flow','zone_activity','after_hours'),
      'office_floor',jsonb_build_array('zone_activity','after_hours'),
      'school_gate',jsonb_build_array('visitor_flow','vehicle_flow','boundary_monitoring','after_hours'),
      'corridor',jsonb_build_array('visitor_flow','after_hours'),
      'custom',jsonb_build_array('visitor_flow','vehicle_flow','boundary_monitoring','zone_activity','dwell','after_hours')
    )
  )
$$;
revoke all on function public.wl_analytics_catalog() from public,anon;
grant execute on function public.wl_analytics_catalog() to authenticated;

-- ---------------------------------------------------------------------
-- Portal write APIs: owner/admin only
-- ---------------------------------------------------------------------
create or replace function public.wl_set_site_type(p_site_id uuid, p_site_type text)
returns jsonb language plpgsql security definer set search_path=public as $$
declare v_tenant uuid; v_version bigint;
begin
  select tenant_id into v_tenant from sites where id=p_site_id;
  if v_tenant is null then raise exception 'not your site' using errcode='42501'; end if;
  perform public.wl_analytics_require_manager(v_tenant);
  if not wl_analytics_valid_site_type(p_site_type) then raise exception 'invalid site type'; end if;
  update sites set site_type=p_site_type where id=p_site_id;
  v_version:=wl_analytics_bump_site(p_site_id);
  return jsonb_build_object('ok',true,'version',v_version,'site_type',p_site_type);
end $$;

create or replace function public.wl_set_camera_profile(
  p_camera_id uuid,p_name text default null,p_purpose text default 'custom',p_enabled boolean default true
) returns jsonb language plpgsql security definer set search_path=public as $$
declare v_tenant uuid; v_site uuid; v_version bigint;
begin
  select tenant_id,site_id into v_tenant,v_site from cameras where id=p_camera_id;
  if v_tenant is null then raise exception 'not your camera' using errcode='42501'; end if;
  perform public.wl_analytics_require_manager(v_tenant);
  if not wl_analytics_valid_purpose(p_purpose) then raise exception 'invalid camera purpose'; end if;
  update cameras set name=coalesce(nullif(trim(p_name),''),name),purpose=p_purpose,
    analytics_enabled=coalesce(p_enabled,true) where id=p_camera_id;
  v_version:=wl_analytics_bump_site(v_site);
  return jsonb_build_object('ok',true,'version',v_version);
end $$;

create or replace function public.wl_upsert_monitoring_schedule(
  p_id uuid,p_site_id uuid,p_name text,p_timezone text,p_schedule jsonb,p_enabled boolean default true
) returns jsonb language plpgsql security definer set search_path=public as $$
declare v_tenant uuid; v_id uuid; v_version bigint;
begin
  select tenant_id into v_tenant from sites where id=p_site_id;
  if v_tenant is null then raise exception 'not your site' using errcode='42501'; end if;
  perform public.wl_analytics_require_manager(v_tenant);
  if p_id is null then
    insert into monitoring_schedules(tenant_id,site_id,name,timezone,schedule_json,enabled)
    values(v_tenant,p_site_id,coalesce(nullif(trim(p_name),''),'Schedule'),
      coalesce(nullif(trim(p_timezone),''),'Asia/Karachi'),coalesce(p_schedule,'{}'::jsonb),coalesce(p_enabled,true))
    returning id into v_id;
  else
    update monitoring_schedules set name=coalesce(nullif(trim(p_name),''),name),
      timezone=coalesce(nullif(trim(p_timezone),''),timezone),schedule_json=coalesce(p_schedule,schedule_json),
      enabled=coalesce(p_enabled,enabled),updated_at=now()
     where id=p_id and site_id=p_site_id and tenant_id=v_tenant returning id into v_id;
    if v_id is null then raise exception 'schedule not found' using errcode='42501'; end if;
  end if;
  v_version:=wl_analytics_bump_site(p_site_id);
  return jsonb_build_object('id',v_id,'version',v_version);
end $$;

create or replace function public.wl_upsert_monitoring_rule_v2(
  p_id uuid,
  p_camera_id uuid,
  p_analytic_key text,
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
set search_path=public
as $$
declare v_tenant uuid; v_site uuid; v_id uuid; v_version bigint; v_rule_version bigint; v_purpose text;
begin
  select tenant_id,site_id,purpose into v_tenant,v_site,v_purpose from cameras where id=p_camera_id;
  if v_tenant is null then raise exception 'not your camera' using errcode='42501'; end if;
  perform public.wl_analytics_require_manager(v_tenant);
  perform public.wl_analytics_validate_rule(p_analytic_key,p_rule_type,p_object_classes,p_geometry,p_schedule_id);
  if p_analytic_key='checkout_activity' and v_purpose <> 'checkout_till' then
    raise exception 'Checkout Activity is only available for a Checkout / Till camera';
  end if;
  if p_schedule_id is not null and not exists(
    select 1 from monitoring_schedules where id=p_schedule_id and site_id=v_site and tenant_id=v_tenant and enabled) then
    raise exception 'schedule not in this site' using errcode='42501';
  end if;
  if p_dwell_seconds is not null and p_dwell_seconds not between 1 and 86400 then
    raise exception 'dwell time must be between 1 and 86400 seconds';
  end if;
  if coalesce(p_sample_seconds,2.0) not between .5 and 60 then
    raise exception 'sample interval must be between 0.5 and 60 seconds';
  end if;
  if coalesce(p_severity,'measurement') not in ('measurement','info','attention','incident') then
    raise exception 'invalid severity';
  end if;

  v_version:=wl_analytics_bump_site(v_site);
  if p_id is null then
    insert into monitoring_rules(tenant_id,site_id,camera_id,analytic_key,name,rule_type,object_classes,
      geometry_json,direction_json,schedule_id,dwell_seconds,sample_seconds,enabled,severity,promote_incident,config_version)
    values(v_tenant,v_site,p_camera_id,p_analytic_key,coalesce(nullif(trim(p_name),''),'Monitoring rule'),p_rule_type,
      coalesce(p_object_classes,array['person']::text[]),coalesce(p_geometry,'{}'::jsonb),coalesce(p_direction,'{}'::jsonb),
      p_schedule_id,p_dwell_seconds,coalesce(p_sample_seconds,2.0),coalesce(p_enabled,true),coalesce(p_severity,'measurement'),
      coalesce(p_promote_incident,false),v_version)
    returning id,config_version into v_id,v_rule_version;
  else
    update monitoring_rules set analytic_key=p_analytic_key,name=coalesce(nullif(trim(p_name),''),name),
      rule_type=p_rule_type,object_classes=coalesce(p_object_classes,object_classes),
      geometry_json=coalesce(p_geometry,geometry_json),direction_json=coalesce(p_direction,direction_json),
      schedule_id=p_schedule_id,dwell_seconds=p_dwell_seconds,sample_seconds=coalesce(p_sample_seconds,sample_seconds),
      enabled=coalesce(p_enabled,enabled),severity=coalesce(p_severity,severity),
      promote_incident=coalesce(p_promote_incident,promote_incident),config_version=v_version,updated_at=now()
     where id=p_id and camera_id=p_camera_id and tenant_id=v_tenant and rule_type<>'health'
     returning id,config_version into v_id,v_rule_version;
    if v_id is null then raise exception 'rule not found' using errcode='42501'; end if;
  end if;
  return jsonb_build_object('id',v_id,'version',v_rule_version,'analytic_key',p_analytic_key);
end $$;

-- Compatibility wrapper for an older portal build. New UI uses v2 explicitly.
create or replace function public.wl_upsert_monitoring_rule(
  p_id uuid,p_camera_id uuid,p_name text,p_rule_type text,p_object_classes text[],p_geometry jsonb,
  p_direction jsonb default '{}'::jsonb,p_schedule_id uuid default null,p_dwell_seconds integer default null,
  p_sample_seconds numeric default 2.0,p_enabled boolean default true,p_severity text default 'measurement',
  p_promote_incident boolean default false
) returns jsonb
language plpgsql security definer set search_path=public as $$
declare k text;
begin
  k := case
    when p_rule_type='occupancy' then 'checkout_activity'
    when p_rule_type='zone_entry' then 'zone_activity'
    when p_rule_type='zone_dwell' then 'dwell'
    when p_rule_type='schedule_activity' then 'after_hours'
    when p_rule_type='health' then null
    when p_rule_type='line_crossing' and coalesce(p_object_classes,array[]::text[]) <@ array['person']::text[] then 'visitor_flow'
    when p_rule_type='line_crossing' and not ('person'=any(coalesce(p_object_classes,array[]::text[]))) then 'vehicle_flow'
    when p_rule_type='line_crossing' then 'boundary_monitoring'
    else null end;
  if k is null then raise exception 'Site Health is always on and does not use a monitoring rule'; end if;
  return public.wl_upsert_monitoring_rule_v2(p_id,p_camera_id,k,p_name,p_rule_type,p_object_classes,p_geometry,
    p_direction,p_schedule_id,p_dwell_seconds,p_sample_seconds,p_enabled,p_severity,p_promote_incident);
end $$;

create or replace function public.wl_delete_monitoring_rule(p_rule_id uuid)
returns jsonb language plpgsql security definer set search_path=public as $$
declare v_tenant uuid; v_site uuid; v_version bigint;
begin
  select tenant_id,site_id into v_tenant,v_site from monitoring_rules where id=p_rule_id and rule_type<>'health';
  if v_tenant is null then raise exception 'rule not found' using errcode='42501'; end if;
  perform public.wl_analytics_require_manager(v_tenant);
  delete from monitoring_rules where id=p_rule_id and rule_type<>'health';
  v_version:=wl_analytics_bump_site(v_site);
  return jsonb_build_object('ok',true,'version',v_version);
end $$;

create or replace function public.wl_delete_monitoring_schedule(p_schedule_id uuid)
returns jsonb language plpgsql security definer set search_path=public as $$
declare v_tenant uuid; v_site uuid; v_version bigint; v_refs int;
begin
  select tenant_id,site_id into v_tenant,v_site from monitoring_schedules where id=p_schedule_id;
  if v_tenant is null then raise exception 'schedule not found' using errcode='42501'; end if;
  perform public.wl_analytics_require_manager(v_tenant);
  select count(*) into v_refs from monitoring_rules where schedule_id=p_schedule_id and rule_type<>'health';
  if v_refs>0 then raise exception 'schedule is used by % monitoring rule(s); reassign those rules first',v_refs using errcode='23503'; end if;
  delete from monitoring_schedules where id=p_schedule_id;
  v_version:=wl_analytics_bump_site(v_site);
  return jsonb_build_object('ok',true,'version',v_version);
end $$;

create or replace function public.wl_request_config_snapshot(p_camera_id uuid)
returns jsonb language plpgsql security definer set search_path=public as $$
declare v_tenant uuid; v_site uuid; v_id uuid;
begin
  select tenant_id,site_id into v_tenant,v_site from cameras where id=p_camera_id;
  if v_tenant is null then raise exception 'not your camera' using errcode='42501'; end if;
  perform public.wl_analytics_require_manager(v_tenant);
  select id into v_id from camera_snapshot_requests where camera_id=p_camera_id and completed_at is null;
  if v_id is null then
    insert into camera_snapshot_requests(tenant_id,site_id,camera_id,requested_by)
    values(v_tenant,v_site,p_camera_id,auth.uid()) returning id into v_id;
  end if;
  return jsonb_build_object('request_id',v_id,'status','pending');
end $$;

-- ---------------------------------------------------------------------
-- Read payloads include semantic identity and current user's manage flag.
-- ---------------------------------------------------------------------
create or replace function public.wl_analytics_studio()
returns jsonb
language plpgsql
stable
security definer
set search_path=public
as $$
declare v_tenant uuid:=wl_my_tenant();
begin
  if v_tenant is null then return jsonb_build_object('sites','[]'::jsonb,'can_manage',false); end if;
  return jsonb_build_object(
    'can_manage',public.wl_analytics_can_manage(v_tenant),
    'sites',coalesce((select jsonb_agg(jsonb_build_object(
      'id',s.id,'name',s.name,'timezone',s.timezone,'site_type',s.site_type,
      'config_version',s.analytics_config_version,
      'cameras',coalesce((select jsonb_agg(jsonb_build_object(
        'id',c.id,'channel',c.channel,'name',c.name,'purpose',c.purpose,'analytics_enabled',c.analytics_enabled,
        'has_config_snapshot',exists(select 1 from camera_config_snapshots cs where cs.camera_id=c.id),
        'snapshot_requested',exists(select 1 from camera_snapshot_requests cr where cr.camera_id=c.id and cr.completed_at is null),
        'rules',coalesce((select jsonb_agg(jsonb_build_object(
          'id',r.id,'analytic_key',r.analytic_key,'name',r.name,'rule_type',r.rule_type,'object_classes',r.object_classes,
          'geometry',r.geometry_json,'direction',r.direction_json,'schedule_id',r.schedule_id,'dwell_seconds',r.dwell_seconds,
          'sample_seconds',r.sample_seconds,'enabled',r.enabled,'severity',r.severity,
          'promote_incident',r.promote_incident,'config_version',r.config_version) order by r.created_at)
          from monitoring_rules r where r.camera_id=c.id and r.rule_type<>'health'),'[]'::jsonb)
      ) order by c.channel) from cameras c where c.site_id=s.id),'[]'::jsonb),
      'schedules',coalesce((select jsonb_agg(jsonb_build_object(
        'id',ms.id,'name',ms.name,'timezone',ms.timezone,'schedule',ms.schedule_json,'enabled',ms.enabled,
        'rule_count',(select count(*) from monitoring_rules r where r.schedule_id=ms.id and r.rule_type<>'health')) order by ms.name)
        from monitoring_schedules ms where ms.site_id=s.id),'[]'::jsonb)
    ) order by s.name) from sites s where s.tenant_id=v_tenant),'[]'::jsonb)
  );
end $$;

create or replace function public.wl_agent_analytics_config(
  p_agent_id uuid,p_agent_key text,p_known_version bigint default 0
) returns jsonb
language plpgsql stable security definer set search_path=public as $$
declare v_agent agents; v_version bigint; v_config jsonb; v_requests jsonb;
begin
  v_agent:=wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then raise exception 'agent not recognised' using errcode='28000'; end if;
  select analytics_config_version into v_version from sites where id=v_agent.site_id;
  select coalesce(jsonb_agg(jsonb_build_object('request_id',cr.id,'camera_id',cr.camera_id,'channel',c.channel)
    order by cr.requested_at),'[]'::jsonb) into v_requests
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
      'rules',coalesce((select jsonb_agg(jsonb_build_object('id',r.id,'analytic_key',r.analytic_key,'name',r.name,
        'rule_type',r.rule_type,'object_classes',r.object_classes,'geometry',r.geometry_json,'direction',r.direction_json,
        'schedule_id',r.schedule_id,'dwell_seconds',r.dwell_seconds,'sample_seconds',r.sample_seconds,
        'severity',r.severity,'promote_incident',r.promote_incident))
        from monitoring_rules r where r.camera_id=c.id and r.enabled and r.rule_type<>'health'),'[]'::jsonb)
      ) order by c.channel) from cameras c where c.site_id=s.id and c.analytics_enabled),'[]'::jsonb)
  ) into v_config from sites s where s.id=v_agent.site_id;
  return jsonb_build_object('version',v_version,'changed',true,'config',v_config,'snapshot_requests',v_requests);
end $$;

-- ---------------------------------------------------------------------
-- Agent ingest copies server-owned semantic identity onto every measurement.
-- ---------------------------------------------------------------------
create or replace function public.wl_ingest_analytic_events(
  p_agent_id uuid,p_agent_key text,p_events jsonb
) returns jsonb
language plpgsql security definer set search_path=public as $$
declare v_agent agents; v_received int; v_inserted int; v_promoted int:=0;
begin
  v_agent:=wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then raise exception 'agent not recognised' using errcode='28000'; end if;
  select count(*) into v_received from jsonb_array_elements(coalesce(p_events,'[]'::jsonb));
  with incoming as (
    select e,nullif(e->>'rule_id','')::uuid as rule_id,e->>'channel' as channel,
      coalesce(nullif(e->>'event_type',''),'measurement') as event_type,nullif(e->>'object_class','') as object_class,
      nullif(e->>'track_key','') as track_key,nullif(e->>'direction','') as direction,
      (e->>'occurred_at')::timestamptz as occurred_at,nullif(e->>'duration_seconds','')::numeric as duration_seconds,
      coalesce(nullif(e->>'dedupe_key',''),md5(e::text)) as dedupe_key,coalesce(e->'metadata','{}'::jsonb) as metadata
    from jsonb_array_elements(coalesce(p_events,'[]'::jsonb)) e
    where e->>'occurred_at' is not null and e->>'channel' is not null
  ), valid as (
    select i.*,c.id as camera_id,r.analytic_key,r.promote_incident,r.severity,r.name as rule_name
    from incoming i join cameras c on c.site_id=v_agent.site_id and c.channel=i.channel
    join monitoring_rules r on r.id=i.rule_id and r.camera_id=c.id and r.site_id=v_agent.site_id and r.enabled and r.rule_type<>'health'
    where i.object_class is null or i.object_class in ('person','car','motorcycle')
  ), ins as (
    insert into analytic_events(tenant_id,site_id,camera_id,agent_id,monitoring_rule_id,analytic_key,event_type,
      object_class,track_key,direction,occurred_at,duration_seconds,dedupe_key,metadata_json)
    select v_agent.tenant_id,v_agent.site_id,v.camera_id,v_agent.id,v.rule_id,v.analytic_key,v.event_type,
      v.object_class,v.track_key,v.direction,v.occurred_at,v.duration_seconds,v.dedupe_key,v.metadata
    from valid v on conflict(tenant_id,dedupe_key) do nothing
    returning id
  ) select count(*) into v_inserted from ins;

  with candidates as (
    select ae.*,r.name as rule_name,r.severity from analytic_events ae
    join monitoring_rules r on r.id=ae.monitoring_rule_id
    where ae.agent_id=v_agent.id and ae.received_at>now()-interval '2 minutes' and r.promote_incident
  ), put as (
    insert into events(tenant_id,site_id,camera_id,agent_id,event_type,device_ts,agent_ts,dedupe_key,payload)
    select v_agent.tenant_id,v_agent.site_id,c.camera_id,v_agent.id,'analytic_'||c.event_type,c.occurred_at,now(),
      'analytics:'||c.dedupe_key,jsonb_build_object('analytic_event_id',c.id,'analytic_key',c.analytic_key,
      'rule_id',c.monitoring_rule_id,'rule',c.rule_name,'severity',c.severity,'object_class',c.object_class,
      'direction',c.direction,'duration_seconds',c.duration_seconds,'metadata',c.metadata_json)
    from candidates c on conflict(tenant_id,dedupe_key) do nothing returning 1
  ) select count(*) into v_promoted from put;
  update agents set last_seen_at=now() where id=v_agent.id;
  return jsonb_build_object('received',v_received,'inserted',v_inserted,'skipped',v_received-v_inserted,
    'promoted_incidents',v_promoted,'server_time',now());
end $$;

-- ---------------------------------------------------------------------
-- Semantic aggregates. A boundary line no longer inflates Visitor Flow.
-- ---------------------------------------------------------------------
create or replace function public.wl_analytics_overview(
  p_days int default 7,p_site_id uuid default null
) returns jsonb
language plpgsql stable security definer set search_path=public as $$
declare v_tenant uuid:=wl_my_tenant(); v_days int:=least(greatest(coalesce(p_days,7),1),90);
begin
  if v_tenant is null then return jsonb_build_object('summary','{}'::jsonb,'daily','[]'::jsonb,'by_rule','[]'::jsonb); end if;
  if p_site_id is not null and not exists(select 1 from sites where id=p_site_id and tenant_id=v_tenant) then
    raise exception 'not your site' using errcode='42501';
  end if;
  return jsonb_build_object(
    'window_days',v_days,
    'summary',jsonb_build_object(
      'visitor_in',(select count(*) from analytic_events ae where ae.tenant_id=v_tenant and (p_site_id is null or ae.site_id=p_site_id)
        and ae.occurred_at>=now()-make_interval(days=>v_days) and ae.analytic_key='visitor_flow' and ae.direction='in'),
      'visitor_out',(select count(*) from analytic_events ae where ae.tenant_id=v_tenant and (p_site_id is null or ae.site_id=p_site_id)
        and ae.occurred_at>=now()-make_interval(days=>v_days) and ae.analytic_key='visitor_flow' and ae.direction='out'),
      'vehicles_in',(select count(*) from analytic_events ae where ae.tenant_id=v_tenant and (p_site_id is null or ae.site_id=p_site_id)
        and ae.occurred_at>=now()-make_interval(days=>v_days) and ae.analytic_key='vehicle_flow' and ae.direction='in'),
      'vehicles_out',(select count(*) from analytic_events ae where ae.tenant_id=v_tenant and (p_site_id is null or ae.site_id=p_site_id)
        and ae.occurred_at>=now()-make_interval(days=>v_days) and ae.analytic_key='vehicle_flow' and ae.direction='out'),
      'zone_entries',(select count(*) from analytic_events ae where ae.tenant_id=v_tenant and (p_site_id is null or ae.site_id=p_site_id)
        and ae.occurred_at>=now()-make_interval(days=>v_days) and ae.analytic_key='zone_activity'),
      'after_hours',(select count(*) from analytic_events ae where ae.tenant_id=v_tenant and (p_site_id is null or ae.site_id=p_site_id)
        and ae.occurred_at>=now()-make_interval(days=>v_days) and ae.analytic_key='after_hours'),
      'checkout_peak',coalesce((select max((ae.metadata_json->>'count')::int) from analytic_events ae
        where ae.tenant_id=v_tenant and (p_site_id is null or ae.site_id=p_site_id)
          and ae.occurred_at>=now()-make_interval(days=>v_days) and ae.analytic_key='checkout_activity'
          and (ae.metadata_json->>'count')~'^[0-9]+$'),0)
    ),
    'daily',coalesce((select jsonb_agg(to_jsonb(x) order by x.day) from (
      select (ae.occurred_at at time zone coalesce(s.timezone,'Asia/Karachi'))::date as day,
        count(*) filter(where ae.analytic_key='visitor_flow' and ae.direction='in') as visitor_in,
        count(*) filter(where ae.analytic_key='visitor_flow' and ae.direction='out') as visitor_out,
        count(*) filter(where ae.analytic_key='vehicle_flow' and ae.direction='in') as vehicles_in,
        count(*) filter(where ae.analytic_key='vehicle_flow' and ae.direction='out') as vehicles_out,
        count(*) filter(where ae.analytic_key='zone_activity') as zone_entries,
        count(*) filter(where ae.analytic_key='after_hours') as after_hours,
        coalesce(max((ae.metadata_json->>'count')::int) filter(where ae.analytic_key='checkout_activity' and (ae.metadata_json->>'count')~'^[0-9]+$'),0) as checkout_peak
      from analytic_events ae join sites s on s.id=ae.site_id
      where ae.tenant_id=v_tenant and (p_site_id is null or ae.site_id=p_site_id)
        and ae.occurred_at>=now()-make_interval(days=>v_days)
      group by 1 order by 1) x),'[]'::jsonb),
    'by_rule',coalesce((select jsonb_agg(to_jsonb(x) order by x.count desc) from (
      select r.id as rule_id,r.analytic_key,r.name,r.rule_type,c.name as camera,s.name as site,count(*) as count
      from analytic_events ae join monitoring_rules r on r.id=ae.monitoring_rule_id
      join cameras c on c.id=ae.camera_id join sites s on s.id=ae.site_id
      where ae.tenant_id=v_tenant and (p_site_id is null or ae.site_id=p_site_id)
        and ae.occurred_at>=now()-make_interval(days=>v_days)
      group by r.id,r.analytic_key,r.name,r.rule_type,c.name,s.name order by count(*) desc limit 20) x),'[]'::jsonb)
  );
end $$;

create or replace function public.wl_daily_analytics(p_site_id uuid,p_date date)
returns jsonb
language plpgsql stable security definer set search_path=public as $$
declare v_site sites; v_start timestamptz; v_end timestamptz;
begin
  select * into v_site from sites where id=p_site_id;
  if v_site.id is null then raise exception 'no such site' using errcode='22023'; end if;
  v_start:=(p_date::text||' 00:00:00')::timestamp at time zone v_site.timezone;
  v_end:=v_start+interval '1 day';
  return jsonb_build_object(
    'visitor_in',(select count(*) from analytic_events where site_id=v_site.id and occurred_at>=v_start and occurred_at<v_end and analytic_key='visitor_flow' and direction='in'),
    'visitor_out',(select count(*) from analytic_events where site_id=v_site.id and occurred_at>=v_start and occurred_at<v_end and analytic_key='visitor_flow' and direction='out'),
    'vehicles_in',(select count(*) from analytic_events where site_id=v_site.id and occurred_at>=v_start and occurred_at<v_end and analytic_key='vehicle_flow' and direction='in'),
    'vehicles_out',(select count(*) from analytic_events where site_id=v_site.id and occurred_at>=v_start and occurred_at<v_end and analytic_key='vehicle_flow' and direction='out'),
    'zone_entries',(select count(*) from analytic_events where site_id=v_site.id and occurred_at>=v_start and occurred_at<v_end and analytic_key='zone_activity'),
    'after_hours',(select count(*) from analytic_events where site_id=v_site.id and occurred_at>=v_start and occurred_at<v_end and analytic_key='after_hours'),
    'checkout_peak',coalesce((select max((metadata_json->>'count')::int) from analytic_events where site_id=v_site.id
      and occurred_at>=v_start and occurred_at<v_end and analytic_key='checkout_activity' and (metadata_json->>'count')~'^[0-9]+$'),0),
    'measurements',(select count(*) from analytic_events where site_id=v_site.id and occurred_at>=v_start and occurred_at<v_end),
    'by_rule',coalesce((select jsonb_agg(to_jsonb(x) order by x.count desc) from (
      select r.id as rule_id,r.analytic_key,r.name,r.rule_type,count(*) as count
      from analytic_events ae join monitoring_rules r on r.id=ae.monitoring_rule_id
      where ae.site_id=v_site.id and ae.occurred_at>=v_start and ae.occurred_at<v_end
      group by r.id,r.analytic_key,r.name,r.rule_type order by count(*) desc) x),'[]'::jsonb)
  );
end $$;

-- ---------------------------------------------------------------------
-- Grants. Read APIs remain available to all tenant members; configuration
-- functions perform their own owner/admin checks.
-- ---------------------------------------------------------------------
revoke all on function public.wl_upsert_monitoring_rule_v2(uuid,uuid,text,text,text,text[],jsonb,jsonb,uuid,integer,numeric,boolean,text,boolean) from public,anon;
grant execute on function public.wl_upsert_monitoring_rule_v2(uuid,uuid,text,text,text,text[],jsonb,jsonb,uuid,integer,numeric,boolean,text,boolean) to authenticated;

-- Reassert existing authenticated surfaces after CREATE OR REPLACE.
revoke all on function public.wl_set_site_type(uuid,text) from public,anon;
revoke all on function public.wl_set_camera_profile(uuid,text,text,boolean) from public,anon;
revoke all on function public.wl_upsert_monitoring_schedule(uuid,uuid,text,text,jsonb,boolean) from public,anon;
revoke all on function public.wl_upsert_monitoring_rule(uuid,uuid,text,text,text[],jsonb,jsonb,uuid,integer,numeric,boolean,text,boolean) from public,anon;
revoke all on function public.wl_delete_monitoring_rule(uuid) from public,anon;
revoke all on function public.wl_delete_monitoring_schedule(uuid) from public,anon;
revoke all on function public.wl_request_config_snapshot(uuid) from public,anon;

grant execute on function public.wl_set_site_type(uuid,text) to authenticated;
grant execute on function public.wl_set_camera_profile(uuid,text,text,boolean) to authenticated;
grant execute on function public.wl_upsert_monitoring_schedule(uuid,uuid,text,text,jsonb,boolean) to authenticated;
grant execute on function public.wl_upsert_monitoring_rule(uuid,uuid,text,text,text[],jsonb,jsonb,uuid,integer,numeric,boolean,text,boolean) to authenticated;
grant execute on function public.wl_delete_monitoring_rule(uuid) to authenticated;
grant execute on function public.wl_delete_monitoring_schedule(uuid) to authenticated;
grant execute on function public.wl_request_config_snapshot(uuid) to authenticated;
