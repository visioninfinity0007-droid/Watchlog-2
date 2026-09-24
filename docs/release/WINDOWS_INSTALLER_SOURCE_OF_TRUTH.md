# Windows installer source of truth

**Purpose:** prevent release work, support analysis, or AI-assisted changes from
patching the wrong WatchLog repository.

## Current authoritative successful Windows baseline

As of 2026-09-24, the latest successful Windows installer is:

- Product: WatchLog Windows Site Connector
- Build: **50**
- Product version: **5.0.7**
- Authoritative repository: `visioninfinity0007-droid/Watchlog-2`
- Authoritative branch: `build/site-connector-v5-watchlog2`
- Source commit: `4d7533d7525b2cecffa47d566d277d92e7b30054`
- Windows Release workflow: **#50**, run id `36038016858`
- Workflow conclusion: **success**
- Artifact: `WatchLog-Windows-50`
- Artifact id: `10825154761`
- Artifact size: `66535907` bytes
- GitHub artifact digest:
  `sha256:7260ed7c441ea5ac4afc3aa5d7986f9fd38b16d464669db8bf7e69ad19f6a932`
- `WatchLog-Setup.exe` SHA-256:
  `DE6B681F306C2B795ADA85A4657D1480B2C92E90C2DBF87FF2F45D5467E7EE77`
- `watchlog-agent.exe` SHA-256:
  `553ADBB92B5ACA18F07DA2B72C999DDE3338EAE56541A2BAD569774B9A9FF21D`
- `watchlog-setup-ui.exe` SHA-256:
  `436CDCCAB0AD90A2C80F6BE7C2976A0AF9A96ED8573A60882241CDC284DC20FA`
- Packaged setup-UI lifecycle/self-test: **passed**
- Packaged connector self-test and checksum gates: **passed**
- Python compile + recorder field-regression tests: **passed**

The successful artifact and its exact source commit determine installer authority.
This repository (`Alkalid-security/Watchlog`) remains product/database/portal
context, not the Windows installer source.

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
