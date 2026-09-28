# WatchLog Windows 5.0.24 — Existing-Site Repair/Upgrade Validation Handoff

Updated: 2026-09-28

## Scope

This file records the Windows packaging/validation state for the 5.0.24
existing-site bootstrap.

Product authority remains:
`Alkalid-security/Watchlog/main`

Product Repair/Upgrade implementation:
PR `#78` / merge `e74526affc4722daf5e14aab07e56b08f72c3d44`

Validation repository:
`visioninfinity0007-droid/Watchlog-2`

Validation branch:
`fix/existing-site-repair-upgrader-v5`

Implementation baseline before documentation-only commits:
`0f3507483ffd7369134fe8b7aa0c6a7be5946ea9`

## Why a separate artifact is required

The full WatchLog Setup contains the Qt/PySide6 discovery/setup UI and the Agent.
It is appropriate for new-site onboarding but is not the normal upgrade path for an
already-enrolled site.

5.0.24 introduces:
`WatchLog-Repair-Upgrade.exe`

It must:
- validate the candidate while the old Agent is still running;
- validate the saved recorder before file replacement;
- preserve enrollment/config/DPAPI secrets;
- avoid Search Network/re-login;
- replace files transactionally;
- commit only after real runtime health;
- bootstrap the permanent signed online updater.

## Ported validation-branch components

- `prototype/installer/nsis/watchlog-repair.nsi`
- `prototype/installer/wl-repair-upgrade.ps1`
- `prototype/agent/remote_update.py`
- `prototype/installer/apply-remote-update.ps1`
- read-only credential access in `credential_store.py`
- staged preflight/runtime-health in `watchlog_agent.py`
- evidence-based capabilities in `connector_capabilities.py`
- production updater wrapper in `release_agent.py`
- launcher-side staged update application/rollback in `run-agent.ps1`
- cryptography/Ed25519 packaging in `build_exe.ps1`
- Repair/Upgrade output + update URL/public-key inputs in `tools/build_windows_release.ps1`
- release test gate `test_existing_site_repair.py`
- full Setup redirect for complete enrolled sites
- truthful rollback success/failure messages.

## Required packaged behavior

### Passive preflight

Before stopping the installed Agent:
- candidate runs as SYSTEM;
- loads the installed `watchlog.ini`;
- reads DPAPI secrets without mutation;
- proves Agent identity;
- proves read-only cloud auth;
- proves signed updater config;
- proves required packaged dependencies.

Failure => installed WatchLog remains untouched.

### Recorder preflight

Before replacing any installed file:
- pause old runtime safely;
- back up full payload;
- staged candidate authenticates to saved recorder;
- recorder identity and channels enumerate.

Failure => old runtime restored and restart verified.

### Health commit

After replacement, require:
- exact 5.0.24 version;
- fresh `heartbeat_at`;
- fresh `recorder_seen_at`;
- fresh `remote_update_poll_at`.

Do not accept process liveness alone.

## Release configuration

The release must contain:
- production HTTPS update manifest URL;
- Ed25519 public key;
- signature-required flag.

Build/publish must fail closed when the update URL/key pair is missing.

The customer update source is a WatchLog-controlled signed feed. GitHub is build/source,
not an arbitrary executable URL supplied by cloud commands.

## Capability evidence

`site_control_runtime` only after a real successful Site Control poll.

`remote_update_v1` only after a real successful update-claim poll with valid signed updater config.

## Historical evidence retained

- Build 69 / 5.0.17 — field-proven discovery/connectivity baseline.
- Build 74 — field discovery failure; green package != field proof.
- Build 83 / 5.0.21 — bounded discovery/setup packaging proof.
- Build 98 / 5.0.21 — exact-path shutdown/file-lock/rollback proof.
- Build 100 / 5.0.23 — frozen FFmpeg archive/gap-recovery proof.

## Current status

Source port: **IN PROGRESS / PRESENT ON VALIDATION BRANCH**

Final Windows Release run: **NOT YET RECORDED**

Final 5.0.24 artifact id/digest/hashes: **NOT YET RECORDED**

Al-Khalid Repair/Upgrade field acceptance: **NOT YET RUN**

Fleet promotion: **NOT AUTHORIZED YET**

## Required next proof

1. run Windows Release on the reviewed validation head;
2. record exact run/artifact/hashes;
3. confirm Repair/Upgrade excludes Qt setup UI and is materially smaller than full Setup;
4. packaged preflight test passes;
5. signed updater config present;
6. controlled Al-Khalid existing-site upgrade;
7. fresh heartbeat + recorder + updater poll;
8. `remote_update_v1` appears only after real poll;
9. one read-only Site Control command;
10. rollback proof;
11. then promote as the standard existing-site upgrade route.
