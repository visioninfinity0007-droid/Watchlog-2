# WatchLog 5.1.4-RC — Windows installation verification (Salman / AKS)

**HOLD: DO NOT INSTALL until the replacement run's `verify-downloaded-release` job succeeds and the SHA-256 values are approved.** Do not use the previously distributed 196 MB ZIP.

1. Download the **WatchLog-Windows-<run-number>** artifact from the approved GitHub Actions run using the artifact download button. Do not re-ZIP or rename an incomplete download.
2. In PowerShell: `Get-FileHash .\WatchLog-Windows-<run-number>.zip -Algorithm SHA256`. Compare against the exact GitHub Actions artifact digest for that run (do not use run #163's digest for a new build).
3. Open with 7-Zip or WinRAR, run a full **Test archive** before extracting. The archive should contain `WatchLog-Setup.exe`, `WatchLog-Repair-Upgrade.exe`, their `.sha256` sidecars, and release reports.
4. Extract to a new local folder. Confirm `Get-FileHash .\WatchLog-Setup.exe -Algorithm SHA256` equals the first 64 hexadecimal characters of `WatchLog-Setup.exe.sha256` (same for Repair/Upgrade). Stop if mismatched.
5. On a dedicated clean Windows test machine, use an administrator account, record Windows version and machine ID, run `WatchLog-Setup.exe`, record dependency installation, UI, Windows service registration and service startup. Record actual error messages/screenshots.
6. Enroll against the approved WatchLog backend; validate agent identity, authentication, cloud connectivity and heartbeat timestamps in the backend. Ensure the reported version matches the release manifest.
7. Verify Hikvision and Dahua individually where NVRs are available; record firmware/model, connectivity, discovery and any unsupported feature explicitly.
8. Test uninstall; verify service removal, then reinstall and repeat startup, enrollment and heartbeat tests. Never uninstall a production site agent without approval.
9. Repeat the entire installation and connectivity checklist on the **second** AKS PC. Do not mark customer acceptance complete until both machines pass.

**Record:** artifact run ID, ZIP SHA-256, setup SHA-256, PC/Windows details, install logs, Windows service name/state, enrolled device ID, heartbeat timestamp, NVR results, uninstall/reinstall and reviewer approval.

**Stop and report immediately** on failed CRC, hash mismatch, blocked executable, failed service start or missing heartbeat. Do not bypass Windows security warnings without publisher verification and authorization.

## Repair changelog
- Added an independent post-upload CI job on Windows that downloads the original artifact ZIP from GitHub.
- Added strict ZIP inventory, CRC/decompression and GitHub-reported outer SHA-256 validation.
- Added installer SHA-256 cross-checking against sidecars and release manifest, executable header checks and JSON report parsing.
- No agent runtime, installer behavior, deployed system, or Hikvision/Dahua capability code intentionally changed.

## Test status
- Original run #163 Windows build/payload/checksum workflow: PASS (recorded GitHub Actions evidence).
- Downloaded run #163 ZIP independently opened/tested: NOT VERIFIED in this environment.
- New post-upload verification on repair branch: PENDING its GitHub Actions result.
- Clean Windows installation / service / enrollment / heartbeat / uninstall / reinstall: NOT RUN.
- Salman AKS site, two PCs and Hikvision/Dahua hardware: NOT RUN.
