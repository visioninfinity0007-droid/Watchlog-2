-- =====================================================================
-- 0103 — Production security hardening after AI-first portal rollout
--
-- 1) Pin helper-function search paths so caller-controlled search_path cannot
--    influence name resolution.
-- 2) Remove direct client execution from unguarded core/helper RPCs. Tenant-
--    guarded wrappers and SECURITY DEFINER callers continue to work because
--    they execute as the function owner.
-- 3) Optimize the memberships RLS predicate to evaluate auth.uid() once.
-- 4) Make AI recovered-event parsing fail-safe for legacy/malformed payloads.
-- =====================================================================

alter function public.wl_dedupe_key(uuid,text,text,timestamptz,text)
  set search_path = public, pg_temp;
alter function public.wl_plan_retention_days(text)
  set search_path = public, pg_temp;
alter function public.wl_analytics_valid_site_type(text)
  set search_path = public, pg_temp;
alter function public.wl_analytics_valid_purpose(text)
  set search_path = public, pg_temp;
alter function public.wl_analytics_points_valid(jsonb,integer,integer)
  set search_path = public, pg_temp;
alter function public.wl_analytics_valid_key(text)
  set search_path = public, pg_temp;
alter function public.wl_try_timestamptz(text)
  set search_path = public, pg_temp;
alter function public.wl_try_bigint(text)
  set search_path = public, pg_temp;
alter function public.wl_try_int(text)
  set search_path = public, pg_temp;
alter function public.wl_try_bool(text)
  set search_path = public, pg_temp;
alter function public.wl_monitoring_rule_bump_version()
  set search_path = public, pg_temp;
alter function public.wl_operations_primitives()
  set search_path = public, pg_temp;
alter function public.wl_known_capabilities()
  set search_path = public, pg_temp;

-- Core helpers below do not perform their own tenant assertion. Do not expose
-- them directly to browser roles; tenant-safe RPCs call them internally.
revoke execute on function public.wl_current_site_agent(uuid) from public, anon, authenticated;
grant execute on function public.wl_current_site_agent(uuid) to service_role;

revoke execute on function public.wl_overlay_camera_truth(uuid,jsonb) from public, anon, authenticated;
grant execute on function public.wl_overlay_camera_truth(uuid,jsonb) to service_role;

revoke execute on function public.wl_site_coverage_report(uuid,timestamptz,timestamptz) from public, anon, authenticated;
grant execute on function public.wl_site_coverage_report(uuid,timestamptz,timestamptz) to service_role;

revoke execute on function public.wl_site_coverage_report_classes(uuid,timestamptz,timestamptz) from public, anon, authenticated;
grant execute on function public.wl_site_coverage_report_classes(uuid,timestamptz,timestamptz) to service_role;

-- Reference capability lookups are acceptable to signed-in users, but there is
-- no reason to expose them before authentication.
revoke execute on function public.wl_recorder_capability(text,text,text,text) from public, anon;
grant execute on function public.wl_recorder_capability(text,text,text,text) to authenticated, service_role;
revoke execute on function public.wl_recorder_profile(text,text) from public, anon;
grant execute on function public.wl_recorder_profile(text,text) to authenticated, service_role;

-- Account/bootstrap and control entry points are authenticated-only and contain
-- their own role/tenant checks.
revoke execute on function public.wl_bootstrap_tenant(text,text,text) from public, anon;
grant execute on function public.wl_bootstrap_tenant(text,text,text) to authenticated, service_role;
revoke execute on function public.wl_site_control_set_enabled(uuid,boolean) from public, anon;
grant execute on function public.wl_site_control_set_enabled(uuid,boolean) to authenticated, service_role;

-- Trigger helpers should not be callable from browser roles.
revoke execute on function public.wl_monitoring_rule_snapshot() from public, anon, authenticated;
grant execute on function public.wl_monitoring_rule_snapshot() to service_role;

-- Evaluate auth.uid() once per statement rather than once per row.
drop policy if exists portal_read_memberships on public.memberships;
create policy portal_read_memberships on public.memberships
  for select to authenticated
  using (user_id = (select auth.uid()));

-- Harden AI context against legacy payloads where `recovered` is not a valid
-- PostgreSQL boolean literal. Only an explicit truthy token becomes true.
create or replace function public.wl_ai_context(p_site_id uuid)
returns jsonb
language plpgsql stable security definer set search_path = public, pg_temp as $$
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
             c.name as camera,c.channel,
             coalesce(e.payload->>'source','live') as source,
             case lower(trim(coalesce(e.payload->>'recovered','false')))
               when 'true' then true
               when 't' then true
               when '1' then true
               when 'yes' then true
               else false
             end as recovered
        from events e
        left join cameras c on c.id=e.camera_id
       where e.site_id=p_site_id
       order by e.device_ts desc
       limit 20
    ) r;

  select coalesce(jsonb_agg(jsonb_build_object(
      'id',c.id,'channel',c.channel,'name',c.name,'purpose',c.purpose,
      'monitor',c.is_configured,'analytics_enabled',coalesce(c.analytics_enabled,false),
      'health_state',coalesce(h.health_state::text,'unknown'),
      'recording_state',coalesce(h.recording_state::text,'unknown')
    ) order by c.channel),'[]'::jsonb) into v_camera_rows
    from cameras c left join camera_health h on h.camera_id=c.id
   where c.site_id=p_site_id;

  return jsonb_build_object(
    'facts_version','watchlog-ai-context-v2',
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
