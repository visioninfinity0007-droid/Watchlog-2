-- =====================================================================
-- 0099 — Daily intelligence v4: surface the THREE coverage classes in the client dataset (§3).
--
-- Identical to the 0081 v3 body except the coverage section now comes from
-- wl_site_coverage_report_classes (0098), so the one canonical payload carries
-- coverage.classes = { live / recovered / unverified }, never a single blended figure.
-- =====================================================================

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
  v_coverage := wl_site_coverage_report_classes(p_site_id, v_start, v_to);   -- 3-class coverage
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
    'schema', 'daily_intelligence.v4',
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
        union all select case when coalesce((v_coverage#>>'{classes,recovered_seconds}')::numeric,0) > 0
                    then 'Coverage separates LIVE monitored, RECOVERED from the recorder, and UNVERIFIED time — never blended.' end
        union all select case when v_open is null then 'Business hours are not configured, so after-hours is not asserted.' end
        union all select 'Visitor/staff labels are estimated behavioral classifications from movement patterns, not identities.'
      ) z where x is not null));
end $$;
revoke all on function public.wl_daily_intelligence(uuid,date,boolean) from public, anon, authenticated;
grant execute on function public.wl_daily_intelligence(uuid,date,boolean) to service_role;
