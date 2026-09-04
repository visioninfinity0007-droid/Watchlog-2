# WatchLog Production Parity — 0037 to 0038

This runbook is for the SaaS-operations portal finalization release only.

It does **not** authorize blind migration execution. The production operator must first prove the WatchLog project identity and current schema boundary with the repository's read-only preflight utility.

## Hard target guard

Authoritative WatchLog Supabase project ref:

`oyvgubyxmjlijiczjona`

Never run these steps against:

`jssitaduuhjvyznldfoc` (`Al khalid`)

Stop immediately if the project ref, direct database host or pooler user metadata identifies any other project.

## Files in this release

1. `prototype/supabase/migrations/0037_saas_operations.sql`
2. `prototype/supabase/migrations/0038_customer_account_lifecycle.sql`

0037 adds Customer 360 commercial/support operations: billing profiles, invoices, invoice items, contracts, manual payments, internal support notes, reason-bound Support Mode sessions and audited platform RPCs.

0038 adds a separate customer account lifecycle (`active` / `suspended`) so WatchLog can pause portal/reporting access without deleting memberships, customer data or changing the commercial subscription status.

## 1. Update to the approved release commit

Use the final merged `main` commit for this portal release. Do not run migration files from an unmerged working branch.

```bash
git fetch origin
git checkout main
git pull --ff-only origin main
git log -1 --oneline
```

Record the exact commit SHA in the deployment evidence.

## 2. Run the read-only production preflight

Configure the database connection through the operator's secure environment. Never paste credentials into chat, tickets, commits or command history where avoidable.

```bash
python -m pip install 'psycopg[binary]'
python tools/watchlog_production_preflight.py \
  --admin-email awais.envison@gmail.com \
  --output watchlog-preflight-before-0037-0038.json
```

The tool starts a `READ ONLY` transaction and cannot apply DDL.

Expected safe boundaries for this release are:

- `0036_exact_candidate` → 0037 and 0038 may be considered, after backup.
- `0037_exact_candidate` → only 0038 may be considered, after backup.
- `0038_candidate_requires_authz_smoke` → do not reapply either migration; proceed to verification.

Any boundary containing `stop_and_reconcile` is a hard stop. Investigate the exact schema state before doing anything else.

If `schema_migrations` exists but its expected ledger shape is not trustworthy, stop and reconcile. Do not use the generic migration runner to guess history.

## 3. Capture recovery evidence before DDL

Before applying a pending migration:

- verify a recoverable Supabase/Postgres backup exists;
- record backup timestamp / recovery point;
- record the approved source commit;
- retain the preflight JSON privately;
- capture the pre-migration counts for Auth users, tenants, sites and cameras;
- verify the platform-owner Auth user still exists and is confirmed.

Do not continue if the backup cannot be proven.

## 4. Apply only the proven pending files

### If preflight says `0036_exact_candidate`

Apply, one at a time, in this order:

```text
0037_saas_operations.sql
0038_customer_account_lifecycle.sql
```

Stop on the first error. Do not apply 0038 after an unexplained 0037 failure.

### If preflight says `0037_exact_candidate`

Apply only:

```text
0038_customer_account_lifecycle.sql
```

### If preflight says `0038_candidate_requires_authz_smoke`

Apply nothing.

Use the team's approved Supabase SQL/psql execution path. Do not add `CASCADE`, disable RLS, edit `auth.users`, or weaken grants/policies to force a migration through.

If the production ledger is valid and your approved deployment process records migrations there, record the applied file and exact SHA-256 according to that established process. Do not fabricate ledger entries.

## 5. Immediate structural verification

Run the read-only preflight again:

```bash
python tools/watchlog_production_preflight.py \
  --admin-email awais.envison@gmail.com \
  --output watchlog-preflight-after-0037-0038.json
```

The expected boundary is:

`0038_candidate_requires_authz_smoke`

Compare pre/post row counts. The migration must not unexpectedly remove Auth users, tenants, sites or cameras.

## 6. 0037 authorization and commercial smoke

Use real authenticated sessions for each role. Do not infer authorization from object presence alone.

### Platform Owner / Platform Admin

Prove that authorized platform staff can:

- open Customer 360;
- read the commercial summary;
- update a billing profile with a mandatory audit reason;
- create an invoice with server-calculated totals;
- update invoice status;
- create a contract;
- update contract status;
- record a manual payment;
- add an internal support note;
- start Support Mode with a reason;
- open only their own active Support Mode session;
- exit Support Mode.

Verify the corresponding mutations appear in `platform_admin_audit` with the real platform actor identity and reason.

### Platform Support

Prove platform support can read the supported customer/commercial context and use reason-bound Support Mode, but cannot perform owner/admin-only commercial mutations.

### Tenant Owner

Prove a tenant Owner can call `wl_customer_documents()` and sees only their own invoice/contract history.

Prove customer documents do **not** expose:

- internal support notes;
- platform support sessions;
- platform audit internals;
- other tenants' commercial records.

### Tenant Admin / Viewer and anonymous

Prove they cannot read `wl_customer_documents()` when the function requires Owner access, and cannot execute platform commercial/support mutations.

Also verify browsers still have no direct table write access to the new 0037 tables.

## 7. 0038 account-lifecycle smoke

Use a disposable/test tenant, never the platform-owner account's only path to production administration.

1. Record its current subscription state, members, sites and data counts.
2. As Platform Owner/Admin, call the supported account-status RPC with a clear reason and set the account to `suspended`.
3. Verify the customer is routed to `/account-suspended/` after authentication.
4. Verify ordinary tenant data RPCs fail closed because `wl_my_tenant()` no longer returns an active tenant.
5. Verify scheduled reporting is disabled for that tenant.
6. Verify memberships, sites, cameras and historical data remain present.
7. Verify the commercial subscription status was **not** silently changed to `cancelled` or another billing state.
8. Verify the suspension audit event contains the real platform actor and reason.
9. Reactivate the same tenant through the supported RPC with a reason.
10. Verify normal portal access and reporting eligibility return according to the underlying subscription state.
11. Verify the reactivation audit event.

Do not test suspension by deleting memberships or editing Auth users.

## 8. SECURITY DEFINER and RLS checks

After DDL, verify every new exposed `SECURITY DEFINER` function has a pinned `search_path` and the intended execute grants/revokes.

Verify RLS is enabled on all new 0037 tables and direct browser access remains revoked unless explicitly intended.

Run the Supabase Security Advisor after migration and resolve any new high-severity finding introduced by 0037/0038 before calling the release aligned.

Review the performance advisor as well for material new findings, especially around the new tenant-scoped commercial/support indexes.

## 9. Portal deployment order

The portal code is backwards-aware enough to show a migration-pending state for the new admin tools, but production should be aligned in this order:

1. backup + preflight;
2. apply proven pending DB migrations;
3. authz/lifecycle smoke;
4. deploy the approved `main` portal build;
5. authenticated platform/customer portal smoke.

This avoids exposing operational buttons before their authoritative RPCs exist.

## 10. Live portal acceptance

As `platform_owner`, verify:

- Overview;
- Customers;
- Customer 360;
- Operations;
- Commercial;
- Support;
- Invoice workspace;
- Contract workspace;
- Audit;
- Admins.

In Customer 360 verify the visible actions match role permissions:

- Change plan / subscription state;
- Grant/restart trial;
- Suspend/reactivate customer;
- Send password recovery;
- billing profile;
- invoice creation;
- contract creation;
- manual payment;
- internal support notes;
- Support Mode.

As a real tenant user, verify:

- Overview;
- Incidents;
- Site Health;
- Analytics;
- Reports;
- Team;
- Settings;
- Account & Plan;
- direct `/admin/` denial/redirect.

The customer portal should use WatchLog/customer language and must not expose Supabase, DPAPI, service-role, webhook or other implementation detail.

## 11. Evidence to retain

Record without secrets:

- source `main` commit;
- preflight boundary before DDL;
- backup timestamp/recovery point;
- migrations actually applied (zero, one or two);
- post-preflight boundary;
- pre/post core row counts;
- authenticated role/authz results;
- Support Mode audit evidence;
- suspend/reactivate audit evidence;
- Security Advisor result;
- deployed portal revision;
- live route-smoke result.

## Stop conditions

Stop immediately if:

- project identity is not `oyvgubyxmjlijiczjona`;
- boundary is partial/inconsistent;
- migration ledger shape is untrusted and execution depends on it;
- backup cannot be proven;
- 0037 or 0038 errors unexpectedly;
- RLS/role isolation regresses;
- platform audit attribution is lost;
- suspension deletes/changes customer identity or data unexpectedly;
- another tenant's commercial data becomes visible.

Do not call production aligned until the authenticated smoke and Security Advisor checks pass.
