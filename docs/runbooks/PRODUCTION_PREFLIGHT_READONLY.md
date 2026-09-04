# WatchLog read-only production preflight

This runbook is the safe first step before the bounded production migration procedure in `PRODUCTION_PARITY_0024_0036.md`.

It does **not** apply migrations. It proves target identity and current schema/data state inside a PostgreSQL `READ ONLY` transaction.

## Hard target

Expected WatchLog Supabase project ref:

`oyvgubyxmjlijiczjona`

The preflight tool refuses any other `SUPABASE_PROJECT_REF` and also checks direct database host / session-pooler user metadata when those values embed a project ref.

Do not change the expected project ref to make a different database pass.

## Requirements

Use a trusted current checkout of `main` and a protected operator shell.

Install the database driver:

```bash
python -m pip install 'psycopg[binary]'
```

Provide connection values only through environment variables:

```text
SUPABASE_PROJECT_REF
SUPABASE_DB_HOST
SUPABASE_DB_PORT        # optional; default 5432
SUPABASE_DB_USER
SUPABASE_DB_PASSWORD
SUPABASE_DB_NAME        # optional; default postgres
```

Never commit those values and never paste the database password into a ticket, PR, report, or command argument.

## Run

```bash
python tools/watchlog_production_preflight.py \
  --admin-email '<ADMIN_EMAIL>' \
  --output watchlog-production-preflight.json
```

`--admin-email` is optional. When supplied, the report checks only whether that Auth user exists, whether its email is confirmed, and whether it has a platform role. It never reads or prints a password.

The tool starts:

```sql
begin transaction read only;
```

and explicitly rolls the transaction back after the report. If PostgreSQL does not report `transaction_read_only = on`, the tool aborts.

## Report contents

The JSON report contains:

- project ref supplied by the protected environment;
- database identity and server version;
- proof that the transaction is read-only;
- core WatchLog object markers;
- reliable 0023 markers;
- selected 0024/0029/0030/0031/0033/0036 markers;
- tenant/site/camera/Auth-user row counts where available;
- `public.schema_migrations` presence;
- migration-ledger column shape and rows only when the expected columns exist;
- optional platform-admin existence/role state;
- a conservative boundary classification.

It never includes database credentials, API keys, Auth passwords, recorder credentials, or service secrets.

## Boundary classifications

### `0023_exact_candidate`

The reliable 0023 functions resolve and the selected later markers are absent.

This is consistent with the historical production evidence, but still does not itself authorize DDL. Continue with the backup gate and `PRODUCTION_PARITY_0024_0036.md`.

### `partial_after_0023_stop_and_reconcile`

At least one later marker exists but the expected later set is incomplete.

**STOP.** Determine exactly what was applied. Do not replay the full 0024–0036 train.

### `0036_candidate_requires_authz_smoke`

The selected structural markers are consistent with the later train being present.

Do not reapply migrations. Continue with structural verification, authorization tests, Security Advisor, platform-owner verification, and portal smoke from `PRODUCTION_PARITY_0024_0036.md`.

### `inconsistent_or_unknown_stop_and_reconcile`

Later markers exist without the reliable 0023 marker pair.

**STOP.** Treat the database as drifted/unknown until reconciled.

### `before_0023_or_unknown_stop_and_reconcile`

Neither the reliable 0023 pair nor later markers provide the expected boundary.

**STOP.** This is not the bounded 0024–0036 production procedure.

## Migration ledger interpretation

If `public.schema_migrations` exists, the preflight checks whether it contains the expected `filename`, `sha256`, and `applied_at` columns before reading rows.

- `present=false`: preserve this as evidence and use the missing-ledger path in `PRODUCTION_PARITY_0024_0036.md`.
- `present=true, shape_ok=false`: **STOP** and reconcile the ledger before relying on it.
- `present=true, shape_ok=true`: compare the ledger to the current checkout and the schema boundary before using the generic migration runner.

Do not fabricate historical migration rows.

## What this tool intentionally cannot do

It cannot:

- create or alter tables;
- create or replace functions;
- change RLS policies;
- insert/update/delete application data;
- create Auth users;
- assign a platform role;
- apply migrations;
- repair schema drift;
- declare production aligned.

Those actions require the separate guarded migration/auth procedure and its acceptance evidence.

## Required evidence to retain

Keep the preflight JSON with the production execution record together with:

- repository commit SHA;
- project ref;
- operator/date;
- backup/snapshot identifier;
- exact migrations applied or proven already present;
- post-migration structural verification;
- authorization test output;
- Supabase Security Advisor results;
- portal/admin smoke results.

No `production aligned` claim is valid from the preflight report alone.
