# RC8 — first field-proven 0.3.4 Dahua baseline (FROZEN)

**Do not rebuild, re-sign, or overwrite RC8.** This exact build is the field-proven reference for
0.3.4 on Dahua hardware. Later fixes (CAMERA_SYNC_PARTIAL raise, P1 connectivity items, Authenticode
signing) ship in the NEXT RC — never by replacing RC8.

## Identity
- Branch / SHA: `fix/installer-0.3.4-stabilization` @ **`72647a1eda15`**
- Installer SHA-256: **`C7848FE5EC62A1A7F720EB7B75A52B7BEA1B82E794C84556D567B0E3FCE0AFDB`** (328.8 MB)
- RC build: run **`34108372696`** (#8), non-production, **unsigned**
- CI: `34107917964` green · Windows Security Gate: `34107918054` green
- Published (versioned, NOT `/latest/`): `…/downloads/watchlog/0.3.4-rc8/WatchLog-Setup.exe`

## Field test — 2026-09-07 (~10:49 UTC) — PASS
On a real **Dahua DH-XVR1B08-I** (`dahua-cgi`) at a live client site. Verified read-only in production
(customer-identifying detail kept out of this repo; see the internal forensic record):
- a new **0.3.4** agent enrolled, bound to the correct tenant + site;
- the previously-**unused** enrollment code is now **consumed** by that exact agent;
- **8/8 cameras** (channels 1–8) — no missing, no duplicates, no orphans;
- agent **heartbeating live** at verification; runtime **ingesting `person` events**; **0 error events**.

Full path proven end-to-end on the client's physical environment:
installer → recorder auth → channel discovery → enrollment → camera reconciliation → persistent
heartbeat → event ingestion. This is the fix for the stale-local-state defect (setup previously skipped
enrollment against a defunct agent).

## Governance
- `/latest/` stays **0.3.1**; production promotion is blocked until Authenticode signing + the remaining
  acceptance (reboot resilience, wrong-credential error classification) are complete.
- The exact `28000`-vs-FK subtype of the ORIGINAL failure was not (and need not be) reproduced — the
  stale-identity defect and the successful repair path are proven; the fix covers both subtypes.
