# Windows installer source of truth

**Purpose:** prevent release work, support analysis, or AI-assisted changes from
patching the wrong WatchLog repository.

## Current authoritative successful Windows baseline

As of 2026-09-26, the latest successful Windows installer is:

- Product: WatchLog Windows Site Connector
- Build: **72**
- Product version: **5.0.20**
- Authoritative repository: `visioninfinity0007-droid/Watchlog-2`
- Authoritative branch: `build/site-connector-v5-watchlog2`
- Source commit: `abd098a5473473b7fb61c4aff75bfd4919072824`
- Windows Release workflow: **#72**, run id `36256857431`
- Workflow conclusion: **success**
- Artifact: `WatchLog-Windows-72`
- Artifact id: `10911380895`
- Artifact size: `66573318` bytes
- GitHub artifact digest:
  `sha256:1eca7d880b320b621d5f455054ddbce05026ac74c874e7293b9705ed481ec2a8`
- `WatchLog-Setup.exe` SHA-256:
  `7AF6192EFD818BF857218AA65B5263D919A4E1B9D95B7835BAC94514325F20E9`
- `watchlog-agent.exe` SHA-256:
  `7557AD69B1AF1A26A6060331D7A9A8612BDC04E40C7EC52669113F4A3465A2EF`
- `watchlog-setup-ui.exe` SHA-256:
  `5CC1472CC7D1E57964D8D92604BE3EBF6858975DC41E29E3C00798B360CECDEC`
- Packaged setup-UI lifecycle/selection self-test: **passed**
- Packaged connector self-test and version truth: **passed**
- Installer checksum verification: **passed**
- Hikvision archive/download + recovery contracts: **passed**
- Recorder-push parser/liveness contracts: **passed**
- Generic Dahua AlarmServer safety contract: **passed**
- Durable spool-overflow-to-archive-recovery contract: **passed**
- Packaged file/runtime version truth: **5.0.20 / 5.0.20**

Build 72 supersedes Builds 69-71 for field testing. Build 69 (`5.0.17`) still had
an intermittent recorder-login path near the 30-second UI watchdog. Build 70
reduced that watchdog to 24 seconds and Build 71 to 22 seconds, turning the same
valid-but-slow path into a consistent false timeout on the field Dahua recorder.

Build 72 fixes the underlying path rather than merely extending the timer:
recorder discovery retries until an actual recorder signature is found even when
an unrelated router/web device answered first; scan concurrency is bounded and
visible in the UI; the web endpoint actually proven by fingerprinting is retained;
vendor-native login remains first but ONVIF fallback is tried on that same endpoint
before spending another full timeout on a second native web port; Dahua and ONVIF
LAN sessions bypass proxy/PAC settings; Dahua no longer performs a blind Basic-auth
retry after a failed Digest exchange; and the login page restores a retryable
30-second safety watchdog with visible progress.
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

Build 72 is the correct field installer for both Dahua and Hikvision.

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
- **Build 72 / 5.0.20** — current successful Windows baseline; deterministic first-pass discovery + bounded same-endpoint recorder auth fallback + visible progress.

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

Windows Release #72 passed all Windows packaging/release gates. The repository's
broader CI can still be red for unrelated product/backend tests; that does not turn
a failed Windows Release into a success, and it does not invalidate a successful
Windows Release artifact. Treat the Windows Release workflow and its artifact
identity as the installer release authority.


## Push-bridge production handoff (carried forward through Build 72)

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

Field acceptance for Build 72:
1. install/upgrade one Dahua and one Hikvision site with Build 72;
2. prove both recorders connect and a recent bounded historical retrieval works;
3. create an Internet/PC/recorder connectivity gap and prove missed data is
   recovered/backfilled after connectivity returns;
4. redeploy the Coolify push bridge from main;
5. on Hikvision, turn the Windows PC off and prove the NVR-originated heartbeat
   continues to advance the recorder-push virtual agent;
6. on generic Dahua, do **not** modify AlarmServer for WatchLog. PC-off direct
   push remains unsupported unless that firmware's HTTP callback is separately
   hardware-proven.
