# Analytics Studio v1 test plan

The plan is split into deterministic repository gates and target-environment
acceptance. Source/CI tests must be green before migration or deployment; target
DB and physical CCTV tests are required before the product is called field
accepted.

## A. Deterministic repository gates

### Local analytics engine

- polygon point-in-zone.
- finite-segment line intersection (crossing an infinite extension does not
  count).
- directional line crossing and no frame-by-frame double count.
- zone entry and object-class filtering.
- dwell threshold and once-per-visit behavior.
- weekly schedule inclusion/exclusion in the site timezone.
- overnight schedule carry into the following day.
- after-hours activity emits once while a track remains active.
- occupancy establishes the first baseline immediately.
- a one-frame occupancy detector miss does not emit a false count change.
- a changed occupancy count emits after two consecutive samples.
- a different candidate count resets the occupancy confirmation streak.

### Sampling and local capacity

- one-camera requested interval remains unchanged below capacity.
- aggregate inference rate never exceeds `analytics_max_fps`.
- active cameras are selected fairly in round-robin order.
- many-camera plans receive an explicit effective sampling interval rather than
  spawning parallel inference load.
- status distinguishes `full`, `reduced` and `capacity_limited` sampling.

### Analytics reporting

- zero measurements leave the security-only base report unchanged.
- measurements add the Site Intelligence chapter.
- Visitor Flow / Vehicle Flow / Checkout Activity wording is business-facing.
- Checkout Activity never says transactions.
- HTML inserts Site Intelligence before the report CTA.
- the canonical `daily_report.py` entrypoint includes Analytics, so manual CLI,
  scheduled service and n8n execution cannot drift into separate report shapes.

### Portal/source contracts

- semantic `analytic_key` is sent explicitly by Studio.
- Site Health is not a configurable AI/video rule.
- Owner/Admin configure and Viewer is read-only.
- purpose + site-type recommendations are consumed.
- Analytics Overview / Studio / Schedules subnavigation is consistent.
- configuration-still refresh waits for a changed `captured_at`.
- new line/zone geometry cannot be published without a current camera still.
- refreshed still clears in-progress geometry.
- polygons render closed.
- report `both` UX writes two provider-specific endpoints.
- billing and operational writes fail closed by role/provider configuration.

### Installer/release contract

- production build entrypoint is `release_agent.py`.
- explicit `--setup` cancellation/failure is non-zero.
- explicit successful `--setup` reaches recorder/enrollment/camera validation but
  does not enter the infinite runtime.
- NSIS stops an existing task before upgrade and attempts to resume it on a
  failed validation.
- Windows Add/Remove Programs registration occurs only after setup and task
  startup succeed.
- task registration uses `Register-ScheduledTask -Force`, then proves `Running`.
- NSIS manifest compiles on a Windows CI runner.
- when signing is requested, both `watchlog-agent.exe` and
  `WatchLog-Setup.exe` are Authenticode-verified.
- final SHA256 is generated after installer signing.

### Regression suite

- incident ingest and local false-alarm filter.
- durable security-event spool.
- report entitlement and delivery idempotency.
- billing authorization.
- team/trial roles.
- tenant isolation/static authorization contracts.
- recorder push parser and capability probes.
- email template.
- ONNX packaged-inference contract.
- pricing alignment and public-site copy checks.
- production Next.js static export.

## B. Target Supabase integration acceptance

Run only against a disposable/staging target first, then production-safe smoke
checks after migration. Never mutate production with the destructive isolation
fixture.

- verify migration ledger/checksums before applying anything.
- apply ordered migrations through `0035_billing_read_authz.sql`.
- confirm PostgREST schema cache exposes every new Analytics/portal RPC.
- Owner can configure own tenant/site.
- Admin can configure own tenant/site.
- Viewer can read Analytics but every configuration RPC is denied.
- a member cannot read/write another tenant's rules, schedules, snapshots or
  measurements.
- a valid agent receives only its own site's versioned config.
- a valid agent cannot submit a rule/camera belonging to another site.
- duplicate `dedupe_key` ingest is idempotent.
- server-side semantic `analytic_key` survives ingest and aggregation.
- incident promotion happens only for a server-published rule with
  `promote_incident=true`.
- daily Analytics aggregation matches known fixture counts.
- canonical report includes the same Analytics values.
- report-recipient `both` produces one WhatsApp + one email endpoint.
- Viewer never receives open enrollment-code values.
- Admin/Viewer cannot read detailed billing tables or `wl_billing_overview()`.

## C. Portal acceptance against migrated target

For Owner, Admin and Viewer accounts:

- authentication/onboarding redirect behavior.
- Overview hierarchy and stable still loading.
- Incidents filters and large-still review.
- Site Health site/agent/camera/fault detail.
- site type selection.
- deterministic first-camera selection when changing site.
- camera purpose selection.
- purpose-aware recommendations.
- configuration still request and new-still refresh.
- line drawing against the still.
- 3–8 point polygon drawing and closure.
- schedule selection and reusable schedule editing.
- Analytics filters/summary/empty/loading/error states.
- recipient management and delivery history.
- Team role/invitation behavior.
- Settings site enrollment and billing fail-closed behavior.

## D. Windows clean-install / upgrade acceptance

Use a supported Windows PC on the same LAN as a test recorder.

### Clean install

1. Build AI-enabled release with production/staging public Supabase values.
2. If this is a production candidate, sign with the production certificate.
3. Verify Authenticode on both inner Site Agent and outer setup.
4. Verify the published SHA256 against the final installer bytes.
5. Run `WatchLog-Setup.exe` as a normal customer would.
6. Prove recorder discovery, local login, camera discovery and WatchLog
   enrollment.
7. Confirm the setup process returns to NSIS instead of continuing forever.
8. Confirm Add/Remove Programs appears only after the task has started.
9. Confirm `WatchLog Agent` task is `Running` as SYSTEM.
10. Reboot, confirm automatic startup, heartbeat and camera sync.
11. Confirm local recorder credentials never appear in portal/network payloads.

### Failure paths

- cancel recorder setup: installer fails, no new ready registration is claimed.
- invalid recorder credentials: installer fails safely.
- invalid/expired enrollment code: installer fails non-zero.
- Task Scheduler registration failure: installer does not claim completion.
- upgrade an existing installation: old task is stopped before binary
  replacement and can be resumed if validation fails.

## E. Real CCTV / performance acceptance

This is the contractual field gate and cannot be replaced by synthetic tests.

- validate the actual target recorder drivers and exact camera channels.
- prove person/car/motorcycle detector behavior against representative day/night
  footage.
- tune confidence/false-positive/false-negative behavior from field samples.
- validate Visitor Flow direction at a real entrance/gate.
- validate Vehicle Flow at a real gate/parking/loading path.
- validate polygon Zone Activity.
- validate Dwell duration.
- validate After-Hours schedule in local time.
- validate Checkout Activity as people-in-zone occupancy only.
- exercise multiple configured cameras until the sampler reports the expected
  effective interval; confirm CPU/memory/network remain acceptable and the
  security incident collector remains responsive.
- disconnect internet and prove cached config + measurement spool continue
  locally; reconnect and prove idempotent upload.
- restart/reboot and prove no duplicate counts caused by retry/upload behavior.

Record recorder model/firmware, camera count, test duration, sampling status,
observed false positives/negatives and any per-site tuning before signing off the
field gate.
