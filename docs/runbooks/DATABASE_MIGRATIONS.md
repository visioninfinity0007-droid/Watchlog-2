# Runbook — Database migrations

WatchLog's schema is a numbered chain of SQL files applied by a small runner that records what has
run. There is no ORM and no auto-migrate on deploy — migrations are applied deliberately.

- **Files:** `prototype/supabase/migrations/NNNN_name.sql` (0001 … current).
- **Runner:** `prototype/supabase/apply_migrations.py` (imports `psycopg` lazily, reads `.env`).
- **Ledger:** table `schema_migrations (filename, sha256, applied_at)`. Locked down in 0016 (RLS on,
  no anon access); the runner connects as the postgres owner and is unaffected.

---

## Integrity model (read this first)

The runner is **fail-closed** and its checksums are **line-ending-normalized**:

- **Normalized checksums.** The `sha256` is computed over content with CRLF/CR folded to LF
  (`normalized_sha`). A migration recorded on a Linux/LF checkout and later read back from a Windows/CRLF
  checkout hashes identically — a line-ending flip can **never** look like a changed migration. This is
  what bit the 0042–0048 production apply (0016–0023/0039 showed spurious "CHANGED SINCE APPLIED").
- **Fail-closed on drift.** An already-applied migration whose normalized checksum no longer matches the
  recorded one is **DRIFT**. A bare apply refuses to proceed on *any* drift and runs **nothing** — not
  even genuinely pending migrations — until an operator resolves it explicitly. A changed applied
  migration is therefore **never re-run automatically** (the old runner silently re-ran it).

Proven by `prototype/tests/test_apply_migrations.py` (pure logic) and, on a disposable Postgres in CI,
`prototype/tests/test_apply_migrations_pg.py` (CRLF idempotence + drift-fails-closed + rehash + force).

## Status / apply

```bash
python prototype/supabase/apply_migrations.py --status   # list state, change nothing
python prototype/supabase/apply_migrations.py            # apply PENDING (fails closed on drift)
python prototype/supabase/apply_migrations.py --rehash   # re-baseline drifted checksums, runs NO SQL
python prototype/supabase/apply_migrations.py --force    # re-execute EVERY migration (dangerous)
```

`--status` prints `PENDING` / `applied` / `DRIFT` per file. A bare apply runs pending files in order and
records each with its normalized checksum. CI runs `tools/lint_migrations.py` (sequential numbering,
non-empty, no BOM, destructive heads-up).

## Resolving DRIFT (checksum mismatch on an applied migration)

A bare apply that hits DRIFT exits non-zero and lists the offending files without touching the DB. Do NOT
reach for `--force` reflexively. Decide which case it is:

1. **`git diff` shows only line-ending changes (CRLF↔LF).** The content is unchanged. Re-baseline the
   stored checksum to the normalized scheme — this updates the ledger row only and runs **no SQL**:
   ```bash
   python prototype/supabase/apply_migrations.py --rehash
   ```
   (This is also the one-time step to migrate any legacy pre-normalization checksums.)
2. **The SQL genuinely changed and you intend to re-run it.** Only if the migration is fully idempotent:
   `--force` re-executes every migration and rewrites checksums. Prefer instead writing a **new** numbered
   migration for the change and restoring the old file.
3. **You did not expect a change.** Someone edited an applied migration in place — restore the file to
   what was applied (`git checkout -- <file>`) and investigate before doing anything else.

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
