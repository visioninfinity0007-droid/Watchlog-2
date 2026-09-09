-- ===================================================================
-- 0057 — Generic Operations-Intelligence rule authoring (first-class primitives).
--
-- The engine (analytics.py) has long supported the full primitive set, and 0056 ships the
-- governance layer to the runtime, but the customer-facing AUTHORING contract only accepted the
-- seven established analytics goals — so line_crossing/zone_entry were reachable while zone_exit,
-- presence, absence, generic occupancy min/max, generic schedule activity, vehicle activity and
-- queue/wait were not authorable and failed at save.
--
-- This upgrades the NORMAL contract (wl_upsert_monitoring_rule_v2 + wl_analytics_validate_rule +
-- catalog + studio read model) rather than adding a parallel RPC. Every primitive becomes a
-- first-class, generically-authorable rule with STRICT validation (an impossible configuration
-- fails at save, never silently produces a useless rule). No vertical/industry hardcoding — the
-- primitives are geometry + class + schedule + threshold, nothing domain-specific.
--
-- Governance (confidence/cooldown/evidence/actions/sensitive/review/severity/classes) stays
-- attached via the existing wl_set_rule_governance path (0053) and now travels to the agent (0056).
-- ===================================================================

-- ---------------------------------------------------------------------
-- 1. Widen the analytic_key allow-lists (rules + events) with the generic primitive keys.
--    The seven established goals stay valid (existing rules unaffected).
-- ---------------------------------------------------------------------
alter table public.monitoring_rules  drop constraint if exists monitoring_rules_analytic_key_chk;
alter table public.monitoring_rules  add  constraint monitoring_rules_analytic_key_chk check (
  analytic_key in ('visitor_flow','vehicle_flow','boundary_monitoring','zone_activity','dwell',
    'checkout_activity','after_hours','site_health','custom',
    'line_crossing','zone_entry','zone_exit','zone_presence','zone_absence','zone_dwell',
    'occupancy','schedule_activity','vehicle_activity','queue_wait'));
alter table public.analytic_events   drop constraint if exists analytic_events_analytic_key_chk;
alter table public.analytic_events   add  constraint analytic_events_analytic_key_chk check (
  analytic_key in ('visitor_flow','vehicle_flow','boundary_monitoring','zone_activity','dwell',
    'checkout_activity','after_hours','site_health','custom',
    'line_crossing','zone_entry','zone_exit','zone_presence','zone_absence','zone_dwell',
    'occupancy','schedule_activity','vehicle_activity','queue_wait'));

-- ---------------------------------------------------------------------
-- 2. Valid-key predicate.
-- ---------------------------------------------------------------------
create or replace function public.wl_analytics_valid_key(p text)
returns boolean language sql immutable as $$
  select p in ('visitor_flow','vehicle_flow','boundary_monitoring','zone_activity','dwell',
    'checkout_activity','after_hours',
    'line_crossing','zone_entry','zone_exit','zone_presence','zone_absence','zone_dwell',
    'occupancy','schedule_activity','vehicle_activity','queue_wait')
$$;
revoke all on function public.wl_analytics_valid_key(text) from public,anon,authenticated;

-- ---------------------------------------------------------------------
-- 3. Rule validation — established goals PLUS the generic primitives. Type/geometry/class/schedule
--    checks live here (signature unchanged); threshold/duration checks that need the numeric params
--    run in the upsert below. A primitive that reaches the engine but is mis-configured is rejected.
-- ---------------------------------------------------------------------
create or replace function public.wl_analytics_validate_rule(
  p_analytic_key text, p_rule_type text, p_classes text[], p_geometry jsonb, p_schedule_id uuid
) returns void language plpgsql stable security definer set search_path = public as $$
declare classes text[] := coalesce(p_classes, array[]::text[]);
begin
  if not public.wl_analytics_valid_key(p_analytic_key) then
    raise exception 'invalid analytics goal';
  end if;
  if exists(select 1 from unnest(classes) c where c not in ('person','car','motorcycle')) then
    raise exception 'invalid object class';
  end if;

  -- established preset goals (unchanged) -------------------------------------------------
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

  -- generic operations primitives -------------------------------------------------------
  elsif p_analytic_key = 'line_crossing' then
    if p_rule_type <> 'line_crossing' or cardinality(classes)=0
       or not public.wl_analytics_points_valid(p_geometry,2,2) then
      raise exception 'Line Crossing needs a two-point line and at least one object type';
    end if;
  elsif p_analytic_key = 'zone_entry' then
    if p_rule_type <> 'zone_entry' or cardinality(classes)=0
       or not public.wl_analytics_points_valid(p_geometry,3,8) then
      raise exception 'Zone Entry needs a 3 to 8 point zone and at least one object type';
    end if;
  elsif p_analytic_key = 'zone_exit' then
    if p_rule_type <> 'zone_exit' or cardinality(classes)=0
       or not public.wl_analytics_points_valid(p_geometry,3,8) then
      raise exception 'Zone Exit needs a 3 to 8 point zone and at least one object type';
    end if;
  elsif p_analytic_key = 'zone_presence' then
    -- "expected presence": the zone should be occupied during a schedule window.
    if p_rule_type <> 'zone_presence' or cardinality(classes)=0
       or not public.wl_analytics_points_valid(p_geometry,3,8) or p_schedule_id is null then
      raise exception 'Expected Presence needs a 3 to 8 point zone, an object type and an expected schedule';
    end if;
  elsif p_analytic_key = 'zone_absence' then
    if p_rule_type <> 'zone_absence' or cardinality(classes)=0
       or not public.wl_analytics_points_valid(p_geometry,3,8) then
      raise exception 'Absence needs a 3 to 8 point zone and at least one object type';
    end if;
  elsif p_analytic_key = 'zone_dwell' then
    if p_rule_type <> 'zone_dwell' or cardinality(classes)=0
       or not public.wl_analytics_points_valid(p_geometry,3,8) then
      raise exception 'Dwell needs a 3 to 8 point zone and at least one object type';
    end if;
  elsif p_analytic_key = 'occupancy' then
    if p_rule_type <> 'occupancy' or cardinality(classes)=0
       or not public.wl_analytics_points_valid(p_geometry,3,8) then
      raise exception 'Occupancy needs a 3 to 8 point zone and at least one object type';
    end if;
  elsif p_analytic_key = 'schedule_activity' then
    if p_rule_type <> 'schedule_activity' or cardinality(classes)=0 or p_schedule_id is null then
      raise exception 'Schedule Activity needs a schedule and at least one object type';
    end if;
  elsif p_analytic_key = 'vehicle_activity' then
    if p_rule_type <> 'vehicle_activity' or cardinality(classes)=0 or 'person'=any(classes)
       or not (classes <@ array['car','motorcycle']::text[])
       or not public.wl_analytics_points_valid(p_geometry,3,8) then
      raise exception 'Vehicle Activity needs a 3 to 8 point zone and Cars/Motorcycles only';
    end if;
  elsif p_analytic_key = 'queue_wait' then
    if p_rule_type <> 'queue_wait' or not (classes @> array['person']::text[] and classes <@ array['person']::text[])
       or not public.wl_analytics_points_valid(p_geometry,3,8) then
      raise exception 'Queue / Wait needs a 3 to 8 point queue zone and People only';
    end if;
  end if;
end $$;
revoke all on function public.wl_analytics_validate_rule(text,text,text[],jsonb,uuid) from public,anon,authenticated;

-- ---------------------------------------------------------------------
-- 4. Upsert v2 — adds occupancy_min/occupancy_max and the strict threshold/duration checks the
--    numeric params make possible. Dropped + recreated (signature widened by two trailing params;
--    the 14-arg compat wrapper and any 14-arg caller still bind via defaults).
-- ---------------------------------------------------------------------
drop function if exists public.wl_upsert_monitoring_rule_v2(
  uuid,uuid,text,text,text,text[],jsonb,jsonb,uuid,integer,numeric,boolean,text,boolean);

create or replace function public.wl_upsert_monitoring_rule_v2(
  p_id uuid, p_camera_id uuid, p_analytic_key text, p_name text, p_rule_type text,
  p_object_classes text[], p_geometry jsonb, p_direction jsonb default '{}'::jsonb,
  p_schedule_id uuid default null, p_dwell_seconds integer default null,
  p_sample_seconds numeric default 2.0, p_enabled boolean default true,
  p_severity text default 'measurement', p_promote_incident boolean default false,
  p_occupancy_min integer default null, p_occupancy_max integer default null
) returns jsonb
language plpgsql security definer set search_path = public as $$
declare v_tenant uuid; v_site uuid; v_id uuid; v_version bigint; v_rule_version bigint; v_purpose text;
begin
  select tenant_id, site_id, purpose into v_tenant, v_site, v_purpose from cameras where id = p_camera_id;
  if v_tenant is null then raise exception 'not your camera' using errcode = '42501'; end if;
  perform public.wl_analytics_require_manager(v_tenant);
  perform public.wl_analytics_validate_rule(p_analytic_key, p_rule_type, p_object_classes, p_geometry, p_schedule_id);

  -- checkout_activity keeps its retail affordance; the generic 'occupancy' primitive does not.
  if p_analytic_key = 'checkout_activity' and v_purpose <> 'checkout_till' then
    raise exception 'Checkout Activity is only available for a Checkout / Till camera';
  end if;
  if p_schedule_id is not null and not exists(
    select 1 from monitoring_schedules where id = p_schedule_id and site_id = v_site and tenant_id = v_tenant and enabled) then
    raise exception 'schedule not in this site' using errcode = '42501';
  end if;
  if p_dwell_seconds is not null and p_dwell_seconds not between 1 and 86400 then
    raise exception 'dwell time must be between 1 and 86400 seconds';
  end if;
  if coalesce(p_sample_seconds, 2.0) not between .5 and 60 then
    raise exception 'sample interval must be between 0.5 and 60 seconds';
  end if;
  if coalesce(p_severity, 'measurement') not in ('measurement','info','attention','incident') then
    raise exception 'invalid severity';
  end if;

  -- occupancy min/max bounds and coherence
  if p_occupancy_min is not null and (p_occupancy_min < 0 or p_occupancy_min > 100000) then
    raise exception 'occupancy minimum must be between 0 and 100000';
  end if;
  if p_occupancy_max is not null and (p_occupancy_max < 0 or p_occupancy_max > 100000) then
    raise exception 'occupancy maximum must be between 0 and 100000';
  end if;
  if p_occupancy_min is not null and p_occupancy_max is not null and p_occupancy_min > p_occupancy_max then
    raise exception 'occupancy minimum cannot exceed the maximum';
  end if;

  -- primitive-specific required parameters (fail here, not silently at runtime)
  if p_analytic_key = 'occupancy' and p_occupancy_min is null and p_occupancy_max is null then
    raise exception 'Occupancy needs a minimum, a maximum, or both';
  end if;
  if p_analytic_key in ('zone_presence','zone_absence') and p_dwell_seconds is null then
    raise exception 'This rule needs a duration (how long before it counts)';
  end if;
  if p_analytic_key = 'queue_wait' then
    if p_dwell_seconds is null then raise exception 'Queue / Wait needs a wait-time threshold'; end if;
    if coalesce(p_occupancy_min,0) < 1 then raise exception 'Queue / Wait needs a minimum queue length of at least 1'; end if;
  end if;

  v_version := wl_analytics_bump_site(v_site);
  if p_id is null then
    insert into monitoring_rules(tenant_id, site_id, camera_id, analytic_key, name, rule_type, object_classes,
      geometry_json, direction_json, schedule_id, dwell_seconds, sample_seconds, enabled, severity,
      promote_incident, occupancy_min, occupancy_max, config_version)
    values(v_tenant, v_site, p_camera_id, p_analytic_key, coalesce(nullif(trim(p_name),''),'Monitoring rule'), p_rule_type,
      coalesce(p_object_classes, array['person']::text[]), coalesce(p_geometry,'{}'::jsonb), coalesce(p_direction,'{}'::jsonb),
      p_schedule_id, p_dwell_seconds, coalesce(p_sample_seconds,2.0), coalesce(p_enabled,true), coalesce(p_severity,'measurement'),
      coalesce(p_promote_incident,false), p_occupancy_min, p_occupancy_max, v_version)
    returning id, config_version into v_id, v_rule_version;
  else
    update monitoring_rules set analytic_key = p_analytic_key, name = coalesce(nullif(trim(p_name),''),name),
      rule_type = p_rule_type, object_classes = coalesce(p_object_classes, object_classes),
      geometry_json = coalesce(p_geometry, geometry_json), direction_json = coalesce(p_direction, direction_json),
      schedule_id = p_schedule_id, dwell_seconds = p_dwell_seconds, sample_seconds = coalesce(p_sample_seconds, sample_seconds),
      enabled = coalesce(p_enabled, enabled), severity = coalesce(p_severity, severity),
      promote_incident = coalesce(p_promote_incident, promote_incident),
      occupancy_min = p_occupancy_min, occupancy_max = p_occupancy_max, config_version = v_version, updated_at = now()
     where id = p_id and camera_id = p_camera_id and tenant_id = v_tenant and rule_type <> 'health'
     returning id, config_version into v_id, v_rule_version;
    if v_id is null then raise exception 'rule not found' using errcode = '42501'; end if;
  end if;
  return jsonb_build_object('id', v_id, 'version', v_rule_version, 'analytic_key', p_analytic_key);
end $$;
revoke all on function public.wl_upsert_monitoring_rule_v2(
  uuid,uuid,text,text,text,text[],jsonb,jsonb,uuid,integer,numeric,boolean,text,boolean,integer,integer) from public,anon;
grant execute on function public.wl_upsert_monitoring_rule_v2(
  uuid,uuid,text,text,text,text[],jsonb,jsonb,uuid,integer,numeric,boolean,text,boolean,integer,integer) to authenticated;

-- ---------------------------------------------------------------------
-- 5. Catalog — a generic 'primitives' list with the control metadata the Studio needs to show ONLY
--    the fields relevant to a chosen primitive. Kept industry-neutral. The preset 'analytics' goals
--    remain for the guided packs; 'primitives' is the full generic authoring surface.
-- ---------------------------------------------------------------------
create or replace function public.wl_operations_primitives()
returns jsonb language sql immutable as $$
  select jsonb_build_array(
    jsonb_build_object('key','line_crossing','label','Line crossing','rule_type','line_crossing',
      'geometry','line','classes','any','controls',jsonb_build_array('direction')),
    jsonb_build_object('key','zone_entry','label','Zone entry','rule_type','zone_entry',
      'geometry','polygon','classes','any','controls',jsonb_build_array()),
    jsonb_build_object('key','zone_exit','label','Zone exit','rule_type','zone_exit',
      'geometry','polygon','classes','any','controls',jsonb_build_array()),
    jsonb_build_object('key','zone_presence','label','Expected presence','rule_type','zone_presence',
      'geometry','polygon','classes','any','controls',jsonb_build_array('schedule','duration')),
    jsonb_build_object('key','zone_absence','label','Absence','rule_type','zone_absence',
      'geometry','polygon','classes','any','controls',jsonb_build_array('duration')),
    jsonb_build_object('key','zone_dwell','label','Dwell / time in zone','rule_type','zone_dwell',
      'geometry','polygon','classes','any','controls',jsonb_build_array('dwell')),
    jsonb_build_object('key','occupancy','label','Occupancy (min / max)','rule_type','occupancy',
      'geometry','polygon','classes','any','controls',jsonb_build_array('occupancy')),
    jsonb_build_object('key','queue_wait','label','Queue / wait','rule_type','queue_wait',
      'geometry','polygon','classes','person','controls',jsonb_build_array('occupancy_min','dwell')),
    jsonb_build_object('key','schedule_activity','label','Schedule activity','rule_type','schedule_activity',
      'geometry','none','classes','any','controls',jsonb_build_array('schedule')),
    jsonb_build_object('key','after_hours','label','After-hours activity','rule_type','schedule_activity',
      'geometry','none','classes','any','controls',jsonb_build_array('schedule')),
    jsonb_build_object('key','vehicle_activity','label','Vehicle activity','rule_type','vehicle_activity',
      'geometry','polygon','classes','vehicle','controls',jsonb_build_array('schedule'))
  )
$$;
revoke all on function public.wl_operations_primitives() from public,anon;
grant execute on function public.wl_operations_primitives() to authenticated;

-- ---------------------------------------------------------------------
-- 6. Studio read model — surface occupancy_min/max and the governance layer on each rule so the
--    portal can display/edit operations rules without a second query.
-- ---------------------------------------------------------------------
create or replace function public.wl_analytics_studio()
returns jsonb language plpgsql stable security definer set search_path = public as $$
declare v_tenant uuid := wl_my_tenant();
begin
  if v_tenant is null then return jsonb_build_object('sites','[]'::jsonb,'can_manage',false); end if;
  return jsonb_build_object(
    'can_manage', public.wl_analytics_can_manage(v_tenant),
    'primitives', public.wl_operations_primitives(),
    'sites', coalesce((select jsonb_agg(jsonb_build_object(
      'id',s.id,'name',s.name,'timezone',s.timezone,'site_type',s.site_type,
      'config_version',s.analytics_config_version,
      'cameras', coalesce((select jsonb_agg(jsonb_build_object(
        'id',c.id,'channel',c.channel,'name',c.name,'purpose',c.purpose,'analytics_enabled',c.analytics_enabled,
        'has_config_snapshot',exists(select 1 from camera_config_snapshots cs where cs.camera_id=c.id),
        'snapshot_requested',exists(select 1 from camera_snapshot_requests cr where cr.camera_id=c.id and cr.completed_at is null),
        'rules', coalesce((select jsonb_agg(jsonb_build_object(
          'id',r.id,'analytic_key',r.analytic_key,'name',r.name,'rule_type',r.rule_type,'object_classes',r.object_classes,
          'geometry',r.geometry_json,'direction',r.direction_json,'schedule_id',r.schedule_id,'dwell_seconds',r.dwell_seconds,
          'sample_seconds',r.sample_seconds,'enabled',r.enabled,'severity',r.severity,'promote_incident',r.promote_incident,
          'occupancy_min',r.occupancy_min,'occupancy_max',r.occupancy_max,
          'confidence_min',r.confidence_min,'cooldown_seconds',r.cooldown_seconds,'actions',r.actions,
          'sensitive',r.sensitive,'review_required',r.review_required,'config_version',r.config_version) order by r.created_at)
          from monitoring_rules r where r.camera_id=c.id and r.rule_type<>'health'),'[]'::jsonb)
      ) order by c.channel) from cameras c where c.site_id=s.id),'[]'::jsonb),
      'schedules', coalesce((select jsonb_agg(jsonb_build_object(
        'id',ms.id,'name',ms.name,'timezone',ms.timezone,'schedule',ms.schedule_json,'enabled',ms.enabled,
        'rule_count',(select count(*) from monitoring_rules r where r.schedule_id=ms.id and r.rule_type<>'health')) order by ms.name)
        from monitoring_schedules ms where ms.site_id=s.id),'[]'::jsonb)
    ) order by s.name) from sites s where s.tenant_id=v_tenant),'[]'::jsonb)
  );
end $$;
