# WatchLog Windows — Mirror Field Handoff Reconciliation (2026-09-28)

Status: **SUPERSEDED MIRROR HANDOFF — preserved for reconciliation, not release authority.**

Source mirror commit:
`visioninfinity0007-droid/Watchlog-2@d3b2d0a10a8f4be490b98e9e28dcb208e6f6426b`

The mirror document created at that commit called Build 76 / 5.0.21 the current field installer.
That statement is historical and must not override canonical release authority.

Current authority:
- product/source: `Alkalid-security/Watchlog/main`
- release ledger: `docs/release/WINDOWS_INSTALLER_SOURCE_OF_TRUTH.md`
- current live handoff: `docs/production/CURRENT_LIVE_CONTEXT_2026-09-28.md`
- authoritative source version: 5.0.24
- field-proven discovery/connectivity baseline: Build 69 / 5.0.17 until a later exact authoritative artifact passes physical Hikvision + Dahua acceptance.
- existing-site upgrade authority: `WatchLog-Repair-Upgrade.exe` staged bootstrap in 5.0.24; full Setup is for new-site/new-PC onboarding.

## Mirror facts reconciled into canonical

The useful field facts from the mirror handoff are retained in canonical context/release documentation:

1. **ONVIF physical-camera normalization**
   - encoding profiles must not be exposed as separate physical cameras;
   - Al-Khalid retains 8 canonical physical cameras plus hidden historical profile rows;
   - historical evidence is preserved.

2. **Hikvision historical footage compatibility**
   - search-first recorded-media retrieval and recorder-returned playback URI handling were part of the Build-76 lineage;
   - canonical 5.0.24 owns the bounded archive/gap recovery contract plus the staged existing-site Repair/Upgrade bootstrap;
   - exact physical-recorder acceptance is still required before claiming a hardware path field-proven.

3. **Al-Khalid mapping boundary**
   - Reception / Director's Office / Armory Gate / Admin Entrance remain valid business areas;
   - contradictory legacy stream-profile labels must not be rebound to physical channels until the views are visually re-confirmed.

4. **HASCO Steel**
   - Hikvision DS-7608NI-Q1, eight physical cameras, business camera roles governed from canonical product context.

5. **Chai Wala**
   - Build-69 lineage proved the request/worker transport path but did not prove video-byte export on that recorder;
   - do not claim clip/archive extraction field-proven until an accepted authoritative build returns real bounded footage from the physical recorder.

6. **Field acceptance guardrails**
   - verify physical camera identity, still acquisition, bounded archive retrieval, real bytes, recovery after a gap, and exact recorder firmware;
   - never treat CI/package success alone as field proof.

## Rule

Do not copy product work back into Watchlog-2 and then treat the mirror as authority.
All new product/runtime/reporting work belongs in canonical `Alkalid-security/Watchlog/main`.


## 5.0.24 installer context supersession note

This mirror handoff is historical. Current installer truth is in:

- `docs/release/WINDOWS_INSTALLER_SOURCE_OF_TRUTH.md`
- `docs/release/EXISTING_SITE_REPAIR_UPGRADE_5_0_24.md`
- `docs/runbooks/WINDOWS_EXISTING_SITE_REPAIR_UPGRADE.md`

Current product behavior:
- `WatchLog-Setup.exe` is the new-site/new-PC path;
- `WatchLog-Repair-Upgrade.exe` is the existing enrolled-site path;
- staged passive validation runs before the old Agent is stopped;
- staged recorder validation runs before installed files are replaced;
- health commit requires fresh heartbeat, recorder observation and real updater polling;
- rollback success requires proof that the previous Agent actually restarted;
- `remote_update_v1` is evidence-based, not code-presence-based.

Recent Al-Khalid Head Office field evidence:
- a full-installer candidate failed post-install health and rolled back to 5.0.19;
- 5.0.19 remains online-update incapable;
- this is the field case that triggered the permanent staged Repair/Upgrade model.

Windows 5.0.24 validation remains in
`visioninfinity0007-droid/Watchlog-2:fix/existing-site-repair-upgrader-v5`.
No exact 5.0.24 artifact should be called field-ready until Windows Release and the
controlled Al-Khalid acceptance are recorded.
