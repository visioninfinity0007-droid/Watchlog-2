# WatchLog — M1 Pre-Deployment Review

**Date:** 2026-09-10 · **Branch:** `feat/m1-final-closure` (pushed to canonical `Alkalid-security/Watchlog` + CI mirror `visioninfinity0007-droid/Watchlog-2`) · **Base:** canonical `origin/main` `243d690` (contains v0.4.1 `5f6783d`), branch is +N/−0.

> Nothing deployed. This is the review gate before the single controlled deployment package.

## 1. Migration diff audit — `0060 → 0064` (reviewed as one change set)
| Check | Finding |
| --- | --- |
| Sequential ordering | 0060..0064 contiguous; `lint_migrations` ok (0001..0064). |
| Idempotency / rerun | `create table if not exists`, `create or replace function`, `add column if not exists`, seed `on conflict do nothing`, `drop constraint if exists`→add. Re-runnable. |
| Destructive statements | None. No DROP TABLE / TRUNCATE / DELETE. Only `drop constraint if exists` (immediately re-added). |
| Ownership / search_path / definer | Every function `security definer set search_path = public`. |
| Grants | Agent RPCs → `anon, authenticated` (auth inside via `wl_auth_agent`); portal RPCs → `authenticated`; capability/coverage read → `authenticated`(+`service_role`). No function left open to `public`. |
| RLS | All new tables (`recorder_*`, `agent_coverage_gaps`, `site_commands`, `site_managed_actions`) `enable row level security` with **no direct policies** — reachable only through the definer functions. |
| Tenant isolation | `wl_site_command_enqueue` / `propose_write` / `approve` / `result` derive tenant from the **site row** and require `wl_platform_role()` or `site.tenant = wl_my_tenant()`. Proven: an unauth caller is rejected; a member can act only on their tenant's site. |
| Agent authentication | `wl_auth_agent(id, sha256(key))`; bad key rejected (proven). |
| No cross-tenant recorder access | `wl_agent_claim_command` claims only `where site_id = <agent's own site>`; enqueue/propose are tenant-scoped. A customer cannot issue a command against another customer's site/agent/recorder (proven). |
| Authoritative-agent / fencing | Claim is atomic (`for update skip locked`, one claimer) and stamps `lease_fence`; complete requires `agent_id = caller`. **Note (honest):** strict fence *re-validation on complete* is a P2 item for multi-agent sites — Multi-agent is OFF for M1, so a single site agent owns the queue. |
| Command expiry | `expires_at = now()+10m`; claim filters `expires_at > now()` (expired never claimed). A janitor to *mark* stale rows `expired` is a nice-to-have, not a safety gap. |
| Idempotency (commands) | Claim atomic; complete only transitions `claimed/executing/verifying→`; re-complete → `not_claimed_by_this_agent`. |
| Rollback behavior | Agent `execute_write`: read→backup→apply→read-back→verify; on failed verify or fault → rollback to before + verify rollback. |
| Status-transition integrity | Check constraint (proposed/queued/claimed/executing/verifying/succeeded/failed/rolled_back/expired) + WHERE-guards on each RPC. |

**Clean-DB chain (`0059→0060→0061→0062→0063→0064`):** runs in the **CI migration job** (fresh Postgres, all migrations applied in order) on the exact remote SHA — see item 9. It cannot be reproduced locally here (no Docker/local PG; the repo's e2e harness runs against live Supabase, not a fresh DB). Reported with CI run IDs.

## 2. Site Control classification — CORRECTED
- **READ:** `CODE / TEST PROVEN` — **not** field-proven. The cloud plane (enqueue→claim→complete→result) is txn-proven (9/9); the read executor is unit-tested; the "Inspect Al-Khalid" combine used this session's recorded field observations. **The one live round-trip attempt happened after the host had left the `192.168.100.x` CCTV LAN, so `cloud→agent→recorder→result` is NOT yet field-proven.**
- **WRITE:** `CODE / TEST PROVEN` — the transactional engine (read→diff→write→read-back→verify→rollback) is unit-proven and the cloud permission/gate plane is txn-proven (8/8), but **no approved write has yet gone through the live Site Agent to the recorder.** The M1 field gate must prove both.

## 3. Site Control security review — allowlist, not deny-and-allow-rest
- **Allowlist:** reads restricted to an explicit catalog; writes to an explicit safe-write catalog `{rename_channel, configure_smd, configure_time}` with a fixed action→capability map. Anything not in the catalog is refused. A hard deny-list (firmware/format/factory/reset/reboot/user/network/password/wipe/erase) is defense-in-depth on top.
- **No passthrough:** actions are fixed names; params are structured fields (channel/name/human/vehicle/sensitivity/dst/ntp). The agent executor dispatches action→specific driver method — there is **no** raw URL/path/CGI/shell/SQL/arbitrary payload passthrough.
- **Default OFF:** the agent `command_worker` is gated on `site_control_enabled` (default false) — a new capability is never auto-enabled on a live site.
- **RECOMMEND never mutates:** recommend → `proposed`; the agent only claims `queued`. A write reaches the recorder only after explicit `wl_site_command_approve` (or a MANAGED action explicitly listed in `site_managed_actions`).
- **Unknown capability never authorizes:** the write gate requires verdict=supported.
- **Every write proves:** correct tenant/site/recorder (resolved from the site's agent), authoritative agent (claim), capability supported, WatchLog implements it (in `WRITE_ACTIONS`), safe-write tier, caller permission, before recorded, read-back verify, immutable audit row, and no false success (only a matching read-back = success).

## 4. Capability gate — multiple truths (hardened)
An action executes only when, **for the exact recorder model** (resolved from the reporting agent, never cross-authorized from another model): (1) manufacturer/documented + (2) WatchLog-implemented + (3) write-supported + (4) safety_class = `safe_write` + (5) **evidence_class = `FIELD_VERIFIED`**. For M1 this restricts writes to the subset physically proven on the client's `DH-XVR1B08-I`. Proven: DH-XVR1B08-I field-verified write allowed; DH-XVR5108HS-I3 (officially supported, not field-tested) **blocked**; unknown model **blocked**.

## 5. Monitoring-coverage semantics — corrected + bounded
- **Honest cause:** a wall-clock jump proves only that the Agent process was **not scheduled** (sleep/hibernate/off/stall — indistinguishable from the clock alone), so the cause is the generic `observation_gap` → customer wording "site not monitored (agent not running)". It no longer asserts "asleep". A cloud/ISP outage does not freeze the process, so it never produces this gap (handled by the spool).
- **No double count / no impossible values:** coverage unions server-unreachable + reconciled-unverified + agent-attributed via `range_agg` (merge). Intervals are clipped to the window before merge; `monitored = greatest(0, wall − unverified)`; ratio clamped to `[0,1]`. Proven: a gap far larger than the window → unverified = wall, monitored = 0, ratio = 0 (never >100% unverified, never negative).

## 6. Offline-buffering truth (stated plainly)
- **Agent running + cloud unavailable:** events **are** buffered locally and uploaded idempotently — via the **pre-existing spool** + `wl_ingest_events` dedupe, not something H3 added. Agent-observed coverage gaps also retry until the cloud accepts them.
- **Agent stopped / not scheduled / off LAN:** **no** WatchLog observation occurred → reported `Not monitored` (this is what H3 added: detection + honest attribution + coverage %). It does **not** claim to have watched.
- **Historical backfill:** **NOT implemented** in this branch. It is gated on a validated recorder retrieval mechanism; the `DH-XVR1B08-I` clip/history path is UNSUPPORTED, so a missed period stays `Not verified` rather than back-filled. H3 did not "solve" offline buffering; do not read it that way.

## 7. Corrected status matrix
| M1 item | Code-complete | Gated (CI/branch) | Deployed | Field-proven | Client-accepted |
| --- | :-: | :-: | :-: | :-: | :-: |
| VideoLoss health truth | ✅ | pending CI on SHA | ❌ | ❌ (needs 0.4.2 on-site) | — |
| Recorder Intelligence KB + Capability Model | ✅ | pending CI | ❌ | n/a (research/model) | — |
| Monitoring coverage (H3) | ✅ | pending CI | ❌ | ❌ (needs full day) | — |
| Site Control READ | ✅ | pending CI | ❌ | ❌ (LAN round-trip) | — |
| Site Control SAFE-WRITE + AI permission | ✅ | pending CI | ❌ | ❌ (live approved write) | — |
| Installer reliability | ✅ | ✅ (53/53 + 10/10 live upgrade) | n/a | partial (fresh install done; 0.4.2 upgrade pending) | — |
| Daily Office Intelligence brief v0 | ✅ | pending CI | ❌ | ❌ (needs full day + calibration) | — |
| Daily WhatsApp | engineering ready | pending | ❌ | ❌ | **blocked on recipient (Client-Input)** |
| 2×10 cameras | n/a | n/a | n/a | 1×8 delivered | **Commercial decision** |

**Legend:** code-complete = built + unit/txn-tested on branch; gated = passes CI/SG/WR on the exact SHA; deployed = live in prod; field-proven = demonstrated on the real site; client-accepted = signed.
