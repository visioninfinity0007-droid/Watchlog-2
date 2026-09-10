-- =====================================================================
-- 0067 — Canonical Daily Intelligence dataset (ONE source for Portal / PDF / WhatsApp).
--
-- The daily client report is rendered three ways (portal dashboard, branded PDF, a
-- WhatsApp summary). If each computed its own numbers they would drift and the client
-- would catch us contradicting ourselves. So there is exactly ONE server-side dataset,
-- wl_daily_intelligence(site, date), and every surface renders from it.
--
-- It COMPOSES the layers already built rather than recomputing:
--   * office    — wl_office_brief (0060): opening/closing, per-area access, restricted,
--                 after-hours, peak hour, agent liveness (events-based, always live).
--   * coverage  — wl_site_coverage_report (0062): monitored vs unverified seconds, gaps.
--   * access_windows / incidents — the derived intelligence pipeline (0065): episodes and
--                 per-site-promoted incidents, with provenance and severity.
--
-- p_derive (default true) refreshes the 0065 derived layers for the day first (all three
-- derivations are idempotent) so a consumer always gets current incidents from one call.
--
-- Exposure mirrors wl_daily_report: the raw builder is service_role-only (the report-runner
-- backend). Portal users go through wl_my_daily_intelligence, which is tenant-guarded via
-- wl_my_tenant() so one tenant can never read another's site.
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
  v_site      sites;
  v_date      date;
  v_start     timestamptz;
  v_end       timestamptz;
  v_to        timestamptz;
  v_partial   boolean;
  v_office    jsonb;
  v_coverage  jsonb;
  v_episodes  jsonb;
  v_incidents jsonb;
  v_attn      jsonb;
begin
  select * into v_site from sites where id = p_site_id;
  if v_site.id is null then
    raise exception 'no such site' using errcode = '22023';
  end if;

  v_date  := coalesce(p_date, (now() at time zone v_site.timezone)::date);
  v_start := (v_date::text || ' 00:00:00')::timestamp at time zone v_site.timezone;
  v_end   := v_start + interval '1 day';
  v_partial := v_end > now();
  -- Coverage window: full day for a past date, midnight..now for today (so the ratio
  -- reflects elapsed time, not a 24h day that hasn't happened yet).
  v_to := least(v_end, greatest(v_start, now()));

  if p_derive then
    perform wl_derive_activities(p_site_id, v_start, v_end);
    perform wl_derive_episodes(p_site_id, v_start, v_end);
    perform wl_promote_incidents(p_site_id, v_start, v_end);
  end if;

  v_office   := wl_office_brief(p_site_id, v_date);
  v_coverage := wl_site_coverage_report(p_site_id, v_start, v_to);

  select coalesce(jsonb_agg(jsonb_build_object(
           'camera',        coalesce(c.name, 'unassigned'),
           'purpose',       c.purpose,
           'type',          ep.episode_type,
           'object_class',  ep.object_class,
           'start',         to_char(ep.started_at at time zone v_site.timezone, 'HH24:MI'),
           'end',           to_char(ep.ended_at   at time zone v_site.timezone, 'HH24:MI'),
           'dwell_seconds', ep.dwell_seconds,
           'detections',    ep.detection_count) order by ep.started_at), '[]'::jsonb)
    into v_episodes
    from episodes ep
    left join cameras c on c.id = ep.camera_id
   where ep.site_id = p_site_id and ep.started_at >= v_start and ep.started_at < v_end;

  select coalesce(jsonb_agg(jsonb_build_object(
           'type',     ii.incident_type,
           'severity', ii.severity,
           'camera',   coalesce(c.name, 'unassigned'),
           'purpose',  c.purpose,
           'time',     to_char(ii.occurred_at at time zone v_site.timezone, 'HH24:MI'),
           'status',   ii.status,
           'policy',   ii.detail_json->>'policy',
           'detail',   ii.detail_json)
           order by case ii.severity when 'critical' then 0 when 'warning' then 1 else 2 end,
                    ii.occurred_at), '[]'::jsonb)
    into v_incidents
    from intel_incidents ii
    left join cameras c on c.id = ii.camera_id
   where ii.site_id = p_site_id and ii.occurred_at >= v_start and ii.occurred_at < v_end;

  select jsonb_build_object(
           'incidents_total', count(*),
           'critical', count(*) filter (where severity = 'critical'),
           'warning',  count(*) filter (where severity = 'warning'),
           'info',     count(*) filter (where severity = 'info'))
    into v_attn
    from intel_incidents
   where site_id = p_site_id and occurred_at >= v_start and occurred_at < v_end;

  return jsonb_build_object(
    'schema', 'daily_intelligence.v1',
    'meta', jsonb_build_object(
       'site', v_site.name, 'site_id', v_site.id, 'site_type', v_site.site_type,
       'date', v_date, 'timezone', v_site.timezone,
       'generated_at', now(), 'partial_day', v_partial),
    'office', v_office,
    'coverage', v_coverage,
    'access_windows', v_episodes,
    'incidents', v_incidents,
    'attention', v_attn,
    -- Honesty caveats, nulls stripped, so the report never over-claims.
    'honesty', (select coalesce(jsonb_agg(to_jsonb(x)), '[]'::jsonb) from (
        select 'Counts are camera detections, not a headcount of distinct people.'::text as x
        union all
        select case when v_partial
                    then 'Partial day: figures cover midnight to report time only.' end
        union all
        select case when coalesce((v_coverage->>'coverage_ratio')::numeric, 1) < 1
                    then 'Monitoring had gaps in this window; some activity may be unobserved.' end
      ) z where x is not null)
  );
end $$;

revoke all on function public.wl_daily_intelligence(uuid,date,boolean) from public, anon, authenticated;
grant execute on function public.wl_daily_intelligence(uuid,date,boolean) to service_role;

-- ---------------------------------------------------------------------
-- Portal entry point: tenant-guarded. Never accepts a site outside the caller's tenant.
-- ---------------------------------------------------------------------
create or replace function public.wl_my_daily_intelligence(
  p_site_id uuid,
  p_date    date default null
) returns jsonb
language plpgsql
volatile
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
  return wl_daily_intelligence(p_site_id, p_date, true);
end $$;

revoke all on function public.wl_my_daily_intelligence(uuid,date) from public, anon;
grant execute on function public.wl_my_daily_intelligence(uuid,date) to authenticated, service_role;
