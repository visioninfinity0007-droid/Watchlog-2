-- =====================================================================
-- 0016 — Security foundation (P1)
--
-- 1. Lock the migration ledger table. `schema_migrations` was created by
--    the migration runner with default grants and no RLS, so `anon` could
--    read the schema-evolution history and even INSERT rows. It holds no
--    tenant data, but anon must not read or write any table. The runner
--    connects as the table owner (postgres), which bypasses RLS and keeps
--    its grants, so applying this does not break future migrations.
--
-- 2. Add covering indexes for the foreign keys on the tables that actually
--    grow (events, snapshots, agents). The tiny config tables are left
--    unindexed on purpose — an index there costs more than it saves.
--
-- Non-destructive and idempotent.
-- Rollback: `alter table public.schema_migrations disable row level
-- security; grant ... ;` and `drop index ...` — none of this touches data.
-- =====================================================================

alter table public.schema_migrations enable row level security;
revoke all on public.schema_migrations from anon, public;

create index if not exists events_camera_idx   on public.events    (camera_id);
create index if not exists snapshots_site_idx   on public.snapshots (site_id);
create index if not exists snapshots_camera_idx on public.snapshots (camera_id);
create index if not exists agents_site_idx      on public.agents    (site_id);
