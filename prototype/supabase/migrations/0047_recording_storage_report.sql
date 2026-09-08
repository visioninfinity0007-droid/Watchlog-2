-- =====================================================================
-- 0047 - Phase A increment 6: NVR recording + storage health (durable reconcile)
--
-- Recording and storage are FIRST-CLASS health layers, kept strictly separate from video health
-- (increment 4) and NVR connectivity/auth (increment 3):
--
--   * their CURRENT reasons live in their OWN columns (camera_health.recording_reason_code,
--     nvr_health.storage_reason_code) — never the reason_code owned by another layer.
--   * their transition LEDGERS are their own, correctly TYPED tables (wl_recording_state /
--     wl_storage_state) — never shoehorned into the wl_health_state camera_health_transitions.
--
-- SINGLE-WRITER, watermark-owning design (matches increment 5's intent, corrected): there is NO
-- separate live current-state RPC. EVERY recording/storage state change — even while online — flows
-- through the agent's local HealthStore (layers camera_recording / nvr_storage) and is applied by
-- wl_reconcile_recording_storage, which OWNS both the typed ledger AND the durable current-state
-- watermark. A direct live write cannot exist to bypass the ordering watermark and regress state.
--
-- This is a PARALLEL deduped replay path to wl_reconcile_health (0046, frozen, camera video),
-- carrying every increment-5 invariant: epoch-qualified identity, observed-vs-received time,
-- future-clock clamp, immutable accepted effective_at, single-epoch batch, forward-only composite
-- watermark (effective_at -> numeric seq -> server first-seen), replay idempotency, tenant/site from
-- the agent row, poison-row isolation (received = valid + rejected, categorized).
--
-- Two durability guarantees this migration adds on top of that:
--   * PER-ID DISPOSITION: the RPC returns accepted_ids / duplicate_ids / rejected[{id,reason}], so the
--     agent acknowledges ONLY ledger-durable ids and never infers "all accepted" from RPC success — a
--     partially rejected row (e.g. unmapped_channel) stays retriable and a poison row is quarantined.
--   * CREATE-IF-ABSENT current state: current state is an UPSERT from the authoritative ledger winner,
--     so a ledger insert can never succeed while the current row silently no-ops for want of a
--     pre-existing baseline. The upsert sets ONLY this layer's columns; all others are preserved.
--
-- Honesty: the agent already resolved UNKNOWN where the vendor API is unreadable/config-only, and a
-- storage DEGRADED never blanket-faults a camera. This SQL only orders + stores what it observed.
-- The NVR-level "is the recorder recording?" rollup is a read-model derivation (later increment);
-- increment 6 owns the authoritative per-channel recording state + the NVR storage state.
-- =====================================================================

-- ---- separate CURRENT reasons (never clobber the reason owned by another layer) ----
alter table public.camera_health add column if not exists recording_reason_code public.wl_reason_code;
alter table public.nvr_health    add column if not exists storage_reason_code   public.wl_reason_code;

-- ---- forward-only ordering watermarks per dimension (mirror camera_health.observed_* from 0046) ----
alter table public.camera_health add column if not exists rec_observed_at     timestamptz;
alter table public.camera_health add column if not exists rec_observed_epoch  text;
alter table public.camera_health add column if not exists rec_observed_seq    bigint;
alter table public.camera_health add column if not exists rec_observed_ingest bigint;

alter table public.nvr_health add column if not exists sto_observed_at     timestamptz;
alter table public.nvr_health add column if not exists sto_observed_epoch  text;
alter table public.nvr_health add column if not exists sto_observed_seq    bigint;
alter table public.nvr_health add column if not exists sto_observed_ingest bigint;

-- ---- dedicated TYPED transition ledgers (full reconciliation shape) ----
create table if not exists public.recording_transitions (
  id           bigint generated always as identity primary key,   -- server first-seen order
  tenant_id    uuid not null references public.tenants(id) on delete cascade,
  site_id      uuid not null references public.sites(id) on delete cascade,
  camera_id    uuid not null references public.cameras(id) on delete cascade,
  from_state   public.wl_recording_state not null,
  to_state     public.wl_recording_state not null,
  reason_code  public.wl_reason_code,
  source       text,
  at           timestamptz not null,                  -- device-observed time (evidence)
  effective_at timestamptz not null,                  -- server-safe ordering time (immutable)
  received_at  timestamptz not null default now(),
  store_epoch  text,
  seq          bigint,
  dedupe_key   text
);
create unique index if not exists recording_tx_dedupe_uidx
  on public.recording_transitions (dedupe_key) where dedupe_key is not null;
create index if not exists recording_tx_camera_idx on public.recording_transitions (camera_id, at desc);

create table if not exists public.storage_transitions (
  id           bigint generated always as identity primary key,
  tenant_id    uuid not null references public.tenants(id) on delete cascade,
  site_id      uuid not null references public.sites(id) on delete cascade,
  agent_id     uuid not null references public.agents(id) on delete cascade,   -- the recorder
  from_state   public.wl_storage_state not null,
  to_state     public.wl_storage_state not null,
  reason_code  public.wl_reason_code,
  source       text,
  at           timestamptz not null,
  effective_at timestamptz not null,
  received_at  timestamptz not null default now(),
  store_epoch  text,
  seq          bigint,
  dedupe_key   text
);
create unique index if not exists storage_tx_dedupe_uidx
  on public.storage_transitions (dedupe_key) where dedupe_key is not null;
create index if not exists storage_tx_agent_idx on public.storage_transitions (agent_id, at desc);

alter table public.recording_transitions enable row level security;
alter table public.storage_transitions enable row level security;
revoke all on table public.recording_transitions from public, anon, authenticated;
revoke all on table public.storage_transitions from public, anon, authenticated;

-- =====================================================================
-- wl_reconcile_recording_storage — the SOLE writer of recording/storage transitions + current state.
-- Layers: 'camera_recording' (entity = channel), 'nvr_storage' (entity = the recorder).
-- =====================================================================
create or replace function public.wl_reconcile_recording_storage(
  p_agent_id     uuid,
  p_agent_key    text,
  p_transitions  jsonb,
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
  v_seen     int := 0;
  v_valid    int := 0;
  v_applied  int := 0;
  v_rejected int := 0;
  v_rej_by   jsonb := '{}'::jsonb;
  v_accepted     jsonb := '[]'::jsonb;   -- ids newly written to the ledger this call
  v_duplicate    jsonb := '[]'::jsonb;   -- ids valid but already present (idempotent replay)
  v_rejected_ids jsonb := '[]'::jsonb;   -- [{id, reason}] never written — agent must NOT ack these
  v_n_epochs int := 0;
  v_mixed    boolean := false;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;

  -- single-epoch-per-batch invariant (same reasoning as wl_reconcile_health).
  select count(distinct coalesce(nullif(t->>'store_epoch',''), split_part(t->>'id', ':', 2)))
    into v_n_epochs
    from jsonb_array_elements(coalesce(p_transitions, '[]'::jsonb)) t
   where lower(coalesce(t->>'layer','')) in ('camera_recording','nvr_storage')
     and coalesce(t->>'to','') <> '' and coalesce(t->>'id','') <> ''
     and wl_try_timestamptz(t->>'device_ts') is not null
     and wl_try_bigint(t->>'seq') is not null;
  v_mixed := v_n_epochs > 1;

  -- classify every row; valid = applied + duplicate; received = valid + rejected (categorized).
  with raw as (
    select t, (t->>'id') as dedupe_key, lower(coalesce(t->>'layer','')) as layer,
           (t->>'entity') as entity, lower(t->>'to') as to_raw,
           wl_try_timestamptz(t->>'device_ts') as device_ts, wl_try_bigint(t->>'seq') as seq
      from jsonb_array_elements(coalesce(p_transitions, '[]'::jsonb)) t
  ),
  classified as (
    select r.*, cm.id as camera_id,
           case
             when coalesce(r.dedupe_key,'') = '' then 'missing_id'
             when r.layer not in ('camera_recording','nvr_storage') then 'wrong_layer'
             when coalesce(r.to_raw,'') = '' then 'missing_state'
             when r.device_ts is null then 'invalid_timestamp'
             when r.seq is null then 'invalid_sequence'
             when r.layer = 'camera_recording' and cm.id is null then 'unmapped_channel'
             when v_mixed then 'mixed_epoch_batch'
             else 'valid'
           end as verdict
      from raw r
      left join cameras cm on r.layer = 'camera_recording'
                          and cm.site_id = v_agent.site_id and cm.channel = r.entity
  ),
  valid as (
    select dedupe_key, layer, camera_id, seq, device_ts,
           coalesce(nullif(t->>'store_epoch',''), split_part(t->>'id', ':', 2)) as store_epoch,
           case when device_ts > v_now + v_skew then v_now else device_ts end as effective_at,
           case when layer = 'nvr_storage'
                then (case when to_raw in ('ok','degraded','fault','unknown') then to_raw else 'unknown' end)
                else (case when to_raw in ('recording','not_recording','storage_fault','unknown') then to_raw else 'unknown' end)
           end as to_state,
           case when layer = 'nvr_storage'
                then (case when lower(coalesce(t->>'from','')) in ('ok','degraded','fault','unknown')
                           then lower(t->>'from') else 'unknown' end)
                else (case when lower(coalesce(t->>'from','')) in ('recording','not_recording','storage_fault','unknown')
                           then lower(t->>'from') else 'unknown' end)
           end as from_state,
           case when lower(coalesce(t->>'reason','unknown')) in ('ok','unknown','probe_timeout',
                     'stale_frame','video_loss','channel_missing','channel_disabled','nvr_unreachable',
                     'nvr_auth_failed','agent_unreachable','storage_fault','not_recording','tamper',
                     'disk_error','disk_full') then lower(t->>'reason') else 'unknown' end as reason,
           case when lower(coalesce(t->>'source','probe')) in ('native','probe','inventory','upper_layer')
                then lower(t->>'source') else 'probe' end as source
      from classified where verdict = 'valid'
  ),
  ins_rec as (
    insert into recording_transitions (tenant_id, site_id, camera_id, from_state, to_state,
                                       reason_code, at, effective_at, received_at, source,
                                       store_epoch, seq, dedupe_key)
    select v_agent.tenant_id, v_agent.site_id, camera_id, from_state, to_state, reason, device_ts,
           effective_at, v_now, source, store_epoch, seq, dedupe_key
      from valid where layer = 'camera_recording'
    on conflict (dedupe_key) where dedupe_key is not null do nothing
    returning dedupe_key
  ),
  ins_sto as (
    insert into storage_transitions (tenant_id, site_id, agent_id, from_state, to_state,
                                     reason_code, at, effective_at, received_at, source,
                                     store_epoch, seq, dedupe_key)
    select v_agent.tenant_id, v_agent.site_id, v_agent.id, from_state, to_state, reason, device_ts,
           effective_at, v_now, source, store_epoch, seq, dedupe_key
      from valid where layer = 'nvr_storage'
    on conflict (dedupe_key) where dedupe_key is not null do nothing
    returning dedupe_key
  ),
  accepted as (   -- ids ACTUALLY inserted this call (ON CONFLICT skips are NOT returned)
    select dedupe_key from ins_rec union all select dedupe_key from ins_sto
  )
  select (select count(*) from raw),
         (select count(*) from valid),
         (select count(*) from accepted),
         (select count(*) from classified where verdict <> 'valid'),
         (select coalesce(jsonb_object_agg(verdict, c), '{}'::jsonb)
            from (select verdict, count(*) c from classified where verdict <> 'valid' group by verdict) z),
         -- PER-ID dispositions: the agent must acknowledge by these, never infer "all accepted" from
         -- RPC success. accepted = newly ledgered; duplicate = valid but already ledgered (replay);
         -- rejected = [{id, reason}] that never entered the ledger (quarantine/bounded-retry locally).
         (select coalesce(jsonb_agg(dedupe_key), '[]'::jsonb) from accepted),
         (select coalesce(jsonb_agg(v.dedupe_key), '[]'::jsonb) from valid v
           where v.dedupe_key not in (select dedupe_key from accepted)),
         (select coalesce(jsonb_agg(jsonb_build_object('id', dedupe_key, 'reason', verdict)), '[]'::jsonb)
            from classified where verdict <> 'valid')
    into v_seen, v_valid, v_applied, v_rejected, v_rej_by, v_accepted, v_duplicate, v_rejected_ids;

  -- ---- CURRENT STATE from the AUTHORITATIVE (immutable) ledger, forward-only. Skipped for a
  -- rejected mixed-epoch batch. Watermark tuple = (effective_at, store_epoch, seq, server id). ----
  if not v_mixed then
    -- camera recording
    with bids as (
      select distinct (t->>'id') as dedupe_key from jsonb_array_elements(coalesce(p_transitions,'[]'::jsonb)) t
       where coalesce(t->>'id','') <> ''
    ),
    a_cam as (
      select rt.camera_id, rt.effective_at, rt.store_epoch, rt.seq, rt.id as ingest, rt.to_state, rt.reason_code
        from recording_transitions rt join bids b on b.dedupe_key = rt.dedupe_key
       where rt.site_id = v_agent.site_id
    ),
    l_cam as (
      select distinct on (camera_id) camera_id, to_state, reason_code, effective_at, store_epoch, seq, ingest
        from a_cam order by camera_id, effective_at desc, seq desc nulls last, ingest desc
    )
    -- UPSERT, not a bare UPDATE: the current row is CREATED from the authoritative ledger winner if
    -- it does not exist yet, so a successful ledger insert can never leave current state silently
    -- absent (increment 6 must not depend on 0044/0045 having pre-materialised the row). On conflict
    -- it advances FORWARD-ONLY and sets ONLY recording columns — every video-health / inventory
    -- column (health_state, reason_code, consecutive_*, observed_* video watermark, probe_meta,
    -- last_*) is absent from the SET and therefore preserved untouched.
    insert into camera_health as ch
      (camera_id, tenant_id, site_id, recording_state, recording_reason_code,
       rec_observed_at, rec_observed_epoch, rec_observed_seq, rec_observed_ingest, updated_at)
    select l.camera_id, v_agent.tenant_id, v_agent.site_id, l.to_state, l.reason_code,
           l.effective_at, l.store_epoch, l.seq, l.ingest, v_now
      from l_cam l
    on conflict (camera_id) do update
       set recording_state       = excluded.recording_state,
           recording_reason_code  = excluded.recording_reason_code,
           rec_observed_at        = excluded.rec_observed_at,
           rec_observed_epoch     = excluded.rec_observed_epoch,
           rec_observed_seq       = excluded.rec_observed_seq,
           rec_observed_ingest    = excluded.rec_observed_ingest,
           updated_at             = v_now
     where ( excluded.rec_observed_at > coalesce(ch.rec_observed_at, '-infinity'::timestamptz)
          or (excluded.rec_observed_at = ch.rec_observed_at
              and excluded.rec_observed_epoch is not distinct from ch.rec_observed_epoch
              and excluded.rec_observed_seq > coalesce(ch.rec_observed_seq, -1))
          or (excluded.rec_observed_at = ch.rec_observed_at
              and excluded.rec_observed_epoch is distinct from ch.rec_observed_epoch
              and excluded.rec_observed_ingest > coalesce(ch.rec_observed_ingest, -1)) );

    -- nvr storage (entity = the recorder)
    with bids as (
      select distinct (t->>'id') as dedupe_key from jsonb_array_elements(coalesce(p_transitions,'[]'::jsonb)) t
       where coalesce(t->>'id','') <> ''
    ),
    a_sto as (
      select st.effective_at, st.store_epoch, st.seq, st.id as ingest, st.to_state, st.reason_code
        from storage_transitions st join bids b on b.dedupe_key = st.dedupe_key
       where st.agent_id = v_agent.id
    ),
    l_sto as (
      select to_state, reason_code, effective_at, store_epoch, seq, ingest
        from a_sto order by effective_at desc, seq desc nulls last, ingest desc limit 1
    )
    -- UPSERT, not a bare UPDATE (same reasoning as camera recording): create the recorder's health
    -- row from the ledger winner if absent, else advance FORWARD-ONLY, touching ONLY storage columns.
    -- Connectivity/auth (nvr_reachable, nvr_auth_ok), the recording rollup and reason_code are absent
    -- from the SET and therefore preserved untouched.
    insert into nvr_health as nh
      (agent_id, tenant_id, site_id, storage_state, storage_reason_code,
       sto_observed_at, sto_observed_epoch, sto_observed_seq, sto_observed_ingest, updated_at)
    select v_agent.id, v_agent.tenant_id, v_agent.site_id, l.to_state, l.reason_code,
           l.effective_at, l.store_epoch, l.seq, l.ingest, v_now
      from l_sto l
    on conflict (agent_id) do update
       set storage_state        = excluded.storage_state,
           storage_reason_code   = excluded.storage_reason_code,
           sto_observed_at       = excluded.sto_observed_at,
           sto_observed_epoch    = excluded.sto_observed_epoch,
           sto_observed_seq      = excluded.sto_observed_seq,
           sto_observed_ingest   = excluded.sto_observed_ingest,
           updated_at            = v_now
     where ( excluded.sto_observed_at > coalesce(nh.sto_observed_at, '-infinity'::timestamptz)
          or (excluded.sto_observed_at = nh.sto_observed_at
              and excluded.sto_observed_epoch is not distinct from nh.sto_observed_epoch
              and excluded.sto_observed_seq > coalesce(nh.sto_observed_seq, -1))
          or (excluded.sto_observed_at = nh.sto_observed_at
              and excluded.sto_observed_epoch is distinct from nh.sto_observed_epoch
              and excluded.sto_observed_ingest > coalesce(nh.sto_observed_ingest, -1)) );
  end if;   -- not v_mixed

  update agents set last_seen_at = v_now where id = v_agent.id;

  return jsonb_build_object(
    'ok', true,
    'transitions_received', v_seen,      -- = valid + rejected
    'transitions_valid', v_valid,
    'transitions_applied', v_applied,
    'transitions_duplicate', v_valid - v_applied,
    'transitions_rejected', v_rejected,
    'transitions_rejected_by', v_rej_by,
    -- per-id dispositions (agent acknowledges accepted+duplicate; parks rejected by reason):
    'accepted_ids', v_accepted,
    'duplicate_ids', v_duplicate,
    'rejected', v_rejected_ids,
    'server_time', v_now);
end $$;

revoke all on function public.wl_reconcile_recording_storage(uuid, text, jsonb, int) from public;
grant execute on function public.wl_reconcile_recording_storage(uuid, text, jsonb, int) to anon, authenticated;
