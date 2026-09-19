-- =====================================================================
-- 0091 — Authority-aware monitoring coverage
--
-- Fixes a single-agent replacement bug in 0062:
-- coverage intervals were unioned by site_id, so an old Agent's still-open
-- `agent_unreachable` interval could continue to make the CURRENT site appear
-- unmonitored after a replacement Agent was enrolled.
--
-- Single-agent authority:
--   agent.enrolled_at -> next agent.enrolled_at
-- The newest Agent's authority extends to the requested report end.
--
-- Site-level intervals with no agent_id stay site-level.
-- Unknown/inconsistent agent references fail CLOSED (their interval is retained).
--
-- Multi-agent sites deliberately preserve the pre-0091 behavior. Correct
-- multi-agent coverage requires lease-history authority, not an enrollment
-- approximation.
-- =====================================================================

create or replace function public.wl_site_coverage_report(
  p_site_id uuid, p_from timestamptz, p_to timestamptz
) returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  with lo as (
    select least(p_from,p_to) a, greatest(p_from,p_to) b
  ),
  cfg as (
    select coalesce(multi_agent_enabled,false) as multi_agent_enabled
      from public.sites
     where id = p_site_id
  ),
  authority as (
    select a.id as agent_id,
           a.enrolled_at as auth_start,
           lead(a.enrolled_at) over (
             partition by a.site_id
             order by a.enrolled_at, a.id
           ) as auth_end
      from public.agents a
     where a.site_id = p_site_id
  ),
  raw as (
    select agent_id, started_at, ended_at, cause
      from public.unverified_intervals
     where site_id = p_site_id
    union all
    select agent_id, started_at, ended_at, 'agent_unreachable'::text
      from public.agent_unreachable_intervals
     where site_id = p_site_id
    union all
    select agent_id, started_at, ended_at, cause
      from public.agent_coverage_gaps
     where site_id = p_site_id
  ),
  bounded as (
    select r.agent_id,
           r.cause,
           case
             when c.multi_agent_enabled then r.started_at
             when r.agent_id is null then r.started_at
             when aw.agent_id is null then r.started_at
             else greatest(r.started_at, aw.auth_start)
           end as started_at,
           case
             when c.multi_agent_enabled then r.ended_at
             when r.agent_id is null then r.ended_at
             when aw.agent_id is null then r.ended_at
             else least(
               coalesce(r.ended_at, (select b from lo)),
               coalesce(aw.auth_end, (select b from lo))
             )
           end as ended_at
      from raw r
      cross join cfg c
      left join authority aw on aw.agent_id = r.agent_id
  ),
  src as (
    select *
      from bounded
     where started_at < coalesce(ended_at, (select b from lo))
  ),
  clipped as (
    select tstzrange(
             greatest(s.started_at, lo.a),
             least(coalesce(s.ended_at, lo.b), lo.b),
             '[)'
           ) as r
      from src s, lo
     where greatest(s.started_at, lo.a)
           < least(coalesce(s.ended_at, lo.b), lo.b)
  ),
  merged as (
    select range_agg(r) rr from clipped
  ),
  unv as (
    select coalesce(
             sum(extract(epoch from (upper(x) - lower(x)))),
             0
           )::numeric as secs
      from (select unnest(rr) x from merged) z
  )
  select jsonb_build_object(
    'wall_seconds',
      extract(epoch from ((select b from lo) - (select a from lo)))::numeric,
    'unverified_seconds', (select secs from unv),
    'monitored_seconds',
      greatest(
        0,
        extract(epoch from ((select b from lo) - (select a from lo)))::numeric
        - (select secs from unv)
      ),
    'coverage_ratio',
      case
        when (select b from lo) <= (select a from lo) then 1.0
        else greatest(
          0::numeric,
          least(
            1::numeric,
            round(
              1 - (select secs from unv)
                  / nullif(
                      extract(epoch from ((select b from lo) - (select a from lo)))::numeric,
                      0
                    ),
              4
            )
          )
        )
      end,
    'gaps',
      (
        select coalesce(
          jsonb_agg(
            jsonb_build_object(
              'start', to_jsonb(greatest(s.started_at, lo.a)),
              'end', to_jsonb(least(coalesce(s.ended_at, lo.b), lo.b)),
              'cause', s.cause,
              'agent_id', s.agent_id
            )
            order by s.started_at
          ),
          '[]'::jsonb
        )
          from src s, lo
         where greatest(s.started_at, lo.a)
               < least(coalesce(s.ended_at, lo.b), lo.b)
      )
  );
$$;

revoke all on function public.wl_site_coverage_report(uuid,timestamptz,timestamptz)
  from public;
grant execute on function public.wl_site_coverage_report(uuid,timestamptz,timestamptz)
  to authenticated, service_role;
