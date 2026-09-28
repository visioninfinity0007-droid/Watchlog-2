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
