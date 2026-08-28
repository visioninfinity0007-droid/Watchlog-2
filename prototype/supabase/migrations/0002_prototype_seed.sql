-- =====================================================================
-- WatchLog prototype — seed: one tenant, one site, two enrollment codes
-- =====================================================================
-- Fixed UUIDs so the README, the viewer and any hand-written SQL can
-- refer to them without a lookup. Re-runnable.
-- =====================================================================

insert into tenants (id, name) values
  ('00000000-0000-4000-8000-000000000001', 'AKSS (prototype)')
on conflict (id) do nothing;

insert into sites (id, tenant_id, name, timezone) values
  ('00000000-0000-4000-8000-000000000002',
   '00000000-0000-4000-8000-000000000001',
   'Prototype Site A', 'Asia/Karachi')
on conflict (id) do nothing;

-- Two codes: one for the dev box, one for the second machine in Phase 6.
insert into enrollment_codes (code, tenant_id, site_id, expires_at) values
  ('WL-PROTO-DEV-0001',
   '00000000-0000-4000-8000-000000000001',
   '00000000-0000-4000-8000-000000000002',
   now() + interval '7 days'),
  ('WL-PROTO-EXE-0001',
   '00000000-0000-4000-8000-000000000001',
   '00000000-0000-4000-8000-000000000002',
   now() + interval '7 days')
on conflict (code) do nothing;

-- ---------------------------------------------------------------------
-- Mint another code later (run ad hoc, not part of the migration):
--
--   insert into enrollment_codes (code, tenant_id, site_id, expires_at)
--   values ('WL-PROTO-' || upper(encode(gen_random_bytes(4), 'hex')),
--           '00000000-0000-4000-8000-000000000001',
--           '00000000-0000-4000-8000-000000000002',
--           now() + interval '1 day')
--   returning code;
-- ---------------------------------------------------------------------
