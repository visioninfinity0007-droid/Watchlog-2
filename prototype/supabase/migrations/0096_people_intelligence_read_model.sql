-- =====================================================================
-- 0096 — People & Operational Intelligence read model
--
-- Uses existing WatchLog analytics. It does NOT turn raw detections into a
-- fake distinct-person count.
-- =====================================================================

create or replace function public.wl_people_intelligence_core(
  p_site_id uuid,
  p_from timestamptz,
  p_to timestamptz
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_owner uuid;
  v_cov jsonb;
  v_entries bigint := 0;
  v_exits bigint := 0;
  v_dwell_count bigint := 0;
  v_dwell_avg numeric;
  v_dwell_max numeric;
  v_journeys bigint := 0;
  v_after bigint := 0;
  v_site_occ_rules int := 0;
  v_current_occ int;
  v_current_occ_at timestamptz;
  v_peak_occ int;
  v_hourly jsonb;
  v_occ_zones jsonb;
begin
  if p_to <= p_from then
    raise exception 'invalid time window' using errcode='22023';
  end if;

  select tenant_id into v_owner from public.sites where id=p_site_id;
  if v_owner is null then
    raise exception 'no such site' using errcode='22023';
  end if;

  v_cov := public.wl_site_coverage_report(p_site_id,p_from,p_to);

  select
    count(*) filter (where ae.direction='in'),
    count(*) filter (where ae.direction='out')
    into v_entries,v_exits
    from public.analytic_events ae
   where ae.site_id=p_site_id
     and ae.occurred_at>=p_from
     and ae.occurred_at<p_to
     and ae.analytic_key='visitor_flow'
     and ae.event_type='line_crossing'
     and ae.object_class='person';

  select coalesce(jsonb_agg(jsonb_build_object(
      'hour',h,
      'entries',entries,
      'exits',exits
    ) order by h),'[]'::jsonb)
    into v_hourly
    from (
      select date_trunc('hour',ae.occurred_at) h,
             count(*) filter(where ae.direction='in') entries,
             count(*) filter(where ae.direction='out') exits
        from public.analytic_events ae
       where ae.site_id=p_site_id
         and ae.occurred_at>=p_from
         and ae.occurred_at<p_to
         and ae.analytic_key='visitor_flow'
         and ae.event_type='line_crossing'
         and ae.object_class='person'
       group by 1
    ) x;

  select count(*) into v_site_occ_rules
    from public.monitoring_rules r
   where r.site_id=p_site_id
     and r.enabled
     and r.rule_type='occupancy'
     and r.provenance->>'occupancy_scope'='site';

  select coalesce(jsonb_agg(jsonb_build_object(
      'rule_id',z.rule_id,
      'camera_id',z.camera_id,
      'camera',z.camera,
      'current',z.current_count,
      'observed_at',z.current_at,
      'peak',z.peak_count,
      'scope',z.occupancy_scope
    ) order by z.camera,z.rule_id),'[]'::jsonb)
    into v_occ_zones
    from (
      select r.id rule_id,
             r.camera_id,
             c.name camera,
             coalesce(r.provenance->>'occupancy_scope','zone') occupancy_scope,
             (
               select (ae.metadata_json->>'count')::int
                 from public.analytic_events ae
                where ae.monitoring_rule_id=r.id
                  and ae.occurred_at>=p_from and ae.occurred_at<p_to
                  and ae.event_type='occupancy'
                  and (ae.metadata_json->>'count') ~ '^[0-9]+$'
                order by ae.occurred_at desc
                limit 1
             ) current_count,
             (
               select ae.occurred_at
                 from public.analytic_events ae
                where ae.monitoring_rule_id=r.id
                  and ae.occurred_at>=p_from and ae.occurred_at<p_to
                  and ae.event_type='occupancy'
                  and (ae.metadata_json->>'count') ~ '^[0-9]+$'
                order by ae.occurred_at desc
                limit 1
             ) current_at,
             (
               select max((ae.metadata_json->>'count')::int)
                 from public.analytic_events ae
                where ae.monitoring_rule_id=r.id
                  and ae.occurred_at>=p_from and ae.occurred_at<p_to
                  and ae.event_type='occupancy'
                  and (ae.metadata_json->>'count') ~ '^[0-9]+$'
             ) peak_count
        from public.monitoring_rules r
        left join public.cameras c on c.id=r.camera_id
       where r.site_id=p_site_id
         and r.enabled
         and r.rule_type='occupancy'
    ) z;

  if v_site_occ_rules = 1 then
    select
      (
        select (ae.metadata_json->>'count')::int
          from public.analytic_events ae
          join public.monitoring_rules r on r.id=ae.monitoring_rule_id
         where ae.site_id=p_site_id
           and ae.occurred_at>=p_from and ae.occurred_at<p_to
           and ae.event_type='occupancy'
           and (ae.metadata_json->>'count') ~ '^[0-9]+$'
           and r.provenance->>'occupancy_scope'='site'
           and r.enabled
         order by ae.occurred_at desc
         limit 1
      ),
      (
        select ae.occurred_at
          from public.analytic_events ae
          join public.monitoring_rules r on r.id=ae.monitoring_rule_id
         where ae.site_id=p_site_id
           and ae.occurred_at>=p_from and ae.occurred_at<p_to
           and ae.event_type='occupancy'
           and (ae.metadata_json->>'count') ~ '^[0-9]+$'
           and r.provenance->>'occupancy_scope'='site'
           and r.enabled
         order by ae.occurred_at desc
         limit 1
      ),
      (
        select max((ae.metadata_json->>'count')::int)
          from public.analytic_events ae
          join public.monitoring_rules r on r.id=ae.monitoring_rule_id
         where ae.site_id=p_site_id
           and ae.occurred_at>=p_from and ae.occurred_at<p_to
           and ae.event_type='occupancy'
           and (ae.metadata_json->>'count') ~ '^[0-9]+$'
           and r.provenance->>'occupancy_scope'='site'
           and r.enabled
      )
      into v_current_occ,v_current_occ_at,v_peak_occ;
  end if;

  select count(*),
         round(avg(duration_seconds),1),
         round(max(duration_seconds),1)
    into v_dwell_count,v_dwell_avg,v_dwell_max
    from public.analytic_events
   where site_id=p_site_id
     and occurred_at>=p_from and occurred_at<p_to
     and (analytic_key='dwell' or event_type in ('zone_dwell','dwell_completed'))
     and object_class='person'
     and duration_seconds is not null;

  select count(*) into v_journeys
    from public.journeys
   where site_id=p_site_id
     and started_at>=p_from and started_at<p_to;

  select count(*) into v_after
    from public.analytic_events
   where site_id=p_site_id
     and occurred_at>=p_from and occurred_at<p_to
     and analytic_key='after_hours'
     and object_class='person';

  return jsonb_build_object(
    'schema','people_intelligence.v1',
    'site_id',p_site_id,
    'window',jsonb_build_object('from',p_from,'to',p_to),
    'coverage',v_cov,
    'flow',jsonb_build_object(
      'entries',v_entries,
      'exits',v_exits,
      'hourly',v_hourly,
      'basis','inbound/outbound person crossing episodes; repeat re-entry can count again'
    ),
    'occupancy',jsonb_build_object(
      'site_scope_verified',v_site_occ_rules=1,
      'current',case when v_site_occ_rules=1 then v_current_occ else null end,
      'current_observed_at',case when v_site_occ_rules=1 then v_current_occ_at else null end,
      'peak',case when v_site_occ_rules=1 then v_peak_occ else null end,
      'zones',v_occ_zones,
      'note',case
        when v_site_occ_rules=1 then
          'site occupancy uses the one enabled rule explicitly configured with occupancy_scope=site'
        else
          'site-wide occupancy is Not verified; zone occupancy is returned without summing overlapping zones'
      end
    ),
    'dwell',jsonb_build_object(
      'episodes',v_dwell_count,
      'average_seconds',v_dwell_avg,
      'longest_seconds',v_dwell_max
    ),
    'plausible_journeys',v_journeys,
    'after_hours_person_events',v_after,
    'estimated_visits',v_entries,
    'estimated_visits_basis',
      'inbound entrance crossing episodes; repeat re-entry may count again',
    'estimated_distinct_people',null,
    'estimated_distinct_people_status',
      'Not verified — requires a calibrated re-entry/cross-camera estimator or approved identity source',
    'honesty',jsonb_build_array(
      'Raw person detections are not a people count.',
      'Entries/exits are movement episodes, not identity.',
      'Occupancy is observed and depends on configured camera/zone coverage.',
      'Journeys are plausible movement journeys, not unique-person identity.',
      'Monitoring gaps are returned explicitly and reduce confidence.'
    )
  );
end $$;

revoke all on function public.wl_people_intelligence_core(uuid,timestamptz,timestamptz)
  from public, anon, authenticated;
grant execute on function public.wl_people_intelligence_core(uuid,timestamptz,timestamptz)
  to service_role;

create or replace function public.wl_my_people_intelligence(
  p_site_id uuid,
  p_from timestamptz,
  p_to timestamptz
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_my_tenant();
  v_owner uuid;
begin
  if v_tenant is null then
    raise exception 'not signed in' using errcode='42501';
  end if;

  select tenant_id into v_owner from public.sites where id=p_site_id;
  if v_owner is null or v_owner <> v_tenant then
    raise exception 'not authorized for this site' using errcode='42501';
  end if;

  return public.wl_people_intelligence_core(p_site_id,p_from,p_to);
end $$;

revoke all on function public.wl_my_people_intelligence(uuid,timestamptz,timestamptz)
  from public, anon;
grant execute on function public.wl_my_people_intelligence(uuid,timestamptz,timestamptz)
  to authenticated, service_role;
