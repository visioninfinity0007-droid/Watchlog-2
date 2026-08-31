# WatchLog — Decision Log

Concise architecture decisions taken during production completion. Newest first.
Format: Decision · Reason · Evidence · Rollback.

---

### 2026-08-31 · P0 — Merge audit PR #1 into main before building
- **Decision:** merge the docs-only audit PR into `main`, branch `production/watchlog-end-to-end` from it.
- **Reason:** the audit is the agreed baseline; every phase references it. Keeping it on `main` makes
  the baseline canonical and the working branch traceable.
- **Evidence:** PR #1 was clean, docs-only, mergeable (3 files, 0 code).
- **Rollback:** revert the merge commit `70cd7d6`; audit content is immutable history regardless.

### 2026-08-31 · P1 — Kill self-service paid state; make billing state webhook-authoritative
- **Decision:** `wl_set_plan` will become **request-only** (a tenant owner may request a plan / start a
  checkout) and will **not** be able to set `subscription_status` to a paid/active value. Authoritative
  paid state is written only by an internal, non-customer path (Switch webhook / admin), keyed to
  verified payment events.
- **Reason:** SOW billing-security invariant D — customers never authoritatively mark themselves paid.
- **Evidence:** audit showed `wl_set_plan(p_plan,p_status)` lets any owner set `subscription_status`
  (AKSS tenant is already `starter/active`, no payment).
- **Rollback:** the superseding migration keeps the old function body in a comment; a one-line migration
  restores it if billing is not yet live and manual plan-setting is temporarily needed.

### 2026-08-31 · P1 — Reconcile migrations 0010–0015 into the ledger WITHOUT replay
- **Decision:** insert ledger rows for 0010–0015 (recording their real file sha256) rather than
  re-running them; make `apply_migrations.py` treat an already-present object set as applied.
- **Reason:** the objects are already live; replaying `create`/`alter` could error or, worse, be
  destructive. The ledger must simply reflect reality.
- **Evidence:** audit — ledger stops at 0009; 0010–0015 objects all exist live.
- **Rollback:** ledger rows are additive; deleting the 6 inserted rows restores the prior ledger.

### 2026-08-31 · P1 — Tighten 4 `public`-role RLS policies to `authenticated`
- **Decision:** re-declare the SELECT policies on `push_sources`, `report_recipients`, `invitations`,
  `report_deliveries` for role `authenticated` (same `wl_is_member` USING clause).
- **Reason:** defense in depth + consistency with the other 8 policies; today they rely solely on the
  USING clause holding for anon.
- **Evidence:** audit — these 4 are the only `public`-role policies; anon already returns `[]` but the
  grant surface is unnecessarily broad.
- **Rollback:** re-declare for `public`; behavior is identical for real users.

### 2026-08-31 · P1 — Lock `schema_migrations` (RLS on + revoke anon/public writes)
- **Decision:** enable RLS with no policy on `schema_migrations` and `REVOKE ALL … FROM anon, public`
  (keep owner/service access for the migration runner).
- **Reason:** anon must not read the schema-evolution ledger or write to any table.
- **Evidence:** audit — `has_table_privilege('anon',…,'INSERT')` was true.
- **Rollback:** re-grant; low risk (table holds only migration filenames/hashes).
