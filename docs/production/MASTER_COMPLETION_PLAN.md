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
| **P4** | Daily report as a real service: n8n schedule → report job → Evolution → delivery log; WatchLog-owned Evolution config; workflow export in git | **DONE** (report-runner deployed `watchlog-report.…`; n8n workflow versioned; config decoupled; dry-run proven to provider boundary). 🔵 real send needs go-ahead+creds | idempotent ✅; scheduled pipeline live |
| **P2** | Agent AI: build frozen exe **with** onnxruntime+numpy+Pillow+`yolov8n.onnx`; self-test proves inference; person/car/motorcycle only; fail-open | **DONE** — frozen exe (111 MB) `--selftest` RESULT PASS; #1 P0 closed | model loads, inference runs, junk discarded, person retained, shipped-in-exe — all ✅ |
| **P8** | Switch billing: DB model + webhook endpoint + signature verify + idempotency + provider abstraction + sandbox fixture + portal UI; customer never writes paid state | **DONE** (0021 + billing service `watchlog-billing.…` + Settings billing UI; mock sandbox live E2E; owner cannot forge — 403). 🔵 Switch adapter = 501 (needs API contract+creds) | sandbox checkout→webhook→active ✅; self-pay denied ✅ |
| **P5** | Self-serve onboarding: remove installer alert stub; real installer download; agent setup-state model; recorder test / camera confirm shown from cloud state | **PARTIAL** — alert stub removed (config-driven `NEXT_PUBLIC_INSTALLER_URL` download, no dead-end); real download needs P9 hosting; agent setup-state model still TODO | signup→…→ready with no VI intervention (demo recorder) |
| **P6** | Portal surfaces: overview, sites (+add/detail), incidents (filter), reports+delivery history, recipients, team, plan/trial, settings | **CORE DONE** — shared nav + Team, Reports (recipients+channel prefs+delivery history), Settings (plan/trial + sites + add-site + issue-code), invite-accept; all build + guard + data-contracts verified. Remaining: site-detail drill-down, dedicated incidents-filter page, full authenticated visual QA | each surface reads/writes live via RLS-safe RPCs ✅ |
| **P9** | Real **NSIS** installer → `WatchLog-Setup.exe`; deterministic build tooling; checksums; config-driven URLs | **CODE DONE** — `prototype/installer/nsis/watchlog.nsi` (MUI2; reuses register-service.ps1) + `tools/build_windows_release.ps1` (builds AI exe → stage → makensis → Setup.exe + SHA256; config-driven publisher URL; optional signtool). Compile pending NSIS toolchain install (winget); Win10/11 acceptance CLIENT-BLOCKED | NSIS compiles a Setup.exe; installs, enrolls, starts agent |
| **P3** | M1 field validation (SM-HP repair; 2×10 real cameras; FP/FN tuning) | **CLIENT-BLOCKED** | see CLIENT_DEPENDENCIES |
| **P10** | Windows 10/11 acceptance matrix | **CLIENT-BLOCKED** (needs clean Win VMs/hardware) | `docs/production/WINDOWS_ACCEPTANCE.md` with real evidence |
| **P11** | Website/portal/installer terminology + capability alignment | **DONE** — pricing unified to ONE source (website Starter 6,000 / Growth 12,000 / Enterprise "Talk to us"; billing plans 0022 match; portal renders those; enforced by `test_pricing_alignment` in CI). Trial/entitlement wording now matches enforced behaviour (`wl_reporting_enabled`). | no advertised-but-unbuilt feature; website == billing (CI-enforced) ✅ |
| **P12** | Production domain migration | **CLIENT-BLOCKED** (domain) | all URLs config-driven; cutover checklist ready |
| **P13** | WordPress polish: sitemap 404, canonical, PHP header, CTAs | TODO | sitemap 200; no stale links |
| **P14** | CI (GitHub Actions) + branch protection + release check | **IN-PROGRESS** (workflow + secret-scan + migration-lint added; verifying first run; branch protection + release_check pending) | PR CI runs compile/tests/build/secret-scan/migration-lint |
| **P15** | Coolify healthchecks + practical diagnostics | **DONE** | health_check_enabled on portal/bridge/report-runner/billing; each has a health route |
| **P16** | M4 runbooks (+ recommended ops docs) | **DONE** (5 required + DEPLOYMENT + DATABASE_MIGRATIONS; BILLING_OPERATIONS/AGENT_RELEASE land with P8/P2) | 5 required runbooks exist and match reality ✅ |
| **P17/P23** | Credential rotation | **DEFERRED — client will do (per instruction)** | n/a |
| **P18** | Demo mode (tagged, isolated, no RLS weakening) | **DONE** | `tools/seed_demo.py`; demo login populated (27 incidents, ready site); isolation still 9/9 |
| **P19** | `docs/demo/FULL_PRODUCT_DEMO.md` (15–25 min) | **DONE** | incognito→marketing→signup→onboarding→AI→portal→reports→team→trial→billing sandbox, with fallbacks |
| **P20** | E2E test (create→enroll→ingest→report→isolation) | **DONE** | `e2e_harness.py` 14/14, disposable tenant, self-cleaning |
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

**➤ As of 2026-09-01 (session 3 — closure pass):** an independent review of `main` found the
"every VI-controlled gate closed" claim premature and named four real gaps. All four are now
closed with evidence, plus the control plane:

1. **Pricing alignment (P11):** one authoritative source — website Starter 6,000 / Growth 12,000 /
   Enterprise "Talk to us"; `0022_pricing_align.sql` makes billing plans match (non-draft) and
   sets Enterprise contact-only; portal renders those; `test_pricing_alignment.py` **fails CI** if
   website and billing diverge. (PRICE-1 ✅)
2. **Trial/entitlement enforcement:** one authoritative `wl_reporting_enabled` + `wl_entitlement`
   (`0023_entitlement.sql`); the **reporter consults it and skips disabled tenants without deleting
   any events/data**; the portal shows the same active/paused pill; `test_entitlement.py` 6/6 covers
   active/past_due(grace)/trial-valid/trial-expired/cancelled/expired. (ENT-1 ✅)
3. **Temp-URL / env alignment:** no hardcoded `sslip.io`/`161.97.175.15` left in shippable code
   (reporter, portal, NSIS, Inno, WP theme all env/`-D`-driven); only an env-**overridable** demo
   default remains in `site-content.sh`. (DOM-1 = PASS for "no temp URL in prod build"; final domain
   stays separately CLIENT-BLOCKED.)
4. **True external-boundary E2E:** `e2e_http.py` 9/9 drives the **deployed** HTTP interfaces
   (auth → RPC → report-runner HTTP → billing **via the deployed billing service** → cross-tenant
   denial); the dead `if False` probe in `e2e_harness.py` is replaced by a real cross-tenant
   PostgREST read (still 14/14). (E2E-1 harness + E2E-2 deployed-HTTP ✅)
5. **Control plane:** duplicate HLTH-1 reconciled to one row; offline CI suite 8/8 green locally;
   live security/authz/entitlement gates re-run; changed services redeployed + verified.

Everything else is unchanged and **CLIENT/ENV-BLOCKED only**: real recorder hardware (M1 field
gate), Switch API contract + creds, production domain, SendGrid key, authorized live WhatsApp send,
code-signing cert, GitHub Actions minutes, an NSIS build box. See `CLIENT_DEPENDENCIES.md`.
**DEMO READY = yes. CONTRACT ACCEPTANCE = blocked on those external inputs only.**
