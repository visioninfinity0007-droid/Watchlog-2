# WatchLog Windows Validation Status — 5.0.24 Existing-Site Repair/Upgrade

Updated: 2026-09-28

## Role of this repository

Repository:
`visioninfinity0007-droid/Watchlog-2`

This repository is the Windows release-line validation repository.

It is **not** the product source of truth.

Product authority:
`Alkalid-security/Watchlog/main`

Current product version:
**5.0.24**

Repair/Upgrade product implementation:
PR `#78` / merge `e74526affc4722daf5e14aab07e56b08f72c3d44`

## Current validation branch

Branch:
`fix/existing-site-repair-upgrader-v5`

Head at this status update:
`0f3507483ffd7369134fe8b7aa0c6a7be5946ea9`

This branch starts from the current 5.0.23 Windows connector lineage and ports the
5.0.24 existing-site bootstrap without replacing the validated discovery/connectivity path.

## Golden field baseline

Build 69 / 5.0.17 remains the field-proven discovery/connectivity baseline.

Build 74 remains the negative field example: green packaging did not prevent Search Network
from failing to surface the recorder.

No 5.0.24 artifact may replace Build 69 as the discovery/connectivity baseline until the exact
artifact passes physical Hikvision + Dahua acceptance.

## 5.0.24 validation scope currently present on this branch

Agent/runtime:
- 5.0.24 version identity;
- read-only DPAPI recorder credential loader;
- staged existing-site preflight command;
- protected runtime-health proof;
- secure signed remote-update worker;
- evidence-based Site Control capability proof;
- evidence-based `remote_update_v1` proof;
- production connector wraps the remote updater;
- bundled Ed25519/cryptography verifier.

Installer/update:
- `WatchLog-Repair-Upgrade.exe` NSIS source;
- `wl-repair-upgrade.ps1` staged orchestration;
- passive SYSTEM preflight while old WatchLog remains untouched;
- recorder preflight before payload replacement;
- full-payload backup/rollback;
- `apply-remote-update.ps1` transactional handoff;
- launcher-side staged update apply/rollback;
- full Setup redirects complete enrolled sites to Repair/Upgrade;
- rollback messages distinguish proven restart from unproven restart.

Release build:
- Repair/Upgrade artifact output;
- production update URL/public-key inputs;
- signed updater config written into the release;
- release gates for existing-site Repair/Upgrade contracts.

## Required safety order

1. Validate candidate payload/version.
2. Run passive staged candidate as SYSTEM against existing config/identity while old WatchLog keeps running.
3. Passive failure = current installation remains untouched.
4. Only after passive pass, suspend/stop old WatchLog safely.
5. Back up the full old payload.
6. Run staged candidate against the actual saved recorder.
7. Recorder failure = restore/restart old WatchLog; no candidate install.
8. Only after recorder pass, replace payload.
9. Start exact new version.
10. Require fresh `heartbeat_at`, `recorder_seen_at` and `remote_update_poll_at`.
11. Commit only after all health proof passes.
12. Otherwise rollback and prove old Agent restart.

## Capability truth

`site_control_runtime` is valid only after a recent successful Site Control claim poll.

`remote_update_v1` is valid only after:
- signed update configuration is valid; and
- the remote-update worker has successfully polled the update-claim RPC recently.

Code presence alone is not capability evidence.

## Existing validation evidence retained

Build 83 / 5.0.21:
- packaged bounded discovery/setup proof.

Build 98 / 5.0.21:
- real Windows running-process/file-lock shutdown and rollback proof.

Build 100 / 5.0.23:
- frozen FFmpeg archive/gap-recovery decoder proof.

These are behavior-specific validation artifacts, not the final 5.0.24 fleet release.

## Field incident driving this work

Recent Al-Khalid Head Office full-installer test:
- candidate new Agent failed health;
- rollback returned the site to 5.0.19;
- old message path could claim restore without independently proving old-Agent restart;
- 5.0.19 has no active `remote_update_v1`.

Therefore the next field test must use the staged Repair/Upgrade path, not full Setup.

## Current release state

**5.0.24 Windows artifact: NOT YET PROMOTED.**

Do not call this branch field-ready merely because the source port exists.

Before promotion:
- complete Windows Release from this exact branch head or its reviewed successor;
- record run id, artifact id, ZIP digest and executable SHA-256 hashes;
- verify Repair/Upgrade is materially smaller than full Setup and contains no Qt setup UI;
- verify packaged staged preflight;
- verify signed update feed configuration;
- run controlled Al-Khalid bootstrap;
- prove fresh heartbeat + recorder + updater poll;
- prove `remote_update_v1` appears only after real polling;
- run one safe read-only Site Control command;
- prove rollback.

## Do-not-regress

- Do not replace the release-line discovery implementation with a product-repo copy wholesale.
- Do not reduce Build 69 discovery reach.
- Do not force Search Network during a normal existing-site upgrade.
- Do not stop the old Agent before passive staged validation succeeds.
- Do not replace installed files before recorder staged validation succeeds.
- Do not mutate DPAPI credentials during staged preflight.
- Do not broad-kill same-named processes outside the WatchLog install path.
- Do not accept process liveness as health.
- Do not advertise remote update without real updater polling.
- Do not publish a Repair/Upgrade without the signed update URL/public key.
- Do not claim rollback success without old-Agent restart proof.
