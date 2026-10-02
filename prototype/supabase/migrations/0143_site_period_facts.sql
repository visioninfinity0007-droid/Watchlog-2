-- 0143 - Site-neutral governed period facts (Phase 28: Site-Type Intelligence Profiles).
--
-- NOT APPLIED. Requires owner approval before any production apply.
--
-- Why a new function instead of widening wl_office_period (0129):
--   wl_office_period is office-specific in meaning, not just in name. It reads the office brief
--   (v_d->'office'), returns schema office-period-v1 and emits office measurement notes. Routing
--   warehouse, factory or retail sites through it would give them office semantics with new labels.
--
-- This migration adds:
--   wl_site_day_facts(jsonb)  pure helper: one governed business-day dataset -> site-neutral facts.
--                             Reads ONLY site-neutral keys of wl_my_daily_intelligence:
--                             coverage, attention, after_hours, day_boundaries, access_windows, meta.
--                             Never reads the 'office' brief.
--   wl_site_period(...)       tenant-scoped period facts for office, warehouse, factory and retail
--                             (schema site-period-v1). Interpretation per site type happens in the
--                             site-type intelligence profile (portal) and harness (Ask WatchLog).
--
-- Backwards compatibility:
--   wl_office_period is NOT redefined, so office clients (portal, watchlog-ai) are unchanged.
--   Restaurant keeps its own governed period (wl_restaurant_period); wl_site_period returns
--   enabled:false for restaurant and for unprofiled site types. Nothing is inserted, updated or deleted.
--
-- Authorization: SECURITY DEFINER with search_path=public; wl_assert_my_site() raises unless the site
-- belongs to the caller's tenant; the site row is re-read with tenant_id; every day is read through
-- wl_my_daily_intelligence, which re-checks the tenant.
--
-- Grants (narrowest that the real callers need):
--   wl_site_period         -> authenticated ONLY. Its callers are the portal (browser, the customer's
--                             session) and watchlog-ai gatherTools(sb) with the customer's JWT. No server
--                             runtime calls it as service_role. It is deliberately NOT granted to
--                             service_role: wl_assert_my_site (0122) intentionally lets service_role read
--                             any tenant's site, and this customer RPC needs no such privileged path.
--   wl_site_day_facts,
--   wl_site_period_summary -> owner only. Pure internal helpers, called only inside wl_site_period
--                             (SECURITY DEFINER, runs as the owner); no role needs to call them directly.

create or replace function public.wl_site_day_facts(p_day jsonb)
returns jsonb
language sql
immutable
set search_path = public
as $$
  with w as (
    select
      coalesce(nullif(trim(x->>'purpose'),''),'unassigned') as purpose,
      coalesce(x->>'object_class','person') as object_class,
      coalesce((x->>'detections')::integer,0) as detections,
      nullif(x->>'start','') as s,
      nullif(x->>'end','') as e
    from jsonb_array_elements(coalesce(p_day->'access_windows','[]'::jsonb)) x
  ),
  cls as (
    select coalesce(p_day->'coverage'->'classes', p_day->'coverage') c
  )
  select jsonb_build_object(
    'coverage_ratio', coalesce((p_day->'coverage'->>'coverage_ratio')::numeric,0),
    'coverage_classes', (select jsonb_build_object(
        'live_seconds', (c->>'live_seconds')::numeric,
        'recovered_seconds', (c->>'recovered_seconds')::numeric,
        'unverified_seconds', (c->>'unverified_seconds')::numeric) from cls),
    'incidents_total', coalesce((p_day->'attention'->>'incidents_total')::integer,0),
    'critical', coalesce((p_day->'attention'->>'critical')::integer,0),
    'warning', coalesce((p_day->'attention'->>'warning')::integer,0),
    'after_hours_count', coalesce((p_day->'after_hours'->>'count')::integer,0),
    'after_hours_verified', coalesce((p_day->'after_hours'->>'verified')::boolean,false),
    'activity_episodes', (select count(*) from w where object_class<>'vehicle'),
    'vehicle_episodes', (select count(*) from w where object_class='vehicle'),
    'activity_detections', (select coalesce(sum(detections),0) from w where object_class<>'vehicle'),
    'first_observed', (select min(s) from w),
    'last_observed', (select max(coalesce(e,s)) from w),
    'opening_at', p_day->'day_boundaries'->>'opening_at',
    'closing_at', p_day->'day_boundaries'->>'closing_at',
    'boundary_confidence', p_day->'day_boundaries'->>'confidence',
    'partial_day', coalesce((p_day->'meta'->>'partial_day')::boolean,false),
    'by_purpose', (select coalesce(jsonb_agg(jsonb_build_object(
        'purpose', purpose, 'episodes', ep, 'vehicle_episodes', vep, 'detections', det) order by purpose),'[]'::jsonb)
      from (select purpose,
                   count(*) filter (where object_class<>'vehicle') ep,
                   count(*) filter (where object_class='vehicle') vep,
                   coalesce(sum(detections) filter (where object_class<>'vehicle'),0) det
            from w group by purpose) z),
    'hourly', (select coalesce(jsonb_agg(jsonb_build_object('hour', h, 'episodes', n) order by h),'[]'::jsonb)
      from (select substr(s,1,2)::integer h, count(*) n from w where s ~ '^\d{2}:\d{2}' group by 1) z)
  );
$$;

revoke all on function public.wl_site_day_facts(jsonb) from public, anon, authenticated, service_role;

-- Deterministic period roll-up of wl_site_day_facts rows (pure; no data access).
create or replace function public.wl_site_period_summary(p_days jsonb)
returns jsonb
language sql
immutable
set search_path = public
as $$
  with d as (select x from jsonb_array_elements(coalesce(p_days,'[]'::jsonb)) x),
  p as (
    select coalesce(b->>'purpose','unassigned') purpose,
           sum((b->>'episodes')::integer) ep, sum((b->>'vehicle_episodes')::integer) vep, sum((b->>'detections')::integer) det
    from d, jsonb_array_elements(coalesce(x->'by_purpose','[]'::jsonb)) b group by 1
  ),
  h as (
    select (b->>'hour')::integer as hour_bucket, sum((b->>'episodes')::integer) as n
    from d, jsonb_array_elements(coalesce(x->'hourly','[]'::jsonb)) b group by 1
  )
  select jsonb_build_object(
    'days', (select count(*) from d),
    'observed_days', (select count(*) from d where (x->>'coverage_ratio')::numeric > 0),
    'avg_coverage_ratio', (select case when count(*) = 0 then null else round(avg((x->>'coverage_ratio')::numeric),3) end from d),
    -- LIVE / RECOVERED / UNVERIFIED kept as separate sums; null when no day reported that class.
    'coverage_classes', (select jsonb_build_object(
        'live_seconds', sum((x->'coverage_classes'->>'live_seconds')::numeric),
        'recovered_seconds', sum((x->'coverage_classes'->>'recovered_seconds')::numeric),
        'unverified_seconds', sum((x->'coverage_classes'->>'unverified_seconds')::numeric)) from d),
    'incidents_total', (select coalesce(sum((x->>'incidents_total')::integer),0) from d),
    'critical_total', (select coalesce(sum((x->>'critical')::integer),0) from d),
    'after_hours_total', (select coalesce(sum((x->>'after_hours_count')::integer),0) from d),
    'activity_episodes', (select coalesce(sum((x->>'activity_episodes')::integer),0) from d),
    'activity_detections', (select coalesce(sum((x->>'activity_detections')::integer),0) from d),
    'vehicle_episodes', (select coalesce(sum((x->>'vehicle_episodes')::integer),0) from d),
    'by_purpose', (select coalesce(jsonb_agg(jsonb_build_object('purpose',purpose,'episodes',ep,'vehicle_episodes',vep,'detections',det) order by purpose),'[]'::jsonb) from p),
    'hourly', (select coalesce(jsonb_agg(jsonb_build_object('hour',hour_bucket,'episodes',n) order by hour_bucket),'[]'::jsonb) from h)
  );
$$;

revoke all on function public.wl_site_period_summary(jsonb) from public, anon, authenticated, service_role;

create or replace function public.wl_site_period(p_site_id uuid, p_days integer default 7, p_working_only boolean default true)
returns jsonb
language plpgsql
volatile
security definer
set search_path = public
as $function$
declare
  v_tenant uuid := public.wl_assert_my_site(p_site_id);
  v_site public.sites;
  v_ctx public.site_business_context;
  v_type text;
  v_days integer[];
  v_cursor date;
  v_end_date date;
  v_start_date date;
  v_prev_start date;
  v_prev_end date;
  v_current jsonb := '[]'::jsonb;
  v_previous jsonb := '[]'::jsonb;
  v_f jsonb;
  v_count integer := 0;
  v_prev_count integer := 0;
  v_working boolean;
  v_sum jsonb;
  v_prev_sum jsonb;
begin
  if p_days < 2 or p_days > 31 then
    raise exception 'p_days must be between 2 and 31' using errcode = '22023';
  end if;

  select * into v_site from public.sites where id = p_site_id and tenant_id = v_tenant;
  if v_site.id is null then raise exception 'not authorized for this site' using errcode = '42501'; end if;
  select * into v_ctx from public.site_business_context where site_id = p_site_id and tenant_id = v_tenant;
  v_type := coalesce(v_ctx.site_type, v_site.site_type, 'other');
  if v_type not in ('office','warehouse','factory','retail') then
    return jsonb_build_object('enabled', false, 'site_type', v_type,
      'reason', case when v_type = 'restaurant' then 'restaurant_period' else 'site_type_not_profiled' end);
  end if;

  v_days := coalesce(v_ctx.working_days, array[1,2,3,4,5]);
  if p_working_only then
    v_cursor := public.wl_my_last_completed_business_date(p_site_id, now());
  else
    v_cursor := (now() at time zone coalesce(v_site.timezone,'UTC'))::date - 1;
  end if;
  v_end_date := v_cursor;

  while v_count < p_days and v_cursor > v_end_date - 90 loop
    v_working := extract(isodow from v_cursor)::integer = any(v_days);
    if (not p_working_only) or v_working then
      v_f := public.wl_site_day_facts(public.wl_my_daily_intelligence(p_site_id, v_cursor))
             || jsonb_build_object('date', v_cursor, 'working_day', v_working);
      v_current := jsonb_build_array(v_f) || v_current;
      v_count := v_count + 1;
      v_start_date := v_cursor;
    end if;
    v_cursor := v_cursor - 1;
  end loop;

  v_prev_end := v_cursor;
  while v_prev_count < p_days and v_cursor > v_prev_end - 90 loop
    v_working := extract(isodow from v_cursor)::integer = any(v_days);
    if (not p_working_only) or v_working then
      v_f := public.wl_site_day_facts(public.wl_my_daily_intelligence(p_site_id, v_cursor))
             || jsonb_build_object('date', v_cursor, 'working_day', v_working);
      v_previous := jsonb_build_array(v_f) || v_previous;
      v_prev_count := v_prev_count + 1;
      v_prev_start := v_cursor;
    end if;
    v_cursor := v_cursor - 1;
  end loop;

  v_sum := public.wl_site_period_summary(v_current);
  v_prev_sum := public.wl_site_period_summary(v_previous);

  return jsonb_build_object(
    'enabled', true,
    'schema', 'site-period-v1',
    'site_type', v_type,
    'window_type', case when p_working_only then 'completed_working_days' else 'completed_calendar_days' end,
    'period', jsonb_build_object('start_date', v_start_date, 'end_date', v_end_date,
                                 'previous_start_date', v_prev_start, 'previous_end_date', v_prev_end),
    'summary', v_sum,
    'previous_period', v_prev_sum,
    -- Deterministic differences only; whether a comparison is reliable (enough observed days on both
    -- sides) is decided by the reader from observed_days, never implied here.
    'comparison', jsonb_build_object(
      'coverage_delta_points', case when (v_sum->>'avg_coverage_ratio') is null or (v_prev_sum->>'avg_coverage_ratio') is null then null
        else round(100*((v_sum->>'avg_coverage_ratio')::numeric-(v_prev_sum->>'avg_coverage_ratio')::numeric),1) end,
      'incidents_delta', (v_sum->>'incidents_total')::integer-(v_prev_sum->>'incidents_total')::integer,
      'critical_delta', (v_sum->>'critical_total')::integer-(v_prev_sum->>'critical_total')::integer,
      'after_hours_delta', (v_sum->>'after_hours_total')::integer-(v_prev_sum->>'after_hours_total')::integer,
      'activity_episodes_delta', (v_sum->>'activity_episodes')::integer-(v_prev_sum->>'activity_episodes')::integer,
      'activity_detections_delta', (v_sum->>'activity_detections')::integer-(v_prev_sum->>'activity_detections')::integer,
      'vehicle_episodes_delta', (v_sum->>'vehicle_episodes')::integer-(v_prev_sum->>'vehicle_episodes')::integer
    ),
    'daily', v_current,
    'measurement_notes', jsonb_build_array(
      'Activity episodes and detections are camera observations, not unique people or vehicles.',
      'Days without monitoring coverage are missing evidence, not zero activity.',
      'Area facts are grouped by each camera''s configured purpose; unassigned cameras are reported as unassigned.',
      'Facts are observations only; they carry no business cause, production, throughput, sales or attendance meaning.'
    )
  );
end $function$;


revoke all on function public.wl_site_period(uuid,integer,boolean) from public, anon, service_role;
grant execute on function public.wl_site_period(uuid,integer,boolean) to authenticated;
