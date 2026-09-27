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
3. The automatic subnet set is bounded; an unusual topology always retains manual-IP and
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
- cap broad automatic scanning to the four highest-ranked local /24s;
- rank physical interfaces before virtual/VPN interfaces;
- use a 32-second discovery budget;
- use a 40-second setup-UI watchdog;
- keep manual-IP available during discovery;
- invalidate an abandoned discovery worker generation so late results cannot move the UI;
- add packaged multi-NIC discovery proof to `--ui-selftest`;
- gate Windows Release on `test_recorder_probe.py` and `test_recorder_setup.py`.

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

Build 77 is therefore a controlled **field candidate**, not a promoted fleet release.
Do not call it field-reliable until the physical Hikvision + Dahua acceptance above has been
completed. Existing enrolled upgrades preserve the stored recorder configuration and do not
re-run Search Network unless the encrypted recorder credential is missing.
