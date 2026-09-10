-- =====================================================================
-- 0062 — Agent-attributed monitoring coverage (H3).
--
-- The server watchdog (0043) already infers agent-down from missing heartbeats and
-- folds it into monitoring coverage, but it "does not guess the cause". The Agent,
-- from its own wall clock, CAN tell the site PC was asleep/hibernated/stalled
-- (prototype/agent/coverage.py) and reports that stretch WITH a cause here, so the
-- daily report can say "Not monitored 02:21-06:28 (site PC asleep)" truthfully.
--
-- Coverage % is computed by UNIONING all three interval sources (server-unreachable,
-- reconciled-unverified, agent-attributed) via range_agg, so overlapping windows for
-- the same outage never double-count. The reporting layer consumes it: the office brief
-- gains a distinct 'monitoring_coverage' field (0060's activity 'coverage' is untouched).
-- Additive: one table, two functions, plus one new field on wl_office_brief.
-- =====================================================================

create table if not exists public.agent_coverage_gaps (
  id         bigint generated always as identity primary key,
  tenant_id  uuid not null references public.tenants(id) on delete cascade,
  site_id    uuid not null references public.sites(id) on delete cascade,
  agent_id   uuid references public.agents(id) on delete cascade,
  started_at timestamptz not null,
  ended_at   timestamptz not null,
  -- 'observation_gap' = the Agent process was not scheduled (sleep/hibernate/off/stall) — the
  -- honest generic the Agent can PROVE from its own wall clock; it does not assert OS-sleep.
  cause      text not null check (cause in
               ('observation_gap','agent_restart','recorder_lan_lost','cloud_link_lost')),
  source     text not null default 'agent',
  created_at timestamptz not null default now()
);
create index if not exists agent_coverage_gaps_site_idx
  on public.agent_coverage_gaps (site_id, started_at desc);
alter table public.agent_coverage_gaps enable row level security;

-- ---------------------------------------------------------------------
-- Agent reports a self-observed coverage gap (with cause). Agent-authenticated,
-- outbound-only, idempotent against a near-duplicate of the same window.
-- ---------------------------------------------------------------------
create or replace function public.wl_report_coverage_gap(
  p_agent_id   uuid,
  p_agent_key  text,
  p_started_at timestamptz,
  p_ended_at   timestamptz,
  p_cause      text default 'observation_gap'
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent agents;
  v_cause text := coalesce(p_cause, 'observation_gap');
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;
  if p_ended_at <= p_started_at then
    return jsonb_build_object('ok', false, 'reason', 'empty_interval');
  end if;
  if v_cause not in ('observation_gap','agent_restart','recorder_lan_lost','cloud_link_lost') then
    v_cause := 'observation_gap';
  end if;
  if exists (select 1 from agent_coverage_gaps g
              where g.agent_id = v_agent.id
                and abs(extract(epoch from (g.started_at - p_started_at))) < 5
                and abs(extract(epoch from (g.ended_at   - p_ended_at)))   < 5) then
    return jsonb_build_object('ok', true, 'duplicate', true);
  end if;
  insert into agent_coverage_gaps (tenant_id, site_id, agent_id, started_at, ended_at, cause, source)
    values (v_agent.tenant_id, v_agent.site_id, v_agent.id, p_started_at, p_ended_at, v_cause, 'agent');
  return jsonb_build_object('ok', true);
end
$$;
revoke all on function public.wl_report_coverage_gap(uuid,text,timestamptz,timestamptz,text) from public;
grant execute on function public.wl_report_coverage_gap(uuid,text,timestamptz,timestamptz,text) to anon, authenticated;

-- ---------------------------------------------------------------------
-- Report-facing coverage over a window: unions server-unreachable + reconciled-
-- unverified + agent-attributed gaps (range_agg => no double count). Tenant is
-- derived from the site (no wl_my_tenant), so the report runner can call it.
-- ---------------------------------------------------------------------
create or replace function public.wl_site_coverage_report(
  p_site_id uuid, p_from timestamptz, p_to timestamptz
) returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  with lo as (select least(p_from,p_to) a, greatest(p_from,p_to) b),
  src as (
    select started_at, ended_at, cause from unverified_intervals where site_id = p_site_id
    union all
    select started_at, ended_at, 'agent_unreachable'::text
      from agent_unreachable_intervals where site_id = p_site_id
    union all
    select started_at, ended_at, cause from agent_coverage_gaps where site_id = p_site_id
  ),
  clipped as (
    select tstzrange(greatest(s.started_at, lo.a),
                     least(coalesce(s.ended_at, lo.b), lo.b), '[)') as r
      from src s, lo
     where greatest(s.started_at, lo.a) < least(coalesce(s.ended_at, lo.b), lo.b)
  ),
  merged as (select range_agg(r) rr from clipped),
  unv as (
    select coalesce(sum(extract(epoch from (upper(x) - lower(x)))), 0)::numeric secs
      from (select unnest(rr) x from merged) z
  )
  select jsonb_build_object(
    'wall_seconds', extract(epoch from ((select b from lo) - (select a from lo)))::numeric,
    'unverified_seconds', (select secs from unv),
    'monitored_seconds', greatest(0,
        extract(epoch from ((select b from lo) - (select a from lo)))::numeric - (select secs from unv)),
    -- Intervals are clipped to [lo,hi] before range_agg, so merged unverified <= wall; the
    -- greatest/least clamp is belt-and-braces so the ratio can never exceed 1 or go negative.
    'coverage_ratio', case
        when (select b from lo) <= (select a from lo) then 1.0
        else greatest(0::numeric, least(1::numeric, round(1 - (select secs from unv)
             / nullif(extract(epoch from ((select b from lo) - (select a from lo)))::numeric, 0), 4)))
      end,
    'gaps', (select coalesce(jsonb_agg(jsonb_build_object(
                'start', to_jsonb(greatest(s.started_at, lo.a)),
                'end',   to_jsonb(least(coalesce(s.ended_at, lo.b), lo.b)),
                'cause', s.cause) order by s.started_at), '[]'::jsonb)
               from src s, lo
              where greatest(s.started_at, lo.a) < least(coalesce(s.ended_at, lo.b), lo.b))
  );
$$;
revoke all on function public.wl_site_coverage_report(uuid,timestamptz,timestamptz) from public;
grant execute on function public.wl_site_coverage_report(uuid,timestamptz,timestamptz) to authenticated, service_role;

-- ---------------------------------------------------------------------
-- Add ONE 'monitoring_coverage' field to the office brief. Body is 0060's verbatim;
-- every prior field (activity 'coverage', peak_hour, by_area, restricted,
-- after_hours_total, agent) is preserved unchanged.
-- ---------------------------------------------------------------------
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
       from agents where site_id = p_site_id),
  'monitoring_coverage', public.wl_site_coverage_report(
       p_site_id, (select v_start from win), (select v_start from win) + interval '1 day')
);
$$;
