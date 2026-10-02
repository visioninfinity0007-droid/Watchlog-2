-- Restaurant vertical analytics for WatchLog.
-- Generic schema only: tenant-specific calibration lives under tenant-config/.
-- This layer consumes structured output from snapshot_visual_reviews and never
-- changes the canonical security/event pipeline.

create table if not exists public.restaurant_camera_profiles (
  camera_id uuid primary key references public.cameras(id) on delete cascade,
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  site_id uuid not null references public.sites(id) on delete cascade,
  analytics_role text not null check (analytics_role in (
    'dining_floor','service_handoff','cash_counter','kitchen',
    'service_access','office_security','customer_entrance','other'
  )),
  sampling_mode text not null default 'interval'
    check (sampling_mode in ('interval','event','hybrid')),
  interval_seconds integer null
    check (interval_seconds is null or interval_seconds between 15 and 3600),
  enabled boolean not null default true,
  config jsonb not null default '{}'::jsonb
    check (jsonb_typeof(config)='object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists restaurant_camera_profiles_site_idx
  on public.restaurant_camera_profiles(site_id,enabled,analytics_role);
create index if not exists restaurant_camera_profiles_tenant_idx
  on public.restaurant_camera_profiles(tenant_id,site_id);

create table if not exists public.restaurant_tables (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  site_id uuid not null references public.sites(id) on delete cascade,
  camera_id uuid not null references public.cameras(id) on delete cascade,
  table_key text not null,
  label text not null,
  capacity integer null check (capacity is null or capacity between 1 and 30),
  tracking_mode text not null default 'anchor_match'
    check (tracking_mode in ('fixed_roi','anchor_match','dynamic')),
  anchor jsonb not null default '{}'::jsonb check (jsonb_typeof(anchor)='object'),
  roi jsonb not null default '{}'::jsonb check (jsonb_typeof(roi)='object'),
  can_combine boolean not null default true,
  active boolean not null default true,
  sort_order integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(site_id,table_key)
);

create index if not exists restaurant_tables_camera_idx
  on public.restaurant_tables(camera_id,active,sort_order);
create index if not exists restaurant_tables_site_idx
  on public.restaurant_tables(site_id,active,sort_order);

create table if not exists public.restaurant_visual_observations (
  event_id bigint primary key references public.snapshot_visual_reviews(event_id) on delete cascade,
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  site_id uuid not null references public.sites(id) on delete cascade,
  camera_id uuid null references public.cameras(id) on delete set null,
  captured_at timestamptz not null,
  camera_role text not null,
  visible_customers integer null check (visible_customers is null or visible_customers>=0),
  staff_count integer null check (staff_count is null or staff_count>=0),
  occupied_tables integer null check (occupied_tables is null or occupied_tables>=0),
  served_tables integer null check (served_tables is null or served_tables>=0),
  kitchen_load numeric(5,4) null check (kitchen_load is null or (kitchen_load>=0 and kitchen_load<=1)),
  handoff_load numeric(5,4) null check (handoff_load is null or (handoff_load>=0 and handoff_load<=1)),
  counter_active boolean null,
  confidence numeric(5,4) null check (confidence is null or (confidence>=0 and confidence<=1)),
  activity jsonb not null default '{}'::jsonb check (jsonb_typeof(activity)='object'),
  schema_version text not null default 'restaurant-vision-v1',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists restaurant_visual_observations_site_time_idx
  on public.restaurant_visual_observations(site_id,captured_at desc);
create index if not exists restaurant_visual_observations_camera_time_idx
  on public.restaurant_visual_observations(camera_id,captured_at desc);

create table if not exists public.restaurant_table_observations (
  event_id bigint not null references public.snapshot_visual_reviews(event_id) on delete cascade,
  table_id uuid not null references public.restaurant_tables(id) on delete cascade,
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  site_id uuid not null references public.sites(id) on delete cascade,
  camera_id uuid null references public.cameras(id) on delete set null,
  captured_at timestamptz not null,
  occupied boolean null,
  customer_count integer null check (customer_count is null or customer_count>=0),
  food_present boolean null,
  drinks_present boolean null,
  staff_present boolean null,
  clearing_state boolean null,
  combined_group text null,
  visibility_quality numeric(5,4) null check (visibility_quality is null or (visibility_quality>=0 and visibility_quality<=1)),
  confidence numeric(5,4) null check (confidence is null or (confidence>=0 and confidence<=1)),
  created_at timestamptz not null default now(),
  primary key(event_id,table_id)
);

create index if not exists restaurant_table_observations_table_time_idx
  on public.restaurant_table_observations(table_id,captured_at desc);
create index if not exists restaurant_table_observations_site_time_idx
  on public.restaurant_table_observations(site_id,captured_at desc);

alter table public.restaurant_camera_profiles enable row level security;
alter table public.restaurant_tables enable row level security;
alter table public.restaurant_visual_observations enable row level security;
alter table public.restaurant_table_observations enable row level security;

revoke all on public.restaurant_camera_profiles from public,anon,authenticated;
revoke all on public.restaurant_tables from public,anon,authenticated;
revoke all on public.restaurant_visual_observations from public,anon,authenticated;
revoke all on public.restaurant_table_observations from public,anon,authenticated;
grant all on public.restaurant_camera_profiles to service_role;
grant all on public.restaurant_tables to service_role;
grant all on public.restaurant_visual_observations to service_role;
grant all on public.restaurant_table_observations to service_role;

create index if not exists restaurant_tables_tenant_site_idx
  on public.restaurant_tables(tenant_id,site_id);
create index if not exists restaurant_visual_observations_tenant_site_time_idx
  on public.restaurant_visual_observations(tenant_id,site_id,captured_at desc);
create index if not exists restaurant_table_observations_tenant_site_time_idx
  on public.restaurant_table_observations(tenant_id,site_id,captured_at desc);
create index if not exists restaurant_table_observations_camera_time_idx
  on public.restaurant_table_observations(camera_id,captured_at desc);

create policy restaurant_camera_profiles_no_direct on public.restaurant_camera_profiles
  for all to authenticated using (false) with check (false);
create policy restaurant_tables_no_direct on public.restaurant_tables
  for all to authenticated using (false) with check (false);
create policy restaurant_visual_observations_no_direct on public.restaurant_visual_observations
  for all to authenticated using (false) with check (false);
create policy restaurant_table_observations_no_direct on public.restaurant_table_observations
  for all to authenticated using (false) with check (false);

create or replace function public.wl_analytics_valid_site_type(p text)
returns boolean
language sql
immutable
set search_path=public,pg_temp
as $$
  select p in (
    'retail','warehouse_logistics','manufacturing','office_commercial',
    'school_campus','parking_yard','residential_community','restaurant','custom'
  )
$$;

create or replace function public.wl_restaurant_site_config(p_site_id uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path=public
as $$
declare
  v_tenant uuid := public.wl_assert_my_site(p_site_id);
  v_site public.sites;
  v_ctx public.site_business_context;
begin
  select * into v_site from public.sites where id=p_site_id and tenant_id=v_tenant;
  if v_site.id is null then
    raise exception 'not authorized for this site' using errcode='42501';
  end if;
  select * into v_ctx from public.site_business_context
   where site_id=p_site_id and tenant_id=v_tenant;
  if coalesce(v_ctx.site_type,v_site.site_type,'other')<>'restaurant' then
    return jsonb_build_object('enabled',false,'site_type',coalesce(v_ctx.site_type,v_site.site_type,'other'));
  end if;

  return jsonb_build_object(
    'enabled',true,
    'site_id',p_site_id,
    'timezone',v_site.timezone,
    'open_time',v_ctx.open_time,
    'close_time',v_ctx.close_time,
    'overnight',coalesce(v_ctx.overnight,false),
    'cameras',coalesce((
      select jsonb_agg(jsonb_build_object(
        'camera_id',p.camera_id,
        'camera',coalesce(c.name,'Camera '||coalesce(c.physical_channel,c.channel,'?')),
        'channel',coalesce(c.physical_channel,c.channel),
        'role',p.analytics_role,
        'sampling_mode',p.sampling_mode,
        'interval_seconds',p.interval_seconds,
        'enabled',p.enabled,
        'config',p.config
      ) order by coalesce(c.physical_channel,c.channel))
      from public.restaurant_camera_profiles p
      join public.cameras c on c.id=p.camera_id
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
end $$;

revoke all on function public.wl_restaurant_site_config(uuid) from public;
grant execute on function public.wl_restaurant_site_config(uuid) to authenticated;

create or replace function public.wl_restaurant_day(p_site_id uuid,p_date date default null)
returns jsonb
language plpgsql
stable
security definer
set search_path=public
as $$
declare
  v_tenant uuid := public.wl_assert_my_site(p_site_id);
  v_site public.sites;
  v_ctx public.site_business_context;
  v_date date;
  v_local_now timestamp;
  v_start timestamptz;
  v_end timestamptz;
  v_hourly jsonb;
  v_tables jsonb;
  v_sessions jsonb;
  v_observations integer;
  v_table_observations integer;
begin
  select * into v_site from public.sites where id=p_site_id and tenant_id=v_tenant;
  if v_site.id is null then raise exception 'not authorized for this site' using errcode='42501'; end if;
  select * into v_ctx from public.site_business_context
   where site_id=p_site_id and tenant_id=v_tenant;
  if coalesce(v_ctx.site_type,v_site.site_type,'other')<>'restaurant' then
    return jsonb_build_object('enabled',false,'site_type',coalesce(v_ctx.site_type,v_site.site_type,'other'));
  end if;

  v_local_now := now() at time zone v_site.timezone;
  v_date := p_date;
  if v_date is null then
    v_date := v_local_now::date;
    if coalesce(v_ctx.overnight,false)
       and v_ctx.close_time is not null and v_ctx.open_time is not null
       and v_ctx.close_time<=v_ctx.open_time
       and v_local_now::time<v_ctx.close_time then
      v_date := v_date-1;
    end if;
  end if;

  v_start := (v_date+coalesce(v_ctx.open_time,'00:00'::time)) at time zone v_site.timezone;
  if coalesce(v_ctx.overnight,false)
     and v_ctx.close_time is not null and v_ctx.open_time is not null
     and v_ctx.close_time<=v_ctx.open_time then
    v_end := ((v_date+1)+v_ctx.close_time) at time zone v_site.timezone;
  else
    v_end := (v_date+coalesce(v_ctx.close_time,'23:59:59'::time)) at time zone v_site.timezone;
    if v_end<=v_start then v_end:=v_start+interval '1 day'; end if;
  end if;

  select count(*) into v_observations
    from public.restaurant_visual_observations o
   where o.site_id=p_site_id and o.tenant_id=v_tenant
     and o.captured_at>=v_start and o.captured_at<v_end;
  select count(*) into v_table_observations
    from public.restaurant_table_observations o
   where o.site_id=p_site_id and o.tenant_id=v_tenant
     and o.captured_at>=v_start and o.captured_at<v_end;

  with hours as (
    select generate_series(v_start,v_end-interval '1 hour',interval '1 hour') hour_start
  ), agg as (
    select h.hour_start,
           count(o.event_id) samples,
           round(avg(o.visible_customers)::numeric,1) avg_visible_customers,
           max(o.visible_customers) peak_visible_customers,
           round(avg(o.occupied_tables)::numeric,1) avg_occupied_tables,
           max(o.occupied_tables) peak_occupied_tables,
           round(avg(o.kitchen_load)::numeric,3) avg_kitchen_load,
           round(avg(o.handoff_load)::numeric,3) avg_handoff_load
      from hours h
      left join public.restaurant_visual_observations o
        on o.site_id=p_site_id and o.tenant_id=v_tenant
       and o.captured_at>=h.hour_start and o.captured_at<h.hour_start+interval '1 hour'
     group by h.hour_start
  )
  select coalesce(jsonb_agg(jsonb_build_object(
      'hour_start',a.hour_start,'local_hour',to_char(a.hour_start at time zone v_site.timezone,'HH24:MI'),
      'samples',a.samples,'avg_visible_customers',a.avg_visible_customers,
      'peak_visible_customers',a.peak_visible_customers,
      'avg_occupied_tables',a.avg_occupied_tables,'peak_occupied_tables',a.peak_occupied_tables,
      'avg_kitchen_load',a.avg_kitchen_load,'avg_handoff_load',a.avg_handoff_load
    ) order by a.hour_start),'[]'::jsonb)
    into v_hourly from agg a;

  with per_table as (
    select t.id,t.table_key,t.label,t.camera_id,t.capacity,t.sort_order,
           count(o.event_id) samples,
           count(o.event_id) filter(where o.occupied is true) occupied_samples,
           count(o.event_id) filter(where o.food_present is true) food_samples,
           round(avg(o.customer_count) filter(where o.occupied is true)::numeric,1) avg_party_when_occupied,
           max(o.customer_count) peak_party,
           round(avg(o.confidence)::numeric,3) avg_confidence
      from public.restaurant_tables t
      left join public.restaurant_table_observations o
        on o.table_id=t.id and o.captured_at>=v_start and o.captured_at<v_end
     where t.site_id=p_site_id and t.tenant_id=v_tenant and t.active
     group by t.id,t.table_key,t.label,t.camera_id,t.capacity,t.sort_order
  )
  select coalesce(jsonb_agg(jsonb_build_object(
      'table_id',p.id,'table_key',p.table_key,'label',p.label,'camera_id',p.camera_id,
      'capacity',p.capacity,'samples',p.samples,'occupied_samples',p.occupied_samples,
      'occupancy_pct',case when p.samples=0 then null else round(100.0*p.occupied_samples/p.samples,1) end,
      'food_present_samples',p.food_samples,'avg_party_when_occupied',p.avg_party_when_occupied,
      'peak_party',p.peak_party,'avg_confidence',p.avg_confidence
    ) order by p.sort_order,p.table_key),'[]'::jsonb)
    into v_tables from per_table p;

  with ordered as (
    select o.*,
           lag(o.occupied) over(partition by o.table_id order by o.captured_at) prev_occupied,
           lag(o.captured_at) over(partition by o.table_id order by o.captured_at) prev_at
      from public.restaurant_table_observations o
     where o.site_id=p_site_id and o.tenant_id=v_tenant
       and o.captured_at>=v_start and o.captured_at<v_end
  ), flagged as (
    select o.*,
           case when o.occupied is true and (
             coalesce(o.prev_occupied,false)=false or o.prev_at is null
             or o.captured_at-o.prev_at>interval '20 minutes'
           ) then 1 else 0 end session_start
      from ordered o
  ), tagged as (
    select f.*,
           sum(f.session_start) over(partition by f.table_id order by f.captured_at rows unbounded preceding) session_no
      from flagged f
  ), session_rows as (
    select t.table_id,t.session_no,min(t.captured_at) started_at,max(t.captured_at) last_occupied_at,
           min(t.captured_at) filter(where t.food_present is true) first_food_at,
           max(t.customer_count) peak_party,bool_or(t.food_present is true) served,
           round(avg(t.confidence)::numeric,3) confidence
      from tagged t
     where t.occupied is true and t.session_no>0
     group by t.table_id,t.session_no
  ), named as (
    select s.*,rt.table_key,rt.label,
           extract(epoch from (s.first_food_at-s.started_at))/60.0 time_to_food_min,
           extract(epoch from (s.last_occupied_at-s.started_at))/60.0 observed_dwell_min
      from session_rows s join public.restaurant_tables rt on rt.id=s.table_id
  )
  select jsonb_build_object(
      'count',count(*),
      'estimated_covers',coalesce(sum(coalesce(n.peak_party,0)),0),
      'served_sessions',count(*) filter(where n.served),
      'avg_observed_time_to_food_minutes',
        round((avg(n.time_to_food_min) filter(where n.time_to_food_min is not null))::numeric,1),
      'median_observed_time_to_food_minutes',
        round((percentile_cont(0.5) within group(order by n.time_to_food_min)
          filter(where n.time_to_food_min is not null))::numeric,1),
      'median_minimum_observed_dwell_minutes',
        round((percentile_cont(0.5) within group(order by n.observed_dwell_min)
          filter(where n.observed_dwell_min is not null))::numeric,1),
      'items',coalesce(jsonb_agg(jsonb_build_object(
        'table_key',n.table_key,'label',n.label,'session_no',n.session_no,
        'started_at',n.started_at,'last_occupied_at',n.last_occupied_at,
        'first_food_at',n.first_food_at,'served',n.served,'peak_party',n.peak_party,
        'observed_time_to_food_minutes',case when n.time_to_food_min is null then null else round(n.time_to_food_min::numeric,1) end,
        'minimum_observed_dwell_minutes',round(n.observed_dwell_min::numeric,1),
        'confidence',n.confidence
      ) order by n.started_at) filter(where n.table_id is not null),'[]'::jsonb)
    ) into v_sessions from named n;

  return jsonb_build_object(
    'enabled',true,'schema','restaurant-day-v1','service_date',v_date,'timezone',v_site.timezone,
    'window',jsonb_build_object('start',v_start,'end',v_end),
    'hourly',coalesce(v_hourly,'[]'::jsonb),
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
end $$;

revoke all on function public.wl_restaurant_day(uuid,date) from public;
grant execute on function public.wl_restaurant_day(uuid,date) to authenticated;

create or replace function public.wl_extract_restaurant_visual_observation()
returns trigger
language plpgsql
set search_path=public
as $$
declare
  v_profile public.restaurant_camera_profiles;
  v_rest jsonb;
  v_row jsonb;
  v_table public.restaurant_tables;
begin
  if new.status<>'done' or new.analysis is null or jsonb_typeof(new.analysis)<>'object' then return new; end if;
  select * into v_profile from public.restaurant_camera_profiles
   where camera_id=new.camera_id and site_id=new.site_id and tenant_id=new.tenant_id and enabled;
  if v_profile.camera_id is null then return new; end if;

  v_rest:=new.analysis->'restaurant';
  if (v_rest is null or jsonb_typeof(v_rest)<>'object') and jsonb_typeof(new.analysis->'business')='object' then
    v_rest:=new.analysis->'business'->'restaurant';
  end if;
  if v_rest is null or jsonb_typeof(v_rest)<>'object' then return new; end if;

  insert into public.restaurant_visual_observations(
    event_id,tenant_id,site_id,camera_id,captured_at,camera_role,
    visible_customers,staff_count,occupied_tables,served_tables,kitchen_load,handoff_load,
    counter_active,confidence,activity,schema_version,updated_at
  ) values (
    new.event_id,new.tenant_id,new.site_id,new.camera_id,new.captured_at,v_profile.analytics_role,
    case when coalesce(v_rest->>'visible_customers','')~'^\d+$' then (v_rest->>'visible_customers')::int end,
    case when coalesce(v_rest->>'staff_count','')~'^\d+$' then (v_rest->>'staff_count')::int end,
    case when coalesce(v_rest->>'occupied_tables','')~'^\d+$' then (v_rest->>'occupied_tables')::int end,
    case when coalesce(v_rest->>'served_tables','')~'^\d+$' then (v_rest->>'served_tables')::int end,
    case when coalesce(v_rest->>'kitchen_load','')~'^(0(\.\d+)?|1(\.0+)?)$' then (v_rest->>'kitchen_load')::numeric end,
    case when coalesce(v_rest->>'handoff_load','')~'^(0(\.\d+)?|1(\.0+)?)$' then (v_rest->>'handoff_load')::numeric end,
    case when lower(coalesce(v_rest->>'counter_active','')) in ('true','1','yes') then true
         when lower(coalesce(v_rest->>'counter_active','')) in ('false','0','no') then false end,
    case when coalesce(v_rest->>'confidence','')~'^(0(\.\d+)?|1(\.0+)?)$' then (v_rest->>'confidence')::numeric end,
    v_rest,coalesce(nullif(v_rest->>'schema_version',''),'restaurant-vision-v1'),now()
  )
  on conflict(event_id) do update set
    camera_role=excluded.camera_role,visible_customers=excluded.visible_customers,
    staff_count=excluded.staff_count,occupied_tables=excluded.occupied_tables,
    served_tables=excluded.served_tables,kitchen_load=excluded.kitchen_load,
    handoff_load=excluded.handoff_load,counter_active=excluded.counter_active,
    confidence=excluded.confidence,activity=excluded.activity,
    schema_version=excluded.schema_version,updated_at=now();

  if jsonb_typeof(v_rest->'tables')='array' then
    for v_row in select value from jsonb_array_elements(v_rest->'tables')
    loop
      if jsonb_typeof(v_row)<>'object' or coalesce(v_row->>'table_key','')='' then continue; end if;
      select * into v_table from public.restaurant_tables
       where site_id=new.site_id and camera_id=new.camera_id and table_key=v_row->>'table_key' and active
       limit 1;
      if v_table.id is null then continue; end if;

      insert into public.restaurant_table_observations(
        event_id,table_id,tenant_id,site_id,camera_id,captured_at,occupied,customer_count,
        food_present,drinks_present,staff_present,clearing_state,combined_group,visibility_quality,confidence
      ) values (
        new.event_id,v_table.id,new.tenant_id,new.site_id,new.camera_id,new.captured_at,
        case when lower(coalesce(v_row->>'occupied','')) in ('true','1','yes') then true
             when lower(coalesce(v_row->>'occupied','')) in ('false','0','no') then false end,
        case when coalesce(v_row->>'customer_count','')~'^\d+$' then (v_row->>'customer_count')::int end,
        case when lower(coalesce(v_row->>'food_present','')) in ('true','1','yes') then true
             when lower(coalesce(v_row->>'food_present','')) in ('false','0','no') then false end,
        case when lower(coalesce(v_row->>'drinks_present','')) in ('true','1','yes') then true
             when lower(coalesce(v_row->>'drinks_present','')) in ('false','0','no') then false end,
        case when lower(coalesce(v_row->>'staff_present','')) in ('true','1','yes') then true
             when lower(coalesce(v_row->>'staff_present','')) in ('false','0','no') then false end,
        case when lower(coalesce(v_row->>'clearing_state','')) in ('true','1','yes') then true
             when lower(coalesce(v_row->>'clearing_state','')) in ('false','0','no') then false end,
        nullif(left(coalesce(v_row->>'combined_group',''),80),''),
        case when coalesce(v_row->>'visibility_quality','')~'^(0(\.\d+)?|1(\.0+)?)$' then (v_row->>'visibility_quality')::numeric end,
        case when coalesce(v_row->>'confidence','')~'^(0(\.\d+)?|1(\.0+)?)$' then (v_row->>'confidence')::numeric end
      )
      on conflict(event_id,table_id) do update set
        occupied=excluded.occupied,customer_count=excluded.customer_count,food_present=excluded.food_present,
        drinks_present=excluded.drinks_present,staff_present=excluded.staff_present,
        clearing_state=excluded.clearing_state,combined_group=excluded.combined_group,
        visibility_quality=excluded.visibility_quality,confidence=excluded.confidence;
    end loop;
  end if;
  return new;
exception when others then
  return new;
end $$;

drop trigger if exists trg_extract_restaurant_visual_observation on public.snapshot_visual_reviews;
create trigger trg_extract_restaurant_visual_observation
after insert or update of status,analysis on public.snapshot_visual_reviews
for each row execute function public.wl_extract_restaurant_visual_observation();

-- Extend the existing private vision claim contract with restaurant-specific
-- context while preserving the queue/lease semantics.
create or replace function public.wl_vision_claim_snapshots(p_limit integer default 4,p_worker_id text default null)
returns jsonb
language plpgsql
security definer
set search_path=public
as $$
declare v_out jsonb;
begin
  if auth.role()<>'service_role' then
    raise exception 'service role required' using errcode='42501';
  end if;
  with picked as (
    select r.event_id
      from public.snapshot_visual_reviews r
      join public.snapshots s on s.event_id=r.event_id
      join public.cameras c on c.id=s.camera_id
     where coalesce(c.is_canonical,true)
       and ((r.status='pending' and r.next_attempt_at<=now())
        or (r.status='failed' and r.attempts<5 and r.next_attempt_at<=now())
        or (r.status='processing' and r.lease_until<now() and r.attempts<5))
     order by r.next_attempt_at,r.captured_at
     for update of r skip locked
     limit least(greatest(coalesce(p_limit,4),1),16)
  ), claimed as (
    update public.snapshot_visual_reviews r
       set status='processing',attempts=r.attempts+1,
           lease_until=now()+interval '12 minutes',
           worker_id=left(coalesce(p_worker_id,'vision-worker'),120),
           last_error=null,updated_at=now()
      from picked p where r.event_id=p.event_id
     returning r.event_id
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'event_id',s.event_id,'tenant_id',s.tenant_id,'site_id',s.site_id,'camera_id',s.camera_id,
    'camera',coalesce(c.name,'Camera '||coalesce(c.physical_channel,c.channel,'?')),
    'channel',coalesce(c.physical_channel,c.channel),'camera_purpose',coalesce(c.purpose,'general'),
    'captured_at',s.captured_at,'timezone',coalesce(si.timezone,'Asia/Karachi'),
    'site_type',coalesce(b.site_type,si.site_type,'other'),
    'business_context',jsonb_build_object(
      'site_type',coalesce(b.site_type,si.site_type,'other'),
      'open_time',b.open_time,'close_time',b.close_time,'overnight',coalesce(b.overnight,false),
      'working_days',coalesce(to_jsonb(b.working_days),'[]'::jsonb),
      'camera_context',coalesce(b.reporting_prefs->'camera_context','{}'::jsonb),
      'owner_insight_priorities',coalesce(b.reporting_prefs->'owner_insight_priorities','[]'::jsonb),
      'ai_context_note',coalesce(b.reporting_prefs->>'ai_context_note',''),
      'restaurant_analytics',case
        when coalesce(b.site_type,si.site_type,'other')='restaurant' and rp.enabled then
          jsonb_build_object(
            'enabled',true,'camera_role',rp.analytics_role,'sampling_mode',rp.sampling_mode,
            'interval_seconds',rp.interval_seconds,'config',rp.config,
            'tables',coalesce((
              select jsonb_agg(jsonb_build_object(
                'table_key',rt.table_key,'label',rt.label,'capacity',rt.capacity,
                'tracking_mode',rt.tracking_mode,'anchor',rt.anchor,'roi',rt.roi,'can_combine',rt.can_combine
              ) order by rt.sort_order,rt.table_key)
              from public.restaurant_tables rt
              where rt.camera_id=s.camera_id and rt.site_id=s.site_id and rt.active
            ),'[]'::jsonb),
            'output_contract',jsonb_build_object(
              'top_level_key','restaurant','schema_version','restaurant-vision-v1',
              'truth_rules',jsonb_build_array(
                'Count only visible people; do not infer unique identity.',
                'visible_customers means currently visible customers, not unique footfall.',
                'food_present means visible food on a table; do not infer order correctness or food quality.',
                'Do not infer sales, revenue, staff identity, health diagnosis, or customer demographics.',
                'Use null when a requested field is not visually defensible.'
              ),
              'fields',jsonb_build_array(
                'visible_customers','staff_count','occupied_tables','served_tables',
                'kitchen_load','handoff_load','counter_active','confidence','tables'
              ),
              'table_fields',jsonb_build_array(
                'table_key','occupied','customer_count','food_present','drinks_present',
                'staff_present','clearing_state','combined_group','visibility_quality','confidence'
              )
            )
          )
        else null end
    ),
    'content_type',s.content_type,'bytes',s.bytes,'image_b64',encode(s.image,'base64'),
    'media_bucket',r.media_bucket,'media_key',r.media_key,'media_sha256',r.media_sha256,'media_bytes',r.media_bytes
  ) order by s.captured_at),'[]'::jsonb)
  into v_out
  from claimed q
  join public.snapshot_visual_reviews r on r.event_id=q.event_id
  join public.snapshots s on s.event_id=q.event_id
  join public.sites si on si.id=s.site_id
  left join public.site_business_context b on b.site_id=s.site_id
  left join public.cameras c on c.id=s.camera_id
  left join public.restaurant_camera_profiles rp on rp.camera_id=s.camera_id
  where coalesce(c.is_canonical,true);

  return coalesce(v_out,'[]'::jsonb);
end $$;

revoke all on function public.wl_vision_claim_snapshots(integer,text) from public,anon,authenticated;
grant execute on function public.wl_vision_claim_snapshots(integer,text) to service_role;
