-- =====================================================================
-- 0043 - Phase A increment 2: server-side agent watchdog + coverage math
--
-- Two server surfaces on top of the 0042 schema:
--
--   1. wl_agent_watchdog_sweep() — the SERVER derives AGENT_UNREACHABLE from
--      heartbeat gaps (the agent cannot report that it is offline). It opens an
--      agent_unreachable_intervals row when an agent goes silent past the
--      threshold, backdated to the last confirmed contact, and closes it when the
--      agent reports again. Idempotent (advisory-locked + not-exists guard + the
--      0042 partial-unique index): re-running never creates a duplicate. Cron-only,
--      like wl_enforce_retention (0014) — never reachable by a client.
--
--   2. wl_site_monitoring_coverage() — the read model's coverage/availability
--      denominator. Over a window it returns wall vs UNVERIFIED vs MONITORED time.
--
-- THE INVARIANT (design §9): a period the cloud could not observe reduces MONITORING
-- COVERAGE only. It is never camera downtime and never camera uptime. So
-- monitored_seconds = wall - unverified is the verified-availability denominator, and
-- unverified windows are excluded from both uptime and downtime. The exact rules are
-- specified and unit-tested in prototype/server/coverage_model.py; this SQL mirrors
-- them and test_agent_watchdog_contract.py pins the two together.
--
-- Cause is NOT guessed: an unreachable gap is recorded as 'agent_unreachable'. Whether
-- the PC was off or the internet dropped is reconciled later from local evidence
-- (increment 5), never fabricated by the server here.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Server watchdog sweep (cron-invoked; no client access).
-- ---------------------------------------------------------------------
create or replace function public.wl_agent_watchdog_sweep(p_threshold_seconds int default 180)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_now    timestamptz := now();
  -- floor the threshold so a bad argument can never make us flap every second
  v_thresh interval := make_interval(secs => greatest(coalesce(p_threshold_seconds, 180), 30));
  v_opened int := 0;
  v_closed int := 0;
begin
  -- Serialize sweeps: a manual run must not race the cron into duplicate intervals.
  perform pg_advisory_xact_lock(hashtext('wl_agent_watchdog_sweep'));

  -- CLOSE first: any open interval whose agent has reported since the gap began and
  -- is now within threshold. ended_at = the moment contact resumed (last_seen_at).
  with recovered as (
    update agent_unreachable_intervals u
       set ended_at = a.last_seen_at
      from agents a
     where u.agent_id = a.id
       and u.ended_at is null
       and a.last_seen_at is not null
       and a.last_seen_at > u.started_at
       and (v_now - a.last_seen_at) <= v_thresh
    returning 1
  )
  select count(*) into v_closed from recovered;

  -- OPEN: agents overdue past threshold with no currently-open interval. started_at is
  -- backdated to last_seen_at — everything after the last confirmed contact is unverified.
  with overdue as (
    select a.id, a.tenant_id, a.site_id, a.last_seen_at
      from agents a
     where a.last_seen_at is not null
       and (v_now - a.last_seen_at) > v_thresh
       and not exists (
         select 1 from agent_unreachable_intervals u
          where u.agent_id = a.id and u.ended_at is null)
  ),
  ins as (
    insert into agent_unreachable_intervals (tenant_id, site_id, agent_id, started_at)
    select tenant_id, site_id, id, last_seen_at from overdue
    returning 1
  )
  select count(*) into v_opened from ins;

  return jsonb_build_object(
    'ok', true, 'evaluated_at', v_now,
    'threshold_seconds', extract(epoch from v_thresh),
    'opened', v_opened, 'closed', v_closed);
end $$;

-- Cron / superuser only. Not the portal, not anon. (Same posture as wl_enforce_retention.)
revoke all on function public.wl_agent_watchdog_sweep(int) from public, anon, authenticated;

-- ---------------------------------------------------------------------
-- 2. Monitoring coverage read model (tenant-scoped; the availability denominator).
-- ---------------------------------------------------------------------
create or replace function public.wl_site_monitoring_coverage(
  p_site_id uuid,
  p_from    timestamptz,
  p_to      timestamptz
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_tenant     uuid := wl_my_tenant();
  v_lo         timestamptz := least(p_from, p_to);
  v_hi         timestamptz := greatest(p_from, p_to);
  v_wall       numeric;
  v_unverified numeric := 0;
begin
  if v_tenant is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  if not exists (select 1 from sites s where s.id = p_site_id and s.tenant_id = v_tenant) then
    raise exception 'that site does not belong to your account';
  end if;

  v_wall := greatest(0, extract(epoch from (v_hi - v_lo)));

  -- Union of every window the cloud could NOT verify the site, clipped to [lo,hi]:
  --   server-derived agent-unreachable intervals  UNION  reconciled unverified intervals.
  -- range_agg merges overlaps, so multiple agents/causes are never double-counted.
  with raw as (
    select tstzrange(greatest(started_at, v_lo),
                     least(coalesce(ended_at, v_hi), v_hi), '[)') as r
      from agent_unreachable_intervals
     where site_id = p_site_id and tenant_id = v_tenant
       and started_at < v_hi and coalesce(ended_at, v_hi) > v_lo
    union all
    select tstzrange(greatest(started_at, v_lo),
                     least(coalesce(ended_at, v_hi), v_hi), '[)')
      from unverified_intervals
     where site_id = p_site_id and tenant_id = v_tenant
       and started_at < v_hi and coalesce(ended_at, v_hi) > v_lo
  ),
  nonempty as (select r from raw where not isempty(r)),
  merged as (select range_agg(r) as mr from nonempty)
  select coalesce((
    select sum(extract(epoch from (upper(x) - lower(x))))
      from merged m, unnest(m.mr) as x
  ), 0) into v_unverified from merged;

  return jsonb_build_object(
    'site_id', p_site_id,
    'from', v_lo, 'to', v_hi,
    'wall_seconds', v_wall,
    'unverified_seconds', v_unverified,
    -- monitored_seconds is the verified-availability DENOMINATOR; unverified time is
    -- excluded from it and therefore counts as neither uptime nor downtime.
    'monitored_seconds', greatest(0, v_wall - v_unverified),
    'coverage_ratio', case when v_wall <= 0 then 1.0
                           else round((greatest(0, v_wall - v_unverified) / v_wall)::numeric, 6) end
  );
end $$;

revoke all on function public.wl_site_monitoring_coverage(uuid, timestamptz, timestamptz) from public, anon;
grant execute on function public.wl_site_monitoring_coverage(uuid, timestamptz, timestamptz) to authenticated;

-- ---------------------------------------------------------------------
-- Schedule the watchdog every minute via pg_cron, if available. Wrapped so the
-- migration still applies where pg_cron is not enabled (same guard as 0014).
-- ---------------------------------------------------------------------
do $$
begin
  if exists (select 1 from pg_available_extensions where name = 'pg_cron') then
    create extension if not exists pg_cron;
    perform cron.unschedule(jobid) from cron.job where jobname = 'watchlog-agent-watchdog';
    perform cron.schedule('watchlog-agent-watchdog', '* * * * *',
                          'select public.wl_agent_watchdog_sweep()');
  end if;
exception when others then
  raise notice 'pg_cron scheduling skipped: %', sqlerrm;
end $$;
