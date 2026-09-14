# WatchLog M1 — Engineering Closure Matrix (revised)

Response to **"FINISH THE REMAINING UNBLOCKED PRODUCT WORK"**. The previously designed-only
items are now implemented, tested, integrated, documented, committed, pushed, and CI-gated. The
only things left pending are genuine external dependencies (deployment window, field hardware,
client input, commercial).

**Branch** `feat/m1-final-closure` · **code HEAD** `90a35c1` (pushed to origin + the CI fork).

Columns: **Des**igned · **Imp**lemented · **Tst** (tested, with count) · **Int**egrated · **Doc**umented · **Com**mitted+**Pushed** · **CI**-gated. Deploy/Field/Client may remain pending.

| # | Item | Des | Imp | Tst | Int | Doc | C+P | CI | Deploy/Field/Client |
|---|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|---|
| 1 | Visitor/staff inference (0073, folded into 0074) | ✅ | ✅ | ✅ 8/8 | ✅ | ✅ | ✅ | ✅ | prod migrate; real multi-day data |
| 2 | AI Site Configuration Skill (advisor) | ✅ | ✅ | ✅ 7/7 | ✅ | ✅ | ✅ | ✅ | live recorder for real proposals |
| 3 | SaaS onboarding foundation (0072 + portal) | ✅ | ✅ | ✅ 9/9 | ✅ | ✅ | ✅ | ✅ | prod migrate |
| 4 | Site Control SaaS surface (0079 + page) | ✅ | ✅ | ✅ 9/9 | ✅ | ✅ | ✅ | ✅ | prod migrate; live agent on-LAN |
| 5 | Capability-aware UX everywhere (+contract) | ✅ | ✅ | ✅ 22/22 | ✅ | ✅ | ✅ | ✅ | — |
| 6 | Historical-backfill framework (backfill.py) | ✅ | ✅ | ✅ 8/8 | ✅ | ✅ | ✅ | ✅ | vendor archive validation (field) |
| 7 | Offline buffering audit (+full-cycle proof) | ✅ | ✅ | ✅ 10+5 | ✅ | ✅ | ✅ | ✅ | — |
| 8 | WhatsApp engineering path (delivery + n8n) | ✅ | ✅ | ✅ 8/8+13/13 | ✅ | ✅ | ✅ | ✅ | **CLIENT RECIPIENT** + prod migrate |
| 9 | Critical-alert delivery chain (0075) | ✅ | ✅ | ✅ 8/8 | ✅ | ✅ | ✅ | ✅ | prod migrate; live transport |
| 10 | Report calibration tooling (0076) | ✅ | ✅ | ✅ 11/11 | ✅ | ✅ | ✅ | ✅ | real reviewer data |
| 11 | Demo/Acceptance Preflight (0078) | ✅ | ✅ | ✅ 12/12 | ✅ | ✅ | ✅ | ✅ | — |
| 12 | Edge deployment (spec + configurable spool) | ✅ | ✅ | ✅ 10/10 | ✅ | ✅ | ✅ | ✅ | hardware procurement |
| 13 | Monthly reporting integration | ✅ | ✅ | ✅ 7/7 | ✅ | ✅ | ✅ | ✅ | one real month |
| 14 | Opening/closing + restricted verified (0074) | ✅ | ✅ | ✅ 10/10 | ✅ | ✅ | ✅ | ✅ | — |
| 15 | Native-AI semantic change (doc + behavioral) | ✅ | ✅ | ✅ 16/16 | ✅ | ✅ | ✅ | ✅ | — |
| 16 | Gate evidence correction | ✅ | ✅ | — | ✅ | ✅ | ✅ | — | (see below) |
| 17 | Release candidate integrity | ✅ | ✅ | — | — | ✅ | ✅ | — | ONE final rebuild after CI green |
| 18 | Recorder research batch 3 (0077, 8 models) | ✅ | ✅ | ✅ 10/10 | ✅ | ✅ | ✅ | ✅ | — (29 models total) |

Migrations this wave: **0072–0079**. New agent/reporter/advisor/portal modules with unit +
live-PG integration tests (every DB test runs inside a rolled-back transaction — **zero writes
to prod**).

## Item 16 — CI provenance (the execution authority, named exactly)

- The canonical GitHub org (Alkalid-security) has **Actions billing-blocked**, so its runs do
  not execute. The **runnable CI is the fork `visioninfinity0007-droid/Watchlog-2`** — that is
  the current CI **execution authority**, and "CI green" in this repo means green **there**, on
  the exact pushed SHA.
- First-wave green: CI run **34507402977** and Windows Release **34507948208**, both SUCCESS on
  code SHA `3aeb93d`.
- Second-wave (this closure): CI run **34514528465** SUCCESS on code SHA `90a35c1` (all six
  jobs: backend, setup-ui-build, integration, integration-prod-order, installer-contract,
  portal). Deployment provenance always cites the fork run id + SHA, never a bare "CI green".

## Item 17 — release candidate integrity

Agent source changed again this wave (`backfill.py`, `native_verification.annotate_event`,
`spool.py` configurable cap, `native_event_collector.py`). Per the "one final candidate" rule,
the 0.4.2 installer was rebuilt **once** from the final green SHA (Windows Release run
**34515113008**, SUCCESS) — not re-published per commit. Rebuilt artifact `WatchLog-Windows-18`
(~345 MB):

- **Final 0.4.2 installer** `WatchLog-Setup.exe` SHA-256 = `6D6FE1D4FFC6CC82BC98BA9701292729263C67C884F6ACEE47D862E2FD8D2C62`
- agent exe = `9279FBAA776E752C679FD0487CF0322AE41CE9D8CE1EC055C70CC5830272B68B`
- setup-UI exe = `BD265FBA0865C929D9C1FCCFCEF1CFE2BBAFF5F3C61463C54C20738645E32DF7`

This supersedes every earlier 0.4.2 build (wave-1 `F0D1317E…`, originally-published stale
`3B69D99D…`). Field-install THIS candidate. `/latest/` is untouched; no intermediate binaries
were published.

## Genuinely blocked (do not wait) — unchanged

Ch5 physical repair · SM-HP back on the CCTV LAN · live 0.4.2 upgrade · live Site Control proof
· an uninterrupted 24-hour dataset · client report calibration · the client WhatsApp recipient ·
the 2×10 commercial decision. Everything else in the engineering column is done.

## Deployment order when the window opens

1. `apply_migrations.py` → prod (0065–0079), verify head + no drift.
2. Rebuild + verify the ONE final 0.4.2 candidate from the final green SHA; field-install on SM-HP.
3. Deploy the portal (Site Control + onboarding pages) and point the report-runner at the delivery pipeline.
4. First supervised WhatsApp send = next FULL day, once the client recipient is supplied.

---

# Final product-integration pass (wave 3)

Closes the four (ten-point) gaps found between the built layers and the M1 requirements. After
this pass the engineering column is genuinely exhausted.

**Final CODE SHA `160c187`** (a real code commit, not docs). Migrations `0080–0084` + reporter
refactor, all tested against live PG in rolled-back transactions and CI-gated.

| # | Integration item | Delivered | Proof |
|---|---|---|---|
| 1 | wl_daily_intelligence is the ACTUAL report source | runner default = `run_intelligence` (frozen snapshot → WhatsApp/PDF from one payload; legacy `wl_daily_report` behind `--legacy`); one `headline_metrics` extraction shared by all surfaces | report unification 7/7 (identical metrics across portal/WhatsApp/PDF) |
| 2 | PDF operational, not optional | packaged xhtml2pdf (pure-Python, shipped + in CI); real %PDF bytes; runner saves the PDF ref to the snapshot | 7/7 incl. empty-section, coverage-warning, incident/restricted, uncertainty |
| 3 | Journey Intelligence strengthened | 0080 topology-gated (allowed edges + per-edge temporal bounds), impossible-transition rejection, concurrency separation, confidence + reasons + uncertainty + stable identity; "plausible movement journeys" | e2e 6/6 |
| 4 | Visitor/staff inference strengthened | 0082 feeds journey confidence + coverage + calibration state; exposes them; "estimated behavioral classification"; still visitor/staff/unclassified, honest | e2e 8/8 |
| 5 | Opening/closing state machine | 0081 activity-session model (arrival → sustained session → OPEN → quiet period → CLOSED); cleaner/late-motion excluded; entrance/internal/hours/quiet/min configurable; labelled fallback | e2e 8/8 |
| 6 | After-hours truth | 0081 asserts after-hours ONLY when the schedule is known, else "not verified / schedule incomplete" | e2e (in 8/8) |
| 7 | Persisted report snapshot | 0083 generate-once + freeze (report id, payload, schema + versions, coverage, PDF ref/hash, delivery status); immune to later config change | e2e 6/6 |
| 8 | Delivery failure/idempotency | 0084 durable outbox (unique idempotency key, atomic claim, crash-edge stale reclaim); provider-key dedup = effective-once; honest at-least-once semantic documented | e2e 6/6 + DELIVERY_SEMANTICS.md |

## Item 9 — release provenance (recorded distinctly)

The earlier wave's Windows Release ran from `d94f9af`, a docs-only child of code SHA `3aeb93d`
— binary-equivalent but a different ref, now recorded as such above. For THIS pass all three
gates run against the one final CODE ref:

- **application code SHA** = `160c187` (the final integration commit)
- **CI SHA** = `160c187` (fork run `34523322939`, SUCCESS)
- **artifact source SHA** = `160c187` (Windows Release run `34523342941`, SUCCESS)
- **Security Gate SHA** = `160c187` (run `34523345583`, SUCCESS)
- **final 0.4.2 installer** `WatchLog-Setup.exe` SHA-256 = `D55862A57D060F4C1CEB3713E323415563C8EA73A31103F805127A18BB2BC4CC` (artifact `WatchLog-Windows-19`)

This matrix commit is a docs-only child of `160c187`; the artifact source SHA above is the code
SHA `160c187`, NOT this docs commit.

## DONE

**M1 ENGINEERING COMPLETE — FIELD/CLIENT ACCEPTANCE PENDING.** The remaining gates are only:
SM-HP/site access · 0.4.2 field upgrade · live Site Control proof · Ch5 physical repair · a full
monitored day · calibration with the client · the approved WhatsApp recipient · the 2×10
contractual resolution.

## Item 10 — not deployed (held)

No prod migration (`0065–0084`), no portal/report-runner deploy, no client agent upgrade, no
recorder write, no WhatsApp client send, `/latest/` untouched.
