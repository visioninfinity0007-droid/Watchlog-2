# WatchLog Windows Installer — Source of Truth

For current live tenant/site/runtime context, also read:

`docs/production/CURRENT_LIVE_CONTEXT_2026-09-28.md`

This document is the release authority for Windows installer work.

---

## 1. Authoritative source

Authoritative repository:

`Alkalid-security/Watchlog`

Authoritative branch:

`main`

Current authoritative source version:

**5.0.22**

Authoritative merged implementation commit containing the current installer/runtime hardening:

`a3fe605f51f06605355bf9133f8568b5a4a56491`

This main branch contains both:

- the Build-69-derived discovery/connectivity reliability work; and
- the newer Site Control / secure remote-update / Hikvision readback work.

The separate `visioninfinity0007-droid/Watchlog-2` repository is no longer the
product source of truth. It remains useful as a Windows release-line validation
repository because Builds 69–83 were produced there and provide exact field/release evidence.

---

## 2. Field-proven baseline — Build 69

**Build 69 / product version 5.0.17 is the current field-proven discovery/connectivity baseline.**

Exact identity:

- source SHA:
  `811d378e3a7556047f294bb128b8caf45a295469`
- Windows Release run:
  **#69**
- run id:
  `36238903083`
- artifact:
  `WatchLog-Windows-69`
- artifact id:
  `10905576478`
- artifact ZIP digest:
  `sha256:6f84aa14b10eb245f66b9a344524fa39490817bb1882c89daa1954da266809d1`
- `WatchLog-Setup.exe` SHA-256:
  `A5428B33A9789056D8156F445FE099F73CC926E5903C4162F96746D7C90E1B5E`
- `watchlog-agent.exe` SHA-256:
  `24EEAC5826CF104DC41770A66A69F53F83960443B73D9D3CEEC997B39BFDD5F4`
- `watchlog-setup-ui.exe` SHA-256:
  `C5B732F1D28F0D2FB54EBC4ACD00C4BBFF3EC654960F3BA3C82AA8E48007F8E6`

### Why Build 69 matters

Build 69 has real field evidence of successful recorder discovery/connectivity on a live site.

Its discovery behavior covered up to eight local /24 networks and included the common
Hikvision/Dahua/RTSP/web ports.

Build 69 therefore remains the **golden regression baseline** until a later exact artifact
passes physical Hikvision and Dahua acceptance.

Do not replace this baseline with a newer build merely because CI or packaging is green.

---

## 3. Build 74 field failure

Build 74 is the counterexample to “green build = reliable installer”.

Real field behavior:

- setup remained on `Search Network`;
- no recorder IP was surfaced;
- the installer could keep loading instead of recovering cleanly.

The discovery implementation could scan several local /24s across physical and
virtual/VPN adapters with no product-level deadline in the setup UI.

This failure created a permanent release rule:

> **Discovery/connectivity is a field acceptance gate, not only a packaging gate.**

Any successor must preserve Build 69's useful network reach while preventing Build 74's
spinner-forever failure class.

---

## 4. Discovery/connectivity hardening now in authoritative 5.0.22

The authoritative main source now implements:

- up to **8 local /24s**, preserving Build 69 reach;
- physical Ethernet/Wi-Fi before virtual/VPN/Hyper-V/Docker/VMware/WSL/Tailscale/WireGuard adapters;
- bounded discovery;
- **32-second backend discovery budget**;
- **40-second setup-UI discovery watchdog**;
- **256 bounded workers**, instead of Build 69's much larger socket burst;
- fast ports:
  - Dahua `37777`
  - Hikvision `8000`
  - HTTP `80`
  - HTTPS `443`
- deeper compatibility ports including:
  - RTSP `554`
  - Dahua `37778`
  - `8080`, `8443`, `81`, `82`, `88`, `8081`, `8888`
  - `34567`
- preservation of multiple recorder candidates across ranked LANs;
- manual IP usable while automatic discovery is still running;
- “Use this IP” safely invalidates the old discovery worker generation;
- same-endpoint recorder auth fallback before wasting time on alternate endpoints;
- targeted web-port rescue when a native recorder port is found;
- recorder rediscovery by stored device identity after DHCP/IP changes.

Recorder login timing remains aligned with the proven field behavior:

- **5 seconds per recorder probe**
- **18 seconds backend login deadline**
- **30 seconds UI login watchdog**

---

## 5. Release-line validation candidate — Build 83

The discovery/setup hardening was validated in the Windows release-line repository:

`visioninfinity0007-droid/Watchlog-2`

Branch:

`build/site-connector-v5-watchlog2`

Exact Build 83 identity:

- Build:
  **83**
- product version:
  **5.0.21**
- source SHA:
  `dfc3ec5bc1229a88c510f8057cd9ac898f8cf848`
- Windows Release run id:
  `36351875478`
- artifact:
  `WatchLog-Windows-83`
- artifact id:
  `10942702630`
- artifact ZIP digest:
  `sha256:0abf05c447abfa74277f4355f86da2a66f7d75788d29bba815b9413898eb871d`
- `WatchLog-Setup.exe` SHA-256:
  `EEBA56F5879306CDA0662E6CCA5DBDA2D85AA66D0463D09B21D4F90E54B6F8EC`
- `watchlog-agent.exe` SHA-256:
  `6593168ED008D36467772948C909888CE58380BBC4CE5D3B7657C26563024576`
- `watchlog-setup-ui.exe` SHA-256:
  `F345DC7C247741C6643BD7021EB285FCC9796881AF213DECF204431B2DF5853D`

Build 83 passed:

- Windows packaging;
- packaged setup UI self-test;
- multi-NIC discovery simulation;
- manual-IP escape behavior;
- discovery watchdog behavior;
- recorder discovery regression tests;
- recorder login/vendor routing tests;
- eighth-subnet regression;
- ProductVersion/runtime checks;
- checksum verification.

### Important boundary

Build 83 is a **validation candidate**, not the final authoritative installer.

It is version **5.0.21** and does not represent the full authoritative 5.0.22
remote-maintenance/runtime source.

Do not call Build 83 the final production installer.

---

## 6. Authoritative 5.0.22 installer status

The authoritative `Alkalid-security/Watchlog` main source is version **5.0.22** and includes:

- Build-69 discovery/connectivity parity;
- Site Control runtime;
- truthful runtime capability reporting;
- secure signed remote-update infrastructure;
- transactional update rollback;
- Hikvision recorder identity/channel/clock/storage/analytics readback;
- Hikvision native-AI provenance;
- incident still/clip workers;
- archive/recovery;
- health/status/runtime protections.

### Current blocker

The final authoritative **5.0.22 Windows artifact has not yet been produced**.

The repository's GitHub Actions jobs are currently terminating before checkout/execution:
all jobs show **zero executed steps**. This is runner/infrastructure failure, not evidence
that the 5.0.22 source failed its tests.

Until an exact Windows artifact is produced from authoritative main and recorded here,
Build 69 remains the live-site field baseline.

---

## 7. Existing-site upgrade rule

For a working enrolled site such as Chai Wala, **the installer must stop WatchLog before replacing files**.

Required upgrade sequence:

1. suspend/disable the `WatchLog Agent` scheduled-task watchdog so it cannot restart the Agent during file replacement;
2. stop only the current installation's `run-agent.ps1` / launcher process;
3. close, then force-stop if required, only the current installation's `watchlog-setup-ui.exe`;
4. stop, then force-stop if required, only the current installation's `watchlog-agent.exe`;
5. verify every core payload file is exclusively writable before NSIS extracts anything;
6. back up the complete existing payload;
7. replace the full payload under checked/non-blocking overwrite semantics;
8. preserve encrypted recorder credentials, enrollment identity and site configuration;
9. verify the new file ProductVersion and runtime `--version`;
10. re-register/start WatchLog and verify the new Agent remains running;
11. if any stage fails, restore the complete previous payload and fail closed unless the previous Agent actually restarts.

The installer must **never sit indefinitely on “Updating files” because WatchLog is still running**.
It must either free the files and continue, or stop the upgrade and restore the previous working installation.

The installer should only require recorder rediscovery when the stored recorder identity/
credential material is missing or genuinely invalid.

### Release-line proof for the running-file-lock fix

The shutdown/rollback behavior was validated in the Windows release-line repository by
**Windows Release #98 / run id `36371718065`**.

Exact validation artifact:

- source SHA:
  `c653c6a38664491ee51788d0466e7338a1f3da53`
- artifact:
  `WatchLog-Windows-98`
- artifact id:
  `10949248440`
- artifact ZIP digest:
  `sha256:c6a29f4b1642c1fab1d546749e6928e432b605ca564395499e8e2e0e5c76f30c`
- `WatchLog-Setup.exe` SHA-256:
  `06DFCC486EA15E123BA1E366A68A3DB83C996A6876CFAA0FDCA31BB4AAED2940`
- `watchlog-agent.exe` SHA-256:
  `F91B77053E3F1BE8EA2C52E4230CD7746CCDCFEE07290E1ACF792166014F8994`
- `watchlog-setup-ui.exe` SHA-256:
  `C0702F8312F2CE4D9FD2A247C5D184B9F4ABA3949EDDD2D4B7BFBC587574DFC4`

The Windows regression used real processes/file locks and proved that preflight:

- stopped the target WatchLog Setup UI;
- stopped the target WatchLog Agent;
- handled the WatchLog launcher path;
- left an unrelated same-named process outside the install directory alone;
- proved the payload was unlocked and backed up before replacement;
- restored the complete previous payload during rollback;
- refused to claim rollback success when no enabled background task existed to restart the old Agent.

Build 98 is still a **5.0.21 release-line validation artifact**, not the final authoritative 5.0.22 fleet installer.

---

## 8. Secure remote-update direction

The 5.0.22 source contains the permanent remote-maintenance architecture:

- outbound-only Agent polling;
- no inbound Windows management port;
- cloud cannot send arbitrary shell commands;
- cloud cannot choose arbitrary binaries;
- agent fetches its own configured HTTPS signed release manifest;
- Ed25519 manifest verification;
- SHA-256 payload verification;
- package-size verification;
- staged replacement between agent runs;
- previous binary retained for rollback;
- health window before success is finalized;
- early-start failure restores the previous agent;
- runtime capability advertised only after successful remote-update polling.

This solves the long-term requirement that future field upgrades should not require repeated site visits.

The bootstrap limitation remains: an already-installed old binary that does not poll the
remote-update queue cannot be taught that worker purely from the cloud.

---

## 9. Mandatory physical field acceptance for the next promoted installer

The first authoritative 5.0.22 (or later) Windows artifact must pass all of the following
before replacing Build 69 as the fleet baseline.

### Hikvision

- automatic discovery;
- manual-IP fallback;
- correct native login;
- channel inventory;
- background Agent connectivity after setup closes;
- Site Control claim/completion;
- recorder inspection;
- recording/storage/analytics readback;
- one bounded historical clip/archive attempt;
- upgrade/rollback behavior.

### Dahua

- automatic discovery;
- manual-IP fallback;
- native CGI authentication;
- channel inventory;
- background Agent connectivity after setup closes;
- Site Control claim/completion;
- recording/storage/analytics readback;
- one bounded archive retrieval;
- upgrade/rollback behavior.

### Existing Build-69 site

At least one Build-69 live site must be upgraded **in place** and prove:

- no forced rediscovery;
- existing recorder connectivity preserved;
- Agent returns online;
- heartbeat/event/snapshot path still works;
- Site Control worker polls;
- remote-update worker polls;
- rollback remains available until the health window passes.

Only after these tests should the new exact artifact replace Build 69 as the field baseline.

---

## 10. Do-not-regress rules

- Never reduce automatic discovery below Build 69's eight-/24 reach without explicit field evidence.
- Never allow automatic discovery to spinner forever.
- Never block manual IP behind an automatic scan.
- Never treat virtual/VPN adapters as higher priority than physical CCTV LANs.
- Never treat CI/package success alone as field discovery proof.
- Never call Build 83 the authoritative 5.0.22 installer.
- Never replace a working Build-69 site with an unaccepted candidate.
- Never claim Site Control/remote update from capability strings alone; require live poll proof.
- Never expose recorder passwords or signing secrets.
- Never treat HTTP 200 alone as proof a recorder write succeeded.
- Never remove rollback before the replacement agent has passed its health window.
- Never replace WatchLog files while its launcher, Setup UI or Agent from that install are still running or holding the payload.
- Never broad-kill same-named processes outside the current WatchLog install path during upgrade.

---

## 11. Historical lineage

- **Build 37 / 5.0.0** — historical lineage anchor.
- **Build 39 / 5.0.1** — recorder selection/UI regression successor.
- **Build 41 / 5.0.2** — Step-06/login-watchdog successor.
- **Build 46 / 5.0.3** — installer-child lifecycle/Hikvision integration hardening.
- **Build 49 / 5.0.6** — recorder-backed readiness predecessor.
- **Build 50 / 5.0.7** — Hikvision single-session monitoring predecessor.
- **Build 56 / 5.0.8** — dual-vendor archive/recovery predecessor.
- **Build 61 / 5.0.12** — production recovery + PC-off safety semantics.
- **Build 69 / 5.0.17** — **current field-proven golden baseline**.
- **Build 70 / 5.0.18** — duplicate-login/Step-06 iteration.
- **Build 71 / 5.0.19** — login retry/watchdog iteration.
- **Build 72 / 5.0.20** — deterministic first-pass discovery iteration.
- **Build 74** — **real field discovery failure; do not use as reliability evidence**.
- **Build 76 / 5.0.21** — ONVIF physical-camera + Hikvision footage hardening, but inherited the same core discovery class.
- **Build 77 / 5.0.21** — first bounded-discovery candidate.
- **Build 82 / 5.0.21** — Build-69 eight-subnet parity candidate passed Windows Release.
- **Build 83 / 5.0.21** — discovery/setup validation candidate with exact artifact recorded above.
- **Build 98 / 5.0.21** — running-file-lock/transactional-upgrade validation candidate; real Windows process test and packaged release passed.
- **Authoritative source 5.0.22** — combined Build-69 reliability + remote maintenance/readback + shutdown-before-replace upgrade hardening; final Windows artifact still pending.

---

## 12. Mandatory rule for future installer work

Before changing, diagnosing, recommending, or promoting a WatchLog Windows installer:

1. identify the exact build/artifact currently installed;
2. resolve it to source SHA, workflow run, artifact ID and hashes;
3. treat Build 69 as the current field-proven discovery/connectivity baseline;
4. make product-source changes in `Alkalid-security/Watchlog` main or a PR targeting main;
5. use release-line builds only as validation evidence, not as product authority;
6. produce an exact Windows artifact from authoritative source;
7. record artifact ID/digest and executable hashes here;
8. pass packaged setup/discovery/login tests;
9. pass physical Hikvision + Dahua acceptance;
10. only then promote the build to live sites/fleet.

Do not infer authority from repository age, branch recency, build number or a green package workflow alone.
