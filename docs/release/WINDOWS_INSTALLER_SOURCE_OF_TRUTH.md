# WatchLog Windows Installer — Source of Truth

For current live tenant/site/runtime context, also read:

`docs/production/CURRENT_LIVE_CONTEXT_2026-09-28.md`

This document is the release authority for Windows installer work.

---

## 1. Authoritative source

Authoritative repository:

`Alkalid-security/Watchlog`

Authoritative branch:

`main`

Current source line under validation:

**5.0.28**

5.0.28 (section 1B, branch `fix/agent-5.0.28`) is an Agent-only **candidate, not
promoted**: no Windows artifact has been built from it and none is installed on any
site. It stays unpromoted until its exact merge SHA, Windows workflow run, artifact
IDs/hashes and physical HASCO, then Al-Khalid acceptance are recorded below. 5.0.27
(section 1A) was never promoted either. Build 69 / 5.0.17 remains the field-proven
discovery/connectivity baseline until that happens.

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
  ISAPI calls, stills and alertStream) and the session keeps Digest. Dahua `_get` moves that
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

Test and CI hygiene: every test file added for 5.0.28 runs in a CI step
(`test_ci_runs_every_test.py` fails on a test no step runs), and no test writes to the real
`%ProgramData%\WatchLog` (`conftest.py`, `programdata_sandbox.py`,
`test_programdata_isolation_guard.py`).

### What is proven, and what is not

- **CI-covered (fakes, no hardware)**: each change above has regression tests with
  hardware-free fakes, run by the backend job ("Agent 5.0.27 repair gates", "Agent 5.0.28
  live-site gates") and, for ingest and recovery payloads, by the integration job on a
  disposable Postgres. They prove the Agent's logic against the documented protocol shapes.
  Status at this commit (the backend and integration jobs run locally on 2026-10-05, again after
  the review fixes; GitHub CI has not run it): the integration job passes, including the
  recorder-scoped ingest and recovery-payload e2e steps. The backend job fails three Agent steps
  on tests from the merged recovery, Hikvision-media and Dahua-media work that disagree with each
  other: "Agent 5.0.27 repair gates" (13 tests in `test_dahua_archive_paging`,
  `test_dahua_archive_timezone`, `test_hikvision_archive_recovery_status`,
  `test_recovery_dahua_archive_times`, `test_recovery_ai`), "Agent 5.0.28
  live-site gates" (6 in `test_recovery_hikvision_terminal`; its ONVIF, incident, event-stream,
  Hikvision and Dahua lines pass) and "Automatic outage recovery" (1 in `test_recovery_ai`).
  Nothing in this section is CI-proven until those pass on GitHub.
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

### Per-site field acceptance gates (in this order)

Promotion needs one exact Windows artifact built from the merged source, recorded here with its
merge SHA, workflow run, artifact ID and executable hashes, then:

1. **HASCO first** (Hikvision DS-7608NI-Q1). Before installing, confirm the recorder is reachable
   again (the audit recorded it unreachable since 2026-09-26) and read the installed
   `--version` / BUILD_SHA (the exact 5.0.26 source deployed there is unknown). Then the 1A
   gates 1-10, plus: `event_stream.connected` true with `last_frame_at` advancing on a quiet
   site (keep-alives); a stream drop reopened within seconds; a controlled restart gap opening
   exactly one recovery interval with camera UUIDs; a channel-less or disk alert stored with no
   camera; one bounded incident clip whose window contains the event.
2. **Al-Khalid second** (Dahua DH-XVR1B08-I, persisted live driver `onvif`). First retrieve that
   install's `setup.log` to learn why `onvif` was persisted, and read `--version` / BUILD_SHA.
   FIELD-AKSS-001 covers dahua-cgi at Agent 0.4.1 only and does not carry over to ONVIF. Then
   the 1A gates 1-10, plus: events attributed to the right camera (walk-tests on at least two
   cameras); `event_stream.dropped_unmapped` stays 0 or the logged Source token is recorded;
   `event_stream.last_clock_skew_s` read and the XVR clock checked; ONVIF stills for a camera
   other than 1; an incident clip through the mapped dahua-cgi archive whose window contains
   the event; recovery of a controlled gap with RECOVERED provenance.
3. **Chai Wala**: its 5.0.17 (Build 69) code is UNKNOWN (source 811d378 is in neither object
   store). Capture its support bundle and `--version` / BUILD_SHA before planning any upgrade;
   Site Control is enabled there while its executor is unknown.

Any failed gate keeps 5.0.28 unpromoted and preserves rollback to the installed version.

---

## 2. Field-proven baseline — Build 69

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
- any failed health gate triggers full rollback and old-Agent restart verification.

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
before replacing Build 69 as the fleet baseline.

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
