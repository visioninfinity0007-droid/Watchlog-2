-- =====================================================================
-- WatchLog — connectivity prototype schema
-- =====================================================================
-- THROWAWAY. This is NOT the delivered Milestone 1 schema. It exists to
-- prove one thing: software on a machine we cannot reach can enroll
-- itself and report deduplicated events outbound, with no inbound access.
--
-- Deliberate deviation from WATCHLOG_FULL_BUILD_PLAN.md (which puts
-- multi-tenancy in Milestone 2 / Phase F): tenant_id is on every table
-- from day one. Costs nothing now, saves a backfill later. Prototype-only
-- choice — it does not pull billable M2 work forward.
--
-- No table is ever written by a client directly. Agents go through the
-- SECURITY DEFINER functions in 0004. RLS is on with no policies, so the
-- tables are unreachable to anon and authenticated by default.
-- =====================================================================

-- ---------------------------------------------------------------------
-- tenants — one row for the prototype
-- ---------------------------------------------------------------------
create table if not exists tenants (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  created_at  timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- sites — a physical location
-- ---------------------------------------------------------------------
create table if not exists sites (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references tenants(id) on delete cascade,
  name        text not null,
  timezone    text not null default 'Asia/Karachi',
  created_at  timestamptz not null default now()
);
create index if not exists sites_tenant_idx on sites(tenant_id);

-- ---------------------------------------------------------------------
-- agents — one installed copy of the software
--
-- The long-lived secret minted at enrollment is stored ONLY as a SHA-256
-- hash. Nothing in this database can reveal an agent's key; the plaintext
-- is returned once, at enrollment, and lives thereafter only in that
-- machine's local state file.
-- ---------------------------------------------------------------------
create table if not exists agents (
  id              uuid primary key default gen_random_uuid(),
  tenant_id       uuid not null references tenants(id) on delete cascade,
  site_id         uuid not null references sites(id)   on delete cascade,
  agent_key_hash  text not null unique,
  hostname        text,
  platform        text,
  agent_version   text,
  -- what the agent found at the other end of the NVR connection
  device_vendor   text,
  device_model    text,
  device_driver   text,
  enrolled_at     timestamptz not null default now(),
  last_seen_at    timestamptz
);
create index if not exists agents_tenant_idx    on agents(tenant_id);
create index if not exists agents_last_seen_idx on agents(last_seen_at desc);

-- ---------------------------------------------------------------------
-- enrollment_codes — short-lived, single-use; how a fresh install claims
-- an identity. Single-use is enforced atomically inside wl_enroll(): the
-- UPDATE only matches rows still unused and unexpired, and the whole
-- enrollment runs in one transaction, so a failure cannot burn a code.
-- ---------------------------------------------------------------------
create table if not exists enrollment_codes (
  code             text primary key,
  tenant_id        uuid not null references tenants(id) on delete cascade,
  site_id          uuid not null references sites(id)   on delete cascade,
  expires_at       timestamptz not null default now() + interval '7 days',
  used_at          timestamptz,
  used_by_agent_id uuid references agents(id) on delete set null,
  created_at       timestamptz not null default now()
);
create index if not exists enrollment_codes_open_idx
  on enrollment_codes(expires_at) where used_at is null;

-- ---------------------------------------------------------------------
-- cameras — discovered/declared channels on the NVR
-- ---------------------------------------------------------------------
create table if not exists cameras (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references tenants(id) on delete cascade,
  site_id     uuid not null references sites(id)   on delete cascade,
  channel     text not null,
  name        text,
  created_at  timestamptz not null default now(),
  constraint cameras_site_channel_uniq unique (site_id, channel)
);
create index if not exists cameras_tenant_idx on cameras(tenant_id);

-- ---------------------------------------------------------------------
-- events — the payload
--
-- Three clocks are stored separately, on purpose. The NVR, the agent
-- machine and Supabase will disagree, and collapsing them corrupts both
-- ordering and dedupe:
--   device_ts   — what the NVR said
--   agent_ts    — the agent machine's clock when it polled
--   received_at — Supabase's clock on insert (the only one we trust)
--
-- dedupe_key is computed inside wl_ingest_events() and ENFORCED by the
-- unique constraint below, so a retry, a restart or an overlapping poll
-- window cannot create a second row. Cargo Max lost time to exactly this
-- failure mode when a source had no stable event IDs; the key falls back
-- to site+camera+timestamp+type when the device gives no id.
-- ---------------------------------------------------------------------
create table if not exists events (
  id              bigint generated always as identity primary key,
  tenant_id       uuid not null references tenants(id) on delete cascade,
  site_id         uuid not null references sites(id)   on delete cascade,
  camera_id       uuid references cameras(id) on delete set null,
  agent_id        uuid references agents(id)  on delete set null,
  event_type      text not null,
  device_event_id text,
  device_ts       timestamptz not null,
  agent_ts        timestamptz not null,
  received_at     timestamptz not null default now(),
  dedupe_key      text not null,
  payload         jsonb not null default '{}'::jsonb,
  constraint events_dedupe_key_not_blank check (length(dedupe_key) > 0),
  constraint events_tenant_dedupe_uniq   unique (tenant_id, dedupe_key)
);
create index if not exists events_site_device_ts_idx on events(site_id, device_ts desc);
create index if not exists events_tenant_recv_idx    on events(tenant_id, received_at desc);
create index if not exists events_agent_idx          on events(agent_id);

-- ---------------------------------------------------------------------
-- RLS on, no policies: no client role can touch these tables directly.
-- All agent traffic goes through the functions in 0004.
-- ---------------------------------------------------------------------
alter table tenants          enable row level security;
alter table sites            enable row level security;
alter table agents           enable row level security;
alter table enrollment_codes enable row level security;
alter table cameras          enable row level security;
alter table events           enable row level security;
