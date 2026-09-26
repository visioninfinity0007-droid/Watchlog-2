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
    hikvision = text("prototype/agent/drivers/hikvision.py")
    launcher = text("prototype/installer/run-agent.ps1")
    register = text("prototype/installer/register-service.ps1")
    nsis = text("prototype/installer/nsis/watchlog.nsi")
    upgrade = text("prototype/installer/nsis/wl-upgrade.ps1")
    readme = text("prototype/installer/READ ME FIRST.txt")
    build_ui = text("prototype/agent/build_setup_gui.ps1")
    release = text("tools/build_windows_release.ps1")
    release_workflow = text(".github/workflows/windows-release.yml")

    checks = {
        "GUI is real PySide6": "from PySide6" in gui,
        "GUI build is windowed": '"--windowed"' in build_ui,
        "GUI build packages only required Qt modules (fast one-file startup)":
            '"--collect-all", "PySide6"' not in build_ui,
        "GUI covers recorder discovery": "discover_recorders" in gui and "test_recorder" in gui,
        "GUI performs real finalization": "finalize_install" in gui,
        "discovery auto-selects a real recorder candidate":
            "self.recorder_list.setCurrentRow(0)" in gui and "current.setSelected(True)" in gui,
        "Continue falls back to the highlighted recorder":
            "current = self.recorder_list.currentItem()" in gui
            and 'address = str(current.data(Qt.UserRole) or "").strip()' in gui,
        "optional recorder integration is never on installer critical path":
            "provision_recorder_push(" not in backend.split("def finalize_install", 1)[1],
        "background success requires a fresh real-agent cloud heartbeat":
            "background-ready.json" in agent
            and "background-ready.json" in register
            and "did not prove a cloud heartbeat" in register,
        "background Ready proof also requires a recorder connection":
            "if device is not None:" in agent
            and '"recorder_connected": True' in agent,
        "setup seeds non-secret recorder identity for DHCP recovery":
            "def _seed_recorder_identity" in backend
            and "connector_rediscovery.save_identity" in backend
            and '"serial": info.serial or ""' in backend,
        "runtime starts live work before recorder capability enrichment":
            "driver.capabilities()" not in agent.split("# Identify the recorder ONCE", 1)[1],
        "Recorder Continue is disabled while discovery worker is busy":
            "self.recorder_next.setEnabled(not busy)" in gui,
        "packaged UI exposes behavioral self-test":
            '"--ui-selftest"' in gui and "def _run_ui_selftest" in gui,
        "single recorder is selected but multiple recorders are never silently auto-picked":
            "if len(rows) == 1:" in gui and "self.recorder_list.setCurrentRow(0)" in gui,
        "Step 06 reuses Step 04 recorder proof and has a terminal 50s watchdog":
            "acceptance is a POST-INSTALL diagnostic" in gui
            and "self.go(6)" in gui
            and "verified_recorder=self.recorder_result" in gui
            and "timeout_ms=50000" in gui
            and "if verified_recorder:" in backend
            and 'progress("Recorder login already verified.")' in backend,
        "installer-child login timeout closes instead of stranding NSIS":
            "timeout_ms=24000" in gui
            and "if self.installer_child:" in gui.split("def _worker_timeout", 1)[1].split("def _on_progress", 1)[0]
            and "timed.exit_code != 2" in gui,
        "NSIS child setup auto-exits after Ready so ExecWait cannot strand installer":
            '"--installer-child"' in gui
            and "self.installer_child" in gui
            and "QTimer.singleShot(1800, self.finish)" in gui
            and '"--installer-child"' in nsis,
        "NSIS never restarts a connector already heartbeat-proved by Setup":
            'StrCpy $7 "1"' in nsis
            and '${If} $7 == "1"' in nsis
            and "already heartbeat-proven by Setup" in nsis,
        "Hikvision integration errors distinguish API auth from browser password":
            "hikvision_integration_auth" in backend
            and "hikvision_integration_unavailable" in backend
            and "hikvision_auth_rejected" in backend,
        "Hikvision login ignores proxy env and avoids pointless Basic retry after Digest rejection":
            "self.s.trust_env = False" in hikvision
            and '"digest" not in challenge' in hikvision,
        "native recorder login succeeds on identity auth without full inventory crawl":
            'if driver_name in ("hikvision-isapi", "dahua-cgi") and info.channel_count:' in backend
            and 'progress("Recorder login verified.")' in backend
            and "SimpleNamespace(channel=str(i)" in backend,
        "Hikvision quiet sites deliver rotating stills on the live session":
            "HIKVISION_STREAM_SLICE_SECONDS = 30" in hikvision
            and 'event_type="visual_sample"' in hikvision
            and '"source": "periodic_snapshot"' in hikvision,
        "Hikvision health reuses fresh collector truth instead of competing sessions":
            'cfg.nvr_driver == "hikvision-isapi"' in agent
            and '"recorder_live_at"' in agent
            and "collector_recent" in agent,
        "Hikvision archive/video extraction is packaged and recovery-enabled":
            "import hikvision_archive" in text("prototype/agent/release_agent.py")
            and "hikvision_archive.install()" in text("prototype/agent/release_agent.py")
            and "hikvision_archive.install()" in agent,
        "gap clock advances only on fresh recorder transport":
            "Persist RECORDER observation" in agent
            and "last_activity_monotonic" in agent,
        "Hikvision cached-snapshot health evaluates every channel without extra NVR probes":
            "health_batch = len(chans) if collector_recent else cfg.health_batch" in agent,
        "actual live collector publishes recorder transport for recovery":
            'holder["live_driver"] = driver' in agent
            and 'holder["recorder_live_at"] = time.monotonic()' in agent
            and 'holder.pop("live_driver", None)' in agent,
        "recovery accepts startup channel dictionaries":
            "if isinstance(item, dict)" in agent
            and 'item.get("channel")' in agent,
        "PC-off recorder push auto-provisions asynchronously and waits for fresh real delivery":
            "def recorder_push_worker" in agent
            and "wl_agent_push_status" in agent
            and "target=recorder_push_worker" in agent
            and "verification_after = now_utc()" in agent
            and 'status.get("last_push_at")' in agent
            and "last_push >= (verification_after - timedelta(seconds=5))" in agent
            and "Configure first" in agent,
        "failed upgrade rollback verifies the previous agent is running again":
            "rollback complete: previous agent restored AND running" in text("prototype/installer/nsis/wl-upgrade.ps1")
            and "Fail 14" in text("prototype/installer/nsis/wl-upgrade.ps1"),
        "installer-child terminal failure closes all windows and returns nonzero":
            "def _terminal_installer_failure" in setup_gui
            and "self._close_installer_child(2)" in setup_gui
            and "failed.exit_code != 2" in setup_gui
            and "SetErrorLevel 2" in text("prototype/installer/nsis/watchlog.nsi")
            and "Quit" in text("prototype/installer/nsis/watchlog.nsi"),
        "installer-child exits the whole Qt process even with Site Status open":
            "app.exit(0)" in setup_gui
            and "os._exit(int(code))" in setup_gui
            and 'getattr(self, "_status_win", None)' in setup_gui,
        "ONVIF discovery preserves Dahua/Hikvision identity and native drivers win all web targets":
            '"vendor_hint": vendor_hint' in setup_backend
            and "if hint in _DRIVERS_BY_VENDOR:" in setup_backend
            and "for driver_name in drivers:" in setup_backend,
        "Hikvision PC-off path requests 30s NVR heartbeats and broken-link resend":
            "<heartbeat>30</heartbeat>" in hikvision
            and "<httpBroken>true</httpBroken>" in hikvision
            and "<SubscribeEvent>" in hikvision,
        "generic Dahua PC-off path never overwrites proprietary AlarmServer settings":
            "generic Dahua AlarmServer is a proprietary alarm-centre protocol" in dahua
            and "action=setConfig&AlarmServer." not in dahua.split("def configure_push", 1)[1].split("def get_clock", 1)[0],
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

        # ---- transactional upgrade reliability (the locked-EXE failure class) ----
        "installer stages the transactional upgrade orchestrator":
            "InitPluginsDir" in nsis and 'File "/oname=$PLUGINSDIR\\wl-upgrade.ps1"' in nsis and 'File "wl-upgrade.ps1"' in nsis,
        "upgrade STOPS+verifies the agent BEFORE replacing the binary":
            "-Stage preflight" in nsis and nsis.index("-Stage preflight") < nsis.index('File "watchlog-agent.exe"'),
        "a locked agent binary cannot silently continue (SetOverwrite try + error check + rollback)":
            "SetOverwrite try" in nsis and "${Errors}" in nsis and "-Stage rollback" in nsis,
        "upgrade verifies the INSTALLED version before starting":
            "-Stage verify-version" in nsis and "-ExpectedVersion" in nsis,
        "upgrade verifies the agent is RUNNING after start (commit)":
            "-Stage commit" in nsis,
        "no false success: commit precedes the ARP DisplayVersion write":
            nsis.index("-Stage commit") < nsis.index('"DisplayVersion" "${APPVERSION}"'),
        "failed upgrade rolls back to the previous working agent":
            "-Stage rollback" in nsis and "rollback" in upgrade and "wlbak" in upgrade,
        "helper stops ONLY the exact watchlog-agent.exe (no broad kill)":
            "Name='watchlog-agent.exe'" in upgrade and "ExecutablePath" in upgrade and "-Force -ErrorAction SilentlyContinue" in upgrade,
        "helper verifies BOTH file ProductVersion and runtime --version":
            "VersionInfo.ProductVersion" in upgrade and "--version" in upgrade,
        "helper verifies a single instance (no duplicate runtime)":
            "duplicate runtime" in upgrade,
        "upgrade helper reads/logs NO secret":
            "agent_key" not in upgrade and "nvr_credential" not in upgrade and "Unprotect" not in upgrade and "ProtectedData" not in upgrade,
        "agent exposes --version for installed/running version verification":
            '"--version"' in agent and "print(AGENT_VERSION)" in agent,
        "upgrade PRESERVES identity + secrets (no state/Secrets deletion during install)":
            ("RMDir /r" not in nsis.split('Section "Uninstall"')[0]
             and 'Delete "${DATAROOT}' not in nsis.split('Section "Uninstall"')[0]),

        # ---- install/startup path regression (the "cannot find Program Files" meeting class) ----
        "launcher builds the agent path space-safely (Join-Path, not string concat)":
            'Join-Path $InstallDir "watchlog-agent.exe"' in launcher,
        "launcher invokes the agent via the call operator (quotes a spaced path)":
            "& $agent" in launcher,
        "launcher fails loudly on a missing agent binary (no false success)":
            "Test-Path $agent" in launcher and 'throw "WatchLog Site Agent is missing"' in launcher,
        "scheduled task runs AtStartup (reboot recovery)":
            "New-ScheduledTaskTrigger -AtStartup" in register,
        "scheduled task passes runner + InstallDir QUOTED (Program Files spaces)":
            '`"$runner`"' in register and '`"$InstallDir`"' in register,
        "scheduled task auto-restarts the agent (RestartCount)":
            "-RestartCount" in register,
        "registration verifies task Running AND actual agent heartbeat (no false success)":
            'ne "Running"' in register and "background-ready.json" in register
            and "cloud heartbeat" in register and "throw" in register,
        "installer keeps the site PC awake on AC (H3 coverage) via power policy":
            "standby-timeout-ac 0" in register,
    }

    failed = [name for name, ok in checks.items() if not ok]
    for name, ok in checks.items():
        print(("PASS" if ok else "FAIL") + "  " + name)
    if failed:
        raise SystemExit("installer product contract failed: " + ", ".join(failed))
    print(f"\n{len(checks)}/{len(checks)} installer product checks passed")


if __name__ == "__main__":
    main()
