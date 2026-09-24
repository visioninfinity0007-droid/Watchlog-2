# Windows installer source of truth

**Purpose:** prevent release work, support analysis, or AI-assisted changes from
patching the wrong WatchLog repository.

## Current authoritative successful Windows baseline

As of 2026-09-24, the latest successful Windows installer candidate is:

- Product: WatchLog Windows Site Connector
- Build: **49**
- Product version: **5.0.6**
- Authoritative repository: `visioninfinity0007-droid/Watchlog-2`
- Authoritative branch: `build/site-connector-v5-watchlog2`
- Source commit: `e9761cb32025c2c3dc596cfe13d7c2225c8a4aa0`
- Windows Release workflow: **#49**, run id `36018778077`
- Workflow conclusion: **success**
- Artifact: `WatchLog-Windows-49`
- Artifact id: `10815424189`
- Artifact size: `66530060` bytes
- GitHub artifact digest:
  `sha256:fd8ef666c55333c68c4828bef1a84f023d80c0edb72cd9686c4d341215d6340d`
- `WatchLog-Setup.exe` SHA-256:
  `EB89263BFE19BCC51FEDD8C9F1BECD93D527E652E32F70A1C9713C725D3C6B67`
- `watchlog-agent.exe` SHA-256:
  `580FB64806150034546AF02184BF4F00905EFF81FB5272AABB1E9C0FC4F642C0`
- `watchlog-setup-ui.exe` SHA-256:
  `2493BA6B234F69556B158045469CF2551421FC961FA4C41259B17FD1AD4E7A94`
- Packaged setup-UI recorder/lifecycle self-test: **passed**
- Installer product-contract gate: **passed**
- Connector self-test, version truth and checksum gates: **passed**

The successful artifact and its exact source commit determine installer authority.
This repository (`Alkalid-security/Watchlog`) is product/database/portal context,
not the Windows installer source, unless a future release ledger explicitly changes
that authority.

## Why Build 49 supersedes Build 46

The Salman field run after earlier successful builds exposed a more important
failure than the original UI hang: a site could remain cloud-heartbeating while
the background process could no longer reach the Hikvision recorder. That created
a false "connected" impression while WatchLog received no recorder events or
snapshots.

Build 49 changes the readiness contract:

1. **No cloud-only false green.** The fresh `background-ready.json` marker is
   written only when the actual SYSTEM-launched background agent has both a
   successful WatchLog heartbeat and a recorder identity from a real recorder
   connection during startup.
2. **Recorder identity is seeded before background startup.** The setup process
   persists non-secret vendor/model/serial/driver/URL identity, allowing the
   production connector to safely rediscover the same recorder after DHCP/IP
   movement rather than spraying credentials across arbitrary LAN devices.
3. **Optional recorder-push is not an installation gate.** It cannot hold Step 06
   after the normal background Site Connector is established.
4. **Expensive recorder capability enrichment is outside startup.** Hikvision
   capability fan-out cannot delay the live collector/heartbeat critical path.
5. **Packaged UI startup was tightened.** The setup build no longer bundles all
   PySide6 modules such as QtWebEngine/3D/Charts. The actual frozen setup UI is
   about 51 MB and passed the release lifecycle gate that Build 48 failed.
6. Existing field protections remain: recorder selection state is explicit,
   discovery cannot race Continue, Hikvision login has a 30-second UI watchdog,
   stale worker results are ignored, and installer-child Ready exits back to NSIS.

## Field-validation boundary

Build 49 proves the packaged installer behavior and prevents a recorder-dead
background process from being reported as Ready during installation. It does **not**
prove that a specific customer's Hikvision `DS-7608NI-Q1` firmware/network will
remain reachable indefinitely or that its native ISAPI alert stream will emit all
desired events. The next field test should therefore verify that exact recorder's
long-running event/data path. If it fails, collect the Build 49 support bundle and
agent log before changing the release again.

## Historical lineage

- **Build 37 / 5.0.0** — frozen historical lineage anchor:
  source `cffd32a47c70fc1141efef7e30315a5c4c844c57`, Windows Release
  #37 / `35695121845`.
- **Build 39 / 5.0.1** — recorder selection/UI regression successor.
- **Build 41 / 5.0.2** — Step 06/login-watchdog successor.
- **Build 46 / 5.0.3** — installer-child lifecycle and Hikvision integration
  hardening predecessor to Build 49.
- **Build 49 / 5.0.6** — current successful Windows baseline.

## Mandatory rule for future installer work

Before changing, diagnosing, or claiming a fix for the Windows installer:

1. identify the exact artifact/build the customer is running;
2. resolve that artifact to repository, branch, source SHA and Windows Release run;
3. make the fix in that authoritative Watchlog-2 lineage;
4. produce a successful Windows Release artifact;
5. record the new artifact name/id/digest and executable hashes here;
6. only then mirror relevant context or code into this main repository.

Do **not** infer installer authority from repository name, branch recency, a similar
file path, or a source commit that has not produced a successful Windows artifact.

## CI note

Windows Release #49 passed all Windows packaging/release gates. The repository's
broader CI can still be red for unrelated product/backend tests; that does not turn
a failed Windows Release into a success, and it does not invalidate a successful
Windows Release artifact. Treat the Windows Release workflow and its artifact
identity as the installer release authority.
