# WatchLog — Current State — 2026-09-04

**Audit baseline:** `main` at `4efa9a645cc0c2bffe10f0f5f360195fb2fbf751`  
**Purpose:** one factual baseline for the September staging and QSR/control-room expansion.  
**Rule:** code/CI evidence, deployed-production evidence, field evidence, and roadmap intent are separate states. A green repository check is not proof that production is running the same revision.

## Evidence reviewed

- Current private GitHub repository and `main`.
- Latest `main` CI run: run `33611100973`, conclusion `success` on 2026-09-02.
- Portal routes/navigation and platform-admin routes.
- Analytics Studio specifications, portal implementation, local analytics engine and reporting integration.
- Site Agent, recorder drivers/discovery, ONNX model packaging and Windows setup path.
- Supabase migrations through `0036`.
- WordPress theme, sitemap, homepage, navigation, pricing and public-claims documentation.
- Production/runbook documents.
- Current connector visibility for Supabase and Google Drive.
- Direct SQL evidence supplied during the 2026-09-04 production-database troubleshooting session.
- Latest client direction: broader Video Analytics SaaS, QSR/control-room pilot, fire/smoke, people/vehicle analytics, integrations and vertical solutions.

## Current repository baseline

### Core SaaS — strong foundation

The repository already contains a coherent multi-tenant CCTV SaaS core:

- authentication, signup, invitation and tenant routing;
- first-site onboarding and one-time enrollment codes;
- recorder discovery/drivers and camera discovery;
- Overview, Incidents, Site Health, Analytics, Reports, Team and Settings;
- Platform Admin: overview, tenants, operations, billing, admins and audit;
- reporting service and delivery history;
- billing model/provider abstraction/entitlements;
- WordPress marketing site;
- local Site Agent and recorder-push bridge;
- release/runbook/test infrastructure.

This product should be extended, not rebuilt.

### Analytics Studio — implemented foundation

Implemented Analytics Studio concepts include:

- site types and camera purposes;
- current-camera configuration stills;
- monitoring lines/polygons;
- schedules and dwell thresholds;
- class filters;
- versioned Site Agent configuration;
- local analytic events separated from incidents;
- Visitor Flow;
- Vehicle Flow;
- Boundary Monitoring;
- Zone Activity;
- Dwell / Time in Zone;
- After-Hours Activity;
- Checkout Activity as non-transactional activity;
- analytics recommendations/monitoring packs;
- daily-report analytics sections.

Current production detector classes remain `person`, `car`, and `motorcycle`.

Do not represent these as facial recognition, visitor identity, cross-camera re-identification, or exact POS/till transaction counting.

### New client scope that is not implemented on current main

- dedicated Control Room route and saved multi-camera layout manager;
- true live-video cloud wall;
- fire/smoke detector;
- attendance/visitor identity workflow;
- POS integration;
- Shopify inventory integration;
- attendance-machine integration;
- CRM integration layer;
- support/after-sales workspace in Platform Admin;
- generic broad object-classification promise.

These require separate product, integration, or R&D workstreams.

## Portal state

Current signed-in navigation:

1. Overview
2. Incidents
3. Site Health
4. Analytics
5. Reports
6. Team
7. Settings

Platform administrators additionally receive the Platform/Admin route.

The next major product module should be Control Room, but v1 must use real available semantics such as camera health, last-seen, recent incidents/events and explicit latest/current stills. It must not label still refresh as live video.

## Windows installer state

The existing release path is an engineering release, not the desired commercial installer.

Current facts on baseline `main`:

- `prototype/agent/build_exe.ps1` freezes the agent with PyInstaller `--console`.
- `prototype/agent/setup_wizard.py` uses terminal `input()` prompts.
- recorder credentials are persisted in the local INI file in plaintext.
- NSIS launches the setup process and registers the background agent.
- `tools/build_windows_release.ps1` is the authoritative packaging path.
- the ordinary CI `installer-contract` job stages a zero-byte agent solely to validate the NSIS manifest; that CI output is not a customer release.

Sprint 0 changes are introducing a separate genuine Windows release workflow that builds the full AI-enabled agent, runs packaged self-test, rejects suspiciously small payloads, checks SHA-256 and uploads the actual installer artifact.

Customer-grade graphical setup and Windows-native credential protection remain Sprint 2 work.

## Supabase / production database

Repository migrations exist through `0036`.

The WatchLog production project is **not visible** in the Supabase connector available to this ChatGPT session. The connector currently exposes only the unrelated `Al khalid` project (`jssitaduuhjvyznldfoc`). Do not apply WatchLog SQL there.

The latest direct production SQL evidence supplied by the operator showed:

- `wl_entitlement()` exists;
- `wl_reporting_enabled(uuid)` exists;
- `monitoring_rules` was absent;
- `platform_admins` was absent;
- `wl_platform_me` was absent;
- site-health/billing authorization functions from later migrations were absent;
- `sites.site_type` was absent;
- `cameras.purpose` was absent.

That evidence places the database at the `0023` boundary **at the time of that query**. Another tool/operator may have changed production afterwards, so production must be re-read before applying any migration.

Required next database procedure:

1. backup/snapshot;
2. prove the current boundary from schema objects;
3. apply only genuinely pending migrations in order;
4. verify RLS, SECURITY DEFINER search paths and RPCs;
5. verify platform-owner authorization;
6. run security advisor and tenant-isolation tests;
7. smoke all portal routes against production.

## Live deployment verification

Known architecture includes:

- marketing WordPress;
- portal;
- Supabase;
- recorder push bridge;
- report runner;
- billing service.

The temporary `sslip.io` endpoints could not be resolved from this execution environment during the 2026-09-04 audit, and no Coolify connector is available here. Therefore the exact deployed commit/image, current environment variables, live HTTP health and live visual parity are **not verified by this document**.

Any older document that says a deployed service is healthy remains historical evidence until re-checked.

## Website state

The custom WordPress site is a strong visual/product foundation. Current messaging centers on:

- existing CCTV;
- incident filtering;
- daily reports;
- Site Health;
- local person/car/motorcycle filtering.

The homepage already contains the umbrella line:

> Make your existing cameras useful every day.

The updated client direction requires the website to evolve toward **Video Analytics & CCTV Intelligence**, while using explicit maturity labels for Available, Pilot, Coming Soon and Custom Solution.

The site must not imply that KFC/McDonald's are customers, that AWS/manufacturer partnerships exist before approval, that WatchLog performs facial recognition, or that roadmap modules are currently available.

## Source-of-truth corrections required

Older repository documents contain stale statements. In particular:

- `PUBLIC_CLAIMS_MATRIX.md` previously classified all behavioural analytics as unbuilt even though Analytics Studio now implements flow, zone, boundary, dwell and schedule analytics.
- `MASTER_COMPLETION_PLAN.md` still contains historical status text that says AI/reporting/billing/portal work is barely started even though those code paths have since landed.
- `CLIENT_DEPENDENCIES.md` still lists GitHub Actions minutes as a blocker even though the latest `main` CI run succeeded on 2026-09-02.
- pricing is already publicly represented as Starter PKR 6,000, Growth PKR 12,000 and Enterprise “Talk to us”; the open decision from the latest meeting is naming/packaging (for example Starter vs Standard), not whether any pricing exists.

Sprint 0 updates these records around this baseline.

## Product maturity classification

### Strong existing foundation

- multi-tenant auth/authorization model;
- core portal;
- Incidents;
- Site Health;
- Analytics Studio configuration/engine;
- people/vehicle flow primitives;
- zone/boundary/dwell/schedule primitives;
- Reports foundation;
- Team;
- Platform Admin foundation;
- local person/car/motorcycle AI;
- recorder drivers/outbound-only architecture;
- WordPress design/marketing foundation;
- billing and entitlement foundation.

### Partial / must upgrade

- production DB parity;
- live deployment verification;
- installer UX/distribution/signing;
- local credential protection;
- end-to-end onboarding release path;
- collective/enterprise reporting;
- support operations;
- live commercial payment path;
- public claims/documentation alignment;
- hardware compatibility evidence.

### New product modules

- Control Room;
- saved multi-camera layouts;
- QSR operational presets;
- integrations management;
- support workspace;
- Fuel & Infrastructure solution packaging.

### R&D / field validation

- fire/smoke;
- clip extraction by timestamp and duration;
- true live streaming;
- broad object catalogue;
- field people-count accuracy;
- field dwell/loitering accuracy;
- recorder concurrency/load limits.

### Custom/integration workstreams

- POS;
- Shopify;
- attendance systems;
- CRM/API;
- manufacturing machine workflows;
- transaction-linked mobile/retail workflows.

## September staging definition

For the September staging checkpoint, “staged” means:

- production schema and auth are aligned and proven;
- core portal routes work against production;
- current Analytics Studio capabilities work;
- reporting works coherently;
- billing/trial state is coherent;
- website reflects the broader but honest product direction;
- an actual installer download path exists;
- security/RLS checks remain clean;
- one demo/pilot tenant can be demonstrated end to end.

It does **not** mean fire/smoke, POS/Shopify, attendance identity, a true cloud live-stream wall or every vertical custom solution is production-ready.

## Execution order

1. Sprint 0 — production truth, source-of-truth cleanup, release baseline.
2. Sprint 1 — production database/auth/core portal alignment.
3. Sprint 2 — customer-grade Windows installer/onboarding.
4. Sprint 3 — website/product staging alignment.
5. Sprint 4 — QSR/Control Room pilot foundation.
6. Sprint 5 — field validation, clip extraction and analytics calibration.
7. Sprint 6 — advanced analytics R&D.
8. Sprint 7 — reporting/payment/support operations.
9. Sprint 8 — integration platform.
10. Sprint 9 — verticalization/domain/GTM scale.

## Non-negotiable guardrails

- Recorder credentials stay onsite.
- No inbound recorder exposure by default.
- No facial-recognition claims.
- No exact POS transaction claim from CCTV alone.
- No “live” label for a still refresh.
- No fire-safety guarantee without validation/certification.
- No customer or partner claim without approval.
- No RLS/admin bypass for demos.
- No “100% complete” label until production and field gates pass.
