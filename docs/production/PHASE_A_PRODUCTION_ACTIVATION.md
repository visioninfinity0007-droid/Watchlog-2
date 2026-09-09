# Phase A — production activation record + recovery levers

Captures what went live in the Phase-A "Health Foundation" activation (2026-09-08) and exactly how to
recover each layer. Companion to [DATABASE_MIGRATIONS.md](../runbooks/DATABASE_MIGRATIONS.md) (migration
integrity/drift) and [RC_0.3.5_FIELD_UPGRADE.md](../runbooks/RC_0.3.5_FIELD_UPGRADE.md) (the agent).

## What went live

| Layer | State | Evidence |
|---|---|---|
| Gated code | `feat/phase-a-health-foundation` @ `e345d0a9effd3af8f7d3ded6d46f0f56bebdfa71` | fork CI + disposable-PG integration + Windows Security Gate + RC build all green on this SHA |
| Production DB | Supabase `oyvgubyxmjlijiczjona`, migrations `0042–0048` applied (ledger 39 → 46) | applied one-at-a-time, allowlisted, `0001–0039` untouched/never re-hashed |
| Health runtime | tables `camera_health`/`nvr_health`/`operational_faults` (+transitions), RPCs `wl_reconcile_site_faults`/`wl_sweep_faults`/`wl_ack_fault`/`wl_site_health_snapshot`; pg_cron `watchlog-agent-watchdog` + `watchlog-fault-sweep` (1/min) | live; tenant-scoped snapshot verified; RC8 agent unaffected |
| Portal | Coolify `watchlog-portal-git` deployed from `main` = `e345d0a`, `running:healthy`, serving the "Operational health" section | deploy finished; served JS contains `wl_site_health_snapshot` |
| Installer | `…/downloads/watchlog/0.3.5/WatchLog-Setup.exe` published, host-verified SHA-256 `A38AF34…D0BE6`, **unsigned** | `/latest/` and `NEXT_PUBLIC_INSTALLER_URL` deliberately unchanged |

Recovery point before the DB apply: read-only snapshot captured **2026-09-08T10:32:16 UTC** (pre-apply
head `0039`; 34 tables / 118 functions / 1 cron). Managed PITR could not be confirmed, so recovery is
self-controlled (accepted) and rests on the additive/idempotent nature of `0042–0048` + the snapshot.

## Recovery levers

**Database.** `0042–0048` are additive (new tables/functions/enums/cron) and idempotent
(`create … if not exists`, `create or replace`), so they can be re-applied safely and reverted
deterministically:
- Function/RPC regression → re-apply the previous `create or replace` for that function (prior migration).
- Cron misbehaviour → `select cron.unschedule('watchlog-fault-sweep');` (and/or `watchlog-agent-watchdog`)
  to stop server-side derivation without touching data.
- New tables are inert unless an agent/cron writes to them; dropping them (last resort) is deterministic
  because nothing in `0001–0039` references them.
- The migration runner is now **fail-closed** (see the migrations runbook): it will never silently
  re-run an applied migration, so a recovery re-apply is a deliberate `--force` or a new migration.

**Portal.** Static export from `main` via Coolify `watchlog-portal-git`. Roll back by redeploying the
previous commit (Coolify → the resource → Deployments → redeploy the prior SHA), then verify the served
build. No DB coupling — the portal only reads via RPCs.

**Agent (field).** Reinstall the prior known-good version; `C:\ProgramData\WatchLog\` state is
backward-compatible (shared encrypted credential store). Full procedure + the "installed but not
reporting" fix in the field-upgrade runbook. An agent rollback reverses no server state — the health
tables simply stop receiving data and the site reads UNKNOWN.

## Still gated (separate approvals)

Authenticode signing of the installer; `/latest/` promotion; the branded-domain cutover
(see [DOMAIN_CUTOVER.md](../runbooks/DOMAIN_CUTOVER.md)); PR #36 Security-Intelligence deploy.
