-- =====================================================================
-- WatchLog prototype — Phase 5 view
-- =====================================================================
-- Answers "is site X alive right now, and what was its last event"
-- without opening a terminal. Open it in the Supabase table editor, or
-- read it from the static HTML viewer.
--
-- security_invoker = true so the view respects the caller's RLS instead
-- of silently running with the owner's rights. The service role still
-- bypasses; anon needs the policies in 0004.
-- =====================================================================

-- Dropped rather than replaced: create-or-replace cannot insert columns
-- in the middle of an existing view's column list.
drop view if exists v_agent_fleet;

create view v_agent_fleet
with (security_invoker = true) as
select
  a.id                as agent_id,
  t.name              as tenant,
  s.name              as site,
  a.hostname,
  a.platform,
  a.agent_version,
  a.device_vendor,
  a.device_model,
  a.device_driver,
  a.enrolled_at,
  a.last_seen_at,
  case
    when a.last_seen_at is null                            then 'never checked in'
    when a.last_seen_at > now() - interval '3 minutes'     then 'online'
    when a.last_seen_at > now() - interval '30 minutes'    then 'stale'
    else                                                        'offline'
  end                 as status,
  now() - a.last_seen_at as since_last_seen,
  ev.last_event_at,
  ev.last_event_type,
  ev.last_event_camera,
  ev.event_count
from agents a
join tenants t on t.id = a.tenant_id
join sites   s on s.id = a.site_id
left join lateral (
  select
    max(e.device_ts) as last_event_at,
    count(*)         as event_count,
    (array_agg(e.event_type order by e.device_ts desc))[1] as last_event_type,
    (array_agg(c.name  order by e.device_ts desc))[1]      as last_event_camera
  from events e
  left join cameras c on c.id = e.camera_id
  where e.agent_id = a.id
) ev on true
order by a.last_seen_at desc nulls last;

comment on view v_agent_fleet is
  'WatchLog prototype fleet health: one row per installed agent, its liveness and its latest event.';
