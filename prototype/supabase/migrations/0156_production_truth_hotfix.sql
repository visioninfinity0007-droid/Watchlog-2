-- 0156 - Production-truth hotfix (standalone; needs explicit approval before it is
-- applied to production; see docs/production/MIGRATION_LEDGER_RECONCILIATION_2026-10-05.md).
--
-- Numbers 0146-0155 belong to the in-flight multi-recorder chain (mr/db-contracts).
-- None of those files redefines an object below, so applying 0156 after that chain
-- never reverts it.
--
-- 1. wl_known_capabilities(): production's body (read with pg_get_functiondef on
--    2026-10-05) lacks 'config_snapshot_requests'. wl_agent_report_capabilities
--    keeps only known capabilities, so it stripped the one capability the 0125
--    restaurant scheduler requires, and wl_restaurant_schedule_snapshot_requests
--    could never select an Agent. The body below is the production body with that
--    one element appended; search_path and grants are unchanged (CREATE OR REPLACE
--    keeps the existing ACL).
--
-- 2. wl_agent_semver_triplet(text): 0119's regex literal '\\.' (two backslashes
--    under standard_conforming_strings=on) never matches, so a fresh chain parsed
--    every version as {0,0,0} and dropped site_control_runtime / remote_update_v1.
--    Production runs the single-backslash body; this redefinition is that body
--    exactly. No production behaviour changes.
--
-- 3. camera_snapshot_requests had no expiry: a request no Agent completed stayed
--    open forever, blocked new requests for that camera (one open request per
--    camera) and stayed at the head of the Agent's poll (oldest first, two per
--    poll). wl_expire_stale_snapshot_requests closes such requests after a bounded
--    window and records expired_at, so an expired request is never read as a
--    delivered image. Server-side only, in the same style as 0142_stale.

-- ---------------------------------------------------------------------------
-- 1. Known capabilities: production body + 'config_snapshot_requests'
-- ---------------------------------------------------------------------------
create or replace function public.wl_known_capabilities()
returns text[] language sql immutable
set search_path = public
as $$
  select array[
    'operations_runtime',
    'operations_extended_primitives',
    'operations_evidence_still',
    'operations_evidence_clip',
    'archive_processing',
    'multi_agent_fencing',
    'recorder_probe_v2',
    'site_control_runtime',
    'remote_update_v1',
    'config_snapshot_requests'
  ]
$$;

-- ---------------------------------------------------------------------------
-- 2. Semver triplet: the production body exactly
-- ---------------------------------------------------------------------------
create or replace function public.wl_agent_semver_triplet(p_version text)
returns int[]
language plpgsql
immutable
set search_path = public
as $$
declare
  m text[];
begin
  m := regexp_match(coalesce(p_version,''), '^([0-9]+)\.([0-9]+)\.([0-9]+)');
  if m is null then return array[0,0,0]; end if;
  return array[m[1]::int,m[2]::int,m[3]::int];
end
$$;

-- ---------------------------------------------------------------------------
-- 3. Bounded expiry of snapshot requests no Agent completed
-- ---------------------------------------------------------------------------
alter table public.camera_snapshot_requests
  add column if not exists expired_at timestamptz null;

comment on column public.camera_snapshot_requests.expired_at is
  'WatchLog 0156: set (with completed_at) when the request was closed because no Agent delivered an image within the expiry window. Null for delivered requests.';

create or replace function public.wl_expire_stale_snapshot_requests(
  p_manual_max_age_minutes integer default 1440,
  p_analytics_max_age_minutes integer default 30
) returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_manual integer := least(greatest(coalesce(p_manual_max_age_minutes, 1440), 60), 10080);
  v_analytics integer := least(greatest(coalesce(p_analytics_max_age_minutes, 30), 5), 1440);
  v_now timestamptz := now();
  v_count integer := 0;
begin
  with stale as (
    select q.id
      from public.camera_snapshot_requests q
     where q.completed_at is null
       and q.requested_at < v_now - make_interval(mins => case
             when q.request_source = 'restaurant_analytics' then v_analytics
             else v_manual
           end)
     for update skip locked
  ), expired as (
    update public.camera_snapshot_requests q
       set completed_at = v_now,
           expired_at = v_now
      from stale s
     where q.id = s.id
    returning q.id
  )
  select count(*) into v_count from expired;

  return v_count;
end
$$;

comment on function public.wl_expire_stale_snapshot_requests(integer, integer) is
  'WatchLog 0156: close snapshot requests no Agent completed (manual after 24h, restaurant analytics after 30 min by default) and mark them expired';

revoke all on function public.wl_expire_stale_snapshot_requests(integer, integer)
  from public, anon, authenticated;
grant execute on function public.wl_expire_stale_snapshot_requests(integer, integer)
  to service_role;

-- Independent server-side cleanup: the Agent that should complete a request may be
-- the component that is offline or failing.
do $$
begin
  if exists (select 1 from pg_available_extensions where name = 'pg_cron') then
    create extension if not exists pg_cron;
  end if;
  if to_regclass('cron.job') is not null then
    if exists (select 1 from cron.job where jobname = 'watchlog-expire-stale-snapshot-requests') then
      perform cron.unschedule('watchlog-expire-stale-snapshot-requests');
    end if;
    perform cron.schedule(
      'watchlog-expire-stale-snapshot-requests',
      '*/5 * * * *',
      'select public.wl_expire_stale_snapshot_requests(1440, 30)'
    );
  end if;
exception when others then
  raise notice 'stale snapshot request expiry scheduling skipped: %', sqlerrm;
end $$;
