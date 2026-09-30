# Windows Recorder Discovery Field Reliability Gate

## Why this exists

A real field test of Windows Release **Build 74** found that the setup UI could remain
on **Search Network** without surfacing the recorder. Build 74's Windows Release workflow
was green, so packaging success alone is not evidence that recorder discovery is field
reliable.

Builds 72, 73, 74 and 76 share the same core automatic discovery implementation. Build 76
improved ONVIF physical-camera identity and Hikvision footage retrieval, but it did not
replace the Build-74 network-search path. Therefore Build 76 must not be treated as
field-proven for automatic recorder discovery solely because its installer workflow passed.

## Release-blocking requirements

A successor Windows build may only be promoted for field use when all of these are true:

1. Automatic discovery has a hard backend time budget and a separate UI watchdog.
2. Physical Ethernet/Wi-Fi networks are searched before virtual/VPN/Hyper-V/Docker networks.
3. The automatic subnet set preserves Build 69's eight-/24 coverage but is time-bounded; an unusual topology always retains manual-IP and
   explicit-subnet escape paths.
4. A technician can enter/use the recorder IP while automatic search is still running.
5. Hikvision and Dahua native/control ports are checked before broad uncommon-port scanning.
6. Multiple ranked real recorder LANs remain discoverable within the bounded scan.
7. The frozen setup executable itself runs a deterministic second-NIC discovery self-test.
8. Recorder discovery and recorder login/connectivity tests are explicit Windows Release gates.
9. One physical Hikvision field install and one physical Dahua field install must prove:
   - Search Network returns or cleanly times out; it never spins indefinitely.
   - The recorder is detected automatically on the correct LAN, including a PC with Wi-Fi
     internet plus separate CCTV Ethernet.
   - Manual IP works immediately if automatic discovery is not appropriate.
   - Correct credentials reach recorder identity/channel verification without a false timeout.
   - The background agent remains connected after setup closes.

## Current implementation candidate

Branch: `fix/discovery-field-reliability-v5`

The candidate changes:
- preserve Build 69's eight-/24 automatic discovery reach while ranking physical LANs first;
- rank physical interfaces before virtual/VPN interfaces;
- use a 32-second discovery budget;
- use a 40-second setup-UI watchdog;
- keep manual-IP available during discovery;
- invalidate an abandoned discovery worker generation so late results cannot move the UI;
- add packaged multi-NIC discovery proof to `--ui-selftest`;
- gate Windows Release on `test_recorder_probe.py` and `test_recorder_setup.py`.

## Current product integration — 5.0.24

The original discovery candidate is historical validation evidence. Current product
authority is now:

- repository: `Alkalid-security/Watchlog`
- branch: `main`
- version: **5.0.24**
- current product main at the installer-context update:
  `7034e2a1deb0c1909fe68ddbd1f7338a3e82bae7`
- Repair/Upgrade implementation:
  PR `#78` / merge `e74526affc4722daf5e14aab07e56b08f72c3d44`

For **new sites**, the discovery requirements in this document still apply to
`WatchLog-Setup.exe`.

For **existing enrolled sites**, discovery is no longer part of the normal upgrade
flow. The required path is `WatchLog-Repair-Upgrade.exe`, which validates the staged
candidate against the existing config/DPAPI identity and recorder before replacing files.

Current Windows validation branch:

`visioninfinity0007-droid/Watchlog-2:fix/existing-site-repair-upgrader-v5`

Head at this context update:

`0f3507483ffd7369134fe8b7aa0c6a7be5946ea9`

Do not force Search Network during a healthy-site upgrade.

## Promotion status

Source fix: **MERGED** to `build/site-connector-v5-watchlog2` at
`7990cfe4e502b3b0722ba36588be1e0405653da4`.

CI / packaged setup proof: **PASSED**.
- CI run 284: all seven jobs passed.
- Windows Release **Build 77** / run id `36351124843`: passed.
- Artifact: `WatchLog-Windows-77`, id `10941814471`.
- Artifact ZIP digest:
  `sha256:120f3d11329ddfbe59774e02209b5bc2b920daed3cc7f830dc4a5f8baeb28845`.
- `WatchLog-Setup.exe` SHA-256:
  `B73F314FA809FE38B04A2E8DC47FDD51E22864AFDE7EEE62B663734F9A53960C`.
- `watchlog-agent.exe` SHA-256:
  `1305EB5BB936AEFC9F77F8B5DE53481850B2E375CD064BE5F3C5C35F1280A429`.
- `watchlog-setup-ui.exe` SHA-256:
  `0889A810519B1A035C52CF213DFC9974A80EA3BF7BD1108AD75963A20FAF2203`.
- Packaged setup-UI discovery behavior: **passed**, including the deterministic second-NIC
  recorder simulation and discovery watchdog/manual-IP escape contracts.
- Recorder discovery and login/connectivity release gates: **passed**.
- Product version remains **5.0.21**; Build 77 identity and hashes distinguish this candidate
  from Build 76. Do not infer remote-maintenance capability from the product version alone.

Physical field acceptance: **NOT YET PROVEN**.

Build 77 is therefore historical controlled **field-candidate evidence**, not a promoted fleet release.
Do not call it field-reliable until the physical Hikvision + Dahua acceptance above has been
completed. Existing enrolled upgrades preserve the stored recorder configuration and do not
re-run Search Network unless the encrypted recorder credential is missing.


## Golden baseline: Build 69

Field-proven Windows Release Build 69 is the discovery/connectivity regression baseline:
- release run 69 / run id `36238903083`;
- product version `5.0.17`;
- source SHA `811d378e3a7556047f294bb128b8caf45a295469`;
- artifact `WatchLog-Windows-69`, id `10905576478`;
- artifact ZIP digest `sha256:6f84aa14b10eb245f66b9a344524fa39490817bb1882c89daa1954da266809d1`;
- `WatchLog-Setup.exe` SHA-256 `A5428B33A9789056D8156F445FE099F73CC926E5903C4162F96746D7C90E1B5E`;
- `watchlog-agent.exe` SHA-256 `24EEAC5826CF104DC41770A66A69F53F83960443B73D9D3CEEC997B39BFDD5F4`;
- `watchlog-setup-ui.exe` SHA-256 `C5B732F1D28F0D2FB54EBC4ACD00C4BBFF3EC654960F3BA3C82AA8E48007F8E6`;
- automatic discovery covered up to eight local /24s;
- the common recorder port set included HTTP/HTTPS, Hikvision 8000, Dahua 37777/37778,
  RTSP 554, alternate web ports and 34567;
- recorder login used a 5-second per-probe timeout, an 18-second backend deadline and a
  30-second UI watchdog.

A newer build may reorder/prioritize those probes and may bound concurrency/deadlines, but
it must not silently reduce this field-proven reach. Build 69 itself remains the live-site
reference until a successor passes physical Hikvision and Dahua acceptance.


## Existing-site reliability rule added by 5.0.24

A future release is not existing-site reliable unless the exact Repair/Upgrade artifact proves:

1. passive candidate validation while the old Agent remains untouched;
2. recorder validation before payload replacement;
3. no forced rediscovery or recorder-password re-entry;
4. exact-path process shutdown and file-unlock proof;
5. full-payload rollback;
6. old-Agent restart verification on rollback;
7. new-version heartbeat + recorder contact;
8. real updater polling before `remote_update_v1` is advertised.

The recent Al-Khalid Head Office full-installer failure/rollback is the field reason for
this rule. The site returned to 5.0.19, which still lacks active online-update capability.
