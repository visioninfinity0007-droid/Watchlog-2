-- =====================================================================
-- 0042 - Phase A: Health Foundation (schema + state contract)
--
-- This migration lays down the STORAGE and the STATE VOCABULARY for the
-- layered health model (docs/design/OPERATIONAL_INTELLIGENCE_ARCHITECTURE.md
-- §§5-6, §25). It is schema only: no agent-write RPCs, no server watchdog, no
-- read model yet — those land in later Phase A increments (0043+). Committing
-- the contract first lets every later increment be reviewed against a fixed,
-- test-pinned vocabulary.
--
-- Two ideas the design insists on, encoded here as TYPES so they cannot drift:
--   * INVENTORY is separate from HEALTH. `inventory_state` answers "is this
--     channel configured on the recorder?"; `health_state` answers "is the
--     configured channel actually working?". A camera the operator removed is
--     MISSING (inventory), never OFFLINE (health).
--   * A camera we cannot currently observe (recorder down, credentials wrong,
--     agent unreachable) is UNKNOWN, never OFFLINE. We only claim OFFLINE when
--     we positively confirmed loss of a signal we could otherwise see.
--
-- The vocabularies below are the SAME strings the pure state machine emits
-- (prototype/agent/health_model.py). test_health_foundation_contract.py proves
-- the two are in lockstep, so a rename on one side fails CI, not the field.
--
-- Fail-closed: every table has RLS on and ALL direct grants revoked. Nothing is
-- reachable by anon/authenticated; access is exclusively through the
-- SECURITY DEFINER RPCs added in later increments. This mirrors 0039
-- (control_room_layouts), the strongest posture already in the tree.
--
-- Numbering: 0040/0041 are intentionally reserved for the in-flight incident-
-- footage work (PR #36) and are absent on this branch on purpose; the health
-- line begins at 0042 (tools/reserved_migrations.json records the reservation).
-- =====================================================================

-- ---------------------------------------------------------------------
-- State vocabulary as domains (single source of truth; idempotent).
-- VOCAB markers below are parsed by the contract test — keep them in sync
-- with the domain bodies (the test enforces it).
-- ---------------------------------------------------------------------
-- VOCAB inventory_state: present|missing|disabled|unknown
-- VOCAB health_state: operational|degraded|offline|unknown
-- VOCAB recording_state: recording|not_recording|storage_fault|unknown
-- VOCAB reason_code: ok|unknown|probe_timeout|stale_frame|video_loss|channel_missing|channel_disabled|nvr_unreachable|nvr_auth_failed|agent_unreachable|storage_fault|not_recording|tamper|disk_error|disk_full
-- VOCAB storage_state: ok|degraded|fault|unknown

do $$ begin
  if not exists (select 1 from pg_type where typname = 'wl_inventory_state') then
    create domain public.wl_inventory_state as text
      check (value in ('present','missing','disabled','unknown'));
  end if;
  if not exists (select 1 from pg_type where typname = 'wl_health_state') then
    create domain public.wl_health_state as text
      check (value in ('operational','degraded','offline','unknown'));
  end if;
  if not exists (select 1 from pg_type where typname = 'wl_recording_state') then
    create domain public.wl_recording_state as text
      check (value in ('recording','not_recording','storage_fault','unknown'));
  end if;
  if not exists (select 1 from pg_type where typname = 'wl_storage_state') then
    create domain public.wl_storage_state as text
      check (value in ('ok','degraded','fault','unknown'));
  end if;
  if not exists (select 1 from pg_type where typname = 'wl_reason_code') then
    create domain public.wl_reason_code as text
      check (value in ('ok','unknown','probe_timeout','stale_frame','video_loss',
                       'channel_missing','channel_disabled','nvr_unreachable',
                       'nvr_auth_failed','agent_unreachable','storage_fault',
                       'not_recording','tamper','disk_error','disk_full'));
  end if;
end $$;

-- ---------------------------------------------------------------------
-- 1. Camera inventory — current + transition ledger (§25.1)
-- ---------------------------------------------------------------------
create table if not exists public.camera_inventory (
  camera_id        uuid primary key references public.cameras(id) on delete cascade,
  tenant_id        uuid not null references public.tenants(id) on delete cascade,
  site_id          uuid not null references public.sites(id) on delete cascade,
  inventory_state  public.wl_inventory_state not null default 'unknown',
  reason_code      public.wl_reason_code,
  first_seen_at    timestamptz,
  last_present_at  timestamptz,
  updated_at       timestamptz not null default now()
);
create index if not exists camera_inventory_site_idx   on public.camera_inventory (site_id);
create index if not exists camera_inventory_tenant_idx on public.camera_inventory (tenant_id);

create table if not exists public.camera_inventory_transitions (
  id           bigint generated always as identity primary key,
  tenant_id    uuid not null references public.tenants(id) on delete cascade,
  site_id      uuid not null references public.sites(id) on delete cascade,
  camera_id    uuid not null references public.cameras(id) on delete cascade,
  from_state   public.wl_inventory_state not null,
  to_state     public.wl_inventory_state not null,
  reason_code  public.wl_reason_code,
  at           timestamptz not null default now(),
  meta         jsonb not null default '{}'::jsonb
);
create index if not exists camera_inv_tx_camera_idx on public.camera_inventory_transitions (camera_id, at desc);
create index if not exists camera_inv_tx_site_idx   on public.camera_inventory_transitions (site_id, at desc);

-- ---------------------------------------------------------------------
-- 2. Camera health — current + outage ledger (§25.2)
-- ---------------------------------------------------------------------
create table if not exists public.camera_health (
  camera_id         uuid primary key references public.cameras(id) on delete cascade,
  tenant_id         uuid not null references public.tenants(id) on delete cascade,
  site_id           uuid not null references public.sites(id) on delete cascade,
  health_state      public.wl_health_state not null default 'unknown',
  recording_state   public.wl_recording_state not null default 'unknown',
  reason_code       public.wl_reason_code,
  consecutive_fail  int not null default 0,
  consecutive_ok    int not null default 0,
  last_probe_at     timestamptz,
  last_ok_at        timestamptz,
  last_change_at    timestamptz,
  last_offline_at   timestamptz,
  last_recovery_at  timestamptz,
  probe_meta        jsonb not null default '{}'::jsonb,
  updated_at        timestamptz not null default now()
);
create index if not exists camera_health_site_idx    on public.camera_health (site_id);
create index if not exists camera_health_tenant_idx  on public.camera_health (tenant_id);
-- fast "what is down right now" without scanning healthy cameras
create index if not exists camera_health_offline_idx on public.camera_health (site_id)
  where health_state = 'offline';

create table if not exists public.camera_health_transitions (
  id           bigint generated always as identity primary key,
  tenant_id    uuid not null references public.tenants(id) on delete cascade,
  site_id      uuid not null references public.sites(id) on delete cascade,
  camera_id    uuid not null references public.cameras(id) on delete cascade,
  from_state   public.wl_health_state not null,
  to_state     public.wl_health_state not null,
  reason_code  public.wl_reason_code,
  at           timestamptz not null default now(),
  ended_at     timestamptz,     -- for an OFFLINE/DEGRADED span, set when it clears
  meta         jsonb not null default '{}'::jsonb
);
create index if not exists camera_health_tx_camera_idx on public.camera_health_transitions (camera_id, at desc);
create index if not exists camera_health_tx_site_idx   on public.camera_health_transitions (site_id, at desc);

-- ---------------------------------------------------------------------
-- 3+4. NVR connectivity/auth + recording/storage health (§25.3-4)
-- One row per recorder (== one agent). Layers are distinct columns so the
-- read model can say "recorder reachable, credentials good, but STORAGE_FAULT".
-- ---------------------------------------------------------------------
create table if not exists public.nvr_health (
  agent_id         uuid primary key references public.agents(id) on delete cascade,
  tenant_id        uuid not null references public.tenants(id) on delete cascade,
  site_id          uuid not null references public.sites(id) on delete cascade,
  nvr_reachable    boolean,                 -- null = not yet observed / unknown
  nvr_auth_ok      boolean,
  recording_state  public.wl_recording_state not null default 'unknown',
  storage_state    public.wl_storage_state not null default 'unknown',
  reason_code      public.wl_reason_code,
  last_ok_at       timestamptz,
  last_change_at   timestamptz,
  updated_at       timestamptz not null default now()
);
create index if not exists nvr_health_site_idx   on public.nvr_health (site_id);
create index if not exists nvr_health_tenant_idx on public.nvr_health (tenant_id);

create table if not exists public.nvr_health_transitions (
  id           bigint generated always as identity primary key,
  tenant_id    uuid not null references public.tenants(id) on delete cascade,
  site_id      uuid not null references public.sites(id) on delete cascade,
  agent_id     uuid not null references public.agents(id) on delete cascade,
  layer        text not null check (layer in ('connectivity','auth','recording','storage')),
  from_state   text not null,
  to_state     text not null,
  reason_code  public.wl_reason_code,
  at           timestamptz not null default now(),
  meta         jsonb not null default '{}'::jsonb
);
create index if not exists nvr_health_tx_agent_idx on public.nvr_health_transitions (agent_id, at desc);
create index if not exists nvr_health_tx_site_idx  on public.nvr_health_transitions (site_id, at desc);

-- ---------------------------------------------------------------------
-- 5. Server-derived agent-unreachable intervals (§25.5)
-- The SERVER opens these from heartbeat gaps. Cause is fixed 'agent_unreachable':
-- we deliberately do NOT guess PC-off vs internet-down (design §6/§9) — that is
-- reconciled later from local evidence, never fabricated here.
-- ---------------------------------------------------------------------
create table if not exists public.agent_unreachable_intervals (
  id           bigint generated always as identity primary key,
  tenant_id    uuid not null references public.tenants(id) on delete cascade,
  site_id      uuid not null references public.sites(id) on delete cascade,
  agent_id     uuid not null references public.agents(id) on delete cascade,
  started_at   timestamptz not null,
  ended_at     timestamptz,                 -- null = still unreachable
  cause        text not null default 'agent_unreachable' check (cause = 'agent_unreachable'),
  detected_by  text not null default 'server_watchdog',
  created_at   timestamptz not null default now()
);
create index if not exists agent_unreach_agent_idx on public.agent_unreachable_intervals (agent_id, started_at desc);
-- at most one OPEN interval per agent
create unique index if not exists agent_unreach_open_uidx
  on public.agent_unreachable_intervals (agent_id) where ended_at is null;

-- ---------------------------------------------------------------------
-- 6. Monitoring coverage + unverified intervals (§25.6)
-- Availability is measured over MONITORED time; coverage is measured over
-- WALL-CLOCK. Unverified windows are excluded from availability and counted
-- against coverage — so we never invent an outage for a period we could not see.
-- ---------------------------------------------------------------------
create table if not exists public.monitoring_coverage (
  id                 bigint generated always as identity primary key,
  tenant_id          uuid not null references public.tenants(id) on delete cascade,
  site_id            uuid not null references public.sites(id) on delete cascade,
  bucket_date        date not null,
  wall_seconds       int not null default 0,
  monitored_seconds  int not null default 0,
  unverified_seconds int not null default 0,
  updated_at         timestamptz not null default now(),
  unique (site_id, bucket_date)
);
create index if not exists monitoring_coverage_tenant_idx on public.monitoring_coverage (tenant_id);

create table if not exists public.unverified_intervals (
  id           bigint generated always as identity primary key,
  tenant_id    uuid not null references public.tenants(id) on delete cascade,
  site_id      uuid not null references public.sites(id) on delete cascade,
  agent_id     uuid references public.agents(id) on delete cascade,
  started_at   timestamptz not null,
  ended_at     timestamptz,
  cause        text not null default 'unknown'
                 check (cause in ('agent_unreachable','cloud_link_gap','startup','unknown')),
  source       text not null default 'server'
                 check (source in ('server','reconciled_local')),
  created_at   timestamptz not null default now()
);
create index if not exists unverified_site_idx on public.unverified_intervals (site_id, started_at desc);

-- ---------------------------------------------------------------------
-- 7. Operational faults (§25.7) — reliability lifecycle, DISTINCT from the
-- security `events` stream. A fault is "your CCTV needs attention" (camera
-- offline, storage fault, recorder unreachable); it is not a person/intrusion
-- alarm. Open faults dedupe so a sustained condition is ONE row, not a storm.
-- ---------------------------------------------------------------------
create table if not exists public.operational_faults (
  id               bigint generated always as identity primary key,
  tenant_id        uuid not null references public.tenants(id) on delete cascade,
  site_id          uuid not null references public.sites(id) on delete cascade,
  camera_id        uuid references public.cameras(id) on delete cascade,
  agent_id         uuid references public.agents(id) on delete cascade,
  fault_domain     text not null check (fault_domain in
                     ('camera','nvr_connectivity','nvr_auth','recording','storage','agent','coverage')),
  fault_type       text not null,
  severity         text not null default 'warning' check (severity in ('info','warning','critical')),
  state            text not null default 'open' check (state in ('open','acknowledged','resolved')),
  reason_code      public.wl_reason_code,
  opened_at        timestamptz not null default now(),
  acknowledged_at  timestamptz,
  resolved_at      timestamptz,
  dedupe_key       text not null,
  detail           jsonb not null default '{}'::jsonb
);
create index if not exists op_faults_site_state_idx on public.operational_faults (site_id, state);
create index if not exists op_faults_tenant_open_idx on public.operational_faults (tenant_id, opened_at desc);
-- one live fault per condition; a resolved fault frees the key for a future recurrence
create unique index if not exists op_faults_open_uidx
  on public.operational_faults (dedupe_key) where state <> 'resolved';

-- ---------------------------------------------------------------------
-- Fail-closed: RLS on, all direct grants revoked. RPC-only (definer RPCs
-- arrive in later Phase A increments). No client policies by design.
-- ---------------------------------------------------------------------
alter table public.camera_inventory              enable row level security;
alter table public.camera_inventory_transitions  enable row level security;
alter table public.camera_health                 enable row level security;
alter table public.camera_health_transitions     enable row level security;
alter table public.nvr_health                     enable row level security;
alter table public.nvr_health_transitions         enable row level security;
alter table public.agent_unreachable_intervals    enable row level security;
alter table public.monitoring_coverage            enable row level security;
alter table public.unverified_intervals           enable row level security;
alter table public.operational_faults             enable row level security;

revoke all on table public.camera_inventory              from public, anon, authenticated;
revoke all on table public.camera_inventory_transitions  from public, anon, authenticated;
revoke all on table public.camera_health                 from public, anon, authenticated;
revoke all on table public.camera_health_transitions     from public, anon, authenticated;
revoke all on table public.nvr_health                     from public, anon, authenticated;
revoke all on table public.nvr_health_transitions         from public, anon, authenticated;
revoke all on table public.agent_unreachable_intervals    from public, anon, authenticated;
revoke all on table public.monitoring_coverage            from public, anon, authenticated;
revoke all on table public.unverified_intervals           from public, anon, authenticated;
revoke all on table public.operational_faults             from public, anon, authenticated;
