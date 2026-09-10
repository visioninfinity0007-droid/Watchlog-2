-- =====================================================================
-- 0081 — Opening/closing state machine + after-hours truth (final integration pass, items 5+6).
--
-- v2 (0074) used first/last sustained presence episode as opening/closing. That still treats a
-- brief early cleaner visit as "opening". This replaces it with an activity-SESSION model:
--
--   internal activity is grouped into sessions separated by the site's quiet period. A session
--   only counts as OPEN when it is genuinely sustained (>= min_activity detections AND lasts at
--   least min_open_minutes). Opening = the start of the first qualifying session that follows an
--   entrance arrival; closing = the end of the last qualifying session. A short cleaner visit is
--   a non-qualifying session and is skipped; the office opens at the real session.
--
-- Inputs are site-configurable: entrance cameras, internal cameras, operating hours, quiet
-- period, minimum activity, minimum open duration. If entrance cameras are not mapped, an
-- explicitly-labelled lower-confidence FALLBACK runs on all cameras (it never pretends the
-- primary model ran). Coverage lowers confidence and is disclosed.
--
-- After-hours (item 6): if business hours are genuinely unknown, after-hours is NOT asserted —
-- it returns "not verified / business schedule incomplete".
-- =====================================================================

alter table public.site_business_context add column if not exists internal_camera_ids uuid[] not null default '{}';
alter table public.site_business_context add column if not exists quiet_period_minutes int not null default 30;
alter table public.site_business_context add column if not exists min_activity int not null default 2;
alter table public.site_business_context add column if not exists min_open_minutes int not null default 15;

create or replace function public.wl_site_day_state(p_site_id uuid, p_date date default null)
returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
  v_site sites; v_tz text; v_date date; v_start timestamptz; v_end timestamptz; v_to timestamptz;
  v_ent uuid[]; v_int uuid[]; v_open time; v_close time; v_overnight boolean; v_wdays int[];
  v_quiet int; v_minact int; v_minopen int; v_has_ent boolean; v_working boolean;
  v_ratio numeric; v_first_arrival timestamptz; v_open_at timestamptz; v_close_at timestamptz;
  v_model text; v_conf numeric; v_open_basis text; v_state text; v_after jsonb; v_now timestamptz := now();
begin
  select * into v_site from sites where id = p_site_id;
  if v_site.id is null then raise exception 'no such site' using errcode = '22023'; end if;
  v_tz := v_site.timezone;
  v_date := coalesce(p_date, (v_now at time zone v_tz)::date);
  v_start := (v_date::text||' 00:00:00')::timestamp at time zone v_tz;
  v_end := v_start + interval '1 day';
  v_to := least(v_end, greatest(v_start, v_now));

  select coalesce(entrance_camera_ids,'{}'), coalesce(internal_camera_ids,'{}'),
         open_time, close_time, coalesce(overnight,false), coalesce(working_days,'{1,2,3,4,5}'),
         coalesce(quiet_period_minutes,30), coalesce(min_activity,2), coalesce(min_open_minutes,15)
    into v_ent, v_int, v_open, v_close, v_overnight, v_wdays, v_quiet, v_minact, v_minopen
    from site_business_context where site_id = p_site_id;
  if v_wdays is null then v_wdays := '{1,2,3,4,5}'; end if;
  v_has_ent := coalesce(array_length(v_ent,1),0) > 0;
  -- internal defaults to "every camera that isn't an entrance" when entrances are mapped
  if coalesce(array_length(v_int,1),0) = 0 and v_has_ent then
    select coalesce(array_agg(id),'{}') into v_int from cameras where site_id = p_site_id and not (id = any(v_ent));
  end if;
  v_working := (extract(isodow from (v_start at time zone v_tz))::int = any(v_wdays));

  v_ratio := coalesce((wl_site_coverage_report(p_site_id, v_start, v_to)->>'coverage_ratio')::numeric, 1);

  -- activity SESSIONS (quiet-period-separated) over the relevant camera set; a qualifying
  -- session is genuinely sustained (>= min_activity detections AND >= min_open_minutes long).
  -- Opening = first qualifying session start; closing = last qualifying session end.
  select min(start_at), max(end_at) into v_open_at, v_close_at
    from (
      with base as (
        select ep.started_at, ep.ended_at, ep.detection_count
          from episodes ep
         where ep.site_id = p_site_id and ep.episode_type = 'presence'
           and ep.started_at >= v_start and ep.started_at < v_end
           and (case when v_has_ent then ep.camera_id = any(v_int) else true end)
      ),
      marked as (
        select *, case when extract(epoch from (started_at - lag(ended_at) over (order by started_at)))
                            > v_quiet*60 or lag(ended_at) over (order by started_at) is null then 1 else 0 end ng
          from base
      ),
      grp as (select *, sum(ng) over (order by started_at rows unbounded preceding) g from marked)
      select min(started_at) start_at, max(ended_at) end_at, sum(detection_count) det,
             extract(epoch from (max(ended_at)-min(started_at)))/60.0 dur_min
        from grp group by g
    ) sess
   where det >= v_minact and dur_min >= v_minopen;

  if v_has_ent then
    v_model := 'state_machine';
    select min(started_at) into v_first_arrival from episodes
      where site_id=p_site_id and episode_type='presence' and camera_id = any(v_ent)
        and started_at >= v_start and started_at < v_end;
    if v_open_at is not null and v_first_arrival is not null and v_first_arrival <= v_open_at then
      v_open_basis := 'entrance arrival then sustained internal activity'; v_conf := 0.9;
    elsif v_open_at is not null then
      v_open_basis := 'sustained internal activity (no entrance arrival observed)'; v_conf := 0.7;
    else
      v_open_basis := 'no sustained internal session'; v_conf := 0.4;
    end if;
  else
    v_model := 'fallback_sustained_presence';
    v_open_basis := 'fallback: sustained activity session (entrance cameras not configured)'; v_conf := 0.55;
  end if;
  v_conf := round(v_conf * v_ratio, 2);

  -- open/closed state at report time (quiet period since last activity)
  if v_close_at is null then v_state := 'closed';
  elsif v_date = (v_now at time zone v_tz)::date and v_close_at > v_now - make_interval(mins => v_quiet)
    then v_state := 'open'; v_close_at := null;   -- still open; closing not yet determined
  else v_state := 'closed'; end if;

  -- after-hours truth (item 6): assert only when we actually know the schedule
  if v_open is null then
    v_after := jsonb_build_object('verified', false, 'count', null,
                 'reason', 'business schedule incomplete — after-hours not asserted');
  else
    v_after := jsonb_build_object('verified', true,
      'count', (select count(*) from episodes ep where ep.site_id=p_site_id
                  and ep.started_at>=v_start and ep.started_at<v_end
                  and not ((extract(isodow from (ep.started_at at time zone v_tz))::int = any(v_wdays))
                           and (case when v_overnight then ((ep.started_at at time zone v_tz)::time >= v_open or (ep.started_at at time zone v_tz)::time < v_close)
                                     else ((ep.started_at at time zone v_tz)::time >= v_open and (ep.started_at at time zone v_tz)::time < v_close) end))),
      'reason', null);
  end if;

  return jsonb_build_object(
    'model', v_model,
    'opening_at', to_char(v_open_at at time zone v_tz, 'HH24:MI'),
    'closing_at', to_char(v_close_at at time zone v_tz, 'HH24:MI'),
    'state_at_report', v_state,
    'opening_basis', v_open_basis,
    'confidence', v_conf,
    'low_confidence', v_conf < 0.7,
    'working_day', v_working,
    'after_hours', v_after,
    'notes', (select coalesce(jsonb_agg(to_jsonb(x)),'[]'::jsonb) from (
        select case when v_ratio < 1 then 'monitoring had gaps; opening/closing may fall inside an unobserved window' end x
        union all select case when not v_has_ent then 'entrance cameras not configured — lower-confidence fallback model' end
        union all select case when not v_working then 'non-working day for this site' end
        union all select case when v_open is null then 'business hours not configured' end) z where x is not null));
end $$;
revoke all on function public.wl_site_day_state(uuid,date) from public, anon, authenticated;
grant execute on function public.wl_site_day_state(uuid,date) to service_role;

-- ---------------------------------------------------------------------
-- Daily intelligence v3: day_boundaries now come from the opening/closing state machine,
-- and after-hours is the honest object (asserted only when the schedule is known).
-- ---------------------------------------------------------------------
create or replace function public.wl_daily_intelligence(
  p_site_id uuid, p_date date default null, p_derive boolean default true
) returns jsonb
language plpgsql volatile security definer set search_path = public as $$
declare
  v_site sites; v_date date; v_start timestamptz; v_end timestamptz; v_to timestamptz; v_tz text; v_partial boolean;
  v_office jsonb; v_coverage jsonb; v_episodes jsonb; v_incidents jsonb; v_attn jsonb;
  v_people jsonb; v_bounds jsonb; v_restricted jsonb; v_restr text[]; v_ratio numeric; v_open time;
begin
  select * into v_site from sites where id = p_site_id;
  if v_site.id is null then raise exception 'no such site' using errcode = '22023'; end if;
  v_tz := v_site.timezone;
  v_date := coalesce(p_date, (now() at time zone v_tz)::date);
  v_start := (v_date::text||' 00:00:00')::timestamp at time zone v_tz;
  v_end := v_start + interval '1 day';
  v_partial := v_end > now();
  v_to := least(v_end, greatest(v_start, now()));
  select coalesce(restricted_purposes,'{armory,restricted,vault,strongroom,safe}'), open_time
    into v_restr, v_open from site_business_context where site_id = p_site_id;
  if v_restr is null then v_restr := '{armory,restricted,vault,strongroom,safe}'; end if;

  if p_derive then
    perform wl_derive_activities(p_site_id, v_start, v_end);
    perform wl_derive_episodes(p_site_id, v_start, v_end);
    perform wl_derive_journeys(p_site_id, v_start, v_end);
    perform wl_promote_incidents(p_site_id, v_start, v_end);
    perform wl_derive_entity_inferences(p_site_id, v_start - interval '13 days', v_end);
  end if;

  v_office := wl_office_brief(p_site_id, v_date);
  v_coverage := wl_site_coverage_report(p_site_id, v_start, v_to);
  v_ratio := coalesce((v_coverage->>'coverage_ratio')::numeric, 1);
  v_people := wl_site_entity_inferences(p_site_id, v_start, v_end);
  v_bounds := wl_site_day_state(p_site_id, v_date);

  select coalesce(jsonb_agg(jsonb_build_object(
           'camera', coalesce(c.name,'unassigned'), 'purpose', c.purpose, 'type', ep.episode_type,
           'object_class', ep.object_class,
           'start', to_char(ep.started_at at time zone v_tz,'HH24:MI'),
           'end', to_char(ep.ended_at at time zone v_tz,'HH24:MI'),
           'dwell_seconds', ep.dwell_seconds, 'detections', ep.detection_count) order by ep.started_at), '[]'::jsonb)
    into v_episodes from episodes ep left join cameras c on c.id = ep.camera_id
   where ep.site_id = p_site_id and ep.started_at >= v_start and ep.started_at < v_end;

  select coalesce(jsonb_agg(jsonb_build_object(
           'camera', coalesce(c.name,'unassigned'), 'purpose', c.purpose, 'episodes', cnt,
           'last', to_char(last_at at time zone v_tz, 'HH24:MI')) order by c.name), '[]'::jsonb)
    into v_restricted from (
      select ep.camera_id, count(*) cnt, max(ep.ended_at) last_at from episodes ep
        left join cameras c on c.id = ep.camera_id
       where ep.site_id = p_site_id and ep.started_at >= v_start and ep.started_at < v_end
         and exists (select 1 from unnest(v_restr) term
                      where coalesce(c.purpose,'') ilike '%'||term||'%' or coalesce(c.name,'') ilike '%'||term||'%')
       group by ep.camera_id) z left join cameras c on c.id = z.camera_id;

  select coalesce(jsonb_agg(jsonb_build_object(
           'type', ii.incident_type, 'severity', ii.severity, 'camera', coalesce(c.name,'unassigned'),
           'purpose', c.purpose, 'time', to_char(ii.occurred_at at time zone v_tz,'HH24:MI'),
           'status', ii.status, 'policy', ii.detail_json->>'policy', 'detail', ii.detail_json)
           order by case ii.severity when 'critical' then 0 when 'warning' then 1 else 2 end, ii.occurred_at), '[]'::jsonb)
    into v_incidents from intel_incidents ii left join cameras c on c.id = ii.camera_id
   where ii.site_id = p_site_id and ii.occurred_at >= v_start and ii.occurred_at < v_end;

  select jsonb_build_object('incidents_total', count(*),
           'critical', count(*) filter (where severity='critical'),
           'warning', count(*) filter (where severity='warning'),
           'info', count(*) filter (where severity='info'))
    into v_attn from intel_incidents where site_id = p_site_id and occurred_at >= v_start and occurred_at < v_end;

  return jsonb_build_object(
    'schema', 'daily_intelligence.v3',
    'meta', jsonb_build_object('site', v_site.name, 'site_id', v_site.id, 'site_type', v_site.site_type,
       'date', v_date, 'timezone', v_tz, 'generated_at', now(), 'partial_day', v_partial),
    'day_boundaries', v_bounds,
    'office', v_office,
    'coverage', v_coverage,
    'access_windows', v_episodes,
    'restricted', v_restricted,
    'after_hours', v_bounds->'after_hours',
    'incidents', v_incidents,
    'attention', v_attn,
    'people', v_people,
    'honesty', (select coalesce(jsonb_agg(to_jsonb(x)), '[]'::jsonb) from (
        select 'Counts are camera detections, not a headcount of distinct people.'::text as x
        union all select case when v_partial then 'Partial day: figures cover midnight to report time only.' end
        union all select case when v_ratio < 1 then 'Monitoring had gaps in this window; some activity may be unobserved.' end
        union all select case when v_open is null then 'Business hours are not configured, so after-hours is not asserted.' end
        union all select 'Visitor/staff labels are estimated behavioral classifications from movement patterns, not identities.'
      ) z where x is not null));
end $$;
revoke all on function public.wl_daily_intelligence(uuid,date,boolean) from public, anon, authenticated;
grant execute on function public.wl_daily_intelligence(uuid,date,boolean) to service_role;
