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

Authoritative repository:

`visioninfinity0007-droid/Watchlog-2`

Authoritative branch:

`build/site-connector-v5-watchlog2`

Current successful Windows release:

- Build: **76**
- Version: **5.0.21**
- Source SHA: `26b036a654446b3e8c262b2f476118f2e2f41916`
- Windows Release run: **#76**
- Run ID: `36346424676`
- Result: **success**
- Artifact: `WatchLog-Windows-76`
- Artifact ID: `10941445308`
- Artifact digest:
  `sha256:0fdf73bc6421291e58413330ac73b439a6256f59c95227cc4dfd1e4c6eea54b5`

Build 76 supersedes Build 72 for new installations.

Build 76 carries forward the Build-72 discovery/login fixes and adds:

1. ONVIF physical-camera normalization so MainStream/SubStream profiles are not exposed as separate physical cameras.
2. Backward-compatible cloud camera identity for older deployed ONVIF Agents, preserving historical evidence.
3. Hikvision bounded historical-footage retrieval hardening:
   search first, use recorder-returned playback URI, and support firmware-dependent GET/POST download behavior.

See `docs/release/WINDOWS_INSTALLER_SOURCE_OF_TRUTH.md` for the release ledger.

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
  `2026-09-26 13:38:57.716037+00`

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
  `2026-09-27 20:16:47.448647+00`

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
- generated reports.

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

Important production status as of 2026-09-28:

- no active `vision_worker_status` heartbeat was returned by the live verification
- `snapshot_visual_reviews`:
  - pending: **3,425**
  - failed: **2,119**
- no worker should be described as live/healthy until a fresh worker heartbeat
  and new completed reviews are observed

The deployment blocker is operational/Coolify runtime, not the site-business-context schema.

Images should remain on WatchLog-controlled infrastructure.
Do not turn on external CCTV image egress merely to bypass the local worker.

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

Do not claim Chai Wala video extraction is field-proven until Build 76 (or a later
authoritative build) returns a real bounded clip from that physical NVR.

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

## 11. Current high-priority gates

1. **Visual Worker deployment**
   - restore a real worker heartbeat;
   - process a controlled current-site batch;
   - verify context-aware outputs before draining the backlog.

2. **Build 76 field deployment**
   - upgrade one Hikvision and one Dahua pilot;
   - prove camera inventory remains physical/canonical;
   - prove bounded historical clip retrieval on the actual recorder;
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
- Never describe the Visual Worker as active without a fresh heartbeat + completed reviews.
- Never share tenant AI chats across tenants.
- Never silently broaden chats from per-user to tenant-wide.
- Never expose recorder passwords or service keys in Git/chat/UI.
- Never infer exact sales/revenue/orders from CCTV.
- Never infer employee/visitor identity from appearance alone.
- Never claim certified fire/safety detection from visual heuristics.
- Never call periodic still refresh a live video wall.
- Never make a recorder write without the Site Control approval/authorization path.
