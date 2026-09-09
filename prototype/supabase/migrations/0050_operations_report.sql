-- ===================================================================
-- 0050 — Management Reporting: ONE tenant-scoped Executive Operations Report
-- combining Reliability + Security + Operations for a site over a period.
--
-- Completeness is a first-class citizen: availability is reported over MONITORED
-- time, and unverified/UNKNOWN time is surfaced explicitly and NEVER converted
-- into false compliance or false downtime. Every metric carries drill-down IDs
-- (metric -> rule/fault -> incident/event -> timestamp -> camera -> evidence).
--
-- Read-only (stable). Sources already exist: monitoring_coverage / *_intervals
-- (0042), camera_health / nvr_health / operational_faults (0042-0048),
-- operations_incidents (0049), events (native/security), incident_clip_requests
-- (0040 evidence). This migration ADDS no tables — it is a pure read model.
-- ===================================================================

create or replace function public.wl_operations_report(
  p_site_id uuid,
  p_from    timestamptz default now() - interval '7 days',
  p_to      timestamptz default now()
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_my_tenant();
  v_site   public.sites;
  v_cov    jsonb;
  v_rel    jsonb;
  v_sec    jsonb;
  v_ops    jsonb;
begin
  if v_tenant is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  select * into v_site from public.sites where id = p_site_id and tenant_id = v_tenant;
  if v_site.id is null then
    raise exception 'site not in your account' using errcode = '42501';
  end if;

  -- ---- completeness: coverage over MONITORED time; unverified excluded ------
  select jsonb_build_object(
    'wall_seconds', coalesce(sum(wall_seconds), 0),
    'monitored_seconds', coalesce(sum(monitored_seconds), 0),
    'unverified_seconds', coalesce(sum(unverified_seconds), 0),
    'coverage_pct', case when coalesce(sum(wall_seconds), 0) = 0 then null
        else round(100.0 * sum(monitored_seconds) / nullif(sum(wall_seconds), 0), 1) end,
    'unverified_pct', case when coalesce(sum(wall_seconds), 0) = 0 then null
        else round(100.0 * sum(unverified_seconds) / nullif(sum(wall_seconds), 0), 1) end,
    'note', 'Availability is measured over MONITORED time; unverified/UNKNOWN periods '
            || 'are surfaced, not counted as uptime or downtime.'
  ) into v_cov
  from public.monitoring_coverage
  where site_id = p_site_id
    and bucket_date between (p_from at time zone 'UTC')::date and (p_to at time zone 'UTC')::date;

  -- ---- reliability ----------------------------------------------------------
  with agent_un as (
    select id, started_at, coalesce(ended_at, now()) as endd
      from public.agent_unreachable_intervals
     where site_id = p_site_id and started_at < p_to and coalesce(ended_at, now()) > p_from
  ),
  unv as (
    select id, started_at, coalesce(ended_at, now()) as endd
      from public.unverified_intervals
     where site_id = p_site_id and started_at < p_to and coalesce(ended_at, now()) > p_from
  ),
  cam as (
    select count(*) as total,
           count(*) filter (where health_state = 'offline') as offline_now,
           count(*) filter (where health_state = 'unknown') as unknown_now
      from public.camera_health where site_id = p_site_id
  ),
  camf as (
    select count(*) as offline_faults,
           coalesce(jsonb_agg(id order by opened_at desc), '[]'::jsonb) as drill
      from public.operational_faults
     where site_id = p_site_id and fault_domain = 'camera' and opened_at between p_from and p_to
  )
  select jsonb_build_object(
    'agent_unreachable', jsonb_build_object(
       'intervals', (select count(*) from agent_un),
       'seconds', (select coalesce(round(sum(extract(epoch from (least(endd, p_to) - greatest(started_at, p_from)))))::bigint, 0) from agent_un),
       'drill', (select coalesce(jsonb_agg(id), '[]'::jsonb) from agent_un)),
    'recorders', (select coalesce(jsonb_agg(jsonb_build_object(
       'agent_id', agent_id, 'reachable', nvr_reachable, 'auth_ok', nvr_auth_ok,
       'recording_state', recording_state, 'storage_state', storage_state)), '[]'::jsonb)
       from public.nvr_health where site_id = p_site_id),
    'cameras', jsonb_build_object(
       'total', (select total from cam), 'offline_now', (select offline_now from cam),
       'unknown_now', (select unknown_now from cam),
       'offline_faults_opened', (select offline_faults from camf), 'drill', (select drill from camf)),
    'unverified', jsonb_build_object(
       'intervals', (select count(*) from unv),
       'seconds', (select coalesce(round(sum(extract(epoch from (least(endd, p_to) - greatest(started_at, p_from)))))::bigint, 0) from unv))
  ) into v_rel;

  -- ---- security -------------------------------------------------------------
  with inc as (
    select * from public.operations_incidents
     where site_id = p_site_id and opened_at between p_from and p_to
  ),
  ev as (
    select event_type, count(*) c from public.events
     where site_id = p_site_id and received_at between p_from and p_to group by event_type
  ),
  clip as (
    select status, count(*) c from public.incident_clip_requests
     where site_id = p_site_id and requested_at between p_from and p_to group by status
  )
  select jsonb_build_object(
    'incidents', jsonb_build_object(
       'total', (select count(*) from inc),
       'by_status', (select coalesce(jsonb_object_agg(status, c), '{}'::jsonb)
                     from (select status, count(*) c from inc group by status) s),
       'by_severity', (select coalesce(jsonb_object_agg(severity, c), '{}'::jsonb)
                       from (select severity, count(*) c from inc group by severity) s),
       'review_required', (select count(*) from inc where review_required),
       'drill', (select coalesce(jsonb_agg(jsonb_build_object(
                   'id', id, 'type', incident_type, 'severity', severity, 'status', status,
                   'camera_id', camera_id, 'occurred_at', occurred_at,
                   'rule_id', rule_id, 'rule_version', rule_version) order by opened_at desc), '[]'::jsonb)
                 from inc)),
    'native_events', jsonb_build_object(
       'total', (select coalesce(sum(c), 0) from ev),
       'by_type', (select coalesce(jsonb_object_agg(event_type, c), '{}'::jsonb) from ev)),
    'evidence', jsonb_build_object(
       'clip_requests', (select coalesce(sum(c), 0) from clip),
       'by_status', (select coalesce(jsonb_object_agg(status, c), '{}'::jsonb) from clip))
  ) into v_sec;

  -- ---- operations (SOP violations by primitive) -----------------------------
  with inc as (
    select incident_type, id, opened_at from public.operations_incidents
     where site_id = p_site_id and opened_at between p_from and p_to
  )
  select jsonb_build_object(
    'sop_violations', jsonb_build_object(
       'total', (select count(*) from inc),
       'by_type', (select coalesce(jsonb_object_agg(incident_type, c), '{}'::jsonb)
                   from (select incident_type, count(*) c from inc group by incident_type) s),
       'drill', (select coalesce(jsonb_agg(id order by opened_at desc), '[]'::jsonb) from inc)),
    'dwell_wait', (select count(*) from inc where incident_type in ('zone_dwell', 'queue_wait')),
    'presence_absence', (select count(*) from inc where incident_type in ('zone_presence', 'zone_absence')),
    'occupancy', (select count(*) from inc where incident_type = 'occupancy'),
    'schedule', (select count(*) from inc where incident_type = 'schedule_activity')
  ) into v_ops;

  return jsonb_build_object(
    'site', jsonb_build_object('id', v_site.id, 'name', v_site.name),
    'period', jsonb_build_object('from', p_from, 'to', p_to),
    'completeness', coalesce(v_cov, jsonb_build_object(
       'wall_seconds', 0, 'monitored_seconds', 0, 'unverified_seconds', 0,
       'coverage_pct', null, 'unverified_pct', null,
       'note', 'No monitoring-coverage data recorded for this period; completeness is UNKNOWN.')),
    'reliability', v_rel,
    'security', v_sec,
    'operations', v_ops
  );
end $$;

revoke all on function public.wl_operations_report(uuid, timestamptz, timestamptz) from public, anon;
grant execute on function public.wl_operations_report(uuid, timestamptz, timestamptz) to authenticated;
