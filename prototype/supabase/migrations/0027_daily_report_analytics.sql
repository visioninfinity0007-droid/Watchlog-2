-- =====================================================================
-- 0027 — Include Analytics Studio measurements in the existing daily
-- report payload. Existing security-event fields remain unchanged.
-- =====================================================================

create or replace function public.wl_daily_report(
  p_site_id uuid default null,
  p_date    date default null
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_site   sites;
  v_date   date;
  v_start  timestamptz;
  v_end    timestamptz;
begin
  if p_site_id is null then
    select * into v_site from sites order by created_at limit 1;
  else
    select * into v_site from sites where id = p_site_id;
  end if;
  if v_site.id is null then
    raise exception 'no such site' using errcode = '22023';
  end if;

  v_date  := coalesce(p_date, (now() at time zone v_site.timezone)::date);
  v_start := (v_date::text || ' 00:00:00')::timestamp at time zone v_site.timezone;
  v_end   := v_start + interval '1 day';

  return jsonb_build_object(
    'site', v_site.name,
    'site_type', v_site.site_type,
    'timezone', v_site.timezone,
    'date', v_date,
    'total_events', (select count(*) from events
                      where site_id = v_site.id
                        and device_ts >= v_start and device_ts < v_end),
    'first_event_at', (select min(device_ts) from events
                        where site_id = v_site.id
                          and device_ts >= v_start and device_ts < v_end),
    'last_event_at',  (select max(device_ts) from events
                        where site_id = v_site.id
                          and device_ts >= v_start and device_ts < v_end),
    'by_type', (
      select coalesce(jsonb_object_agg(event_type, n), '{}'::jsonb)
        from (select event_type, count(*) n from events
               where site_id = v_site.id
                 and device_ts >= v_start and device_ts < v_end
               group by event_type) x),
    'by_camera', (
      select coalesce(jsonb_agg(t order by (t->>'count')::int desc), '[]'::jsonb)
        from (select jsonb_build_object('camera', coalesce(c.name, 'unassigned'),
                                        'count', count(*)) as t
                from events e left join cameras c on c.id = e.camera_id
               where e.site_id = v_site.id
                 and e.device_ts >= v_start and e.device_ts < v_end
               group by c.name) x),
    'after_hours_events', (
      select count(*) from events e
       where e.site_id = v_site.id
         and e.device_ts >= v_start and e.device_ts < v_end
         and extract(hour from e.device_ts at time zone v_site.timezone)
             not between 8 and 19),
    'faults', (
      select coalesce(jsonb_object_agg(event_type, n), '{}'::jsonb)
        from (select event_type, count(*) n from events
               where site_id = v_site.id
                 and device_ts >= v_start and device_ts < v_end
                 and event_type in ('video_loss','tamper','disk_error','disk_full')
               group by event_type) x),
    'analytics', public.wl_daily_analytics(v_site.id, v_date)
  );
end
$$;

-- Preserve the current privilege posture. 0010 removed browser/anon access;
-- the report-runner uses its trusted direct database connection.
revoke all on function public.wl_daily_report(uuid,date) from public,anon,authenticated;
