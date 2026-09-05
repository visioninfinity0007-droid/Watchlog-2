# WatchLog Windows + Recorder Field Acceptance

Status: field execution required

This runbook is the authoritative Handoff D procedure for the first real WatchLog Windows/customer-recorder acceptance. It is evidence-driven. Do not mark a recorder family, Windows lifecycle, analytics behavior, or customer readiness as validated without completing the applicable checks below.

## 1. Release under test

Use this exact installer unless a later signed/rebuilt release explicitly supersedes it:

- Source commit: `046aae27e847758e65f012896ed9272654a146c5`
- Windows Release run: `33947339434`
- Artifact: `WatchLog-Windows-1` (`9963855819`)
- Installer: `WatchLog-Setup.exe`
- Installer version: `0.3.1`
- Size: `344,506,558` bytes
- SHA-256: `458524b7af718dc216a5786159c44bd2533a180ee4fb636d3cd458d240781d32`
- Signing: `UNSIGNED`

The public `latest` installer must hash to the value above before testing. If it does not, STOP and reconcile the release before installing.

Expected public path:

`https://watchlogsite.161.97.175.15.sslip.io/downloads/watchlog/latest/WatchLog-Setup.exe`

Because this release is unsigned, Windows SmartScreen may warn. Record the warning as expected release-quality evidence; do not treat the warning itself as an installer functional failure.

## 2. Exact Windows implementation facts

Current `main` installs to:

- Program files: `C:\Program Files\WatchLog`
- Local data: `C:\ProgramData\WatchLog`
- Protected recorder credential: `C:\ProgramData\WatchLog\nvr_password.dpapi`
- Agent log: `C:\ProgramData\WatchLog\agent.log`
- Setup log: `C:\ProgramData\WatchLog\setup.log`
- Local state: `C:\ProgramData\WatchLog\agent_state.json`
- Local config: `C:\Program Files\WatchLog\watchlog.ini`
- Windows scheduled task: `WatchLog Agent`
- Task identity: `SYSTEM`
- Task trigger: Windows startup

The installer must not register/start `WatchLog Agent` after an incomplete or failed fresh setup.

Uninstall intentionally removes the protected recorder credential and Windows task, while retaining local logs/state under `C:\ProgramData\WatchLog` for support/reinstall continuity.

## 3. Evidence rules

Create one evidence folder per machine/test session, for example:

`WatchLog-Acceptance-2026-09-05-WIN11-PC01`

Capture:

- Windows edition/build and architecture;
- installer SHA-256 and byte size;
- recorder vendor/model/firmware;
- LAN topology notes;
- screenshots of each major wizard state and portal result;
- task/process evidence;
- timestamps before/after reboot/restart/soak;
- camera count and mapping;
- analytics/report evidence;
- PASS / FAIL / PARTIAL result for every applicable test.

Never capture or paste recorder passwords, site codes, auth tokens, service-role keys, private API keys, the contents of `nvr_password.dpapi`, or unredacted logs that may contain credentials.

## 4. Stage A — release identity and clean-machine baseline

Run in an elevated PowerShell on the test machine after downloading the public installer:

```powershell
$Installer = "$env:USERPROFILE\Downloads\WatchLog-Setup.exe"
Get-Item $Installer | Select-Object FullName,Length,LastWriteTime
Get-FileHash $Installer -Algorithm SHA256
Get-ComputerInfo | Select-Object WindowsProductName,WindowsVersion,OsBuildNumber,OsArchitecture
```

PASS requires:

- size = `344506558` bytes;
- SHA-256 = `458524b7af718dc216a5786159c44bd2533a180ee4fb636d3cd458d240781d32`;
- supported 64-bit Windows environment;
- no pre-existing `WatchLog Agent` task or `C:\Program Files\WatchLog` installation for the fresh-install test.

For the fresh-machine baseline:

```powershell
Get-ScheduledTask -TaskName "WatchLog Agent" -ErrorAction SilentlyContinue
Test-Path "$env:ProgramFiles\WatchLog"
Test-Path "$env:ProgramData\WatchLog"
```

If an old test installation exists, record it and use the upgrade path instead of calling the machine clean.

## 5. Stage B — fresh graphical install

### B1. Launch / UX

Run `WatchLog-Setup.exe` normally.

PASS requires:

- Windows elevation prompt appears;
- installer opens graphical WatchLog setup;
- no Python, CMD, or PowerShell console remains visible during normal customer setup;
- branding/product name is `WatchLog`;
- installer does not expose implementation jargon such as DPAPI/service-role/Supabase to the customer.

Record SmartScreen behavior separately because the current pilot release is unsigned.

### B2. Site code validation

Before using the valid site code, enter a deliberately invalid/non-current value that cannot belong to another customer.

PASS requires:

- setup does not mark the site connected;
- background task is not registered as a completed installation;
- customer receives a recoverable WatchLog error without secrets/internal stack traces.

Then use the authorized valid site code from the WatchLog customer portal.

Do not save the code in screenshots or notes.

### B3. Recorder discovery

Place the Windows PC on the same LAN/VLAN as the approved CCTV recorder.

Run automatic recorder discovery.

Record:

- whether discovery found the recorder;
- discovered IP/hostname;
- displayed vendor/model where available;
- elapsed discovery time;
- any irrelevant candidates.

PASS if the target recorder is found and can be selected.

If automatic discovery does not find it, this is not automatically a product failure. Continue to manual-IP fallback and classify discovery separately.

### B4. Manual IP fallback

Enter the recorder's known LAN IP manually.

PASS requires the same recorder can proceed to connection testing without requiring port forwarding, inbound Internet access, or a VPN.

### B5. Bad recorder credentials

Use one intentionally incorrect recorder password.

PASS requires:

- login is rejected;
- wizard remains recoverable;
- no background task is started as if setup succeeded;
- no raw driver stack trace, password, or sensitive request detail is shown.

### B6. Correct recorder credentials

Enter the approved recorder credentials.

PASS requires:

- recorder connection verifies;
- displayed vendor/model is plausible;
- discovered camera/channel count matches the recorder closely enough to reconcile channel-by-channel;
- no credential appears in plaintext on disk or in screenshots.

### B7. Camera review and purposes

Review every discovered camera.

Capture a table:

| Recorder channel | Camera name | WatchLog camera | Intended purpose | Correct? |
| --- | --- | --- | --- | --- |
|  |  |  |  |  |

PASS requires no silent channel loss or duplication. Any intentionally disabled/unavailable recorder channel must be noted rather than counted as a WatchLog failure.

### B8. Finalize

Complete setup only after recorder/cameras are correct.

PASS requires the success page to confirm:

- recorder verified;
- WatchLog site linked;
- expected camera count connected;
- recorder credential protected locally.

## 6. Stage C — immediate Windows post-install checks

Run elevated PowerShell:

```powershell
$Install = "$env:ProgramFiles\WatchLog"
$Data = "$env:ProgramData\WatchLog"

Get-ChildItem $Install | Select-Object Name,Length,LastWriteTime
Get-ScheduledTask -TaskName "WatchLog Agent" | Select-Object TaskName,State
Get-ScheduledTaskInfo -TaskName "WatchLog Agent" | Select-Object LastRunTime,LastTaskResult,NextRunTime
(Get-ScheduledTask -TaskName "WatchLog Agent").Principal | Select-Object UserId,LogonType,RunLevel
(Get-ScheduledTask -TaskName "WatchLog Agent").Triggers
Test-Path "$Data\nvr_password.dpapi"
Test-Path "$Data\agent_state.json"
Test-Path "$Data\agent.log"
```

PASS requires:

- `WatchLog Agent` exists;
- task state reaches `Running`;
- principal is `SYSTEM`;
- task has an at-startup trigger;
- protected credential file exists;
- state file exists;
- agent log exists/starts updating;
- `watchlog-agent.exe`, `watchlog-setup-ui.exe`, `run-agent.ps1`, and `register-service.ps1` exist under Program Files.

Check that the config does not contain a plaintext recorder password:

```powershell
$Config = "$env:ProgramFiles\WatchLog\watchlog.ini"
$PlainPasswordKeys = Select-String -Path $Config -Pattern '^\s*nvr_password\s*=' -CaseSensitive:$false
if ($PlainPasswordKeys) { throw "FAIL: plaintext nvr_password key exists in watchlog.ini" }
"PASS: no plaintext nvr_password key"
```

Do not print the rest of `watchlog.ini` into shared evidence because it contains site/runtime identifiers.

## 7. Stage D — portal connection proof

Within five minutes of successful installation, verify in the customer portal:

- Site Health shows the intended site;
- the new WatchLog connection is online/recently contacted;
- camera count matches the installed recorder mapping;
- recorder/vendor/model fields are plausible where surfaced;
- no unrelated tenant/site is affected.

Capture screenshots with site codes/secrets excluded.

PASS requires the portal state to be backed by the real site connection; do not insert fake database rows to make the page green.

## 8. Stage E — reboot persistence

Record pre-reboot time and portal last-contact state, then reboot Windows normally.

After login, wait up to five minutes and run:

```powershell
Get-ScheduledTask -TaskName "WatchLog Agent" | Select-Object TaskName,State
Get-ScheduledTaskInfo -TaskName "WatchLog Agent" | Select-Object LastRunTime,LastTaskResult
Get-Process -Name "watchlog-agent" -ErrorAction SilentlyContinue | Select-Object Id,StartTime,Path
Get-Item "$env:ProgramData\WatchLog\agent.log" | Select-Object Length,LastWriteTime
```

PASS requires:

- task returns to `Running` without a user manually starting WatchLog;
- agent process is present;
- portal returns to online/recent-contact status;
- camera mapping remains intact;
- no new setup/site code is requested after a normal reboot.

## 9. Stage F — child-agent crash/restart recovery

Do this only on the approved test installation, not on a live customer-critical site.

Record the current agent PID, then terminate only `watchlog-agent.exe` (do not delete the scheduled task):

```powershell
Get-Process -Name "watchlog-agent" | Select-Object Id,StartTime
Stop-Process -Name "watchlog-agent" -Force
Start-Sleep -Seconds 35
Get-Process -Name "watchlog-agent" -ErrorAction SilentlyContinue | Select-Object Id,StartTime
Get-ScheduledTask -TaskName "WatchLog Agent" | Select-Object TaskName,State
```

The launcher intentionally restarts an exited child after approximately 15 seconds.

PASS requires a new `watchlog-agent.exe` process to appear without user intervention and the scheduled task to remain healthy. Verify the portal resumes/recently contacts after recovery.

## 10. Stage G — minimum 60-minute soak

Run for at least 60 continuous minutes after the system is healthy.

Capture evidence at approximately 0, 15, 30, 45, and 60+ minutes:

- Windows time;
- `WatchLog Agent` state;
- agent process start time/PID;
- `agent.log` last-write timestamp and size;
- portal connection status/last contact;
- camera count;
- analytics/event counts relevant to the controlled test.

PASS requires:

- no unexplained agent stop;
- no recurrence of the historical few-minute stop/zero-camera-sync failure;
- heartbeats/last contact continue;
- cameras remain synced;
- no runaway restart loop;
- no sustained high CPU/memory condition that makes the site PC unusable.

Record Task Manager CPU/memory observations at least twice during soak.

## 11. Stage H — analytics acceptance

Use representative cameras with known field of view. Configure only analytics that are actually available in the deployed portal/runtime.

For each tested rule record:

| Camera | Purpose | Rule | Controlled action | Expected | Observed | PASS/FAIL |
| --- | --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |  |

At minimum, where relevant, test:

- person/activity detection;
- vehicle/motorcycle behavior;
- line crossing direction;
- dwell/time-in-zone;
- after-hours rule behavior.

For each test, create a controlled physical event with known start/end time and compare WatchLog output to what actually happened.

Do not claim accuracy from one successful event. Record false positives, false negatives, camera angle, lighting, occlusion and crowding observations.

If a rule is unavailable or not suitable for the camera, mark it NOT APPLICABLE rather than faking a PASS.

## 12. Stage I — scheduled report delivery

Configure a real test report recipient through the customer portal using an approved destination.

Choose the nearest safe schedule window supported by the product and allow the actual report service to deliver it.

PASS requires:

- schedule/recipient remains saved;
- reporting remains enabled for the tenant;
- report runner records the delivery attempt/history;
- destination actually receives the message/report;
- report content reflects real tenant/site data and does not use preview/sample numbers;
- no recipient from another tenant receives the report.

Capture the delivery timestamp and portal delivery-history evidence. Redact destination details if required.

## 13. Stage J — same-release upgrade/preservation

With the site already enrolled and healthy, run the same authoritative installer again to exercise the existing-installation path.

Before upgrade record:

- site ID/identity from the portal (not site code);
- camera mapping/count;
- task state;
- `agent_state.json` modification time;
- current portal status.

Run `WatchLog-Setup.exe` again.

PASS requires:

- installation detects/preserves existing enrollment/configuration rather than creating a duplicate site;
- protected credential remains usable;
- task returns to `Running`;
- same site/cameras return online;
- no duplicate cameras/site connection records are created.

Legacy plaintext-password migration is already covered by Windows CI. Do not deliberately put a real customer recorder password into plaintext merely to repeat that test on production hardware.

## 14. Stage K — uninstall and reinstall

Uninstall through Windows Settings > Apps > Installed apps > WatchLog, or the WatchLog Start Menu uninstall shortcut.

After uninstall:

```powershell
Get-ScheduledTask -TaskName "WatchLog Agent" -ErrorAction SilentlyContinue
Test-Path "$env:ProgramFiles\WatchLog"
Test-Path "$env:ProgramData\WatchLog\nvr_password.dpapi"
Test-Path "$env:ProgramData\WatchLog\agent_state.json"
Test-Path "$env:ProgramData\WatchLog\agent.log"
```

PASS requires:

- scheduled task removed;
- Program Files installation removed;
- protected credential removed;
- retained ProgramData state/logs are understood as intentional support/reinstall continuity, not an uninstall failure.

Then reinstall the same verified installer and complete setup again.

PASS requires the reinstall to reach the same healthy site/camera/heartbeat state without duplicate tenant/site data.

A full local data wipe is a separate destructive support action and is not part of ordinary uninstall acceptance.

## 15. Windows 10 / Windows 11 matrix

The strongest release gate is:

| Test | Windows 11 | Windows 10 |
| --- | --- | --- |
| Exact installer SHA |  |  |
| Fresh graphical install |  |  |
| No persistent console |  |  |
| Site-code rejection/acceptance |  |  |
| Auto recorder discovery |  |  |
| Manual IP fallback |  |  |
| Bad/good credentials |  |  |
| Camera discovery/mapping |  |  |
| Protected credential/no plaintext |  |  |
| Task runs as SYSTEM |  |  |
| Portal online/heartbeat |  |  |
| Reboot persistence |  |  |
| Child-process restart recovery |  |  |
| 60+ minute soak |  |  |
| Uninstall/reinstall |  |  |
| Analytics pilot |  |  |
| Scheduled report delivery |  |  |

If Windows 10 hardware/VM is not immediately available, do not block the first controlled Win11 pilot solely on that fact; mark Win10 as PENDING and keep broad Windows compatibility claims constrained until tested.

## 16. Recorder compatibility result

For every recorder tested, produce:

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
| Snapshot support observed | |
| Event support observed | |
| Reboot/soak result | |
| Analytics result | |
| Classification | VALIDATED / PARTIAL / UNSUPPORTED / MORE EVIDENCE |

A successful protocol connection on one model is not evidence for an entire vendor family.

## 17. Stop conditions

STOP and open a defect before proceeding to a customer pilot if any of these occur:

- installer SHA differs from the approved release;
- setup claims success with an invalid site code or failed recorder connection;
- background task is created after failed/incomplete setup;
- recorder password is stored in plaintext;
- credentials/tokens are printed in logs/UI;
- task does not run as SYSTEM or survive reboot;
- cameras silently disappear/duplicate;
- tenant/site data crosses account boundaries;
- agent repeatedly dies/restarts during soak;
- a scheduled report is delivered to the wrong tenant/destination;
- install/upgrade creates duplicate site identity;
- recorder behavior requires weakening production security/RLS to make it pass.

## 18. Final acceptance report

Return one concise report containing:

1. test date/operator;
2. exact installer SHA/size;
3. Windows machines/builds;
4. recorder hardware matrix;
5. lifecycle PASS/FAIL/PARTIAL matrix;
6. screenshots/evidence filenames;
7. reboot/recovery results;
8. 60+ minute soak timeline;
9. analytics observations and known limitations;
10. scheduled-report delivery evidence;
11. security/credential-storage result;
12. defects opened;
13. compatibility classification;
14. recommendation: `PILOT GO`, `PILOT GO WITH LIMITATIONS`, or `NO-GO`.

Do not mark overall WatchLog staging green solely from a successful Handoff D if GitHub main protection (Issue #21) or P0 Drive credential containment/rotation (Issue #23) remain unresolved.
