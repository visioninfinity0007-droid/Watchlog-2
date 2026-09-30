# WatchLog 5.0.24 Existing-Site Repair/Upgrade — Current Status

Updated: 2026-09-28

## Authority

Product repository: `Alkalid-security/Watchlog`

Product branch: `main`

Current product version: **5.0.24**

Repair/Upgrade implementation:
- PR: **#78**
- merge commit: `e74526affc4722daf5e14aab07e56b08f72c3d44`

Current product main at this context update:
`7034e2a1deb0c1909fe68ddbd1f7338a3e82bae7`

Windows validation repository:
`visioninfinity0007-droid/Watchlog-2`

Validation branch:
`fix/existing-site-repair-upgrader-v5`

Validation branch head at this context update:
`0f3507483ffd7369134fe8b7aa0c6a7be5946ea9`

## Why this exists

The full ~123 MB WatchLog installer combines:
- the self-contained Site Agent; and
- the Qt/PySide6 Setup UI used for new-site discovery/enrollment.

That is appropriate for first installation, but it is the wrong upgrade mechanism for an already-enrolled site.

A real Al-Khalid Head Office test proved the problem:
- the newer full installer replaced the runtime;
- the new Agent did not stay healthy;
- rollback restored the previous 5.0.19 runtime;
- the old installer message could say “restored” without independently proving the old Agent restart;
- 5.0.19 still had no active `remote_update_v1`.

5.0.24 therefore separates new-site setup from existing-site upgrade.

## Permanent Windows model

### WatchLog-Setup.exe

Use only for:
- new PC / new site;
- recorder discovery;
- recorder login;
- initial camera mapping;
- enrollment.

A complete enrolled site must be redirected before the full installer stops WatchLog or replaces files.

### WatchLog-Repair-Upgrade.exe

Use for:
- existing enrolled sites;
- repair/bootstrap from old versions;
- one-time transition onto the permanent signed online updater.

The Repair/Upgrade package intentionally does **not** ship the Qt setup/discovery UI.

## Phase 1 — passive staged validation

Runs while the old WatchLog remains untouched.

The candidate is staged outside the live install and run as Windows SYSTEM.

It must prove:
- candidate EXE starts;
- exact version is readable;
- existing `watchlog.ini` can be loaded explicitly;
- existing Agent identity/state can be read;
- machine-bound DPAPI recorder credential can be decrypted read-only;
- bundled runtime dependencies load;
- bundled historical-video decoder self-test works;
- cloud identity authenticates via read-only `wl_agent_preflight_auth`;
- signed online-update URL/public key configuration is present.

If Phase 1 fails:
- old WatchLog is never stopped;
- no installed file is replaced;
- no recorder credential is migrated/changed.

## Phase 2 — recorder staged validation

Only after Phase 1 succeeds:
- suspend the WatchLog scheduled-task watchdog;
- stop the current WatchLog runtime safely;
- back up the complete existing payload;
- run the staged candidate against the saved recorder credential;
- prove recorder identity/authentication;
- prove channel enumeration.

If Phase 2 fails:
- candidate is not installed;
- old payload/runtime is restored;
- rollback is not called successful until the old Agent restart is verified.

## Phase 3 — atomic replacement + health commit

Only after Phases 1 and 2 pass:
- copy candidate payload;
- verify ProductVersion and runtime version;
- register/start WatchLog;
- wait for protected runtime-health evidence.

Required success evidence:
- exact `agent_version`;
- fresh `heartbeat_at`;
- fresh `recorder_seen_at`;
- fresh `remote_update_poll_at`.

`remote_update_poll_at` is written only after the new updater successfully calls the production update-claim RPC.

A running process alone is **not** upgrade success.

## Runtime capability truth

`site_control_runtime`:
- advertise only after a recent successful Site Control claim poll.

`remote_update_v1`:
- advertise only when signed updater config is valid;
- AND a recent real remote-update claim poll has succeeded.

Do not advertise either capability merely because code is present.

## Secure online-update contract

The online updater is outbound-only.

Cloud may not:
- execute arbitrary shell commands;
- choose arbitrary binary URLs;
- bypass release verification.

Agent must:
- fetch its locally configured HTTPS manifest;
- verify Ed25519 manifest signature;
- verify SHA-256 and size;
- stage the next Agent;
- apply it only between Agent runs;
- retain rollback copy;
- verify target version;
- restore previous Agent on early-start failure;
- wait for health proof before declaring success.

The Windows release must fail closed if either production update URL or Ed25519 public key is absent.

## Existing validation evidence retained

Build 69 / 5.0.17:
- field-proven discovery/connectivity golden baseline.

Build 74:
- real field failure; Search Network could hang without surfacing recorder.

Build 83 / 5.0.21:
- packaged bounded discovery/setup validation.

Build 98 / 5.0.21:
- real Windows process/file-lock shutdown + full rollback validation.

Build 100 / 5.0.23:
- packaged FFmpeg historical-footage decoder/gap-recovery validation.

None of Builds 83/98/100 is the final 5.0.24 fleet installer.

## Current 5.0.24 validation status

The Windows validation branch currently includes:
- `watchlog-repair.nsi`;
- `wl-repair-upgrade.ps1`;
- read-only recorder credential access;
- staged existing-site preflight;
- protected runtime-health file;
- secure `remote_update.py` worker;
- transactional `apply-remote-update.ps1`;
- launcher-side staged update handoff/rollback;
- Ed25519/cryptography frozen packaging;
- update URL/public-key build inputs;
- release tests for the Repair/Upgrade contract;
- full-installer redirect for fully enrolled sites;
- truthful rollback messages.

**A final 5.0.24 Windows Repair/Upgrade artifact has not yet been promoted.**

Do not instruct a field operator to run a 5.0.24 Repair/Upgrade until the exact artifact/run/hashes are recorded and Windows Release passes.

## Mandatory field acceptance before promotion

On Al-Khalid Head Office:
1. verify old Agent healthy before upgrade;
2. run `WatchLog-Repair-Upgrade.exe`, not full Setup;
3. passive staged preflight passes while old Agent remains running;
4. recorder staged preflight identifies the saved recorder and channels;
5. replacement occurs only after both staged gates pass;
6. new Agent heartbeats;
7. recorder contact becomes fresh;
8. remote updater polls;
9. `remote_update_v1` appears only after actual poll proof;
10. run one read-only Site Control command;
11. verify no rediscovery/re-login was required;
12. verify rollback remains available until health commit.

After that bootstrap, normal future updates should use signed online update instead of repeated site visits/full installer runs.

## Do-not-regress

- Never use full Setup as the default upgrade route for a complete enrolled site.
- Never stop the old Agent before passive candidate validation passes.
- Never replace files before recorder staged validation passes.
- Never re-run Search Network during a normal healthy-site upgrade.
- Never migrate/mutate DPAPI credentials during staged validation.
- Never broad-kill same-named processes outside the WatchLog install path.
- Never call rollback successful without old-Agent restart proof.
- Never commit based only on process liveness.
- Never advertise online-update capability without real updater polling.
- Never ship a Repair/Upgrade with missing signed update-feed configuration.
- Never promote a new discovery baseline solely because CI/package checks are green.
