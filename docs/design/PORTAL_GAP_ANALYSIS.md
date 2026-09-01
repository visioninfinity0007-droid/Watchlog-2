# WatchLog Portal — Gap Analysis and Alignment Plan

**Status:** post-first-pass validation, 2026-09-01  
**Baseline:** `main` at `c843029`  
**Scope:** customer portal, Analytics Studio, onboarding, platform-admin surface, and the installer handoff that the portal initiates.  
**Reference:** current marketing-site source on `main`, especially Platform, Reporting, Site Health and Setup. The live sslip website was not reachable from this execution environment, so this pass does not claim a fresh live-browser visual inspection.

This document supersedes the original five-screen walkthrough below. The first pass was useful, but several of its largest visual defects have now been fixed. This version records what is actually still open, extends the audit to Analytics/onboarding/admin/installer, and makes the information-architecture and production-migration decisions explicit.

---

## 1. What the first pass already fixed

The following are **closed and must not be regressed**:

| Area | Closed on `main` | Result |
|---|---|---|
| Global portal skin | `af1f761` | Platform blue primary, ice links/focus, white mark on dark, elevated surfaces, ambient blue/violet field |
| Overview noise/data presentation | `64e9112` | Attention is summarised; agents are grouped under sites; online state is semantic |
| Incident still rendering | `c7cd8c1` | Missing images no longer render a broken browser image; incident types are humanised |
| Reports flagship visibility | `546a596` | A daily-report preview is visible before recipient management |
| Voice/component cleanup | `c843029` | Remaining portal em dashes removed in touched screens, role dropdown fixed, onboarding progress uses platform blue |

`main` CI is green at this baseline. Therefore this plan does **not** call for another global reskin or rewrites of those fixes.

---

# 2. Canonical product taxonomy — decision for website ↔ portal alignment

The website uses two related vocabularies:

- **Marketing capability pages:** Platform, Incidents, Reporting, Site Health.
- **Actual product-screen names in the website's own product explorer:** Overview, Incidents, Reports, Site Health.

That means two apparent naming mismatches are not actually mismatches.

## Decision

| Website capability | Portal screen | Decision |
|---|---|---|
| **Platform** | **Overview** | Keep `Overview`. “Platform” is the umbrella product/capability, while the website itself calls the dashboard screen “Overview”. |
| **Incidents** | **Incidents** | Already aligned. |
| **Reporting** | **Reports** | Keep `Reports`. “Reporting” is the capability; the website product explorer already labels the screen “Reports”. |
| **Site Health** | **missing dedicated screen** | **Add `/site-health/` and add `Site Health` to the portal navigation.** |
| On-site intelligence / business analytics | **Analytics** | Keep `Analytics`. It is an authenticated operational capability and does not need a marketing-nav rename. |

### Target customer navigation

`Overview | Incidents | Site Health | Analytics | Reports | Team | Settings`

Do not rename `Overview` to `Platform` and do not rename `Reports` to `Reporting`. Instead, use page headings/subcopy to reconnect screen names to the website language: e.g. Overview can use “One place to understand every site”; Reports can use “Daily reporting” as its page title while the nav remains `Reports`.

This resolves the section-naming question without creating a new mismatch with the website's own screenshots/explorer.

---

# 3. Production Analytics failure — decision and migration path

## Current failure

Production `/analytics` calls `wl_analytics_studio` and `wl_analytics_overview`. The frontend is present on `main`, but production reports:

> Could not find the function public.wl_analytics_studio in the schema cache

This is a backend deployment/version skew, not a frontend rendering bug.

The source migration chain is ordered and cumulative:

- `0024_analytics_studio.sql` — core schema, schedules/rules/events, tenant + agent RPCs
- `0025_analytics_agent_bootstrap.sql` — installer/agent bootstrap
- `0026_checkout_and_daily_analytics.sql` — catalog, checkout occupancy, overview aggregation
- `0027_daily_report_analytics.sql` — analytics added to daily report payload
- `0028_analytics_recommendations_and_schedules.sql` — purpose recommendations + schedule lifecycle
- `0029_platform_admin.sql` — platform-admin control plane which also assumes the analytics tables exist in its cross-tenant views

## Decision: apply the **whole pending train**, never a hand-picked RPC

Production should be brought to the repository migration ledger using the existing migration runner:

1. From a trusted deployment/admin environment containing the WatchLog production DB credentials, run:
   `python prototype/supabase/apply_migrations.py --status`
2. Review `PENDING` and especially any `CHANGED SINCE APPLIED` state. Do **not** use `--force` to hide drift.
3. Apply all pending files in filename order with:
   `python prototype/supabase/apply_migrations.py`
4. Re-run `--status`; every repository migration through the intended production head must show `applied` with the matching SHA-256.
5. Verify the PostgREST surface as an authenticated tenant user:
   - `wl_analytics_catalog()`
   - `wl_analytics_studio()`
   - `wl_analytics_overview(...)`
   - create/read/update/delete schedule path
   - create/read/update/delete monitoring-rule path
6. Verify a tenant `viewer` cannot mutate Analytics configuration, while owner/admin can.
7. Verify a normal tenant user cannot call platform-admin data surfaces.
8. Run one agent config poll and one analytic-event ingest to prove portal configuration reaches the local agent and measurements return.
9. Reload/wait for the PostgREST schema cache only after the SQL is confirmed present; a cache reload is not a substitute for the missing migration.

### Current execution blocker

The Supabase connector available in this session does **not** have the WatchLog project. `list_projects` exposes only a different project, and direct access to WatchLog project ref `oyvgubyxmjlijiczjona` returns permission denied. Therefore the migration path is decided, but the production SQL cannot honestly be marked applied from this session until WatchLog Supabase access is granted.

### Additional correctness gate before calling Analytics production-ready

Applying the migration train fixes the missing function, but the current source also needs a semantic/authz review before Analytics is accepted as final:

- `visitor_flow`, `vehicle_flow` and `boundary_monitoring` can all be implemented as `line_crossing`; aggregation must preserve the **business meaning** of a rule rather than infer meaning only from object class/event type.
- Site Health should be an always-on platform capability, not something a customer “adds” as a video analytics rule.
- Purpose-aware recommendations already exist in `0028`, but the current Studio frontend primarily uses the site-type pack and does not yet intersect it with camera purpose.
- Analytics writes must be owner/admin only; viewers should remain read-only.

These are P0/P1 production-hardening items in the fix plan below and should be closed before customer analytics validation.

---

# 4. System-level remaining gaps

## 4.1 Visual design

The global palette and surface system now match the website well enough to stop treating the portal as a different brand. The remaining gap is **composition**, not color.

The website uses strong page heads, outcome-led section copy, icon-led cards, 2-column feature sections, visual anchors, and deliberate alternation of dense and quiet blocks. Most portal pages still render as repeated `h2 → card/table → h2 → card/table` stacks.

The global portal `h2` style is still a tiny uppercase section label. That works as an eyebrow, but not as the only section hierarchy across operational screens. Pages need a real `h1`, short explanatory copy, and stronger card headers instead of making every subsection look like a form label.

## 4.2 Layout

- Team, Settings and Incidents remain especially table/form-heavy.
- Overview now has useful data grouping, but the first viewport lacks a strong page title and activity/health narrative.
- Analytics has the richest layout, but uses its own violet-heavy micro-system and does not fully inherit the global blue-primary decision.
- Tables need responsive/card fallbacks on narrow screens; hiding columns alone is not enough for the most important incident/site information.

## 4.3 Communication

The tone has improved, but several developer/system concepts remain visible:

- Overview still shows raw event enums in “Recent incidents”, “Event types”, and “Latest events”.
- Several timestamps are raw `toLocaleString()` rather than a consistent `Sep 1, 10:16 PM` / `5m ago` pattern.
- Analytics Studio exposes implementation language such as `sample every`, object classes and rule types before explaining the business outcome.
- Installer/setup output still exposes ports, drivers, firmware and scan mechanics at equal visual weight to the customer's task.

The website consistently leads with the operational outcome and puts mechanics second. The portal should do the same.

## 4.4 Functional consistency

The largest functional mismatches are:

1. Analytics production schema missing.
2. Dedicated Site Health screen missing.
3. Reports still offers a one-destination “WhatsApp + Email” UX (`Number, then edit for email`), which is not a valid data model even before either provider is connected.
4. Analytics Studio does not fully consume purpose recommendations and can misrepresent the semantic purpose of technically-similar line-crossing rules.
5. Onboarding says the agent is “a single program. Nothing to install”, while the product actually ships `WatchLog-Setup.exe` as installed software.
6. Settings defaults billing provider to `mock` if the environment variable is absent; production should fail closed rather than quietly look like a sandbox.

---

# 5. Screen-by-screen audit

## 5.1 Overview (`/dashboard/`)

### Visual / layout
**Improved:** grouped sites, semantic online state, summary banner, incident cards and event bars create far more structure than the original page.

**Remaining:** the screen begins with an alert/banner rather than a product-level page head. The website's Platform page leads with “One place to understand every site”; the real product should have the same calm orientation before showing exceptions. Recent incidents, event types and latest events then become one long vertical stack.

### Communication
- Change the opening from “things need attention” as the page's dominant first message to a compact Site Health summary: “2 sites healthy · 1 camera needs attention”.
- Humanise raw event types everywhere, not only on `/incidents/`.
- Standardise timestamps and avoid showing device/system detail unless useful to an operator.

### Functionality
- Validate dashboard still loading states: the recent-shot cards still assign an undefined image source while a requested still is pending; use the same explicit placeholder treatment as Incidents.
- Add a compact activity trend or “last 7 days” visual to make Overview match the website product screenshot/value proposition without duplicating full Analytics.
- The full camera/agent health drill-down should link to the new Site Health screen.

**Priority:** P1 after Site Health and Analytics production recovery.

---

## 5.2 Incidents (`/incidents/`)

### Visual / layout
The broken-image bug and raw type labels are fixed, but the screen is still a filter card over a table. The website sells the still as the reason an incident is worth opening; an 84×47 thumbnail does not carry that value.

### Communication
Keep the current human type labels. Add a page head such as “Incidents worth reviewing” and short copy that reinforces local filtering. The empty state should distinguish “no validated incidents” from “no camera/agent data”.

### Functionality
- Add a selected incident detail drawer/panel with a large still, site, camera, time, type and relevant event metadata.
- Convert the filter card into a compact toolbar with result count.
- Humanise timestamps and provide clear “no still” behavior.

**Priority:** P1.

---

## 5.3 Site Health (`/site-health/`) — **new required screen**

This is the largest website ↔ portal feature gap.

The website sells four specific health signals:

1. Camera last seen
2. Recorder/camera faults
3. Agent reporting status
4. Per-site health

The portal already receives much of this data in `wl_portal_overview`; it is simply compressed into the Overview attention block.

### Target layout
- Page head: “Know when something goes quiet.”
- Top summary: healthy sites, cameras reporting, agents online, open faults.
- Site health cards: one card per site, with overall status and last agent contact.
- Camera table/list within site: camera, last event/last seen, health state.
- Fault history: recorder/camera faults with clear human labels.
- No implication of instant paging; copy must match the website: Site Health is surfaced in the portal and the daily report.

### Functionality
Reuse existing tenant-scoped health data first. Add a new RPC only if camera-level last-seen data is not available in the current overview payload. The screen must not depend on Analytics migrations to render baseline recorder/camera health.

**Priority:** P0/P1. This is the explicit IA addition approved by this analysis.

---

## 5.4 Analytics Overview (`/analytics/`)

### Visual / layout
This is already the most website-like portal screen: page head, metrics, a chart, coverage panel and rule table. However its local CSS still treats violet as the primary interactive accent in several places. Primary actions/current states should move to platform blue, with violet retained for secondary/gradient accents and ice for dark links/data-series contrast.

### Communication
“Site intelligence” is a good authenticated heading. Metric hints should describe outcomes rather than implementation (“Entrances counted” rather than “line crossings”) where possible.

### Functionality
- **P0:** production DB migration mismatch currently makes the entire screen fail.
- Add Schedules to the analytics subnav for consistency with the Schedules page.
- Handle “Analytics not configured yet” separately from database/RPC failure.
- Keep Site Health out of Analytics counts/rules; it belongs to the dedicated health surface.

**Priority:** P0.

---

## 5.5 Analytics Studio (`/analytics/studio/`)

### Visual / layout
The site/camera sidebar + editor architecture is appropriate and richer than other portal pages. Keep it. Align violet-heavy current states/controls to the global blue-primary system rather than rewriting the whole module.

### Communication
The opening copy is strong: site type → camera purpose → monitoring geometry. Keep business labels such as Visitor Flow, Vehicle Flow, Boundary Monitoring, Dwell and Checkout Activity. Continue to state that Checkout Activity is occupancy/activity, not transaction count.

### Functionality
- Use both `packs[site_type]` **and** `purpose_recommendations[camera.purpose]`; do not recommend checkout analytics to a perimeter camera merely because the site is retail.
- Preserve an explicit semantic `analytic_key` through rule → measurement → historical aggregation. Do not infer “Visitor Flow” merely because a person crossed some line.
- Remove Site Health from “Add monitoring goal”; display it as an always-on capability instead.
- Switching site should deterministically select its first camera.
- Close polygon geometry visually and validate 3+ points.
- After requesting a camera still, poll/refresh until it arrives or show a clear timeout/retry state.
- Add Schedules to the subnav.
- Owners/admins configure; viewers read only.

**Priority:** P0 semantics/authz before production Analytics validation; P1 UX polish.

---

## 5.6 Analytics Schedules (`/analytics/schedules/`)

### Visual / layout
The two-column published-schedules/editor layout is sound and aligned with the richer website card language.

### Communication
The current “Business hours / Night shift / Weekend” quick starts are understandable. Keep the explicit note explaining overnight windows.

### Functionality
- Show how many monitoring rules use a schedule before allowing deletion.
- The UI currently edits the first time window per day even though the JSON model can represent arrays of windows. Either support multiple windows or explicitly constrain v1 to one window/day in both validation and copy.
- Keep subnav consistent across all three Analytics routes.

**Priority:** P1.

---

## 5.7 Reports (`/reports/`)

### Visual / layout
The new daily-report preview fixes the original “flagship deliverable is invisible” problem. The remaining layout is still preview → large recipient form → recipient table → delivery table. Convert this into a clear 2-column first section (latest/preview + delivery settings) followed by history.

### Communication
- Keep portal nav label **Reports**. It is already the screen label used by the website product explorer; the marketing capability remains “Reporting”.
- Replace the hardcoded sample site/date once real report data is available. A hardcoded “AKSS Head Office” should not appear in another tenant.
- Reconcile displayed report time with the actual scheduler; do not promise `07:00` if production dispatch is configured differently.

### Functionality
- **High:** `both` currently accepts one destination and tells the user “Number, then edit for email”. A phone number and email address must be separate fields, even when delivery providers are disabled. Fix the portal/database contract independently of external provider credentials.
- Prefer showing the latest generated report/summary from tenant data rather than only an example.
- Humanise delivery dates and errors.

**Priority:** P0 data-contract fix; P1 layout.

---

## 5.8 Team (`/team/`)

### Visual / layout
Functionality is adequate, but the page remains visually thin. The website explains owner/admin/read-only as a product benefit; the portal should make roles understandable before the invite form.

Target first section: three compact role cards (Owner / Admin / Viewer) with plain-language permissions, then an invite panel beside a team summary. Members and pending invites follow.

### Communication
Use the same role labels everywhere. Current invite choices are friendly, but existing-member role dropdowns fall back to raw `owner/admin/viewer` labels.

### Functionality
- Add a copy-invite-link action rather than dumping the entire token URL into a success sentence.
- Confirm destructive remove/revoke actions.
- Keep no hard dependency on email invitations; manual invite links are valid for this stage.

**Priority:** P2 after core operational screens.

---

## 5.9 Settings (`/settings/`)

### Visual / layout
This is still the weakest IA screen. Plan/billing, site creation, enrollment codes and installer download are unrelated jobs placed on one long page. That is why it feels color-noisy and form-heavy even after the global reskin.

### IA decision
Keep `Settings` as one top-level nav item, but split it internally into subroutes/tabs rather than adding more top-level navigation:

- **Account & Plan** — trial/subscription state, plan options, payment history, account/company basics
- **Sites & Setup** — sites, setup state, enrollment code, installer/setup actions

This keeps the customer navigation compact while separating commercial administration from physical-site onboarding.

### Communication
- “Sites & Setup” should reuse the website Setup language: Windows PC at the site, same network as recorder, recorder credentials stay local, outbound-only connection.
- Reduce color as decoration. Green/amber/red remain semantic states only; primary actions stay blue.

### Functionality
- Production must fail closed if billing provider/config is missing; `mock` should be an explicit staging/demo choice, not the silent default.
- Enrollment codes should be clearly single-use with expiry, with copy/regenerate affordances.
- Installer state should say whether a site is awaiting the Site Agent, connected, cameras discovered, or ready.

**Priority:** P1.

---

## 5.10 Onboarding (`/onboarding/`)

### Visual / layout
The branded card and live setup progress are now coherent with the portal. No full redesign needed.

### Communication
There is one important contradiction: onboarding says the agent is “A single program. Nothing to install”, while Settings and the actual release use `WatchLog-Setup.exe` as installed software. Replace with “Download WatchLog for Windows” / “Run the setup”.

The rest is well aligned with the website Setup narrative: PC at site, same network, recorder credentials stay local, enrollment code, live progress.

### Functionality
Keep the live setup-state poll. If the installer URL is not configured, show a neutral “Your setup engineer will provide it” state rather than implying a signed public download exists.

**Priority:** P1 copy fix.

---

## 5.11 Login / Signup / Invite

These are deliberately sparse and can remain so. They now inherit the correct dark field, white mark, blue primary action and ice links.

Remaining polish is secondary: consistent security reassurance, friendlier password/account recovery when implemented, and clear invite expiry handling. Do not turn authentication into a marketing landing page.

**Priority:** P3.

---

# 6. Platform Admin audit

Platform Admin is an internal control plane rather than a screen the marketing website promises to customers, so it should **not** mirror the website IA. Its current Overview/Tenants/Operations/Billing/Audit/Admins structure is appropriate.

Alignment requirements are nevertheless the same:

- same WatchLog mark/tokens/blue-primary hierarchy;
- humanised timestamps and statuses;
- semantic color only;
- no raw secrets/recorder credentials;
- cross-tenant actions audited with actor + reason;
- support/read roles cannot mutate commercial or tenant state.

The most important admin dependency is the same as Analytics: production must actually have `0029_platform_admin.sql` applied before the control plane can be considered deployed.

**Priority:** security verification P0, visual refinements P2.

---

# 7. Demo / validation data

The product cannot be judged visually with a tenant where every agent is offline and one site is repeated many times.

`tools/seed_demo.py` already contains the right safety principle: it touches only the tenant belonging to `demo@watchlog.test`, tags all synthetic events as demo data, and never edits AKSS/customer data. Preserve that hard boundary.

## Decision

Use a **dedicated demo tenant**, not the AKSS/live-validation tenant, as the canonical visual walkthrough account.

Enhance the demo seed so it mirrors the product story shown on the website:

- 3 sites: Karachi Head Office, Warehouse, Factory Floor
- one healthy Site Agent per site
- realistic camera names and a healthy majority
- one deliberately silent camera / one fault so Site Health has something useful to show
- realistic incidents across person, car and motorcycle with stills
- seven days of analytics measurements once the Analytics migration is live
- report/delivery history sample data clearly marked as demo
- deterministic/idempotent reseed

The target is **healthy with one explainable exception**, not “everything green” and not “everything broken”.

**Priority:** P1, after production schema is compatible with the seed.

---

# 8. Installer ↔ website ↔ portal alignment

There are currently two installer definitions plus the console setup wizard:

- contractual NSIS: `prototype/installer/nsis/watchlog.nsi`
- Inno: `prototype/installer/watchlog.iss`
- interactive setup: `prototype/agent/setup_wizard.py` + analytics wrapper

This creates avoidable drift. Both installer scripts currently identify the publisher as Vision Infinity, use version `0.2.0`, and have a placeholder publisher URL. The setup wizard is technically useful but presents as an engineering console.

## Decision

1. **NSIS is the authoritative customer release path.** Mark Inno as legacy/non-release or generate both from one metadata source; do not maintain two independent “production” installers.
2. Keep the visible product brand **WatchLog** everywhere. Legal publisher metadata can remain the actual legal publisher; do not invent a legal entity for cosmetic alignment.
3. Remove `watchlog.example` from release output. Publisher URL must be injected at build time when a production domain exists, or omitted rather than shipping a fake URL.
4. Align release version with the actual Agent/product version.
5. Restructure the console copy as a simple branded step flow, without changing the proven discovery logic:
   - `1/4 Find your recorder`
   - `2/4 Verify the recorder login`
   - `3/4 Confirm site and camera roles`
   - `4/4 Link this site to WatchLog`
6. Keep ports, driver names and low-level diagnostics behind a troubleshooting/verbose path rather than putting them at the same level as customer instructions.
7. Use the website Setup language verbatim in intent: same network, Windows PC stays on, credentials stay on that PC, outbound-only connection, site appears in portal after enrollment.

This does not require a graphical installer rewrite. The goal is a coherent WatchLog setup experience around the proven hardware discovery path.

**Priority:** P1.

---

# 9. Prioritised implementation plan

## P0 — functional / release blockers

1. **Analytics production schema:** reconcile migration ledger and apply the full pending migration train in order. Current session is blocked by missing WatchLog Supabase permission.
2. **Analytics semantics + authorization:** preserve analytic business key, owner/admin writes only, viewer read-only, Site Health not a configurable video rule.
3. **Add dedicated `/site-health/` customer screen and nav entry.** Reuse existing health payload first.
4. **Fix Reports “both” destination contract** so phone and email are separate inputs/data fields regardless of provider availability.
5. **Production authz regression:** prove tenant isolation and platform-admin denial after migrations.

## P1 — product alignment and validation quality

6. Overview page head + calmer health summary + raw enum/timestamp cleanup + shot-loading parity.
7. Incidents detail experience with large still and compact filter toolbar.
8. Analytics Studio purpose-aware recommendations, deterministic site/camera selection, polygon closure, snapshot polling, consistent subnav/colors.
9. Settings split into Account & Plan / Sites & Setup; onboarding copy reconciled with installed Agent.
10. Seed a realistic dedicated demo tenant with healthy multi-site data and one controlled exception.
11. Align NSIS/setup-wizard brand narrative and remove placeholder release metadata.

## P2 — polish

12. Team role explanation cards, copy-invite-link action, confirmation for destructive actions.
13. Reports richer latest-report/history composition and humanised delivery presentation.
14. Platform Admin visual/token/timestamp polish after security behavior is proven.

## P3 — later quality

15. Auth/account-recovery polish, richer mobile table-to-card transformations, optional illustrations/empty-state visuals.

---

# 10. Acceptance criteria

The portal alignment increment is complete when all of the following are true:

### Product / IA
- Customer nav is `Overview | Incidents | Site Health | Analytics | Reports | Team | Settings`.
- Overview and Reports keep those names; website capability mapping is documented as Platform → Overview and Reporting → Reports.
- Site Health exists as a first-class customer screen and shows the four signals sold by the website.

### Analytics
- Production no longer reports missing Analytics RPCs.
- Migration ledger matches repository checksums for the applied production head.
- Owner/admin can configure Analytics; viewer cannot mutate.
- Visitor Flow, Vehicle Flow and Boundary Monitoring remain semantically distinct in historical measurements.
- Site Health is always-on platform health, not an optional video rule.

### Design / communication
- Primary actions are platform blue; violet is accent/gradient; ice is the dark-link/data accent.
- Every operational page has a clear page head and at least one visual/decision anchor beyond a bare table.
- No raw enum labels or inconsistent timestamps in customer-facing primary views.
- Status colors are semantic and never the only carrier of meaning.

### Demo / validation
- Dedicated demo tenant shows realistic multi-site healthy data with one controlled exception.
- Demo data is unmistakably tagged and seeding cannot touch a real tenant.

### Installer
- Portal onboarding, website Setup and installer wizard describe the same sequence and requirements.
- Release installer has no placeholder URL/version drift and visibly presents the WatchLog product brand.

### Regression
- Portal production build passes.
- Existing first-pass fixes remain intact.
- Tenant-isolation and platform-admin authorization tests pass against the target database before deployment sign-off.
