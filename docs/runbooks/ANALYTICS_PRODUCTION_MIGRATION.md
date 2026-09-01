# WatchLog portal + Analytics production migration

This runbook resolves the production Analytics schema-cache failure and keeps the
portal, reporting model, Site Health detail model and operational authorization
in one release train.

The frontend must not be deployed ahead of the database. Apply the complete
ordered migration train before merging/deploying the portal alignment branch.

## Preconditions

- Run from a trusted admin/deployment machine with the WatchLog production DB
  credentials in the repository-root `.env` (gitignored).
- Confirm the target Supabase project is **WatchLog**, project ref
  `oyvgubyxmjlijiczjona`.
- Never run these migrations against the separate Al khalid Supabase project.
- Do not use `--force` to hide migration checksum drift.

## 1. Preflight

```bash
python prototype/supabase/apply_migrations.py --status
```

Expected: every migration already deployed shows `applied`; unreleased files show
`PENDING`. Any `CHANGED SINCE APPLIED` state is a stop condition.

The release train is:

- `0024_analytics_studio.sql`
- `0025_analytics_agent_bootstrap.sql`
- `0026_checkout_and_daily_analytics.sql`
- `0027_daily_report_analytics.sql`
- `0028_analytics_recommendations_and_schedules.sql`
- `0029_platform_admin.sql`
- `0030_analytics_semantics_authz.sql`
- `0031_report_recipient_destinations.sql`
- `0032_portal_operational_authz.sql`
- `0033_site_health_details.sql`
- `0034_enrollment_code_read_authz.sql`

Do not paste only `wl_analytics_studio` into SQL Editor. The portal depends on the
tables, policies, reporting endpoint model, Site Health detail API and
authorization hardening created by the full ordered train.

## 2. Apply

```bash
python prototype/supabase/apply_migrations.py
python prototype/supabase/apply_migrations.py --status
```

All files through `0034` must report `applied` with repository-matching checksums.

## 3. Database smoke test

From the trusted database session:

```sql
select to_regprocedure('public.wl_analytics_studio()');
select to_regprocedure('public.wl_analytics_overview(integer,uuid)');
select to_regprocedure('public.wl_add_recipient_v2(text,text,text,text,uuid)');
select to_regprocedure('public.wl_add_site(text,text)');
select to_regprocedure('public.wl_issue_code(uuid,integer)');
select to_regprocedure('public.wl_site_health_details(integer)');
select to_regprocedure('public.wl_sites()');
```

Every row must resolve to a function. Then, as a normal authenticated tenant user,
verify:

- `wl_analytics_catalog()` returns site types, camera purposes, semantic goals,
  purpose recommendations and Site Health under `always_on`.
- `wl_analytics_studio()` returns tenant sites and `can_manage`.
- `wl_analytics_overview(7, null)` returns summary/daily/by-rule payloads.
- `wl_site_health_details(1)` returns only the caller tenant's cameras and recent
  fault events with site identity.
- `wl_sites()` reports `has_open_code` to all members but returns the actual
  `open_code` value only to Owner/Admin.
- `/analytics/`, `/analytics/studio/`, `/analytics/schedules/` and `/site-health/`
  load without schema-cache errors.

If SQL is present but PostgREST still reports an old cache, refresh/reload
PostgREST only **after** proving the migration is applied.

## 4. Authorization proof

Use disposable users in a test tenant:

- Owner: can add sites, issue enrollment codes, manage report recipients and
  configure Analytics.
- Admin: can perform the same operational actions except owner-only billing.
- Viewer: can read Overview, Site Health, Incidents, Analytics, Reports, Team and
  Settings but receives permission denied from all configuration writers.
- Viewer specifically cannot call `wl_add_site`, `wl_issue_code`,
  `wl_add_recipient_v2`, Analytics writers or team writers successfully.
- Viewer can see whether a site has an open enrollment code but cannot read the
  code value itself.
- Billing checkout/cancellation remains Owner-only.
- A normal tenant user receives no Platform Admin data.
- `wl_site_health_details` never returns cameras or fault rows from another
  tenant.

Do not test destructive authorization assumptions against a real customer.

## 5. Reporting endpoint proof

Create a test recipient with `channel='both'` through `wl_add_recipient_v2` and
verify the database contains **two** rows for that person/site:

1. `channel='whatsapp'`, phone-number destination.
2. `channel='email'`, email destination.

Run the reporter in dry-run mode and verify each provider is paired with its own
address. There must be no remaining `channel='both'` row after migration.

## 6. Analytics semantic proof

Create separate rules on a test camera:

1. Visitor Flow, person, line crossing.
2. Boundary Monitoring, person, line crossing.
3. Vehicle Flow, car/motorcycle, line crossing.

Ingest one event for each and verify:

- Visitor event increments only `visitor_flow` / visitor metrics.
- Boundary event remains `boundary_monitoring` and does not inflate visitor flow.
- Vehicle event increments only vehicle flow.
- Site Health appears without a video-inference `health` monitoring rule.

## 7. Agent contract

Run one enrolled Site Agent against the migrated test site and verify:

- config poll returns `analytic_key` on enabled rules;
- config version changes after a portal edit;
- one configuration-still request completes;
- one analytics measurement is accepted and appears in the portal;
- reboot/autostart returns the agent to online without repeating enrollment.

## 8. Refresh the demo tenant

After migrations are applied, from the trusted deployment checkout run:

```bash
python tools/seed_demo.py
```

The demo must show exactly three curated sites with recent Site Agent contact,
meaningful camera names, semantic Analytics history and synthetic report delivery
history. The seed script is deliberately scoped to the tenant belonging to
`PORTAL_DEMO_EMAIL`.

Expected controlled exceptions are part of the walkthrough, not a failed seed:

- Korangi Warehouse / Rear Perimeter has no activity in the last 24 hours.
- Korangi Warehouse / Loading Bay has one synthetic `video_loss` fault in the
  last 24 hours.

All sample stills/events remain explicitly tagged as demo/synthetic data.

## 9. Deployment order

1. Production database through `0034`.
2. Run schema/auth/reporting/Site Health smoke tests.
3. Refresh the dedicated demo tenant.
4. Deploy the matching portal build.
5. Deploy agent/reporting services if their build changed.
6. Build the canonical NSIS `WatchLog-Setup.exe` on Windows and perform a clean
   install, reboot and autostart smoke test.
7. Smoke-test Overview, Incidents, Site Health, Analytics, Reports, Team and both
   Settings sections.

Never reverse database and portal steps for a migration-dependent release.

## 10. Release blockers that are not software defects

Do not disguise external release prerequisites as completed work:

- WatchLog production Supabase project access is required to apply this train.
- A Windows build host with NSIS is required for the real installer artifact.
- A code-signing certificate is required to remove the unsigned SmartScreen
  warning on fresh Windows installations.
- Real recorder/hardware acceptance still requires the contracted site hardware.
