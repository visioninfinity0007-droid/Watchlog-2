# Windows installer source of truth

**Purpose:** prevent release work, support analysis, or AI-assisted changes from
patching the wrong WatchLog repository.

## Authoritative field baseline

As of 2026-09-22, the authoritative latest shipped Windows installer baseline is:

- Product: WatchLog Windows Site Connector
- Build: **37**
- Product version: **5.0.0**
- Authoritative repository: `visioninfinity0007-droid/Watchlog-2`
- Authoritative branch: `build/site-connector-v5-watchlog2`
- Source commit: `cffd32a47c70fc1141efef7e30315a5c4c844c57`
- Windows Release workflow run: **#37**, run id `35695121845`
- Workflow conclusion: **success**
- Artifact: `WatchLog-Windows-37`
- Artifact id: `10680586104`
- Artifact size: `271634225` bytes
- GitHub artifact digest:
  `sha256:12349dc7796e0efd5e4762edde26d9f496d46413a9a49d4f43bd893250d2e71c`

The shipped artifact and its source commit determine installer authority. This
repository (`Alkalid-security/Watchlog`) is **not** the source baseline for
Build 37, even where files look similar or newer.

## Current build-forward work

Build 37 exposed two field setup defects:

1. recorder discovery could visually highlight a row while the address field
   remained empty, causing Continue to reject the visible selection;
2. optional recorder-push / PC-free integration could keep the Connecting screen
   occupied by the old long timeout after the core site connection was already
   established.

Those corrections were applied **in Watchlog-2**, directly on the authoritative
build branch as a descendant of Build 37:

- first successor source commit:
  `aaae5462f432ffe4fe3080ed97d05f3925dc1c38`
- UI-confirmation successor commit:
  `a635ef16b7b7f50bfec1bc65a7f255e92727a80c`
- successor product version: `5.0.1`
- authoritative candidate Windows Release run **#39**: `35721540889`
- paired CI run **#146**: `35721540913`
- status at the time this context was written: queued / building

The second audit confirmed the recorder-selection defect is a Qt/UI state issue
and found an additional UI race: Recorder Continue remained enabled while
asynchronous discovery was still running. The candidate now disables Continue
during discovery, directly maps row clicks to the address field, only
auto-selects when exactly one recorder is found, and embeds a
`--ui-selftest` in the frozen setup executable. Both CI and the Windows
release workflow execute this packaged UI self-test.

**Build 39 is now the authoritative latest Windows installer baseline.**

- Product version: `5.0.1`
- Authoritative repository: `visioninfinity0007-droid/Watchlog-2`
- Branch: `build/site-connector-v5-watchlog2`
- Source commit: `a635ef16b7b7f50bfec1bc65a7f255e92727a80c`
- Windows Release run: **#39**, run id `35721540889`
- Workflow conclusion: **success**
- Artifact: `WatchLog-Windows-39`
- Artifact id: `10692431328`
- Artifact size: `271640486` bytes
- GitHub artifact digest:
  `sha256:80435f10108c12bb5f36f31891382d65eaf469f068f2f71f5b06683ca8f9a36c`
- Packaged recorder-selection UI self-test: **passed**

Build 39 supersedes Build 37 for all future installer diagnosis and build-forward work.

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
