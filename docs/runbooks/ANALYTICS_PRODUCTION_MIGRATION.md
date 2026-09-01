# WatchLog Analytics production migration

This runbook resolves the production error:

`Could not find the function public.wl_analytics_studio in the schema cache`

The frontend must not be deployed ahead of the database. Apply the complete
ordered migration train before merging/deploying the portal alignment branch.

## Preconditions

- Run from a trusted admin/deployment machine with the WatchLog production DB
  credentials in the repository-root `.env` (gitignored).
- Confirm the target Supabase project is WatchLog, project ref
  `oyvgubyxmjlijiczjona`.
- Do not use `--force` to hide migration checksum drift.

## 1. Preflight

```bash
python prototype/supabase/apply_migrations.py --status
```

Expected: every migration already deployed shows `applied`; Analytics files that
have not yet reached production show `PENDING`. Any `CHANGED SINCE APPLIED` state
is a stop condition and must be reconciled before proceeding.

The Analytics train is:

- `0024_analytics_studio.sql`
- `0025_analytics_agent_bootstrap.sql`
- `0026_checkout_and_daily_analytics.sql`
- `0027_daily_report_analytics.sql`
- `0028_analytics_recommendations_and_schedules.sql`
- `0029_platform_admin.sql`
- `0030_analytics_semantics_authz.sql`

Do not paste only `wl_analytics_studio` into SQL Editor. The functions depend on
the tables, indexes, policies, agent contract and semantic hardening created by
the ordered train.

## 2. Apply

```bash
python prototype/supabase/apply_migrations.py
python prototype/supabase/apply_migrations.py --status
```

All files through `0030` must now report `applied` with repository-matching
checksums.

## 3. Schema/API smoke test

As a normal authenticated tenant user, verify:

- `wl_analytics_catalog()` returns site types, camera purposes, analytics goals,
  purpose recommendations and Site Health under `always_on`.
- `wl_analytics_studio()` returns tenant sites and `can_manage`.
- `wl_analytics_overview(7, null)` returns summary/daily/by-rule payloads.
- `/analytics/`, `/analytics/studio/` and `/analytics/schedules/` load without
  schema-cache errors.

If the SQL is present but PostgREST still reports an old schema cache, wait for
refresh/reload PostgREST only **after** proving the migration is applied.

## 4. Authorization proof

Use disposable test users in one test tenant:

- Owner: can update site type, camera profile, schedules and rules.
- Admin: same configuration rights as Owner.
- Viewer: can read Analytics but every configuration writer returns permission
  denied.
- Normal tenant user: receives no Platform Admin data.

Do not test destructive authorization assumptions against a real customer
account.

## 5. Semantic proof

Create separate rules on a test camera:

1. Visitor Flow, person, line crossing.
2. Boundary Monitoring, person, line crossing.
3. Vehicle Flow, car/motorcycle, line crossing.

Ingest one event for each and verify:

- Visitor event increments only `visitor_flow` / visitor metrics.
- Boundary event remains `boundary_monitoring` and does not inflate visitor
  counts merely because the object is a person.
- Vehicle event increments only vehicle flow.

Verify Site Health appears in the portal without any `health` monitoring rule.

## 6. Agent contract

Run one enrolled Site Agent against the migrated test site and verify:

- config poll returns `analytic_key` on enabled rules;
- config version changes after a portal edit;
- one configuration-still request completes;
- one analytics measurement is accepted and appears in the portal.

## 7. Deployment order

1. Production database through `0030`.
2. Portal build containing the matching frontend.
3. Agent/reporting services if their build changed.
4. Smoke-test Overview, Site Health, Analytics Overview, Studio and Schedules.

Never reverse steps 1 and 2 for a migration-dependent portal release.
