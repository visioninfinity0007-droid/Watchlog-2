# WatchLog Windows Build 37 — authoritative baseline

**Status:** frozen source baseline for the latest field installer as of 2026-09-22.

## Canonical identity

- Repository: `visioninfinity0007-droid/Watchlog-2`
- Build branch: `build/site-connector-v5-watchlog2`
- Source commit: `cffd32a47c70fc1141efef7e30315a5c4c844c57`
- Product version in that source: `5.0.0`
- GitHub Windows Release run: **#37**, run id `35695121845`
- Release conclusion: **success**
- Artifact: `WatchLog-Windows-37`
- Artifact id: `10680586104`
- Artifact size: `271634225` bytes
- GitHub artifact digest: `sha256:12349dc7796e0efd5e4762edde26d9f496d46413a9a49d4f43bd893250d2e71c`

This commit is the baseline even if another repository contains newer-looking
installer code. The shipped artifact determines authority, not repository age,
branch naming, or a later refactor.

## Build-forward rule

All Windows installer corrections after Build 37 must:

1. start from Build 37's source commit above or a verified descendant in this repository;
2. preserve Build 37 as a frozen historical reference rather than rewriting it;
3. run the Windows release workflow from `build/site-connector-v5-watchlog2`;
4. record the successor source SHA, release run number, artifact name and digest;
5. update the main WatchLog repository's installer context document so operators and AI
   assistants do not accidentally patch a different code line.

`Alkalid-security/Watchlog` is **not** the authoritative Windows-installer
source for this lineage. It may contain mirrored/product code, but installer
changes must not be treated as release fixes until they are applied and built
from Watchlog-2.

## Field defects found on Build 37

Two setup defects were confirmed from the field screenshots:

- a discovered recorder could look highlighted while the local-address field
  remained empty, causing Continue to reject a visibly selected recorder;
- optional recorder-push / PC-free integration could hold the Connecting screen
  for the old 90-second child timeout after the core site connection was
  already established.

The successor change makes the first discovered recorder a real selection,
allows Continue to fall back to the highlighted row, and caps optional recorder
integration to a short bounded window. The successor product version is
`5.0.1`.

## Non-negotiable verification

A replacement installer is not "latest" merely because source was changed.
The next authoritative baseline is established only after its Windows Release
run completes successfully and the generated artifact identity is recorded
here (or in a successor baseline ledger).
