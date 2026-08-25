-- =====================================================================
-- WatchLog — analytics
-- =====================================================================
-- Turns the raw event stream into the thing the client actually buys:
-- "what happened at my sites, and is anything wrong right now".
--
-- Three functions:
--   wl_analytics(days)      dashboard aggregates
--   wl_site_health()        what needs attention NOW
--   wl_daily_report(date)   the daily security report payload
--
-- All timestamps are bucketed in the SITE's own timezone, not UTC. A
-- report for a Karachi site that puts a 2am event on the wrong day
-- because the server thinks in UTC is worse than no report.
--
-- These are read-only and SECURITY DEFINER, same posture as the viewer
-- API in 0005: the tables stay sealed, the surface is these calls.
-- =====================================================================

-- Aggregation over a date range is the common access pattern now.
create index if not exists events_tenant_type_ts_idx
  on events(tenant_id, event_type, device_ts desc);

-- ---------------------------------------------------------------------
-- wl_analytics — the dashboard numbers.
-- ---------------------------------------------------------------------
create or replace function public.wl_analytics(p_days int default 7)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_days  int := least(greatest(coalesce(p_days, 7), 1), 90);
  v_from  timestamptz := now() - make_interval(days => v_days);
  v_out   jsonb;
begin
  select jsonb_build_object(
    'window_days', v_days,
    'from', v_from,
    'generated_at', now(),

    'totals', (
      select jsonb_build_object(
        'events',  count(*),
        'cameras', count(distinct camera_id),
        'sites',   count(distinct site_id),
        'busiest_camera', (
          select coalesce(c.name, 'unknown')
            from events e2 left join cameras c on c.id = e2.camera_id
           where e2.device_ts >= v_from
           group by c.name order by count(*) desc limit 1)
      ) from events where device_ts >= v_from),

    'agents', (
      select jsonb_build_object(
        'total',   count(*),
        'online',  count(*) filter (where last_seen_at > now() - interval '3 minutes'),
        'offline', count(*) filter (where last_seen_at is null
                                       or last_seen_at <= now() - interval '30 minutes')
      ) from agents),

    -- What kind of events, most common first.
    'by_type', (
      select coalesce(jsonb_agg(t order by (t->>'count')::int desc), '[]'::jsonb)
        from (select jsonb_build_object('event_type', event_type,
                                        'count', count(*)) as t
                from events where device_ts >= v_from
               group by event_type) x),

    -- Which cameras are noisy. This is what drives "camera 3 needs its
    -- sensitivity turned down", the single most common real finding.
    'by_camera', (
      select coalesce(jsonb_agg(t order by (t->>'count')::int desc), '[]'::jsonb)
        from (select jsonb_build_object(
                       'camera', coalesce(c.name, 'unassigned'),
                       'site',   s.name,
                       'count',  count(*),
                       'last_event_at', max(e.device_ts)) as t
                from events e
                left join cameras c on c.id = e.camera_id
                left join sites   s on s.id = e.site_id
               where e.device_ts >= v_from
               group by c.name, s.name) x),

    -- Hour of day in the SITE's timezone. Reveals whether activity is
    -- clustered outside working hours, which is the whole point.
    -- Group by the hour value itself. `group by 1` here would group by
    -- the constructed json object, which contains the aggregate.
    'by_hour', (
      select coalesce(jsonb_agg(jsonb_build_object('hour', h, 'count', n)
                                order by h), '[]'::jsonb)
        from (select extract(hour from e.device_ts at time zone s.timezone)::int as h,
                     count(*) as n
                from events e join sites s on s.id = e.site_id
               where e.device_ts >= v_from
               group by 1) y),

    'by_day', (
      select coalesce(jsonb_agg(jsonb_build_object('day', d, 'count', n)
                                order by d), '[]'::jsonb)
        from (select (e.device_ts at time zone s.timezone)::date as d,
                     count(*) as n
                from events e join sites s on s.id = e.site_id
               where e.device_ts >= v_from
               group by 1) y)
  ) into v_out;

  return v_out;
end
$$;

-- ---------------------------------------------------------------------
-- wl_site_health — what needs attention now.
--
-- Deliberately conservative: it reports observable facts, not guesses.
-- "Camera has been silent 26 hours" is a fact. "Camera is broken" is
-- not, and a security company that gets told the wrong one twice stops
-- reading the reports.
-- ---------------------------------------------------------------------
create or replace function public.wl_site_health(p_silent_hours int default 24)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_hours int := least(greatest(coalesce(p_silent_hours, 24), 1), 720);
begin
  return jsonb_build_object(
    'generated_at', now(),
    'silent_hours_threshold', v_hours,

    -- A camera that has reported nothing for a long time is either a
    -- very quiet corner or a dead camera. Worth a human look either way.
    'silent_cameras', (
      select coalesce(jsonb_agg(t order by t->>'last_event_at'), '[]'::jsonb)
        from (select jsonb_build_object(
                       'camera', c.name, 'site', s.name,
                       'channel', c.channel,
                       'last_event_at', max(e.device_ts),
                       'hours_silent', round(extract(epoch from
                           now() - coalesce(max(e.device_ts), c.created_at))/3600.0, 1)
                     ) as t
                from cameras c
                join sites s on s.id = c.site_id
                left join events e on e.camera_id = c.id
               group by c.id, c.name, c.channel, s.name, c.created_at
              having coalesce(max(e.device_ts), c.created_at)
                     < now() - make_interval(hours => v_hours)) x),

    -- Agents that stopped checking in. The site PC is off, or the link
    -- is down, or someone closed the window.
    'offline_agents', (
      select coalesce(jsonb_agg(t order by t->>'last_seen_at'), '[]'::jsonb)
        from (select jsonb_build_object(
                       'hostname', a.hostname, 'site', s.name,
                       'last_seen_at', a.last_seen_at,
                       'recorder', concat_ws(' ', a.device_vendor, a.device_model)
                     ) as t
                from agents a join sites s on s.id = a.site_id
               where a.last_seen_at is null
                  or a.last_seen_at < now() - interval '30 minutes') x),

    -- Faults the recorder itself reported in the last 24h.
    'faults_24h', (
      select coalesce(jsonb_agg(t order by (t->>'count')::int desc), '[]'::jsonb)
        from (select jsonb_build_object(
                       'event_type', e.event_type,
                       'camera', coalesce(c.name, 'unassigned'),
                       'count', count(*)) as t
                from events e left join cameras c on c.id = e.camera_id
               where e.device_ts > now() - interval '24 hours'
                 and e.event_type in ('video_loss','tamper','disk_error','disk_full')
               group by e.event_type, c.name) x)
  );
end
$$;

-- ---------------------------------------------------------------------
-- wl_daily_report — the payload behind the daily WhatsApp/email summary
-- that the product is sold on. One site, one day, in that site's own
-- timezone.
-- ---------------------------------------------------------------------
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
    -- Overnight is what a guarding company cares about.
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
               group by event_type) x)
  );
end
$$;

revoke all on function public.wl_analytics(int)          from public;
revoke all on function public.wl_site_health(int)        from public;
revoke all on function public.wl_daily_report(uuid,date) from public;

grant execute on function public.wl_analytics(int)          to anon, authenticated;
grant execute on function public.wl_site_health(int)        to anon, authenticated;
grant execute on function public.wl_daily_report(uuid,date) to anon, authenticated;
