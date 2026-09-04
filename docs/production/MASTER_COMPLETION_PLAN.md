# WatchLog — Master Completion Plan

**Re-baselined:** 2026-09-04  
**Repository baseline:** `main` `07ca75e377f571079df440bdb452e17a183f4cce`  
**Post-merge CI:** run `33868554241` — backend, portal, installer-contract and setup-ui-build all SUCCESS  
**Current audit:** `docs/audit/CURRENT_STATE_2026-09-04.md`  
**Acceptance source:** `docs/production/PRODUCTION_GATES.md`  
**External blockers:** `docs/production/CLIENT_DEPENDENCIES.md`

This is the current execution plan. Older audit findings remain useful as history, but this file must describe what still needs to be done now.

## Operating rule

A task is **DONE** only when its acceptance gate is proven at the correct layer.

- Repository/CI evidence proves code/build behavior.
- Production evidence proves the deployed schema/services/routes.
- Field evidence proves real recorder/camera behavior.
- Client intent defines roadmap/commercial direction.

Do not convert one kind of evidence into another. In particular, green `main` does not prove that Coolify or Supabase production is current.

## Current execution status

| Workstream | Repository status | Production / field status |
|---|---|---|
| Sprint 0 — source of truth + release baseline | **MERGED** via PR #14 | production verification still access-dependent |
| Sprint 1 — DB/auth/core production parity | runbook prepared; migration train verified | **BLOCKED on WatchLog Supabase access / manual execution** |
| Sprint 2 — graphical Windows installer | **MERGED** via PR #15 at `50486bfb...` | publication/signing/manual Win10/11 acceptance still open |
| Sprint 3 — website/product positioning | **MERGED** via PR #16 at `07ca75e...` | WordPress/Coolify deployment and live QA still open |
| Sprint 4+ — Control Room / field / R&D / integrations | planned | intentionally not started as production claims |

### Completed repository evidence

- graphical PySide6 Windows setup UI;
- Site Code → recorder discovery → recorder login → camera discovery → enrollment → camera sync → Ready flow;
- machine-scoped Windows DPAPI recorder-password storage;
- protected credential ACL restricted to SYSTEM + local Administrators;
- legacy plaintext credential migration path;
- real Windows setup build + DPAPI round-trip/migration-mode CI exercise;
- release tooling for the real agent + setup UI + NSIS installer;
- portal visual alignment and production static-export gate;
- website repositioned to **Video Analytics & CCTV Intelligence**;
- Analytics Studio capabilities surfaced as current product with explicit qualification;
- `Available`, `Coming Soon` and `Custom Solution` maturity labels enforced in website copy;
- Control Room, fire/smoke and integrations prevented from silently reading as current standard-product capability;
- public claims matrix re-baselined to current installer/security/analytics behavior;
- public website claims/maturity contract and WordPress PHP syntax lint added to CI.

## Product direction

WatchLog is a **Video Analytics & CCTV Intelligence Platform** built on the existing CCTV SaaS core.

Use three layers:

1. **Core SaaS Platform** — onboarding, sites, cameras, incidents, Site Health, Analytics Studio, reports, team, settings, billing and Platform Admin.
2. **Advanced Analytics Modules** — visitor/people flow, vehicle flow, boundary/zone activity, dwell/time-in-zone and after-hours activity now exist as configured Analytics Studio capabilities; fire/smoke and broader object analytics remain R&D.
3. **Industry / Custom Solutions** — QSR/control room, warehousing/logistics, manufacturing/textile, fuel/infrastructure and business-system integrations.

The repository already contains substantial Core SaaS and Analytics Studio functionality. Extend it; do not rebuild it.

## What is still staging-critical

### P0 — Production database and auth parity

The repository migrations run through `0036`. The last verified production evidence showed the WatchLog database behind current source, with `0024+` markers absent. Before applying anything, re-read the live schema because another operator may have changed it.

Required execution:

1. Verify the target is the WatchLog project, never the unrelated `Al khalid` project.
2. Take a backup/snapshot.
3. Prove the current migration boundary.
4. Apply only genuinely pending migrations, in order, through `0036`.
5. Verify tables, RPCs, RLS, grants and SECURITY DEFINER search paths.
6. Verify `platform_admins` and `wl_platform_me()`.
7. Create/verify the intended platform-owner Auth user through Supabase Auth, not direct password manipulation in `auth.users`.
8. Assign `platform_owner` only after migration `0029` exists.
9. Run tenant-isolation/platform-denial/billing/authz smoke tests.
10. Smoke Overview / Incidents / Site Health / Analytics / Reports / Team / Settings / Admin against the real production backend.
11. Run the Supabase security advisor after DDL.

The guarded handoff is `docs/runbooks/PRODUCTION_PARITY_0024_0036.md` on the prepared Sprint 1 handoff branch. If production access remains unavailable here, execute that bounded runbook from the authorized local/Claude/Supabase-admin environment.

### P0 — Actual deployment parity

Repository completion is not deployment completion.

Verify and, where necessary, deploy:

- portal;
- WordPress marketing site;
- push bridge;
- report runner;
- billing service;
- environment variables and public URLs;
- exact deployed commit/image for each service.

Then run live route and visual smoke tests. The Sprint 3 website is merged to source but must not be described as live until WordPress/Coolify deployment is proven.

### P0 — Installer publication / onboarding closure

The customer-grade graphical installer exists in source and passes a real Windows CI build. Remaining release gates:

1. Configure the real Windows Release workflow with the required public release variables.
2. Run the genuine release workflow successfully.
3. Publish a versioned `WatchLog-Setup.exe`.
4. Publish a stable/latest download URL.
5. Set `NEXT_PUBLIC_INSTALLER_URL` and rebuild/redeploy the portal.
6. Add Authenticode signing when the certificate is available.
7. Verify clean Windows 10/11 install, reboot, upgrade and uninstall.
8. Verify the background agent survives reboot and remains healthy.
9. Validate diagnostics/support behavior for failure states.

### P0/P1 — Real hardware and field acceptance

Repository tests do not replace site evidence.

Per pilot site capture:

- recorder vendor/model/firmware;
- camera model/channel/purpose/FOV/resolution/FPS;
- recording mode;
- snapshot/playback/clip APIs;
- timestamp behavior;
- pre/post-event duration;
- concurrency limits;
- network conditions;
- agent soak/reboot behavior;
- analytic measurement error ranges.

Current compatibility language remains conservative: Dahua has real-hardware experience but broad model/firmware validation is ongoing; Hikvision has implemented driver/simulator coverage and still needs broader field acceptance; other protocol-compatible recorders must be confirmed per unit.

## Current product claims boundary

### Available / implemented

- incidents with on-site person/car/motorcycle filtering;
- Site Health;
- daily WhatsApp/email reporting and delivery history;
- Analytics Studio configuration;
- visitor/people flow;
- vehicle flow;
- boundary / zone activity;
- dwell / time in zone;
- after-hours activity;
- checkout-zone activity as activity/occupancy, not exact transactions;
- multi-site/team/admin foundations;
- graphical Windows setup in repository/CI.

### Coming Soon / roadmap only

- Control Room as a dedicated product module;
- saved multi-camera operational layouts;
- fire/smoke detection research;
- true live video wall;
- recorded clip extraction until recorder-specific field support is proven.

### Custom Solution / scoped implementation

- POS;
- Shopify;
- attendance systems;
- CRM/API connectors;
- other vertical business-system integrations.

### Never imply without new evidence

- facial recognition or visitor identity;
- cross-camera person re-identification;
- exact POS/sales transactions from CCTV alone;
- universal recorder compatibility;
- generic “detect anything” capability;
- certified fire/safety detection;
- customer or partner status without permission/formal status;
- cloud live video if the product is displaying still refreshes.

## Sprint plan

### Sprint 0 — Production truth, source-of-truth cleanup and release baseline

**Status: MERGED / repository-complete**  
**Evidence:** PR #14; current-state audit, production-gate rebaseline, release workflow and release-tooling fixes.

Remaining items from this sprint are no longer code tasks; they are deployment/production verification gates listed above.

### Sprint 1 — Production database, auth and core portal alignment

**Priority:** P0  
**Status:** execution-ready, access-dependent

Acceptance:

- production schema matches current `main`;
- platform admin works without bypasses;
- tenant users cannot access platform admin;
- portal has no missing-RPC errors;
- existing tenant/site/camera/auth data is preserved;
- post-DDL security checks pass.

### Sprint 2 — Customer-grade Windows installer and onboarding

**Priority:** P0  
**Status: repository implementation MERGED via PR #15**

Completed in source/CI:

- branded graphical setup;
- no customer-facing terminal dependency in normal setup;
- recorder discovery/enrollment/camera sync reused from real backend logic;
- DPAPI-protected recorder password;
- SYSTEM/Administrators ACL;
- legacy plaintext migration;
- hidden background launcher;
- real frozen setup-UI Windows build and DPAPI exercise;
- release tooling builds/checks agent + setup UI + installer.

Still open:

- configured production release workflow run;
- stable/versioned installer hosting;
- `NEXT_PUBLIC_INSTALLER_URL` deployment;
- Authenticode certificate/signing;
- clean Win10/11 lifecycle acceptance;
- separate support/diagnostics polish where needed.

### Sprint 3 — September staging website/product alignment

**Priority:** P0/P1  
**Status: repository implementation MERGED via PR #16**

Completed:

- umbrella positioning changed to **Video Analytics & CCTV Intelligence**;
- Analytics Studio is first-class website content;
- visitor/people flow, vehicle flow, boundary/zone activity, dwell, after-hours and checkout-zone activity surfaced with precise qualifiers;
- current security wording aligned to DPAPI/local-data boundaries;
- compatibility language corrected to avoid broad field-validation claims;
- Control Room and fire/smoke marked `Coming Soon`;
- integrations marked `Custom Solution`;
- unconfirmed hard camera-cap copy removed from homepage;
- website maturity/claims CI contract added;
- WordPress PHP syntax lint added;
- post-merge main CI run `33868554241` fully green.

Still open:

- actual WordPress/Coolify deploy;
- live browser visual/route QA;
- any later vertical pages such as QSR/fuel only when product/pilot requirements justify them.

### Sprint 4 — QSR / Control Room pilot foundation

**Priority:** P1  
**Start only after core production parity is proven.**

1. Add dedicated Control Room route/nav.
2. Add saved layout model and 2×2 / 3×3 / 4×4 views.
3. Add site/camera grouping.
4. Show camera health, last-seen, purpose and real event/incident state.
5. Allow explicitly labelled latest/current still refresh where the existing agent path supports it.
6. Add fullscreen operations mode.
7. Add QSR camera-purpose presets.
8. Reuse visitor-flow/dwell/occupancy/zone/schedule rules.
9. Add camera-wise, site-level and collective QSR reporting.
10. Do not label still refresh as live video.

Acceptance:

- saved layouts persist;
- all health/event state is real;
- latest-still semantics are explicit;
- pilot analytics come from actual analytic events;
- collective reporting uses real data.

### Sprint 5 — Field validation, clip extraction and analytics calibration

**Priority:** P1

1. Record hardware/firmware/camera conditions per site.
2. Build recorder-specific clip adapters only where field-proven.
3. Define event→clip timestamp mapping.
4. Measure people-flow/count accuracy.
5. Measure line-crossing direction accuracy.
6. Measure dwell behavior.
7. Calibrate sample rates/thresholds.
8. Produce camera-placement guidance.
9. Re-run long agent soak/reboot acceptance.

Acceptance requires measured error ranges and supported/unsupported classification per recorder family.

### Sprint 6 — Advanced analytics R&D

**Priority:** P2

- richer occupancy/queue/dwell presets using the existing engine;
- broader vehicle workflows where justified;
- separate fire/smoke model + dataset + acceptance track;
- additional object classes only with a real use case and dataset.

Every new analytic needs a model definition, field use case, test set, accuracy evidence, failure semantics and maturity label.

### Sprint 7 — Reporting, payment and support operations

**Priority:** P2

Reporting:

- camera report;
- site report;
- multi-site/executive report;
- QSR template.

Payment:

- finalize gateway vs interim bank-transfer path;
- if bank transfer is approved, build authenticated proof upload + admin verification + audit trail + backend-only subscription activation.

Support/Admin:

- customer/site health queue;
- stale/offline agent queue;
- failed-report queue;
- trial/payment issue queue;
- support notes/status;
- safe diagnostic summary.

Do not build a CRM inside WatchLog.

### Sprint 8 — Integration platform

**Priority:** P3

Build architecture first:

- integration account/config model;
- outbox/event delivery;
- signatures;
- retry/idempotency;
- connector health;
- audit/logging;
- secret handling.

Then implement one prioritized signed-pilot connector. Do not build all connectors simultaneously.

### Sprint 9 — Verticalization, domain and GTM scale

**Priority:** P3/P4

- Retail & QSR;
- Warehousing & Logistics;
- Manufacturing/Textile;
- Fuel & Infrastructure;
- Custom Solutions;
- Integrations;
- final domain/DNS/TLS/Auth/canonical/installer URLs;
- approved partner material only after formal confirmation;
- sales/support handoff playbooks;
- case studies only with customer permission.

## September staging definition

For the staging checkpoint, all of these must be true:

- production schema aligned and proven;
- auth/platform admin proven;
- core portal routes clean against production;
- current Analytics Studio capability working against production;
- reporting coherent;
- billing/trial state coherent;
- Sprint 3 website deployed and verified live;
- actual installer download path available;
- no critical RLS/security regression;
- demo/pilot tenant demonstrable end to end.

The following are **not** staging prerequisites:

- production fire/smoke;
- POS/Shopify production integration;
- attendance identity;
- true cloud live-stream wall;
- every vertical custom solution.

## Immediate next sequence

1. Close Sprint 1 production DB/auth parity using the guarded migration runbook.
2. Deploy/verify current `main` across portal + WordPress + backend services.
3. Run live route and visual smoke tests.
4. Complete real installer publication and set `NEXT_PUBLIC_INSTALLER_URL`.
5. Run clean Windows lifecycle + real-site agent soak/reboot acceptance.
6. Only then start Sprint 4 Control Room/QSR pilot work.

## Handoff rule

Use authorized local/Claude tooling only for tasks this environment cannot actually execute or prove, including:

- WatchLog production Supabase while the project remains absent from this connector;
- Coolify/server deployment/env changes without access;
- native Windows manual acceptance beyond CI;
- real recorder/camera field trials;
- code-signing certificate operations;
- DNS/domain control;
- payment-provider credentials;
- authorized external email/WhatsApp sends.

Every handoff must include the current commit, exact blocker, exact files, required commands/checks, acceptance evidence and a prohibition on weakening security/product honesty.

## Non-negotiable guardrails

- Recorder credentials stay onsite.
- No inbound recorder exposure by default.
- Recorded video stays on the recorder; do not overstate this as “nothing leaves the site.”
- No facial-recognition claims.
- No KFC/McDonald's customer claim without evidence/permission.
- No AWS/manufacturer partnership claim without formal status.
- No exact POS transaction claim from CCTV alone.
- No “live video” label for still refresh.
- No fire-safety guarantee without validation/certification.
- No autonomous stock mutation without reconciliation controls.
- No RLS/admin bypass to make demos work.
- No “100% complete” label until production and field gates pass.
