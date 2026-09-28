# WatchLog Recorder Discovery Field Reliability Status

This file supersedes the old 0.4.3-only discovery note.

## Golden field baseline

Build **69 / 5.0.17** is the current field-proven Windows discovery/connectivity baseline.

Source SHA:

`811d378e3a7556047f294bb128b8caf45a295469`

Build 69 successfully proved real recorder discovery/connectivity on site and therefore defines
the minimum acceptable reach for future builds.

Field baseline characteristics that must not regress:

- up to **8 local /24 networks**;
- Hikvision native/control discovery;
- Dahua native/control discovery;
- RTSP/web compatibility ports;
- manual recorder-IP path;
- bounded recorder authentication.

## Build 74 field failure

Build 74 passed packaging but failed real field discovery.

Observed field behavior:

- Search Network could remain loading;
- no recorder IP was surfaced;
- the operator could be trapped behind automatic discovery.

This proved that packaging success alone is not a discovery acceptance result.

## Current authoritative fix

Repository:

`Alkalid-security/Watchlog`

Branch:

`main`

Current combined source version:

**5.0.24**

Current product main at this context update:

`7034e2a1deb0c1909fe68ddbd1f7338a3e82bae7`

Existing-site Repair/Upgrade implementation:

PR `#78` / merge `e74526affc4722daf5e14aab07e56b08f72c3d44`

Current discovery behavior:

- preserves Build 69's **8-/24** reach;
- physical Ethernet/Wi-Fi before VPN/Hyper-V/Docker/VMware/WSL/Tailscale/WireGuard adapters;
- **32-second backend discovery budget**;
- **40-second UI watchdog**;
- bounded **256-worker** pool;
- fast ports:
  - Dahua `37777`
  - Hikvision `8000`
  - HTTP `80`
  - HTTPS `443`
- deeper compatibility scan remains available within the time budget;
- multiple recorder candidates across ranked LANs are preserved;
- manual IP stays usable while automatic search runs;
- choosing manual IP safely abandons/invalidate the old discovery generation;
- explicit regression proves a recorder on the **8th subnet** is still found;
- recorder rediscovery supports DHCP/IP changes using stored device identity.

Recorder-login timing:

- 5 seconds per probe;
- 18-second backend deadline;
- 30-second UI watchdog.

## Packaged validation evidence

The discovery/setup implementation was validated in the Windows release-line repository with:

**Build 83 / 5.0.21**

- source SHA:
  `dfc3ec5bc1229a88c510f8057cd9ac898f8cf848`
- run id:
  `36351875478`
- artifact:
  `WatchLog-Windows-83`
- artifact id:
  `10942702630`
- installer SHA-256:
  `EEBA56F5879306CDA0662E6CCA5DBDA2D85AA66D0463D09B21D4F90E54B6F8EC`

Passed gates:

- Windows packaging;
- packaged setup-UI discovery self-test;
- multi-NIC simulated discovery;
- manual-IP escape;
- discovery watchdog;
- recorder login/vendor routing;
- eighth-subnet regression;
- executable ProductVersion/runtime checks;
- checksum verification.

Build 83 is **validation evidence**, not the final authoritative 5.0.24 installer.

## Existing-site upgrade lock validation

The “installer stuck while updating files because WatchLog is still running” class was validated
separately in the Windows release-line repository by **Build 98 / 5.0.21**.

- source SHA:
  `c653c6a38664491ee51788d0466e7338a1f3da53`
- run id:
  `36371718065`
- artifact:
  `WatchLog-Windows-98`
- artifact id:
  `10949248440`
- installer SHA-256:
  `06DFCC486EA15E123BA1E366A68A3DB83C996A6876CFAA0FDCA31BB4AAED2940`

The Windows regression proved the installer preflight can stop the target WatchLog Setup UI
and Agent, handle the launcher path, keep unrelated same-named processes outside the install
directory untouched, verify the payload is unlocked/backed up, and restore the previous payload
on rollback.

This shutdown-before-replace behavior is merged into authoritative 5.0.24 source at
`a3fe605f51f06605355bf9133f8568b5a4a56491`.

## Current remaining gate

Discovery source hardening remains merged, but the **current existing-site promotion
target is 5.0.24 Repair/Upgrade**, not the full Setup wizard.

Windows validation repository:

`visioninfinity0007-droid/Watchlog-2`

Validation branch:

`fix/existing-site-repair-upgrader-v5`

Head at this context update:

`0f3507483ffd7369134fe8b7aa0c6a7be5946ea9`

That branch contains:
- the separate Repair/Upgrade artifact;
- passive staged SYSTEM preflight;
- recorder staged preflight before replacement;
- read-only DPAPI credential handling;
- signed remote-update worker + transactional apply/rollback;
- protected runtime-health proof;
- evidence-based `site_control_runtime` / `remote_update_v1`;
- full-Setup redirect for complete enrolled sites.

The exact 5.0.24 Windows artifact still requires Windows Release + field acceptance.

Until then:
- keep Build 69 as the field-proven discovery/connectivity baseline;
- do not replace a working site merely because a newer version exists;
- do not call Builds 83/98/100 the final 5.0.24 fleet release;
- do not use full Setup as the default upgrade path for a complete enrolled site.

Recent Al-Khalid Head Office field evidence:
- full-installer candidate failed health and rolled back to 5.0.19;
- 5.0.19 has no active `remote_update_v1`;
- therefore one successful 5.0.24 Repair/Upgrade bootstrap is still required.

## Physical acceptance required before promotion

### Hikvision

- auto-discovery;
- manual-IP path;
- native login;
- camera/channel inventory;
- background Agent remains connected;
- Site Control claim/completion;
- recorder inspection/readback;
- upgrade/rollback behavior.

### Dahua

- auto-discovery;
- manual-IP path;
- native CGI login;
- camera/channel inventory;
- background Agent remains connected;
- Site Control claim/completion;
- recorder inspection/readback;
- upgrade/rollback behavior.

### Existing-site Repair/Upgrade

At least one live enrolled site must prove:

- passive staged candidate validation passes while the old Agent remains running;
- recorder identity/channels are proven before installed files are replaced;
- no forced rediscovery;
- no recorder-password re-entry;
- existing encrypted recorder connection is preserved;
- Agent returns online after replacement;
- heartbeat/events/snapshots continue;
- Site Control worker polls;
- remote-update worker polls;
- `remote_update_v1` appears only after that real poll;
- rollback remains available until health verification succeeds.

## Permanent release rule

A Windows installer is not “reliable” because CI is green.

It is reliable only when the exact artifact:

1. preserves or exceeds Build 69 discovery reach;
2. cannot spinner indefinitely;
3. keeps manual IP available;
4. passes packaged discovery/login tests;
5. passes real Hikvision + Dahua field acceptance;
6. has its source SHA, artifact ID/digest and executable hashes recorded in Git;
7. for existing sites, uses the staged Repair/Upgrade path instead of re-running discovery.
