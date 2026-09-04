# WatchLog production parity handoff: 0024 -> 0036

This is the bounded production procedure for bringing the WatchLog database and platform-admin path from the last proven `0023` boundary to the current repository schema through `0036`.

It is intentionally stricter than the generic migration runner because the latest operator evidence came from a database where later Analytics Studio / Platform Admin objects were absent, while the ChatGPT Supabase connector exposed a different project. **Never substitute another Supabase project because WatchLog is unavailable in a tool.**

The historical `0023` boundary is evidence, not permission to skip a fresh preflight. Another operator may have changed production since the last check.

## Hard target guard

The intended WatchLog Supabase project ref is:

`oyvgubyxmjlijiczjona`

Known unrelated project that must not be used for this work:

`jssitaduuhjvyznldfoc` (`Al khalid`)

Before any DDL, prove the target belongs to WatchLog using the Supabase dashboard/project URL or the deployed WatchLog environment and record the project ref. If it is not the WatchLog ref above, **STOP**.

Do not infer project identity from `current_database() = 'postgres'`; that is normal for Supabase projects and is not a project identifier.

## 0. Repository and backup gate

Use current `main`. Do not copy SQL from chat history or an old checkout.

Record:

- repository commit SHA;
- WatchLog project ref;
- operator and date/time;
- database backup/snapshot identifier.

Create a recoverable backup/snapshot before applying anything. If a reliable backup cannot be produced, **STOP**.

Do not use `apply_migrations.py --force`.

## 1. Non-destructive boundary diagnostic

Run this first in the WatchLog SQL editor or trusted Postgres session. It changes nothing.

```sql
select
  current_database() as database_name,
  (select count(*) from auth.users) as auth_users,
  to_regclass('public.tenants') as tenants,
  to_regclass('public.sites') as sites,
  to_regclass('public.cameras') as cameras,
  to_regclass('public.monitoring_rules') as monitoring_rules,
  to_regclass('public.platform_admins') as platform_admins,
  exists (
    select 1 from information_schema.columns
    where table_schema='public' and table_name='sites' and column_name='site_type'
  ) as has_site_type,
  exists (
    select 1 from information_schema.columns
    where table_schema='public' and table_name='cameras' and column_name='purpose'
  ) as has_camera_purpose,
  exists (
    select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
    where n.nspname='public' and p.proname='wl_platform_me'
  ) as has_platform_me,
  exists (
    select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
    where n.nspname='public' and p.proname='wl_site_health_details'
  ) as has_site_health_details,
  exists (
    select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
    where n.nspname='public' and p.proname='wl_is_owner'
  ) as has_billing_owner_authz;
```

Last proven operator evidence showed `tenants`, `sites`, and `cameras` present while `monitoring_rules`, `platform_admins`, the 0024 site/camera columns, `wl_platform_me`, and later Site Health / billing authorization functions were absent.

Also check the reliable `0023` markers:

```sql
select
  to_regprocedure('public.wl_entitlement()') as migration_0023_entitlement,
  to_regprocedure('public.wl_reporting_enabled(uuid)') as migration_0023_reporting;
```

If both resolve and all 0024+ markers above are absent, the evidence is consistent with `0023` applied and `0024` not applied.

If objects indicate a partially applied later train, do not replay all files. Determine the exact last successfully applied migration before proceeding.

## 2. Migration ledger check — important runner caveat

WatchLog's repository migration runner tracks `public.schema_migrations`, not Supabase CLI's `supabase_migrations.schema_migrations`.

Read the ledger existence without changing anything:

```sql
select to_regclass('public.schema_migrations') as app_migration_ledger;
```

### If `public.schema_migrations` exists

Inspect it directly:

```sql
select filename, sha256, applied_at
from public.schema_migrations
order by filename;
```

Only if this ledger is already present and trustworthy through the proven migration boundary should the generic repository runner be used.

From a trusted checkout:

```bash
python -m pip install 'psycopg[binary]'
python prototype/supabase/apply_migrations.py --status
```

A `CHANGED SINCE APPLIED` result is a **STOP** condition. Investigate drift; do not hide it with `--force`.

### If `public.schema_migrations` is missing or incomplete

**Do not run `python prototype/supabase/apply_migrations.py --status` yet.**

The current generic runner creates `schema_migrations` before printing status. If no ledger exists, it will then consider every repository migration from `0001` onward pending. A subsequent normal run could therefore attempt to replay the entire historical migration train. That is not the bounded production procedure for this database.

In this case:

- preserve the missing-ledger fact as evidence;
- use the schema-object boundary proof;
- apply only the genuinely pending SQL files individually, in order, from the trusted current checkout;
- do not fabricate historical ledger rows merely to make status look clean.

## 3. Exact ordered migration train

Current repository order for this bounded train is:

1. `0024_analytics_studio.sql`
2. `0025_analytics_agent_bootstrap.sql`
3. `0026_checkout_and_daily_analytics.sql`
4. `0027_daily_report_analytics.sql`
5. `0028_analytics_recommendations_and_schedules.sql`
6. `0029_platform_admin.sql`
7. `0030_analytics_semantics_authz.sql`
8. `0031_report_recipient_destinations.sql`
9. `0032_portal_operational_authz.sql`
10. `0033_site_health_details.sql`
11. `0034_enrollment_code_read_authz.sql`
12. `0035_billing_read_authz.sql`
13. `0036_billing_owner_policy_execution.sql`

Do not skip `0036`. It completes legitimate Owner billing-policy execution after the `0035` authorization changes.

### Application path A — trustworthy existing ledger

If `public.schema_migrations` already exists, correctly records the earlier train, and `--status` shows exactly the expected pending files, use:

```bash
python prototype/supabase/apply_migrations.py --status
python prototype/supabase/apply_migrations.py
python prototype/supabase/apply_migrations.py --status
```

Before the apply step, confirm the status output does **not** show `0001` through the proven boundary as pending.

### Application path B — missing/incomplete ledger or SQL Editor only

Apply only the genuinely pending files individually from `prototype/supabase/migrations/`, in the exact order above.

For each file:

1. open the file from the current trusted checkout;
2. execute the complete file in one SQL Editor operation / trusted DB session;
3. record success before moving to the next file;
4. stop on the first error;
5. do not continue to later migrations after a failure.

Do not manually merge, reorder, shorten or “fix up” migration SQL during production execution. Diagnose the first failure against source before any change.

## 4. Structural verification after 0036

The following signatures are verified against current `main` migrations:

```sql
select
  to_regclass('public.monitoring_rules') as monitoring_rules,
  to_regclass('public.platform_admins') as platform_admins,
  to_regprocedure('public.wl_platform_me()') as wl_platform_me,
  to_regprocedure('public.wl_analytics_studio()') as wl_analytics_studio,
  to_regprocedure('public.wl_analytics_overview(integer,uuid)') as wl_analytics_overview,
  to_regprocedure('public.wl_add_recipient_v2(text,text,text,text,uuid)') as wl_add_recipient_v2,
  to_regprocedure('public.wl_add_site(text,text)') as wl_add_site,
  to_regprocedure('public.wl_issue_code(uuid,integer)') as wl_issue_code,
  to_regprocedure('public.wl_site_health_details(integer)') as wl_site_health_details,
  to_regprocedure('public.wl_sites()') as wl_sites,
  to_regprocedure('public.wl_billing_overview()') as wl_billing_overview,
  to_regprocedure('public.wl_is_owner(uuid)') as wl_is_owner;
```

Every expected relation/function must resolve.

Also confirm the 0024 columns:

```sql
select
  exists (
    select 1 from information_schema.columns
    where table_schema='public' and table_name='sites' and column_name='site_type'
  ) as has_site_type,
  exists (
    select 1 from information_schema.columns
    where table_schema='public' and table_name='cameras' and column_name='purpose'
  ) as has_camera_purpose;
```

Both must be true.

## 5. Platform-owner bootstrap timing

Do not create or populate `platform_admins` before `0029_platform_admin.sql` has applied successfully.

The intended administrator must already exist in `auth.users`, or be created through the normal Supabase Auth/admin path. Do not hand-edit `auth.users.encrypted_password`.

After the full train through `0036` is proven, promote the intended account using the repository tool from a protected operator environment:

```bash
python tools/bootstrap_platform_admin.py --email '<ADMIN_EMAIL>' --role platform_owner
```

The tool requires the database connection in environment variables and does not accept a password on the command line. Its optional `--create-user` path additionally requires protected Auth-admin environment variables; prefer normal Supabase Auth user creation when operating manually.

Or, from a protected DB-owner session after confirming the Auth user exists:

```sql
insert into public.platform_admins(user_id, role)
select id, 'platform_owner'
from auth.users
where lower(email)=lower('<ADMIN_EMAIL>')
on conflict (user_id) do update set role='platform_owner';
```

Verify:

```sql
select u.email, u.email_confirmed_at, pa.role
from auth.users u
left join public.platform_admins pa on pa.user_id=u.id
where lower(u.email)=lower('<ADMIN_EMAIL>');
```

Expected role: `platform_owner`.

## 6. Authorization and tenant-isolation proof

Use disposable users in a test tenant, not real customer accounts, for destructive tests.

Required role matrix:

- Owner: operational writes + billing detail for own tenant.
- Admin: operational writes, no detailed financial access.
- Viewer: read product surfaces, no configuration writers, no enrollment code values, no detailed billing rows.
- Normal tenant user: no Platform Admin data or cross-tenant admin RPC access.

Run supported live gates against the correct test target:

```bash
python prototype/tests/test_tenant_isolation.py
python prototype/tests/test_billing_authz.py
python prototype/tests/test_team_and_trial.py
python prototype/tests/test_platform_admin_authz.py
```

Record pass counts/output. If a test requires unavailable environment values, document it as not run rather than treating it as passed.

Specifically prove:

- Owner/Admin can add sites and issue codes while Viewer cannot;
- Viewer can see `has_open_code` but not its value;
- Owner can read only own-tenant billing detail while Admin/Viewer cannot;
- `wl_site_health_details` cannot leak another tenant;
- `wl_platform_me()` returns null for a normal tenant user;
- cross-tenant platform RPCs reject tenant users.

## 7. SECURITY DEFINER / grants / advisor gate

After the migration train:

- review SECURITY DEFINER functions for pinned/safe `search_path` behavior;
- verify `anon` has no unintended app-table read/write grants;
- verify `authenticated` does not gain direct writes that bypass RPC authorization;
- run Supabase Security Advisor and record remaining findings.

Any critical RLS or definer issue blocks staging.

## 8. Portal/auth smoke

Only after database parity is green:

1. sign in as a normal tenant Owner;
2. smoke Overview, Incidents, Site Health, Analytics, Reports, Team and Settings;
3. verify Analytics Studio loads without schema-cache errors;
4. verify billing/entitlement state is coherent;
5. sign in as the platform owner and verify `/admin/` plus Tenants, Operations, Billing, Admins and Audit;
6. verify customer/platform switching does not change authorization roles;
7. sign in as a normal tenant user and prove `/admin/` data remains denied.

If PostgREST appears stale after SQL is proven, refresh/reload schema cache only after confirming the migration objects exist.

## 9. Execution report to return

The operator/Claude handoff must return, in one report:

- project ref used;
- backup/snapshot identifier;
- preflight diagnostic output;
- migration ledger presence and, if present, status output;
- chosen application path (A or B);
- exact migrations applied vs already present;
- first error if any;
- post-0036 structural verification;
- platform-owner verification;
- authorization test results;
- Security Advisor result;
- portal/admin smoke result;
- unresolved blockers.

Do not report `production aligned` unless every applicable gate above has evidence.
