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

- successor source commit:
  `aaae5462f432ffe4fe3080ed97d05f3925dc1c38`
- successor product version: `5.0.1`
- Windows Release run **#38**: `35720403950`
- status at the time this context was written: queued / building

Build 37 remains the authoritative latest installer until Build 38 completes
successfully and its generated artifact is recorded as the new baseline.

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
