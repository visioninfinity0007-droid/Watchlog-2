-- =====================================================================
-- 0060 — Daily Office Intelligence brief (v0).
--
-- The existing daily report answers "how many security events". For an
-- office site the customer wants "what happened at the office today" in
-- business language: when activity started/ended, how busy each area was,
-- how many distinct access windows a restricted area saw, and whether any
-- of it was after hours. This derives those from the RAW person-event
-- stream we already collect — no configured tripwires/zones required (this
-- class of recorder has no IVS), and no identity/roster claims.
--
-- HONESTY BOUNDARIES baked in:
--   * "person events" are detections, NOT a headcount. We report activity
--     windows and access episodes, never a certain number of people.
--   * "access episode" = a run of detections on one camera separated from
--     the next by > 10 minutes of quiet. It approximates distinct visits.
--   * full_day=false when coverage does not span a normal working day, so a
--     partial day (e.g. the agent only came online mid-afternoon) is stated,
--     not dressed up as a full day.
--   * unique visitors / staff-vs-visitor / cross-camera journeys are v1+ and
--     deliberately NOT emitted here (they need appearance/direction signals).
--   * camera-signal health (e.g. armory video-loss) is intentionally out of
--     scope here until the agent reconciles recorder video-loss into
--     camera_health; this brief does not assert cameras are healthy.
--
-- Additive only: adds one function and adds one field ('office') to the
-- existing wl_daily_report payload. All other report fields are unchanged.
-- =====================================================================

create or replace function public.wl_office_brief(
  p_site_id uuid,
  p_date    date
) returns jsonb
language sql
stable
security definer
set search_path = public
as $$
with s as (
  select id, timezone from sites where id = p_site_id
),
win as (
  select (p_date::text || ' 00:00:00')::timestamp
           at time zone (select timezone from s) as v_start
),
base as (
  select e.camera_id, c.name, c.purpose, e.device_ts,
         (e.device_ts at time zone (select timezone from s)) as ts_local
    from events e
    join cameras c on c.id = e.camera_id, s, win
   where e.site_id = s.id
     and e.event_type = 'person'
     and e.device_ts >= win.v_start
     and e.device_ts <  win.v_start + interval '1 day'
),
epi as (
  select *,
         case when extract(epoch from (device_ts - lag(device_ts) over w)) > 600
                or lag(device_ts) over w is null
              then 1 else 0 end as ne
    from base
  window w as (partition by camera_id order by device_ts)
)
select jsonb_build_object(
  'coverage', (
     select jsonb_build_object(
       'first', to_char(min(ts_local), 'HH24:MI'),
       'last',  to_char(max(ts_local), 'HH24:MI'),
       'person_events', count(*),
       'full_day', coalesce(min(ts_local)::time < time '10:00'
                            and max(ts_local)::time > time '17:00', false))
     from base),
  'peak_hour', (
     select jsonb_build_object('hour', h, 'count', n)
       from (select extract(hour from ts_local)::int h, count(*) n
               from base group by 1 order by 2 desc limit 1) z),
  'by_area', (
     select coalesce(jsonb_agg(jsonb_build_object(
              'camera', name, 'events', n, 'episodes', ep,
              'first', f, 'last', l) order by n desc), '[]'::jsonb)
       from (select name, count(*) n, sum(ne) ep,
                    to_char(min(ts_local),'HH24:MI') f,
                    to_char(max(ts_local),'HH24:MI') l
               from epi group by name) z),
  'restricted', (
     select coalesce(jsonb_agg(jsonb_build_object(
              'camera', name, 'episodes', ep,
              'after_hours', ah, 'last', l) order by name), '[]'::jsonb)
       from (select name, sum(ne) ep,
                    count(*) filter (where ts_local::time < time '08:00'
                                        or ts_local::time > time '19:00') ah,
                    to_char(max(ts_local),'HH24:MI') l
               from epi
              where purpose ilike '%armory%' or purpose ilike '%restrict%'
                 or name ilike '%armory%'
              group by name) z),
  'after_hours_total', (
     select count(*) from base
      where ts_local::time < time '08:00' or ts_local::time > time '19:00'),
  'agent', (
     select case when max(last_seen_at) is null then null
                 else jsonb_build_object(
                        'last_seen', max(last_seen_at),
                        'online', max(last_seen_at) > now() - interval '15 minutes')
            end
       from agents where site_id = p_site_id)
);
$$;

revoke all on function public.wl_office_brief(uuid,date) from public,anon,authenticated;

-- ---------------------------------------------------------------------
-- Add the office brief to the canonical report payload. Body is 0027's
-- verbatim, with a single new 'office' field appended. Every prior field
-- is preserved so existing report composition/tests are unaffected.
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
    'analytics', public.wl_daily_analytics(v_site.id, v_date),
    'office', public.wl_office_brief(v_site.id, v_date)
  );
end
$$;

revoke all on function public.wl_daily_report(uuid,date) from public,anon,authenticated;
