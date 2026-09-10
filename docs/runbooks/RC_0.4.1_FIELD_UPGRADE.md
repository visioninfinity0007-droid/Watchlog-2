# WatchLog 0.4.1 — controlled field upgrade + support runbook (supersedes 0.4.0)

**Status:** engineering complete, **0.4.1 RC built and gated, awaiting a controlled field upgrade.**
Unsigned RC; **not published**, `/latest/` unchanged. Supersedes the 0.4.0 RC.

## Why 0.4.1 exists — the 0.4.0 defect (do not ship 0.4.0)

The 0.4.0 RC agent **could not run.** The packaged entrypoint rebinds the run dispatch
(`release_agent` → `wrap_cmd_run(enhanced_cmd_run)`), but `watchlog_agent.main()` calls it with a
`channels` argument the override did not accept → **`TypeError` on every normal start** → the
scheduled task + `run-agent.ps1` restart it every ~15s → a **permanent crash loop** with no events,
no heartbeat, no health, and one cryptic log line. `--version`/`--selftest` return before that call,
so CI stayed green. (This is the same symptom seen in the 0.3.5 field attempt: "got as far as
*analytics reported*, then stopped; the database kept showing the old agent.")

**0.4.1 fixes that plus three defects found alongside it:**
1. **Startup (critical):** the run dispatch accepts `channels` end-to-end; the agent starts.
2. **Recorder auth breaker:** the shipped collector now escalates 5→15→30 min on a **confirmed**
   wrong-password and wakes on a credential change, instead of retrying login every 20s forever
   (recorder account-lockout risk).
3. **Health restored:** the agent now runs the Phase-A health worker, so camera/NVR/recording/storage
   health is reported and the portal shows real state instead of UNKNOWN.
4. **Log hygiene:** the enrollment code and agent key are no longer written to `agent.log`.

A CI-gated `test_agent_run_dispatch.py` now binds the packaged dispatch exactly as `main()` does, so
this class of crash fails in CI, not in the field.

## RC identity — record before the window (attested by the gated build)

Source: `Alkalid-security/Watchlog` (mirror `visioninfinity0007-droid/Watchlog-2`), branch
`fix/agent-runtime-startup` (installer closure + runtime fix).

| Item | Value |
| --- | --- |
| Version | `0.4.1` |
| Source SHA (RC build) | `96fc353c41e34ffb3546b821dd47770592268516` |
| CI run (all 6 jobs green) | `34449114045` |
| Windows Security Gate run (3 jobs green) | `34449131337` |
| Windows Release run (RC build, green) | `34449133878` |
| `watchlog-agent.exe` SHA-256 (inner runtime) | `DB2230F4C54A37D6F5C1840819658593DA4F8147D5A0F9DA242FBA6E93C65778` |
| `watchlog-setup-ui.exe` SHA-256 | `C4497E46D820E6F7EC18A1CC0E2E91D78D27D4F5DAEDF960FCE84C8C567D7DAE` |
| `WatchLog-Setup.exe` SHA-256 (installer) | `F34A4391849B2DDA8362EF1DCC18172FD12395F415CF433201EE2EE05C564778` |

## Field agent facts (Al-Khalid "Main site")

- Host `SM-HP`, recorder Dahua `DH-XVR1B08-I` (`dahua-cgi`), 8 cameras, agent id
  `ca375509-a107-4d64-83ad-c1ff660b749d`, site id `588cb40a-3325-4d0f-8ca7-5eee7eb6e443`,
  tenant id `1c1ccbac-6c6d-4539-adda-560b15c18442`.
- Install dir `C:\Program Files\WatchLog\`; scheduled task `WatchLog Agent` (SYSTEM at boot via
  `run-agent.ps1`); durable state in `C:\ProgramData\WatchLog\`.
- The upgrade preserves identity + the encrypted split credential (no re-enrolment, no credential
  re-entry). The transactional installer (0.4.0 work, retained) stops the old agent, verifies the
  binary is unlocked, replaces it, verifies version, and rolls back on any failure.

## Pre-flight (prove the OLD state first)

- **Old version + connection:** as of this audit the live agent last reported **2026-09-08 13:47
  UTC** (~stale). The PC must be **online and the 0.3.4 agent connected** before upgrading — confirm
  a recent `heartbeat ok` in `agent.log` and the site online in the portal. Record the agent id.
- Back up `C:\ProgramData\WatchLog\` to `WatchLog.bak-pre-0.4.1\`.
- Keep the known-good **0.3.4** installer as the manual rollback artifact.

## Upgrade

1. Download the RC `WatchLog-Setup.exe`; verify SHA-256 =
   `F34A4391849B2DDA8362EF1DCC18172FD12395F415CF433201EE2EE05C564778` (`certutil -hashfile`).
2. Run elevated (Run as administrator). The transactional installer performs preflight → replace →
   verify-version → commit; it cannot report success unless 0.4.1 is installed and running.
3. Watch it come alive:
   ```powershell
   Get-Content 'C:\ProgramData\WatchLog\upgrade.log' -Tail 20
   Get-Content 'C:\ProgramData\WatchLog\agent.log'   -Tail 20 -Wait
   ```

## Acceptance checks (ALL must pass)

1. **Agent actually RUNS (the 0.4.0 regression guard).** `agent.log` shows the run loop entered —
   a line beginning `running: events every …s, analytics enabled, heartbeat every …s, health every
   …s` — followed by `cameras synced: 8` and a fresh `heartbeat ok`. **There must be NO
   `TypeError`, no `unexpected keyword argument 'channels'`, and no repeating restart every ~15s.**
2. **Exact new runtime version 0.4.1** — file `ProductVersion`, `--version`, and the running image
   path under `C:\Program Files\WatchLog\` all agree.
3. **Same agent identity + site** — the `agents` row is the **same agent id** (`ca375509…`), same
   site; `Secrets\nvr_credential.dpapi` still decrypts (no plaintext).
4. **Heartbeat reconnects** — `agents` row flips to `agent_version = 0.4.1` with current `last_seen_at`.
5. **Same 8 cameras** — `cameras synced: 8`; portal shows the same 8, no duplicates.
6. **Events resume** — new events appear after the upgrade timestamp.
7. **Health lights up (0.4.1 restores it)** — `camera_health` / `nvr_health` rows for the site
   populate; the portal Operational-health panel shows real recorder/camera state (not UNKNOWN).
8. **Capability advertisement appears (advertised, NOT enabled)** — `agents.capabilities` lists the
   seven runtime classes and `wl_site_has_capability(site,'operations_runtime')` is true, while
   `sites.operations_runtime_enabled` stays **false**.
9. **No duplicate agent / single process** — exactly one `watchlog-agent.exe`; one recently-active
   `agents` row for the site.
10. **Operations / Multi-agent / Archive remain OFF** — `operations_runtime_enabled = false`; no
    ops incidents / evidence / archive scans / leases created by the upgrade.
11. **Recorder auth breaker (spot check, optional)** — if a wrong password is ever set, `agent.log`
    shows `recorder authentication is failing; backing off … min`, not a 20s retry storm.

## Rollback

Automatic: any install stage failure restores the previous `watchlog-agent.exe` and restarts it
(0.3.4 back, connected). Manual: reinstall the known-good 0.3.4 installer; restore
`C:\ProgramData\WatchLog\` from the backup if altered. The server side is unaffected by an agent
rollback.

## Dormancy

0.4.1 advertises capabilities but enables nothing. Do **not** run `wl_set_operations_runtime`, enable
evidence/archive/multi-agent, or author runtime-dependent primitives for any live site in this
window. Intended end state: capabilities advertised, every runtime feature OFF.

## Out of scope (separately gated)

Authenticode-sign the installer and promote `/latest/`. Neither is part of this field upgrade.
