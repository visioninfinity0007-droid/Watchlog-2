# Runbook — Database migrations

WatchLog's schema is a numbered chain of SQL files applied by a small runner that records what has
run. There is no ORM and no auto-migrate on deploy — migrations are applied deliberately.

- **Files:** `prototype/supabase/migrations/NNNN_name.sql` (0001 … current).
- **Runner:** `prototype/supabase/apply_migrations.py` (uses `psycopg`, reads `.env`).
- **Ledger:** table `schema_migrations (filename, sha256, applied_at)`. Locked down in 0016 (RLS on,
  no anon access); the runner connects as the postgres owner and is unaffected.

---

## Status / apply

```bash
python prototype/supabase/apply_migrations.py --status   # list, change nothing
python prototype/supabase/apply_migrations.py            # apply pending, record them
```

The runner skips a file whose recorded sha256 matches, applies the rest in order, and records each.
CI runs `tools/lint_migrations.py` (sequential numbering, non-empty, no BOM, destructive heads-up).

## Adding a migration

1. Create the next number: `0019_your_change.sql`.
2. Make it **idempotent** where possible (`create ... if not exists`, `create or replace`,
   `drop ... if exists` before create, guarded `alter`). A migration may be re-read by the runner if
   its sha changes.
3. Include a header comment: what, why, and a **rollback note**.
4. Avoid unguarded destructive statements. If you must `delete`/`drop`, scope it and say so — the
   linter will flag it for review.
5. Test against a scratch/branch DB if the change is non-trivial; then apply to prod deliberately.
6. After applying, verify the objects exist and run `python prototype/tests/test_tenant_isolation.py`
   (must stay ≥9/9).

## The 0010–0015 reconcile (history)

Migrations 0010–0015 were once applied out-of-band and were missing from the ledger. They were
**reconciled** (their real sha256 recorded) **without replay** — replaying 0012 would have re-run a
data `update`. If you ever find the ledger behind reality again: record the already-applied files with
`insert into schema_migrations(filename,sha256) values (...) on conflict do nothing`, then let the
runner apply only the genuinely new files. Never let the runner blindly re-run a file whose objects
already exist unless you know it is fully idempotent.

## Applying to production safely

- Prod is the live Supabase project `oyvgubyxmjlijiczjona`. The same `.env` the runner uses.
- Apply during low traffic. Migrations here are DDL + function replaces (fast); none rebuild large
  tables today.
- **Rollback:** each migration's header carries a rollback note. Function changes roll back by
  re-applying the previous `create or replace`; grants/policies by re-granting; column adds are
  usually left in place (harmless) rather than dropped.

## Do not

- Do not edit the live schema by hand without capturing the exact change as a migration file.
- Do not run migrations automatically from a deploy pipeline against prod — keep it a deliberate step.
