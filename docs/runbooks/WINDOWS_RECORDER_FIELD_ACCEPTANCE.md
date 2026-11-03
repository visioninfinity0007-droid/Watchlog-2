# WatchLog Windows + Recorder Field Acceptance

Status (2026-10-06): **NOT STARTED for 5.1.1.** No 5.1.1 installer has been built by the Windows
Release workflow, none is signed, and none is installed at any site.

This runbook is the procedure for proving a WatchLog Windows release on a real site PC and a real
recorder. It is evidence-driven. Do not mark a recorder family, the Windows lifecycle, analytics
behaviour or customer readiness as validated without completing the applicable checks below.

What this runbook is not:

- the release status, components and update contract:
  `docs/release/WINDOWS_INSTALLER_SOURCE_OF_TRUTH.md` (section 0);
- the step-by-step Repair/Upgrade of an enrolled site:
  `docs/runbooks/WINDOWS_EXISTING_SITE_REPAIR_UPGRADE.md`;
- the footage/archive and gap/recovery checklists and their status:
  `docs/acceptance/ARCHIVE_GAP_RECOVERY_STATUS.md`.

Status words: NOT STARTED, FIXED LOCALLY, CI VERIFIED, INSTALLER VERIFIED, FIELD VERIFIED,
PRODUCTION VERIFIED, BLOCKED (defined in the source of truth, section 0). A gate becomes FIELD
VERIFIED only from evidence captured under this runbook on the exact artifact under test.

## 1. Release under test

Test exactly one Windows Release artifact, recorded in
`docs/release/WINDOWS_INSTALLER_SOURCE_OF_TRUTH.md` section 0.1 with its source SHA, workflow run,
artifact ID and the SHA-256 of `WatchLog-Setup.exe`, `WatchLog-Repair-Upgrade.exe`,
`watchlog-agent.exe` and `watchlog-setup-ui.exe`. For 5.1.1 that record does not exist yet
(NOT STARTED). A locally compiled package is not an artifact under test.

Signing: a production release needs a code-signing certificate and none exists, so a production
release is BLOCKED (source of truth section 0.6). An unsigned build is test-only:

- it may be used on a test PC to reach INSTALLER VERIFIED;
- installing it at a customer site needs the owner's explicit approval for that site and does
  not make it a production release;
- record the SmartScreen warning as expected for an unsigned build, not as an installer failure.

If the installer on the test machine does not hash to the recorded value, STOP and reconcile the
release before installing.

## 2. Windows facts the tests rely on (5.1.1 code)

- Program files: `C:\Program Files\WatchLog` (`watchlog-agent.exe`, `watchlog-setup-ui.exe`,
  `run-agent.ps1`, `register-service.ps1`, `apply-remote-update.ps1`, `wl-upgrade.ps1`,
  `watchlog.ini`, `uninstall.exe`).
- Local data: `C:\ProgramData\WatchLog`.
- Credentials: DPAPI LocalMachine blobs in `C:\ProgramData\WatchLog\Secrets`:
  `agent_key.dpapi`, `nvr_credential.dpapi` (continuity recorder, legacy copy) and one login per
  recorder in `Secrets\recorders\<local_id>.dpapi`. The folder is owned by Administrators and
  only SYSTEM and Administrators have access. No recorder password is stored in plain text:
  `watchlog.ini` has no `nvr_password`, and `watchlog.env` (0.3.3) and `nvr_password.dpapi`
  (0.2-0.3.2) are legacy files that Setup migrates and removes.
- Recorder registry: `C:\ProgramData\WatchLog\recorders.json`, stamped with this site.
- Logs: `agent.log`, `setup.log`, `upgrade.log`, `repair-upgrade.log`,
  `repair-upgrade-result.ini` in `C:\ProgramData\WatchLog`.
- Local identity: `C:\ProgramData\WatchLog\agent_state.json` (no secret in it).
- Scheduled task `WatchLog Agent`: SYSTEM, at start-up (30 s delay) plus a 5-minute watchdog
  trigger. The launcher restarts an exited Agent after about 15 s.
- Start menu: WatchLog Setup, WatchLog Site Status, WatchLog Manage Recorders, Uninstall
  WatchLog.
- Configuration happens only in WatchLog Setup. `watchlog-agent.exe --setup` is refused (exit 2,
  nothing written).
- The installer does not register or start `WatchLog Agent` after an incomplete or failed fresh
  setup, and the Ready page appears only after the background Agent has proved the recorder.
- Uninstall keeps only the support logs listed above; everything else WatchLog created is
  removed, including identity, credentials, recorder registry, queues and tasks, and the power
  settings recorded at install are restored (Stage N).

## 3. Evidence rules

Create one evidence folder per machine and test session, for example
`WatchLog-Acceptance-2026-10-20-WIN11-PC01`.

Capture:

- Windows edition, build and architecture;
- installer SHA-256, byte size and Authenticode status;
- recorder vendor, model and firmware;
- LAN topology notes;
- screenshots of each major Setup state and portal result;
- task and process evidence;
- timestamps before and after reboot, restart and soak;
- camera count and mapping;
- event, still and analytics evidence;
- PASS / FAIL / PARTIAL for every applicable test.

Never capture or paste recorder passwords, site codes, auth tokens, service-role keys, private
API keys, any `.dpapi` file or other file from the `Secrets` folder (the one exception is
`Secrets\runtime-health.json`, the Agent's health proof, which holds no secret and may be read
in an elevated shell), or unredacted logs that may contain credentials.

## 4. Field gate register (5.1.1)

Every gate is NOT STARTED. Update this table only with evidence from this runbook.

| # | Gate | Where | Status |
|---|---|---|---|
| G1 | Fresh install A-Z on Windows 11 | Stages A-I, N | NOT STARTED |
| G2 | Fresh install A-Z on Windows 10 | Stages A-I, N | NOT STARTED |
| G3 | DPAPI machine binding on two physical PCs | Stage L | NOT STARTED |
| G4 | Hikvision single-recorder site | Stages B-H with the Hikvision checks | NOT STARTED |
| G5 | Repair/Upgrade from 5.0.26 | Stage J | NOT STARTED |
| G6 | Two-recorder site | Stage K | NOT STARTED |
| G7 | Native alarm event chain | Stage H | NOT STARTED |
| G8 | Footage/archive physical proofs (12 points) | Stage M | NOT STARTED |
| G9 | Gap/recovery physical proofs (15 points) | Stage M | NOT STARTED |
| G10 | MacVisen | none until defined | OWNER FIELD CLARIFICATION REQUIRED: define MacVisen |

Known site facts (2026-10-06): Chai Wala runs 5.0.17 (Hikvision, Build 75), HASCO and Al-Khalid
run 5.0.26. At Chai Wala, 4 `visual_sample` / `periodic_snapshot` events after the database
migration prove only legacy periodic ingestion compatibility; native alarms, clip/archive,
recovery and recording truth there are not proven.

## 5. Stage A: release identity and clean-machine baseline

Run in an elevated PowerShell on the test machine:

```powershell
$Installer = "$env:USERPROFILE\Downloads\WatchLog-Setup.exe"
Get-Item $Installer | Select-Object FullName,Length,LastWriteTime
Get-FileHash $Installer -Algorithm SHA256
Get-AuthenticodeSignature $Installer | Select-Object Status,SignerCertificate
Get-ComputerInfo | Select-Object WindowsProductName,WindowsVersion,OsBuildNumber,OsArchitecture
```

PASS requires:

- size and SHA-256 equal the recorded artifact;
- signature status recorded (an unsigned build is a test build, section 1);
- supported 64-bit Windows;
- for a fresh-install test, no `WatchLog Agent` task and no `C:\Program Files\WatchLog`.

```powershell
Get-ScheduledTask -TaskName "WatchLog Agent" -ErrorAction SilentlyContinue
Test-Path "$env:ProgramFiles\WatchLog"
Test-Path "$env:ProgramData\WatchLog"
```

If an earlier installation exists, record it and do not call the machine clean. An enrolled
site is upgraded with Repair/Upgrade (Stage J), not with `WatchLog-Setup.exe`.

## 6. Stage B: fresh graphical install

### B1. Launch and presentation

Run `WatchLog-Setup.exe` normally.

PASS requires:

- the Windows elevation prompt appears;
- WatchLog Setup opens as a graphical window;
- no Python, CMD or PowerShell console stays visible;
- the product name is `WatchLog`;
- nothing shown to the customer names internal components or technologies (for example DPAPI,
  service roles, the database or cloud provider).

### B2. Site code

Enter a deliberately invalid value first. PASS requires: the site is not marked connected, no
background task is registered as a completed install, and the customer sees a recoverable
WatchLog message without secrets or stack traces. Then use the valid site code from the WatchLog
portal. Do not save the code in screenshots or notes.

### B3. Recorder discovery

Put the PC on the same LAN/VLAN as the recorder and run automatic discovery. Record whether the
recorder was found, its IP, the displayed vendor/model, elapsed time and any irrelevant
candidates. Discovery must finish or hand over to manual entry within about 40 s; it must never
spin indefinitely (Build 74 failure class). Not finding the recorder is classified separately and
is not by itself a product failure.

### B4. Manual IP

Enter the recorder's LAN IP. PASS requires the recorder to proceed to the connection test without
port forwarding, inbound Internet access or a VPN, while automatic discovery may still be running.

### B5. Wrong recorder password

PASS requires: login rejected, Setup recoverable, no background task started as if setup
succeeded, no stack trace, password or request detail shown.

### B6. Correct recorder password

PASS requires: the recorder verifies, the vendor/model is plausible, the channel count can be
reconciled channel by channel, and no credential appears in plain text on disk or on screen.

### B7. Cameras and purposes

| Recorder channel | Camera name | WatchLog camera | Intended purpose | Correct? |
| --- | --- | --- | --- | --- |
|  |  |  |  |  |

PASS requires no silent channel loss or duplication. A disabled or empty recorder channel is noted
and is not a WatchLog camera.

### B8. Finish

PASS requires the Ready page ("WatchLog is ready") to appear only after the background Agent has
proved the recorder, and to confirm the recorder, the site link and the connected camera count.
If a required check fails, Setup must say what needs attention instead of claiming ready.

## 7. Stage C: Windows checks right after install

Run elevated:

```powershell
$Install = "$env:ProgramFiles\WatchLog"
$Data = "$env:ProgramData\WatchLog"

Get-ChildItem $Install | Select-Object Name,Length,LastWriteTime
Get-ScheduledTask -TaskName "WatchLog Agent" | Select-Object TaskName,State
Get-ScheduledTaskInfo -TaskName "WatchLog Agent" | Select-Object LastRunTime,LastTaskResult,NextRunTime
(Get-ScheduledTask -TaskName "WatchLog Agent").Principal | Select-Object UserId,LogonType,RunLevel
(Get-ScheduledTask -TaskName "WatchLog Agent").Triggers
Test-Path "$Data\Secrets\agent_key.dpapi"
Test-Path "$Data\Secrets\nvr_credential.dpapi"
Test-Path "$Data\recorders.json"
Test-Path "$Data\agent_state.json"
Test-Path "$Data\agent.log"
Test-Path "$Data\watchlog.env"
(Get-Acl "$Data\Secrets").Owner
(Get-Acl "$Data\Secrets").Access | Select-Object IdentityReference,FileSystemRights,IsInherited
Get-ItemProperty 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\WatchLog' |
  Select-Object DisplayVersion,ComponentsVersion,InstallLocation
& "$Install\watchlog-agent.exe" --version
```

These commands print names, owners and access rules only, never secret contents.

PASS requires:

- `WatchLog Agent` exists, reaches `Running`, runs as `SYSTEM`, and has a start-up trigger and the
  5-minute repeating trigger;
- `agent_key.dpapi`, `nvr_credential.dpapi`, `recorders.json` and `agent_state.json` exist;
  `watchlog.env` does not;
- `Secrets` is owned by Administrators, grants access only to SYSTEM and Administrators and does
  not inherit;
- `agent.log` exists and keeps updating;
- the installed files listed in section 2 exist;
- `DisplayVersion`, `ComponentsVersion` and the first line of `--version` equal the release
  version; the `build_sha` line equals the recorded source SHA.

Check that the config has no plain-text recorder password:

```powershell
$Config = "$env:ProgramFiles\WatchLog\watchlog.ini"
$PlainPasswordKeys = Select-String -Path $Config -Pattern '^\s*nvr_password\s*=' -CaseSensitive:$false
if ($PlainPasswordKeys) { throw "FAIL: plaintext nvr_password key exists in watchlog.ini" }
"PASS: no plaintext nvr_password key"
```

Do not print the rest of `watchlog.ini` into shared evidence: it holds site and runtime
identifiers.

Confirm the console wizard is refused:

```powershell
& "$env:ProgramFiles\WatchLog\watchlog-agent.exe" --setup; $LASTEXITCODE
```

PASS requires exit code 2, a message pointing to WatchLog Setup, and `watchlog.ini` unchanged.

## 8. Stage D: portal connection

Within five minutes of a successful install, verify in the WatchLog portal:

- Site Health shows the intended site;
- the connection is online / recently contacted;
- the camera count matches the recorder mapping;
- recorder vendor/model are plausible where shown;
- no other tenant or site is affected.

PASS requires the portal state to come from the real site connection. Never insert database rows
to make a page green. Exclude site codes and secrets from screenshots.

## 9. Stage E: reboot persistence

Record the time and the portal last-contact state, then reboot normally. After login, wait up to
five minutes:

```powershell
Get-ScheduledTask -TaskName "WatchLog Agent" | Select-Object TaskName,State
Get-ScheduledTaskInfo -TaskName "WatchLog Agent" | Select-Object LastRunTime,LastTaskResult
Get-Process -Name "watchlog-agent" -ErrorAction SilentlyContinue | Select-Object Id,StartTime,Path
Get-Item "$env:ProgramData\WatchLog\agent.log" | Select-Object Length,LastWriteTime
```

PASS requires: the task returns to `Running` without anyone starting it, the Agent process is
present, the portal returns to online, the camera mapping is intact, and no site code is asked
for again.

## 10. Stage F: Agent restart recovery

Only on an approved test installation, never on a live customer-critical site.

```powershell
Get-Process -Name "watchlog-agent" | Select-Object Id,StartTime
Stop-Process -Name "watchlog-agent" -Force
Start-Sleep -Seconds 35
Get-Process -Name "watchlog-agent" -ErrorAction SilentlyContinue | Select-Object Id,StartTime
Get-ScheduledTask -TaskName "WatchLog Agent" | Select-Object TaskName,State
```

PASS requires a new `watchlog-agent.exe` process without intervention (the launcher restarts it
after about 15 s), the task staying healthy and the portal resuming contact.

## 11. Stage G: soak (at least 60 minutes)

Capture at about 0, 15, 30, 45 and 60+ minutes: Windows time, task state, Agent PID and start
time, `agent.log` last write and size, portal status and last contact, camera count, and event and
still counts for the controlled test.

PASS requires: no unexplained Agent stop, no runaway restart loop, heartbeats continue, cameras
stay synced, and no sustained CPU or memory load that makes the PC unusable (record Task Manager
twice).

Stills during the soak:

- On a camera the server schedules (restaurant schedule), each still must come from a server
  request; the Agent must not add its own periodic still of that camera. Two stills per interval
  for one camera is a FAIL.
- On a camera the server does not schedule, the Agent's own periodic stills continue as before; a
  camera that had no capture must not gain any.
- A still failure must not stop events or the heartbeat.

## 12. Stage H: native alarm event chain and analytics

### H1. Native alarm event chain (gate G7)

For each recorder, trigger a controlled recorder-native alarm (motion or the recorder's own
analytics, on a camera where it is enabled) at a known time and prove the chain end to end:

1. the recorder raises the alarm (recorder's own log or screen);
2. the Agent receives it on the live stream (`agent.log`; the recorder's `event_stream` block
   in `Secrets\runtime-health.json`);
3. the event reaches WatchLog with the right camera and recorder and a plausible time;
4. it appears in the portal for the right site only;
5. a recorder-level alarm (disk, alarm input) is stored without a camera.

Use `docs/runbooks/NVR_NATIVE_AI_INCIDENT_FOOTAGE_ACCEPTANCE.md` for the recorder-native analytics
and incident still checks. Zero native events on a site is a finding, not a PASS.

Hikvision checks (gate G4), on a Hikvision recorder:

- the alert stream runs in 30 s slices (`watchlog-agent.exe --version` reports
  `hikvision_stream_slice_seconds=30`) and keeps `event_stream.connected` true with
  `last_frame_at` advancing on a quiet site;
- a stream that goes silent is reported as a stream error and reopened, not left quiet;
- stills keep arriving while the stream runs, and a still failure does not end monitoring;
- other recorder calls (health, stills, footage) do not cause timeouts on the live stream.

### H2. WatchLog analytics

Configure only analytics that the deployed portal and runtime offer. For each tested rule:

| Camera | Purpose | Rule | Controlled action | Expected | Observed | PASS/FAIL |
| --- | --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |  |

Where relevant: person/activity detection, vehicle/motorcycle behaviour, line-crossing direction,
dwell/time in zone, after-hours rules. Create each event physically at a known time and compare.
Do not claim accuracy from one event; record false positives and negatives, angle, light,
occlusion and crowding. Mark an unavailable or unsuitable rule NOT APPLICABLE.

## 13. Stage I: scheduled report delivery

Configure a real test recipient through the portal with an approved destination, choose the
nearest safe schedule window, and let the report service deliver it.

PASS requires: the schedule and recipient stay saved, reporting stays enabled, the delivery
attempt is recorded, the destination receives it, its content reflects real site data (never
preview or sample numbers), and no other tenant's recipient receives it. Capture the delivery time
and portal history; redact destinations if required.

## 14. Stage J: existing site, Repair/Upgrade (gate G5)

1. On the enrolled, healthy site, run `WatchLog-Setup.exe`. PASS requires it to refuse with the
   message to use `WatchLog-Repair-Upgrade.exe`, before stopping anything or replacing any file.
2. Upgrade with `WatchLog-Repair-Upgrade.exe`, following
   `docs/runbooks/WINDOWS_EXISTING_SITE_REPAIR_UPGRADE.md` (preconditions, run, success criteria,
   failure handling). For gate G5 the starting version is 5.0.26.

Record before and after: site identity from the portal (not the site code), camera mapping and
count, task state, `agent_state.json` modification time, portal status,
`repair-upgrade-result.ini`.

PASS requires, in addition to the runbook's success criteria: the same site and cameras, no
duplicate site, camera or connection records, no recorder rediscovery, no password re-entry, and
`ComponentsVersion` equal to the new version.

A 5.0.26 Agent must not take 5.1.1 as a remote (Agent-only) update: with the release published as
`REQUIRES_REPAIR_PACKAGE` it refuses it (`agent_too_old`). Seeing such an update applied is a STOP
condition.

## 15. Stage K: two-recorder site (gate G6)

Minimum topology: one WatchLog site, two physical recorders with overlapping channel numbers,
preferably mixed vendors. Run the fifteen proofs listed in
`docs/release/WINDOWS_INSTALLER_SOURCE_OF_TRUTH.md` section 1C ("Field acceptance") on the exact
artifact, using Manage Recorders to add, rename, disable and re-add a recorder. Record each proof
PASS / FAIL / PARTIAL here; do not copy the list.

## 16. Stage L: DPAPI machine binding (gate G3)

Use two distinct physical PCs and the procedure in `docs/security/MACHINE_BINDING.md` ("Genuine
proof — distinct hardware"). PASS requires a blob from PC 1 to decrypt on PC 1 in every elevated
context WatchLog uses, and to fail on PC 2. Hosted CI runners cannot prove this.

## 17. Stage M: footage/archive and gap/recovery (gates G8, G9)

Run the 12 footage/archive proofs and the 15 gap/recovery proofs in
`docs/acceptance/ARCHIVE_GAP_RECOVERY_STATUS.md`, per vendor, on the exact artifact. Record
recording state, storage state, archive availability and gap/recovery as separate results;
Unknown stays Unknown.

## 18. Stage N: uninstall and reinstall

Uninstall through Settings > Apps > Installed apps > WatchLog, or the Uninstall WatchLog
shortcut. Then:

```powershell
$Data = "$env:ProgramData\WatchLog"
Get-ScheduledTask -TaskName "WatchLog*" -ErrorAction SilentlyContinue
Test-Path "$env:ProgramFiles\WatchLog"
Get-ChildItem $Data -Force -ErrorAction SilentlyContinue | Select-Object Name
Get-ChildItem "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\WatchLog" -ErrorAction SilentlyContinue
powercfg /query SCHEME_CURRENT SUB_SLEEP
```

PASS requires:

- no WatchLog scheduled task remains ("WatchLog Agent", "WatchLog Agent Upgrade Recovery", any
  "WatchLog Candidate Preflight *");
- `C:\Program Files\WatchLog` is gone;
- `C:\ProgramData\WatchLog` holds only support logs (`agent.log`, `agent.log.old`,
  `upgrade.log`, `repair-upgrade.log`, `repair-upgrade-result.ini`, `setup.log`); no `Secrets`,
  `recorders.json`, `agent_state.json` or queue files;
- the Start menu folder is gone;
- the AC sleep/hibernate/disk settings are back to the values recorded before the install (on a
  PC first installed before 5.1.1 the originals are unknown and nothing is restored).

Then reinstall with the same verified installer. A reinstall is a new setup (site code, recorder
login) because the identity and credentials were removed. PASS requires the same healthy site,
cameras and heartbeat without duplicate tenant or site data.

## 19. Windows 10 / Windows 11 matrix

| Test | Windows 11 | Windows 10 |
| --- | --- | --- |
| Exact installer SHA and signature status |  |  |
| Fresh graphical install |  |  |
| No persistent console |  |  |
| Site code rejected / accepted |  |  |
| Automatic discovery |  |  |
| Manual IP |  |  |
| Wrong / correct recorder password |  |  |
| Camera mapping |  |  |
| Protected credentials, no plain text, Secrets ACL |  |  |
| Console wizard refused |  |  |
| Task runs as SYSTEM |  |  |
| Portal online / heartbeat |  |  |
| Reboot persistence |  |  |
| Agent restart recovery |  |  |
| 60+ minute soak, stills ownership |  |  |
| Native alarm event chain |  |  |
| Uninstall / reinstall, power restored |  |  |
| Analytics |  |  |
| Scheduled report delivery |  |  |

If Windows 10 is not available, a first controlled Windows 11 pilot is not blocked by that alone:
mark Windows 10 NOT STARTED and do not claim Windows 10 compatibility.

## 20. Recorder compatibility result

| Field | Evidence |
| --- | --- |
| Vendor | |
| Model | |
| Firmware | |
| Recorder IP/ports (redact if needed) | |
| Discovery method | Auto / Manual |
| Driver/protocol selected | |
| Total recorder channels | |
| WatchLog cameras synced | |
| Stills observed | |
| Native events observed | |
| Footage/archive result | |
| Reboot/soak result | |
| Analytics result | |
| Classification | VALIDATED / PARTIAL / UNSUPPORTED / MORE EVIDENCE |

A protocol connection on one model is not evidence for a vendor family.

## 21. Stop conditions

STOP and open a defect before any customer pilot if:

- the installer SHA differs from the recorded artifact;
- Setup claims success with an invalid site code or a failed recorder connection;
- a background task exists after a failed or incomplete setup;
- a recorder password is stored in plain text, or `Secrets` grants access beyond SYSTEM and
  Administrators;
- `watchlog-agent.exe --setup` asks for or writes anything;
- credentials or tokens appear in logs or UI;
- the task does not run as SYSTEM or does not survive reboot;
- cameras silently disappear or duplicate;
- data crosses tenant or site boundaries (including another site's queued data uploading here);
- the Agent repeatedly dies or restarts during the soak;
- a camera gets duplicate stills (server request plus the Agent's own);
- a scheduled report reaches the wrong tenant or destination;
- install or upgrade creates a duplicate site identity;
- a fielded 5.0.x Agent installs a `REQUIRES_REPAIR_PACKAGE` release as a remote update;
- passing requires weakening production security or RLS.

## 22. Final acceptance report

Return one concise report:

1. test date and operator;
2. exact artifact: source SHA, workflow run, installer SHA-256, size, signature status;
3. Windows machines and builds;
4. recorder hardware matrix;
5. gate register (section 4) with PASS / FAIL / PARTIAL per gate;
6. evidence file names;
7. reboot and recovery results;
8. soak timeline;
9. native event, analytics and stills observations, with known limits;
10. footage/archive and gap/recovery results (separate states);
11. scheduled report delivery evidence;
12. security and credential-storage result;
13. defects opened;
14. compatibility classification;
15. recommendation: `PILOT GO`, `PILOT GO WITH LIMITATIONS` or `NO-GO`.

A successful run of this runbook does not by itself make the overall release green while code
signing is BLOCKED or other release gates in the source of truth, section 0.3, are open.
