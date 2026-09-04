#!/usr/bin/env python3
"""Static product-contract gates for the customer Windows installer.

These do not pretend to replace Win10/11 acceptance. They prevent easy
regressions back to the terminal wizard, plaintext credential storage or an
NSIS manifest that packages only the agent.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def text(path):
    return (ROOT / path).read_text(encoding="utf-8")


def main():
    gui = text("prototype/agent/setup_gui.py")
    backend = text("prototype/agent/setup_backend.py")
    secret = text("prototype/agent/windows_secret.py")
    launcher = text("prototype/installer/run-agent.ps1")
    nsis = text("prototype/installer/nsis/watchlog.nsi")
    build_ui = text("prototype/agent/build_setup_gui.ps1")
    release = text("tools/build_windows_release.ps1")
    release_workflow = text(".github/workflows/windows-release.yml")

    checks = {
        "GUI is real PySide6": "from PySide6" in gui,
        "GUI build is windowed": '"--windowed"' in build_ui,
        "GUI covers recorder discovery": "discover_recorders" in gui and "test_recorder" in gui,
        "GUI performs real finalization": "finalize_install" in gui,
        "DPAPI local-machine protection": "CRYPTPROTECT_LOCAL_MACHINE" in secret,
        "DPAPI file ACL is restricted": "icacls" in secret and "SYSTEM_SID" in secret and "ADMINISTRATORS_SID" in secret,
        "protected temp file is locked before publish": secret.index("_lock_acl(tmp)") < secret.index("tmp.replace(path)"),
        "backend never writes plaintext nvr_password key": 'section["nvr_password"]' not in backend,
        "background launcher unwraps DPAPI": "ProtectedData]::Unprotect" in launcher,
        "password exists only in child process environment": "WATCHLOG_NVR_PASSWORD" in launcher,
        "NSIS packages setup UI": 'File "watchlog-setup-ui.exe"' in nsis,
        "NSIS launches branded setup": 'watchlog-setup-ui.exe' in nsis,
        "NSIS no longer launches agent --setup": 'watchlog-agent.exe\" --setup' not in nsis,
        "release packages setup UI": "watchlog-setup-ui.exe" in release,
        "release rejects small setup UI": "setupUiBytes -lt 5MB" in release,
        "release workflow verifies setup UI": "Verified setup UI" in release_workflow and "--migrate-only" in release_workflow,
        "uninstall removes protected credential": "nvr_password.dpapi" in nsis,
    }

    failed = [name for name, ok in checks.items() if not ok]
    for name, ok in checks.items():
        print(("PASS" if ok else "FAIL") + "  " + name)
    if failed:
        raise SystemExit("installer product contract failed: " + ", ".join(failed))
    print(f"\n{len(checks)}/{len(checks)} installer product checks passed")


if __name__ == "__main__":
    main()
