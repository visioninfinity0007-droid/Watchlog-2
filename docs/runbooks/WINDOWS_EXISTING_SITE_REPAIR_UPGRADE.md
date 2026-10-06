# WatchLog Windows Existing-Site Repair/Upgrade Runbook

Use this runbook only for an already-enrolled WatchLog site.

For a new site/new PC, use `WatchLog-Setup.exe`.

Behaviour described here is the 5.1.1 candidate (`docs/release/WINDOWS_INSTALLER_SOURCE_OF_TRUTH.md`
section 1D). It is CI-tested, not field-proven.

## Before starting

Confirm:
- site is already enrolled;
- current Agent is heartbeating (Repair reads the Agent's own last proof of which recorders were
  live; without a recent heartbeat the original recorder must answer the new version);
- current recorder identity is known;
- encrypted DPAPI Agent key exists;
- encrypted DPAPI recorder credential exists;
- exact candidate Repair/Upgrade artifact SHA-256 matches the release ledger;
- the candidate release is configured with the production signed update manifest URL/public key.

Do not continue if the exact artifact has not passed the release gates recorded in
`docs/release/WINDOWS_INSTALLER_SOURCE_OF_TRUTH.md`.

A 5.1.x Repair/Upgrade package repairs multi-recorder sites. Only a 5.0.x package refuses a
site with more than one configured recorder (exit 24) or an unreadable recorder list (exit 25).

## Run

1. Launch `WatchLog-Repair-Upgrade.exe` elevated.
2. Do not manually kill the old Agent, and do not stop the installer. If it is stopped anyway
   (or the PC loses power) while WatchLog is paused, the "WatchLog Agent Upgrade Recovery" task
   restores the previous WatchLog within about 5 minutes (or 1 minute after the next boot) and
   writes the outcome to `repair-upgrade-result.ini` (stage "interrupted upgrade recovery").
3. Phase 1 validates the staged candidate as SYSTEM while the current Agent keeps running.
4. If Phase 1 fails, stop. The installed WatchLog is unchanged.
5. Phase 2 pauses the current WatchLog and validates the candidate against each recorder.
   A recorder that was live before the update and that the new version cannot reach is a
   failure (rolled back). A recorder already offline before is noted and does not block.
6. Only after Phase 2 passes is the payload replaced.
7. Wait for the final health commit.

## Success criteria

Do not accept "installer finished" or "process is running" as success.

The installer commits only with:
- exact new version;
- a cloud heartbeat and a remote-update poll written after the new Agent started (older or
  more than 120 s future-dated markers do not count);
- every recorder that was live before the update live again, each by its own protected row.

A recorder that was offline before may still be offline: the result says so ("still offline
(it was offline before the update; not verified by this update)") and the installer shows how
many. Such a recorder has NOT been verified by the update; check it in Site Status or Manage
Recorders once it is back.

Then check on site:
- `remote_update_v1` visible only after that poll;
- camera/channel inventory unchanged/correct;
- no forced recorder rediscovery;
- no re-entry of recorder password;
- one safe read-only Site Control command succeeds.

## Failure handling

If the candidate fails before replacement:
- no rollback is needed because the old install was not changed.

If failure happens after the pause (before or after replacement), Repair rolls back in this
order and reports success only with proof:
1. restore the full previous payload with the task still suspended;
2. remove a recorder registry this run staged (before the old Agent can start and use it);
3. re-register and start the task, then require a fresh cloud heartbeat from the restored
   version (up to 4 minutes).

Result messages:
- "proven running: its Agent sent a fresh cloud heartbeat" - rollback proven;
- "recovery is NOT proven" - files restored and task running, but no heartbeat in time: check the
  site's heartbeat in the portal; if still offline after 10 minutes, contact support;
- "AUTOMATIC RECOVERY FAILED" - a file could not be restored or the task could not start; the
  task is re-enabled anyway and the recovery task retries every 5 minutes (3 attempts).

In every failure case:
- preserve logs;
- do not uninstall WatchLog and do not run another installer;
- investigate before any new install attempt.

A remote update that was staged or applied but not confirmed before the Repair is closed as
"superseded by Repair/Upgrade"; its old package and rollback image are removed.

## After a successful Repair/Upgrade

- `uninstall.exe` is the 5.1.x uninstaller (it removes the recorder registry, per-recorder
  credentials, all WatchLog tasks, restores the power settings recorded at install and keeps
  only support logs).
- The installed component version (`ComponentsVersion` in the uninstall registry entry) is the
  package version; remote (Agent-only) updates that need newer components are refused and ask
  for a Repair/Upgrade package.

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

`wl-repair-upgrade.ps1` refuses a recorder registry with more than one configured recorder
(exit 24) or an unreadable one (exit 25) only when its candidate is older than 5.1.0. A 5.0.x
package whose script predates that guard does not check, so for it the one-recorder check above
is manual.

## Required logs

Primary installer/update logs:
`C:\ProgramData\WatchLog\repair-upgrade.log` and `repair-upgrade-result.ini` (Repair/Upgrade),
`C:\ProgramData\WatchLog\upgrade.log` (pause, rollback, recovery task and uninstall steps)

Agent/runtime log:
`C:\ProgramData\WatchLog\agent.log`

Do not copy secrets/DPAPI files into support tickets or Git.

## Promotion rule

One successful existing-site bootstrap must prove the permanent online updater. After that,
future normal releases should be delivered through the signed outbound update channel rather
than the full Setup wizard.
