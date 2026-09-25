# Windows installer source of truth

**Purpose:** prevent release work, support analysis, or AI-assisted changes from
patching the wrong WatchLog repository.

## Current authoritative successful Windows baseline

As of 2026-09-25, the latest successful Windows installer is:

- Product: WatchLog Windows Site Connector
- Build: **61**
- Product version: **5.0.12**
- Authoritative repository: `visioninfinity0007-droid/Watchlog-2`
- Authoritative branch: `build/site-connector-v5-watchlog2`
- Source commit: `78569edba4f125eb8c02fd6dc9fecdc9c7c669f0`
- Windows Release workflow: **#61**, run id `36136000657`
- Workflow conclusion: **success**
- Artifact: `WatchLog-Windows-61`
- Artifact id: `10865515882`
- Artifact size: `66562912` bytes
- GitHub artifact digest:
  `sha256:5344d2b0efbfc1b6f438b09f94e9c0546f239f05f79631d9483320e3251374d7`
- `WatchLog-Setup.exe` SHA-256:
  `715D3586521F0707E71FA7758FEFF51596EF234B548CBFCDBAE074F8E8BE199A`
- `watchlog-agent.exe` SHA-256:
  `9FF3DDF44B1D72F5E5EF5F5E8943D5C0CF71A6A97D221108780BD9F037F77DA5`
- `watchlog-setup-ui.exe` SHA-256:
  `C0B257EA2233F8AC4E3A8AEA4DC2AA520C492925A67684D614825A5A455629AC`
- Packaged setup-UI lifecycle/selection test: **passed**
- Packaged connector self-test: **passed**
- Hikvision archive/download contracts: **passed**
- Runtime recovery wiring, including real collector recorder truth: **passed**
- Recorder push parser/liveness contracts: **passed**
- Generic Dahua AlarmServer safety contract: **passed**
- Durable spool-overflow-to-archive-recovery contract: **passed**
- Packaged file/runtime version truth: **5.0.12 / 5.0.12**

Build 61 supersedes Build 56. It is the first baseline in this lineage where the
automatic recovery path is wired to the actual live recorder session, a long
cloud outage that exceeds the local spool safety cap becomes a durable recorder
recovery interval, generic Dahua AlarmServer is fail-closed instead of being
misused as an HTTPS webhook, and PC-off verification requires a fresh recorder
POST rather than accepting historical `last_push_at`.

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

Build 61 is the correct field installer for both Dahua and Hikvision.

- **Dahua:** live event monitoring + local durable spool + recorder archive
  search/download + automatic gap recovery are the production path. Generic
  Dahua `AlarmServer` is a proprietary alarm-centre interface, not a generic
  HTTP webhook, so WatchLog intentionally does **not** rewrite it for PC-off
  delivery. A Dahua site may only claim direct PC-off cloud push if that exact
  firmware exposes a separately proven HTTP callback mechanism.
- **Hikvision:** the package includes live ISAPI monitoring, bounded archive/video
  retrieval, automatic gap recovery and HTTP-host push configuration requesting
  30-second NVR heartbeats plus broken-link retransmission. End-to-end PC-off
  status is only verified after a fresh recorder POST reaches WatchLog.
- **Any site:** a physical field acceptance still has to prove one bounded
  historical retrieval and one induced gap/recovery cycle on the actual recorder.


## Historical lineage

- **Build 37 / 5.0.0** — frozen historical lineage anchor:
  source `cffd32a47c70fc1141efef7e30315a5c4c844c57`, Windows Release
  #37 / `35695121845`.
- **Build 39 / 5.0.1** — recorder selection/UI regression successor.
- **Build 41 / 5.0.2** — Step 06/login-watchdog successor.
- **Build 46 / 5.0.3** — installer-child lifecycle and Hikvision integration
  hardening predecessor to Build 49.
- **Build 49 / 5.0.6** — recorder-backed readiness predecessor.
- **Build 50 / 5.0.7** — Hikvision single-session live-monitoring predecessor.
- **Build 56 / 5.0.8** — dual-vendor archive/recovery predecessor.
- **Build 61 / 5.0.12** — current successful Windows baseline; production recovery wiring + safe PC-off semantics.

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

Windows Release #61 passed all Windows packaging/release gates. The repository's
broader CI can still be red for unrelated product/backend tests; that does not turn
a failed Windows Release into a success, and it does not invalidate a successful
Windows Release artifact. Treat the Windows Release workflow and its artifact
identity as the installer release authority.


## Build 61 push-bridge production handoff

The Windows installer source is Watchlog-2, but the **live Coolify push bridge**
is built from this repository's `main` branch, `/prototype/bridge`.

Bridge hardening source commit:
`d57e600ff57eec0c849c6a818a8087802024d06e`.

That main-repo change treats Hikvision `heartBeat` as liveness only, records
token-authenticated recorder liveness before vendor event parsing, retains
Dahua payload parsing for firmware/gateways that truly POST HTTP, and contains
the production `wl_agent_push_status` migration source. The live database RPC
was applied on 2026-09-25.

**Deployment is still a separate gate.** The Coolify runbook states there is no
GitHub auto-deploy. Hikvision PC-off direct heartbeat must not be called
production-active until `watchlog-push-bridge` is redeployed from main and a
fresh recorder POST advances `push_sources.last_push_at`.

Field acceptance for Build 61:
1. install/upgrade one Dahua and one Hikvision site with Build 61;
2. prove both recorders connect and a recent bounded historical retrieval works;
3. create an Internet/PC/recorder connectivity gap and prove missed data is
   recovered/backfilled after connectivity returns;
4. redeploy the Coolify push bridge from main;
5. on Hikvision, turn the Windows PC off and prove the NVR-originated heartbeat
   continues to advance the recorder-push virtual agent;
6. on generic Dahua, do **not** modify AlarmServer for WatchLog. PC-off direct
   push remains unsupported unless that firmware's HTTP callback is separately
   hardware-proven.
