-- =====================================================================
-- 0026 — Checkout Activity + daily analytics report surface
--
-- Checkout v1 measures PEOPLE PRESENT in a configured checkout zone. It
-- does NOT claim completed transaction counts. Exact transactions need a
-- POS integration and remain future work.
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
      jsonb_build_object('key','visitor_flow','label','Visitor Flow','rule_type','line_crossing','classes',jsonb_build_array('person')),
      jsonb_build_object('key','vehicle_flow','label','Vehicle Flow','rule_type','line_crossing','classes',jsonb_build_array('car','motorcycle')),
      jsonb_build_object('key','boundary_monitoring','label','Boundary Monitoring','rule_type','line_crossing','classes',jsonb_build_array('person','car','motorcycle')),
      jsonb_build_object('key','zone_activity','label','Zone Activity','rule_type','zone_entry','classes',jsonb_build_array('person','car','motorcycle')),
      jsonb_build_object('key','dwell','label','Dwell / Time in Zone','rule_type','zone_dwell','classes',jsonb_build_array('person')),
      jsonb_build_object('key','checkout_activity','label','Checkout Activity','rule_type','occupancy','classes',jsonb_build_array('person')),
      jsonb_build_object('key','after_hours','label','After-Hours Activity','rule_type','schedule_activity','classes',jsonb_build_array('person','car','motorcycle')),
      jsonb_build_object('key','site_health','label','Site Health','rule_type','health','classes','[]'::jsonb)
    ),
    'packs', jsonb_build_object(
      'retail', jsonb_build_array('visitor_flow','checkout_activity','after_hours','site_health'),
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

create or replace function public.wl_analytics_overview(
  p_days int default 7, p_site_id uuid default null
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_my_tenant();
  v_days int := least(greatest(coalesce(p_days,7),1),90);
begin
  if v_tenant is null then
    return jsonb_build_object('summary','{}'::jsonb,'daily','[]'::jsonb,'by_rule','[]'::jsonb);
  end if;
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
        and ae.occurred_at>=now()-make_interval(days=>v_days) and ae.event_type='schedule_activity'),
      'checkout_peak',coalesce((select max((ae.metadata_json->>'count')::int)
        from analytic_events ae join cameras c on c.id=ae.camera_id
        where ae.tenant_id=v_tenant and (p_site_id is null or ae.site_id=p_site_id)
          and ae.occurred_at>=now()-make_interval(days=>v_days)
          and ae.event_type='occupancy' and c.purpose='checkout_till'
          and (ae.metadata_json->>'count') ~ '^[0-9]+$'),0)
    ),
    'daily',coalesce((select jsonb_agg(to_jsonb(x) order by x.day) from (
      select (ae.occurred_at at time zone coalesce(s.timezone,'Asia/Karachi'))::date as day,
        count(*) filter(where ae.object_class='person' and ae.event_type='line_crossing' and ae.direction='in') as visitor_in,
        count(*) filter(where ae.object_class='person' and ae.event_type='line_crossing' and ae.direction='out') as visitor_out,
        count(*) filter(where ae.object_class in ('car','motorcycle') and ae.event_type='line_crossing' and ae.direction='in') as vehicles_in,
        count(*) filter(where ae.event_type='zone_entry') as zone_entries,
        count(*) filter(where ae.event_type='schedule_activity') as after_hours,
        coalesce(max((ae.metadata_json->>'count')::int) filter(where ae.event_type='occupancy' and c.purpose='checkout_till' and (ae.metadata_json->>'count') ~ '^[0-9]+$'),0) as checkout_peak
      from analytic_events ae
      join sites s on s.id=ae.site_id
      join cameras c on c.id=ae.camera_id
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
revoke all on function public.wl_analytics_overview(integer,uuid) from public,anon;
grant execute on function public.wl_analytics_overview(integer,uuid) to authenticated;

-- Site/date report data. Intended for the trusted report-runner DB connection.
create or replace function public.wl_daily_analytics(p_site_id uuid, p_date date)
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  with site_ctx as (
    select s.id,s.tenant_id,s.timezone from sites s where s.id=p_site_id
  ), bounds as (
    select (p_date::timestamp at time zone sc.timezone) as from_ts,
           ((p_date + 1)::timestamp at time zone sc.timezone) as to_ts,
           sc.* from site_ctx sc
  ), day_events as (
    select ae.*,c.name as camera_name,c.purpose,r.name as rule_name,r.rule_type
      from analytic_events ae
      join bounds b on b.id=ae.site_id
      join cameras c on c.id=ae.camera_id
      left join monitoring_rules r on r.id=ae.monitoring_rule_id
     where ae.occurred_at>=b.from_ts and ae.occurred_at<b.to_ts
  )
  select jsonb_build_object(
    'site_id',p_site_id,
    'date',p_date,
    'visitor_in',count(*) filter(where object_class='person' and event_type='line_crossing' and direction='in'),
    'visitor_out',count(*) filter(where object_class='person' and event_type='line_crossing' and direction='out'),
    'vehicles_in',count(*) filter(where object_class in ('car','motorcycle') and event_type='line_crossing' and direction='in'),
    'vehicles_out',count(*) filter(where object_class in ('car','motorcycle') and event_type='line_crossing' and direction='out'),
    'zone_entries',count(*) filter(where event_type='zone_entry'),
    'after_hours',count(*) filter(where event_type='schedule_activity'),
    'checkout_peak',coalesce(max((metadata_json->>'count')::int) filter(where event_type='occupancy' and purpose='checkout_till' and (metadata_json->>'count') ~ '^[0-9]+$'),0),
    'measurements',count(*),
    'by_rule',coalesce((select jsonb_agg(to_jsonb(z) order by z.measurements desc) from (
      select rule_name,rule_type,camera_name,count(*) as measurements,
             max(duration_seconds) filter(where duration_seconds is not null) as max_duration_seconds
        from day_events where rule_name is not null
       group by rule_name,rule_type,camera_name order by count(*) desc limit 12
    ) z),'[]'::jsonb)
  ) from day_events
$$;

-- This is an internal reporting surface. Browser and anon roles do not need it.
revoke all on function public.wl_daily_analytics(uuid,date) from public,anon,authenticated;
