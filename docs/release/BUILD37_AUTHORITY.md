# WatchLog Windows Build 37 — authoritative baseline

**Status:** Build 37 is the frozen historical lineage anchor. The authoritative latest field installer is Build 46 as of 2026-09-23.

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
