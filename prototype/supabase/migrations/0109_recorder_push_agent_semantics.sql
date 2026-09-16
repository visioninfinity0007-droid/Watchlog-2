-- =====================================================================
-- Recorder-push agent semantics (0.4.10).
--
-- 0013/0108 model a PC-free site as a VIRTUAL AGENT: a normal `agents` row with
-- device_driver = 'recorder-push', deliberately so that health, events, snapshots
-- and the portal treat a push site like any other site with no special cases.
--
-- That worked for ingest and breaks everywhere liveness or authority is inferred
-- from `agents.last_seen_at`, because a push agent is ALARM-DRIVEN. Nothing bumps
-- its last_seen_at except an actual alarm (wl_ingest_push). A quiet night is not a
-- fault, but to every heartbeat-shaped query it is indistinguishable from a dead PC.
--
-- Two concrete consequences, both found by audit before push was ever switched on:
--
--   1. AUTHORITY HIJACK. wl_current_site_agent (0088:37-47) picks the newest
--      last_seen_at. The instant push is provisioned the virtual agent is newest, so
--      it becomes the site's current agent. It has no nvr_health and no camera_health
--      rows, so wl_reconcile_site_faults sees "nothing wrong" and AUTO-RESOLVES every
--      open fault the real agent had raised. A recorder genuinely offline would be
--      silently marked healthy.
--
--   2. FALSE OFFLINE. wl_portal_overview's offline_agents (0009:110-116) and
--      v_agent_fleet's status ladder (0003:31-38) both classify by last_seen_at, so a
--      perfectly healthy PC-free site reports an offline agent minutes after its last
--      alarm and stays that way all night.
--
-- Fix: push agents are excluded from BOTH. Their liveness is push_sources.last_push_at
-- with alarm-driven semantics, exposed separately by wl_push_sources (0013:213) rather
-- than smuggled through a heartbeat threshold. A live lease still overrides authority,
-- so PC failover is unaffected.
--
-- NOTE the limitation this makes explicit, and it is a real product constraint:
-- a Dahua recorder alone cannot emit a PERIODIC heartbeat. A push-only site with no
-- alarms produces no writes at all, so WatchLog cannot distinguish "quiet" from
-- "unplugged". Recorder-push is a resilience layer over an agent, not a replacement
-- for one, and it must be sold that way.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Authority: a recorder-push agent may never become the site's current agent.
-- ---------------------------------------------------------------------
create or replace function public.wl_current_site_agent(p_site_id uuid)
returns uuid
language sql
stable
security definer
set search_path = public
as $$
  with site_cfg as (
    select coalesce(multi_agent_enabled, false) as multi_agent_enabled
      from public.sites where id = p_site_id
  ), live_lease as (
    select l.holder_agent_id
      from public.site_agent_leases l, site_cfg s
     where s.multi_agent_enabled
       and l.site_id = p_site_id
       and l.holder_agent_id is not null
       and l.lease_expires_at > now()
     limit 1
  ), newest as (
    select a.id
      from public.agents a
     where a.site_id = p_site_id
       -- A push agent's last_seen_at tracks ALARMS, not health. Letting it win here
       -- hands site authority to a row that can never report a fault, which then
       -- auto-resolves the real agent's open faults.
       and coalesce(a.device_driver, '') <> 'recorder-push'
     order by a.last_seen_at desc nulls last,
              a.enrolled_at desc nulls last,
              a.id desc
     limit 1
  )
  select coalesce((select holder_agent_id from live_lease),
                  (select id from newest));
$$;

comment on function public.wl_current_site_agent(uuid) is
  'Site authority agent. Excludes recorder-push virtual agents: their last_seen_at is '
  'alarm-driven, so they would take authority and auto-resolve real faults.';

-- ---------------------------------------------------------------------
-- 2. Fleet status: classify a push agent by its own semantics, never as offline.
--
-- CREATE OR REPLACE, deliberately NOT drop+create: the original (0003:17-18) carries
-- `with (security_invoker = true)` -- a security property -- and 0005_viewer_api.sql
-- selects from this view, so the column list and names must not move. Only the status
-- CASE expression changes.
-- ---------------------------------------------------------------------
create or replace view public.v_agent_fleet
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
    -- Alarm-driven by design: silence is not evidence of failure, so a push agent is
    -- never reported offline/stale. wl_push_sources exposes last_push_at instead.
    when coalesce(a.device_driver, '') = 'recorder-push'   then 'recorder push'
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
  'WatchLog prototype fleet health: one row per installed agent, its liveness and its '
  'latest event. Recorder-push virtual agents report status ''recorder push'' because '
  'their liveness is alarm-driven, not heartbeat-driven.';

-- ---------------------------------------------------------------------
-- 3. Portal overview: a PC-free site must not raise a false offline alert.
-- ---------------------------------------------------------------------
do $$
declare v_src text;
begin
  select pg_get_functiondef(p.oid) into v_src
    from pg_proc p join pg_namespace n on n.oid = p.pronamespace
   where n.nspname = 'public' and p.proname = 'wl_portal_overview'
   limit 1;
  if v_src is null then
    raise notice '0109: wl_portal_overview not found; skipping offline_agents patch';
    return;
  end if;
  if position('recorder-push' in v_src) > 0 then
    raise notice '0109: wl_portal_overview already excludes push agents';
    return;
  end if;
  -- Narrow, idempotent rewrite of the single offline_agents predicate.
  v_src := replace(
    v_src,
    'and (a.last_seen_at is null or a.last_seen_at < now() - interval ''30 minutes'')',
    'and coalesce(a.device_driver, '''') <> ''recorder-push'''
    || ' and (a.last_seen_at is null or a.last_seen_at < now() - interval ''30 minutes'')');
  execute v_src;
end $$;

-- ---------------------------------------------------------------------
-- 4. Liveness from recorder CHATTER, not just alarms.
--
-- A Dahua recorder cannot be made to emit a periodic heartbeat, so a push-only site
-- with a quiet night produces no writes at all and WatchLog cannot tell "quiet" from
-- "unplugged". That limitation is real and is not fixable from our side.
--
-- What IS recoverable: recorders do emit non-event chatter (Heartbeat, KeepAlive,
-- TimeChange, NTPAdjustTime). The bridge currently drops those on the floor. They are
-- not events and must never become events -- but they ARE proof the recorder is alive
-- and can reach us. This records exactly that and nothing more: it touches
-- push_sources.last_push_at and the virtual agent's last_seen_at, and inserts nothing.
--
-- Token-authenticated like wl_ingest_push, and deliberately returns no site data.
-- ---------------------------------------------------------------------
create or replace function public.wl_push_liveness(p_token text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_src push_sources;
begin
  select * into v_src from push_sources where token = p_token and enabled;
  if v_src.id is null then
    raise exception 'push token not recognised' using errcode = '28000';
  end if;

  update push_sources set last_push_at = now() where id = v_src.id;
  update agents set last_seen_at = now() where id = v_src.agent_id;

  return jsonb_build_object('ok', true, 'recorded_at', now());
end $$;

comment on function public.wl_push_liveness(text) is
  'Record that a recorder-push source is alive WITHOUT inserting an event. For recorder '
  'chatter (Heartbeat/KeepAlive/TimeChange) that proves reachability but is not an alarm.';

revoke all on function public.wl_push_liveness(text) from public;
grant execute on function public.wl_push_liveness(text) to anon, authenticated;
