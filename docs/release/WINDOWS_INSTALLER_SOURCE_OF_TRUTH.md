# WatchLog Windows Installer — Source of Truth

For current live tenant/site/runtime context, also read:

`docs/production/CURRENT_LIVE_CONTEXT_2026-09-28.md`

This document is the release authority for Windows installer work: the one production
install/upgrade/uninstall path, its components, the update contract and the release status.
It does not repeat procedures that live elsewhere:

| Document | Purpose |
|---|---|
| this document | the production path, components, update contract, release status |
| `docs/runbooks/WINDOWS_RECORDER_FIELD_ACCEPTANCE.md` | field acceptance procedure on a real site PC and recorder |
| `docs/runbooks/WINDOWS_EXISTING_SITE_REPAIR_UPGRADE.md` | operator runbook for Repair/Upgrade of an enrolled site |
| `docs/acceptance/ARCHIVE_GAP_RECOVERY_STATUS.md` | footage/archive and gap/recovery status; recording and storage truth as separate states |
| `docs/release/WINDOWS_PACKAGING.md` | how the release is built (locked toolchain, payload proof) |
| `docs/security/MACHINE_BINDING.md` | DPAPI machine-binding proof |

---

## 0. Release candidate 5.1.1: production path and status (2026-10-06)

Section 0 and section 1D govern 5.1.1. Sections 1A-1C record earlier unpromoted candidates and
sections 2-12 record the 5.0.x lineage; where they disagree with section 0 or 1D, section 0 and
1D win.

Status words used in this document set: NOT STARTED, FIXED LOCALLY, CI VERIFIED, INSTALLER
VERIFIED, FIELD VERIFIED, PRODUCTION VERIFIED, BLOCKED. **CI VERIFIED** means the repository CI
suite was reproduced locally (`wl-ci-local.py` / `wl-ci-windows.py`). No 5.1.1 commit has run on
GitHub CI, no 5.1.1 installer has been built by the Windows Release workflow or signed, and 5.1.1
is installed at no site. Nothing about 5.1.1 is FIELD VERIFIED or PRODUCTION VERIFIED.

### 0.1 Release identity

- Candidate line: local branch `release/5.1.1` (nothing pushed). 5.1.1 is a candidate,
  **not promoted**: no Windows artifact has been built from it.
- Database prerequisite: contract v4 (`mr/db-contracts` migrations `0146`-`0155`, plus 0156 and
  0157), as for 5.1.0 (section 1C); applied in production (section 0.7).
- Deliberate downgrade to 5.0.x: the 5.1.0 rule applies unchanged, see
  `docs/runbooks/WINDOWS_EXISTING_SITE_REPAIR_UPGRADE.md`, section
  "Deliberate downgrade from 5.1.0 to 5.0.x".
- Version string: `prototype/agent/wl_version.py` and the NSIS `APPVERSION` fallbacks still read
  `5.1.0`. The bump to 5.1.1 (`tools/bump_version.py`) belongs to the RC freeze; until then
  section 1 names the version the code carries (`test_release_version_contract.py`).
  Status: NOT STARTED.
- Immutable RC record (source SHA, version, `watchlog-agent.exe`, `watchlog-setup-ui.exe`,
  `WatchLog-Setup.exe` and `WatchLog-Repair-Upgrade.exe` SHA-256, workflow run, artifact ID):
  NOT STARTED. It is recorded here when it exists.

### 0.2 The one production path

WatchLog has one production installer technology, **NSIS**, built only by
`.github/workflows/windows-release.yml` running `tools/build_windows_release.ps1`
(`tools/make_installer.ps1` only delegates to it). Build details: `WINDOWS_PACKAGING.md`.

| Package | Use |
|---|---|
| `WatchLog-Setup.exe` | New site or new PC: first install, recorder discovery and login, cameras, enrollment. On a fully connected site it refuses, before stopping anything, and points to Repair/Upgrade (`watchlog.nsi`). |
| `WatchLog-Repair-Upgrade.exe` | Existing enrolled site: repair, upgrade and bootstrap, with no discovery. Procedure: `docs/runbooks/WINDOWS_EXISTING_SITE_REPAIR_UPGRADE.md`. |
| `uninstall.exe` | Written by both packages (same Uninstall section, compared by a test). |

Installed components (`C:\Program Files\WatchLog`): `watchlog-agent.exe` (background Agent, entry
point `release_agent.py`), `watchlog-setup-ui.exe` (WatchLog Setup, Site Status, Manage
Recorders), `run-agent.ps1` (launcher), `register-service.ps1`, `apply-remote-update.ps1`,
`wl-upgrade.ps1`, `watchlog.ini` (no recorder password), `READ ME FIRST.txt`, `setup.ico`,
`uninstall.exe`. Machine state lives in `C:\ProgramData\WatchLog` (section 0.4).

- Scheduled task `WatchLog Agent`, SYSTEM, highest privileges: a start-up trigger (30 s delay)
  plus a 5-minute watchdog trigger with "ignore new instance". The launcher restarts an exited
  Agent after 15 s.
- Start menu, all users: WatchLog Setup, WatchLog Site Status, WatchLog Manage Recorders,
  Uninstall WatchLog.
- Add/Remove Programs key in the 32-bit view with `DisplayVersion` and `ComponentsVersion`
  (section 1D).

**WatchLog Setup is the only configuration path.** The packaged `watchlog-agent.exe --setup`,
and the automatic console wizard the Agent used to start when no recorder was configured, exit
with code 2 and write nothing (`release_agent.py`, `test_console_setup_retired.py`). The console
wizard wrote the recorder password in plain text into `watchlog.ini` and ignored the recorder
registry. The legacy `prototype/installer/Install-WatchLog.ps1`, `Install WatchLog.cmd`,
`Uninstall-WatchLog.ps1` and `run-agent.cmd` are deleted; NSIS and `wl-upgrade.ps1` still remove
a `run-agent.cmd` left on an older site. No Inno Setup script exists.

Not production: an unsigned build (test-only, section 0.6), a locally compiled NSIS package, and
any build from the Watchlog-2 field line.

### 0.3 Status at a glance

| Item | Status | Evidence |
|---|---|---|
| One NSIS path; console setup retired; legacy installer files deleted | CI VERIFIED | `0b77080c`; `test_console_setup_retired.py`, `test_portal_alignment_contract.py` |
| Repair/Upgrade lifecycle: version-aware guard, per-recorder proof, proven rollback, interrupted-upgrade recovery | CI VERIFIED; elevated steps (SYSTEM task, real powercfg, `test_upgrade_shutdown.ps1`) NOT STARTED | section 1D; `e3a534d5` |
| Remote-update stage trust (a standard user could get code run as SYSTEM through the ProgramData stage) | CI VERIFIED; elevated test NOT STARTED | `f85df0a9`; `test_remote_update_stage_trust.py`. Fielded 5.0.24-5.0.26 stay exposed until upgraded with the Repair package |
| Update contract (`AGENT_ONLY_COMPATIBLE` / `REQUIRES_REPAIR_PACKAGE`) | CI VERIFIED | section 1D; `915d5ea4`; `test_update_contract.py` |
| Credentials and site data on the PC | CI VERIFIED | section 0.4 |
| DPAPI machine binding on two physical PCs | NOT STARTED | `docs/security/MACHINE_BINDING.md` |
| Server-owned stills | CI VERIFIED | section 0.5; `5f308766`; `test_server_owned_stills.py` |
| Hikvision field safety | CI VERIFIED | section 0.5; `e6f87e44`, `3fb462e7`, `1eb5b7b6`; `test_hikvision_field_safety.py` |
| Uninstall keep-list policy and power baseline restore | CI VERIFIED; powercfg on a real scheme NOT STARTED | section 1D; `4db87674`; `test_installer_lifecycle_contract.py` |
| Packaging: hash-locked builds, NSIS payload proof, baked `BUILD_SHA` | CI VERIFIED (local); Windows Release end to end NOT STARTED | `67906ebc`; `WINDOWS_PACKAGING.md` |
| GitHub CI on a 5.1.1 commit | NOT STARTED | |
| Windows Release artifact (Setup + Repair, hashes) | NOT STARTED | |
| Code signing | BLOCKED | section 0.6 |
| Installer run on a test PC | NOT STARTED | |
| Field gates | NOT STARTED | `docs/runbooks/WINDOWS_RECORDER_FIELD_ACCEPTANCE.md` section 4 |
| Production database for 5.1.x: migrations 0144-0157, ledger reconciled, recorder backfill | PRODUCTION VERIFIED (owner, 2026-10-06; database only) | section 0.7 |
| Deployed portal, report runner and push bridge match canonical | NOT STARTED | |

### 0.4 Credentials and site data on the PC

- Secrets are DPAPI **LocalMachine** blobs in `C:\ProgramData\WatchLog\Secrets`: `agent_key.dpapi`
  (cloud key), `nvr_credential.dpapi` (the continuity recorder's legacy copy) and one login per
  recorder under `Secrets\recorders\<local_id>.dpapi`. A recorder never falls back to another
  recorder's login. The folder (and `Secrets\recorders`) is owned by Administrators and its DACL
  is exactly SYSTEM + Administrators, FullControl, inheritance off, verified before any blob is
  written (`windows_secret.py`; Windows Security Gate, `4d7faa5c`).
- `watchlog.ini` carries no recorder password. `watchlog.env` (0.3.3 plain text) and
  `nvr_password.dpapi` (0.2-0.3.2) are legacy files: Setup migrates them into the DPAPI store and
  removes them.
- The recorder registry (`recorders.json`) carries a site stamp (`site_id`). At start the Agent
  sets aside (never deletes, never uses) a registry stamped for another site together with its
  recorders' queues, state and logins, and stamps an unstamped one as this site's
  (`recorder_registry.ensure_registry_belongs`). Queued data is stamped by `site_runtime.json`;
  another site's queue is set aside before anything uploads (`site_runtime.py`).
- A queued event the server rejects is set aside to `spool.sqlite.rejected.jsonl`, so one
  rejected row no longer holds the whole queue (`af44f166`).
- Secrets, the recorder registry and the Agent identity are fsync'd before they replace the old
  file (`4abb10d3`).
- The Repair registry preflight compares, without failing, the continuity recorder's login with
  the legacy copy and returns `legacy_mirror` plus a Manage Recorders instruction when they differ
  (`setup_gui.py`, `7525c434`). Open gap: `wl-repair-upgrade.ps1` does not yet log or show that
  result, so the operator cannot see it. The comparison is CI VERIFIED
  (`test_setup_registry_selftest.py`); showing it in Repair is NOT STARTED.

### 0.5 Server-owned stills and Hikvision field safety

Stills (owner decision, 2026-10-06):

- The server owns scheduled periodic restaurant capture: its scheduler (migration 0125) issues
  `snapshot_requests` to Agents that advertise `config_snapshot_requests`. The Agent is the
  transport for those requests.
- The Agent never also takes its own periodic still of a camera the server is scheduling: a camera
  the server asked for is server-owned for 900 s after that request (`server_capture.py`).
- Native monitoring is never suppressed; a still failure never stops monitoring; cameras not
  enrolled in the server schedule gain no capture; 5.0.17 (which does not advertise the
  capability) is unchanged. Test: `test_server_owned_stills.py`.

Hikvision (field Build 69 behaviour restored, per recorder):

- the alert stream runs in 30 s slices (`HIKVISION_STREAM_SLICE_SECONDS`, also reported by the
  frozen Agent's `--version` for the release payload proof);
- ISAPI calls are serialised by a per-recorder lock with fair handoff, so one recorder never
  holds another;
- the camera still is sampled between slices on the same session;
- a silent stream is a stream error, never a quiet re-open;
- recorder health is taken from the live collector only while its stream is proven live and a
  real recorder assessment is at most 900 s old; recording and storage are never inferred from
  the stream, and a fault that needs a recorder read is reported as not observed
  (`drivers/hikvision.py`, `native_event_collector.py`, `watchlog_agent.py`).

### 0.6 Code signing

A production build requires a code-signing certificate: the Windows Release `production` input,
and every `v*` tag push, hard-fail without one (`build_windows_release.ps1 -Production`). No
certificate exists, so a production release is **BLOCKED** (T3 owner blocker: certificate
procurement). An unsigned build is test-only: it is not for customer sites and never for the
update channel.

### 0.7 Fleet and production state (2026-10-06)

- Fleet: Chai Wala 5.0.17 (Hikvision, Build 75), HASCO 5.0.26, Al-Khalid 5.0.26.
- Production database (owner-verified): migrations 0144-0157 applied and the ledger reconciled;
  recorder backfill complete (40/40 cameras, 23,772/23,772 events, 7,016/7,016 analytic events
  with recorder provenance); one continuity owner per site; the 5.0.x Agents kept heartbeating.
  5.1.1 needs no further Multi-NVR migration.
- Not verified: that the deployed portal, report runner and push bridge match canonical.
- Chai Wala after the migration: 4 `visual_sample` / `periodic_snapshot` events. That is a PASS
  only for legacy periodic ingestion compatibility. Native alarms, clip/archive, recovery and
  recording truth there are NOT proven.

---

## 0A. Release candidate 5.1.2: production hardening (branch `release/5.1.2`)

**Status: candidate, not promoted.** No Windows artifact has been built from it yet. It is
production-ready only when `python tools/acceptance_matrix.py gate --scope 5.1.2` passes: every
5.1.2-scope capability FIELD VERIFIED (or UNSUPPORTED BY THIS HARDWARE, with evidence) on BOTH a
physical Dahua recorder (certification site Al-Khalid, DH-XVR1B08-I) and a physical Hikvision
recorder (HASCO Steel, then Chai Wala; both DS-7608NI-Q1, so that is the certified model).
Matrix: `docs/acceptance/production_acceptance_matrix.json`.

- **Database prerequisite:** contract v4 (`mr/db-contracts` migrations `0146`-`0155`, plus 0156
  and 0157, applied in production) and the 5.1.2 migrations: 0159 (camera sync after an ONVIF
  era) and 0160 (viewer RPCs tenant-scoped), both applied in production on 2026-10-07; 0161
  (recording/storage truth), 0162 (Agent runtime status), 0163 (remote acceptance test), 0164
  (native-event evidence, off per site by default), 0165 (fault/restore/raw signals are never
  presence), repo only until approved. Canonical `main` owns 0158 (restaurant metrics).
- **Mandatory scope:** Dahua archive/clip channels 1-based (field-proven); tenant isolation;
  Setup stops a running Agent before the recorder login (HASCO); recorder and per-camera
  recording truth; storage/HDD truth for both vendors (a nearly full overwriting disk is
  healthy); tamper never depends on person/vehicle detection; video loss -> restore,
  disconnect -> reconnect, recorder restart; capability reporting outside analytics; worker
  runtime state and supervised restarts; remote `run_full_acceptance_test`, diagnostics,
  restart, reconnect; evidence T-15 s..T+30 s with attribution and server-side clip hash.
- **Multi-recorder field acceptance:** the fifteen proofs are listed in section 1C; the
  two-NVR field procedure is `multi-nvr-audit/rc-5.1.1-ci/FIELD-TEST-2NVR.md`.
- **Deliberate downgrade to 5.0.x:** the 5.1.0 rule applies unchanged, see
  `docs/runbooks/WINDOWS_EXISTING_SITE_REPAIR_UPGRADE.md`, section
  "Deliberate downgrade from 5.1.0 to 5.0.x".

---

## 1. Authoritative source

Authoritative repository:

`Alkalid-security/Watchlog`

Authoritative branch:

`main`

Current source line under validation:

**5.1.2**

That is the version string the code carries: the production-hardening release candidate
(section 0A, branch `release/5.1.2`), not promoted. 5.1.1 (section 0 and 1D) is the previous
candidate; its unsigned build #141 runs at Al-Khalid and HASCO for field evidence only.

5.1.0 (section 1C, branch `mr/agent-5.1.0`, merged to `main`) is the multi-recorder Agent and
installer **candidate, not promoted**: no Windows artifact has been built from it, none is
promoted to any update channel and none is installed on any site. It requires database contract
v4 (`mr/db-contracts` migrations `0146`-`0155`), which production now has (section 0.7), and it
contains every 5.0.28 fix.

5.0.28 (section 1B, branch `fix/agent-5.0.28`) is an Agent-only **candidate, not
promoted**: no Windows artifact has been built from it and none is installed on any
site. It stays unpromoted until its exact merge SHA, Windows workflow run, artifact
IDs/hashes and physical site acceptance (Al-Khalid first; see section 1B) are
recorded below. 5.0.27 (section 1A) was never promoted either. Build 69 / 5.0.17
remains the field-proven discovery/connectivity baseline until that happens.

Existing-site Repair/Upgrade implementation merge:

PR `#78` / merge commit `e74526affc4722daf5e14aab07e56b08f72c3d44`

This main branch contains both:

- the Build-69-derived discovery/connectivity reliability work; and
- the newer Site Control / secure remote-update / Hikvision readback work.

The separate `visioninfinity0007-droid/Watchlog-2` repository is no longer the
product source of truth. It remains useful as a Windows release-line validation
repository because Builds 69–83 were produced there and provide exact field/release evidence.

---

## 1A. 5.0.27 A-Z Agent repair candidate

5.0.27 closes production-composition defects observed while 5.0.26 was
heartbeating on HASCO and Al-Khalid without current event/snapshot evidence.

Candidate scope:

- production native-event collector publishes current recorder transport liveness;
- packaged Analytics run loop starts the automatic recorder recovery worker;
- runtime health advances recorder-seen proof only from current recorder activity,
  not from a stale startup identity;
- incident footage and recovery use a separate read-only vendor-native archive
  transport when a proven live ONVIF path identifies Dahua/Hikvision;
- archive scans execute bounded recorded-frame retrieval + local analytics instead
  of the previous placeholder composition;
- Hikvision incident footage has a 90-second total retrieval budget and a bounded
  candidate count so one request cannot spend ~30 minutes cycling playback URIs.

**Do not deploy merely because CI is green.** Promotion requires one exact Windows
artifact to prove on HASCO first, then Al-Khalid:

1. Repair/Upgrade passive preflight;
2. recorder auth + configured channel inventory;
3. continuous heartbeat without false recorder-seen freshness;
4. native events and still evidence during the configured monitoring window;
5. camera/NVR health;
6. bounded incident clip retrieval for a known camera/time;
7. automatic recovery of a controlled/reproducible gap where recorder archive
   evidence exists, with RECOVERED provenance;
8. Site Control poll/read path;
9. remote-update poll + transactional rollback;
10. reboot/restart survival and a quantified soak.

Any failed gate keeps 5.0.27 unpromoted and preserves rollback.

---

## 1B. 5.0.28 Agent live-site fix candidate (`fix/agent-5.0.28`)

5.0.28 is an **Agent-only** candidate for the live 5.0.x sites. It carries no database
migration, so it runs against the schema production already has, and a rollback to 5.0.27
has no database step. It is **not promoted**: no Windows artifact has been built from it, no
Windows workflow run, artifact ID or executable hash exists for it, and it is installed on no
site. Build 69 / 5.0.17 stays the field-proven baseline; 5.0.27 stays unpromoted.

### Changes by audit ID (multi-NVR audit, MNVR-###)

Live events and recorder liveness:

- **MNVR-008 / MNVR-005**: recorder liveness comes from the event stream, not from a probe that
  answers. Hikvision alertStream and Dahua attach count only after a 2xx and on every received
  chunk (keep-alives included); a 2xx that ends before any chunk is taken back. ONVIF counts
  every PullMessages response that comes back (empty pulls included), never a failed pull,
  subscribe or renew. The heartbeat writes a per-recorder `event_stream` block
  (`connected`, `connected_at`, `last_frame_at`, redacted `last_error`) into the local
  runtime-health proof, and `last_live.json` is persisted from stream activity in the shipped
  run loop, so restart and outage gaps can open recovery intervals.
- **MNVR-022**: a dropped Hikvision/Dahua/ONVIF stream is reopened on the same driver after a
  short jittered delay instead of a 20 s wait plus a full re-probe; auth failures still take the
  5 -> 15 -> 30 min backoff.
- **MNVR-023**: burst collapse runs on a monotonic receive clock (Hikvision, Dahua, ONVIF), so a
  backward clock step no longer suppresses events.
- **MNVR-024**: event time provenance is explicit. Hikvision naive times are localised with the
  recorder's stated offset (or receive time is used); Dahua takes receive time before still/AI
  work; ONVIF reads the inner `tt:Message` UtcTime and trusts it within 300 s of the PC
  (otherwise receive time, with `device_utc` and `clock_skew_s` kept). All three drivers name
  the clock in `payload.clock_source`.
- **MNVR-028**: recorder-level events (disks, alarm inputs, ONVIF storage faults) and camera
  alerts without a channel carry channel `null` and `payload.recorder_scoped` (or
  `channel_unknown`), never camera 1. `Event.to_json` serialises a JSON null channel;
  `--probe` and the collectors no longer crash on or mis-attribute them. A Hikvision `IO`
  alert, or any alert carrying `inputIOPortID`, is recorder-scoped with the port in
  `payload.native_input`, even when it also carries a `channelID`.
- **MNVR-001**: the ONVIF driver loads its profile/token maps on the driver `open_driver()`
  returns; events resolve to the physical camera; an unknown token is dropped and counted
  (`event_stream.dropped_unmapped`, agent log), never put on camera 1; stills use that camera's
  profile.
- **MNVR-027 / MNVR-056**: ONVIF `Initialized`/`Deleted` property messages and cleared states
  are not occurrences; pull points are renewed from the granted lifetime and unsubscribed on
  close and before resubscribing.
- **MNVR-054**: Hikvision targetType split on whitespace, not the letter "s".
- **MNVR-055 / MNVR-036**: a 401 is retried with Basic only when the challenge offers Basic
  and not Digest, so a wrong password or a stray 401 from a Digest recorder no longer sends the
  password in the clear or costs a second login attempt. Hikvision does this per request (its
  ISAPI calls, stills and alertStream) and the session keeps Digest. ONVIF stills do the
  same per request, and a refused ONVIF still raises `NvrAuthFailed` instead of returning
  no image. Dahua `_get` moves that
  recorder's session to Basic after a Basic-only challenge and keeps it there, because
  `snapshot.cgi` and the attach stream use the same session and a Basic-only unit must keep
  serving both. The Dahua archive reader (`dahua_archive._request`) and
  `NativeDahuaDriver.get_clip` use the same rule (`drivers.dahua.moves_to_basic`). Not yet
  changed: the Hikvision archive reader still keeps Basic after a Basic-only challenge.

Recovery:

- **MNVR-004 / U-2**: the recovery and Site Control threads survive any fault; a claimed Site
  Control command is always completed; the `auto` driver resolves for Site Control.
- **MNVR-006 / MNVR-007**: recovery intervals are opened and read by camera UUID, never by
  channel number or a guessed channel "1"; an interval with no resolvable camera does not
  complete as recovered.
- **MNVR-059 / MNVR-032 / MNVR-058 / MNVR-031 / MNVR-061**: archive read failures back off and
  then close the interval with a bounded number of claims; recovered samples stay inside the
  recovery window; one clip download per sample; recovered stills carry their footage time;
  recording segments are no longer replayed as recorder events and recorder URIs stay on site.

Recorded media:

- **MNVR-029 / MNVR-036 / MNVR-063**: the vendor-native archive is used for an ONVIF-live site
  only for cameras with a label-consistent ONVIF-to-native channel map (the equivalence is
  IMPLEMENTED_UNVERIFIED); rejected native logins back off per recorder; acceptance and status
  prove the archive through the transport the runtime uses.
- **MNVR-030 / MNVR-057 / MNVR-060**: footage and still failures are recorded truthfully
  (retryable unless the recorder affirmatively refused); recorder addresses are redacted from
  customer-visible text; still failures always read as still failures; clips are labelled by
  their container.
- **MNVR-031 (Hikvision)**: footage downloads are bounded to the requested window.
- **MNVR-019 / MNVR-034 / MNVR-062 (Dahua)**: archive segment times come back on the agent
  clock; clip downloads have a total time budget; archive search pages through all results.
- **MNVR-035 (clip clock, ONVIF-live Dahua site through the mapped dahua-cgi archive)**: the
  clip claim does not say which clock stamped an event's time, so the Agent chooses by request
  source. An operations/rule clip (no event) uses the agent clock. An incident clip on an event
  uses the recorder clock, as for a live ONVIF event stamped by the recorder, and the agent clock
  only when the recorder clock is more than 5 minutes from every civil offset. That is a best
  guess, not provenance. Until the claim carries the event's `payload.clock_source` (a database
  change, not in 5.0.28) these windows are placed wrongly: a recovered event, or an event
  uploaded by an Agent before 5.0.28, is off by the recorder's drift (up to 5 minutes); an ONVIF
  event stamped with receive time whose recorder clock comes within 5 minutes of another civil
  offset is off by up to 5 minutes. When a claim does carry `clock_source`, the Agent follows it
  and fails a recorder-stamped window it can no longer place instead of guessing (CI-covered
  with fakes; the server side is not built). Dahua-live sites keep the agent clock; the
  Hikvision archive takes no clock argument.
- **U-3**: the shipped run loop honours `spool_max_rows`.

Timed stills:

- **NEW-L2 (periodic stills)**: the Agent produces one timed still per configured camera about
  every 300 s, staggered across the cadence, as the deployed HASCO and Chai Wala Agents already do.
  Each is a `visual_sample` event with `payload` exactly
  `{"sample": true, "source": "periodic_snapshot", "vendor": <vendor>}` and the JPEG inline, the
  contract production's still claim, visual review and restaurant reports consume. A sample with
  no still is never sent. Local settings: `periodic_stills` (default on) and
  `periodic_still_seconds` (default 300, clamped to 60-3600 s). An unreachable or refusing
  recorder is backed off. The still comes from the live driver, so hikvision-isapi, dahua-cgi
  and onvif share the code. Without it an upgrade would remove those sites' only timed stills.
  Not yet seen on any site.

Repair/Upgrade downgrade guard:

- `wl-repair-upgrade.ps1` reads `%ProgramData%\WatchLog\recorders.json` (written only by
  5.1.0+) first, before the candidate runs or the installed Agent is paused. It refuses with
  exit 24 and "This site uses more than one recorder; WatchLog 5.0.28 cannot manage it. Disable
  the extra recorders in Manage Recorders first, or install 5.1.0 or later." when more than one
  recorder is configured (a row without `is_configured` counts as configured, as in 5.1). It
  refuses with exit 25 when the file exists but is not readable JSON, has another schema or a
  malformed row, because the count cannot be proven. A missing file (a 5.0.x site), an empty
  list or one configured recorder is not blocked. Reason: 5.0.28 ignores the registry and runs
  only the legacy recorder, and a multi-recorder site refuses its legacy calls while the
  heartbeat still looks online. Covered by `test_repair_multi_recorder_guard.py` (static checks,
  the reader under PowerShell, and the whole orchestrator on Windows against a sandbox
  ProgramData). It guards Repair/Upgrade only (the full installer sends a complete existing site
  to Repair/Upgrade). A remote update to 5.0.28 does not run this script and is not covered by
  it.

Test and CI hygiene: every test file added for 5.0.28 runs in a CI step
(`test_ci_runs_every_test.py` fails on a test no step runs), and no test writes to the real
`%ProgramData%\WatchLog` (`conftest.py`, `programdata_sandbox.py`,
`test_programdata_isolation_guard.py`).

### What is proven, and what is not

- **CI-covered (fakes, no hardware)**: each change above has regression tests with
  hardware-free fakes, run by the backend job ("Agent 5.0.27 repair gates", "Agent 5.0.28
  live-site gates") and, for ingest and recovery payloads, by the integration job on a
  disposable Postgres. They prove the Agent's logic against the documented protocol shapes.
  Status at this commit: **GitHub Actions has not run any job for this head**, because the
  account's Actions billing is failing, so no CI result exists for it. The backend and
  integration jobs were reproduced locally on 2026-10-06 (`wl-ci-local.py`, integration on a
  disposable local Postgres 16). Every Agent step passes there, including "Agent 5.0.27 repair
  gates", "Agent 5.0.28 live-site gates" and "Automatic outage recovery", and every integration
  step passes. The one backend step that fails, "Reports preview truth contract", fails on
  `main` too: an archived Chai Wala report (2026-10-01) contains a banned phrase. A local
  reproduction is not GitHub CI: it ran on Windows rather than ubuntu-latest and skips the
  package-install steps. Nothing in this section is CI-proven until the jobs pass on GitHub.
- **IMPLEMENTED_UNVERIFIED on hardware** (no field evidence yet): everything a recorder decides.
  DS-7608NI-Q1 (HASCO, Chai Wala): keep-alive cadence within 90 s, `dateTime` with or without an
  offset, the timezone stated in `/ISAPI/System/time`, playbackURI windowing, channel-less
  alerts, targetType strings, Digest-only behaviour. DH-XVR1B08-I (Al-Khalid): ONVIF PullPoint
  delivery, the Source token it sends, Initialized/StorageFailure messages, granted
  TerminationTime, GetProfiles order, whether its ONVIF UtcTime and CGI clock agree,
  `mediaFileFind` wall times, loadfile trimming, DHAV/H.265 decode, recorder clock drift,
  lockout thresholds, whether any unit offers Basic only. Coverage on all three sites stays
  UNVERIFIED for events and evidence until the gates below pass; the heartbeat is their only
  LIVE signal today.
- **Behaviour changes to watch in the field**:
  - Repair/Upgrade now needs real event-stream activity (`recorder_seen_at`): a site whose
    alertStream, attach or pull point is refused (for example a user without notification
    rights) will no longer commit an upgrade. Check this first on each site.
  - ONVIF `device_ts` follows the recorder's UtcTime while it is within 300 s of the PC; a
    recorder clock further off gives receive time plus `clock_skew_s`.
  - ONVIF `Initialized` states at subscribe time are no longer emitted as new occurrences.
  - On an ONVIF-live Dahua site whose recorder clock drifts, incident clips on recovered or
    pre-5.0.28 events can miss by that drift (see MNVR-035 above). Read
    `event_stream.last_clock_skew_s` and the XVR clock before judging a clip that missed.
  - A recorder that answers a 401 offering Digest but accepts only Basic, or a 401 with no
    `WWW-Authenticate` challenge, would now fail login instead of being retried with Basic
    (not expected; not field-checked).
  - A Dahua-derived unit that offers Basic only keeps working for probe, stills and the attach
    stream (the session stays on Basic after the first Basic-only challenge; CI-covered with a
    fake). A Digest-capable Dahua unit that ever answers with a Basic-only challenge would stay
    on Basic until the Agent reopens the driver (not expected; not field-checked).

### Known limits of 5.0.28

- **Event-stream liveness is local only.** The per-recorder `event_stream` block is written to
  the local runtime-health proof (`Secrets\runtime-health.json`) and read by Repair/Upgrade; the
  cloud heartbeat does not carry it in 5.0.28. The cloud `nvr_health` "reachable" still comes
  from the recorder probe, so the portal can show a recorder reachable while no events arrive,
  as at Al-Khalid (recorder reachable and authenticated on 2026-10-04, no event since
  2026-09-26).
- **Chai Wala could get two sets of timed stills.** 5.0.28 advertises `config_snapshot_requests`.
  With the 0156 hotfix applied (2026-10-06) the restaurant scheduler can issue requests, so a
  5.0.28 Agent at Chai Wala would receive both the restaurant scheduler's requested stills and
  its own periodic stills for the same cameras. The owner decided on 2026-10-06 that the server
  owns scheduled restaurant stills; 5.1.1 implements that (section 0.5) and 5.0.28 does not.
- **Multi-recorder sites are refused**, not managed: see the Repair/Upgrade downgrade guard
  above (exit 24 or 25).

### First field site

Al-Khalid is the recommended first 5.0.28 site. Its recorder is reachable and authenticated,
but it has produced no events or stills since 2026-09-26 on the ONVIF transport, which is the
defect class 5.0.28 fixes. HASCO's recorder has been unreachable since 2026-09-30 11:25Z (its
last successful contact; the first failed probe was at 11:31Z), so HASCO cannot be the first
acceptance site until that is resolved on site. Chai Wala goes last: the source of its 5.0.17
Agent is unknown and its stills feed live restaurant reports.

### Per-site field acceptance gates (in this order)

Promotion needs one exact Windows artifact built from the merged source, recorded here with its
merge SHA, workflow run, artifact ID and executable hashes. On every site, in addition to the
site gates below: `visual_sample` rows with `payload.source='periodic_snapshot'` appear from the
5.0.28 Agent, one per configured camera about every 300 s. Then:

1. **Al-Khalid first** (Dahua DH-XVR1B08-I, persisted live driver `onvif`). First retrieve that
   install's `setup.log` to learn why `onvif` was persisted, and read `--version` / BUILD_SHA.
   FIELD-AKSS-001 covers dahua-cgi at Agent 0.4.1 only and does not carry over to ONVIF. Then
   the 1A gates 1-10, plus: events attributed to the right camera (walk-tests on at least two
   cameras); `event_stream.dropped_unmapped` stays 0 or the logged Source token is recorded;
   `event_stream.last_clock_skew_s` read and the XVR clock checked; ONVIF stills for a camera
   other than 1; an incident clip through the mapped dahua-cgi archive whose window contains
   the event; recovery of a controlled gap with RECOVERED provenance.
2. **HASCO second** (Hikvision DS-7608NI-Q1). Before installing, confirm the recorder is
   reachable again (unreachable since 2026-09-30 11:25Z; the Agent itself has been offline since
   2026-10-03) and read the installed `--version` / BUILD_SHA (the exact 5.0.26 source deployed
   there is unknown). Then the 1A gates 1-10, plus: `event_stream.connected` true with
   `last_frame_at` advancing on a quiet site (keep-alives); a stream drop reopened within
   seconds; a controlled restart gap opening exactly one recovery interval with camera UUIDs; a
   channel-less or disk alert stored with no camera; one bounded incident clip whose window
   contains the event.
3. **Chai Wala last**: it runs 5.0.17 from Build 75 (Watchlog-2 field branch; Agent code equal
   to Build 69). Capture its support bundle and `--version` / BUILD_SHA before planning any
   upgrade; Site Control is enabled there while its executor is unknown. The timed-still source
   is now decided (server-owned, see Known limits); 5.0.28 does not implement it and would
   duplicate Chai Wala's stills, 5.1.1 does.

Any failed gate keeps 5.0.28 unpromoted and preserves rollback to the installed version.

---

## 1C. 5.1.0 multi-recorder Agent candidate (`mr/agent-5.1.0`)

5.1.0 is the **multi-recorder** Agent and installer: one WatchLog site, one Agent authority,
many recorders. It is a **candidate, not promoted**: no Windows artifact has been built from
it, no Windows workflow run, artifact ID or executable hash exists for it, nothing is promoted
to any update channel, and it is installed on no site. Build 69 / 5.0.17 stays the field-proven
baseline; 5.0.27 and 5.0.28 stay unpromoted. 5.1.0 contains every 5.0.28 live-site fix
(section 1B, merged from `fix/agent-5.0.28`), applied inside the per-recorder runtime.

### Deployment order (hard prerequisite)

5.1.0 requires **database contract v4** from `mr/db-contracts`: migrations
`0146`-`0155` (`0146_multi_recorder_foundation` through `0155_multi_recorder_reporting_coverage`), deployed and verified first.
`0144` and `0145` are reserved for the production-only portal migrations already applied
(portal QA truth contracts, camera preview performance) and are not part of this set. Without
contract v4 the recorder RPCs it calls (`wl_multi_recorder_agent_contract`,
`wl_sync_recorders`, the recorder health, recovery, evidence and job RPCs) do not exist. A
one-recorder site against a database without the recorder contract falls back to the 5.0.x
recorder-less RPCs (no recorder identity); a site with two or more recorders fails closed and
does not monitor until the database is upgraded. Status 2026-10-06: contract v4 is applied in
production (owner-verified, section 0.7); no Agent deployment or customer configuration change
is implied by that.

### What 5.1.0 adds

- **Multi-recorder per site.** Each configured recorder runs as its own `RecorderContext`
  (local id, cloud recorder id, display name, driver, address, credential reference, cameras,
  health, capabilities) with its own collector, event spool, health worker and durable health
  store, recovery worker, footage worker and periodic-still producer. One recorder that is
  unreachable, slow, refusing its login or held for an unreadable credential does not stop
  another recorder's events, health, recovery, evidence or stills.
- **Manage Recorders.** Add, rename, disable and re-add recorders after install without a
  reinstall. The immutable continuity recorder (the site's original recorder, owner of the
  legacy singleton spool, health and dedupe namespace) cannot be disabled by these flows;
  disabling another recorder keeps its history, and re-adding the same physical recorder does
  not create duplicate active cameras.
- **Per-recorder credentials.** Each recorder's login is its own DPAPI blob under
  `ProgramData\WatchLog\Secrets`; it never falls back to another recorder's or the legacy
  singleton credential. A repaired login is picked up by that recorder's workers without a
  restart.
- **Recorder-scoped events.** Every spooled event carries its `recorder_id`; the server dedupe
  key is namespaced by recorder (the continuity recorder keeps the historical channel
  namespace), so recorder A channel 1 and recorder B channel 1 never collide. Recorder-scoped
  events (disk, alarm input) carry a JSON null channel. Periodic stills (5.0.28 NEW-L2) run per
  recorder with the same production payload contract plus `recorder_id`, each recorder sampling
  by its own Monitor/Ignore choices.
- **Recorder-scoped health.** Camera/NVR health, recording/storage transitions and checkpoints
  are reported through the recorder RPCs; each recorder's liveness is its own event stream
  (Hikvision keep-alives, Dahua heartbeats, ONVIF answered pulls), never a probe. The local
  protected runtime-health proof lists every recorder with `live`, `last_live_at` and its own
  redacted `event_stream` state; `recorder_seen_at` advances only when the full configured set
  is live. Each recorder keeps its own `last_live` outage clock.
- **Recorder-scoped recovery.** Outages open and claim recovery intervals per recorder
  (`wl_open_recorder_recovery_interval`, `wl_agent_claim_recorder_recovery`,
  `wl_complete_recorder_recovery`) over that recorder's explicitly synced channels, never a
  guessed channel; the archive is opened only for a claimed interval, and an unopenable archive
  hands the claim back as pending on the same recorder RPC.
- **Recorder-scoped evidence.** Incident clips and stills, archive jobs and Site Control
  commands resolve exactly one recorder from the claimed row and run on that recorder's
  config, with that recorder's address redacted from any customer-visible reason; clip clock
  selection follows 5.0.28 (MNVR-035).
- **Multi-recorder first install.** Setup can add further recorders on the camera step before
  Connect; each gets its own registry row, credential and camera choices, and Setup binds all
  of them to WatchLog before the Agent starts. The installer child process carries the
  multi-recorder wiring. The Repair/Upgrade gate as shipped in 5.1.0 was unreachable (its
  5.0.28 guard refused every multi-recorder site); 5.1.1 makes it per recorder (section 1D).

### What is proven, and what is not

- **CI-covered (fakes, no hardware)**: the backend job's multi-recorder steps (identity,
  enrollment, registry, runtime, fan-out, first install, failure isolation, recorder push,
  Site Control routing) and the 5.0.28 live-site gates, including two-recorder tests with
  overlapping channel numbers. The Setup disable/reinstall Postgres e2e steps need contract v4
  and run only in a tree that contains `0146`-`0155`. Status at this commit: the backend job
  run locally on 2026-10-05 passes every step except "Public website claims", which needs PHP
  (not installed on that PC); GitHub CI has not run it.
- **IMPLEMENTED_UNVERIFIED on hardware**: everything a recorder decides, as in 1B, now for two
  or more recorders at once. No multi-recorder site exists; nothing about mixed vendors is
  inferred from vendor or model. Coverage stays UNVERIFIED for every recorder until the field
  acceptance below passes.

### Field acceptance (docs/architecture/MULTI_RECORDER_CONTRACT.md section 20)

"Multi-recorder supported" requires a physical test, not CI alone. Minimum topology: one
WatchLog site, two physical recorders, overlapping channel numbers, preferably mixed vendors.
Prove, on one exact recorded artifact:

1. both recorders discovered/configured;
2. credentials remain independent and local;
3. cameras remain distinct despite overlapping channels;
4. events map to the correct camera/recorder;
5. one recorder outage does not stop the other;
6. camera/recorder health remains truthful;
7. recovery uses the correct recorder;
8. snapshots/evidence use the correct recorder;
9. bounded incident clip comes from the correct recorder;
10. reboot preserves both recorder contexts;
11. Repair/Upgrade preserves both or rolls back;
12. adding another recorder works without reinstall;
13. disabling/removing one recorder preserves history;
14. portal groups/root-causes the fault correctly;
15. report coverage remains truthful.

Until these pass in the field, 5.1.0 is implementation, not field proof, and stays unpromoted.
The 1B per-site gates still apply to every single-recorder site upgraded to 5.1.0.

### Deliberate downgrade to 5.0.x

A 5.1.0 site may go back to 5.0.x only when its recorder registry has exactly one configured
recorder (the continuity recorder). With more than one, disable the extra recorders in Manage
Recorders first: otherwise database contract v4 refuses the 5.0.x legacy calls with `42501`
while the 5.0.x heartbeat still looks online. Procedure, health-ledger behaviour and checks:
`docs/runbooks/WINDOWS_EXISTING_SITE_REPAIR_UPGRADE.md`, section
"Deliberate downgrade from 5.1.0 to 5.0.x". `wl-repair-upgrade.ps1` refuses a multi-recorder
registry (exit 24/25) only when its candidate is older than 5.1.0; a 5.0.x package that does not
carry that script version does not check, so the one-recorder check stays manual for it.

---

## 1D. 5.1.1 installer / upgrade lifecycle (`release/5.1.1`, candidate, not promoted)

Status: CI VERIFIED (local reproduction; executing tests with fakes, sandbox ProgramData and real
Windows PowerShell, listed per row in the lifecycle fault matrix). **Not promoted, no artifact,
not installed anywhere.** Steps that need an elevated PC (registering SYSTEM tasks, powercfg on
the real scheme, NSIS compile in the release job) run only in the Windows CI jobs, which have not
run for 5.1.1 (NOT STARTED). This section replaces any older claim in this file that a rollback
"verified" the previous Agent by the task state alone. Release status, credentials, stills and
signing: section 0.

### Repair/Upgrade (`wl-repair-upgrade.ps1`, `nsis/wl-upgrade.ps1`)

- **Multi-recorder guard is version-aware.** The refusal of a site with more than one configured
  recorder (exit 24) or an unreadable `recorders.json` (exit 25) applies only when the candidate
  is older than 5.1.0 (a 5.0.x Repair package). A 5.1.x candidate manages multi-recorder sites;
  an unreadable registry is refused by its own registry validation before anything is paused
  (exit 30). 5.1.0 shipped the 5.0.28 guard unchanged and refused every two-recorder site.
- **Per-recorder proof.** Before the pause, Repair reads what the running Agent itself last
  proved (protected `Secrets\runtime-health.json`: heartbeat within 15 minutes, each recorder's
  row). A recorder is required to be live after the update when it was live before (that proof,
  or the candidate's probe after the pause). A recorder already offline before may stay offline:
  it is reported as "still offline (it was offline before the update; not verified by this
  update)", counted in `not_live_after`, never reported healthy. This now also applies to a
  one-recorder site, which 5.1.0 could not repair while its recorder was offline. Without recent
  proof from the old Agent the continuity recorder stays required. A recorder live before and
  unreachable by the candidate rolls back before any file is replaced.
- **Fresh, not future.** `heartbeat_at`, `remote_update_poll_at`, `recorder_seen_at` and each
  recorder's `last_live_at` must be at or after the phase start and at most 120 s in the future.
- **Rollback proves the previous Agent.** Order: restore the previous payload with the task
  suspended (`rollback-restore`), remove a recorder registry staged by this run, then start the
  task and require a fresh runtime-health heartbeat from the restored version
  (`rollback-start -ProveHealth`, up to 240 s). Rollback success is reported only with that
  proof. Helper exit codes: 0 proven; 15 task running, no fresh heartbeat; 14 task could not be
  started; 16 a payload file could not be restored (the task is re-enabled anyway and the backup
  kept); 17 no proof possible because the site was not heartbeating before (full installer on a
  partial site only). The full installer's "restart was verified" message is shown only on 0.
- **Interrupted upgrade.** Repair's preflight arms a SYSTEM task "WatchLog Agent Upgrade
  Recovery" (at startup and every 5 minutes) and writes `ProgramData\WatchLog\upgrade-in-progress.json`
  (owner PID and start time). If the orchestrator is killed or the PC loses power while WatchLog
  is paused, the task restores the previous payload, removes a staged registry (when the
  protected candidate Setup UI is still there), re-enables the task and requires the heartbeat;
  bounded to 3 attempts; it writes `repair-upgrade-result.ini` with stage "interrupted upgrade
  recovery". It runs a copy of the helper in the install folder (administrators only). Commit
  and every completed rollback remove it. `run-agent.ps1` cannot do this: a Disabled task never
  starts it. The full installer does not arm it (connected sites are sent to Repair/Upgrade).
- **Stale remote update.** While paused, Repair removes a staged remote update and the
  `.remote.bak` image, and closes an unconfirmed remote-update request as "superseded by
  Repair/Upgrade" so the new launcher can neither apply an old package nor revert the repair.
- **Uninstaller.** Repair/Upgrade now writes the current `uninstall.exe` (same Uninstall section
  as the full installer, compared by a test) and `ComponentsVersion` after success.

### Remote update success (`remote_update.py`, `apply-remote-update.ps1`, `run-agent.ps1`)

- Staging records the old Agent's recorder baseline (`remote-update\baseline.json`) and a
  commit window (default 900 s, clamped 300-1800 s). The swap records `applied_at`,
  `commit_deadline`, `previous_version` and the SHA-256 of the rollback image.
- The new Agent commits only when it runs the applied version (and the released build when the
  manifest names `build_sha`; runtime-health now carries `build_sha`), a cloud heartbeat and an
  update poll exist after the swap (not future-dated), and every recorder live before is live
  again. Commit deletes `.remote.bak` at once, so a later cloud-report failure can never revert a
  proven update; reports are retried for 7 days, then dropped.
- Not proven by the deadline: the Agent marks `rollback_requested` and exits; the launcher
  restores the image only if its hash matches the recorded one. It also rolls back on an exit
  within 60 s or an exit after the deadline while uncommitted. The restored Agent reports the
  rollback only after its own fresh heartbeat and update poll (or, after 10 minutes, as "NOT
  proven running").
- Limit: an Agent that hangs without exiting and whose worker thread is dead is not rolled
  back by the launcher (it is blocked on the Agent); the result stays uncommitted and reported.
- **Site Status "Update WatchLog"** no longer swaps the installed Agent from inside itself
  (that ran `wl-upgrade.ps1 preflight`, which stops the very process and window running it and
  left the task Disabled). It reports what is available and directs to the portal (remote
  update) or to `WatchLog-Repair-Upgrade.exe`.

### Agent-only vs full-component updates

Every in-app path replaces `watchlog-agent.exe` only. The signed manifest's channel entry now
carries `update_class` (`AGENT_ONLY_COMPATIBLE` | `REQUIRES_REPAIR_PACKAGE`) and
`min_installed_components`, produced by `tools/release_update_contract.py`. The Agent refuses an
Agent-only update (`requires_repair_package`) when the release requires the Repair package, when
the field is absent across a major/minor change, for an unknown class, or when the installed
component set (ARP `ComponentsVersion`, else `DisplayVersion`, 32-bit registry view) is older
than `min_installed_components` (default: the release's own `major.minor.0`) or unreadable.
For a `REQUIRES_REPAIR_PACKAGE` release the tool also emits `min_agent_version` = the release's
own version: Agents older than 5.1.1 ignore `update_class` but honour `min_agent_version`, so they
refuse it ("agent_too_old") instead of installing it Agent-only.

Concrete **5.0.26 -> 5.1.x** incompatibilities of an Agent-only update (installer/update audit C):

1. the 5.0.26 `register-service.ps1` deletes `background-ready.json` and fails after 25 s unless
   the Agent rewrites it; the 5.1 Agent never writes it, so the old Setup UI's reconnect/setup
   fails (and the old rollback ignores that);
2. once a recorder registry exists, the 5.1 Agent runs from per-recorder credentials while the
   5.0.26 Setup UI still writes only the legacy credential: a credential fix is silently ignored;
3. no Manage Recorders (it comes only with the 5.1 Setup UI from the full or Repair package);
4. the 5.0.26 uninstaller does not remove `recorders.json`, `Secrets\recorders` or the 5.1
   shortcuts;
5. 5.1 adds `recorder_id` to spool payloads and `health.sqlite` columns; 5.0.x reading them
   after a rollback is unverified;
6. 5.1 needs database contract v4; the updater has no server-capability gate;
7. the 5.1.1 security and lifecycle fixes (remote-update stage-trust gate, commit gate,
   proven rollback, recovery task, uninstall policy, power baseline) live in the scripts and the
   uninstaller, which an Agent-only update leaves at 5.0.26.

So **5.0.26 -> 5.1.1 is `REQUIRES_REPAIR_PACKAGE`**, and so is **5.1.0 -> 5.1.1**: 5.1.1 changes
`apply-remote-update.ps1`, `register-service.ps1`, `run-agent.ps1` and `wl-upgrade.ps1`. Every
fielded site (5.0.17, 5.0.26) therefore reaches 5.1.1 only through
`WatchLog-Repair-Upgrade.exe` (`docs/runbooks/WINDOWS_EXISTING_SITE_REPAIR_UPGRADE.md`). That is
also the only way the 5.1.1 remote-update stage-trust fix reaches the fielded 5.0.24-5.0.26
Agents, which stay exposed until then.

**How the contract reaches Agents today (no edge-function change needed).** The deployed manifest
builder (edge function `watchlog-update-manifest`, read 2026-10-06) reads
`watchlog-update-payload.json` from the `watchlog-production` release and signs `version`,
`url`, `sha256`, `size`, `notes`, `generated_at` and `min_agent_version`; it drops any other
field. `min_agent_version` is therefore the signed contract: for a `REQUIRES_REPAIR_PACKAGE`
release it is the release's own version. Every fielded Agent (5.0.17, 5.0.26: `updater.py`
163-165) refuses such a release (`agent_too_old`), and a 5.1.1+ Agent reads the same marker as
`requires_repair_package` and says the Repair/Upgrade package is needed. An
`AGENT_ONLY_COMPATIBLE` release keeps the bootstrap floor (`min_remote_update_version`,
5.0.24). `update_class`, `min_installed_components` and `build_sha` are written into the payload
as well, for when the builder passes them through (an owner-approved edge deployment; not
required for safety).

The Windows Release workflow writes `WatchLog-Update-Contract.json` and
`watchlog-update-payload.json` as build artifacts (inputs `previous_version` and
`previous_ref`; empty = `REQUIRES_REPAIR_PACKAGE`). It uploads nothing to the update channel.
Publishing the payload is a separate owner-approved step. Never republish through the field
line's workflow (`a3266326`): it uploads on every production build with `min_agent_version`
fixed at 5.0.24, so a 5.1.x payload published that way would be installed Agent-only by 5.0.26
sites.

The tool classifies `REQUIRES_REPAIR_PACKAGE` automatically when, since the previous release's
source commit, any of these changed: a script installed beside the Agent; a Setup UI source
(`SETUP_UI_SOURCES`); the declared recorder registry schema, credential store format or
per-recorder camera-choices schema (`CONTRACT_CONSTANTS`). It does the same when the previous
release cannot be compared.

### Machine changes and uninstall

- **Power (option B).** `register-service.ps1` keeps the site PC awake on mains power
  (standby, hibernate and disk timeouts AC = 0; `powercfg /hibernate off`). Before the first
  change it records the previous AC values, the active scheme GUID and `HibernateEnabled` in
  `ProgramData\WatchLog\Secrets\power-baseline.json`; Repair, rollback and re-runs never
  overwrite it. Uninstall (`wl-upgrade.ps1 -Stage uninstall`) restores exactly those values
  (`/setacvalueindex`, `/setactive` when that scheme is active, `/hibernate on` if it was on).
  DC (battery), lid, sleep button, Modern Standby, NIC and USB power settings are not changed.
  A site first installed before 5.1.1 was changed without a record: its first baseline holds
  WatchLog's own values, so uninstall changes nothing there (the originals are unknown).
- **Uninstall policy.** WatchLog has no "keep this site for a reinstall" choice, so uninstall
  removes every identity, credential, queue, cache and staging file it created and keeps only
  support logs (`agent.log`, `agent.log.old`, `upgrade.log`, `repair-upgrade.log`,
  `repair-upgrade-result.ini`, `setup.log`). Removed in addition to the 5.1.0 list: install
  folder `watchlog.defaults.ini`, `watchlog-agent.exe.remote.bak`, `watchlog-agent.next.verify`,
  `wl-upgrade-recover.ps1`; ProgramData `upgrade-backup\`, `remote-update\`, `repair-candidate\`,
  `analytics_*`, `recorder_identity.json`, `camera_profiles.json`, `recorder_auth_backoff.json`,
  `recorders.json.quarantine-*`, `recorders.quarantine-*`, `upgrade-in-progress.json`; scheduled
  tasks "WatchLog Agent", "WatchLog Agent Upgrade Recovery" and any orphaned "WatchLog Candidate
  Preflight *". The uninstall stage no longer makes a 100 MB payload backup. If PowerShell
  cannot run, the NSIS Uninstall section (identical in `watchlog.nsi` and `watchlog-repair.nsi`)
  deletes the same files by name as a fallback, including the site stamp, another site's
  set-aside files and the rejected-row file (`4db87674`).
- **Start menu** shortcuts are created for all users; uninstall removes both the all-users and
  the installing admin's per-user copies.
- **Uninstall registry view (documented, not changed).** NSIS is 32-bit, so the ARP key is under
  `HKLM\Software\WOW6432Node\...\Uninstall\WatchLog`. Moving it to the 64-bit view would orphan
  every existing site's entry (Repair/Upgrade reads `InstallLocation` from it); the Agent reads
  `ComponentsVersion` from the 32-bit view.

---

## 2. Field-proven baseline — Build 69

> Sections 2-12 are the record of the 5.0.x lineage. They stay valid as history and as
> do-not-regress rules; for 5.1.1, section 0 and section 1D govern.

**Build 69 / product version 5.0.17 is the current field-proven discovery/connectivity baseline.**

Exact identity:

- source SHA:
  `811d378e3a7556047f294bb128b8caf45a295469`
- Windows Release run:
  **#69**
- run id:
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

### Why Build 69 matters

Build 69 has real field evidence of successful recorder discovery/connectivity on a live site.

Its discovery behavior covered up to eight local /24 networks and included the common
Hikvision/Dahua/RTSP/web ports.

Build 69 therefore remains the **golden regression baseline** until a later exact artifact
passes physical Hikvision and Dahua acceptance.

Do not replace this baseline with a newer build merely because CI or packaging is green.

---

## 3. Build 74 field failure

Build 74 is the counterexample to “green build = reliable installer”.

Real field behavior:

- setup remained on `Search Network`;
- no recorder IP was surfaced;
- the installer could keep loading instead of recovering cleanly.

The discovery implementation could scan several local /24s across physical and
virtual/VPN adapters with no product-level deadline in the setup UI.

This failure created a permanent release rule:

> **Discovery/connectivity is a field acceptance gate, not only a packaging gate.**

Any successor must preserve Build 69's useful network reach while preventing Build 74's
spinner-forever failure class.

---

## 4. Discovery/connectivity hardening now in authoritative 5.0.24

The authoritative main source now implements:

- up to **8 local /24s**, preserving Build 69 reach;
- physical Ethernet/Wi-Fi before virtual/VPN/Hyper-V/Docker/VMware/WSL/Tailscale/WireGuard adapters;
- bounded discovery;
- **32-second backend discovery budget**;
- **40-second setup-UI discovery watchdog**;
- **256 bounded workers**, instead of Build 69's much larger socket burst;
- fast ports:
  - Dahua `37777`
  - Hikvision `8000`
  - HTTP `80`
  - HTTPS `443`
- deeper compatibility ports including:
  - RTSP `554`
  - Dahua `37778`
  - `8080`, `8443`, `81`, `82`, `88`, `8081`, `8888`
  - `34567`
- preservation of multiple recorder candidates across ranked LANs;
- manual IP usable while automatic discovery is still running;
- “Use this IP” safely invalidates the old discovery worker generation;
- same-endpoint recorder auth fallback before wasting time on alternate endpoints;
- targeted web-port rescue when a native recorder port is found;
- recorder rediscovery by stored device identity after DHCP/IP changes.

Recorder login timing remains aligned with the proven field behavior:

- **5 seconds per recorder probe**
- **18 seconds backend login deadline**
- **30 seconds UI login watchdog**

---

## 5. Release-line validation candidate — Build 83

The discovery/setup hardening was validated in the Windows release-line repository:

`visioninfinity0007-droid/Watchlog-2`

Branch:

`build/site-connector-v5-watchlog2`

Exact Build 83 identity:

- Build:
  **83**
- product version:
  **5.0.21**
- source SHA:
  `dfc3ec5bc1229a88c510f8057cd9ac898f8cf848`
- Windows Release run id:
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

Build 83 passed:

- Windows packaging;
- packaged setup UI self-test;
- multi-NIC discovery simulation;
- manual-IP escape behavior;
- discovery watchdog behavior;
- recorder discovery regression tests;
- recorder login/vendor routing tests;
- eighth-subnet regression;
- ProductVersion/runtime checks;
- checksum verification.

### Important boundary

Build 83 is a **validation candidate**, not the final authoritative installer.

It is version **5.0.21** and does not represent the full authoritative 5.0.24
remote-maintenance/runtime source.

Do not call Build 83 the final production installer.

---

## 6. Authoritative 5.0.25 installer / upgrade status

The authoritative `Alkalid-security/Watchlog` main source is version **5.0.25**.

5.0.25 preserves the 5.0.23 archive/gap-recovery work and the 5.0.24 staged bootstrap, and adds field repair hardening: clean failure exit, exact-stage diagnostics, and repair-only lock/rollback scope. It retains the permanent
**existing-site Repair/Upgrade bootstrap**.

### Two Windows paths are now mandatory

**`WatchLog-Setup.exe` — new site / new PC only**

Use for first installation, recorder discovery/login, camera setup and enrollment.
A fully enrolled existing site must be redirected away from this path before any
WatchLog process is stopped or installed file is replaced.

**`WatchLog-Repair-Upgrade.exe` — existing enrolled sites**

Use for normal upgrades, repair/bootstrap from older versions and the one-time
transition onto permanent signed online updates.

The Repair/Upgrade path intentionally excludes the Qt discovery/setup UI. It must
not re-run Search Network for a healthy enrolled site.

### 5.0.25 staged safety contract

Phase 1 — passive candidate validation while the old WatchLog remains running:

- stage the candidate outside the live install;
- run the candidate as Windows SYSTEM;
- point it explicitly at the existing `watchlog.ini`;
- read the machine-bound DPAPI recorder credential without migration/mutation;
- prove candidate runtime dependencies load;
- prove existing Agent identity/state can be read;
- authenticate through the dedicated read-only `wl_agent_preflight_auth` RPC;
- require valid signed remote-update configuration;
- failure here leaves the installed WatchLog untouched.

Phase 2 — recorder validation before replacement:

- only after passive preflight succeeds, safely suspend/stop the old runtime;
- back up the existing payload;
- run the staged candidate against the actual saved recorder credential;
- prove recorder authentication/identity and channel enumeration;
- if recorder validation fails, do not install the candidate and restore/restart
  the prior runtime.

Phase 3 — atomic replacement and health commit:

- replace only after both staged validations pass;
- verify exact ProductVersion/runtime version;
- re-register/start WatchLog;
- require fresh protected runtime-health proof;
- commit only if the exact new version reports:
  - fresh cloud `heartbeat_at`;
  - fresh `recorder_seen_at`;
  - fresh `remote_update_poll_at` written only after the updater successfully
    reaches the production update-claim RPC;
- a merely-running process is not success;
- any failed health gate triggers full rollback and old-Agent restart verification (until 5.1.1 that "verification" was only the task state Running; 5.1.1 requires a fresh heartbeat from the restored version, section 1D).

### Current Windows validation branch

Repository:

`visioninfinity0007-droid/Watchlog-2`

Branch:

`fix/existing-site-repair-upgrader-v5`

Head at this context update:

`0f3507483ffd7369134fe8b7aa0c6a7be5946ea9`

That validation branch currently contains the prior 5.0.24 Repair/Upgrade port. After this canonical 5.0.25 patch merges, the validation line must be refreshed from canonical source before producing the next Windows artifact. The repair stack includes:

- separate Repair/Upgrade NSIS + PowerShell orchestrator;
- read-only DPAPI staged preflight;
- protected runtime-health proof;
- evidence-based Site Control capability reporting;
- secure remote-update worker;
- transactional `apply-remote-update.ps1`;
- launcher handoff/rollback;
- Ed25519/cryptography packaging;
- update URL/public-key release inputs;
- Windows release gates for the existing-site repair contract;
- full-installer redirect for complete enrolled sites;
- truthful rollback messaging.

### Current blocker

**No 5.0.25 Windows Repair/Upgrade artifact is promoted yet.**

The next step is to complete and run the exact Windows Release on the validation
branch, record the artifact IDs/hashes, then perform controlled Al-Khalid field
acceptance.

Until that exact artifact passes field acceptance, Build 69 remains the
discovery/connectivity golden baseline and working sites must not be replaced
merely to satisfy a version number.

---

## 7. Existing-site upgrade rule

For a working enrolled site, the default upgrade path is now
**`WatchLog-Repair-Upgrade.exe`**, not the full Setup wizard.

The operator must not be forced through recorder rediscovery or re-entry of NVR
credentials during a normal upgrade.

Required invariants:

1. passive staged validation runs before the old installation is stopped;
2. passive failure changes nothing on the installed site;
3. recorder validation happens before any installed payload is replaced;
4. scheduled-task watchdog is suspended during the replacement transaction;
5. only processes belonging to the current WatchLog install path are stopped;
6. all replace-target files are proven unlocked;
7. the complete old payload is backed up;
8. machine-bound enrollment + DPAPI recorder credentials are preserved;
9. the new exact version must prove cloud heartbeat, recorder contact and real
   remote-update polling before commit;
10. rollback success may be claimed only when the previous Agent restart is proven.

The older full-installer shutdown-before-replace contract remains valid as a
fallback safety layer and was Windows-validated by Build 98, but complete modern
enrolled sites should be redirected to Repair/Upgrade before the full installer
touches them.

### Al-Khalid field incident that drove this design

A recent Al-Khalid Head Office upgrade attempt with the full installer reached
the new-Agent health check, failed to remain healthy and rolled back to the
previous working **5.0.19** Agent.

That field event exposed two product requirements now locked into 5.0.24:

- do not replace the installed Agent before a staged candidate proves compatibility;
- never display “previous working version restored” unless old-Agent restart is
  actually verified.

The restored 5.0.19 Agent does **not** advertise `remote_update_v1` and has not
claimed an online-update request. It therefore still requires one successful
5.0.24 Repair/Upgrade bootstrap before future updates can become remote.

### Release-line proof for the running-file-lock fix

The lower-level shutdown/rollback mechanics were validated by Windows Release
**#98 / run id `36371718065`**:

- source SHA `c653c6a38664491ee51788d0466e7338a1f3da53`
- artifact `WatchLog-Windows-98`
- artifact id `10949248440`
- artifact ZIP digest
  `sha256:c6a29f4b1642c1fab1d546749e6928e432b605ca564395499e8e2e0e5c76f30c`
- `WatchLog-Setup.exe` SHA-256
  `06DFCC486EA15E123BA1E366A68A3DB83C996A6876CFAA0FDCA31BB4AAED2940`

The real Windows regression proved exact-path process shutdown, file unlock,
full-payload backup/restore and fail-closed rollback behavior.

## 8. Secure remote-update direction

The 5.0.25 source contains the permanent remote-maintenance architecture:

- outbound-only Agent polling;
- no inbound Windows management port;
- cloud cannot send arbitrary shell commands;
- cloud cannot choose arbitrary binaries;
- agent fetches its own configured HTTPS signed release manifest;
- Ed25519 manifest verification;
- SHA-256 payload verification;
- package-size verification;
- staged replacement between agent runs;
- previous binary retained for rollback;
- health window before success is finalized;
- early-start failure restores the previous agent;
- runtime capability advertised only after successful remote-update polling.

This solves the long-term requirement that future field upgrades should not require repeated site visits.

The bootstrap limitation remains: an already-installed old binary that does not poll the
remote-update queue cannot be taught that worker purely from the cloud. That is why the
5.0.24 Repair/Upgrade exists: it is the one-time safe bootstrap onto the self-updating runtime.

---

## 9. Mandatory physical field acceptance for the next promoted installer

The first authoritative 5.0.25 (or later) Windows artifact must pass all of the following
before replacing Build 69 as the fleet baseline. For 5.1.1 these checks are carried, with the
5.1.1 additions, by the gate register and stages of
`docs/runbooks/WINDOWS_RECORDER_FIELD_ACCEPTANCE.md`; run them from there.

### Hikvision

- automatic discovery;
- manual-IP fallback;
- correct native login;
- channel inventory;
- background Agent connectivity after setup closes;
- Site Control claim/completion;
- recorder inspection;
- recording/storage/analytics readback;
- one bounded historical clip/archive attempt;
- upgrade/rollback behavior.

### Dahua

- automatic discovery;
- manual-IP fallback;
- native CGI authentication;
- channel inventory;
- background Agent connectivity after setup closes;
- Site Control claim/completion;
- recording/storage/analytics readback;
- one bounded archive retrieval;
- upgrade/rollback behavior.

### Existing Build-69 site

At least one existing live site must be upgraded with **WatchLog-Repair-Upgrade.exe** and prove:

- passive staged preflight passes while the old Agent remains untouched;
- recorder staged preflight passes before file replacement;
- no forced rediscovery;
- existing recorder connectivity preserved;
- Agent returns online;
- heartbeat/event/snapshot path still works;
- Site Control worker polls;
- remote-update worker polls and produces fresh `remote_update_poll_at`;
- `remote_update_v1` appears only after that real poll;
- rollback remains available until the health window passes.

Only after these tests should the new exact artifact replace Build 69 as the field baseline.

---

## 10. Do-not-regress rules

- Never reduce automatic discovery below Build 69's eight-/24 reach without explicit field evidence.
- Never allow automatic discovery to spinner forever.
- Never block manual IP behind an automatic scan.
- Never treat virtual/VPN adapters as higher priority than physical CCTV LANs.
- Never treat CI/package success alone as field discovery proof.
- Never call Build 83, Build 98 or Build 100 the authoritative 5.0.25 installer.
- Never replace a working site with an unaccepted candidate.
- Never use the full discovery/setup wizard as the default upgrade path for a complete enrolled site.
- Never stop the installed Agent before passive staged Repair/Upgrade validation succeeds.
- Never replace installed files before staged recorder validation succeeds.
- Never commit an upgrade because the process is merely alive; require fresh heartbeat + recorder + updater-poll proof.
- Never claim rollback success unless the previous Agent restart is proven.
- Never claim Site Control/remote update from capability strings alone; require live poll proof.
- Never expose recorder passwords or signing secrets.
- Never treat HTTP 200 alone as proof a recorder write succeeded.
- Never remove rollback before the replacement agent has passed its health window.
- Never replace WatchLog files while its launcher, Setup UI or Agent from that install are still running or holding the payload.
- Never broad-kill same-named processes outside the current WatchLog install path during upgrade.

---

## 11. Historical lineage

- **Build 37 / 5.0.0** — historical lineage anchor.
- **Build 39 / 5.0.1** — recorder selection/UI regression successor.
- **Build 41 / 5.0.2** — Step-06/login-watchdog successor.
- **Build 46 / 5.0.3** — installer-child lifecycle/Hikvision integration hardening.
- **Build 49 / 5.0.6** — recorder-backed readiness predecessor.
- **Build 50 / 5.0.7** — Hikvision single-session monitoring predecessor.
- **Build 56 / 5.0.8** — dual-vendor archive/recovery predecessor.
- **Build 61 / 5.0.12** — production recovery + PC-off safety semantics.
- **Build 69 / 5.0.17** — **current field-proven golden baseline**.
- **Build 70 / 5.0.18** — duplicate-login/Step-06 iteration.
- **Build 71 / 5.0.19** — login retry/watchdog iteration.
- **Build 72 / 5.0.20** — deterministic first-pass discovery iteration.
- **Build 74** — **real field discovery failure; do not use as reliability evidence**.
- **Build 76 / 5.0.21** — ONVIF physical-camera + Hikvision footage hardening, but inherited the same core discovery class.
- **Build 77 / 5.0.21** — first bounded-discovery candidate.
- **Build 82 / 5.0.21** — Build-69 eight-subnet parity candidate passed Windows Release.
- **Build 83 / 5.0.21** — discovery/setup validation candidate with exact artifact recorded above.
- **Build 98 / 5.0.21** — running-file-lock/transactional-upgrade validation candidate; real Windows process test and packaged release passed.
- **Build 100 / 5.0.23** — packaged FFmpeg + archive/gap-recovery validation candidate; historical decoder self-test and Windows Release passed.
- **Authoritative source 5.0.25** — Build-69 discovery reliability + dual-vendor archive/gap recovery + staged existing-site Repair/Upgrade + signed online-update bootstrap; exact 5.0.25 Windows artifact/field acceptance still pending.
- **Build 75 / 5.0.17** — Chai Wala's installed build (Watchlog-2 field branch; Agent code equal to Build 69).
- **5.0.26** — installed at HASCO and Al-Khalid (field line); not an ancestor of canonical `main`.
- **5.0.27, 5.0.28, 5.1.0** — canonical candidates (sections 1A-1C); never built as a release, never promoted.
- **5.1.1** — release candidate (section 0, 1D); not promoted, no artifact, production release BLOCKED on code signing.

---

## 12. Mandatory rule for future installer work

Before changing, diagnosing, recommending, or promoting a WatchLog Windows installer:

1. identify the exact build/artifact currently installed;
2. resolve it to source SHA, workflow run, artifact ID and hashes;
3. treat Build 69 as the current field-proven discovery/connectivity baseline;
4. make product-source changes in `Alkalid-security/Watchlog` main or a PR targeting main;
5. use release-line builds only as validation evidence, not as product authority;
6. produce an exact Windows artifact from authoritative source;
7. record artifact ID/digest and executable hashes here;
8. pass packaged setup/discovery/login tests;
9. pass physical Hikvision + Dahua acceptance;
10. only then promote the build to live sites/fleet.

Do not infer authority from repository age, branch recency, build number or a green package workflow alone.
