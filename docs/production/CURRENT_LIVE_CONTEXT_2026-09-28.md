# WatchLog — Current Live Context (2026-09-28)

This document is the current operational handoff for the live WatchLog system.
It captures verified production state, current customer/site context, the active
Windows installer baseline, and the remaining field/deployment gates.

**Rules for using this file**

- Treat production database/runtime evidence as current only as of the timestamps below.
- Do not infer that a site is currently online from historical enrollment alone.
- Do not expose recorder passwords, customer credentials, service-role keys, or signing secrets in Git.
- Do not claim a recorder capability is enabled merely because the model datasheet supports it.
- Use the Windows installer source-of-truth document for release authority.

---

## 1. Repository and release authority

### Product / portal / database / bridge

Repository:

`Alkalid-security/Watchlog`

Primary branch:

`main`

This repository owns:

- portal/customer UI;
- production Supabase migrations/RPCs;
- WatchLog AI Edge Function and harness;
- reporting/notification product logic;
- push bridge;
- mirrored Agent/product context.

### Windows installer / Site Connector

**Authoritative product/source repository:**

`Alkalid-security/Watchlog`

**Authoritative branch:**

`main`

Current authoritative product main at this context update:

`7034e2a1deb0c1909fe68ddbd1f7338a3e82bae7`

Existing-site Repair/Upgrade implementation:

PR `#78` / merge commit `e74526affc4722daf5e14aab07e56b08f72c3d44`

Current source version:

**5.0.24**

This main branch now combines:

- Build-69-class discovery/connectivity reach;
- bounded multi-NIC discovery and installer watchdogs;
- Site Control runtime truth;
- secure signed remote-update infrastructure;
- Hikvision recorder readback improvements;
- incident still/clip workers;
- archive/recovery/runtime health logic;
- shutdown-before-replace transactional installer upgrades that stop the existing WatchLog launcher/UI/Agent before touching files;
- Dahua/Hikvision archive footage recovery with bundled FFmpeg historical JPEG extraction;
- recorder-liveness gap detection and durable spool-overflow reconciliation;
- historical recovered snapshots on a 300-second default cadence with original footage timestamps;
- a separate staged `WatchLog-Repair-Upgrade.exe` path for existing enrolled sites;
- passive SYSTEM candidate validation before the old Agent is stopped;
- recorder compatibility validation before installed files are replaced;
- protected runtime-health proof requiring fresh cloud heartbeat, recorder observation and real remote-update polling before commit.

### Field-proven baseline — Build 69

Build **69 / 5.0.17** remains the currently field-proven discovery/connectivity baseline.

Exact identity:

- source SHA:
  `811d378e3a7556047f294bb128b8caf45a295469`
- Windows Release run id:
  `36238903083`
- artifact:
  `WatchLog-Windows-69`
- artifact id:
  `10905576478`
- artifact ZIP digest:
  `sha256:6f84aa14b10eb245f66b9a344524fa39490817bb1882c89daa1954da266809d1`
- `WatchLog-Setup.exe` SHA-256:
  `A5428B33A9789056D8156F445FE099F73CC926E5903C4162F96746D7C90E1B5E`
- `watchlog-agent.exe` SHA-256:
  `24EEAC5826CF104DC41770A66A69F53F83960443B73D9D3CEEC997B39BFDD5F4`
- `watchlog-setup-ui.exe` SHA-256:
  `C5B732F1D28F0D2FB54EBC4ACD00C4BBFF3EC654960F3BA3C82AA8E48007F8E6`

Build 69 is the regression baseline for discovery/login/connectivity until a later exact artifact passes physical Hikvision and Dahua field acceptance.

### Build 74 field failure

Build 74 passed packaging but failed real field discovery: setup could remain on
`Search Network` without surfacing the recorder.

The root reliability class was an unbounded/over-broad Windows network scan across
multiple adapters. Later builds must therefore not be promoted merely because packaging is green.

### Release-line validation candidate — Build 83

A separate Windows release-line repository was used to validate the discovery/setup hardening:

- repository:
  `visioninfinity0007-droid/Watchlog-2`
- branch:
  `build/site-connector-v5-watchlog2`
- Build:
  **83**
- product version:
  **5.0.21**
- source SHA:
  `dfc3ec5bc1229a88c510f8057cd9ac898f8cf848`
- run id:
  `36351875478`
- artifact:
  `WatchLog-Windows-83`
- artifact id:
  `10942702630`
- artifact ZIP digest:
  `sha256:0abf05c447abfa74277f4355f86da2a66f7d75788d29bba815b9413898eb871d`
- `WatchLog-Setup.exe` SHA-256:
  `EEBA56F5879306CDA0662E6CCA5DBDA2D85AA66D0463D09B21D4F90E54B6F8EC`
- `watchlog-agent.exe` SHA-256:
  `6593168ED008D36467772948C909888CE58380BBC4CE5D3B7657C26563024576`
- `watchlog-setup-ui.exe` SHA-256:
  `F345DC7C247741C6643BD7021EB285FCC9796881AF213DECF204431B2DF5853D`

Build 83 passed the Windows release workflow, packaged setup-UI discovery self-test,
recorder discovery/login gates, eighth-subnet regression and installer checksum/version checks.

**Build 83 is a controlled validation candidate, not the final fleet installer.**
It does not contain the authoritative 5.0.24 existing-site Repair/Upgrade/bootstrap source.

### Release-line upgrade-lock validation — Build 98

The existing-site “Updating files” lock/hang failure was reproduced and hardened in the
Windows release-line repository.

Validation result:

- Build:
  **98**
- product version:
  **5.0.21**
- source SHA:
  `c653c6a38664491ee51788d0466e7338a1f3da53`
- run id:
  `36371718065`
- artifact:
  `WatchLog-Windows-98`
- artifact id:
  `10949248440`
- artifact ZIP digest:
  `sha256:c6a29f4b1642c1fab1d546749e6928e432b605ca564395499e8e2e0e5c76f30c`
- installer SHA-256:
  `06DFCC486EA15E123BA1E366A68A3DB83C996A6876CFAA0FDCA31BB4AAED2940`

Release #98 passed the real Windows regression that starts WatchLog-owned processes, then proves
the upgrade preflight stops the target Setup UI/Agent/launcher path, leaves an unrelated same-named
process outside the install directory alone, verifies all payload files are unlocked/backed up,
and restores the previous payload on rollback.

The lower-level shutdown/rollback behavior remains part of authoritative 5.0.24.
Build 98 remains validation evidence, not the final 5.0.24 fleet installer.

### Archive/gap recovery validation — Build 100

Windows Release **100 / 5.0.23** validates the packaged historical-footage recovery runtime.

- source SHA:
  `377462fbd36d834d52864838803299a2a97eb7af`
- run id:
  `36373435504`
- artifact:
  `WatchLog-Windows-100`
- artifact id:
  `10950610443`
- artifact ZIP digest:
  `sha256:35e46bccd8549aa844932970d266c26badcc14ebe358cee50b1c375b73e86a9e`
- installer SHA-256:
  `D40C5622E6DB30BE064FD273624281A08F404112ADD274B7BACE851A558CD42B`

The frozen Site Connector self-test explicitly proved the bundled FFmpeg could synthesize
and decode video. The 5.0.23 recovery implementation added the full recovery semantics,
which remain part of current 5.0.24:

- both Hikvision and Dahua archive adapters;
- recovered visual checkpoint every 300 seconds by default across missed footage;
- quiet frames retained as `recovered_snapshot`;
- activity frames retained as `recovered_activity`;
- recovered frames preserve original footage `device_ts`;
- production `wl_ingest_events` now stores snapshot `captured_at` from that historical timestamp;
- recovered snapshots enter the standard visual-review queue;
- last-live uses actual recorder transport truth rather than cloud heartbeat;
- local spool overflow persists a recovery interval before deleting old rows;
- all-frame decode failure is marked partial/unknown rather than falsely recovered.

Production migration `recovered_snapshot_timestamps` is already live.

Hardware boundary: Dahua has prior archive/pilot evidence; the exact Chai Wala Hikvision
DS-7608NI-Q1 still needs one live promoted 5.0.24 archive/clip/gap test before that hardware path can
be called physically proven.

### Current installer promotion boundary

Do **not** replace a working site simply because a newer version exists.

Current product source is **5.0.24** and the required Windows model is now:

- `WatchLog-Setup.exe` — new site / new PC only;
- `WatchLog-Repair-Upgrade.exe` — existing enrolled sites.

The Repair/Upgrade candidate must validate itself as SYSTEM against the existing site
**before** it stops the old Agent, then validate recorder identity/channels before any
installed payload is replaced.

The Windows validation branch is:

`visioninfinity0007-droid/Watchlog-2:fix/existing-site-repair-upgrader-v5`

Branch head at the installer-context update:

`0f3507483ffd7369134fe8b7aa0c6a7be5946ea9`

The branch contains the staged preflight/runtime-health/updater port, but an exact
5.0.24 Windows Repair/Upgrade artifact has **not yet been promoted**.

### Recent Al-Khalid upgrade field event

The recent full-installer test was on **Al-Khalid Head Office**.

The candidate did not remain healthy and rolled back to the previous **5.0.19** Agent.
That field event proved why full Setup must no longer be the normal existing-site
upgrade mechanism.

5.0.19 does not advertise `remote_update_v1` and has not claimed the online-update queue,
so the site still needs one successful 5.0.24 Repair/Upgrade bootstrap before future normal
updates can be remote.

The old rollback dialog also exposed a truth bug: it could say the previous version was
restored without independently proving the old Agent restart. 5.0.24 corrects that behavior.

The next promotable artifact must pass:

1. packaged Repair/Upgrade contract tests;
2. real Hikvision staged preflight + recorder preflight;
3. real Dahua staged preflight + recorder preflight;
4. in-place upgrade without rediscovery/re-login;
5. fresh heartbeat + recorder_seen_at + remote_update_poll_at on the new exact version;
6. `remote_update_v1` only after actual updater polling;
7. Site Control read-only command claim/completion;
8. rollback proof;
9. archive/gap recovery proof on pilot hardware.

See:
- `docs/release/WINDOWS_INSTALLER_SOURCE_OF_TRUTH.md`
- `docs/release/EXISTING_SITE_REPAIR_UPGRADE_5_0_24.md`
- `docs/runbooks/WINDOWS_EXISTING_SITE_REPAIR_UPGRADE.md`


---

## 2. Live customer/site map

Production Supabase project:

`oyvgubyxmjlijiczjona`

### Al-Khalid Security Services — Main site

Purpose:

Office/security company head-office context.

Site type:

`office`

Timezone:

`Asia/Karachi`

Business hours:

08:00–19:00, Monday–Friday.

Current recorder/Agent evidence at last check:

- Host: `SM-HP`
- Agent version: **5.0.16**
- Recorder vendor: Dahua
- Recorder model: `DH-XVR1B08-I`
- Persisted driver: `onvif`
- Last recorded Agent heartbeat in the verification query:
  `2026-09-27 18:52:23.989918+00`

### Al-Khalid physical-camera repair

The historical Dahua/ONVIF connection created 16 camera rows because ONVIF
`GetProfiles` exposed MainStream/SubStream encoding profiles as if they were
separate cameras.

This is now normalized in production:

- total retained camera rows: **16**
- customer-visible canonical physical cameras: **8**
- hidden historical transport/profile rows: **8**
- historical snapshots/events were not deleted
- non-canonical transport rows are not customer-visible analytics cameras
- migration/cloud sync is backward-compatible with older Agents
- Build 76 also fixes the Agent-side source-token de-duplication

Current business-context status:

`physical_mapping_repaired`

Important remaining mapping boundary:

The old profile configuration contained contradictory semantic labels on
physical Cameras 1 and 2:

- Reception vs Director's Office
- Armory Gate vs Admin Entrance

Those four ambiguous legacy rules were disabled rather than guessed.
Until the physical views are visually re-confirmed, Watch AI must not assign
those four labels to a specific channel.

Known office operational areas remain valid business context:

- Reception
- Director's Office
- Armory Gate
- Admin Entrance

Owner insight priorities:

- opening/closing activity;
- reception/visitor flow;
- management-office after-hours presence;
- restricted/armory access;
- admin/entrance activity;
- unusual dwell/access outside business hours;
- camera/recorder/recording health.

### Al-Khalid legacy HASCO Office record

A separate older site named `HASCO Office` remains under the
Al-Khalid tenant.

It is not the canonical HASCO Steel customer site.

At the latest verification it had:

- 8 camera rows;
- business context = office;
- no recent active Agent heartbeat comparable to the current customer sites.

Do not merge it into the real HASCO Steel tenant without an explicit cleanup decision.

---

### HASCO Steel — Head Office

Tenant:

`HASCO Steel`

Site:

`Head Office`

Site type:

`office`

Timezone:

`Asia/Karachi`

Business hours:

08:00–19:00, Monday–Friday.

Current recorder/Agent evidence at last check:

- Host: `DESKTOP-35QBF9S`
- Agent version: **5.0.19**
- Recorder vendor: Hikvision
- Recorder model: `DS-7608NI-Q1`
- Driver: `hikvision-isapi`
- Last recorded Agent heartbeat in the verification query:
  `2026-09-28 07:40:12.758442+00`

Physical cameras: **8**

Canonical camera context:

| Channel | Camera | Business role |
| --- | --- | --- |
| 1 | Entrance Corridor | Primary internal entrance/corridor access |
| 2 | Reception | Visitor arrival/waiting/reception occupancy |
| 3 | Private Office | Management-sensitive/private office |
| 4 | Office Interior | General office activity |
| 5 | Manager Office | Manager/management office |
| 6 | Parking | Vehicle arrival/departure and parking activity |
| 7 | Internal Corridor | Internal movement corridor |
| 8 | External Entrance | External entrance/perimeter access |

Owner insight priorities:

- office opening/closing;
- visitor arrival and reception waiting;
- private/manager-office after-hours activity;
- entrance/corridor access patterns;
- parking/vehicle activity;
- unusual dwell/access;
- camera/recorder/recording health.

Watch AI must not infer employee identity, visitor identity, employment status,
or wrongdoing from appearance alone.

---

### Chai Wala — Chota Bukhari

Tenant:

`chaiwala`

Site:

`Chai Wala - Chota Bukhari`

Site type:

`restaurant`

Location context:

Chota Bukhari, DHA Phase 6, Karachi.

Timezone:

`Asia/Karachi`

Machine operating envelope:

16:00–04:00, seven days, `overnight=true`.

Public-hours note:

Public listings vary slightly. Exact branch hours should still be confirmed with the owner.

Business model/context:

Late-night chai café / casual restaurant with dine-in/outdoor seating,
takeaway/delivery and car-side service.

Menu focus stored in business context:

- chai / kahwa;
- stuffed/specialty parathas;
- bun kebab / burgers;
- sandwiches;
- fries / chaat;
- cold drinks / lassi.

Current recorder/Agent evidence at last check:

- Host: `DESKTOP-BMEUEAK`
- Agent version: **5.0.17** (Build-69 lineage)
- Recorder: Hikvision `DS-7608NI-Q1`
- Driver: `hikvision-isapi`
- Last recorded Agent heartbeat in the verification query:
  `2026-09-27 22:22:46.118327+00`

Physical cameras: **8**

Canonical restaurant camera context:

| Channel | Camera | Business role |
| --- | --- | --- |
| 1 | Floor 1 | Customer seating / dine-in area |
| 2 | Shop Front | Waiter/service handoff and pickup counter; not the main entrance |
| 3 | Office View | Office/management view |
| 4 | Back Entrance | Staff/service access |
| 5 | Cash Counter | Till, customer counter and queue area |
| 6 | Floor 2 | Customer seating / dine-in area |
| 7 | Kitchen | Restricted back-of-house / safety-critical kitchen view |
| 8 | Office Camera | Office/management view |

Restaurant owner insight priorities:

- activity/occupancy on Floor 1 vs Floor 2;
- busy vs quiet periods;
- dwell and unusual crowding;
- service-handoff pressure at Shop Front;
- Cash Counter queue/waiting/till-area activity;
- kitchen activity continuity and visible safety concerns;
- Back Entrance service access and unusual late-night movement;
- office occupancy/unusual access;
- after-hours presence;
- camera/recording/recorder health.

Kitchen wording boundary:

Visible smoke/flame/fall indicators may be reported as a concern requiring review.
Do not claim a confirmed fire, injury, diagnosis, or food-safety breach from a single CCTV frame.

General restaurant inference boundary:

Do not infer sales, revenue, order accuracy, food quality, unique customer counts,
staff identity, or confirmed theft from camera evidence alone.

### Chai Wala Site Control state

At the latest production query, `site_control_enabled=true` for Chai Wala.
This should be treated as an explicit operational state and reviewed before any
recorder-write workflow. WatchLog still requires the normal Site Control
authorization/approval model for writes.

---

## 3. AI chat isolation

Customer AI conversation storage is scoped by:

- `tenant_id`
- signed-in `user_id`

Direct customer table grants remain restricted; access is mediated through the
authorized Watch AI functions/runtime.

Production integrity check as of 2026-09-28:

- message tenant/user scope mismatches: **0**
- attachment tenant/user scope mismatches: **0**
- current stored conversations observed by the audit query: **172**

A user cannot retrieve another tenant's conversation by supplying a foreign
conversation ID. The current design is stricter than tenant-wide sharing:
different users in the same tenant have separate conversation histories.

Do not weaken this to tenant-only chat visibility without an explicit shared-inbox product decision.

---

## 4. Site removal / Agent disconnect

Production contains:

- `wl_remove_site(uuid,text)`
- exact site-name confirmation
- Owner/Admin authorization
- site-scoped AI conversation cleanup before site deletion
- cascade deletion across site-owned data

Deleting the Site also deletes its enrolled Agent rows and enrollment identity,
so the existing Site Agent can no longer authenticate using that removed site identity.

A rollback-transaction acceptance test verified:

- site removed;
- Agent row removed;
- site-scoped AI conversation removed.

The production foreign-key audit verified all site-owned production tables use
`ON DELETE CASCADE`, except AI conversations which are explicitly deleted by
`wl_remove_site`.

Customer UI includes a destructive-action confirmation:

`Remove site and disconnect Agent`

Do not silently remove a site without exact-name confirmation.

---

## 5. Notification Center

The customer Notification Center is implemented as a read layer over existing
authoritative data rather than a second alert engine.

Sources:

- incidents;
- open operational/site-health faults;
- generated reports (including the top report-backed recommended action when one exists).

Production objects verified:

- `notification_reads`
- `wl_notifications(uuid,integer)`
- `wl_notification_mark_read(text,text)`
- `wl_notifications_mark_all_read(uuid)`

Read state is per signed-in user.

The portal has a Notifications route/navigation and links users back to the
relevant incident, report or Site Health screen.

The existing delivery infrastructure remains authoritative for external delivery:

- `alert_dispatches`
- `delivery_outbox`
- `report_deliveries`
- `report_recipients`
- `report_snapshots`

---

## 6. Watch AI customer behavior

The live Watch AI Edge Function is site-type aware.

Verified live prompt/runtime properties:

- identity is **security and business-operations intelligence assistant**
- old universal **office intelligence assistant** identity is absent
- `SITE OPERATING CONTEXT` is injected
- owner insight priorities and site guidance are injected
- `UNKNOWN means unconfirmed` invariant is present
- recorder credentials remain on the on-site WatchLog service
- recorder writes are not silently executed
- when verified evidence supports a direct answer, the assistant should answer clearly rather than hedge for style

Customer-facing language must not expose implementation terms such as:

- Supabase;
- RPC;
- provider routing;
- model names;
- queues;
- schema;
- guided fallback;
- service-role;
- internal capability plumbing.

Use human language such as:

- “Footage is being retrieved.”
- “WatchLog could not verify this period.”
- “This camera needs attention.”

---

## 7. Visual Worker

Source implementation:

`prototype/vision_worker`

Deployed Edge Function/orchestrator source:

`prototype/supabase/functions/watchlog-vision-worker/index.ts`

The checked-in Edge Function source is synchronized byte-for-byte with the deployed `watchlog-vision-worker` version **6** as of 2026-09-28. It reads deployment credentials only from environment variables; no service key is stored in Git.

Context-aware worker code exists and uses:

- per-site business context;
- per-camera role/watch-for guidance;
- private MinIO media mirror;
- local Ollama vision processing;
- service-role-only snapshot review RPCs.

Current intended snapshot analysis version:

`snapshot-vision-v2-context`

Current intended day-summary version:

`visual-day-v2-context`

Important production status as of the live verification around 08:38–08:39 UTC on 2026-09-28:

- `edge-vision-worker-v1` is alive and reporting `idle`;
- latest worker heartbeat observed: `2026-09-28 08:38:00+00`;
- latest successful visual review observed: `2026-09-28 08:21:01+00`;
- worker model: `qwen/qwen3.8-27b`;
- worker media backend: `snapshot_table`;
- successful snapshot reviews observed: **60**;
- the queue still contains more than **3,200 pending** reviews;
- more than **2,100** rows marked failed are intentional legacy non-canonical ONVIF-profile skips, not real physical-camera review failures;
- roughly **300+** current genuine failures are external vision-provider rate-limit (HTTP 429) failures;
- no `visual_day_summaries` rows existed at the verification point, so daily visual summarization has not yet caught up;
- saved `report_snapshots` were still limited to older Al-Khalid Main site reports at the verification point.

Privacy/runtime eligibility is intentional:

- Al-Khalid Main site currently allows external model egress and is the only real active site receiving the cloud vision-worker processing path;
- Chai Wala and HASCO Steel have external image egress disabled and therefore remain pending for this cloud worker;
- do **not** enable external CCTV-image egress merely to drain those queues;
- Chai Wala and HASCO require the private/local vision-worker path (Coolify/local Ollama/WatchLog-controlled infrastructure) if image analysis is to continue without changing tenant privacy policy.

The remaining visual-intelligence gap is therefore **runtime processing capacity/private-worker availability**, not tenant-reporting structure, business-day semantics, or harness configuration.

Images should remain on WatchLog-controlled infrastructure wherever the tenant has not explicitly enabled external processing.

---

## 8. Incident footage / multi-camera evidence

### Cloud request path

The incident footage worker/transport exists and the live Chai Wala Build-69
Agent successfully **claimed** a real bounded clip request.

Test request result on the live `DS-7608NI-Q1`:

- camera: Floor 1
- requested bounded window: 15 seconds
- Agent version: 5.0.17
- driver: `hikvision-isapi`
- request reached `started`
- final state: `unsupported`
- no video bytes returned
- returned message:
  “This recorder does not expose on-demand incident footage through the validated WatchLog path.”

This proved the cloud queue/claim worker path, but **did not prove video export on Build 69**.

### Build 76 change

Build 76 includes the next Hikvision compatibility implementation:

- ContentMgmt search-first behavior;
- recorder-returned playback URI;
- required legacy Hikvision search fields;
- bounded GET/POST `/ISAPI/ContentMgmt/download` compatibility.

This still requires field acceptance on the actual recorder.

Do not claim Chai Wala video extraction is field-proven until an accepted 5.0.24 (or later) artifact returns real bounded footage from that physical NVR.

### Multi-camera incident design

Preferred architecture:

1. incident determines primary camera/time;
2. camera topology/journey context selects nearby/relevant cameras;
3. bounded synchronized windows are requested per camera;
4. clips are presented as one incident evidence bundle/timeline;
5. optional composite export can be generated later.

Do not blindly download every camera for every incident.

---

## 9. Face recognition / identity requirements

Customer demand exists for identifying office visitors and distinguishing known
staff from unrecognized visitors.

This is **not currently a production WatchLog capability** and must not be claimed as one.

Recommended separation:

### Anonymous multi-camera tracking

Use temporary incident/session identities such as `Person A` to correlate a
visitor across cameras using appearance, timing and topology without assigning
a real-world identity.

### Optional enrolled-known-person recognition

If built, require:

- explicit tenant/admin enrollment;
- clear legal/privacy basis;
- tenant-isolated encrypted biometric templates;
- no cross-tenant face database;
- strong match threshold;
- human review for consequential actions;
- retention/deletion controls;
- access/audit logging;
- no attempt to identify an unknown person from the public Internet or external databases.

Preferred output:

“An unrecognized visitor was observed entering reception.”

Avoid unsupported output:

“This person is X” unless the person is an explicitly enrolled known identity
and the match meets the product's reviewed threshold.

---

## 10. Site-type intelligence direction

WatchLog already has primitives for:

- line crossing;
- zone entry/exit;
- dwell;
- occupancy;
- queue wait;
- presence/absence;
- schedules/after-hours;
- journeys;
- opening/closing state;
- evidence recovery;
- daily intelligence.

Product direction is to compose these primitives into site-type skills rather
than expose generic analytics configuration to normal customers.

### Office skill pack

Priority skills:

- opening/closing verification;
- reception/visitor flow;
- reception waiting/dwell;
- office occupancy;
- restricted-area access;
- access journeys;
- after-hours presence;
- parking/vehicle activity;
- office daily brief.

### Restaurant skill pack

Priority skills:

- floor utilization;
- service-handoff pressure;
- counter queue/waiting;
- kitchen activity continuity;
- visible kitchen safety concerns;
- back-entrance/service access;
- late-night/after-hours exceptions;
- restaurant manager daily brief.

Potential open-source building blocks previously identified for evaluation:

- go2rtc — local stream broker/restream layer;
- roboflow/supervision — zones/line geometry/tracking helpers;
- RF-DETR — detector benchmark/fine-tuning candidate;
- OpenVINO — Intel CPU/iGPU/NPU inference optimization;
- MediaPipe — on-device pose/fall-like posture signal;
- Anomalib — later-stage per-camera visual anomaly modelling;
- Frigate — architecture/reference implementation, not a replacement for WatchLog.

Do not integrate these wholesale without license/performance/field evaluation.

---

## 10A. Governed tenant intelligence + reporting state

Canonical PR #71 established the reusable tenant-intelligence/reporting model.

Implemented harness layers:

- `ai-harness/site-types/restaurant.yaml`
- `ai-harness/site-types/office.yaml`
- `ai-harness/skills/tenant-intelligence-setup.md`
- stable tenant overlays for Chai Wala, Al-Khalid Main site and HASCO Steel Head Office
- per-tenant `reporting/methods/visual-snapshot-analysis.md`
- per-tenant `reporting/daily-reports/` archive contract
- per-tenant summary + detailed-report templates

Daily archive rule:

`ai-harness/tenants/<tenant-site>/reporting/daily-reports/YYYY-MM-DD/summary.md`

and

`ai-harness/tenants/<tenant-site>/reporting/daily-reports/YYYY-MM-DD/detailed-report.md`

The folder date is the tenant's configured business/service date, not automatically midnight-to-midnight.

Current date semantics:

- Chai Wala: 16:00–04:00, seven days; the service day is keyed by the date on which the 16:00 opening occurs.
- Al-Khalid Main site: 08:00–19:00 Monday–Friday; Yesterday = latest completed configured working day.
- HASCO Steel Head Office: 08:00–19:00 Monday–Friday; Yesterday = latest completed configured working day.

Production migrations now include:

- `office_reporting_context`
- `business_day_evidence_window`

Live `watchlog-ai` is version **21**. Structured reporting, visual-day lookup and model-side evidence retrieval now resolve “yesterday” through the same business/service-day boundary. Do not mix calendar-yesterday evidence into a last-working-day or overnight-service-day report.

Customer language is governed by `ai-harness/core/customer-language.md`: natural Pakistan English, management-first wording, no internal implementation jargon, explicit uncertainty where coverage is missing, and evidence-backed recommendations in both the daily summary and detailed report.

Post-PR #71 Watchlog-2 commit `d3b2d0a10a8f4be490b98e9e28dcb208e6f6426b` was also reconciled. Its Build-76 field handoff is preserved only as superseded mirror context; canonical 5.0.24 release/source documents remain authoritative.

---

## 11. Current high-priority gates

1. **Visual Worker deployment**
   - restore a real worker heartbeat;
   - process a controlled current-site batch;
   - verify context-aware outputs before draining the backlog.

2. **Authoritative 5.0.24 Repair/Upgrade artifact + field acceptance**
   - finish Windows Release from the reviewed 5.0.24 validation lineage;
   - verify Repair/Upgrade SHA-256/artifact identity in Git;
   - bootstrap one existing Hikvision site without forced rediscovery/re-login;
   - bootstrap one existing Dahua site the same way;
   - prove passive staged preflight before old-Agent shutdown;
   - prove recorder staged preflight before payload replacement;
   - prove Site Control command claim/completion;
   - prove signed remote-update polling + rollback path;
   - prove `remote_update_v1` appears only after actual updater polling;
   - prove camera inventory remains physical/canonical;
   - prove bounded historical clip/archive retrieval on the actual recorder where supported;
   - prove gap/recovery behavior.

3. **Al-Khalid physical-view naming**
   - visually confirm which physical channels correspond to Reception,
     Director's Office, Armory Gate and Admin Entrance;
   - only then re-enable/create role-specific monitoring rules.

4. **HASCO/Chai Wala heartbeat review**
   - current database timestamps are historical snapshots, not a guarantee of present uptime;
   - always re-query before saying a site is active now.

5. **Notification Center live browser QA**
   - verify responsive UI;
   - unread/read flow;
   - incident/report/health deep links.

6. **Site-removal live customer QA**
   - verify confirmation copy;
   - Agent disconnect behavior;
   - empty/next-site selection;
   - no orphan customer UI state.

7. **Privacy-controlled identity roadmap**
   - implement anonymous tracking before biometric identity;
   - do not market face recognition before the privacy/security/accuracy gates exist.

---

## 12. Do-not-regress list

- Never expose ONVIF encoding profiles as separate physical cameras.
- Never delete historical evidence merely to repair camera identity.
- Never say an Agent row is active without checking a recent heartbeat.
- Never say Chai Wala clip export is proven based on the Build-69 unsupported test.
- Never treat a green packaging workflow alone as field discovery proof; Build 74 is the counterexample.
- Never reduce automatic discovery below Build 69's eight-/24 field baseline without explicit field evidence.
- Never replace a working Build-69 site with a candidate build that has not passed physical field acceptance.
- Never call Build 83, Build 98 or Build 100 the authoritative 5.0.24 installer; they are behavior-specific validation artifacts.
- Never use full Setup as the default upgrade path for a complete enrolled site.
- Never stop the installed Agent before passive staged Repair/Upgrade validation succeeds.
- Never replace WatchLog payload files before staged recorder validation succeeds.
- Never overwrite WatchLog payload files while that install's launcher, Setup UI or Agent is still running.
- Never let the scheduled-task watchdog restart WatchLog during an installer file-replacement transaction.
- Never broad-kill same-named processes outside the current WatchLog install path.
- Never commit an upgrade based only on process liveness; require fresh heartbeat + recorder + updater-poll proof.
- Never advertise `remote_update_v1` without a real successful updater claim poll.
- Never claim rollback success unless the previous Agent restart is proven.
- Never describe the Visual Worker as active without a fresh heartbeat + completed reviews.
- Never share tenant AI chats across tenants.
- Never silently broaden chats from per-user to tenant-wide.
- Never expose recorder passwords or service keys in Git/chat/UI.
- Never infer exact sales/revenue/orders from CCTV.
- Never infer employee/visitor identity from appearance alone.
- Never claim certified fire/safety detection from visual heuristics.
- Never call periodic still refresh a live video wall.
- Never make a recorder write without the Site Control approval/authorization path.
