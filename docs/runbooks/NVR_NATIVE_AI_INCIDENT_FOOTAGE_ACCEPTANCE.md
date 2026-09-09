# WatchLog Native NVR AI + Incident Footage Acceptance

Use this only after the normal Windows/recorder Handoff D connection is healthy.
This gate proves two optional recorder integrations without changing WatchLog's
core boundary: recorded CCTV stays on the recorder unless a customer explicitly
requests a short incident evidence window.

## 1. Record the hardware truth

Capture from the physical recorder and WatchLog probe/logs:

- vendor and exact model
- firmware version
- recorder HTTP/HTTPS port actually used
- WatchLog driver selected
- camera count/channel mapping
- recorder clock and Windows PC clock
- available recorder analytics reported by `wl_capabilities`

Do not mark an entire vendor family validated from one model/firmware.

## 2. Recorder-native analytics

WatchLog reads existing recorder analytics. It must not silently enable or alter
lines, zones, schedules, SMD/AcuSense/IVS settings on customer hardware.

For Dahua, inspect at least:

- MotionDetect
- SmartMotionDetect / human-vehicle SMD, if the model exposes it
- IVS line crossing, if configured
- IVS intrusion/region, if configured

For Hikvision, inspect at least motion, line detection and field/intrusion where
the firmware exposes the documented ISAPI endpoints.

### Native-event proof

Trigger one recorder smart event that is already configured, for example a
human/vehicle SMD event or a line crossing.

PASS requires:

1. the recorder itself logs/indicates the smart event;
2. WatchLog receives one corresponding incident;
3. incident metadata reports `event_source = recorder_native_ai` and
   `native_ai = true`;
4. the event is retained even if a later requested still no longer contains the
   object that caused the recorder event;
5. no facial-recognition identity is created or inferred;
6. the event is deduplicated rather than repeated every second while active.

Then trigger ordinary generic motion. It should remain eligible for WatchLog's
local false-alarm filter. This proves native smart analytics are preferred where
available without disabling WatchLog's noise reduction for basic DVR motion.

## 3. Incident still

For the same incident, prove the normal incident still path independently:

- still present when the recorder can provide one;
- correct camera/channel;
- timestamp displayed;
- failure to capture a still does not delete the event.

## 4. On-demand footage request

Prerequisites: migrations 0040 and 0041 deployed together, packaged agent with
`incident_evidence.py`, and an Owner/Admin test account.

From **Incidents**, select the test incident and click **Retrieve footage**.
Default window is 10 seconds before through 20 seconds after the incident.

Expected state sequence:

`Queued -> Retrieving -> Ready`

PASS requires:

- only an explicit user action creates the request;
- site agent claims only requests for its own tenant/site;
- the recorder password/URL never enters the cloud request schema or logs;
- footage is at most 60 seconds by requested window;
- WatchLog aborts uploads over 32 MiB;
- customer access expires at 24 hours;
- reconstructed download byte count exactly matches the server manifest;
- download SHA-256 exactly matches the server manifest;
- if the browser cannot calculate SHA-256, download fails closed rather than
  silently bypassing integrity verification;
- original CCTV recording remains on the recorder after cloud evidence expires.

### Dahua pilot path

The first implementation requests recorder-native footage through Dahua's local
HTTP `loadfile.cgi` export endpoint. The returned file may be native `.dav`.
Browser-native MP4 playback is **not** a pass criterion unless this exact
recorder/firmware is proven to return or can safely produce MP4.

Open the downloaded DAV using an approved Dahua-compatible player and verify:

- expected camera;
- incident is actually visible;
- requested time window is represented;
- file is not corrupt;
- audio, if any, is not required for pilot acceptance.

Some Dahua firmware may return a larger recording segment than the requested
window. If it returns an hour/file far beyond the requested incident window,
record **GO WITH LIMITATIONS** for footage and do not present WatchLog as precise
clip extraction on that model until local trim/transcode is added and tested.

### Unsupported path

If the recorder has no validated playback/export API, the portal must show
**Unsupported** (or a specific safe failure). It must never fabricate a clip,
expose an RTSP credential URL, or imply that continuous cloud video exists.

## 5. Authorization tests

Using demo/test tenants only, prove:

- Owner/Admin can request footage;
- Viewer cannot request;
- Viewer may read/download already-authorized tenant evidence only if product
  policy allows read-only incident access;
- anonymous cannot request/read chunks;
- tenant A cannot request/read tenant B evidence;
- agent A cannot claim/upload/fail tenant B/site B request;
- 0041 failure path validates the agent claim before deleting any chunks;
- suspended account fails closed through the existing account lifecycle.

## 6. Network/storage boundary

Observe the site connection during a quiet period and during one clip request.
There must be no continuous CCTV video upload. Traffic increase should occur
only for the requested evidence transfer plus normal events/analytics/stills.

The database stores temporary evidence chunks, not RTSP URLs, recorder
credentials or continuous recordings.

### Independent expiry proof

Migration 0041 schedules `watchlog-prune-incident-clips` through Supabase Cron so
physical deletion is not dependent on the originating site agent staying online.
Before production acceptance, an authorized database operator must prove:

- the named cron job exists and is active;
- its latest run succeeds;
- a safe test clip/request with `expires_at` already in the past becomes
  `expired` and its `incident_clip_chunks` rows are physically removed;
- repeat that proof while the originating site agent is stopped/offline;
- the incident/event row remains after media deletion.

Do not shorten a real customer's retention window simply to perform this test.
Use an isolated test row/transaction or other approved non-customer evidence.

## 7. Acceptance result

### NVR AI

- **PASS**: at least one configured native smart analytic produced a correctly
  sourced WatchLog incident on this exact model/firmware.
- **GO WITH LIMITATIONS**: recorder events work but one or more advertised smart
  analytics are unavailable on this firmware.
- **NO-GO**: native event integration is unreliable or misclassifies source.

### Incident footage

- **PASS**: explicit request retrieves the correct incident window and download
  integrity/time/camera are verified, with independent expiry cleanup proven.
- **GO WITH LIMITATIONS**: evidence is retrievable but vendor-native format or
  coarse recorder segmenting needs an operator player/manual trim.
- **NO-GO**: wrong camera/time, cross-tenant/authz failure, corrupt payload,
  integrity bypass, credential leakage, uncontrolled upload size, retention
  failure, or continuous video transfer.

Record model/firmware-specific evidence in the Handoff D result. Only validated
models may be described as supporting WatchLog incident-footage retrieval.
