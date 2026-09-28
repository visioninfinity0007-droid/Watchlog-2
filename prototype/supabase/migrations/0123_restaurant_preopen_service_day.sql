-- Keep the just-finished overnight restaurant service day active until the next opening.
-- Example: for a 16:00-04:00 restaurant, 07:00 still resolves to yesterday's service date.

CREATE OR REPLACE FUNCTION public.wl_restaurant_day(p_site_id uuid, p_date date DEFAULT NULL::date)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
declare
  v_tenant uuid:=public.wl_assert_my_site(p_site_id);
  v_site public.sites;
  v_ctx public.site_business_context;
  v_type text; v_date date; v_local_now timestamp; v_start timestamptz; v_end timestamptz;
  v_hourly jsonb; v_tables jsonb; v_sessions jsonb; v_observations integer; v_table_observations integer;
begin
  select * into v_site from public.sites where id=p_site_id and tenant_id=v_tenant;
  if v_site.id is null then raise exception 'not authorized for this site' using errcode='42501'; end if;
  select * into v_ctx from public.site_business_context where site_id=p_site_id and tenant_id=v_tenant;
  v_type:=coalesce(v_ctx.site_type,v_site.site_type,'other');
  if v_type<>'restaurant' then return jsonb_build_object('enabled',false,'site_type',v_type); end if;

  v_local_now:=now() at time zone v_site.timezone;
  v_date:=p_date;
  if v_date is null then
    v_date:=v_local_now::date;
    if coalesce(v_ctx.overnight,false) and v_ctx.close_time is not null and v_ctx.open_time is not null
       and v_ctx.close_time<=v_ctx.open_time and v_local_now::time<v_ctx.open_time then
      v_date:=v_date-1;
    end if;
  end if;
  v_start:=(v_date+coalesce(v_ctx.open_time,'00:00'::time)) at time zone v_site.timezone;
  if coalesce(v_ctx.overnight,false) and v_ctx.close_time is not null and v_ctx.open_time is not null
     and v_ctx.close_time<=v_ctx.open_time then
    v_end:=((v_date+1)+v_ctx.close_time) at time zone v_site.timezone;
  else
    v_end:=(v_date+coalesce(v_ctx.close_time,'23:59:59'::time)) at time zone v_site.timezone;
    if v_end<=v_start then v_end:=v_start+interval '1 day'; end if;
  end if;

  select count(*) into v_observations from public.restaurant_visual_observations o
   where o.site_id=p_site_id and o.tenant_id=v_tenant and o.captured_at>=v_start and o.captured_at<v_end;
  select count(*) into v_table_observations from public.restaurant_table_observations o
   where o.site_id=p_site_id and o.tenant_id=v_tenant and o.captured_at>=v_start and o.captured_at<v_end;

  with hours as (
    select generate_series(v_start,v_end-interval '1 hour',interval '1 hour') hour_start
  ), agg as (
    select h.hour_start,count(o.event_id) samples,
      round(avg(o.visible_customers)::numeric,1) avg_visible_customers,max(o.visible_customers) peak_visible_customers,
      round(avg(o.occupied_tables)::numeric,1) avg_occupied_tables,max(o.occupied_tables) peak_occupied_tables,
      round(avg(o.kitchen_load)::numeric,3) avg_kitchen_load,round(avg(o.handoff_load)::numeric,3) avg_handoff_load
    from hours h left join public.restaurant_visual_observations o
      on o.site_id=p_site_id and o.tenant_id=v_tenant
     and o.captured_at>=h.hour_start and o.captured_at<h.hour_start+interval '1 hour'
    group by h.hour_start
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'hour_start',a.hour_start,'local_hour',to_char(a.hour_start at time zone v_site.timezone,'HH24:MI'),
    'samples',a.samples,'avg_visible_customers',a.avg_visible_customers,'peak_visible_customers',a.peak_visible_customers,
    'avg_occupied_tables',a.avg_occupied_tables,'peak_occupied_tables',a.peak_occupied_tables,
    'avg_kitchen_load',a.avg_kitchen_load,'avg_handoff_load',a.avg_handoff_load
  ) order by a.hour_start),'[]'::jsonb) into v_hourly from agg a;

  with per_table as (
    select t.id,t.table_key,t.label,t.camera_id,t.capacity,t.sort_order,count(o.event_id) samples,
      count(o.event_id) filter(where o.occupied is true) occupied_samples,
      count(o.event_id) filter(where o.food_present is true) food_samples,
      round(avg(o.customer_count) filter(where o.occupied is true)::numeric,1) avg_party_when_occupied,
      max(o.customer_count) peak_party,round(avg(o.confidence)::numeric,3) avg_confidence
    from public.restaurant_tables t left join public.restaurant_table_observations o
      on o.table_id=t.id and o.captured_at>=v_start and o.captured_at<v_end
    where t.site_id=p_site_id and t.tenant_id=v_tenant and t.active
    group by t.id,t.table_key,t.label,t.camera_id,t.capacity,t.sort_order
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'table_id',p.id,'table_key',p.table_key,'label',p.label,'camera_id',p.camera_id,'capacity',p.capacity,
    'samples',p.samples,'occupied_samples',p.occupied_samples,
    'occupancy_pct',case when p.samples=0 then null else round(100.0*p.occupied_samples/p.samples,1) end,
    'food_present_samples',p.food_samples,'avg_party_when_occupied',p.avg_party_when_occupied,
    'peak_party',p.peak_party,'avg_confidence',p.avg_confidence
  ) order by p.sort_order,p.table_key),'[]'::jsonb) into v_tables from per_table p;

  with ordered as (
    select o.*,lag(o.occupied) over(partition by o.table_id order by o.captured_at) prev_occupied,
      lag(o.captured_at) over(partition by o.table_id order by o.captured_at) prev_at
    from public.restaurant_table_observations o
    where o.site_id=p_site_id and o.tenant_id=v_tenant and o.captured_at>=v_start and o.captured_at<v_end
  ), flagged as (
    select o.*,case when o.occupied is true and (
      coalesce(o.prev_occupied,false)=false or o.prev_at is null or o.captured_at-o.prev_at>interval '20 minutes'
    ) then 1 else 0 end session_start from ordered o
  ), tagged as (
    select f.*,sum(f.session_start) over(partition by f.table_id order by f.captured_at rows unbounded preceding) session_no
    from flagged f
  ), session_rows as (
    select t.table_id,t.session_no,min(t.captured_at) started_at,max(t.captured_at) last_occupied_at,
      min(t.captured_at) filter(where t.food_present is true) first_food_at,max(t.customer_count) peak_party,
      bool_or(t.food_present is true) served,round(avg(t.confidence)::numeric,3) confidence
    from tagged t where t.occupied is true and t.session_no>0 group by t.table_id,t.session_no
  ), named as (
    select s.*,rt.table_key,rt.label,
      extract(epoch from (s.first_food_at-s.started_at))/60.0 time_to_food_min,
      extract(epoch from (s.last_occupied_at-s.started_at))/60.0 observed_dwell_min
    from session_rows s join public.restaurant_tables rt on rt.id=s.table_id
  )
  select jsonb_build_object(
    'count',count(*),'estimated_covers',coalesce(sum(coalesce(n.peak_party,0)),0),
    'served_sessions',count(*) filter(where n.served),
    'avg_observed_time_to_food_minutes',round((avg(n.time_to_food_min) filter(where n.time_to_food_min is not null))::numeric,1),
    'median_observed_time_to_food_minutes',round((percentile_cont(0.5) within group(order by n.time_to_food_min) filter(where n.time_to_food_min is not null))::numeric,1),
    'median_minimum_observed_dwell_minutes',round((percentile_cont(0.5) within group(order by n.observed_dwell_min) filter(where n.observed_dwell_min is not null))::numeric,1),
    'items',coalesce(jsonb_agg(jsonb_build_object(
      'table_key',n.table_key,'label',n.label,'session_no',n.session_no,'started_at',n.started_at,
      'last_occupied_at',n.last_occupied_at,'first_food_at',n.first_food_at,'served',n.served,'peak_party',n.peak_party,
      'observed_time_to_food_minutes',case when n.time_to_food_min is null then null else round(n.time_to_food_min::numeric,1) end,
      'minimum_observed_dwell_minutes',round(n.observed_dwell_min::numeric,1),'confidence',n.confidence
    ) order by n.started_at) filter(where n.table_id is not null),'[]'::jsonb)
  ) into v_sessions from named n;

  return jsonb_build_object(
    'enabled',true,'schema','restaurant-day-v1','service_date',v_date,'timezone',v_site.timezone,
    'window',jsonb_build_object('start',v_start,'end',v_end),'hourly',coalesce(v_hourly,'[]'::jsonb),
    'tables',coalesce(v_tables,'[]'::jsonb),
    'sessions',coalesce(v_sessions,jsonb_build_object('count',0,'estimated_covers',0,'served_sessions',0,'items','[]'::jsonb)),
    'data_quality',jsonb_build_object(
      'camera_observations',v_observations,'table_observations',v_table_observations,
      'has_table_calibration',exists(select 1 from public.restaurant_tables t where t.site_id=p_site_id and t.tenant_id=v_tenant and t.active),
      'measurement_notes',jsonb_build_array(
        'Visible customers are concurrent visible diners, not unique footfall.',
        'Estimated covers sum peak party size across observed table sessions and may be imperfect under occlusion or table movement.',
        'Observed time to food is seated-to-first-food-visible, not POS order-to-serve time.',
        'Minimum observed dwell uses the last occupied observation and can understate the true departure time.'
      )
    )
  );
end $function$
;

CREATE OR REPLACE FUNCTION public.wl_generate_daily_report(p_site_id uuid, p_date date DEFAULT NULL::date, p_force boolean DEFAULT false)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
declare
  v_site public.sites;
  v_ctx public.site_business_context;
  v_is_restaurant boolean;
  v_local_now timestamp;
  v_date date;
  v_existing uuid;
  v_payload jsonb;
  v_inf text;
  v_id uuid;
  v_rev int;
begin
  select * into v_site from public.sites where id=p_site_id;
  if v_site.id is null then raise exception 'no such site' using errcode='22023'; end if;
  select * into v_ctx from public.site_business_context where site_id=p_site_id;
  v_is_restaurant:=coalesce(v_ctx.site_type,v_site.site_type,'other')='restaurant';
  v_local_now:=now() at time zone v_site.timezone;

  if p_date is not null then
    v_date:=p_date;
  elsif v_is_restaurant
    and coalesce(v_ctx.overnight,false)
    and v_ctx.open_time is not null
    and v_ctx.close_time is not null
    and v_ctx.close_time<=v_ctx.open_time
    and v_local_now::time<v_ctx.open_time then
      v_date:=v_local_now::date-1;
  else
    v_date:=v_local_now::date;
  end if;

  select id into v_existing
    from public.report_snapshots
   where site_id=p_site_id and report_date=v_date;
  if v_existing is not null and not p_force then
    return (select jsonb_build_object(
      'report_id',id,'frozen',true,'revision',revision,
      'generated_at',generated_at,'payload',payload)
      from public.report_snapshots where id=v_existing);
  end if;

  v_payload:=public.wl_daily_intelligence(p_site_id,v_date,true);
  if v_is_restaurant then
    v_payload:=v_payload||jsonb_build_object(
      'restaurant',public.wl_restaurant_day(p_site_id,v_date)
    );
  end if;

  select version into v_inf
    from public.inference_config
   where site_id=p_site_id or site_id is null
   order by (site_id is not null) desc
   limit 1;

  insert into public.report_snapshots(
    tenant_id,site_id,report_date,payload,payload_schema,versions,coverage_ratio
  )
  values(
    v_site.tenant_id,p_site_id,v_date,v_payload,v_payload->>'schema',
    jsonb_build_object(
      'inference',coalesce(v_inf,'inference-v1'),
      'journeys','topology-v2',
      'day_state','state-machine-v1',
      'intelligence',v_payload->>'schema',
      'restaurant',case when v_is_restaurant then 'restaurant-day-v1' else null end
    ),
    (v_payload->'coverage'->>'coverage_ratio')::numeric
  )
  on conflict(site_id,report_date) do update
     set payload=excluded.payload,
         payload_schema=excluded.payload_schema,
         versions=excluded.versions,
         coverage_ratio=excluded.coverage_ratio,
         generated_at=now(),
         revision=public.report_snapshots.revision+1,
         delivery_status='pending',
         pdf_sha256=null,
         pdf_bytes=null
  returning id,revision into v_id,v_rev;

  return jsonb_build_object(
    'report_id',v_id,'frozen',false,'revision',v_rev,
    'generated_at',now(),'payload',v_payload
  );
end $function$
;


revoke execute on function public.wl_restaurant_day(uuid,date) from anon,public;
revoke execute on function public.wl_restaurant_site_config(uuid) from anon,public;
grant execute on function public.wl_restaurant_day(uuid,date) to authenticated,service_role;
grant execute on function public.wl_restaurant_site_config(uuid) to authenticated,service_role;
