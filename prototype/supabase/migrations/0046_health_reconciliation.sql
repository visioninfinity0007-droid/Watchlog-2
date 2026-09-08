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
-- server-safe ordering time — a wild future clock is clamped so it can never freeze state. It is
-- set ONCE, when the dedupe id is first inserted, and is IMMUTABLE thereafter (a replay's ON
-- CONFLICT DO NOTHING preserves it), so a resend can never re-clamp it against a later now().
alter table public.camera_health_transitions add column if not exists effective_at timestamptz;
-- The transition's ORDERING identity, stored explicitly so current-state can advance durably and
-- deterministically ACROSS batches: store_epoch + numeric seq. The ledger's own `id` (identity,
-- from 0042) is the server-assigned first-seen order used only to break an equal-time cross-epoch
-- tie (random-UUID epochs never imply chronology).
alter table public.camera_health_transitions add column if not exists store_epoch text;
alter table public.camera_health_transitions add column if not exists seq         bigint;
create unique index if not exists camera_health_tx_dedupe_uidx
  on public.camera_health_transitions (dedupe_key) where dedupe_key is not null;

-- DURABLE ordering watermark on current camera health (not just a timestamp): the winning
-- transition's effective time + epoch + numeric seq + server first-seen id. This is what makes
-- cross-batch ordering correct and replay a no-op.
alter table public.camera_health add column if not exists observed_at     timestamptz;
alter table public.camera_health add column if not exists observed_epoch  text;
alter table public.camera_health add column if not exists observed_seq    bigint;
alter table public.camera_health add column if not exists observed_ingest bigint;

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

-- Fail-soft casts for the OTHER untrusted typed payload fields, so a malformed seq /
-- cameras_observed / cycle_ok cannot abort the whole reconcile either. NULL on garbage.
create or replace function public.wl_try_bigint(p text)
returns bigint language plpgsql immutable as $$
begin return p::bigint; exception when others then return null; end $$;

create or replace function public.wl_try_int(p text)
returns int language plpgsql immutable as $$
begin return p::int; exception when others then return null; end $$;

create or replace function public.wl_try_bool(p text)
returns boolean language plpgsql immutable as $$
begin return p::boolean; exception when others then return null; end $$;

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
  v_agent      agents;
  v_now        timestamptz := now();
  v_skew       interval := make_interval(secs => greatest(coalesce(p_max_future_skew_seconds,300), 0));
  v_seen       int := 0;    -- transitions received (EVERY row in payload)
  v_valid      int := 0;    -- valid = applied + duplicate
  v_applied    int := 0;    -- newly inserted (deduped)
  v_rejected   int := 0;    -- received - valid (categorized below)
  v_clamped    int := 0;    -- valid but future-skewed -> effective_at clamped to now
  v_rej_by     jsonb := '{}'::jsonb;   -- {rejection_category: count} — observable
  v_ck_seen    int := 0;
  v_ck_valid   int := 0;
  v_ck_applied int := 0;
  v_ck_rej     int := 0;
  v_ck_deflt   int := 0;    -- valid checkpoints whose malformed metadata was safe-defaulted
  v_ck_rej_by  jsonb := '{}'::jsonb;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;

  -- TRANSITIONS: classify EVERY payload row (received = valid + rejected), then apply the valid.
  -- Every typed field is parsed fail-soft (wl_try_*), so no single malformed value can abort the
  -- batch. `at` = RAW device time (evidence); `effective_at` = server-safe ordering time (a wild
  -- future clock is clamped to now); received_at = now(). Replay is idempotent on dedupe_key.
  with raw as (
    select t,
           (t->>'id')                            as dedupe_key,
           (t->>'entity')                        as channel,
           lower(coalesce(t->>'layer','camera')) as layer,
           lower(t->>'to')                       as to_raw,
           wl_try_timestamptz(t->>'device_ts')   as device_ts,
           wl_try_bigint(t->>'seq')              as seq
      from jsonb_array_elements(coalesce(p_transitions, '[]'::jsonb)) t
  ),
  classified as (
    select r.*, cm.id as camera_id,
           case
             when coalesce(r.dedupe_key,'') = '' then 'missing_id'
             when r.layer <> 'camera'            then 'wrong_layer'
             when coalesce(r.to_raw,'') = ''     then 'missing_state'
             when r.device_ts is null            then 'invalid_timestamp'
             when r.seq is null                  then 'invalid_sequence'
             when cm.id is null                  then 'unmapped_channel'
             else 'valid'
           end as verdict
      from raw r
      left join cameras cm on cm.site_id = v_agent.site_id and cm.channel = r.channel
  ),
  valid as (
    select dedupe_key, camera_id, seq, device_ts,
           coalesce(nullif(t->>'store_epoch',''), split_part(t->>'id', ':', 2)) as store_epoch,
           case when device_ts > v_now + v_skew then v_now else device_ts end as effective_at,
           (device_ts > v_now + v_skew) as clamped,
           case when lower(coalesce(t->>'from','')) in ('operational','degraded','offline','unknown')
                then lower(t->>'from') else 'unknown' end as from_state,
           case when to_raw in ('operational','degraded','offline','unknown')
                then to_raw else 'unknown' end as to_state,
           case when lower(coalesce(t->>'reason','unknown')) in ('ok','unknown','probe_timeout',
                     'stale_frame','video_loss','channel_missing','channel_disabled','nvr_unreachable',
                     'nvr_auth_failed','agent_unreachable','storage_fault','not_recording','tamper',
                     'disk_error','disk_full') then lower(t->>'reason') else 'unknown' end as reason,
           case when lower(coalesce(t->>'source','probe')) in ('native','probe','inventory','upper_layer')
                then lower(t->>'source') else 'probe' end as source
      from classified where verdict = 'valid'
  ),
  ins as (
    -- effective_at + store_epoch + seq are written ONCE here; ON CONFLICT DO NOTHING keeps them
    -- immutable on any later replay. The row's identity `id` is the server first-seen order.
    insert into camera_health_transitions
      (tenant_id, site_id, camera_id, from_state, to_state, reason_code, at, effective_at,
       received_at, source, dedupe_key, store_epoch, seq)
    select v_agent.tenant_id, v_agent.site_id, camera_id, from_state, to_state, reason,
           device_ts, effective_at, v_now, source, dedupe_key, store_epoch, seq
      from valid
    on conflict (dedupe_key) where dedupe_key is not null do nothing
    returning 1
  )
  select (select count(*) from raw),
         (select count(*) from valid),
         (select count(*) from ins),
         (select count(*) from classified where verdict <> 'valid'),
         (select count(*) from valid where clamped),
         (select coalesce(jsonb_object_agg(verdict, c), '{}'::jsonb)
            from (select verdict, count(*) c from classified where verdict <> 'valid' group by verdict) z)
    into v_seen, v_valid, v_applied, v_rejected, v_clamped, v_rej_by;

  -- CURRENT STATE: advance to the latest transition per camera, reading the AUTHORITATIVE,
  -- IMMUTABLE ordering from the ledger (effective_at/store_epoch/seq/id) — NOT recomputed from the
  -- payload. A prior command's inserts are visible here, so this covers rows just inserted above AND
  -- replayed rows (which reuse their originally-accepted effective_at, so a replay can never
  -- re-clamp against a later now()). Advancement is DURABLE and deterministic across batches: the
  -- watermark is the full tuple (effective_at, store_epoch, numeric seq, server first-seen id).
  with batch_ids as (
    select distinct (t->>'id') as dedupe_key
      from jsonb_array_elements(coalesce(p_transitions, '[]'::jsonb)) t
     where coalesce(t->>'id','') <> ''
  ),
  authoritative as (
    select cht.camera_id, cht.effective_at, cht.store_epoch, cht.seq, cht.id as ingest,
           cht.to_state, cht.reason_code
      from camera_health_transitions cht
      join batch_ids b on b.dedupe_key = cht.dedupe_key
     where cht.site_id = v_agent.site_id
  ),
  latest as (
    -- newest per camera within this batch by (effective_at, numeric seq); id is a final tiebreak
    select distinct on (camera_id) camera_id, to_state, reason_code, effective_at,
           store_epoch, seq, ingest
      from authoritative
      order by camera_id, effective_at desc, seq desc nulls last, ingest desc
  )
  update camera_health ch
     set health_state     = l.to_state,
         reason_code       = l.reason_code,
         observed_at       = l.effective_at,
         observed_epoch    = l.store_epoch,
         observed_seq      = l.seq,
         observed_ingest   = l.ingest,
         last_change_at    = v_now,
         updated_at        = v_now,
         last_offline_at   = case when l.to_state = 'offline' then l.effective_at
                                  else ch.last_offline_at end,
         last_recovery_at  = case when l.to_state = 'operational' and ch.health_state = 'offline'
                                  then l.effective_at else ch.last_recovery_at end
    from latest l
   where ch.camera_id = l.camera_id
     -- DURABLE, deterministic ordering across batches/epochs:
     and ( l.effective_at > coalesce(ch.observed_at, '-infinity'::timestamptz)          -- strictly newer time
        or (l.effective_at = ch.observed_at                                              -- same time, same epoch:
            and l.store_epoch is not distinct from ch.observed_epoch
            and l.seq > coalesce(ch.observed_seq, -1))                                   --   numeric sequence wins
        or (l.effective_at = ch.observed_at                                              -- same time, other epoch:
            and l.store_epoch is distinct from ch.observed_epoch
            and l.ingest > coalesce(ch.observed_ingest, -1)) );                          --   server first-seen wins

  -- CHECKPOINTS: classify (received = valid + rejected). id + a parseable device_ts are essential;
  -- seq/cameras_observed/cycle_ok are metadata parsed fail-soft (wl_try_*) and safe-DEFAULTED — a
  -- malformed one keeps the row (counted as field_defaulted), never rejects or poisons it. Deduped
  -- on the epoch-qualified checkpoint_id.
  with raw as (
    select (c->>'id') as checkpoint_id, (c->>'store_epoch') as store_epoch,
           wl_try_bigint(c->>'seq')             as seq_val,
           wl_try_timestamptz(c->>'device_ts')  as device_ts,
           lower(coalesce(c->>'nvr_state','unknown')) as nvr_state,
           wl_try_int(c->>'cameras_observed')   as cams_val,
           wl_try_bool(c->>'cycle_ok')          as cycle_val,
           (c ? 'seq') as has_seq, (c ? 'cameras_observed') as has_cams, (c ? 'cycle_ok') as has_cycle
      from jsonb_array_elements(coalesce(p_checkpoints, '[]'::jsonb)) c
  ),
  classified as (
    select r.*,
           case when coalesce(r.checkpoint_id,'') = '' then 'missing_id'
                when r.device_ts is null            then 'invalid_timestamp'
                else 'valid' end as verdict,
           ((r.has_seq and r.seq_val is null) or (r.has_cams and r.cams_val is null)
             or (r.has_cycle and r.cycle_val is null)) as metadata_defaulted
      from raw r
  ),
  insc as (
    insert into local_monitoring_checkpoints
      (tenant_id, site_id, agent_id, checkpoint_id, store_epoch, agent_seq, device_ts,
       nvr_state, cameras_observed, cycle_ok)
    select v_agent.tenant_id, v_agent.site_id, v_agent.id, checkpoint_id, store_epoch,
           coalesce(seq_val, 0), device_ts, nvr_state,
           coalesce(cams_val, 0), coalesce(cycle_val, true)
      from classified where verdict = 'valid'
    on conflict (checkpoint_id) do nothing            -- epoch-qualified: rebuild-proof idempotency
    returning 1
  )
  select (select count(*) from raw),
         (select count(*) from classified where verdict = 'valid'),
         (select count(*) from insc),
         (select count(*) from classified where verdict <> 'valid'),
         (select count(*) from classified where verdict = 'valid' and metadata_defaulted),
         (select coalesce(jsonb_object_agg(verdict, c), '{}'::jsonb)
            from (select verdict, count(*) c from classified where verdict <> 'valid' group by verdict) z)
    into v_ck_seen, v_ck_valid, v_ck_applied, v_ck_rej, v_ck_deflt, v_ck_rej_by;

  update agents set last_seen_at = v_now where id = v_agent.id;

  return jsonb_build_object(
    'ok', true,
    -- invariant: transitions_received = transitions_valid + transitions_rejected;
    --            transitions_valid    = transitions_applied + transitions_duplicate.
    'transitions_received',    v_seen,
    'transitions_valid',       v_valid,
    'transitions_applied',     v_applied,
    'transitions_duplicate',   v_valid - v_applied,   -- already-present (idempotent replay)
    'transitions_rejected',    v_rejected,
    'transitions_rejected_by', v_rej_by,               -- {category: count} — observable
    'transitions_clamped',     v_clamped,              -- future-skew clamped for ordering — observable
    -- invariant: checkpoints_received = checkpoints_valid + checkpoints_rejected.
    'checkpoints_received',    v_ck_seen,
    'checkpoints_valid',       v_ck_valid,
    'checkpoints_applied',     v_ck_applied,
    'checkpoints_rejected',    v_ck_rej,
    'checkpoints_rejected_by', v_ck_rej_by,            -- {category: count} — observable
    'checkpoints_field_defaulted', v_ck_deflt,         -- malformed metadata safe-defaulted — observable
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
