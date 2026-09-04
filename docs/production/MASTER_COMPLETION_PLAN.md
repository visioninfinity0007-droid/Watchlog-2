# WatchLog — Master Completion Plan

**Re-baselined:** 2026-09-04  
**Repository baseline:** `main` `4efa9a645cc0c2bffe10f0f5f360195fb2fbf751`  
**Current audit:** `docs/audit/CURRENT_STATE_2026-09-04.md`  
**Acceptance source:** `docs/production/PRODUCTION_GATES.md`  
**External blockers:** `docs/production/CLIENT_DEPENDENCIES.md`

This document replaces the stale August/September phase-status narrative as the current execution plan.

## Operating rule

A task is **DONE** only when its acceptance gate is proven at the correct layer.

- Repository/CI evidence proves code/build behavior.
- Production evidence proves the deployed schema/services/routes.
- Field evidence proves real recorder/camera behavior.
- Client intent defines roadmap/commercial direction.

Do not convert one kind of evidence into another.

## Product direction

WatchLog is evolving from an incident/reporting product into a **Video Analytics & CCTV Intelligence Platform** built on the existing SaaS core.

Use three layers:

1. **Core SaaS Platform** — onboarding, sites, cameras, incidents, Site Health, Analytics Studio, reports, team, settings, billing and Platform Admin.
2. **Advanced Analytics Modules** — people/visitor flow, vehicle flow, dwell/loitering, later fire/smoke and additional justified object analytics.
3. **Industry / Custom Solutions** — QSR/control room, warehousing/logistics, manufacturing/textile, fuel/infrastructure and business-system integrations.

The repository already contains substantial Core SaaS and Analytics Studio functionality. Extend it; do not rebuild it.

## Current reality summary

### Strong existing foundation

- multi-tenant auth/tenant model;
- first-site onboarding/enrollment;
- recorder discovery/drivers/camera sync architecture;
- Overview, Incidents, Site Health, Analytics, Reports, Team, Settings;
- Analytics Studio lines/zones/schedules/dwell/site types/camera purposes;
- Visitor Flow, Vehicle Flow, Boundary Monitoring, Zone Activity, Dwell and After-Hours primitives;
- local person/car/motorcycle ONNX inference and fail-open incident filtering;
- reporting service and analytics report sections;
- billing/entitlement foundation;
- Platform Admin foundation;
- WordPress marketing/design foundation;
- NSIS/release tooling and CI contracts.

### Must upgrade before calling the core production-ready

- production DB parity through the genuinely pending migrations;
- current deployed revision/health verification;
- platform-admin production authorization;
- customer-grade graphical Windows setup;
- protected local recorder credentials;
- genuine release artifact/distribution path;
- clean Win10/11 lifecycle acceptance;
- current website/product-claims alignment;
- hardware field evidence.

### New modules / R&D

- Control Room and saved multi-camera layouts;
- collective/QSR reporting;
- support/after-sales operations workspace;
- integration framework;
- fire/smoke R&D;
- clip extraction/playback research;
- true live streaming architecture only if separately approved;
- POS/Shopify/attendance/CRM connectors.

## Sprint plan

### Sprint 0 — Production truth, source-of-truth cleanup and release baseline

**Priority:** P0  
**Target:** 4–5 September

Goals:

- establish one authoritative current-state record;
- remove stale “done”/“blocked” contradictions;
- fix immediate release-tooling defects;
- create a genuine Windows release path separate from the fast NSIS manifest contract.

Tasks:

1. Add `CURRENT_STATE_2026-09-04.md`.
2. Reconcile this master plan, `PRODUCTION_GATES.md`, `CLIENT_DEPENDENCIES.md` and `PUBLIC_CLAIMS_MATRIX.md`.
3. Fix `tools/build_windows_release.ps1` PowerShell parser defect.
4. Allow explicit/environment Supabase public release configuration without requiring a local `.env` on CI.
5. Add anti-stub size checks.
6. Add a genuine AI-enabled Windows release workflow that builds, self-tests, packages, checksums and uploads `WatchLog-Setup.exe`.
7. Run branch CI and verify the new release workflow when repository variables are available.
8. Record live deployment/DB verification as a separate gate rather than inferring it from repo state.

**Sprint 0 status:** IN PROGRESS on `sprint0/production-truth-release-baseline`.

### Sprint 1 — Production database, auth and core portal alignment

**Priority:** P0  
**Target:** 5–7 September

1. Take production backup/snapshot.
2. Re-read actual migration boundary.
3. Apply only genuinely pending migrations in order through current `main`.
4. Verify tables/RPCs/RLS/SECURITY DEFINER search paths.
5. Verify `platform_admins`, `wl_platform_me()` and platform-owner account.
6. Run tenant/platform denial tests.
7. Run Supabase security advisor.
8. Smoke Overview / Incidents / Site Health / Analytics / Reports / Team / Settings / Admin.
9. Preserve existing tenant/site/camera/auth data.

**Current access limitation:** WatchLog production Supabase is not exposed through this ChatGPT connector. If unresolved, perform via Claude/local Supabase admin using the bounded migration procedure in the dependency register.

### Sprint 2 — Customer-grade Windows installer and onboarding

**Priority:** P0  
**Target:** 5–9 September in parallel with Sprint 1

1. Build graphical WatchLog setup application.
2. Reuse existing recorder discovery/driver/enrollment backend logic.
3. Replace terminal prompts with: Welcome → Site Code → Find Recorder → Recorder Login → Camera Discovery → Connecting → Success.
4. Remove visible console from normal background-agent behavior.
5. Move recorder password out of plaintext INI into Windows-native protected storage (DPAPI/Credential Manager or equivalent).
6. Migrate existing local installations safely.
7. Add graphical diagnostics.
8. Preserve spool/state on upgrade.
9. Publish versioned installer + stable latest URL.
10. Set `NEXT_PUBLIC_INSTALLER_URL`.
11. Sign inner agent + final installer when certificate is available.
12. Run Win10/11 lifecycle/DPI/failure acceptance.

### Sprint 3 — September staging website/product alignment

**Priority:** P0/P1  
**Target:** 7–10 September

1. Keep hero line: **Make your existing cameras useful every day.**
2. Reframe umbrella positioning to **Video Analytics & CCTV Intelligence**.
3. Make Analytics Studio a first-class product story.
4. Update Retail to Retail & QSR positioning.
5. Add/plan Control Room (Pilot), Fuel & Infrastructure (Custom Solution), Integrations and Custom Solutions.
6. Use explicit maturity labels: Available / Pilot / Coming Soon / Custom Solution.
7. Keep KFC/McDonald's as target/use-case examples only unless actual customer status is confirmed.
8. Do not claim AWS/manufacturer partnerships before formal approval.
9. Resolve Starter vs Standard naming only after client approval and change all code/content/tests together.
10. Fix/reverify sitemap, canonical, OG and robots.

### Sprint 4 — QSR / Control Room pilot foundation

**Priority:** P1  
**Target:** 10–16 September

1. Add dedicated Control Room route/nav.
2. Add saved layout model and 2×2 / 3×3 / 4×4 views.
3. Add site/camera grouping.
4. Show camera health, last-seen, purpose and real event/incident state.
5. Allow explicitly labelled latest/current still refresh where the existing agent path supports it.
6. Fullscreen operations mode.
7. Add QSR camera-purpose presets.
8. Reuse visitor-flow/dwell/occupancy/zone/schedule rules.
9. Add camera-wise, site-level and collective QSR reporting.
10. Do not label still refresh as live video.

### Sprint 5 — Field validation, clip extraction and analytics calibration

**Priority:** P1  
**Target:** 13–20 September

Capture per pilot site:

- recorder vendor/model/firmware;
- camera model/channel/purpose/FOV/resolution/FPS;
- recording mode;
- snapshot/playback/clip APIs;
- timestamp behavior;
- pre/post-event duration;
- concurrency limits;
- network conditions.

Engineering:

1. Build recorder-specific clip adapter only where field-proven.
2. Define event→clip timestamp mapping.
3. Measure people-flow/count accuracy.
4. Measure line-crossing direction accuracy.
5. Measure dwell/loitering behavior.
6. Calibrate sample rates/thresholds.
7. Produce camera-placement guidance.
8. Re-run long agent soak/reboot acceptance.

### Sprint 6 — Advanced analytics R&D

**Priority:** P2

- extend people/occupancy/queue/dwell using the existing Analytics Studio engine;
- extend vehicle flow/entry-exit/forecourt use cases;
- run a separate fire/smoke model+dataset track;
- expand object classes only when a real use case + dataset justify them;
- never promise generic “detect anything”.

Every new analytic needs: model definition, use case, test set, accuracy evidence, failure semantics and maturity label.

### Sprint 7 — Reporting, payment and support operations

**Priority:** P2

Reporting:

- camera report;
- site report;
- multi-site/executive report;
- QSR template.

Payment:

- finalize gateway vs interim bank-transfer path;
- if bank transfer is approved, build authenticated private proof upload + admin verification + audit trail + backend-only subscription activation.

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
- event/outbox delivery;
- signatures;
- retry/idempotency;
- connector health;
- audit/logging;
- secret handling.

Then implement **one prioritized signed pilot connector** (POS, Shopify, attendance or CRM/API). Do not build all connectors at once.

Shopify stock automation starts as reconciliation/approval, not autonomous stock mutation from vision alone.

### Sprint 9 — Verticalization, domain and GTM scale

**Priority:** P3/P4

Product/website packages:

- Retail & QSR;
- Warehousing & Logistics;
- Manufacturing/Textile;
- Fuel & Infrastructure;
- Custom Solutions;
- Integrations.

Operational:

- final domain/DNS/TLS/Auth/canonical/installer URLs;
- approved AWS/manufacturer/channel partnerships only after formal confirmation;
- sales/support handoff playbooks;
- customer case studies only with permission.

## September staging definition

For the staging checkpoint, all of these must be true:

- production schema aligned and proven;
- auth/platform admin proven;
- core portal routes clean against production;
- current Analytics Studio capability working against production;
- reporting coherent;
- billing/trial state coherent;
- website reflects broader but honest positioning;
- actual installer download path available;
- no critical RLS/security regression;
- demo/pilot tenant demonstrable end to end.

The following are **not** staging prerequisites:

- production fire/smoke;
- POS/Shopify production integration;
- attendance identity;
- true cloud live-stream wall;
- every vertical custom solution.

## Handoff rule

Use Claude/local tooling only for tasks this environment cannot actually execute or prove, including:

- WatchLog production Supabase while the project remains absent from this connector;
- Coolify/server deployment/env changes without access;
- native Windows manual acceptance beyond CI;
- real recorder/camera field trials;
- code-signing certificate operations;
- DNS/domain control;
- payment-provider credentials;
- authorized external email/WhatsApp sends.

Every handoff prompt must include the current commit, exact blocker, exact files, required commands/checks, acceptance evidence, and a prohibition on weakening security/product honesty.

## Non-negotiable guardrails

- Recorder credentials stay onsite.
- No inbound recorder exposure by default.
- No facial-recognition claims.
- No KFC/McDonald's customer claim without evidence/permission.
- No AWS/manufacturer partnership claim without formal status.
- No exact POS transaction claim from CCTV alone.
- No “live video” label for still refresh.
- No fire-safety guarantee without validation/certification.
- No autonomous stock mutation without reconciliation controls.
- No RLS/admin bypass to make demos work.
- No “100% complete” label until production and field gates pass.
