-- =====================================================================
-- 0069 — Monthly rollup: a calendar-month aggregate over the intelligence layers.
--
-- The daily dataset (0067) answers "what happened today". The monthly rollup answers
-- "what does this site look like over a month" — the view a client reviews in a monthly
-- meeting: incident totals by severity/type, the daily activity trend, the busiest day,
-- after-hours load, and the average monitoring coverage.
--
-- READ-ONLY: it aggregates what the daily scheduler already derived (events, episodes,
-- promoted incidents). It does not re-derive a whole month on demand. Exposure mirrors the
-- daily dataset: service_role builder + tenant-guarded portal wrapper.
-- =====================================================================

create or replace function public.wl_monthly_rollup(
  p_site_id uuid,
  p_month   date default null
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_site   sites;
  v_month  date;
  v_start  timestamptz;
  v_end    timestamptz;
  v_to     timestamptz;
  v_tz     text;
  v_inc    jsonb;
  v_trend  jsonb;
  v_busy   jsonb;
  v_cov    jsonb;
  v_total  int;
  v_after  int;
  v_restr  int;
begin
  select * into v_site from sites where id = p_site_id;
  if v_site.id is null then
    raise exception 'no such site' using errcode = '22023';
  end if;
  v_tz    := v_site.timezone;
  v_month := date_trunc('month', coalesce(p_month, (now() at time zone v_tz)::date))::date;
  v_start := (v_month::text || ' 00:00:00')::timestamp at time zone v_tz;
  v_end   := ((v_month + interval '1 month')::date::text || ' 00:00:00')::timestamp at time zone v_tz;
  v_to    := least(v_end, greatest(v_start, now()));

  select jsonb_build_object(
           'total',    count(*),
           'critical', count(*) filter (where severity = 'critical'),
           'warning',  count(*) filter (where severity = 'warning'),
           'info',     count(*) filter (where severity = 'info'),
           'by_type', (select coalesce(jsonb_object_agg(t, n), '{}'::jsonb)
                         from (select incident_type t, count(*) n
                                 from intel_incidents
                                where site_id = p_site_id and occurred_at >= v_start and occurred_at < v_end
                                group by 1) z))
    into v_inc
    from intel_incidents
   where site_id = p_site_id and occurred_at >= v_start and occurred_at < v_end;

  select coalesce(jsonb_agg(jsonb_build_object('date', d, 'detections', n, 'after_hours', ah)
                            order by d), '[]'::jsonb)
    into v_trend
    from (select (device_ts at time zone v_tz)::date d, count(*) n,
                 count(*) filter (where (device_ts at time zone v_tz)::time < time '08:00'
                                     or (device_ts at time zone v_tz)::time > time '19:00') ah
            from events
           where site_id = p_site_id and event_type = 'person'
             and device_ts >= v_start and device_ts < v_end
           group by 1) z;

  select jsonb_build_object('date', d, 'detections', n)
    into v_busy
    from (select (device_ts at time zone v_tz)::date d, count(*) n
            from events
           where site_id = p_site_id and event_type = 'person'
             and device_ts >= v_start and device_ts < v_end
           group by 1 order by n desc, d limit 1) z;

  select count(*),
         count(*) filter (where (device_ts at time zone v_tz)::time < time '08:00'
                             or (device_ts at time zone v_tz)::time > time '19:00')
    into v_total, v_after
    from events
   where site_id = p_site_id and event_type = 'person'
     and device_ts >= v_start and device_ts < v_end;

  select count(*)
    into v_restr
    from episodes ep left join cameras c on c.id = ep.camera_id
   where ep.site_id = p_site_id and ep.started_at >= v_start and ep.started_at < v_end
     and (coalesce(c.purpose,'') ilike '%armory%' or coalesce(c.purpose,'') ilike '%restrict%'
          or coalesce(c.name,'') ilike '%armory%');

  v_cov := wl_site_coverage_report(p_site_id, v_start, v_to);

  return jsonb_build_object(
    'schema', 'monthly_rollup.v1',
    'meta', jsonb_build_object(
       'site', v_site.name, 'site_id', v_site.id, 'month', to_char(v_month, 'YYYY-MM'),
       'timezone', v_tz, 'generated_at', now(),
       'days_with_activity', (select jsonb_array_length(v_trend))),
    'incidents', v_inc,
    'activity', jsonb_build_object(
       'total_detections', coalesce(v_total, 0),
       'after_hours_detections', coalesce(v_after, 0),
       'restricted_access_windows', coalesce(v_restr, 0),
       'busiest_day', v_busy,
       'daily', v_trend),
    'coverage', v_cov,
    'honesty', jsonb_build_array(
       'Counts are camera detections aggregated over the month, not distinct people.',
       'Incident totals reflect days the daily report was generated.')
  );
end $$;
revoke all on function public.wl_monthly_rollup(uuid,date) from public, anon, authenticated;
grant execute on function public.wl_monthly_rollup(uuid,date) to service_role;

-- Portal entry point: tenant-guarded.
create or replace function public.wl_my_monthly_rollup(
  p_site_id uuid,
  p_month   date default null
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_my_tenant();
  v_owner  uuid;
begin
  if v_tenant is null then
    raise exception 'not authenticated' using errcode = '42501';
  end if;
  select tenant_id into v_owner from sites where id = p_site_id;
  if v_owner is null or v_owner <> v_tenant then
    raise exception 'not authorized for this site' using errcode = '42501';
  end if;
  return wl_monthly_rollup(p_site_id, p_month);
end $$;
revoke all on function public.wl_my_monthly_rollup(uuid,date) from public, anon;
grant execute on function public.wl_my_monthly_rollup(uuid,date) to authenticated, service_role;
