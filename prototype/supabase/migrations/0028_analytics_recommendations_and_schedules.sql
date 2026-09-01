-- =====================================================================
-- 0028 — Purpose-aware monitoring recommendations and schedule lifecycle.
-- Site type narrows the operating context; camera purpose narrows which
-- analytics actually make sense for that view.
-- =====================================================================

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
      jsonb_build_object('key','after_hours','label','After-Hours Activity','rule_type','schedule_activity','classes',jsonb_build_array('person','car','motorcycle'),'geometry','none'),
      jsonb_build_object('key','site_health','label','Site Health','rule_type','health','classes','[]'::jsonb,'geometry','none')
    ),
    'packs', jsonb_build_object(
      'retail', jsonb_build_array('visitor_flow','checkout_activity','after_hours','site_health'),
      'warehouse_logistics', jsonb_build_array('visitor_flow','vehicle_flow','boundary_monitoring','zone_activity','dwell','after_hours','site_health'),
      'manufacturing', jsonb_build_array('visitor_flow','vehicle_flow','boundary_monitoring','zone_activity','dwell','after_hours','site_health'),
      'office_commercial', jsonb_build_array('visitor_flow','dwell','after_hours','site_health'),
      'school_campus', jsonb_build_array('visitor_flow','vehicle_flow','boundary_monitoring','after_hours','site_health'),
      'parking_yard', jsonb_build_array('vehicle_flow','boundary_monitoring','zone_activity','after_hours','site_health'),
      'residential_community', jsonb_build_array('visitor_flow','vehicle_flow','boundary_monitoring','after_hours','site_health'),
      'custom', '[]'::jsonb
    ),
    'purpose_recommendations', jsonb_build_object(
      'entrance_exit',jsonb_build_array('visitor_flow','after_hours','site_health'),
      'main_gate',jsonb_build_array('visitor_flow','vehicle_flow','boundary_monitoring','after_hours','site_health'),
      'reception',jsonb_build_array('visitor_flow','dwell','after_hours','site_health'),
      'checkout_till',jsonb_build_array('checkout_activity','after_hours','site_health'),
      'loading_bay',jsonb_build_array('vehicle_flow','zone_activity','dwell','after_hours','site_health'),
      'warehouse_floor',jsonb_build_array('zone_activity','dwell','after_hours','site_health'),
      'perimeter',jsonb_build_array('boundary_monitoring','after_hours','site_health'),
      'restricted_area',jsonb_build_array('zone_activity','dwell','after_hours','site_health'),
      'parking',jsonb_build_array('vehicle_flow','zone_activity','after_hours','site_health'),
      'office_floor',jsonb_build_array('zone_activity','after_hours','site_health'),
      'school_gate',jsonb_build_array('visitor_flow','vehicle_flow','boundary_monitoring','after_hours','site_health'),
      'corridor',jsonb_build_array('visitor_flow','after_hours','site_health'),
      'custom',jsonb_build_array('visitor_flow','vehicle_flow','boundary_monitoring','zone_activity','dwell','after_hours','site_health')
    )
  )
$$;
revoke all on function public.wl_analytics_catalog() from public,anon;
grant execute on function public.wl_analytics_catalog() to authenticated;

create or replace function public.wl_delete_monitoring_schedule(p_schedule_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_tenant uuid;
  v_site uuid;
  v_version bigint;
  v_refs int;
begin
  select tenant_id,site_id into v_tenant,v_site
    from monitoring_schedules where id=p_schedule_id;
  if v_tenant is null or not wl_is_member(v_tenant) then
    raise exception 'schedule not found' using errcode='42501';
  end if;
  select count(*) into v_refs from monitoring_rules where schedule_id=p_schedule_id;
  if v_refs>0 then
    raise exception 'schedule is used by % monitoring rule(s); reassign those rules first',v_refs
      using errcode='23503';
  end if;
  delete from monitoring_schedules where id=p_schedule_id;
  v_version := wl_analytics_bump_site(v_site);
  return jsonb_build_object('ok',true,'version',v_version);
end $$;

revoke all on function public.wl_delete_monitoring_schedule(uuid) from public,anon;
grant execute on function public.wl_delete_monitoring_schedule(uuid) to authenticated;
