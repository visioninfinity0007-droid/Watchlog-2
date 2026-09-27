# WatchLog Windows — Current Field Context (2026-09-28)

This document is the Windows/Site-Connector field handoff.

For full product/database/site context, use:

`Alkalid-security/Watchlog/docs/production/CURRENT_LIVE_CONTEXT_2026-09-28.md`

## Current release authority

Authoritative release branch:

`build/site-connector-v5-watchlog2`

Current successful Windows release:

- Build: **76**
- Version: **5.0.21**
- Source SHA: `26b036a654446b3e8c262b2f476118f2e2f41916`
- Windows Release run: **#76**
- Run ID: `36346424676`
- Artifact: `WatchLog-Windows-76`
- Artifact ID: `10941445308`
- Artifact digest:
  `sha256:0fdf73bc6421291e58413330ac73b439a6256f59c95227cc4dfd1e4c6eea54b5`

Build 76 is the current installer for new field work.

## Build 76 changes relevant to live sites

### ONVIF physical-camera normalization

Older ONVIF builds could treat recorder encoding profiles as cameras.

Example failure:

- Camera 1 MainStream
- Camera 1 SubStream

were exposed as two WatchLog cameras even though both came from one physical
VideoSource.

Build 76 collapses ONVIF profiles by physical video source and prefers one
profile token for snapshot use.

The product/database sync path is also backward-compatible with older deployed
Agents and preserves historical transport-profile evidence.

### Hikvision incident footage compatibility

Build 76 hardens bounded recorded-video retrieval:

1. search recorded media first;
2. use the recorder-returned playback URI;
3. include Hikvision legacy search metadata fields;
4. support firmware-dependent GET/POST behavior for
   `/ISAPI/ContentMgmt/download`;
5. retain existing byte/time bounds.

This is still a field-acceptance feature: code/workflow success does not prove
every Hikvision firmware exports clips.

## Current live field baselines

### Al-Khalid Security Services

Recorder:

- Dahua `DH-XVR1B08-I`

Last verified deployed Agent:

- 5.0.16
- persisted driver: ONVIF

Production database now exposes:

- 8 canonical physical cameras
- 8 hidden historical transport/profile rows

Historical events/snapshots were preserved.

The old stream-profile role labels contained contradictions on physical Cameras
1 and 2. Role-specific rules for Reception / Director's Office / Armory Gate /
Admin Entrance were disabled until the actual physical views are re-confirmed.

Build 76 should be used for the next Agent upgrade so Agent-side source-token
normalization matches the live cloud normalization.

### HASCO Steel — Head Office

Recorder:

- Hikvision `DS-7608NI-Q1`

Last verified deployed Agent:

- 5.0.19
- driver: `hikvision-isapi`

Physical cameras: 8.

The customer/business camera roles are managed in the product repository/live
database, not in this installer repo.

### Chai Wala — Chota Bukhari

Recorder:

- Hikvision `DS-7608NI-Q1`

Last verified deployed Agent:

- 5.0.17 / Build-69 lineage
- driver: `hikvision-isapi`

A real cloud incident clip request was claimed successfully by the Build-69
Agent, proving the request/worker transport path.

The recorder returned no validated video bytes through the old download path,
so the request ended `unsupported`.

Therefore:

- clip worker transport = proven
- Build-69 on-demand video export on this NVR = **not proven**
- Build-76 field clip export on this NVR = **pending field acceptance**

Do not claim Chai Wala clip extraction is solved until Build 76 or later returns
real bounded bytes from the physical recorder.

## Mandatory field acceptance for Build 76

For each pilot recorder:

1. install/upgrade with Build 76;
2. confirm one physical camera row per physical source;
3. verify native driver where applicable;
4. verify camera still acquisition;
5. request a bounded historical window;
6. prove actual video bytes are returned;
7. verify clip hash/size/content type;
8. induce a bounded connectivity/recording gap;
9. prove recovery/backfill after connectivity returns;
10. capture support logs and exact recorder firmware.

For Al-Khalid specifically:

- verify the 8-camera physical inventory remains stable;
- visually map the real channel views;
- only then restore the four ambiguous office role rules.

For Chai Wala specifically:

- repeat the bounded clip test that failed on Build 69;
- record the recorder-returned playback URI behavior;
- do not enable an automatic multi-camera clip workflow until one-camera export is proven.

## Release guardrails

- Recorder passwords must never be committed.
- Do not hard-code a customer site code.
- Do not treat ONVIF profile count as physical-camera count.
- Do not delete historical evidence to repair camera identity.
- Do not advertise `operations_evidence_clip` as field-proven until real bytes are returned.
- Do not make recorder writes as part of clip retrieval.
- Keep clip retrieval bounded in time and bytes.
- Always resolve a field installer to build/version/source SHA before diagnosing it.
