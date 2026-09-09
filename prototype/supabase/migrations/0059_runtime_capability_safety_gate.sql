-- ===================================================================
-- 0059 — Runtime capability model + production-safe Operations gate.
--
-- The new server (0054-0058) can expose features that require a NEW agent runtime, while the
-- production site still runs the OLD agent. This migration makes sure nothing behaves as if a
-- feature works until a COMPATIBLE agent actually reports it, and that merely APPLYING 0054-0058
-- changes no existing customer's behaviour.
--
--   1. Capability model — the agent EXPLICITLY advertises what its runtime can execute
--      (operations_runtime, operations_extended_primitives, operations_evidence_still,
--       operations_evidence_clip, archive_processing, multi_agent_fencing, recorder_probe_v2).
--      Agents that do not advertise (all current/old agents) resolve to UNSUPPORTED — never
--      assumed. This is a RUNTIME-execution capability, kept separate from field-proven hardware
--      support (vendor_capabilities.py).
--
--   2. Operations gate — sites.operations_runtime_enabled DEFAULT FALSE. The 0054 live bridge does
--      NOTHING while false, so applying the migration cannot suddenly create incidents on an
--      existing customer. Enabling is explicit AND requires a compatible operations_runtime
--      capability. Multi-agent stays independently OFF.
--
--   3. Evidence gate — the emitter creates a still/clip task ONLY when the site advertises the
--      matching evidence capability, so an old-agent site never accumulates misleading pending
--      evidence tasks.
-- ===================================================================

-- ---------------------------------------------------------------------
-- 1. Capability storage + explicit advertisement.
-- ---------------------------------------------------------------------
alter table public.agents
  add column if not exists capabilities jsonb not null default '[]'::jsonb,
  add column if not exists capabilities_reported_at timestamptz;

-- The canonical capability vocabulary. An advertised value outside this set is ignored (never
-- silently trusted). Adding a class here is a deliberate product decision.
create or replace function public.wl_known_capabilities()
returns text[] language sql immutable as $$
  select array['operations_runtime','operations_extended_primitives','operations_evidence_still',
               'operations_evidence_clip','archive_processing','multi_agent_fencing','recorder_probe_v2']
$$;

-- The agent advertises its runtime capabilities. OLD agents never call this, so their capabilities
-- stay '[]' => everything resolves to unsupported. Updates last_seen so the capability is "fresh".
create or replace function public.wl_agent_report_capabilities(
  p_agent_id uuid, p_agent_key text, p_capabilities jsonb
) returns jsonb language plpgsql security definer set search_path = public as $$
declare v_agent public.agents; v_clean jsonb;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then raise exception 'agent not recognised' using errcode = '28000'; end if;
  -- keep only known capabilities, de-duplicated; unknown advertisements are dropped.
  select coalesce(jsonb_agg(distinct c), '[]'::jsonb) into v_clean
    from jsonb_array_elements_text(coalesce(p_capabilities, '[]'::jsonb)) c
   where c = any(public.wl_known_capabilities());
  update public.agents
     set capabilities = v_clean, capabilities_reported_at = now(), last_seen_at = now()
   where id = v_agent.id;
  return jsonb_build_object('ok', true, 'capabilities', v_clean);
end $$;
revoke all on function public.wl_agent_report_capabilities(uuid,text,jsonb) from public;
grant execute on function public.wl_agent_report_capabilities(uuid,text,jsonb) to anon, authenticated;

-- ---------------------------------------------------------------------
-- 2. Site capability read model. A capability is AVAILABLE at a site only if a RECENTLY-ACTIVE
--    agent advertises it (a replaced/offline agent's stale advertisement drops out after 1h).
-- ---------------------------------------------------------------------
create or replace function public.wl_site_has_capability(p_site_id uuid, p_cap text)
returns boolean language sql stable security definer set search_path = public as $$
  select exists(
    select 1 from public.agents a
     where a.site_id = p_site_id
       and a.last_seen_at > now() - interval '1 hour'
       and a.capabilities ? p_cap)
$$;
revoke all on function public.wl_site_has_capability(uuid,text) from public, anon, authenticated;

create or replace function public.wl_site_capabilities(p_site_id uuid)
returns jsonb language plpgsql stable security definer set search_path = public as $$
declare v_tenant uuid := wl_my_tenant();
begin
  if v_tenant is null then raise exception 'not signed in' using errcode = '42501'; end if;
  if not exists(select 1 from public.sites where id = p_site_id and tenant_id = v_tenant) then
    raise exception 'not your site' using errcode = '42501';
  end if;
  return (
    select jsonb_build_object(
      'operations_runtime_enabled', coalesce((select operations_runtime_enabled from public.sites where id = p_site_id), false),
      'capabilities', coalesce((
        select jsonb_agg(distinct c) from public.agents a,
               lateral jsonb_array_elements_text(a.capabilities) c
         where a.site_id = p_site_id and a.last_seen_at > now() - interval '1 hour'), '[]'::jsonb)));
end $$;
revoke all on function public.wl_site_capabilities(uuid) from public, anon;
grant execute on function public.wl_site_capabilities(uuid) to authenticated;

-- ---------------------------------------------------------------------
-- 3. Operations gate — default OFF, explicit enable, requires a compatible runtime.
-- ---------------------------------------------------------------------
alter table public.sites
  add column if not exists operations_runtime_enabled boolean not null default false;

create or replace function public.wl_set_operations_runtime(p_site_id uuid, p_enabled boolean)
returns jsonb language plpgsql security definer set search_path = public as $$
declare v_tenant uuid := wl_require_role(array['owner','admin']);
begin
  if not exists(select 1 from public.sites where id = p_site_id and tenant_id = v_tenant) then
    raise exception 'not your site' using errcode = '42501';
  end if;
  -- turning it ON requires a compatible agent that advertises operations_runtime; you cannot
  -- enable Operations on a site whose installed agent cannot execute it.
  if p_enabled and not public.wl_site_has_capability(p_site_id, 'operations_runtime') then
    raise exception 'This site has no agent that reports the operations runtime capability yet. '
      'Operations stays off until a compatible WatchLog agent is connected.' using errcode = '42501';
  end if;
  update public.sites set operations_runtime_enabled = p_enabled where id = p_site_id;
  return jsonb_build_object('site_id', p_site_id, 'operations_runtime_enabled', p_enabled);
end $$;
revoke all on function public.wl_set_operations_runtime(uuid,boolean) from public, anon;
grant execute on function public.wl_set_operations_runtime(uuid,boolean) to authenticated;

-- ---------------------------------------------------------------------
-- 4. Recreate the 0054 live bridge WITH the gate. While operations_runtime_enabled is false (the
--    default for every existing site) the bridge does nothing — no incident is ever created, so
--    applying 0054-0059 is behaviourally inert until a site explicitly opts in.
-- ---------------------------------------------------------------------
create or replace function public.wl_analytic_event_to_ops_incident()
returns trigger language plpgsql security definer set search_path = public as $$
declare v_rule public.monitoring_rules; v_enabled boolean;
begin
  select operations_runtime_enabled into v_enabled from public.sites where id = new.site_id;
  if not coalesce(v_enabled, false) then
    return new;                         -- Operations runtime OFF for this site: never promote
  end if;
  select * into v_rule from public.monitoring_rules where id = new.monitoring_rule_id;
  if v_rule.id is null then
    return new;
  end if;
  if v_rule.severity in ('attention','incident')
     or v_rule.promote_incident or v_rule.sensitive or v_rule.review_required then
    perform public.wl_emit_operations_incident(
      v_rule.id, new.camera_id, new.object_class,
      nullif(new.metadata_json->>'confidence', '')::numeric, new.occurred_at, null,
      jsonb_build_object('source', 'live', 'event_type', new.event_type,
                         'analytic_event_id', new.id, 'track_key', new.track_key));
  end if;
  return new;
end $$;

-- ---------------------------------------------------------------------
-- 5. Recreate the 0058 evidence CREATORS with a capability gate: a still/clip task is only created
--    when the site advertises the matching evidence capability, so an old-agent site never
--    accumulates misleading indefinitely-pending evidence tasks.
-- ---------------------------------------------------------------------
create or replace function public.wl_create_incident_still_request(
  p_incident_id bigint, p_camera_id uuid, p_occurred_at timestamptz, p_purpose text default null
) returns uuid language plpgsql security definer set search_path = public as $$
declare v_inc public.operations_incidents; v_id uuid;
begin
  select * into v_inc from public.operations_incidents where id = p_incident_id;
  if v_inc.id is null or p_camera_id is null then return null; end if;
  if not public.wl_site_has_capability(v_inc.site_id, 'operations_evidence_still') then
    return null;                        -- no compatible agent: do not create a task that cannot be fulfilled
  end if;
  insert into public.operations_incident_evidence(tenant_id, site_id, incident_id, camera_id, purpose, occurred_at)
  values (v_inc.tenant_id, v_inc.site_id, v_inc.id, p_camera_id, nullif(p_purpose,''), coalesce(p_occurred_at, v_inc.occurred_at))
  on conflict do nothing
  returning id into v_id;
  return v_id;
end $$;

create or replace function public.wl_create_incident_clip_request(
  p_incident_id bigint, p_camera_id uuid, p_occurred_at timestamptz,
  p_pre_seconds int default 10, p_post_seconds int default 20
) returns uuid language plpgsql security definer set search_path = public as $$
declare v_inc public.operations_incidents; v_pre int := least(greatest(coalesce(p_pre_seconds,10),0),30);
        v_post int := least(greatest(coalesce(p_post_seconds,20),1),30); v_id uuid; v_at timestamptz;
begin
  select * into v_inc from public.operations_incidents where id = p_incident_id;
  if v_inc.id is null or p_camera_id is null then return null; end if;
  if not public.wl_site_has_capability(v_inc.site_id, 'operations_evidence_clip') then
    return null;
  end if;
  v_at := coalesce(p_occurred_at, v_inc.occurred_at);
  insert into public.incident_clip_requests(tenant_id, operations_incident_id, site_id, camera_id, source, start_at, end_at)
  values (v_inc.tenant_id, v_inc.id, v_inc.site_id, p_camera_id, 'rule',
    v_at - make_interval(secs => v_pre), v_at + make_interval(secs => v_post))
  on conflict do nothing
  returning id into v_id;
  return v_id;
end $$;

-- ---------------------------------------------------------------------
-- 6. Studio read model — surface the operations gate + the site's available capabilities so the
--    portal can gate authoring and evidence UI truthfully (never let a user believe a rule/action
--    is monitoring when the connected agent cannot execute it).
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
      'operations_runtime_enabled', coalesce(s.operations_runtime_enabled, false),
      'runtime_capabilities', coalesce((select jsonb_agg(distinct c) from public.agents a,
          lateral jsonb_array_elements_text(a.capabilities) c
         where a.site_id = s.id and a.last_seen_at > now() - interval '1 hour'), '[]'::jsonb),
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
