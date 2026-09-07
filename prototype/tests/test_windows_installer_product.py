#!/usr/bin/env python3
"""Static product-contract gates for the customer Windows installer.

These do not pretend to replace Win10/11 acceptance. They prevent easy
regressions back to the terminal wizard, plaintext credential storage,
implementation-oriented customer copy or an NSIS manifest that packages only
the agent.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def text(path):
    return (ROOT / path).read_text(encoding="utf-8")


def main():
    gui = text("prototype/agent/setup_gui.py")
    backend = text("prototype/agent/setup_backend.py")
    secret = text("prototype/agent/windows_secret.py")
    store = text("prototype/agent/credential_store.py")
    agent = text("prototype/agent/watchlog_agent.py")
    launcher = text("prototype/installer/run-agent.ps1")
    nsis = text("prototype/installer/nsis/watchlog.nsi")
    readme = text("prototype/installer/READ ME FIRST.txt")
    build_ui = text("prototype/agent/build_setup_gui.ps1")
    release = text("tools/build_windows_release.ps1")
    release_workflow = text(".github/workflows/windows-release.yml")

    checks = {
        "GUI is real PySide6": "from PySide6" in gui,
        "GUI build is windowed": '"--windowed"' in build_ui,
        "GUI covers recorder discovery": "discover_recorders" in gui and "test_recorder" in gui,
        "GUI performs real finalization": "finalize_install" in gui,
        "recorder credential is DPAPI-encrypted (not plaintext)": "CryptProtectData" in secret and "write_json_secret" in store and "nvr_credential.dpapi" in store,
        "DACL hardened+verified to SYSTEM+Admins only": "SYSTEM_SID" in secret and "ADMINISTRATORS_SID" in secret and "_ALLOWED_SIDS" in secret,
        "ownership set + verified (owner holds WRITE_DAC)": "SetOwner" in secret and "owner is" in secret,
        "ACL hardening is replacement-based (self-repairing)": "SetAccessRuleProtection" in secret and "RemoveAccessRule" in secret,
        "transactional publish: verify temp -> replace -> verify final": secret.index("_secure_and_verify(tmp, container=False)") < secret.index("os.replace(tmp, path)") < secret.index("_secure_and_verify(path, container=False)"),
        "corrupt DPAPI never downgrades to plaintext": "NEVER a downgrade" in store,
        "agent key split from state; is_enrolled = state AND key": "agent_key.dpapi" in store and "is_enrolled" in store,
        "backend never writes plaintext nvr_password key": 'section["nvr_password"]' not in backend,
        "background launcher performs no decryption": "ProtectedData" not in launcher and "Unprotect" not in launcher,
        "agent self-decrypts credential in any launch": "load_recorder_credential" in agent and "credential_store" in agent,
        "interruptible auth breaker (5/15/30, wake on cred change)": "_reconnect_wait" in agent and "credential_generation" in agent and "_AUTH_BACKOFF_SECONDS" in agent,
        "NSIS packages setup UI": 'File "watchlog-setup-ui.exe"' in nsis,
        "NSIS launches branded setup": 'watchlog-setup-ui.exe' in nsis,
        "NSIS no longer launches agent --setup": 'watchlog-agent.exe\" --setup' not in nsis,
        "release packages setup UI": "watchlog-setup-ui.exe" in release,
        "release rejects small setup UI": "setupUiBytes -lt 5MB" in release,
        "release workflow verifies setup UI": "Verified setup UI" in release_workflow and "--migrate-only" in release_workflow,
        "uninstall removes the encrypted Secrets store": "RMDir /r" in nsis and "Secrets" in nsis,
        "setup sidebar uses customer language": "SITE CONNECTION SETUP" in gui and "SITE AGENT SETUP" not in gui,
        "setup does not expose DPAPI terminology": "Protected with Windows DPAPI" not in gui,
        "setup does not expose engineering validation labels": "field-validated driver" not in gui and "model still needs field acceptance" not in gui,
        "setup ready state uses WatchLog connection language": "This WatchLog connection is ready" in gui and "This Site Agent" not in gui,
        "raw recorder driver detail is not surfaced": "({detail})" not in backend,
        "Windows product name is WatchLog": 'MUI_WELCOMEPAGE_TITLE "Install WatchLog"' in nsis and '"DisplayName" "WatchLog"' in nsis,
        "customer guide avoids Site Agent product name": "WatchLog Site Agent" not in readme and "install the Site Agent" not in readme,
        "customer guide avoids DPAPI implementation detail": "machine-scoped DPAPI" not in readme,
        "enrollment honours the site code (no skip-enroll on stale state)":
            "def establish_identity" in backend and "def _enroll" in backend and "core.heartbeat" in backend,
        "camera-sync failures are classified, not the misleading swallow":
            "AgentSyncError" in backend and "CAMERA_SYNC_AUTH_FAILED" in backend
            and "The site linked to WatchLog, but its cameras could not be added" not in backend,
    }

    failed = [name for name, ok in checks.items() if not ok]
    for name, ok in checks.items():
        print(("PASS" if ok else "FAIL") + "  " + name)
    if failed:
        raise SystemExit("installer product contract failed: " + ", ".join(failed))
    print(f"\n{len(checks)}/{len(checks)} installer product checks passed")


if __name__ == "__main__":
    main()
