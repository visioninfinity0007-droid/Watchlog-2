# Windows installer source of truth

**Purpose:** prevent release work, support analysis, or AI-assisted changes from
patching the wrong WatchLog repository.

## Current authoritative successful Windows baseline

As of 2026-09-25, the latest successful Windows installer is:

- Product: WatchLog Windows Site Connector
- Build: **56**
- Product version: **5.0.8**
- Authoritative repository: `visioninfinity0007-droid/Watchlog-2`
- Authoritative branch: `build/site-connector-v5-watchlog2`
- Source commit: `9330c297f12a7b387059d1356b8f4fd113b4336f`
- Windows Release workflow: **#56**, run id `36133615686`
- Workflow conclusion: **success**
- Artifact: `WatchLog-Windows-56`
- Artifact id: `10862408445`
- Artifact size: `66559271` bytes
- GitHub artifact digest:
  `sha256:2160eab4240b2e6c43bf3a30f6958b7e73b00494efead9decc6847fe01da5232`
- `WatchLog-Setup.exe` SHA-256:
  `F0682D449C1E08A6AC687D714E6E2372F277F635071B1C1E9D0C4A870B61E670`
- `watchlog-agent.exe` SHA-256:
  `24853EEBFD227544C3F836B604EC3D238066F729D7762AA023F1ACF5AC752185`
- `watchlog-setup-ui.exe` SHA-256:
  `B9167034E716D05757BE2753549C3E5A4999799BDA02CDC4F82723F508313D19`
- Packaged setup-UI lifecycle/self-test: **passed**
- Packaged connector self-test and checksum gates: **passed**
- Hikvision archive/download + recovery wiring + push-bridge contracts: **passed**

Build 56 supersedes Build 50. It adds the Hikvision archive/video path,
recorder-backed outage detection, resumable dual-vendor recovery, asynchronous
PC-off recorder-push provisioning, end-to-end push delivery verification, and
Hikvision 30-second direct heartbeat/broken-link retransmission configuration.

The successful artifact and its exact source commit determine installer authority.
This repository (`Alkalid-security/Watchlog`) remains product/database/portal/
push-bridge context, not the Windows installer source.

## Why Build 50 supersedes Build 49

Build 49 prevented a false green: the installer could not say Ready unless the
SYSTEM-launched background process had both reached WatchLog and identified the
recorder. Build 50 addresses the remaining field failure itself: **Hikvision was
connected but was not producing usable WatchLog camera data and independent
background recorder sessions could mark the NVR unreachable.**

Build 50 therefore:

1. keeps Hikvision native alert monitoring in bounded 45-second slices;
2. captures one rotating camera still between slices using the **same**
   authenticated recorder session;
3. uploads that still as a `visual_sample`, which enters the existing
   server-side snapshot visual-review queue;
4. makes Hikvision health reuse fresh live-collector truth instead of opening a
   competing Digest-auth recorder session;
5. only gives positive per-camera health after a real JPEG sample has been
   obtained for that camera;
6. disables unvalidated Hikvision archive recovery so it cannot create another
   simultaneous recorder session beside live monitoring;
7. preserves Build 49's recorder-backed installation-readiness gate and all
   earlier UI/login/Step-06 fixes.

The broader repository CI still has an unrelated native-NVR AI/backend contract
failure after the recorder regression/compile stages pass. Windows Release #50
itself passed and produced the artifact above.

## Field-validation boundary

Build 50 is the correct next field installer for both the Dahua and Hikvision
sites. Dahua behavior is unchanged by this runtime change. On Hikvision, the
next field run should verify that camera samples begin arriving after setup and
that the site no longer drifts into `nvr_unreachable` while the collector is
healthy. The code and packaged release now cover this path, but the exact
customer recorder remains the final hardware confirmation.

## Historical lineage

- **Build 37 / 5.0.0** — frozen historical lineage anchor:
  source `cffd32a47c70fc1141efef7e30315a5c4c844c57`, Windows Release
  #37 / `35695121845`.
- **Build 39 / 5.0.1** — recorder selection/UI regression successor.
- **Build 41 / 5.0.2** — Step 06/login-watchdog successor.
- **Build 46 / 5.0.3** — installer-child lifecycle and Hikvision integration
  hardening predecessor to Build 49.
- **Build 49 / 5.0.6** — recorder-backed readiness predecessor.
- **Build 50 / 5.0.7** — current successful Windows baseline; Hikvision single-session live monitoring + rotating visual samples.

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

Windows Release #50 passed all Windows packaging/release gates. The repository's
broader CI can still be red for unrelated product/backend tests; that does not turn
a failed Windows Release into a success, and it does not invalidate a successful
Windows Release artifact. Treat the Windows Release workflow and its artifact
identity as the installer release authority.


## Build 56 push-bridge production handoff

The Windows installer source is Watchlog-2, but the **live Coolify push bridge**
is built from this repository's `main` branch, `/prototype/bridge`.

Bridge hardening source commit:
`d57e600ff57eec0c849c6a818a8087802024d06e`.

That main-repo change makes Hikvision `heartBeat` liveness-only (never a fake
incident), authenticates/records recorder liveness before vendor event parsing,
and contains the production `wl_agent_push_status` migration source. The live
database RPC was already applied on 2026-09-25.

**Deployment is a separate gate.** The Coolify runbook explicitly states that
there is no GitHub auto-deploy. PC-off direct reporting must not be called
production-active until `watchlog-push-bridge` is redeployed from main and a
real recorder POST advances `push_sources.last_push_at`.

Field acceptance for Build 56:
1. install/upgrade the Dahua and Hikvision PCs with Build 56;
2. prove both recorders connect and remain reachable;
3. request a bounded historical clip from each recorder;
4. create a connectivity/PC gap and prove a recovery interval is backfilled;
5. redeploy the Coolify push bridge from main;
6. on Hikvision, turn the PC off and verify the NVR's 30-second HTTP heartbeat
   advances the recorder-push virtual agent; on Dahua, verify direct alarm-server
   delivery when the specific firmware supports it.
