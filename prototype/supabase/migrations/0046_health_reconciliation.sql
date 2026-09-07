-- =====================================================================
-- 0046 - Phase A increment 5: local-health reconciliation after a cloud outage
--
-- Lets health monitoring survive an internet/cloud outage without fabricating camera
-- downtime. The agent persists observed health transitions + observation checkpoints in a
-- LOCAL durable store (prototype/agent/health_store.py) and, on reconnect, replays them here.
-- The reconciliation is idempotent, ordered by OBSERVED time, forward-only, and keeps two
-- coverages distinct: cloud connectivity vs local site monitoring.
--
-- The pure spec is prototype/server/reconcile_model.py; test_health_reconciliation_contract.py
-- pins this SQL to it.
--
-- Design choices encoded here:
--   * dedupe_key makes replay safe — a resent transition/checkpoint is ignored, so no
--     duplicate ledger row and no duplicate fault lifecycle.
--   * `at` on a transition is the DEVICE-OBSERVED time; `received_at` is when the cloud got
--     it — kept separate so reconstruction uses observation order, never upload arrival.
--   * current state advances FORWARD-ONLY by observed time (observed_at): a late/out-of-order
--     upload never regresses a fresher state.
--   * wl_report_camera_health becomes CURRENT-STATE-ONLY: the transition LEDGER is written
--     solely by reconciliation (with dedupe), so the live path and the replay path can never
--     double-write the same transition.
--   * checkpoints can prove a cloud-gap window was locally monitored (recovering availability)
--     while the window REMAINS a cloud connectivity gap. wl_site_local_monitoring reports both.
-- =====================================================================

-- ---- schema: dedupe / provenance / observed-time on the camera-health ledger ----
alter table public.camera_health_transitions add column if not exists dedupe_key  text;
alter table public.camera_health_transitions add column if not exists source      text;
alter table public.camera_health_transitions add column if not exists received_at timestamptz;
-- `at` keeps the RAW device-observed time (evidence/forensics). `effective_at` is the
-- server-safe ordering time used for the forward-only watermark — a wild future device clock
-- is clamped so it can never freeze current state. The two are deliberately distinct.
alter table public.camera_health_transitions add column if not exists effective_at timestamptz;
create unique index if not exists camera_health_tx_dedupe_uidx
  on public.camera_health_transitions (dedupe_key) where dedupe_key is not null;

-- observed-time watermark on current camera health, for forward-only advancement (holds the
-- server-safe EFFECTIVE time, never a raw future device clock).
alter table public.camera_health add column if not exists observed_at timestamptz;

-- Safe timestamptz cast: returns NULL instead of raising on a malformed value, so ONE poison
-- row in a retained batch can be isolated/quarantined rather than failing the whole batch
-- forever (which would spin the agent's reconcile invisibly).
create or replace function public.wl_try_timestamptz(p text)
returns timestamptz language plpgsql stable as $$   -- STABLE: text::timestamptz can depend on TimeZone
begin
  return p::timestamptz;
exception when others then
  return null;
end $$;

-- ---- local monitoring checkpoints (proof of local observation continuity) ----
-- Distinct from agent_unreachable_intervals (which is CLOUD connectivity): a checkpoint says
-- "the site was being watched locally at this instant", even while the cloud could not hear us.
create table if not exists public.local_monitoring_checkpoints (
  id               bigint generated always as identity primary key,
  tenant_id        uuid not null references public.tenants(id) on delete cascade,
  site_id          uuid not null references public.sites(id) on delete cascade,
  agent_id         uuid not null references public.agents(id) on delete cascade,
  checkpoint_id    text not null,                 -- "<agent>:<store_epoch>:cp:<seq>" — rebuild-proof
  store_epoch      text,                          -- new epoch after a local-store rebuild
  agent_seq        bigint not null,               -- per-DB monotonic id (forensics; NOT the dedupe key)
  device_ts        timestamptz not null,          -- observed time
  nvr_state        text not null default 'unknown',
  cameras_observed int  not null default 0,
  cycle_ok         boolean not null default true,
  received_at      timestamptz not null default now(),
  -- Dedupe on the epoch-qualified id, NOT (agent, seq): a corrupt-store rebuild restarts seq at
  -- 1, so bare seq would collide with already-reconciled rows and silently drop new evidence.
  unique (checkpoint_id)
);
create index if not exists local_mon_ckpt_site_idx on public.local_monitoring_checkpoints (site_id, device_ts);

alter table public.local_monitoring_checkpoints enable row level security;
revoke all on table public.local_monitoring_checkpoints from public, anon, authenticated;

-- =====================================================================
-- wl_reconcile_health — replay retained transitions + checkpoints (agent-authenticated).
-- =====================================================================
create or replace function public.wl_reconcile_health(
  p_agent_id     uuid,
  p_agent_key    text,
  p_transitions  jsonb,
  p_checkpoints  jsonb,
  p_max_future_skew_seconds int default 300
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent    agents;
  v_now      timestamptz := now();
  v_skew     interval := make_interval(secs => greatest(coalesce(p_max_future_skew_seconds,300), 0));
  v_seen     int := 0;   -- transitions received (all rows in payload)
  v_total    int := 0;   -- VALID camera transitions (bound + parseable)
  v_applied  int := 0;   -- newly inserted (deduped)
  v_rejected int := 0;   -- quarantined: unparseable ts / unmapped channel / missing id-state
  v_clamped  int := 0;   -- valid but future-skewed -> effective_at clamped to now
  v_ckpt     int := 0;
  v_ckpt_rej int := 0;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;

  select count(*) into v_seen from jsonb_array_elements(coalesce(p_transitions, '[]'::jsonb));

  -- LEDGER: append each transition once. `at` = RAW observed device time (evidence);
  -- `effective_at` = server-safe ordering time (clamps a wild future clock); received_at = now().
  -- Poison-safe: device_ts is parsed with wl_try_timestamptz (NULL on garbage) and such rows are
  -- QUARANTINED (counted), so one bad row never fails the batch. ON CONFLICT on the stable
  -- dedupe_key makes a replay a no-op. State/reason/source are clamped to their domains.
  with parsed as (
    select (t->>'id') as dedupe_key, cm.id as camera_id,
           case when lower(coalesce(t->>'from','')) in ('operational','degraded','offline','unknown')
                then lower(t->>'from') else 'unknown' end as from_state,
           case when lower(t->>'to') in ('operational','degraded','offline','unknown')
                then lower(t->>'to') else 'unknown' end as to_state,
           case when lower(coalesce(t->>'reason','unknown')) in ('ok','unknown','probe_timeout',
                     'stale_frame','video_loss','channel_missing','channel_disabled','nvr_unreachable',
                     'nvr_auth_failed','agent_unreachable','storage_fault','not_recording','tamper',
                     'disk_error','disk_full') then lower(t->>'reason') else 'unknown' end as reason,
           case when lower(coalesce(t->>'source','probe')) in ('native','probe','inventory','upper_layer')
                then lower(t->>'source') else 'probe' end as source,
           wl_try_timestamptz(t->>'device_ts') as device_ts       -- NULL on garbage (poison-safe)
      from jsonb_array_elements(coalesce(p_transitions, '[]'::jsonb)) t
      join cameras cm on cm.site_id = v_agent.site_id and cm.channel = (t->>'entity')
     where lower(coalesce(t->>'layer','camera')) = 'camera'
       and coalesce(t->>'to','') <> '' and coalesce(t->>'id','') <> ''
  ),
  tx as (
    -- only VALID rows (parseable ts); effective_at clamps a future-skewed clock to now.
    select dedupe_key, camera_id, from_state, to_state, reason, source, device_ts,
           case when device_ts > v_now + v_skew then v_now else device_ts end as effective_at,
           (device_ts > v_now + v_skew) as clamped
      from parsed where device_ts is not null
  ),
  ins as (
    insert into camera_health_transitions
      (tenant_id, site_id, camera_id, from_state, to_state, reason_code, at, effective_at,
       received_at, source, dedupe_key)
    select v_agent.tenant_id, v_agent.site_id, camera_id, from_state, to_state,
           reason, device_ts, effective_at, v_now, source, dedupe_key
      from tx
    on conflict (dedupe_key) where dedupe_key is not null do nothing
    returning 1
  )
  select (select count(*) from tx),
         (select count(*) from ins),
         (select count(*) from parsed where device_ts is null),
         (select count(*) from tx where clamped)
    into v_total, v_applied, v_rejected, v_clamped;

  -- CURRENT STATE: advance each camera to its latest transition by EFFECTIVE (server-safe)
  -- time, forward-only. Using effective_at (not raw device_ts) means a future device clock can
  -- never write a future watermark that freezes state; seq breaks ties deterministically.
  with parsed as (
    select cm.id as camera_id, (t->>'id') as dedupe_key,
           case when lower(t->>'to') in ('operational','degraded','offline','unknown')
                then lower(t->>'to') else 'unknown' end as to_state,
           case when lower(coalesce(t->>'reason','unknown')) in ('ok','unknown','probe_timeout',
                     'stale_frame','video_loss','channel_missing','channel_disabled','nvr_unreachable',
                     'nvr_auth_failed','agent_unreachable','storage_fault','not_recording','tamper',
                     'disk_error','disk_full') then lower(t->>'reason') else 'unknown' end as reason,
           wl_try_timestamptz(t->>'device_ts') as device_ts
      from jsonb_array_elements(coalesce(p_transitions, '[]'::jsonb)) t
      join cameras cm on cm.site_id = v_agent.site_id and cm.channel = (t->>'entity')
     where lower(coalesce(t->>'layer','camera')) = 'camera' and coalesce(t->>'to','') <> ''
  ),
  tx as (
    select camera_id, dedupe_key, to_state, reason,
           case when device_ts > v_now + v_skew then v_now else device_ts end as effective_at
      from parsed where device_ts is not null       -- quarantined rows never touch current state
  ),
  latest as (
    select distinct on (camera_id) camera_id, to_state, reason, effective_at
      from tx order by camera_id, effective_at desc, dedupe_key desc
  )
  update camera_health ch
     set health_state     = l.to_state,
         reason_code       = l.reason,
         observed_at       = l.effective_at,
         last_change_at    = v_now,
         updated_at        = v_now,
         last_offline_at   = case when l.to_state = 'offline' then l.effective_at
                                  else ch.last_offline_at end,
         last_recovery_at  = case when l.to_state = 'operational' and ch.health_state = 'offline'
                                  then l.effective_at else ch.last_recovery_at end
    from latest l
   where ch.camera_id = l.camera_id
     and l.effective_at > coalesce(ch.observed_at, '-infinity'::timestamptz);   -- forward-only

  -- CHECKPOINTS: local observation continuity, deduped on the epoch-qualified checkpoint_id.
  -- Poison-safe: a checkpoint with an unparseable device_ts is quarantined (counted), not fatal.
  with parsed as (
    select (c->>'id') as checkpoint_id, (c->>'store_epoch') as store_epoch,
           coalesce((c->>'seq')::bigint, 0) as agent_seq,
           wl_try_timestamptz(c->>'device_ts') as device_ts,
           lower(coalesce(c->>'nvr_state','unknown')) as nvr_state,
           coalesce((c->>'cameras_observed')::int, 0) as cameras_observed,
           coalesce((c->>'cycle_ok')::boolean, true) as cycle_ok
      from jsonb_array_elements(coalesce(p_checkpoints, '[]'::jsonb)) c
     where coalesce(c->>'id','') <> ''
  ),
  insc as (
    insert into local_monitoring_checkpoints
      (tenant_id, site_id, agent_id, checkpoint_id, store_epoch, agent_seq, device_ts,
       nvr_state, cameras_observed, cycle_ok)
    select v_agent.tenant_id, v_agent.site_id, v_agent.id, checkpoint_id, store_epoch,
           agent_seq, device_ts, nvr_state, cameras_observed, cycle_ok
      from parsed where device_ts is not null
    on conflict (checkpoint_id) do nothing            -- epoch-qualified: rebuild-proof idempotency
    returning 1
  )
  select (select count(*) from insc), (select count(*) from parsed where device_ts is null)
    into v_ckpt, v_ckpt_rej;

  update agents set last_seen_at = v_now where id = v_agent.id;

  return jsonb_build_object(
    'ok', true,
    'transitions_received', v_seen,
    'transitions_valid', v_total,
    'transitions_applied', v_applied,
    'transitions_duplicate', v_total - v_applied,   -- ignored as already-present (idempotent)
    'transitions_rejected', v_rejected,             -- quarantined (bad ts / unmapped) — observable
    'transitions_clamped', v_clamped,               -- future-skew clamped for ordering — observable
    'checkpoints_applied', v_ckpt,
    'checkpoints_rejected', v_ckpt_rej,             -- quarantined — observable
    'server_time', v_now);
end $$;

revoke all on function public.wl_reconcile_health(uuid, text, jsonb, jsonb, int) from public;
grant execute on function public.wl_reconcile_health(uuid, text, jsonb, jsonb, int) to anon, authenticated;

-- =====================================================================
-- wl_report_camera_health — REPLACED: current-state only (no ledger write). The transition
-- ledger is now owned exclusively by wl_reconcile_health, so the live path and the replay
-- path can never double-write. Also stamps observed_at so reconciliation stays forward-only.
-- =====================================================================
create or replace function public.wl_report_camera_health(
  p_agent_id  uuid,
  p_agent_key text,
  p_report    jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent agents;
  v_now   timestamptz := now();
  v_op int := 0; v_deg int := 0; v_off int := 0; v_unk int := 0;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;

  with rep as (
    select (c->>'channel') as channel,
           lower(coalesce(c->>'health', 'unknown')) as health,
           lower(coalesce(c->>'reason', 'unknown')) as reason
      from jsonb_array_elements(coalesce(p_report->'cameras', '[]'::jsonb)) c
     where coalesce(c->>'channel', '') <> ''
  ),
  mapped as (
    select cm.id as camera_id,
           case when r.health in ('operational','degraded','offline','unknown')
                then r.health else 'unknown' end as health,
           case when r.reason in ('ok','unknown','probe_timeout','stale_frame','video_loss',
                                  'channel_missing','channel_disabled','nvr_unreachable',
                                  'nvr_auth_failed','agent_unreachable','storage_fault',
                                  'not_recording','tamper','disk_error','disk_full')
                then r.reason else 'unknown' end as reason
      from rep r
      join cameras cm on cm.site_id = v_agent.site_id and cm.channel = r.channel
  ),
  up as (
    -- current snapshot only; forward-only by observed time so it never regresses a state a
    -- later-reconciled offline period already advanced. NO transition rows here.
    insert into camera_health as chh (camera_id, tenant_id, site_id, health_state, reason_code,
                                      last_probe_at, last_ok_at, last_change_at, observed_at,
                                      last_offline_at, last_recovery_at, updated_at)
    select m.camera_id, v_agent.tenant_id, v_agent.site_id, m.health, m.reason,
           v_now,
           case when m.health = 'operational' then v_now else null end,
           v_now, v_now,
           case when m.health = 'offline' then v_now else null end,
           case when m.health = 'operational' then v_now else null end,
           v_now
      from mapped m
    on conflict (camera_id) do update
       set health_state    = excluded.health_state,
           reason_code      = excluded.reason_code,
           last_probe_at    = v_now,
           last_ok_at       = case when excluded.health_state = 'operational' then v_now
                                   else chh.last_ok_at end,
           last_change_at   = case when chh.health_state is distinct from excluded.health_state
                                   then v_now else chh.last_change_at end,
           observed_at      = v_now,
           last_offline_at  = case when excluded.health_state = 'offline'
                                     and chh.health_state is distinct from 'offline'
                                   then v_now else chh.last_offline_at end,
           last_recovery_at = case when excluded.health_state = 'operational'
                                     and chh.health_state = 'offline'
                                   then v_now else chh.last_recovery_at end,
           updated_at       = v_now
       where v_now >= coalesce(chh.observed_at, '-infinity'::timestamptz)   -- forward-only
    returning 1
  )
  select count(*) filter (where m.health = 'operational'),
         count(*) filter (where m.health = 'degraded'),
         count(*) filter (where m.health = 'offline'),
         count(*) filter (where m.health = 'unknown')
    into v_op, v_deg, v_off, v_unk
    from mapped m;

  update agents set last_seen_at = v_now where id = v_agent.id;

  return jsonb_build_object(
    'ok', true, 'operational', v_op, 'degraded', v_deg, 'offline', v_off,
    'unknown', v_unk, 'server_time', v_now);
end $$;

revoke all on function public.wl_report_camera_health(uuid, text, jsonb) from public;
grant execute on function public.wl_report_camera_health(uuid, text, jsonb) to anon, authenticated;

-- =====================================================================
-- wl_site_local_monitoring — reclassify a window's CLOUD gaps by LOCAL checkpoint evidence.
-- Keeps the two coverages distinct: cloud connectivity gap vs the part of it we can prove was
-- locally monitored vs the part that stays genuinely unverified (e.g. PC was off).
-- =====================================================================
create or replace function public.wl_site_local_monitoring(
  p_site_id uuid,
  p_from    timestamptz,
  p_to      timestamptz,
  p_max_gap_seconds int default 900
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_tenant       uuid := wl_my_tenant();
  v_lo           timestamptz := least(p_from, p_to);
  v_hi           timestamptz := greatest(p_from, p_to);
  v_gap          numeric := 0;
  v_local        numeric := 0;
  v_max_gap      interval := make_interval(secs => greatest(coalesce(p_max_gap_seconds,900), 1));
begin
  if v_tenant is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  if not exists (select 1 from sites s where s.id = p_site_id and s.tenant_id = v_tenant) then
    raise exception 'that site does not belong to your account';
  end if;

  -- cloud connectivity gaps in the window (server-derived agent-unreachable), as a multirange
  with cloud as (
    select coalesce(range_agg(tstzrange(greatest(started_at, v_lo),
                                        least(coalesce(ended_at, v_hi), v_hi), '[)')),
                    '{}'::tstzmultirange) as mr
      from agent_unreachable_intervals
     where site_id = p_site_id and tenant_id = v_tenant
       and started_at < v_hi and coalesce(ended_at, v_hi) > v_lo
  ),
  -- checkpoint-derived local monitoring intervals: bridge consecutive checkpoints within max_gap
  cp as (
    select device_ts,
           lead(device_ts) over (order by device_ts) as nxt
      from local_monitoring_checkpoints
     where site_id = p_site_id and tenant_id = v_tenant
       and device_ts between v_lo and v_hi
  ),
  cpr as (
    select tstzrange(device_ts,
                     case when nxt is not null and nxt - device_ts <= v_max_gap then nxt
                          else device_ts end, '[]') as r
      from cp
  ),
  localmon as (
    select coalesce(range_agg(r), '{}'::tstzmultirange) as mr from cpr where not isempty(r)
  )
  select
    coalesce((select sum(extract(epoch from (upper(x) - lower(x))))
                from cloud c, unnest(c.mr) x), 0),
    coalesce((select sum(extract(epoch from (upper(y) - lower(y))))
                from cloud c, localmon l, unnest(c.mr * l.mr) y), 0)
    into v_gap, v_local;

  return jsonb_build_object(
    'site_id', p_site_id, 'from', v_lo, 'to', v_hi,
    -- these three are DISTINCT concepts, deliberately:
    'cloud_gap_seconds', v_gap,                              -- cloud could not receive
    'locally_monitored_seconds', v_local,                   -- but local checkpoints prove we watched
    'still_unverified_seconds', greatest(0, v_gap - v_local) -- genuinely unobserved (e.g. PC off)
  );
end $$;

revoke all on function public.wl_site_local_monitoring(uuid, timestamptz, timestamptz, int) from public, anon;
grant execute on function public.wl_site_local_monitoring(uuid, timestamptz, timestamptz, int) to authenticated;
