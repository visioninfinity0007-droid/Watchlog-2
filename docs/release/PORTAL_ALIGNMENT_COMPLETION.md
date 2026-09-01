# WatchLog portal alignment release completion ledger

**Release branch:** `portal/alignment-validation`  
**Pull request:** #12  
**Scope:** customer portal, Analytics Studio hardening, reporting, operational authorization, demo data and Windows installer alignment.

This document supersedes the implementation-status portions of
`docs/design/PORTAL_GAP_ANALYSIS.md`. The gap analysis remains the design/audit
record that explains why the work was required; this file records what is now
implemented and what still requires access to external production systems.

## 1. Source implementation: COMPLETE

### Customer navigation and information architecture

- [x] Customer navigation is `Overview | Incidents | Site Health | Analytics | Reports | Team | Settings`.
- [x] Overview remains the platform-wide operational home.
- [x] Reports remains the customer-facing reporting surface.
- [x] Site Health is a first-class customer surface rather than an Analytics rule.
- [x] Settings is split internally into `Account & Plan` and `Sites & Setup`.

### Overview and incidents

- [x] Overview has a clear page hierarchy, operational summary, site/agent state,
  recent stills, event mix and latest activity.
- [x] Raw event enum labels are humanised in primary customer views.
- [x] Relative timestamps are used for scanning with exact timestamps available
  for context.
- [x] Incident still loading/unavailable states are deterministic.
- [x] Incidents has site/type/window filters and a dedicated review dialog with a
  large still and exact event context.
- [x] Continuous recorder video is never presented as a portal capability.

### Site Health

- [x] Dedicated `/site-health/` route and navigation entry.
- [x] Site status and setup state.
- [x] Site Agent liveness.
- [x] Every discovered camera's last reported activity.
- [x] Quiet-camera context is described accurately as an activity signal rather
  than a guaranteed camera heartbeat.
- [x] Recorder/camera fault events carry site identity.
- [x] New tenant-scoped `wl_site_health_details(int)` read API.
- [x] No cross-tenant health lookup is accepted as an input argument.

### Analytics Studio

- [x] Business semantic identity is persisted as `analytic_key` on rules and
  measurements.
- [x] Visitor Flow, Vehicle Flow and Boundary Monitoring no longer collapse into
  one ambiguous line-crossing metric.
- [x] Site Health is always-on platform health and cannot be created as a video
  inference rule.
- [x] Owner/Admin can configure; Viewer is read-only at both UI and RPC layers.
- [x] Site type + camera purpose recommendations are combined.
- [x] Site changes deterministically select an available camera.
- [x] Configuration-still requests poll for the returned still.
- [x] Line and polygon geometry is normalized to the camera frame; polygons close
  visually.
- [x] Schedules are reusable, show rule references and refuse deletion while in
  use.
- [x] v1 schedule editor states the one-window-per-day constraint and supports
  overnight windows.
- [x] Measurements remain separate from incidents unless a rule explicitly
  promotes them.
- [x] Checkout Activity is occupancy in a configured checkout zone, never claimed
  as completed transaction counting.
- [x] No facial recognition, identity matching or cloud video processing added.

### Reporting

- [x] `WhatsApp + Email` no longer overloads one destination.
- [x] Canonical storage is one provider-specific endpoint per row.
- [x] `wl_add_recipient_v2` validates WhatsApp and email independently and writes
  two endpoint rows when the UI asks for both.
- [x] Legacy one-destination `both` rows are conservatively migrated instead of
  inventing a missing email address.
- [x] Reporter pairs each provider with its own destination.
- [x] Recipient management is Owner/Admin; Viewer is read-only.
- [x] Reports page exposes recipients and delivery history.
- [x] Example report content is labelled as example content and does not hardcode
  an unverified production send time.

### Team and operational authorization

- [x] Owner/Admin/Viewer roles are explained before invitation.
- [x] Invitation links are copyable.
- [x] Remove and revoke actions require confirmation.
- [x] Owner-only role promotion rules remain server enforced.
- [x] `wl_add_site` and `wl_issue_code` are Owner/Admin only.
- [x] Report-recipient writers are Owner/Admin only.
- [x] Analytics configuration writers are Owner/Admin only.
- [x] Billing mutations remain Owner only.
- [x] Enrollment codes are visibly single-use/expiring, can be regenerated and
  can be copied from Sites & Setup by Owner/Admin.
- [x] Viewer can see that an open enrollment code exists but the code value is
  withheld server-side and hidden in the UI.

### Billing behavior

- [x] Browser actions cannot authoritatively mark a tenant paid.
- [x] Paid-state activation remains provider-webhook/server controlled.
- [x] Portal no longer silently defaults an unspecified environment to the mock
  billing provider.
- [x] Checkout fails closed unless both an explicit provider and hosted checkout
  service URL are configured.
- [x] Detailed billing overview and direct financial-table reads are Owner-only.
- [x] Admin/Viewer can still see non-sensitive plan/account/entitlement state
  without receiving provider customer IDs, checkout records or payment history.
- [x] Draft pricing remains visibly marked provisional.

### Demo account

- [x] Seed is hard-scoped to the tenant belonging to `PORTAL_DEMO_EMAIL`.
- [x] Three curated sites: Karachi Head Office, Korangi Warehouse and Landhi
  Factory Floor.
- [x] One current Site Agent per site and realistic recorder/camera labels.
- [x] Synthetic incidents and stills are explicitly labelled as demo/sample data.
- [x] Fourteen days of semantic analytics are generated when the Analytics schema
  is present.
- [x] Synthetic delivery history is present for Reports walkthroughs.
- [x] Controlled health exception: Korangi Rear Perimeter has no activity for more
  than 24 hours.
- [x] Controlled fault exception: Korangi Loading Bay has a synthetic `video_loss`
  event within 24 hours.

### Installer and onboarding

- [x] NSIS is the only production installer technology.
- [x] Legacy Inno Setup release manifest removed.
- [x] Product/version metadata aligned to WatchLog Site Agent 0.3.0.
- [x] Visible product brand is WatchLog; legal publisher metadata remains Vision
  Infinity.
- [x] Fake/guessed publisher URLs are not embedded by default; a real production
  URL is injected explicitly at build time or omitted.
- [x] Installer aborts if interactive recorder setup fails.
- [x] Background startup is registered only after setup succeeds.
- [x] Setup wizard presents a four-step customer flow around the proven recorder
  discovery/login/camera/enrollment logic.
- [x] Portal onboarding and Sites & Setup use the same intent: Windows PC at site,
  same recorder network, recorder credentials stay local, outbound-only cloud
  connection.

## 2. Repository validation: COMPLETE

GitHub Actions gates cover:

- [x] Python compile.
- [x] Tracked-tree secret scan.
- [x] Migration lint.
- [x] Existing vision false-alarm tests.
- [x] Analytics local engine tests.
- [x] Analytics report rendering tests.
- [x] Analytics portal contract.
- [x] Portal/installer alignment contract.
- [x] Platform Admin contract.
- [x] Recorder push parser tests.
- [x] Capability probe tests.
- [x] Email template tests.
- [x] ONNX inference contract.
- [x] Pricing alignment.
- [x] Public-site copy check.
- [x] Next.js production static export.
- [x] Windows NSIS manifest compilation on a Windows runner.

The PR must remain unmerged if its latest head is not green.

## 3. Production release gates: EXTERNAL / NOT YET CLAIMED

These are intentionally not marked complete by source code or CI.

### WatchLog production database

- [ ] Confirm the production migration ledger and checksums.
- [ ] Apply the complete ordered train through
  `0035_billing_read_authz.sql`.
- [ ] Verify PostgREST exposes the new RPCs after the migration is proven.
- [ ] Run target-database tenant-isolation and role-authorization tests using
  disposable users/data, including proof that a Viewer receives
  `open_code = null` from `wl_sites()` and Admin/Viewer cannot read detailed
  billing tables or `wl_billing_overview()`.
- [ ] Run reporting endpoint and Analytics semantic smoke tests.
- [ ] Refresh the dedicated demo tenant only after schema compatibility is
  confirmed.

**Current environment blocker:** the connected Supabase account does not have
permission to WatchLog project `oyvgubyxmjlijiczjona`. This is an access gate,
not a reason to bypass the migration or deploy the frontend first.

### Deployment

- [ ] Deploy the migration-compatible portal build to the WatchLog Coolify
  service.
- [ ] Deploy matching reporter/agent services if their image/build changed.
- [ ] Smoke-test every customer route against the production database.

**Current environment blocker:** no Coolify connector/API is available in this
session.

### Windows release and hardware acceptance

- [ ] Build the real AI-enabled `WatchLog-Setup.exe` with the production public
  Supabase values on a Windows release host.
- [ ] Supply the real production publisher URL if one is approved.
- [ ] Authenticode-sign the artifact with the production code-signing
  certificate.
- [ ] Clean-install on a supported Windows PC, complete recorder setup, reboot and
  prove autostart.
- [ ] Run the contracted real-recorder / real-camera acceptance and false-positive
  tuning on the target hardware/sites.

These gates require external credentials, a signing certificate and/or physical
hardware. They must not be represented as complete until actually executed.

## 4. Safe release order

1. Latest PR head passes all repository CI jobs.
2. Production DB migration ledger is checked for drift.
3. Apply migrations in order through `0035`.
4. Run DB/authz/semantic/reporting smoke tests.
5. Refresh the dedicated demo tenant.
6. Merge PR #12.
7. Deploy the matching portal/services.
8. Run production route smoke tests.
9. Build/sign/install/reboot-test the real Windows release.
10. Complete real recorder/camera field acceptance.

The database-before-frontend order is mandatory because the aligned portal calls
new RPCs. Merging/deploying the portal first can create avoidable schema-cache
failures even when the frontend itself is correct.
