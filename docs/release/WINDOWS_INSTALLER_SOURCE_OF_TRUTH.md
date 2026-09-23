# Windows installer source of truth

**Purpose:** prevent release work, support analysis, or AI-assisted changes from
patching the wrong WatchLog repository.

## Authoritative field baseline

As of 2026-09-23, the authoritative latest shipped Windows installer baseline is:

- Product: WatchLog Windows Site Connector
- Build: **46**
- Product version: **5.0.3**
- Authoritative repository: `visioninfinity0007-droid/Watchlog-2`
- Authoritative branch: `build/site-connector-v5-watchlog2`
- Source commit: `e39cf1cc04c7ab52f115484b98f926b06cb85c71`
- Windows Release workflow run: **#46**, run id `35855322767`
- Workflow conclusion: **success**
- Artifact: `WatchLog-Windows-46`
- Artifact id: `10747667489`
- Artifact size: `271650885` bytes
- GitHub artifact digest:
  `sha256:f8d2be2f59a258d562e7cec51ca71d684073eb5c4fb9e8312ce588220bf58298`
- Packaged setup-UI lifecycle/self-test: **passed**
- Recorder field-regression gate: **passed**
- Installer/NSIS contract: **passed**

The shipped artifact and its source commit determine installer authority. This
repository (`Alkalid-security/Watchlog`) is **not** the source baseline for
Build 46, even where files look similar or newer.

Build 37 remains the frozen historical lineage anchor. Build 39 was an
intermediate successful successor, but field use exposed further lifecycle and
Hikvision-integration gaps. Build 46 supersedes both for future diagnosis and
build-forward work.

## Field issues closed in Build 46

Build 46 incorporates the full set of field corrections from the Salman runs:

1. **Ready/installer deadlock:** NSIS uses `ExecWait`, so a Ready window that
   stayed open left the parent installer apparently frozen. NSIS now launches
   setup with `--installer-child`; after a proven connection, Ready is shown
   briefly and the setup process exits code 0 automatically so NSIS continues.
2. **Step 06 over-gating:** the broad acceptance suite is post-install
   diagnostics, not another installer gate once recorder/site/cameras/heartbeat
   and the background agent are proven.
3. **Hikvision login ambiguity:** RTSP-only candidates are re-identified after
   web-port rescue; Hikvision ISAPI is probed directly; ISAPI auth rejection no
   longer becomes a generic "bad password" result; WatchLog tries ONVIF where
   appropriate and gives an explicit integration-service/authentication action
   when the normal browser works but the API does not.
4. **UI hangs:** recorder login has a 30-second UI watchdog and stale late worker
   results cannot move the wizard after a retry.
5. **Release verification:** the frozen packaged setup UI is executed in
   installer-child mode during the Windows release gate, and the recorder field
   regressions run before the unrelated backend contract that is currently red.

The known red overall CI status is caused by the separate Native-NVR
incident-evidence contract, not these installer field regressions. The Windows
Release #46 job itself passed all release gates and uploaded the artifact.

## Mandatory rule for future installer work

Before changing, diagnosing, or claiming a fix for the Windows installer:

1. identify the exact artifact/build the customer is running;
2. resolve that artifact to its repository, branch, source SHA and release run;
3. make the fix in that authoritative repository/lineage;
4. build a successor from that exact baseline;
5. only then mirror relevant context or code back into this main repository.

Do **not** infer installer authority from repository name, recency, a similar
file path, or the `main` branch.

## Relationship to this repository

This main WatchLog repository remains useful for product/database/portal work and
may contain mirrored installer code, tests or historical release documents.
Those copies are context, not release authority, unless a future release ledger
explicitly re-establishes this repository as the source of a shipped installer.
