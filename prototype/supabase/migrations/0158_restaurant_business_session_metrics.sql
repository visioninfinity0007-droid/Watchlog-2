-- 0158 - Restaurant business-session metrics: de-duplicated covers, party mix and service responsiveness.
--
-- REPO ONLY until separately approved for production.
--
-- Why:
--   * restaurant-day-v3 can reconstruct sessions per calibrated table row, but a multi-camera
--     restaurant can show the same physical table/party from more than one view. Summing those rows
--     can double-count sessions/covers.
--   * the restaurant vision contract now records explicit visible table-service interactions, but
--     reports need a governed way to turn those observations into owner-facing business metrics.
--
-- This migration is additive. Existing wl_restaurant_day / wl_restaurant_period remain available.
-- The new RPCs fail closed on site-wide session totals until physical-table reconciliation is
-- explicitly configured. No gender, age or other appearance-derived demographics are produced.

alter table public.restaurant_tables
  add column if not exists physical_table_key text null,
  add column if not exists metrics_primary boolean not null default false;

comment on column public.restaurant_tables.physical_table_key is
  'Stable physical table/zone identity used to reconcile the same table across restaurant dining views. Multi-camera sites must configure this explicitly before site-wide sessions/covers are enabled.';
comment on column public.restaurant_tables.metrics_primary is
  'True for the one calibrated view used as the deterministic metrics source for a physical table. Multi-camera sites require exactly one primary row per physical_table_key.';

create index if not exists restaurant_tables_physical_metrics_idx
  on public.restaurant_tables(site_id, physical_table_key, metrics_primary)
  where active;

create or replace function public.wl_restaurant_business_day(
  p_site_id uuid,
  p_date date default null
) returns jsonb
language plpgsql
stable
security definer
set search_path = public, pg_temp
as $function$
declare
  v_tenant uuid := public.wl_assert_my_site(p_site_id);
  v_site public.sites;
  v_ctx public.site_business_context;
  v_type text;
  v_date date;
  v_local_now timestamp;
  v_now timestamptz := now();
  v_start timestamptz;
  v_end timestamptz;
  v_coverage jsonb;
  v_cover_scope text;
  v_dining_cameras integer := 0;
  v_configured_tables integer := 0;
  v_primary_tables integer := 0;
  v_mapping_ready boolean := false;
  v_mapping_reason text;
  v_metrics jsonb;
begin
  select * into v_site
    from public.sites
   where id=p_site_id and tenant_id=v_tenant;
  if v_site.id is null then
    raise exception 'not authorized for this site' using errcode='42501';
  end if;

  select * into v_ctx
    from public.site_business_context
   where site_id=p_site_id and tenant_id=v_tenant;

  v_type := coalesce(v_ctx.site_type,v_site.site_type,'other');
  if v_type <> 'restaurant' then
    return jsonb_build_object('enabled',false,'site_type',v_type);
  end if;

  v_local_now := v_now at time zone v_site.timezone;
  v_date := p_date;
  if v_date is null then
    v_date := v_local_now::date;
    if coalesce(v_ctx.overnight,false)
       and v_ctx.close_time is not null and v_ctx.open_time is not null
       and v_ctx.close_time <= v_ctx.open_time
       and v_local_now::time < v_ctx.open_time then
      v_date := v_date-1;
    end if;
  end if;

  v_start := (v_date + coalesce(v_ctx.open_time,'00:00'::time)) at time zone v_site.timezone;
  if coalesce(v_ctx.overnight,false)
     and v_ctx.close_time is not null and v_ctx.open_time is not null
     and v_ctx.close_time <= v_ctx.open_time then
    v_end := ((v_date+1)+v_ctx.close_time) at time zone v_site.timezone;
  else
    v_end := (v_date+coalesce(v_ctx.close_time,'23:59:59'::time)) at time zone v_site.timezone;
    if v_end<=v_start then v_end:=v_start+interval '1 day'; end if;
  end if;

  select count(*)::integer into v_dining_cameras
    from public.restaurant_camera_profiles p
    join public.cameras c on c.id=p.camera_id
   where p.site_id=p_site_id and p.tenant_id=v_tenant
     and p.enabled and p.analytics_role='dining_floor'
     and c.is_configured and coalesce(c.is_canonical,true);

  select count(*)::integer into v_configured_tables
    from public.restaurant_tables t
    join public.restaurant_camera_profiles p on p.camera_id=t.camera_id
    join public.cameras c on c.id=t.camera_id
   where t.site_id=p_site_id and t.tenant_id=v_tenant and t.active
     and p.enabled and p.analytics_role='dining_floor'
     and c.is_configured and coalesce(c.is_canonical,true);

  if v_dining_cameras=1 and v_configured_tables>0 then
    -- With one dining view there is no cross-camera table duplication. Existing table_key is a
    -- safe physical identity for metrics even before physical_table_key is explicitly populated.
    v_mapping_ready := true;
  elsif v_dining_cameras>1 and v_configured_tables>0 then
    select
      not exists (
        select 1
          from public.restaurant_tables t
          join public.restaurant_camera_profiles p on p.camera_id=t.camera_id
          join public.cameras c on c.id=t.camera_id
         where t.site_id=p_site_id and t.tenant_id=v_tenant and t.active
           and p.enabled and p.analytics_role='dining_floor'
           and c.is_configured and coalesce(c.is_canonical,true)
           and nullif(trim(t.physical_table_key),'') is null
      )
      and not exists (
        select 1
          from public.restaurant_tables t
          join public.restaurant_camera_profiles p on p.camera_id=t.camera_id
          join public.cameras c on c.id=t.camera_id
         where t.site_id=p_site_id and t.tenant_id=v_tenant and t.active
           and p.enabled and p.analytics_role='dining_floor'
           and c.is_configured and coalesce(c.is_canonical,true)
         group by t.physical_table_key
        having count(*) filter(where t.metrics_primary) <> 1
      )
    into v_mapping_ready;
  end if;

  if not v_mapping_ready then
    v_mapping_reason := case
      when v_dining_cameras=0 then 'No calibrated dining view is configured for restaurant session metrics.'
      when v_configured_tables=0 then 'No calibrated dining tables are configured for restaurant session metrics.'
      else 'The dining views do not yet have a confirmed physical-table mapping with exactly one metrics view per physical table. Site-wide covers and sessions remain unavailable to avoid double-counting.'
    end;
  end if;

  select count(*)::integer into v_primary_tables
  from public.restaurant_tables t
  join public.restaurant_camera_profiles p on p.camera_id=t.camera_id
  join public.cameras c on c.id=t.camera_id
  where t.site_id=p_site_id and t.tenant_id=v_tenant and t.active
    and p.enabled and p.analytics_role='dining_floor'
    and c.is_configured and coalesce(c.is_canonical,true)
    and (v_dining_cameras=1 or t.metrics_primary);

  v_coverage := public.wl_effective_site_coverage(
    p_site_id,
    v_start,
    least(v_end,v_now)
  );

  v_cover_scope := case
    when v_now>=v_end
     and coalesce((v_coverage->>'unverified_seconds')::numeric,0)=0
      then 'full_service_day'
    else 'represented_period'
  end;

  if v_mapping_ready then
    with primary_tables as (
      select
        t.id as table_id,
        t.camera_id,
        t.table_key,
        t.label,
        coalesce(nullif(trim(t.physical_table_key),''),t.table_key) as physical_table_key
      from public.restaurant_tables t
      join public.restaurant_camera_profiles p on p.camera_id=t.camera_id
      join public.cameras c on c.id=t.camera_id
      where t.site_id=p_site_id and t.tenant_id=v_tenant and t.active
        and p.enabled and p.analytics_role='dining_floor'
        and c.is_configured and coalesce(c.is_canonical,true)
        and (v_dining_cameras=1 or t.metrics_primary)
    ),
    raw_observations as (
      select
        o.event_id,
        o.table_id,
        p.camera_id,
        p.table_key,
        p.label,
        p.physical_table_key,
        o.captured_at,
        o.occupied,
        o.customer_count,
        o.food_present,
        o.drinks_present,
        o.clearing_state,
        nullif(trim(o.combined_group),'') as combined_group,
        o.confidence,
        exists (
          select 1
          from jsonb_array_elements(
            case when jsonb_typeof(v.activity->'tables')='array'
                 then v.activity->'tables' else '[]'::jsonb end
          ) x
          where x->>'table_key'=p.table_key
            and lower(coalesce(x->>'service_interaction_observed','')) in ('true','1','yes')
        ) as service_interaction_observed
      from public.restaurant_table_observations o
      join primary_tables p on p.table_id=o.table_id
      left join public.restaurant_visual_observations v on v.event_id=o.event_id
      where o.site_id=p_site_id and o.tenant_id=v_tenant
        and o.captured_at>=v_start and o.captured_at<v_end
    ),
    party_observations as (
      -- Joined tables in one reviewed dining view share a deterministic combined_group. Collapse
      -- them to one party observation before sessionization so one party is not counted once per
      -- joined table.
      select
        case when combined_group is null
             then 'table:'||physical_table_key
             else 'joined:'||camera_id::text||':'||combined_group end as party_key,
        event_id,
        min(captured_at) as captured_at,
        bool_or(occupied is true) as occupied,
        max(customer_count) filter(where occupied is true) as customer_count,
        bool_or(food_present is true) as food_present,
        count(*) filter(where food_present is not null) as food_observation_rows,
        bool_or(service_interaction_observed) as service_interaction_observed,
        bool_or(clearing_state is true) as clearing_state,
        min(label) as label,
        min(physical_table_key) as physical_table_key,
        round(avg(confidence)::numeric,3) as confidence
      from raw_observations
      group by
        case when combined_group is null
             then 'table:'||physical_table_key
             else 'joined:'||camera_id::text||':'||combined_group end,
        event_id
    ),
    intervals as (
      select p.*,
        extract(epoch from (
          p.captured_at-lag(p.captured_at) over(partition by p.party_key order by p.captured_at)
        )) as interval_seconds
      from party_observations p
    ),
    cadence as (
      select party_key,
        percentile_cont(0.5) within group(order by interval_seconds)
          filter(where interval_seconds is not null and interval_seconds>0) as median_interval_seconds
      from intervals
      group by party_key
    ),
    ordered as (
      select i.*,
        lag(i.occupied) over(partition by i.party_key order by i.captured_at) as prev_occupied,
        lag(i.captured_at) over(partition by i.party_key order by i.captured_at) as prev_at,
        least(1200.0,greatest(300.0,coalesce(c.median_interval_seconds,300.0)*3.0)) as gap_threshold_seconds
      from intervals i
      left join cadence c using(party_key)
    ),
    flagged as (
      select o.*,
        case when o.occupied is true and (
          coalesce(o.prev_occupied,false)=false
          or o.prev_at is null
          or extract(epoch from (o.captured_at-o.prev_at))>o.gap_threshold_seconds
        ) then 1 else 0 end as session_start
      from ordered o
    ),
    tagged as (
      select f.*,
        sum(f.session_start) over(
          partition by f.party_key order by f.captured_at rows unbounded preceding
        ) as session_no
      from flagged f
    ),
    session_rows as (
      select
        party_key,
        session_no,
        min(captured_at) filter(where occupied is true) as started_at,
        max(captured_at) filter(where occupied is true) as last_occupied_at,
        min(captured_at) filter(where occupied is true and service_interaction_observed) as first_service_at,
        min(captured_at) filter(where occupied is true and food_present) as first_food_at,
        max(customer_count) filter(where occupied is true) as peak_party,
        count(*) filter(where occupied is true) as occupied_observations,
        bool_or(food_present is true) filter(where occupied is true) as served,
        sum(food_observation_rows) filter(where occupied is true) as food_observation_rows,
        min(label) as label,
        min(physical_table_key) as physical_table_key,
        round(avg(confidence) filter(where occupied is true)::numeric,3) as confidence
      from tagged
      where session_no>0
      group by party_key,session_no
    ),
    qualified as (
      -- A single occupied observation is an observation, not a defensible session.
      select s.*,
        extract(epoch from (s.last_occupied_at-s.started_at))/60.0 as minimum_dwell_minutes,
        case when s.first_service_at is null then null
             else extract(epoch from (s.first_service_at-s.started_at))/60.0 end as first_service_minutes,
        case when s.first_food_at is null then null
             else extract(epoch from (s.first_food_at-s.started_at))/60.0 end as first_served_items_minutes
      from session_rows s
      where s.occupied_observations>=2
        and s.started_at is not null
        and s.last_occupied_at is not null
    ),
    base_metrics as (
      select
        count(*)::integer as session_sample_size,
        count(*) filter(where peak_party is not null)::integer as cover_sample_sessions,
        coalesce(sum(peak_party) filter(where peak_party is not null),0)::integer as estimated_covers,
        round(avg(peak_party) filter(where peak_party is not null)::numeric,1) as average_party_size,
        max(peak_party) filter(where peak_party is not null)::integer as largest_visible_party,
        count(*) filter(where peak_party=1)::integer as one_person,
        count(*) filter(where peak_party=2)::integer as two_people,
        count(*) filter(where peak_party between 3 and 4)::integer as three_to_four,
        count(*) filter(where peak_party>=5)::integer as five_plus,
        count(*) filter(where food_observation_rows>0)::integer as served_rate_sample_sessions,
        count(*) filter(where food_observation_rows>0 and served)::integer as served_sessions,
        count(*) filter(where first_service_minutes is not null)::integer as first_service_sample_sessions,
        round((percentile_cont(0.5) within group(order by first_service_minutes)
          filter(where first_service_minutes is not null))::numeric,1) as median_time_to_first_service_minutes,
        count(*) filter(where first_served_items_minutes is not null)::integer as served_items_time_sample_sessions,
        round((percentile_cont(0.5) within group(order by first_served_items_minutes)
          filter(where first_served_items_minutes is not null))::numeric,1) as median_time_to_first_served_items_minutes,
        count(*) filter(where minimum_dwell_minutes>0)::integer as dwell_sample_sessions,
        round((percentile_cont(0.5) within group(order by minimum_dwell_minutes)
          filter(where minimum_dwell_minutes>0))::numeric,1) as median_minimum_observed_dwell_minutes,
        coalesce(jsonb_agg(round(first_service_minutes::numeric,1))
          filter(where first_service_minutes is not null),'[]'::jsonb) as first_service_samples,
        coalesce(jsonb_agg(round(first_served_items_minutes::numeric,1))
          filter(where first_served_items_minutes is not null),'[]'::jsonb) as served_items_samples,
        coalesce(jsonb_agg(round(minimum_dwell_minutes::numeric,1))
          filter(where minimum_dwell_minutes>0),'[]'::jsonb) as dwell_samples
      from qualified
    ),
    starts as (
      select
        to_char(date_trunc('hour',started_at at time zone v_site.timezone),'HH24:00') as period,
        count(*)::integer as sessions
      from qualified
      group by date_trunc('hour',started_at at time zone v_site.timezone)
      order by date_trunc('hour',started_at at time zone v_site.timezone)
    ),
    turnover as (
      select
        physical_table_key,
        min(label) as label,
        count(*)::integer as sessions
      from qualified
      where party_key like 'table:%'
      group by physical_table_key
      order by count(*) desc,physical_table_key
    ),
    high_low as (
      select
        q.*,
        count(*) over(partition by date_trunc('hour',q.started_at at time zone v_site.timezone)) as starts_in_hour
      from qualified q
      where q.first_service_minutes is not null
    ),
    demand_cut as (
      select percentile_cont(0.75) within group(order by starts_in_hour) as q3
      from high_low
    ),
    response_compare as (
      select
        count(*) filter(where h.starts_in_hour>=d.q3)::integer as peak_n,
        count(*) filter(where h.starts_in_hour<d.q3)::integer as other_n,
        round((percentile_cont(0.5) within group(order by h.first_service_minutes)
          filter(where h.starts_in_hour>=d.q3))::numeric,1) as peak_median,
        round((percentile_cont(0.5) within group(order by h.first_service_minutes)
          filter(where h.starts_in_hour<d.q3))::numeric,1) as other_median
      from high_low h cross join demand_cut d
    )
    select jsonb_build_object(
      'estimated_covers',case when b.cover_sample_sessions=0 then null else b.estimated_covers end,
      'cover_sample_sessions',b.cover_sample_sessions,
      'estimated_table_sessions',b.session_sample_size,
      'session_sample_size',b.session_sample_size,
      'average_party_size',b.average_party_size,
      'party_size_sample_sessions',b.cover_sample_sessions,
      'party_size_mix',jsonb_build_object(
        'one_person',b.one_person,
        'two_people',b.two_people,
        'three_to_four',b.three_to_four,
        'five_plus',b.five_plus
      ),
      'largest_visible_party',b.largest_visible_party,
      'served_sessions',b.served_sessions,
      'served_rate_sample_sessions',b.served_rate_sample_sessions,
      'served_session_rate_pct',case when b.served_rate_sample_sessions=0 then null
        else round(100.0*b.served_sessions/b.served_rate_sample_sessions,1) end,
      'median_time_to_first_service_minutes',b.median_time_to_first_service_minutes,
      'first_service_sample_sessions',b.first_service_sample_sessions,
      'median_time_to_first_served_items_minutes',b.median_time_to_first_served_items_minutes,
      'served_items_time_sample_sessions',b.served_items_time_sample_sessions,
      'median_minimum_observed_dwell_minutes',b.median_minimum_observed_dwell_minutes,
      'dwell_sample_sessions',b.dwell_sample_sessions,
      'session_starts_by_period',coalesce((select jsonb_agg(to_jsonb(s) order by s.period) from starts s),'[]'::jsonb),
      'table_turnover',coalesce((select jsonb_agg(to_jsonb(t)) from turnover t),'[]'::jsonb),
      'service_slowdown_vs_demand',(
        select case
          when r.peak_n>=3 and r.other_n>=3 and r.peak_median is not null and r.other_median is not null
            then jsonb_build_object(
              'status','supported',
              'peak_period_sample_sessions',r.peak_n,
              'other_period_sample_sessions',r.other_n,
              'peak_period_median_first_service_minutes',r.peak_median,
              'other_period_median_first_service_minutes',r.other_median,
              'delta_minutes',round(r.peak_median-r.other_median,1)
            )
          else jsonb_build_object(
            'status','unavailable',
            'reason','Not enough qualifying first-service sessions exist in both higher-demand and other periods for a defensible comparison.',
            'peak_period_sample_sessions',coalesce(r.peak_n,0),
            'other_period_sample_sessions',coalesce(r.other_n,0)
          )
        end
        from response_compare r
      ),
      'samples',jsonb_build_object(
        'first_service_minutes',b.first_service_samples,
        'served_items_minutes',b.served_items_samples,
        'minimum_dwell_minutes',b.dwell_samples
      )
    )
    into v_metrics
    from base_metrics b;
  end if;

  return jsonb_build_object(
    'enabled',true,
    'schema','restaurant-business-day-v1',
    'service_date',v_date,
    'timezone',v_site.timezone,
    'window',jsonb_build_object('start',v_start,'end',v_end),
    'cover_scope',v_cover_scope,
    'reconciliation',jsonb_build_object(
      'ready',v_mapping_ready,
      'reason',v_mapping_reason,
      'dining_cameras',v_dining_cameras,
      'configured_table_views',v_configured_tables,
      'metrics_primary_tables',v_primary_tables
    ),
    'coverage',v_coverage,
    'metrics',case when v_mapping_ready then coalesce(v_metrics,'{}'::jsonb) else jsonb_build_object(
      'estimated_covers',null,
      'estimated_table_sessions',null,
      'average_party_size',null,
      'party_size_mix',null,
      'largest_visible_party',null,
      'served_session_rate_pct',null,
      'median_time_to_first_service_minutes',null,
      'median_time_to_first_served_items_minutes',null,
      'median_minimum_observed_dwell_minutes',null,
      'session_starts_by_period','[]'::jsonb,
      'table_turnover','[]'::jsonb,
      'service_slowdown_vs_demand',jsonb_build_object('status','unavailable','reason',v_mapping_reason)
    ) end,
    'measurement_notes',jsonb_build_array(
      'Estimated covers count a de-duplicated visible party once per qualifying dining session; they are not unique footfall or POS covers.',
      'A full-service-day cover label is used only when the configured service day is complete and has no unverified monitoring time. Otherwise the figure is limited to the represented period.',
      'Party-size groups describe visible party size only and do not infer relationship, gender, age or other demographics.',
      'Time to first service is seating to the first defensible visible table-service interaction; it is not proven order-placement time.',
      'Time to served items is seating to the first clearly visible served items; it is not POS or kitchen ticket time.',
      'Every session-derived timing/rate is accompanied by its qualifying session count.'
    )
  );
end
$function$;

revoke all on function public.wl_restaurant_business_day(uuid,date) from public,anon;
grant execute on function public.wl_restaurant_business_day(uuid,date) to authenticated,service_role;


create or replace function public.wl_restaurant_business_period(
  p_site_id uuid,
  p_days integer,
  p_end_date date default null
) returns jsonb
language plpgsql
stable
security definer
set search_path = public, pg_temp
as $function$
declare
  v_tenant uuid := public.wl_assert_my_site(p_site_id);
  v_site public.sites;
  v_ctx public.site_business_context;
  v_end_date date;
  v_start_date date;
  v_date date;
  v_day jsonb;
  v_days jsonb := '[]'::jsonb;
  v_first_service_samples jsonb := '[]'::jsonb;
  v_food_samples jsonb := '[]'::jsonb;
  v_dwell_samples jsonb := '[]'::jsonb;
  v_reconciled integer := 0;
  v_total_covers integer := 0;
  v_total_sessions integer := 0;
  v_cover_samples integer := 0;
  v_party_one integer := 0;
  v_party_two integer := 0;
  v_party_three_four integer := 0;
  v_party_five_plus integer := 0;
  v_summary jsonb;
begin
  if p_days<2 or p_days>31 then
    raise exception 'days must be between 2 and 31' using errcode='22023';
  end if;

  select * into v_site from public.sites where id=p_site_id and tenant_id=v_tenant;
  if v_site.id is null then raise exception 'not authorized for this site' using errcode='42501'; end if;
  select * into v_ctx from public.site_business_context where site_id=p_site_id and tenant_id=v_tenant;
  if coalesce(v_ctx.site_type,v_site.site_type,'other')<>'restaurant' then
    return jsonb_build_object('enabled',false,'site_type',coalesce(v_ctx.site_type,v_site.site_type,'other'));
  end if;

  v_end_date := coalesce(p_end_date,(now() at time zone v_site.timezone)::date);
  if p_end_date is null
     and coalesce(v_ctx.overnight,false)
     and v_ctx.open_time is not null and v_ctx.close_time is not null
     and v_ctx.close_time<=v_ctx.open_time
     and (now() at time zone v_site.timezone)::time<v_ctx.open_time then
    v_end_date:=v_end_date-1;
  end if;
  v_start_date:=v_end_date-(p_days-1);
  v_date:=v_start_date;

  while v_date<=v_end_date loop
    v_day:=public.wl_restaurant_business_day(p_site_id,v_date);

    if coalesce((v_day->'reconciliation'->>'ready')::boolean,false)
       and nullif(v_day->'metrics'->>'estimated_table_sessions','') is not null then
      v_reconciled:=v_reconciled+1;
      v_total_covers:=v_total_covers+coalesce((v_day->'metrics'->>'estimated_covers')::integer,0);
      v_total_sessions:=v_total_sessions+coalesce((v_day->'metrics'->>'estimated_table_sessions')::integer,0);
      v_cover_samples:=v_cover_samples+coalesce((v_day->'metrics'->>'cover_sample_sessions')::integer,0);
      v_party_one:=v_party_one+coalesce((v_day->'metrics'->'party_size_mix'->>'one_person')::integer,0);
      v_party_two:=v_party_two+coalesce((v_day->'metrics'->'party_size_mix'->>'two_people')::integer,0);
      v_party_three_four:=v_party_three_four+coalesce((v_day->'metrics'->'party_size_mix'->>'three_to_four')::integer,0);
      v_party_five_plus:=v_party_five_plus+coalesce((v_day->'metrics'->'party_size_mix'->>'five_plus')::integer,0);
      v_first_service_samples:=v_first_service_samples||coalesce(v_day->'metrics'->'samples'->'first_service_minutes','[]'::jsonb);
      v_food_samples:=v_food_samples||coalesce(v_day->'metrics'->'samples'->'served_items_minutes','[]'::jsonb);
      v_dwell_samples:=v_dwell_samples||coalesce(v_day->'metrics'->'samples'->'minimum_dwell_minutes','[]'::jsonb);
    end if;

    v_days:=v_days||jsonb_build_array(jsonb_build_object(
      'service_date',v_date,
      'reconciliation_ready',coalesce((v_day->'reconciliation'->>'ready')::boolean,false),
      'cover_scope',v_day->>'cover_scope',
      'estimated_covers',v_day->'metrics'->'estimated_covers',
      'table_sessions',v_day->'metrics'->'estimated_table_sessions',
      'average_party_size',v_day->'metrics'->'average_party_size',
      'party_size_mix',v_day->'metrics'->'party_size_mix',
      'median_time_to_first_service_minutes',v_day->'metrics'->'median_time_to_first_service_minutes',
      'first_service_sample_sessions',v_day->'metrics'->'first_service_sample_sessions',
      'coverage_ratio',v_day->'coverage'->'classes'->'total_coverage_ratio'
    ));

    v_date:=v_date+1;
  end loop;

  select jsonb_build_object(
    'reconciled_service_days',v_reconciled,
    'total_estimated_covers',case when v_reconciled=0 then null else v_total_covers end,
    'avg_estimated_covers_per_reconciled_day',case when v_reconciled=0 then null else round(v_total_covers::numeric/v_reconciled,1) end,
    'total_table_sessions',case when v_reconciled=0 then null else v_total_sessions end,
    'avg_table_sessions_per_reconciled_day',case when v_reconciled=0 then null else round(v_total_sessions::numeric/v_reconciled,1) end,
    'average_party_size',case when v_cover_samples=0 then null else round(v_total_covers::numeric/v_cover_samples,1) end,
    'party_size_sample_sessions',v_cover_samples,
    'party_size_mix',jsonb_build_object(
      'one_person',v_party_one,
      'two_people',v_party_two,
      'three_to_four',v_party_three_four,
      'five_plus',v_party_five_plus
    ),
    'median_time_to_first_service_minutes',(
      select round((percentile_cont(0.5) within group(order by value::text::numeric))::numeric,1)
      from jsonb_array_elements(v_first_service_samples)
    ),
    'first_service_sample_sessions',jsonb_array_length(v_first_service_samples),
    'median_time_to_first_served_items_minutes',(
      select round((percentile_cont(0.5) within group(order by value::text::numeric))::numeric,1)
      from jsonb_array_elements(v_food_samples)
    ),
    'served_items_time_sample_sessions',jsonb_array_length(v_food_samples),
    'median_minimum_observed_dwell_minutes',(
      select round((percentile_cont(0.5) within group(order by value::text::numeric))::numeric,1)
      from jsonb_array_elements(v_dwell_samples)
    ),
    'dwell_sample_sessions',jsonb_array_length(v_dwell_samples)
  ) into v_summary;

  return jsonb_build_object(
    'enabled',true,
    'schema','restaurant-business-period-v1',
    'days',p_days,
    'timezone',v_site.timezone,
    'period',jsonb_build_object('start_service_date',v_start_date,'end_service_date',v_end_date),
    'summary',v_summary,
    'daily',v_days,
    'measurement_notes',jsonb_build_array(
      'Only service days with reconciled physical-table session metrics contribute to covers, party-size and service-timing totals.',
      'Missing or unreconciled days are unknown and are never treated as zero demand.',
      'Appearance-derived customer demographics are not produced.'
    )
  );
end
$function$;

revoke all on function public.wl_restaurant_business_period(uuid,integer,date) from public,anon;
grant execute on function public.wl_restaurant_business_period(uuid,integer,date) to authenticated,service_role;
