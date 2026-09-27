# Windows installer source of truth

For current live tenant/site/runtime context, also read `docs/production/CURRENT_LIVE_CONTEXT_2026-09-28.md`.

**Purpose:** prevent release work, support analysis, or AI-assisted changes from
patching the wrong WatchLog repository.

## Current authoritative successful Windows baseline

As of 2026-09-28, the latest successful Windows installer is:

- Product: WatchLog Windows Site Connector
- Build: **76**
- Product version: **5.0.21**
- Authoritative repository: `visioninfinity0007-droid/Watchlog-2`
- Authoritative branch: `build/site-connector-v5-watchlog2`
- Source commit: `26b036a654446b3e8c262b2f476118f2e2f41916`
- Windows Release workflow: **#76**, run id `36346424676`
- Workflow conclusion: **success**
- Artifact: `WatchLog-Windows-76`
- Artifact id: `10941445308`
- Artifact size: `66579742` bytes
- GitHub artifact digest:
  `sha256:0fdf73bc6421291e58413330ac73b439a6256f59c95227cc4dfd1e4c6eea54b5`
- `WatchLog-Setup.exe` SHA-256:
  `3A5102570B24E55062800D28FEE78A5E16028105420A10310FA7BCD90FAFD8C0`
- `watchlog-agent.exe` SHA-256:
  `466EE6894214DD4FA427161D92EB24CC31FF0F9701DFFFC79C4D343219A9FBC8`
- `watchlog-setup-ui.exe` SHA-256:
  `CDEB876DC7429D535E8A33FBB2E1F253BC23B64211DF8FDB291D7FC0853F5862`
- Packaged setup-UI lifecycle/selection self-test: **passed**
- Packaged connector self-test and version truth: **passed**
- Installer checksum verification: **passed**
- Hikvision archive/download + recovery contracts: **passed**
- Recorder-push parser/liveness contracts: **passed**
- Generic Dahua AlarmServer safety contract: **passed**
- Durable spool-overflow-to-archive-recovery contract: **passed**
- Packaged file/runtime version truth: **5.0.21 / 5.0.21**

Build 76 supersedes Build 72 for new field installs. It carries forward the
Build-72 first-pass discovery and recorder-login fixes and adds the two fixes
required by the current live sites:

1. ONVIF camera inventory is normalized by physical video source so recorder
   MainStream/SubStream encoding profiles are not exposed as separate cameras.
   The cloud sync remains backward-compatible with older deployed agents and
   preserves historical transport-profile rows rather than deleting evidence.
2. Hikvision incident footage retrieval now searches recorded media first and
   uses the recorder-returned playback URI, with bounded GET/POST compatibility
   for firmware families that differ on `/ISAPI/ContentMgmt/download`.

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

Build 76 is the correct field installer for both Dahua and Hikvision.

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
- **Build 61 / 5.0.12** — production recovery wiring + safe PC-off semantics.
- **Build 69 / 5.0.17** — fast native-auth/login regression baseline; intermittent near 30-second field timeout.
- **Build 70 / 5.0.18** — duplicate-login/Step-06 fix; 24-second login watchdog proved too aggressive in field use.
- **Build 71 / 5.0.19** — retryable login UI + shorter Dahua identity path; 22-second watchdog still false-timed out.
- **Build 72 / 5.0.20** — deterministic first-pass discovery + bounded same-endpoint recorder auth fallback + visible progress.
- **Build 76 / 5.0.21** — current successful Windows baseline; physical-camera ONVIF de-duplication + legacy-profile compatibility + Hikvision recorded-footage retrieval hardening.

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

Windows Release #76 passed all Windows packaging/release gates. The repository's
broader CI can still be red for unrelated product/backend tests; that does not turn
a failed Windows Release into a success, and it does not invalidate a successful
Windows Release artifact. Treat the Windows Release workflow and its artifact
identity as the installer release authority.


## Push-bridge production handoff (carried forward through Build 76)

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

Field acceptance for Build 76:
1. install/upgrade one Dahua and one Hikvision site with Build 76;
2. prove both recorders connect and a recent bounded historical retrieval works;
3. create an Internet/PC/recorder connectivity gap and prove missed data is
   recovered/backfilled after connectivity returns;
4. redeploy the Coolify push bridge from main;
5. on Hikvision, turn the Windows PC off and prove the NVR-originated heartbeat
   continues to advance the recorder-push virtual agent;
6. on generic Dahua, do **not** modify AlarmServer for WatchLog. PC-off direct
   push remains unsupported unless that firmware's HTTP callback is separately
   hardware-proven.
