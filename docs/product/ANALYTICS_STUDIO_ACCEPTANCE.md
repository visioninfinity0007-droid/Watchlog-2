# Analytics Studio v1 acceptance checklist

**Release branch:** `portal/alignment-validation`  
**Product scope:** site/camera classification, local business measurements, schedules, incident promotion, reporting and the Windows Site Agent.

This checklist deliberately separates **implemented/source-validated behavior**
from **production/physical acceptance**. A checked source item means the behavior
exists in the release branch and is covered by a repository contract or unit
test where practical. It does not mean the WatchLog production database or a
real CCTV site has been exercised.

## 1. Source implementation

### Database and authorization

- [x] Site profiles, camera purposes, monitoring rules, reusable schedules,
  configuration stills and analytic measurements are modelled.
- [x] Tenant-scoped Analytics tables have RLS/read boundaries.
- [x] Portal reads derive the caller tenant; configuration writes require
  Owner/Admin.
- [x] Agent RPCs derive tenant/site from authenticated agent credentials.
- [x] Site analytics configuration version increments on relevant changes.
- [x] `analytic_key` preserves business meaning separately from technical rule
  type on both rules and measurements.
- [x] Measurements are idempotent by tenant + dedupe key.
- [x] Optional incident promotion is controlled server-side by the published
  rule, not trusted from the Site Agent.
- [x] Site Health is platform health and cannot be configured as a video rule.

### Site Agent

- [x] Analytics config is pulled outbound and cached locally for internet
  outages.
- [x] Analytic measurements spool durably and retry outbound upload.
- [x] Tracking is same-camera, short-lived centroid tracking only; no face
  recognition, biometrics or cross-camera identity.
- [x] Directional finite-segment line crossing.
- [x] Zone entry with object-class filtering.
- [x] Dwell with a minimum duration and one completion per in-zone visit.
- [x] Weekly schedules use site-local time and handle overnight windows.
- [x] Checkout Activity uses occupancy inside a configured zone; it does not
  claim completed transactions.
- [x] Occupancy changes require two consecutive changed samples, suppressing a
  one-frame detector miss without hiding a sustained count change.
- [x] `FairSampler` applies an aggregate `analytics_max_fps` budget, fair
  round-robin camera selection and an explicit effective sampling-quality
  status.
- [x] Incident filtering and Analytics share one thread-safe local detector in
  the production runtime.
- [x] Lean builds fail open for security incidents and pause Analytics
  measurement instead of fabricating data.

### Portal

- [x] Site type selector.
- [x] Camera purpose selector.
- [x] Purpose-aware + site-type monitoring recommendations.
- [x] Analytics Overview / Studio / Schedules navigation.
- [x] Owner/Admin editor; Viewer read-only.
- [x] Line and polygon geometry editor using normalized 0..1 coordinates.
- [x] New geometry requires a real camera configuration still.
- [x] An existing configuration still can be refreshed; polling waits for a new
  `captured_at`, and in-progress geometry is cleared against the new view.
- [x] Reusable weekly schedule editor with overnight windows and in-use deletion
  protection.
- [x] Visitor Flow, Vehicle Flow, Zone Activity, After-Hours and Checkout
  summaries.
- [x] Site Health remains a dedicated platform surface rather than an Analytics
  configuration rule.

### Installer and setup

- [x] Canonical production installer is NSIS only, WatchLog Site Agent 0.3.0.
- [x] Installer/agent setup asks site type and assigns/suggests camera purposes
  after recorder discovery.
- [x] Advanced lines, zones and schedules hand off to Analytics Studio.
- [x] Explicit packaged `--setup` is strict: cancellation/failure returns
  non-zero; success validates WatchLog enrollment and exits instead of entering
  the infinite runtime.
- [x] Existing background task is stopped for an upgrade and can be resumed if
  the new setup fails.
- [x] Add/Remove Programs registration is written only after recorder/enrollment
  validation and background-task startup have both succeeded.
- [x] Background task registration proves Windows reports it as `Running`.
- [x] Release script signs and verifies both the inner Site Agent and outer setup
  when a certificate is supplied, then calculates SHA256 from the final signed
  installer bytes.

### Reporting

- [x] Daily report contains Site Intelligence when analytic measurements exist.
- [x] CLI/manual and scheduled/n8n paths use the same canonical analytics-aware
  report composition.
- [x] Measurement events remain separate from security incidents unless a rule
  explicitly promotes them.
- [x] Reporting entitlement remains enforced before delivery.
- [x] WhatsApp and email are separate provider-specific delivery endpoints; the
  UI convenience `both` produces two rows rather than reusing one destination.

## 2. Repository validation

The CI workflow contains gates for:

- [x] Python compile and tracked-secret scan.
- [x] Migration lint.
- [x] Analytics rule-engine tests including line, polygon, dwell, schedules and
  occupancy jitter suppression.
- [x] Fair-sampler capacity/rate/fairness tests.
- [x] Canonical analytics-report rendering tests.
- [x] Analytics portal/source contract, including current-still geometry safety.
- [x] Portal/installer release contract, including strict setup and final-signing
  order.
- [x] Existing vision, recorder push, capability, email, pricing and platform
  authorization regressions.
- [x] Next.js production static export.
- [x] Windows NSIS manifest compilation contract.
- [ ] **Latest release-branch head is green across all CI jobs.** This box is
  checked only after the current head, not an earlier commit, completes CI.

## 3. Production / field acceptance — must be executed externally

### Production Supabase

- [ ] Inspect production migration ledger/checksums.
- [ ] Apply the ordered migration train through `0035_billing_read_authz.sql`.
- [ ] Prove Owner/Admin/Viewer Analytics authorization against the target DB.
- [ ] Prove a valid agent can pull only its own site's config and ingest only its
  own site's measurements.
- [ ] Prove cross-tenant Analytics reads/writes are denied.
- [ ] Prove duplicate measurement ingest is idempotent.
- [ ] Prove daily aggregation and canonical report output against target data.
- [ ] Refresh the dedicated demo tenant after schema compatibility is confirmed.

### Windows / physical CCTV

- [ ] Build the real AI-enabled release on Windows with production public
  Supabase values.
- [ ] Authenticode-sign with the production code-signing certificate and verify
  both Site Agent + installer signatures.
- [ ] Verify the published `.sha256` against the exact distributed setup file.
- [ ] Clean-install on a supported Windows PC.
- [ ] Complete recorder discovery/login/enrollment and confirm the installer
  returns instead of hanging.
- [ ] Reboot and prove the WatchLog Agent task autostarts and remains `Running`.
- [ ] Validate Analytics against the contracted real recorder/camera sites,
  including direction, zones, dwell, after-hours and representative load.
- [ ] Complete false-positive/false-negative tuning on real footage for the
  contracted object classes: person, car and motorcycle.

## Current external blockers

The connected Supabase account in the current ChatGPT session does not have
permission to project `oyvgubyxmjlijiczjona`, and no Coolify deployment
connector/API is available in the session. Production signing also requires the
real code-signing certificate, and final field acceptance requires the physical
recorders/cameras. None of those external gates is represented as complete by
source work alone.
