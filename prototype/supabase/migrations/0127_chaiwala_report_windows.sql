-- Chai Wala report-window contract: Today/Yesterday use restaurant-day-v2;
-- Last 7 and Last 30 use restaurant-period-v1.
-- The profile switch keeps this tenant-specific layout separate from existing non-restaurant reports.

CREATE OR REPLACE FUNCTION public.wl_restaurant_period(p_site_id uuid, p_days integer DEFAULT 7, p_end_date date DEFAULT NULL::date)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
declare
  v_tenant uuid:=public.wl_assert_my_site(p_site_id);
  v_site public.sites;
  v_ctx public.site_business_context;
  v_type text;
  v_current_service_date date;
  v_end_date date;
  v_start_date date;
  v_prev_start date;
  v_prev_end date;
  v_date date;
  v_day jsonb;
  v_days jsonb:='[]'::jsonb;
  v_times jsonb:='[]'::jsonb;
  v_prev_times jsonb:='[]'::jsonb;
  v_day_obs integer;
  v_day_tables integer;
  v_day_covers integer;
  v_day_served integer;
  v_day_sessions integer;
  v_day_peak_visible integer;
  v_day_peak_tables integer;
  v_day_median numeric;
  v_day_coverage numeric;
  v_observed_days integer:=0;
  v_total_covers integer:=0;
  v_total_served integer:=0;
  v_total_sessions integer:=0;
  v_sum_coverage numeric:=0;
  v_prev_observed_days integer:=0;
  v_prev_total_covers integer:=0;
  v_prev_total_served integer:=0;
  v_prev_total_sessions integer:=0;
  v_prev_sum_coverage numeric:=0;
  v_median_time numeric;
  v_prev_median_time numeric;
  v_hour_profile jsonb;
  v_floor_profile jsonb;
  v_table_profile jsonb;
  v_weekday_profile jsonb;
  v_weekly_trend jsonb;
  v_service_distribution jsonb;
  v_busiest_day jsonb;
  v_busiest_hour jsonb;
begin
  if p_days<2 or p_days>31 then
    raise exception 'p_days must be between 2 and 31' using errcode='22023';
  end if;

  select * into v_site from public.sites where id=p_site_id and tenant_id=v_tenant;
  if v_site.id is null then raise exception 'not authorized for this site' using errcode='42501'; end if;
  select * into v_ctx from public.site_business_context where site_id=p_site_id and tenant_id=v_tenant;
  v_type:=coalesce(v_ctx.site_type,v_site.site_type,'other');
  if v_type<>'restaurant' then return jsonb_build_object('enabled',false,'site_type',v_type); end if;

  v_current_service_date:=(public.wl_restaurant_day(p_site_id,null)->>'service_date')::date;
  v_end_date:=coalesce(p_end_date,v_current_service_date);
  if v_end_date>v_current_service_date then
    raise exception 'period end date cannot be in the future' using errcode='22023';
  end if;
  v_start_date:=v_end_date-(p_days-1);
  v_prev_end:=v_start_date-1;
  v_prev_start:=v_prev_end-(p_days-1);

  v_date:=v_start_date;
  while v_date<=v_end_date loop
    v_day:=public.wl_restaurant_day(p_site_id,v_date);
    v_day_obs:=coalesce((v_day->'data_quality'->>'camera_observations')::integer,0);
    v_day_tables:=coalesce((v_day->'data_quality'->>'table_observations')::integer,0);
    v_day_covers:=coalesce((v_day->'sessions'->>'estimated_covers')::integer,0);
    v_day_served:=coalesce((v_day->'sessions'->>'served_sessions')::integer,0);
    v_day_sessions:=coalesce((v_day->'sessions'->>'count')::integer,0);
    v_day_median:=nullif(v_day->'sessions'->>'median_observed_time_to_food_minutes','')::numeric;
    v_day_coverage:=nullif(v_day->'data_quality'->>'business_analytics_coverage_ratio','')::numeric;

    select max(nullif(h->>'peak_visible_customers','')::integer)
      into v_day_peak_visible
      from jsonb_array_elements(coalesce(v_day->'hourly','[]'::jsonb)) h
     where coalesce((h->>'samples')::integer,0)>0;
    select max(nullif(h->>'peak_occupied_tables','')::integer)
      into v_day_peak_tables
      from jsonb_array_elements(coalesce(v_day->'hourly','[]'::jsonb)) h
     where coalesce((h->>'samples')::integer,0)>0;

    if v_day_obs>0 or v_day_tables>0 then v_observed_days:=v_observed_days+1; end if;
    v_total_covers:=v_total_covers+v_day_covers;
    v_total_served:=v_total_served+v_day_served;
    v_total_sessions:=v_total_sessions+v_day_sessions;
    v_sum_coverage:=v_sum_coverage+coalesce(v_day_coverage,0);

    select v_times || coalesce(jsonb_agg((i->>'observed_time_to_food_minutes')::numeric),'[]'::jsonb)
      into v_times
      from jsonb_array_elements(coalesce(v_day->'sessions'->'items','[]'::jsonb)) i
     where nullif(i->>'observed_time_to_food_minutes','') is not null;

    v_days:=v_days||jsonb_build_array(jsonb_build_object(
      'service_date',v_date,
      'weekday',to_char(v_date,'Dy'),
      'camera_observations',v_day_obs,
      'table_observations',v_day_tables,
      'coverage_ratio',v_day_coverage,
      'peak_visible_diners',v_day_peak_visible,
      'peak_occupied_tables',v_day_peak_tables,
      'estimated_covers',v_day_covers,
      'table_sessions',v_day_sessions,
      'served_sessions',v_day_served,
      'median_observed_time_to_food_minutes',v_day_median,
      'hourly',coalesce(v_day->'hourly','[]'::jsonb),
      'floors',coalesce(v_day->'floors','[]'::jsonb),
      'tables',coalesce(v_day->'tables','[]'::jsonb),
      'camera_coverage',coalesce(v_day->'data_quality'->'camera_coverage','[]'::jsonb)
    ));
    v_date:=v_date+1;
  end loop;

  if jsonb_array_length(v_times)>0 then
    select round((percentile_cont(0.5) within group(order by x))::numeric,1)
      into v_median_time
      from (
        select value::text::numeric x
        from jsonb_array_elements(v_times)
      ) q;
  end if;

  v_date:=v_prev_start;
  while v_date<=v_prev_end loop
    v_day:=public.wl_restaurant_day(p_site_id,v_date);
    v_day_obs:=coalesce((v_day->'data_quality'->>'camera_observations')::integer,0);
    v_day_tables:=coalesce((v_day->'data_quality'->>'table_observations')::integer,0);
    v_day_covers:=coalesce((v_day->'sessions'->>'estimated_covers')::integer,0);
    v_day_served:=coalesce((v_day->'sessions'->>'served_sessions')::integer,0);
    v_day_sessions:=coalesce((v_day->'sessions'->>'count')::integer,0);
    v_day_coverage:=nullif(v_day->'data_quality'->>'business_analytics_coverage_ratio','')::numeric;

    if v_day_obs>0 or v_day_tables>0 then v_prev_observed_days:=v_prev_observed_days+1; end if;
    v_prev_total_covers:=v_prev_total_covers+v_day_covers;
    v_prev_total_served:=v_prev_total_served+v_day_served;
    v_prev_total_sessions:=v_prev_total_sessions+v_day_sessions;
    v_prev_sum_coverage:=v_prev_sum_coverage+coalesce(v_day_coverage,0);

    select v_prev_times || coalesce(jsonb_agg((i->>'observed_time_to_food_minutes')::numeric),'[]'::jsonb)
      into v_prev_times
      from jsonb_array_elements(coalesce(v_day->'sessions'->'items','[]'::jsonb)) i
     where nullif(i->>'observed_time_to_food_minutes','') is not null;

    v_date:=v_date+1;
  end loop;

  if jsonb_array_length(v_prev_times)>0 then
    select round((percentile_cont(0.5) within group(order by x))::numeric,1)
      into v_prev_median_time
      from (
        select value::text::numeric x
        from jsonb_array_elements(v_prev_times)
      ) q;
  end if;

  with rows as (
    select d->>'service_date' service_date,h
    from jsonb_array_elements(v_days) d
    cross join lateral jsonb_array_elements(coalesce(d->'hourly','[]'::jsonb)) h
    where coalesce((h->>'samples')::integer,0)>0
  ), agg as (
    select h->>'local_hour' local_hour,
      count(distinct service_date) observed_days,
      round(avg(nullif(h->>'avg_visible_customers','')::numeric),1) avg_visible_diners,
      round(avg(nullif(h->>'peak_visible_customers','')::numeric),1) avg_peak_visible_diners,
      max(nullif(h->>'peak_visible_customers','')::integer) max_peak_visible_diners,
      round(avg(nullif(h->>'avg_occupied_tables','')::numeric),1) avg_occupied_tables,
      max(nullif(h->>'peak_occupied_tables','')::integer) max_peak_occupied_tables,
      round(avg(nullif(h->>'avg_kitchen_load','')::numeric),3) avg_kitchen_load,
      round(avg(nullif(h->>'avg_handoff_load','')::numeric),3) avg_handoff_load
    from rows
    group by h->>'local_hour'
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'local_hour',local_hour,'observed_days',observed_days,
    'avg_visible_diners',avg_visible_diners,
    'avg_peak_visible_diners',avg_peak_visible_diners,
    'max_peak_visible_diners',max_peak_visible_diners,
    'avg_occupied_tables',avg_occupied_tables,
    'max_peak_occupied_tables',max_peak_occupied_tables,
    'avg_kitchen_load',avg_kitchen_load,
    'avg_handoff_load',avg_handoff_load
  ) order by local_hour),'[]'::jsonb)
  into v_hour_profile from agg;

  with rows as (
    select d->>'service_date' service_date,f
    from jsonb_array_elements(v_days) d
    cross join lateral jsonb_array_elements(coalesce(d->'floors','[]'::jsonb)) f
    where coalesce((f->>'samples')::integer,0)>0
  ), agg as (
    select f->>'camera_id' camera_id,f->>'floor' floor,
      count(distinct service_date) observed_days,
      sum((f->>'samples')::integer) samples,
      round(avg(nullif(f->>'avg_visible_customers','')::numeric),1) avg_visible_diners,
      max(nullif(f->>'peak_visible_customers','')::integer) peak_visible_diners,
      round(avg(nullif(f->>'avg_occupied_tables','')::numeric),1) avg_occupied_tables,
      max(nullif(f->>'peak_occupied_tables','')::integer) peak_occupied_tables
    from rows
    group by f->>'camera_id',f->>'floor'
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'camera_id',camera_id,'floor',floor,'observed_days',observed_days,'samples',samples,
    'avg_visible_diners',avg_visible_diners,'peak_visible_diners',peak_visible_diners,
    'avg_occupied_tables',avg_occupied_tables,'peak_occupied_tables',peak_occupied_tables
  ) order by floor),'[]'::jsonb)
  into v_floor_profile from agg;

  with rows as (
    select d->>'service_date' service_date,t
    from jsonb_array_elements(v_days) d
    cross join lateral jsonb_array_elements(coalesce(d->'tables','[]'::jsonb)) t
    where coalesce((t->>'samples')::integer,0)>0
  ), agg as (
    select t->>'table_key' table_key,max(t->>'label') label,max(t->>'capacity') capacity,
      count(distinct service_date) observed_days,
      sum((t->>'samples')::integer) samples,
      sum((t->>'occupied_samples')::integer) occupied_samples,
      max(nullif(t->>'peak_party','')::integer) peak_party
    from rows
    group by t->>'table_key'
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'table_key',table_key,'label',label,'capacity',capacity,'observed_days',observed_days,
    'samples',samples,'occupied_samples',occupied_samples,
    'occupancy_pct',case when samples=0 then null else round(100.0*occupied_samples/samples,1) end,
    'peak_party',peak_party
  ) order by case when samples=0 then null else 100.0*occupied_samples/samples end desc nulls last,table_key),'[]'::jsonb)
  into v_table_profile from agg;

  with rows as (
    select (d->>'service_date')::date service_date,
      coalesce((d->>'camera_observations')::integer,0) camera_observations,
      coalesce((d->>'estimated_covers')::integer,0) estimated_covers,
      coalesce((d->>'served_sessions')::integer,0) served_sessions,
      nullif(d->>'peak_visible_diners','')::integer peak_visible_diners,
      nullif(d->>'median_observed_time_to_food_minutes','')::numeric median_time,
      nullif(d->>'coverage_ratio','')::numeric coverage_ratio
    from jsonb_array_elements(v_days) d
  ), agg as (
    select extract(isodow from service_date)::integer iso_day,to_char(min(service_date),'Dy') weekday,
      count(*) filter(where camera_observations>0) observed_days,
      round(avg(estimated_covers) filter(where camera_observations>0),1) avg_estimated_covers,
      round(avg(peak_visible_diners) filter(where peak_visible_diners is not null),1) avg_peak_visible_diners,
      round(avg(median_time) filter(where median_time is not null),1) avg_daily_median_time_to_food,
      round(avg(coalesce(coverage_ratio,0)),3) avg_coverage_ratio
    from rows
    group by extract(isodow from service_date)
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'iso_day',iso_day,'weekday',weekday,'observed_days',observed_days,
    'avg_estimated_covers',avg_estimated_covers,'avg_peak_visible_diners',avg_peak_visible_diners,
    'avg_daily_median_time_to_food',avg_daily_median_time_to_food,'avg_coverage_ratio',avg_coverage_ratio
  ) order by iso_day),'[]'::jsonb)
  into v_weekday_profile from agg;

  with rows as (
    select (d->>'service_date')::date service_date,
      coalesce((d->>'camera_observations')::integer,0) camera_observations,
      coalesce((d->>'estimated_covers')::integer,0) estimated_covers,
      coalesce((d->>'served_sessions')::integer,0) served_sessions,
      nullif(d->>'peak_visible_diners','')::integer peak_visible_diners,
      nullif(d->>'median_observed_time_to_food_minutes','')::numeric median_time,
      nullif(d->>'coverage_ratio','')::numeric coverage_ratio
    from jsonb_array_elements(v_days) d
  ), agg as (
    select date_trunc('week',service_date)::date week_start,
      min(service_date) first_service_date,max(service_date) last_service_date,
      count(*) filter(where camera_observations>0) observed_days,
      sum(estimated_covers) estimated_covers,
      sum(served_sessions) served_sessions,
      max(peak_visible_diners) peak_visible_diners,
      round(avg(median_time) filter(where median_time is not null),1) avg_daily_median_time_to_food,
      round(avg(coalesce(coverage_ratio,0)),3) avg_coverage_ratio
    from rows
    group by date_trunc('week',service_date)
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'week_start',week_start,'first_service_date',first_service_date,'last_service_date',last_service_date,
    'observed_days',observed_days,'estimated_covers',estimated_covers,'served_sessions',served_sessions,
    'peak_visible_diners',peak_visible_diners,'avg_daily_median_time_to_food',avg_daily_median_time_to_food,
    'avg_coverage_ratio',avg_coverage_ratio
  ) order by week_start),'[]'::jsonb)
  into v_weekly_trend from agg;

  select jsonb_build_object(
    'under_15_minutes',count(*) filter(where x<15),
    '15_to_30_minutes',count(*) filter(where x>=15 and x<30),
    '30_to_45_minutes',count(*) filter(where x>=30 and x<45),
    '45_plus_minutes',count(*) filter(where x>=45),
    'sample_sessions',count(*)
  )
  into v_service_distribution
  from (select value::text::numeric x from jsonb_array_elements(v_times)) q;

  select d into v_busiest_day
  from jsonb_array_elements(v_days) d
  where coalesce((d->>'camera_observations')::integer,0)>0
  order by coalesce((d->>'estimated_covers')::integer,0) desc,
           coalesce((d->>'peak_visible_diners')::integer,0) desc,
           d->>'service_date'
  limit 1;

  select h into v_busiest_hour
  from jsonb_array_elements(coalesce(v_hour_profile,'[]'::jsonb)) h
  order by nullif(h->>'avg_peak_visible_diners','')::numeric desc nulls last,
           h->>'local_hour'
  limit 1;

  return jsonb_build_object(
    'enabled',true,
    'schema','restaurant-period-v1',
    'days',p_days,
    'timezone',v_site.timezone,
    'period',jsonb_build_object(
      'start_service_date',v_start_date,'end_service_date',v_end_date,
      'previous_start_service_date',v_prev_start,'previous_end_service_date',v_prev_end
    ),
    'summary',jsonb_build_object(
      'expected_service_days',p_days,
      'observed_service_days',v_observed_days,
      'total_estimated_covers',v_total_covers,
      'avg_estimated_covers_per_observed_day',case when v_observed_days=0 then null else round(v_total_covers::numeric/v_observed_days,1) end,
      'table_sessions',v_total_sessions,
      'served_sessions',v_total_served,
      'median_observed_time_to_food_minutes',v_median_time,
      'avg_coverage_ratio',round(v_sum_coverage/p_days,3),
      'busiest_day',v_busiest_day,
      'busiest_hour',v_busiest_hour
    ),
    'previous_period',jsonb_build_object(
      'expected_service_days',p_days,
      'observed_service_days',v_prev_observed_days,
      'total_estimated_covers',v_prev_total_covers,
      'avg_estimated_covers_per_observed_day',case when v_prev_observed_days=0 then null else round(v_prev_total_covers::numeric/v_prev_observed_days,1) end,
      'table_sessions',v_prev_total_sessions,
      'served_sessions',v_prev_total_served,
      'median_observed_time_to_food_minutes',v_prev_median_time,
      'avg_coverage_ratio',round(v_prev_sum_coverage/p_days,3)
    ),
    'comparison',jsonb_build_object(
      'estimated_covers_delta',v_total_covers-v_prev_total_covers,
      'estimated_covers_pct',case when v_prev_total_covers=0 then null else round(100.0*(v_total_covers-v_prev_total_covers)/v_prev_total_covers,1) end,
      'served_sessions_delta',v_total_served-v_prev_total_served,
      'served_sessions_pct',case when v_prev_total_served=0 then null else round(100.0*(v_total_served-v_prev_total_served)/v_prev_total_served,1) end,
      'median_time_to_food_delta_minutes',case when v_median_time is null or v_prev_median_time is null then null else round(v_median_time-v_prev_median_time,1) end,
      'coverage_delta_points',round(100.0*((v_sum_coverage-v_prev_sum_coverage)/p_days),1)
    ),
    'daily',v_days,
    'hour_profile',coalesce(v_hour_profile,'[]'::jsonb),
    'floor_profile',coalesce(v_floor_profile,'[]'::jsonb),
    'table_profile',coalesce(v_table_profile,'[]'::jsonb),
    'weekday_profile',coalesce(v_weekday_profile,'[]'::jsonb),
    'weekly_trend',coalesce(v_weekly_trend,'[]'::jsonb),
    'service_time_distribution',coalesce(v_service_distribution,'{}'::jsonb),
    'measurement_notes',jsonb_build_array(
      'Visible diners are concurrent visible people on dining-floor cameras, not unique footfall.',
      'Estimated covers and table sessions are camera-derived estimates.',
      'Observed time to food is seated/occupied to first food visible, not POS order-to-serve time.',
      'Period comparisons are only as representative as the reported camera-analysis coverage.',
      'Missing observation periods are missing coverage, not zero business activity.'
    )
  );
end $function$;


revoke execute on function public.wl_restaurant_period(uuid,integer,date) from public,anon;
grant execute on function public.wl_restaurant_period(uuid,integer,date) to authenticated,service_role;

update public.site_business_context
set reporting_prefs=jsonb_set(
  reporting_prefs,
  '{report_layout_profile}',
  '"chaiwala_restaurant_ops_v1"'::jsonb,
  true
),
updated_at=now()
where site_id='1a1fab32-10b3-4082-b61c-180ec04c758c';


update public.site_business_context
set reporting_prefs=jsonb_set(
  reporting_prefs,
  '{restaurant_intelligence_context,report_windows}',
  '{
    "today":{"label":"Today","purpose":"Latest/current Chai Wala service-day operating report.","sections":["service-day coverage and data quality","headline KPIs","hourly dining demand","floor comparison","table utilization","observed service timing","kitchen and handoff pressure","operational/security exceptions","management reading"]},
    "yesterday":{"label":"Yesterday","purpose":"Completed prior Chai Wala service-day review with restaurant operations plus frozen security/evidence report when available.","sections":["service-day coverage and data quality","headline KPIs","hourly dining demand","floor comparison","table utilization","observed service timing","kitchen and handoff pressure","management reading","security/evidence report"]},
    "last_7_days":{"label":"Last 7 days","purpose":"Short-term operations pattern report with comparison to the previous seven service days.","sections":["period coverage","headline KPIs","previous-period comparison","service-day trend","demand by hour","floor comparison","table utilization ranking","observed service-time distribution","repeated operational patterns","management reading and practical actions"]},
    "last_30_days":{"label":"Last 30 days","purpose":"Management trend report for recurring demand, layout, service and operating-pattern decisions.","sections":["period coverage","headline KPIs","previous-period comparison","weekly trend","weekday pattern","demand by hour","floor comparison","high- and low-utilization tables","observed service-time distribution","recurring operational/security patterns","management improvement opportunities"]}
  }'::jsonb,
  true
),
updated_at=now()
where site_id='1a1fab32-10b3-4082-b61c-180ec04c758c';

CREATE OR REPLACE FUNCTION public.wl_restaurant_site_config(p_site_id uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
declare
  v_tenant uuid:=public.wl_assert_my_site(p_site_id);
  v_site public.sites;
  v_ctx public.site_business_context;
  v_type text;
begin
  select * into v_site from public.sites where id=p_site_id and tenant_id=v_tenant;
  if v_site.id is null then raise exception 'not authorized for this site' using errcode='42501'; end if;
  select * into v_ctx from public.site_business_context where site_id=p_site_id and tenant_id=v_tenant;
  v_type:=coalesce(v_ctx.site_type,v_site.site_type,'other');
  if v_type<>'restaurant' then return jsonb_build_object('enabled',false,'site_type',v_type); end if;
  return jsonb_build_object(
    'enabled',true,'site_id',p_site_id,'site_type','restaurant','timezone',v_site.timezone,
    'open_time',v_ctx.open_time,'close_time',v_ctx.close_time,'overnight',coalesce(v_ctx.overnight,false),
    'report_layout_profile',coalesce(v_ctx.reporting_prefs->>'report_layout_profile','restaurant_default_v1'),
    'intelligence_context',coalesce(v_ctx.reporting_prefs->'restaurant_intelligence_context','{}'::jsonb),
    'cameras',coalesce((
      select jsonb_agg(jsonb_build_object(
        'camera_id',p.camera_id,'camera',coalesce(c.name,'Camera '||coalesce(c.physical_channel,c.channel,'?')),
        'channel',coalesce(c.physical_channel,c.channel),'role',p.analytics_role,
        'sampling_mode',p.sampling_mode,'interval_seconds',p.interval_seconds,'enabled',p.enabled,'config',p.config
      ) order by coalesce(c.physical_channel,c.channel))
      from public.restaurant_camera_profiles p join public.cameras c on c.id=p.camera_id
      where p.site_id=p_site_id and p.tenant_id=v_tenant
    ),'[]'::jsonb),
    'tables',coalesce((
      select jsonb_agg(jsonb_build_object(
        'id',t.id,'camera_id',t.camera_id,'table_key',t.table_key,'label',t.label,
        'capacity',t.capacity,'tracking_mode',t.tracking_mode,'anchor',t.anchor,'roi',t.roi,
        'can_combine',t.can_combine,'active',t.active
      ) order by t.sort_order,t.table_key)
      from public.restaurant_tables t
      where t.site_id=p_site_id and t.tenant_id=v_tenant and t.active
    ),'[]'::jsonb)
  );
end $function$;


revoke execute on function public.wl_restaurant_site_config(uuid) from public,anon;
grant execute on function public.wl_restaurant_site_config(uuid) to authenticated,service_role;
