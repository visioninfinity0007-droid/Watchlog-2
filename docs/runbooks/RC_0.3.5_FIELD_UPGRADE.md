# WatchLog 0.3.5 — Al-Khalid field upgrade + support runbook

**Status (2026-09-09):** the server side is LIVE. Health migrations `0042–0048` are applied to
production Supabase `oyvgubyxmjlijiczjona` (head = 0048); the portal is deployed from `main` at
`e345d0a` (`running:healthy`, serving the "Operational health" section); the 0.3.5 installer is
published at `…/downloads/watchlog/0.3.5/WatchLog-Setup.exe`. The installer is **unsigned**
(production promotion still gated on Authenticode). `/latest/` is unchanged — the client uses the
direct 0.3.5 link for the controlled upgrade.

Because the health RPCs now EXIST in production, a 0.3.5 agent that runs its health cycle will light
up the health features end-to-end (camera/NVR health, storage state, operational faults, and the
portal Site-Health panel). Until a 0.3.5 agent reports, the 8 cameras correctly read **UNKNOWN**.

## Field agent facts (Al-Khalid "Main site")

- Host `SM-HP`, recorder Dahua `DH-XVR1B08-I` (`dahua-cgi`), 8 cameras, agent id `ca375509…`.
- Install dir `C:\Program Files\WatchLog\`; agent exe `watchlog-agent.exe` (version embedded in the
  file's `ProductVersion`); setup UI `watchlog-setup-ui.exe`; config `watchlog.ini`.
- Runs as the **Scheduled Task** `WatchLog Agent` (NOT a Windows service), launched by
  `run-agent.ps1` as SYSTEM, which restarts the agent if it exits and captures output to
  `C:\ProgramData\WatchLog\agent.log`.
- Durable state in `C:\ProgramData\WatchLog\`: `watchlog.ini`, `Secrets\nvr_credential.dpapi`,
  `agent_state.json`, the health store and the spool.

## Pre-flight (before the window)

- Confirm the current install and health: `agent.log` shows a recent `heartbeat ok`; the portal Site
  Health page shows the site online.
- **Back up the whole reversible surface:** copy `C:\ProgramData\WatchLog\` to
  `C:\ProgramData\WatchLog.bak-pre-0.3.5\`. RC8 (0.3.4) and 0.3.5 share the SAME encrypted split
  credential store, so no credential re-migration happens and rollback is clean.
- Keep the known-good RC8 (0.3.4) installer as the rollback artifact.

## Upgrade

1. Download `…/downloads/watchlog/0.3.5/WatchLog-Setup.exe`. **Verify before running:**
   - size = `344,836,206` bytes, and
   - SHA-256 = `A38AF34956653C92E7119ABECA84B575DA82005128AF7FE88E54009ED05D0BE6`
     (`certutil -hashfile WatchLog-Setup.exe SHA256`).
2. **Run it elevated (Run as administrator).** Full elevation matters — see the support note below;
   the task registration needs admin. The installer is upgrade-aware: it ends the `WatchLog Agent`
   task, keeps the encrypted credential (no re-entry), re-registers the task, and starts it.
3. Confirm `agent.log` shows `watchlog-agent 0.3.5` starting, NVR connect, `cameras synced: 8`, and a
   `heartbeat ok` within ~2–3 minutes.

## Acceptance checks (must all pass)

- Installed version is 0.3.5: `(Get-Item 'C:\Program Files\WatchLog\watchlog-agent.exe').VersionInfo.ProductVersion`.
- Task present and running: `schtasks /Query /TN "WatchLog Agent"` → `Running`.
- `agent.log` shows a fresh `heartbeat ok`; the database `agents` row flips to `0.3.5` with a current
  `last_seen_at`.
- Health lights up: `camera_health` / `nvr_health` rows appear for the site; the portal Site-Health
  "Operational health" panel shows real recorder/camera state (not all UNKNOWN).
- DPAPI credential `Secrets\nvr_credential.dpapi` still present and decrypting; no plaintext.

## Troubleshooting — installed but not reporting (validated 2026-09-08)

Symptom seen in the field: the 0.3.5 binary installed correctly (ProductVersion 0.3.5) but the
database kept showing the old 0.3.4 agent and no health rows. Root cause: **the install did not
create the `WatchLog Agent` scheduled task** (`schtasks /Query` returned `TASK NOT FOUND`) — almost
always because the installer was not run **fully elevated**. The agent had run once transiently, got
as far as `analytics reported`, then stopped with nothing to supervise/restart it.

Diagnose (PowerShell as Administrator):

```powershell
(Get-Item 'C:\Program Files\WatchLog\watchlog-agent.exe').VersionInfo.ProductVersion   # expect 0.3.5
schtasks /Query /TN "WatchLog Agent"                                                    # expect it to exist / Running
Get-Content 'C:\ProgramData\WatchLog\agent.log' -Tail 30                                # last run + any error
```

Fix — register + start the task (this is the step the un-elevated install skipped):

```powershell
Get-Process watchlog-agent -ErrorAction SilentlyContinue | Stop-Process -Force
powershell -ExecutionPolicy Bypass -File "C:\Program Files\WatchLog\register-service.ps1" -InstallDir "C:\Program Files\WatchLog"
```

`register-service.ps1` registers the SYSTEM task, starts it, and waits for `Running`; it prints
"registered and started (state: Running)" or throws with the reason. Then watch it come alive:

```powershell
Get-Content 'C:\ProgramData\WatchLog\agent.log' -Tail 8 -Wait   # expect: running… then heartbeat ok
```

The moment `heartbeat ok` appears, the `agents` row flips to 0.3.5 and health rows begin to fill.

## Rollback (if any acceptance check fails)

1. Stop the `WatchLog Agent` task.
2. Reinstall the known-good **RC8 (0.3.4)** installer (upgrade-aware; keeps the credential).
3. If `watchlog.ini`/health store were altered, restore `C:\ProgramData\WatchLog\` from the
   `WatchLog.bak-pre-0.3.5\` backup (the credential store is compatible either way).
4. Confirm the log shows 0.3.4 back, cameras syncing, heartbeat resumed.

The server side (DB/portal) is unaffected by an agent rollback: an older agent simply stops
populating the new health tables and the site reads UNKNOWN again — no schema change is reversed.

## Remaining separately-approved steps

Production-sign the installer (lift the unsigned block) and promote `/latest/`. Neither is part of
this field upgrade and both are explicitly out of current scope.
