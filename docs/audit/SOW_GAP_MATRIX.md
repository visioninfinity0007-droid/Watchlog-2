# WatchLog — Signed-Scope Gap Matrix

**Date:** 2026-08-31 · Audited HEAD `ce651dd` · against the **signed** 4-milestone scope (authoritative
over older draft proposals). Companion: `CURRENT_STATE_2026-08-31.md` / `.json`.

**Legend:** ✅ DELIVERED · 🟠 PARTIAL · 🔴 MISSING · ⚠️ BROKEN · 🔵 CLIENT-DEPENDENCY · ⚪ OUT-OF-SCOPE
· `Code?` = needs Vision Infinity build work · `Client?` = blocked on the client.

---

## Milestone 1 — MVP

| Requirement | Implementation | Live evidence | Status | Gap | Client? | Code? | Acceptance test | Effort |
|---|---|---|---|---|---|---|---|---|
| Windows Site Agent | `watchlog_agent.py`, PyInstaller exe (16 MB, built) | exe on disk; enrolled live | 🟠 | packaged as Inno/zip, not NSIS; unsigned | no | yes | signed installer installs + runs as SYSTEM | see M4 |
| Python service polling NVRs on LAN | agent + drivers, outbound-only, SQLite spool | proven vs sim; 2070 events | ✅ | — | no | no | agent streams from a recorder | done |
| Hikvision / Dahua / ONVIF | 3 drivers + auto-probe | Dahua on real XVR (3 min); Hik/ONVIF **sim only**; all `verified_against_hardware=False` | 🟠 | only Dahua touched real hw, briefly | 🔵 units | yes | each driver validated on real hardware | 2–4 d/unit |
| **YOLOv8n local AI filter** (person/car/motorcycle) | `vision.py` (classes 0/2/3, conf 0.35, fail-open) | **strings scan of shipped exe: onnxruntime/torch/yolov8 = 0; no model file; runtimes excluded at build** | 🔴 | **not in any shipped binary; ships unfiltered** | 🔵 footage | yes | rebuilt exe bundles `yolov8n.onnx`; strings scan finds runtime | 1–2 d + tuning |
| Validated event sync to Supabase | `wl_ingest_events`, dedup, at-least-once | 2070 events live, no dup/gap tests pass | ✅ | — | no | no | isolation + ingest tests green | done |
| Internal dashboard | Next.js portal (exceeds: multi-tenant) | live 200, builds clean | ✅ | — | no | no | dashboard renders live tenant data | done |
| Daily WhatsApp summary | `daily_report.py` (tz, idempotent, logged) | **0 `report_deliveries`; no scheduler; no n8n workflow** | ⚠️ | not scheduled, not via n8n, never sent | 🔵 send OK | yes | scheduled job → `report_deliveries.status=sent` | 1–2 d |
| Exactly 2 MVP sites × 10 cameras | 1 site, 8 sim cameras | real XVR synced **0 cameras** | 🔴 | 1 site, no 10-camera site | 🔵 sites | yes | 2 sites × 10 real cameras online | client |
| **Field validation (HARD GATE)** | — | **SM-HP DH-XVR1B08-I: 9 events / ~3 min / 0 cameras, then stopped** | 🔴 **FAIL** | agent stops; `list_channels` fails on real firmware | 🔵 `agent.log` | yes | real recorder >1 h, cameras synced, stable | blocked |
| FP/FN tuning vs real footage | — | none; only synthetic-frame unit test | 🔴 | no real-footage tuning | 🔵 footage | yes | measured FP/FN on real footage vs agreed threshold | blocked |

**M1 verdict:** 🔴 **not acceptance-complete** — three headline items (AI filter, field gate, WhatsApp
schedule) fail or are undelivered.

---

## Milestone 2 — SaaS

| Requirement | Implementation | Live evidence | Status | Gap | Client? | Code? | Acceptance test | Effort |
|---|---|---|---|---|---|---|---|---|
| Public marketing site | WordPress custom theme, 9 pages | live 200, SEO/OG/legal pages | ✅ | on sslip.io; `wp-sitemap.xml` 404 | 🔵 domain | minor | site reachable on real domain | done* |
| Registration | `/signup` + `wl_bootstrap_tenant` | signup form live, CTAs → portal | ✅ | — | no | no | account creates a tenant | done |
| Full self-serve onboarding | `/onboarding` (tenant+site+code) | **installer download = `alert()` stub** | 🟠 | dead-ends; agent delivered out-of-band | no | yes | signup→install→data with no human help | 2–4 d |
| Agent provisioning | enrollment codes, `wl_issue_code` | codes issued; portal download stub | 🟠 | no in-portal installer download | no | yes | portal serves a per-site installer | 1–2 d |
| NVR connection / test | (in agent `--setup` only) | no portal test UI | 🔴 | no connection-test surface | no | yes | portal "test recorder" returns status | 2–3 d |
| Multi-tenant DB model | 13 tables, tenant_id + RLS | live; 2 tenants | ✅ | — | no | no | schema present | done |
| Tenant isolation | RLS member-scoped + SECURITY DEFINER | **9/9 live gate pass** | ✅ | — | no | no | isolation suite green | done |
| Customer portal | 5-route Next.js | live, builds | ✅ | thin (read-only) | no | no | portal usable | done |
| Team management | `wl_invite/accept/members/roles` + policies | functions live; **no portal UI** | 🟠 | backend only | no | yes | invite/manage members in portal | 3–5 d |
| Complete report history | `report_deliveries` + `wl_deliveries` | table live (0 rows); **no UI** | 🟠 | no history surface; nothing sent | no | yes | portal shows delivery history | 2–3 d |
| Trial logic (default 14 days) | `trial_started_at`+`trial_days`(14), `wl_trial_status` | present; **reported, not enforced**; no UI | 🟠 | not enforced; no UI | no | yes | trial expiry enforced + shown | 2–3 d |

**M2 verdict:** 🟠 **PARTIAL** — backend + isolation strong; onboarding incomplete; team/reports/trial
have no UI.

---

## Milestone 3 — Billing + Email

| Requirement | Implementation | Live evidence | Status | Gap | Client? | Code? | Acceptance test | Effort |
|---|---|---|---|---|---|---|---|---|
| **SWITCH payment gateway** | none | no gateway code / tables / webhook | 🔴 | **NOT STARTED** | 🔵 merchant creds | yes | Switch checkout completes a payment | 2–3 wk |
| Tenant subscriptions | `plan`/`subscription_status` columns | owner-settable via `wl_set_plan` | 🔴 | no gateway-driven state | 🔵 | yes | subscription reflects a real payment | with above |
| Trial-to-paid conversion | none | — | 🔴 | missing | 🔵 | yes | trial converts on payment | with above |
| Webhook-driven subscription updates | none | only push bridge (not billing) | 🔴 | missing | no | yes | signed webhook updates subscription idempotently | with above |
| **Branded SendGrid HTML** daily report | `Email` class, `text/plain` only | code present; **no HTML template**; unconfigured | 🟠 | plain text, not branded HTML | 🔵 sender/domain | yes | a branded HTML email is received | 2–4 d |
| WhatsApp alongside email | reporter supports both channels | data-layer only; 0 sends | 🟠 | no UI; unsent | 🔵 send OK | yes | both channels deliver | 1–2 d |
| Tenant/channel preference (WA/email/both) | `report_recipients.channel` CHECK | data live (1 recipient); **no UI** | 🟠 | no preference UI | no | yes | user sets channel in portal | 1–2 d |

**M3 verdict:** 🔴 **barely started** — Switch absent; email is a plain-text stub.

---

## Milestone 4 — Polish + Handoff

| Requirement | Implementation | Live evidence | Status | Gap | Client? | Code? | Acceptance test | Effort |
|---|---|---|---|---|---|---|---|---|
| **NSIS installer** | Inno `.iss` (never compiled) + PowerShell; ships ZIP | **no `.nsi`; no Setup.exe; unsigned** | 🔴 | wrong tech; uncompiled; unsigned; untested | no | yes | signed NSIS installer, boot/uninstall lifecycle tested | 1–2 wk |
| **Tenant-isolation QA suite (HARD GATE)** | `test_tenant_isolation.py` | **9/9 live pass; proven can fail (0010 regression)** | ✅ | — | no | no | suite green against prod | done |
| Runbooks (add tenant / key rotation / log access / webhook replay / NVR troubleshooting) | none tracked | only design docs + prototype README | 🔴 | all 5 missing | no | yes | each runbook exists + is followable | 3–5 d |
| Final live-hardware handoff | — | real hw unresolved (M1 gate) | 🔴 | not done | 🔵 | yes | client sign-off on live hardware | blocked |
| Production readiness | services live but gaps | no domain, no auto-deploy, no monitoring, filter unshipped | 🟠 | see P1/P2 blockers | 🔵 domain | yes | prod checklist complete | 1–2 wk |

**M4 verdict:** 🟠 **PARTIAL** — only the isolation gate is delivered.

---

## Out-of-scope (⚪ bonus — excluded from contractual completion)

| Item | Status | Note |
|---|---|---|
| Recorder-push (PC-free) mode | live (bridge deployed, proven) | genuine value; not in signed scope |
| Analytics capability probe | built + proven vs sim | read-only NVR analytics detection |
| Per-tier snapshot retention (pg_cron) | live nightly | plan-aware pruning |

---

## Roll-up

| Milestone | Verdict | One-line reason |
|---|---|---|
| **M1 MVP** | 🔴 not complete | AI filter not shipped; field gate FAIL (9 events/3 min/0 cameras); WhatsApp unscheduled |
| **M2 SaaS** | 🟠 partial | multitenancy + isolation + site + registration done; onboarding incomplete; team/reports/trial no UI |
| **M3 Billing+Email** | 🔴 barely started | Switch NOT STARTED; SendGrid plain-text stub |
| **M4 Polish+Handoff** | 🟠 partial | isolation suite delivered; NSIS wrong/untested; runbooks missing; handoff undone |

**Signed-scope completion ≈ 37%** (milestone-weighted 25% each: M1 ~45, M2 ~60, M3 ~10, M4 ~25).
