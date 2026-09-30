-- Service-day monitoring truth and recorder-push authority hardening.
--
-- A recorder-push virtual agent is alarm/chatter driven and must never become
-- heartbeat/coverage authority. Coverage must also treat time before the first
-- real site agent was enrolled as unverified rather than implicitly monitored.

create or replace function public.wl_site_coverage_report(
  p_site_id uuid,
  p_from timestamptz,
  p_to timestamptz
)
returns jsonb
language sql
stable
security definer
set search_path = public
as $function$
  with lo as (
    select least(p_from,p_to) a, greatest(p_from,p_to) b
  ),
  cfg as (
    select coalesce(multi_agent_enabled,false) as multi_agent_enabled
      from public.sites
     where id = p_site_id
  ),
  real_agents as (
    select a.*
      from public.agents a
     where a.site_id = p_site_id
       and coalesce(a.device_driver,'') <> 'recorder-push'
  ),
  authority as (
    select a.id as agent_id,
           a.enrolled_at as auth_start,
           lead(a.enrolled_at) over (
             partition by a.site_id
             order by a.enrolled_at, a.id
           ) as auth_end
      from real_agents a
  ),
  first_authority as (
    select min(auth_start) as first_start from authority
  ),
  raw as (
    select u.agent_id, u.started_at, u.ended_at, u.cause
      from public.unverified_intervals u
     where u.site_id = p_site_id
       and (
         u.agent_id is null
         or exists (select 1 from real_agents a where a.id=u.agent_id)
       )
    union all
    select u.agent_id, u.started_at, u.ended_at, 'agent_unreachable'::text
      from public.agent_unreachable_intervals u
     where u.site_id = p_site_id
       and (
         u.agent_id is null
         or exists (select 1 from real_agents a where a.id=u.agent_id)
       )
    union all
    select u.agent_id, u.started_at, u.ended_at, u.cause
      from public.agent_coverage_gaps u
     where u.site_id = p_site_id
       and (
         u.agent_id is null
         or exists (select 1 from real_agents a where a.id=u.agent_id)
       )
  ),
  bounded as (
    select r.agent_id,
           r.cause,
           case
             when c.multi_agent_enabled then r.started_at
             when r.agent_id is null then r.started_at
             else greatest(r.started_at, aw.auth_start)
           end as started_at,
           case
             when c.multi_agent_enabled then r.ended_at
             when r.agent_id is null then r.ended_at
             else least(
               coalesce(r.ended_at, (select b from lo)),
               coalesce(aw.auth_end, (select b from lo))
             )
           end as ended_at
      from raw r
      cross join cfg c
      left join authority aw on aw.agent_id = r.agent_id
     where c.multi_agent_enabled
        or r.agent_id is null
        or aw.agent_id is not null
  ),
  authority_absence as (
    select null::uuid as agent_id,
           'no_authoritative_agent'::text as cause,
           lo.a as started_at,
           least(coalesce(f.first_start,lo.b),lo.b) as ended_at
      from lo
      cross join first_authority f
     where lo.a < least(coalesce(f.first_start,lo.b),lo.b)
  ),
  src as (
    select * from bounded
     where started_at < coalesce(ended_at,(select b from lo))
    union all
    select * from authority_absence
  ),
  clipped as (
    select tstzrange(
             greatest(s.started_at,lo.a),
             least(coalesce(s.ended_at,lo.b),lo.b),
             '[)'
           ) as r
      from src s, lo
     where greatest(s.started_at,lo.a)
           < least(coalesce(s.ended_at,lo.b),lo.b)
  ),
  merged as (
    select range_agg(r) rr from clipped
  ),
  unv as (
    select coalesce(
             sum(extract(epoch from (upper(x)-lower(x)))),
             0
           )::numeric as secs
      from (select unnest(rr) x from merged) z
  )
  select jsonb_build_object(
    'wall_seconds',
      extract(epoch from ((select b from lo)-(select a from lo)))::numeric,
    'unverified_seconds',(select secs from unv),
    'monitored_seconds',
      greatest(
        0,
        extract(epoch from ((select b from lo)-(select a from lo)))::numeric
        -(select secs from unv)
      ),
    'coverage_ratio',
      case
        when (select b from lo) <= (select a from lo) then 1.0
        else greatest(
          0::numeric,
          least(
            1::numeric,
            round(
              1-(select secs from unv)
                / nullif(
                    extract(epoch from ((select b from lo)-(select a from lo)))::numeric,
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
              'start',to_jsonb(greatest(s.started_at,lo.a)),
              'end',to_jsonb(least(coalesce(s.ended_at,lo.b),lo.b)),
              'cause',s.cause,
              'agent_id',s.agent_id
            )
            order by s.started_at
          ),
          '[]'::jsonb
        )
          from src s, lo
         where greatest(s.started_at,lo.a)
               < least(coalesce(s.ended_at,lo.b),lo.b)
      )
  );
$function$;

comment on function public.wl_site_coverage_report(uuid,timestamptz,timestamptz) is
  'Site monitoring coverage. Recorder-push virtual agents never become heartbeat/coverage authority. Time before the first real site agent enrollment is unverified.';

create or replace function public.wl_my_business_day_monitoring(
  p_site_id uuid,
  p_date date default null
)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $function$
declare
  v_tenant uuid;
  v_window jsonb;
  v_from timestamptz;
  v_to timestamptz;
  v_date date;
  v_coverage jsonb;
  v_visual jsonb;
  v_report jsonb;
  v_ratio numeric;
begin
  v_tenant := public.wl_assert_my_site(p_site_id);

  v_window := public.wl_my_business_day_window(p_site_id,p_date);
  v_from := (v_window->>'from')::timestamptz;
  v_to := (v_window->>'to')::timestamptz;
  v_date := (v_window->>'business_date')::date;

  v_coverage := public.wl_site_coverage_report_classes(p_site_id,v_from,v_to);
  v_ratio := coalesce(
    (v_coverage#>>'{classes,total_coverage_ratio}')::numeric,
    (v_coverage->>'coverage_ratio')::numeric,
    0
  );

  v_visual := public.wl_my_visual_day(p_site_id,v_date);
  v_report := public.wl_my_report_snapshot(p_site_id,v_date);

  return jsonb_build_object(
    'business_date',v_date,
    'window',v_window,
    'fully_monitored',v_ratio >= 0.9999,
    'coverage',v_coverage,
    'visual_review',
      case when v_visual is null then null else jsonb_build_object(
        'status',v_visual->>'status',
        'snapshots_total',v_visual->'snapshots_total',
        'snapshots_analyzed',v_visual->'snapshots_analyzed',
        'review_complete',coalesce((v_visual#>>'{summary,review_complete}')::boolean,false)
      ) end,
    'saved_report_coverage',
      case when v_report is null then null else v_report#>'{payload,coverage}' end
  );
end
$function$;

revoke all on function public.wl_my_business_day_monitoring(uuid,date) from public,anon;
grant execute on function public.wl_my_business_day_monitoring(uuid,date) to authenticated;
