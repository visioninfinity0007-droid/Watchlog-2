# WatchLog — Master Completion Plan

**Branch:** `production/watchlog-end-to-end` · **Baseline:** main `70cd7d6` (audit merged) ·
**Started:** 2026-08-31 · **Source of truth:** signed SOW (30 Jul 2026) + `docs/audit/CURRENT_STATE_2026-08-31.*`

This is the living tracker for taking WatchLog from the audited state to **one coherent,
demonstrable, production-quality product**. Update this file, `PRODUCTION_GATES.md`,
`DECISION_LOG.md`, `CLIENT_DEPENDENCIES.md` at the start/end of every phase.

Status vocabulary: **DONE** · **IN-PROGRESS** · **TODO** · **CLIENT-BLOCKED** · **FAILED**.
"DONE" = acceptance test in `PRODUCTION_GATES.md` passes against the deployed system.

---

## Dependency order (why this sequence)

```
P0 control plane ─┐
P1 security/DB ───┤ (foundation — everything trusts the DB + authz)
                  ├─► P7 SendGrid HTML (pure code) ─┐
                  ├─► P4 reporting/n8n ─────────────┤
P2 agent AI ──────┤                                 ├─► P18 demo mode ─► P19 demo script ─► P20 E2E
P8 billing model ─┤ (DB + webhook + UI + sandbox)   │
P5 onboarding ────┼─► P6 portal surfaces ───────────┤
P9 NSIS installer ┘                                 │
P3 M1 field (CLIENT) ── P10 Win acceptance (CLIENT) ┘
P11 alignment · P12 domain (CLIENT) · P13 WP polish · P14 CI · P15 health · P16 runbooks
```

Security first (P1) because billing, reporting and portal all rely on trustworthy tenant
isolation and non-forgeable paid state. Pure-code, offline-testable wins (P7 email, P1
migrations, runbooks, CI) are sequenced early to keep `main` continuously demonstrable.

---

## Phase tracker

| Phase | Scope | Status | Gate (acceptance) |
|---|---|---|---|
| **P0** | Control plane: merge audit, branch, these 4 docs | **DONE** | 4 docs exist; branch pushed |
| **P1** | Security/DB foundation: `schema_migrations` lockdown + ledger reconcile; kill self-service paid state (`wl_set_plan`); SECURITY DEFINER re-audit; RLS public→authenticated; FK indexes; isolation stays 9/9 | **DONE** (migrations 0016–0018 applied live + verified 2026-09-01) | isolation 9/9 ✅; anon customer-data 0 ✅; schema_migrations anon write impossible ✅; owner cannot self-mark paid ✅ (`test_billing_authz` 4/4) |
| **P7** | SendGrid **branded HTML** daily email + plain-text fallback + tests | **DONE** (code; live send CLIENT-BLOCKED on SendGrid creds) | HTML renders for 0/normal/high/fault days ✅; escaping safe ✅ (`test_email_template` 6/6); sender configurable ✅ |
| **P4** | Daily report as a real service: n8n schedule → report job → Evolution → delivery log; WatchLog-owned Evolution config; workflow export in git | TODO | scheduled run writes `report_deliveries.status=sent` (test destination); idempotent |
| **P2** | Agent AI: build frozen exe **with** onnxruntime+numpy+Pillow+`yolov8n.onnx`; self-test proves inference; person/car/motorcycle only; fail-open | TODO | frozen exe self-test: model loads, inference runs, classes retained, junk frame discarded |
| **P8** | Switch billing: DB model + webhook endpoint + signature verify + idempotency + provider abstraction + sandbox fixture + portal UI; customer never writes paid state | TODO (live = CLIENT-BLOCKED) | sandbox checkout→webhook→subscription active; owner cannot forge paid |
| **P5** | Self-serve onboarding: remove installer alert stub; real installer download; agent setup-state model; recorder test / camera confirm shown from cloud state | TODO | signup→…→ready with no VI intervention (demo recorder) |
| **P6** | Portal surfaces: overview, sites (+add/detail), incidents (filter), reports+delivery history, recipients, team, plan/trial, settings | TODO | each surface reads/writes live via RLS-safe RPCs |
| **P9** | Real **NSIS** installer → `WatchLog-Setup.exe`; deterministic build tooling; checksums; config-driven URLs | TODO | NSIS compiles a Setup.exe; installs, enrolls, starts agent |
| **P3** | M1 field validation (SM-HP repair; 2×10 real cameras; FP/FN tuning) | **CLIENT-BLOCKED** | see CLIENT_DEPENDENCIES |
| **P10** | Windows 10/11 acceptance matrix | **CLIENT-BLOCKED** (needs clean Win VMs/hardware) | `docs/production/WINDOWS_ACCEPTANCE.md` with real evidence |
| **P11** | Website/portal/installer terminology + capability alignment | TODO | no advertised-but-unbuilt feature; consistent nouns |
| **P12** | Production domain migration | **CLIENT-BLOCKED** (domain) | all URLs config-driven; cutover checklist ready |
| **P13** | WordPress polish: sitemap 404, canonical, PHP header, CTAs | TODO | sitemap 200; no stale links |
| **P14** | CI (GitHub Actions) + branch protection + release check | **IN-PROGRESS** (workflow + secret-scan + migration-lint added; verifying first run; branch protection + release_check pending) | PR CI runs compile/tests/build/secret-scan/migration-lint |
| **P15** | Coolify healthchecks + practical diagnostics | TODO | portal/site/bridge have real health endpoints wired |
| **P16** | M4 runbooks (+ recommended ops docs) | **DONE** (5 required + DEPLOYMENT + DATABASE_MIGRATIONS; BILLING_OPERATIONS/AGENT_RELEASE land with P8/P2) | 5 required runbooks exist and match reality ✅ |
| **P17/P23** | Credential rotation | **DEFERRED — client will do (per instruction)** | n/a |
| **P18** | Demo mode (tagged, isolated, no RLS weakening) | TODO | full journey demonstrable without client hardware/creds |
| **P19** | `docs/demo/FULL_PRODUCT_DEMO.md` (15–25 min) | TODO | script runs start-to-finish on demo env |
| **P20** | E2E test (create→enroll→ingest→report→isolation) | TODO | automated E2E green; fixtures cleaned |
| **P21** | Signed-scope final gate re-audit | TODO | every item PASS or CLIENT-BLOCKED (no PARTIAL we control) |

---

## Current signed-scope status (from audit, updated as phases land)

- **M1** 🔴 not complete — AI filter not shipped (P2), field gate FAIL (P3 client), WhatsApp unscheduled (P4).
- **M2** 🟠 partial — onboarding dead-ends (P5), portal surfaces missing (P6).
- **M3** 🔴 barely started — Switch (P8), SendGrid HTML (P7).
- **M4** 🟠 partial — isolation ✅; NSIS (P9), runbooks (P16), handoff (P3/P10 client).

Scores at baseline: engineering ~68% · live-deploy ~58% · signed-scope ~37% · handoff ~28%.

---

## Next action (always keep current)

**➤ P1 DONE.** Next: **P7 (SendGrid branded HTML email)** — pure code, offline-testable, closes an
M3 sub-gate and gives the daily report a real body. Then P4 (n8n reporting pipeline).
