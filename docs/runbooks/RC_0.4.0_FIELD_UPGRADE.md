# WatchLog 0.4.0 — controlled field upgrade + support runbook (transactional upgrade)

**Status:** engineering complete, **RC built and gated, awaiting a controlled field upgrade.** The
0.4.0 installer is an **unsigned RC** built by the gated Windows Release workflow. It is **NOT**
published, and `/latest/` is **unchanged** — the field upgrade uses the direct RC artifact only.
Production promotion (Authenticode signing + `/latest/`) remains separately gated and out of scope
for this window.

Server side is already live and **dormant**: migrations `0054–0059` are applied to production
Supabase `oyvgubyxmjlijiczjona` and the portal is deployed from `main` at `133209e`. Operations
runtime, Operations evidence execution, Archive processing and Multi-agent are **OFF/unavailable on
every live site** and this upgrade does **not** change that (see *Dormancy* below).

## Why this RC exists — the failure class it fixes

A previous attempt to move `0.3.4 → new agent` failed like this:

> installer launched → `watchlog-agent.exe` was **locked** (still running under the scheduled task) →
> NSIS's default `File` hit Abort/Retry/**Ignore** and silently **skipped** the binary → the installer
> nonetheless wrote the ARP `DisplayVersion` and showed its finish page → the agent stopped
> connecting → production still ran/reported the **old** runtime.

Two defects combined: (1) the old binary was never actually replaced because nothing stopped the
running agent or verified the file was unlocked, and (2) the installer **reported success without
verifying anything** — no version check, no "is it running" check. The upgrade could report success
while changing nothing.

## What changed — a transactional, self-recovering upgrade

The install now delegates the dangerous, order-sensitive part to
`prototype/installer/nsis/wl-upgrade.ps1`, staged into the installer. The installer **cannot report
success unless the new agent is actually installed and running**, and any failure **rolls back to
the previous working agent** rather than leaving the site disconnected.

Sequence (all inside the elevated install):

1. **preflight** — stop the `WatchLog Agent` scheduled task; stop **only** the exact
   `watchlog-agent.exe` running under the install dir (never a broad Python/system kill); bounded
   wait for it to exit; **verify the binary is unlocked**; back up the current binary to
   `watchlog-agent.exe.wlbak`. If the binary can't be unlocked, the install **aborts and leaves the
   old runtime intact** (exit 10) — the exact opposite of the original silent-skip.
2. **write binaries** with `SetOverwrite try` + explicit error check — a locked/failed write can no
   longer be silently ignored; it triggers rollback.
3. **verify-version** — the on-disk **file ProductVersion** AND the runtime **`--version`** must both
   equal the release. A binary that was not actually replaced fails here (exit 11) and never starts.
4. register + start the SYSTEM task, then **commit** — re-verify the version, confirm exactly **one**
   `watchlog-agent.exe` is running and stays alive (exit 12 = didn't start/crash-loop, exit 13 =
   duplicate runtime). Only on success is the backup dropped.
5. **ARP `DisplayVersion` is written only after commit succeeds** — no false "0.4.0 installed".
6. On any failure → **rollback**: restore `watchlog-agent.exe.wlbak` and restart the previous agent.

`wl-upgrade.ps1` touches processes, files and version strings **only**. It reads/writes **no** agent
key, enrollment code, NVR password or decrypted secret, and logs none. Support-grade progress is
appended to `C:\ProgramData\WatchLog\upgrade.log`.

**Exit-code contract** (visible in `upgrade.log`): `0` success · `10` locked/still-running (old
runtime preserved) · `11` version mismatch/missing binary · `12` new agent not alive · `13`
duplicate runtime · `20` usage error.

## RC identity — record before the window (attested by the gated build)

Source: `Alkalid-security/Watchlog` (mirror `visioninfinity0007-droid/Watchlog-2`), branch
`fix/windows-upgrade-transactional`.

| Item | Value |
| --- | --- |
| Version | `0.4.0` |
| Source SHA | `8cabddd455aafe6f3bd06d0926e0b160514b50a4` |
| CI run (backend/integration/integration-prod-order/portal/setup-ui-build/installer-contract) | `34406207779` (all 6 green) |
| Windows Security Gate run | `34406281310` (all 3 green) |
| Windows Release run (RC build) | `34406284293` (run #12, green) |
| `watchlog-agent.exe` SHA-256 (inner runtime) | `6D8DC8131C4ED4F01AC0359AB3CFA33196AAAD9EBFA1E26D994212F65EC5538B` |
| `watchlog-setup-ui.exe` SHA-256 | `A5E1ED3E4BF4E046EA392E8D4D66585BCBAF44D8D054B357B6632E131A6E0574` |
| `WatchLog-Setup.exe` SHA-256 (installer) | `EE505C12B4B22B34BDBAB075EC7C129EC9EAEAC2C6DA93C4944326EDF5299473` |
| `WatchLog-Setup.exe` size | ≈ 328.9 MB (agent 54.3 MB + setup UI 276.3 MB bundled) — **verify by SHA-256, not size** |

The RC build itself proves version truth on the real packaged exe: it fails unless all three
executables report `ProductVersion 0.4.0`, the agent `--selftest`/`--version` report `0.4.0`, and
`wl-upgrade.ps1 -Stage verify-version` **passes at 0.4.0 and rejects `9.9.9`**. The three SHA-256s
above are emitted by that same gated build into the artifact
`WatchLog-Release.hashes.txt` — never transcribed by hand.

## Field agent facts (Al-Khalid "Main site")

- Host `SM-HP`, recorder Dahua `DH-XVR1B08-I` (`dahua-cgi`), 8 cameras, agent id `ca375509…`.
- Install dir `C:\Program Files\WatchLog\`; agent exe `watchlog-agent.exe` (version embedded in the
  file's `ProductVersion`); setup UI `watchlog-setup-ui.exe`; config `watchlog.ini`.
- Runs as the **Scheduled Task** `WatchLog Agent` (NOT a Windows service), launched by
  `run-agent.ps1` as SYSTEM in a restart loop, output to `C:\ProgramData\WatchLog\agent.log`.
- Durable state in `C:\ProgramData\WatchLog\`: `watchlog.ini`, `Secrets\nvr_credential.dpapi`,
  `agent_state.json`, the health store and the spool. **The upgrade preserves all of it** — identity
  and the encrypted split credential are 0.3.4↔0.4.0 compatible, so there is no re-enrolment and no
  credential re-entry.

## Pre-flight (before the window) — prove the OLD state first

Item-12 requires proving the *starting* state before touching anything:

- **Old version + connection, confirmed:**
  `(Get-Item 'C:\Program Files\WatchLog\watchlog-agent.exe').VersionInfo.ProductVersion` → `0.3.4`;
  `agent.log` shows a recent `heartbeat ok`; the portal Site-Health page shows the site online; the
  `agents` row reads `agent_version = 0.3.4` with a current `last_seen_at`. Record the agent id.
- **Back up the whole reversible surface:** copy `C:\ProgramData\WatchLog\` to
  `C:\ProgramData\WatchLog.bak-pre-0.4.0\`.
- Keep the known-good **0.3.4** installer as the manual rollback artifact.

## Upgrade

1. Download the RC `WatchLog-Setup.exe` (CI artifact `WatchLog-Windows-12` from run `34406284293`,
   alongside `WatchLog-Setup.exe.sha256` and `WatchLog-Release.hashes.txt`). **Verify before running**
   (do not run on mismatch):
   - SHA-256 = `EE505C12B4B22B34BDBAB075EC7C129EC9EAEAC2C6DA93C4944326EDF5299473`
     (`certutil -hashfile WatchLog-Setup.exe SHA256`) — this is the authoritative integrity check.
2. **Run it elevated (Run as administrator)** — task registration needs full elevation. The installer
   performs the transactional sequence above; it does **not** prompt on a locked binary and cannot
   finish "successfully" unless 0.4.0 is actually running.
3. Watch it come alive:
   ```powershell
   Get-Content 'C:\ProgramData\WatchLog\upgrade.log' -Tail 20      # preflight OK -> verify-version OK -> commit OK
   Get-Content 'C:\ProgramData\WatchLog\agent.log'   -Tail 12 -Wait # watchlog-agent 0.4.0 -> NVR connect -> cameras synced: 8 -> heartbeat ok
   ```

## Acceptance checks (ALL must pass)

1. **No locked-file error / clean transaction.** `upgrade.log` shows `preflight OK`,
   `verify-version OK: on-disk file + runtime both report 0.4.0`, and
   `commit OK: version verified (0.4.0), single instance alive`. No `FAILURE(10/11/12/13)`.
2. **Exact new runtime version — three ways agree:**
   - file: `(Get-Item 'C:\Program Files\WatchLog\watchlog-agent.exe').VersionInfo.ProductVersion` → `0.4.0`;
   - runtime: `& 'C:\Program Files\WatchLog\watchlog-agent.exe' --version` → `0.4.0`;
   - running path: the live `watchlog-agent.exe` process image path is under `C:\Program Files\WatchLog\`.
3. **Same agent identity + site.** The `agents` row is the **same agent id** as pre-flight (no new
   enrolment), same site; `Secrets\nvr_credential.dpapi` still present and decrypting (no plaintext).
4. **Heartbeat reconnects.** `agent.log` shows a fresh `heartbeat ok`; the `agents` row flips to
   `agent_version = 0.4.0` with a current `last_seen_at`.
5. **Same 8 cameras.** `agent.log` shows `cameras synced: 8`; the portal shows the same 8 cameras
   (no loss, no duplicates).
6. **Events resume.** New analytics events appear for the site after the upgrade timestamp.
7. **Phase-A health resumes.** `camera_health` / `nvr_health` rows for the site refresh; the portal
   "Operational health" panel shows real recorder/camera state (not UNKNOWN).
8. **Capability advertisement appears (advertised, NOT enabled).** The `agents.capabilities` column
   for this agent lists the seven runtime classes (`operations_runtime`,
   `operations_extended_primitives`, `operations_evidence_still`, `operations_evidence_clip`,
   `archive_processing`, `multi_agent_fencing`, `recorder_probe_v2`) and
   `wl_site_has_capability(site, 'operations_runtime')` returns true. This is the server learning what
   the runtime *could* do — it does **not** turn anything on (check 10).
9. **No duplicate agent.** Exactly **one** `watchlog-agent.exe` process is running, and exactly one
   recently-active `agents` row exists for the site (no second/ghost agent).
10. **Operations / Multi-agent / Archive remain OFF.** `sites.operations_runtime_enabled = false`
    for the site; no operations-bridged incidents or evidence actions are created; the agent remains
    the single lease authority (multi-agent inert); archive processing stays idle. Advertising the
    capabilities in check 8 must **not** have flipped any of these on.

DB spot-checks (read-only; run against production Supabase `oyvgubyxmjlijiczjona`):

```sql
-- identity, version, freshness, advertised capabilities (check 3,4,8,9)
select id, agent_version, last_seen_at, capabilities
from agents where site_id = '<site-id>' order by last_seen_at desc;

-- the server learned the capability but the feature is still OFF (check 8 vs 10)
select wl_site_has_capability('<site-id>','operations_runtime') as advertised,
       operations_runtime_enabled as enabled
from sites where id = '<site-id>';
```

`advertised = true` with `enabled = false` is the **correct, expected** end state: the runtime is
capable, nothing is switched on.

## Rollback

**Automatic:** any stage failure triggers `-Stage rollback` inside the same install — the previous
`watchlog-agent.exe` is restored from `watchlog-agent.exe.wlbak` and the task restarted, so a failed
upgrade leaves WatchLog running on **0.3.4**, connected, not disconnected. Confirm with
`upgrade.log` (`rollback complete`) and `agent.log` (0.3.4 back, heartbeat resumed).

**Manual (belt-and-braces):** stop the `WatchLog Agent` task, reinstall the known-good **0.3.4**
installer (upgrade-aware, keeps the credential); if `C:\ProgramData\WatchLog\` was altered, restore
it from `WatchLog.bak-pre-0.4.0\`. The server side is unaffected by an agent rollback — an older
agent simply stops advertising the new capabilities and populating new tables; no schema is reversed.

## Dormancy — leave everything OFF

This upgrade delivers a **capable** runtime; it does **not** enable any runtime feature. Do **not**
run `wl_set_operations_runtime`, do not author Operations primitives that depend on the runtime, do
not enable evidence execution, archive processing or multi-agent for any live site as part of this
window. The intended post-upgrade state is exactly *check 10*: capabilities advertised, every runtime
feature OFF.

**Future activation (separate, explicit approval required):** turning Operations on for a site is a
deliberate, owner/admin action (`wl_set_operations_runtime`) that the server accepts **only** when a
recently-active agent for that site has advertised `operations_runtime`. Multi-agent stays bound to
authoritative-capability leasing (a single authoritative agent at a time). None of that is in scope
here.

## Out of scope (separately gated)

Authenticode-sign the installer (lift the unsigned block) and promote `/latest/`. Neither is part of
this field upgrade.
