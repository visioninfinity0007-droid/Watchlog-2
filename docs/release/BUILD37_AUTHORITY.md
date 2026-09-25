# WatchLog Windows Build 37 — authoritative baseline

**Status:** Build 37 is the frozen historical lineage anchor. The authoritative latest Windows installer is **Build 56 / WatchLog 5.0.8** as of 2026-09-25.

## Canonical identity

- Repository: `visioninfinity0007-droid/Watchlog-2`
- Build branch: `build/site-connector-v5-watchlog2`
- Source commit: `cffd32a47c70fc1141efef7e30315a5c4c844c57`
- Product version in that source: `5.0.0`
- GitHub Windows Release run: **#37**, run id `35695121845`
- Release conclusion: **success**
- Artifact: `WatchLog-Windows-37`
- Artifact id: `10680586104`
- Artifact size: `271634225` bytes
- GitHub artifact digest: `sha256:12349dc7796e0efd5e4762edde26d9f496d46413a9a49d4f43bd893250d2e71c`

This commit is the baseline even if another repository contains newer-looking
installer code. The shipped artifact determines authority, not repository age,
branch naming, or a later refactor.

## Build-forward rule

All Windows installer corrections after Build 37 must:

1. start from Build 37's source commit above or a verified descendant in this repository;
2. preserve Build 37 as a frozen historical reference rather than rewriting it;
3. run the Windows release workflow from `build/site-connector-v5-watchlog2`;
4. record the successor source SHA, release run number, artifact name and digest;
5. update the main WatchLog repository's installer context document so operators and AI
   assistants do not accidentally patch a different code line.

`Alkalid-security/Watchlog` is **not** the authoritative Windows-installer
source for this lineage. It may contain mirrored/product code, but installer
changes must not be treated as release fixes until they are applied and built
from Watchlog-2.

## Field defects found on Build 37

Two setup defects were confirmed from the field screenshots:

- a discovered recorder could look highlighted while the local-address field
  remained empty, causing Continue to reject a visibly selected recorder;
- optional recorder-push / PC-free integration could hold the Connecting screen
  for the old 90-second child timeout after the core site connection was
  already established.

The successor change makes the first discovered recorder a real selection,
allows Continue to fall back to the highlighted row, and caps optional recorder
integration to a short bounded window. The successor product version is
`5.0.1`.

## Non-negotiable verification

A replacement installer is not "latest" merely because source was changed.
The next authoritative baseline is established only after its Windows Release
run completes successfully and the generated artifact identity is recorded
here (or in a successor baseline ledger).


## UI confirmation hardening

A second audit of Build 37 confirmed the recorder-selection failure is a Qt/UI
state problem and found an additional UI race: Recorder **Continue** remained
enabled while asynchronous network discovery was still running.

The successor now also disables Continue during discovery, maps direct row
clicks to the address field, auto-selects only when exactly one recorder is
found, and embeds a `--ui-selftest` in the frozen setup executable. CI and the
Windows release workflow execute that **packaged EXE** self-test against the
Build 37 visible-row/empty-field failure and the discovery race.


## Successor baseline: Build 39

Windows Release **#39** (run id `35721540889`) completed successfully from
source commit `a635ef16b7b7f50bfec1bc65a7f255e92727a80c`.

- Product version: `5.0.1`
- Artifact: `WatchLog-Windows-39`
- Artifact id: `10692431328`
- Artifact size: `271640486` bytes
- GitHub artifact digest:
  `sha256:80435f10108c12bb5f36f31891382d65eaf469f068f2f71f5b06683ca8f9a36c`
- Packaged recorder-selection UI self-test: **passed**
- Windows release workflow: **passed**

Build 39 now supersedes Build 37 as the authoritative latest Windows installer
baseline for this lineage.


## Current authoritative baseline: Build 46

Windows Release **#46** (run id `35855322767`) completed successfully from
source commit `e39cf1cc04c7ab52f115484b98f926b06cb85c71`.

- Product version: `5.0.3`
- Artifact: `WatchLog-Windows-46`
- Artifact id: `10747667489`
- Artifact size: `271650885` bytes
- GitHub artifact digest:
  `sha256:f8d2be2f59a258d562e7cec51ca71d684073eb5c4fb9e8312ce588220bf58298`
- Windows Release workflow: **passed**
- Packaged setup-UI lifecycle/self-test: **passed**
- Recorder field-regression gate: **passed**
- Installer/NSIS contract: **passed**

Build 46 supersedes Build 39 as the authoritative latest Windows installer
baseline. Build 37 and Build 39 remain historical references only.

### Field fixes promoted in Build 46

Build 46 incorporates the fixes found from the subsequent Salman field runs:

- the NSIS parent no longer waits indefinitely after the setup UI reaches
  **WatchLog is ready**. When setup is launched by NSIS it enters
  `--installer-child` mode, shows Ready briefly, exits with code 0
  automatically, and lets NSIS continue to its Finish page;
- Step 06 no longer runs the broad post-install acceptance suite as another
  installation gate after the recorder/site/cameras/heartbeat/background agent
  are already proven;
- recorder-login UI has a hard 30-second watchdog, re-enables Test Connection,
  and ignores stale late worker results;
- RTSP-only candidates are re-identified after targeted web-port rescue, so a
  Hikvision recorder is routed back through Hikvision/ONVIF handling instead of
  generic vendor probing;
- Hikvision browser login and Hikvision integration authentication are no longer
  conflated. WatchLog probes the documented ISAPI identity service, falls back
  to ONVIF when appropriate, and gives an explicit ISAPI/HTTP-authentication
  action when the browser works but the integration API does not;
- Hikvision LAN sessions ignore system HTTP proxies and tolerate the common
  HTTP-to-self-signed-HTTPS redirect path;
- the release gate executes the **frozen packaged UI** in installer-child mode
  and fails if that lifecycle does not terminate; recorder field regressions
  run before the unrelated backend contract that is currently red.

Do not promote a later installer merely because a source commit or workflow
exists. Record its successful Windows Release run and artifact identity here
first.


## Successor baseline: Build 41

Windows Release **#41** (run id `35743559870`) completed successfully from
source commit `b0da326fb2d3ceb675c03ff4afc77a3b573d1d42`.

- Product version: `5.0.2`
- Artifact: `WatchLog-Windows-41`
- Artifact id: `10702331062`
- Artifact size: `271641628` bytes
- GitHub artifact digest:
  `sha256:9174a5e2fabf9e92b832a7d1090ee06779c2544c7813312937d1770b8398c9e7`
- Windows release workflow: **passed**
- Packaged setup UI / connector self-test gate: **passed**
- Step 06 no longer runs full acceptance as an installer-success gate.
- Recorder login UI has a 30-second watchdog and ignores stale late worker results.
- Hikvision local HTTP(S) sessions ignore proxy environment settings and do not
  retry Basic auth after a Digest-auth rejection unless the recorder explicitly
  advertises Basic.

Build 41 supersedes Build 39 as the authoritative latest Windows installer
baseline. Real Hikvision hardware login still requires field confirmation on the
customer recorder; the release pipeline proves packaged behavior, not the
customer's credentials/firmware response.


## Current authoritative baseline: Build 50

Windows Release **#50** (run id `36038016858`) completed successfully from
source commit `4d7533d7525b2cecffa47d566d277d92e7b30054`.

- Product version: `5.0.7`
- Artifact: `WatchLog-Windows-50`
- Artifact id: `10825154761`
- Artifact size: `66535907` bytes
- GitHub artifact digest:
  `sha256:7260ed7c441ea5ac4afc3aa5d7986f9fd38b16d464669db8bf7e69ad19f6a932`
- `WatchLog-Setup.exe` SHA-256:
  `DE6B681F306C2B795ADA85A4657D1480B2C92E90C2DBF87FF2F45D5467E7EE77`
- `watchlog-agent.exe` SHA-256:
  `553ADBB92B5ACA18F07DA2B72C999DDE3338EAE56541A2BAD569774B9A9FF21D`
- `watchlog-setup-ui.exe` SHA-256:
  `436CDCCAB0AD90A2C80F6BE7C2976A0AF9A96ED8573A60882241CDC284DC20FA`
- Windows release workflow: **passed**
- Packaged setup UI lifecycle/self-test: **passed**
- Packaged connector self-test: **passed**
- Python compile and recorder field-regression tests: **passed**

Build 50 supersedes Build 49 as the authoritative latest Windows installer
baseline.

### Why Build 50 exists

The Build 41/49 field evidence showed that a Hikvision site could authenticate
during setup and heartbeat to WatchLog while the long-running recorder data path
later became unreachable or produced no events. Build 50 changes the runtime
rather than merely changing the installer verdict:

- Hikvision native alert monitoring is divided into bounded 45-second slices.
  Between slices the SAME authenticated driver/session captures one rotating
  camera still, avoiding a second concurrent recorder login.
- The rotating still is uploaded as a `visual_sample` event with its JPEG. The
  normal snapshot trigger therefore queues it for the WatchLog server-side visual
  review pipeline even when the recorder's native motion/smart-event stream is
  quiet or disabled.
- The health worker reuses fresh live-collector recorder truth instead of opening
  another competing Hikvision Digest session. Per-camera health becomes positive
  only after that camera has produced a real JPEG sample.
- Unvalidated Hikvision archive recovery is disabled so it cannot create another
  simultaneous recorder session beside live monitoring.
- Build 49's recorder-backed readiness gate remains: Setup cannot report Ready
  merely because the cloud heartbeat works.

This is the first build in this lineage that directly addresses both sides of the
field symptom: **installation readiness** and **continued Hikvision camera data**.


## Current authoritative baseline: Build 56

Windows Release **#56** (run id `36133615686`) completed successfully from
source commit `9330c297f12a7b387059d1356b8f4fd113b4336f`.

- Product version: `5.0.8`
- Artifact: `WatchLog-Windows-56`
- Artifact id: `10862408445`
- Artifact size: `66559271` bytes
- GitHub artifact digest:
  `sha256:2160eab4240b2e6c43bf3a30f6958b7e73b00494efead9decc6847fe01da5232`
- `WatchLog-Setup.exe` SHA-256:
  `F0682D449C1E08A6AC687D714E6E2372F277F635071B1C1E9D0C4A870B61E670`
- `watchlog-agent.exe` SHA-256:
  `24853EEBFD227544C3F836B604EC3D238066F729D7762AA023F1ACF5AC752185`
- `watchlog-setup-ui.exe` SHA-256:
  `B9167034E716D05757BE2753549C3E5A4999799BDA02CDC4F82723F508313D19`
- Windows Release workflow: **passed**
- Packaged setup-UI lifecycle/self-test: **passed**
- Packaged connector self-test: **passed**
- Hikvision archive/download contract: **passed**
- automatic recovery wiring contract: **passed**
- recorder push-bridge parser/liveness contract: **passed**

Build 56 supersedes Build 50 as the authoritative Windows installer baseline.

### Production resilience added in Build 56

- **Dahua:** existing recorder-native monitoring, archive search/download and
  resumable recovery remain enabled.
- **Hikvision:** production package now includes bounded ISAPI ContentMgmt
  archive search and incident-video download, with historical segments wired
  into the same resumable recovery engine.
- The recovery clock now advances only while the recorder itself is freshly
  observed; a cloud heartbeat from the Windows PC can no longer erase an NVR
  connectivity gap.
- Recovery detects both a PC restart/sleep gap and an in-process recorder/network
  outage, then opens a resumable recovery interval after recorder contact returns.
- Recorder-push provisioning runs asynchronously after normal monitoring starts;
  it can never block setup or Step 06.
- Push configuration read-back is not called end-to-end success. The agent waits
  for `wl_agent_push_status` to prove a real recorder POST reached WatchLog.
- Hikvision HTTP-host configuration requests an NVR-originated **30-second
  heartbeat**, all events, binary images and broken-link retransmission, using the
  current ISAPI host-write endpoint with a legacy per-ID fallback.
- Production DB migration for `wl_agent_push_status` was applied on 2026-09-25.

### Hardware-validation boundary

The packaged/release gates prove the code path and artifact. Exact firmware
behavior remains a field acceptance item. A Hikvision or Dahua site is only
declared PC-off push verified after `push_sources.last_push_at` records a real
NVR-originated POST. Hikvision clip extraction is only declared hardware-proven
after the customer's recorder returns a non-empty bounded archive clip. Failures
remain visible/unsupported; the product must not fabricate capability.
