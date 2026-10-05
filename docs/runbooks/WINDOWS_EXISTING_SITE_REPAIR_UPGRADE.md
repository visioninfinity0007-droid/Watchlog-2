# WatchLog Windows Existing-Site Repair/Upgrade Runbook

Use this runbook only for an already-enrolled WatchLog site.

For a new site/new PC, use `WatchLog-Setup.exe`.

## Before starting

Confirm:
- site is already enrolled;
- current Agent is heartbeating;
- current recorder identity is known;
- encrypted DPAPI Agent key exists;
- encrypted DPAPI recorder credential exists;
- exact candidate Repair/Upgrade artifact SHA-256 matches the release ledger;
- the candidate release is configured with the production signed update manifest URL/public key.

Do not continue if the exact artifact has not passed the release gates recorded in
`docs/release/WINDOWS_INSTALLER_SOURCE_OF_TRUTH.md`.

## Run

1. Launch `WatchLog-Repair-Upgrade.exe` elevated.
2. Do not manually kill the old Agent.
3. Phase 1 must validate the staged candidate as SYSTEM while the current Agent remains running.
4. If Phase 1 fails, stop. The installed WatchLog should be unchanged.
5. Phase 2 may then pause the current WatchLog and validate the candidate against the saved recorder.
6. If Phase 2 fails, confirm the old Agent is restored/running before leaving the site.
7. Only after Phase 2 passes may the payload be replaced.
8. Wait for final health commit.

## Success criteria

Do not accept “installer finished” or “process is running” as success.

Require:
- exact new version;
- fresh cloud heartbeat;
- fresh recorder observation;
- fresh remote-update poll;
- `remote_update_v1` visible only after that poll;
- camera/channel inventory unchanged/correct;
- no forced recorder rediscovery;
- no re-entry of recorder password;
- one safe read-only Site Control command succeeds.

## Failure handling

If the candidate fails before replacement:
- no rollback should be needed because the old install was not changed.

If failure happens after old-runtime pause but before replacement:
- restore/start the old runtime and prove it is running.

If failure happens after replacement:
- restore the full old payload;
- restore/re-register the prior task;
- prove old Agent restart;
- do not show/record rollback success unless that proof exists.

If old Agent restart cannot be proven:
- preserve logs;
- do not uninstall WatchLog;
- reboot once if required by the support procedure;
- investigate before any new install attempt.

## Deliberate downgrade from 5.1.0 to 5.0.x

A deliberate downgrade installs a 5.0.x build (for example the 5.0.28 Repair/Upgrade) over a
site running the 5.1.0 multi-recorder Agent. It is not the automatic rollback above, which
only ever restores the payload the same Repair/Upgrade run replaced.

**Allowed only when the recorder registry has exactly one configured recorder.** 5.0.x has
no recorder registry: it runs the legacy singleton recorder from `watchlog.ini` with the
legacy `nvr_credential.dpapi`, which 5.1.0 keeps as the continuity recorder's own settings.

Before starting:

1. Open Manage Recorders and count the enabled recorders. The only one that may remain is
   the site's original (continuity) recorder; Manage Recorders never offers to disable it.
2. If more than one recorder is enabled, disable every extra recorder in Manage Recorders
   first and wait for each change to complete. Disabling tells WatchLog first, uploads that
   recorder's queued activity, keeps its history, and only then changes this PC.
   Do not downgrade while more than one recorder is configured: with database contract v4
   the 5.0.x legacy calls (camera sync, health, events, recovery) are refused with SQLSTATE
   `42501` ("legacy recorder path is ambiguous for multi-recorder site") while the 5.0.x
   heartbeat keeps the Agent looking online. The site would look monitored and report
   nothing.
3. Confirm the legacy recorder login is readable. 5.1.0 keeps running when
   `nvr_credential.dpapi` is unreadable (each recorder uses its own login), but 5.0.x exits
   at start without it. Run Repair first if the 5.1.0 log shows "the legacy recorder
   credential on this PC could not be read".

What carries over, and what does not:

- **Health ledger keys behave as they do.** In the shared `health.sqlite`, 5.1.0 keys the
  continuity recorder's last known states as `<recorder_id>:<layer>:<entity>` and leaves the
  5.0.x `<layer>:<entity>` keys as they were at the last cutover. 5.0.x compares against
  those older keys, so its first health cycle reports a transition for every camera or
  recorder whose state changed while 5.1.0 ran; it may repeat a state WatchLog already
  holds. Transitions 5.0.x records carry no recorder id. If 5.1.0 is installed again it
  attributes them to the continuity recorder and takes the newer 5.0.x state for every
  entity 5.0.x moved.
- **Disabled recorders stay on this PC.** `recorders.json`, their credentials and any
  retained queue under `ProgramData\WatchLog\recorders\` are not used by 5.0.x and are not
  deleted. They upload only if 5.1.0 is reinstalled and the recorder is re-enabled.

After the downgrade, accept it with the same success criteria as an upgrade, and check the
Agent log for `42501`: a heartbeat alone does not prove events and health are arriving.

The 5.0.28 Repair/Upgrade will carry a guard that refuses a recorder registry with more than
one configured recorder; that guard is being added on the 5.0.28 branch separately. Until a
release carrying it is recorded in `docs/release/WINDOWS_INSTALLER_SOURCE_OF_TRUTH.md`, the
one-recorder check above is manual.

## Required logs

Primary installer/update log:
`C:\ProgramData\WatchLog\upgrade.log`

Agent/runtime log:
`C:\ProgramData\WatchLog\agent.log`

Do not copy secrets/DPAPI files into support tickets or Git.

## Promotion rule

One successful existing-site bootstrap must prove the permanent online updater. After that,
future normal releases should be delivered through the signed outbound update channel rather
than the full Setup wizard.
