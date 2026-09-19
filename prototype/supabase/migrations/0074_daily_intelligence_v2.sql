-- =====================================================================
-- 0074 — Daily intelligence v2: honest opening/closing, generic after-hours & restricted,
-- and the visitor/staff section folded in (items 14 + 1).
--
-- Supersedes the 0067 wl_daily_intelligence body. Corrections vs v1:
--   * opening/closing are NOT the first/last Person event. They are the start of the first,
--     and end of the last, SUSTAINED presence episode of the day (>= 2 detections) — a blip
--     does not open the office.
--   * opening/closing carry a CONFIDENCE that drops with monitoring coverage: a gap means the
--     true boundary may be earlier/later and we say so.
--   * after-hours honors the SITE's business hours + working days + timezone (generic), not a
--     hardcoded 08:00–19:00. If hours are unknown we do NOT claim anything is "after-hours".
--   * restricted areas come from the site's configured restricted_purposes (generic), not an
--     Al-Khalid "armory" literal.
--   * a `people` section from the visitor/staff inference (0073), derived over a trailing
--     window so recurrence is meaningful.
-- =====================================================================

create or replace function public.wl_daily_intelligence(
  p_site_id uuid,
  p_date    date default null,
  p_derive  boolean default true
) returns jsonb
language plpgsql
volatile
security definer
set search_path = public
as $$
declare
  v_site sites; v_date date; v_start timestamptz; v_end timestamptz; v_to timestamptz;
  v_tz text; v_partial boolean;
  v_office jsonb; v_coverage jsonb; v_episodes jsonb; v_incidents jsonb; v_attn jsonb;
  v_people jsonb; v_bounds jsonb; v_restricted jsonb;
  v_open time; v_close time; v_overnight boolean; v_wdays int[]; v_restr text[];
  v_ratio numeric; v_after int;
begin
  select * into v_site from sites where id = p_site_id;
  if v_site.id is null then raise exception 'no such site' using errcode = '22023'; end if;
  v_tz := v_site.timezone;
  v_date  := coalesce(p_date, (now() at time zone v_tz)::date);
  v_start := (v_date::text || ' 00:00:00')::timestamp at time zone v_tz;
  v_end   := v_start + interval '1 day';
  v_partial := v_end > now();
  v_to := least(v_end, greatest(v_start, now()));

  select open_time, close_time, coalesce(overnight,false), coalesce(working_days,'{1,2,3,4,5}'),
         coalesce(restricted_purposes,'{armory,restricted,vault,strongroom,safe}')
    into v_open, v_close, v_overnight, v_wdays, v_restr
    from site_business_context where site_id = p_site_id;
  if v_wdays is null then v_wdays := '{1,2,3,4,5}'; end if;
  if v_restr is null then v_restr := '{armory,restricted,vault,strongroom,safe}'; end if;

  if p_derive then
    perform wl_derive_activities(p_site_id, v_start, v_end);
    perform wl_derive_episodes(p_site_id, v_start, v_end);
    perform wl_derive_journeys(p_site_id, v_start, v_end);
    perform wl_promote_incidents(p_site_id, v_start, v_end);
    -- inferences over a trailing 14-day window so recurrence is meaningful
    perform wl_derive_entity_inferences(p_site_id, v_start - interval '13 days', v_end);
  end if;

  v_office   := wl_office_brief(p_site_id, v_date);
  v_coverage := wl_site_coverage_report(p_site_id, v_start, v_to);
  v_ratio    := coalesce((v_coverage->>'coverage_ratio')::numeric, 1);
  v_people   := wl_site_entity_inferences(p_site_id, v_start, v_end);

  -- Opening/closing = first/last SUSTAINED presence episode (>=2 detections), with a
  -- coverage-scaled confidence. NULL when the day had no sustained presence.
  select jsonb_build_object(
    'opening_at', to_char(min(started_at) at time zone v_tz, 'HH24:MI'),
    'closing_at', to_char(max(ended_at) at time zone v_tz, 'HH24:MI'),
    'basis', 'first/last sustained presence episode (>=2 detections)',
    'confidence', round(v_ratio, 2),
    'low_confidence', v_ratio < 0.9,
    'note', case when v_ratio < 0.999
                 then 'monitoring had gaps; the true opening/closing may fall inside an unobserved window'
                 else null end)
    into v_bounds
    from episodes
   where site_id = p_site_id and episode_type = 'presence'
     and started_at >= v_start and started_at < v_end and detection_count >= 2;

  -- After-hours count (generic: outside business hours OR non-working day, in site tz).
  select count(*) into v_after
    from episodes ep
   where ep.site_id = p_site_id and ep.started_at >= v_start and ep.started_at < v_end
     and not (
       (extract(isodow from (ep.started_at at time zone v_tz))::int = any(v_wdays))
       and (v_open is null
            or (case when v_overnight then ((ep.started_at at time zone v_tz)::time >= v_open
                                            or (ep.started_at at time zone v_tz)::time < v_close)
                     else ((ep.started_at at time zone v_tz)::time >= v_open
                           and (ep.started_at at time zone v_tz)::time < v_close) end)));

  -- Restricted-area access (generic: camera purpose/name matches a configured restricted term).
  select coalesce(jsonb_agg(jsonb_build_object(
           'camera', coalesce(c.name,'unassigned'), 'purpose', c.purpose,
           'episodes', cnt, 'last', to_char(last_at at time zone v_tz, 'HH24:MI')) order by c.name), '[]'::jsonb)
    into v_restricted
    from (
      select ep.camera_id, count(*) cnt, max(ep.ended_at) last_at
        from episodes ep
        left join cameras c on c.id = ep.camera_id
       where ep.site_id = p_site_id and ep.started_at >= v_start and ep.started_at < v_end
         and exists (select 1 from unnest(v_restr) term
                      where coalesce(c.purpose,'') ilike '%'||term||'%' or coalesce(c.name,'') ilike '%'||term||'%')
       group by ep.camera_id) z
    left join cameras c on c.id = z.camera_id;

  select coalesce(jsonb_agg(jsonb_build_object(
           'camera', coalesce(c.name,'unassigned'), 'purpose', c.purpose, 'type', ep.episode_type,
           'object_class', ep.object_class,
           'start', to_char(ep.started_at at time zone v_tz,'HH24:MI'),
           'end', to_char(ep.ended_at at time zone v_tz,'HH24:MI'),
           'dwell_seconds', ep.dwell_seconds, 'detections', ep.detection_count) order by ep.started_at), '[]'::jsonb)
    into v_episodes
    from episodes ep left join cameras c on c.id = ep.camera_id
   where ep.site_id = p_site_id and ep.started_at >= v_start and ep.started_at < v_end;

  select coalesce(jsonb_agg(jsonb_build_object(
           'type', ii.incident_type, 'severity', ii.severity, 'camera', coalesce(c.name,'unassigned'),
           'purpose', c.purpose, 'time', to_char(ii.occurred_at at time zone v_tz,'HH24:MI'),
           'status', ii.status, 'policy', ii.detail_json->>'policy', 'detail', ii.detail_json)
           order by case ii.severity when 'critical' then 0 when 'warning' then 1 else 2 end, ii.occurred_at), '[]'::jsonb)
    into v_incidents
    from intel_incidents ii left join cameras c on c.id = ii.camera_id
   where ii.site_id = p_site_id and ii.occurred_at >= v_start and ii.occurred_at < v_end;

  select jsonb_build_object('incidents_total', count(*),
           'critical', count(*) filter (where severity='critical'),
           'warning', count(*) filter (where severity='warning'),
           'info', count(*) filter (where severity='info'))
    into v_attn from intel_incidents
   where site_id = p_site_id and occurred_at >= v_start and occurred_at < v_end;

  return jsonb_build_object(
    'schema', 'daily_intelligence.v2',
    'meta', jsonb_build_object('site', v_site.name, 'site_id', v_site.id, 'site_type', v_site.site_type,
       'date', v_date, 'timezone', v_tz, 'generated_at', now(), 'partial_day', v_partial,
       'business_hours', case when v_open is null then null
                              else jsonb_build_object('open', v_open, 'close', v_close, 'overnight', v_overnight,
                                                      'working_days', to_jsonb(v_wdays)) end),
    'day_boundaries', v_bounds,
    'office', v_office,
    'coverage', v_coverage,
    'access_windows', v_episodes,
    'restricted', v_restricted,
    'after_hours_episodes', coalesce(v_after,0),
    'incidents', v_incidents,
    'attention', v_attn,
    'people', v_people,
    'honesty', (select coalesce(jsonb_agg(to_jsonb(x)), '[]'::jsonb) from (
        select 'Counts are camera detections, not a headcount of distinct people.'::text as x
        union all select case when v_partial then 'Partial day: figures cover midnight to report time only.' end
        union all select case when v_ratio < 1 then 'Monitoring had gaps in this window; some activity may be unobserved.' end
        union all select case when v_open is null then 'Business hours are not configured, so after-hours is not asserted.' end
        union all select 'Visitor/staff labels are movement-pattern inferences, not identities.'
      ) z where x is not null));
end $$;

revoke all on function public.wl_daily_intelligence(uuid,date,boolean) from public, anon, authenticated;
grant execute on function public.wl_daily_intelligence(uuid,date,boolean) to service_role;
