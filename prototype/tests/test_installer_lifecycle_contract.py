#!/usr/bin/env python3
"""5.1.1 installer lifecycle contracts that live in the NSIS scripts (no NSIS needed).

makensis itself is compiled by the installer-contract CI job; these checks pin what the scripts
must say: Repair/Upgrade writes the current uninstaller, both installers record the installed
component set, the Start menu is for all users, and uninstall removes every WatchLog task and
the leftovers the audit listed, by the same policy as wl-upgrade.ps1 -Stage uninstall.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NSIS = ROOT / "prototype" / "installer" / "nsis"
FULL = (NSIS / "watchlog.nsi").read_text(encoding="utf-8").replace("\r\n", "\n")
REPAIR = (NSIS / "watchlog-repair.nsi").read_text(encoding="utf-8").replace("\r\n", "\n")
HELPER = (NSIS / "wl-upgrade.ps1").read_text(encoding="utf-8").replace("\r\n", "\n")


def uninstall_section(text: str) -> str:
    start = text.index('Section "Uninstall"\n')
    return text[start:text.index("SectionEnd\n", start)]


def test_repair_writes_the_same_uninstaller_as_the_full_installer():
    """A 5.0.26 site upgraded by Repair kept its 5.0.26 uninstall.exe, which does not know
    recorders.json, Secrets\\recorders or the 5.1 shortcuts."""
    assert uninstall_section(FULL) == uninstall_section(REPAIR)
    assert 'WriteUninstaller "$INSTDIR\\uninstall.exe"' in REPAIR
    assert "!insertmacro MUI_UNPAGE_INSTFILES" in REPAIR


def test_repair_changes_the_install_record_only_after_success():
    failure = REPAIR.index("SetErrorLevel $9")
    for needle in ('WriteUninstaller "$INSTDIR\\uninstall.exe"', '"ComponentsVersion" "${APPVERSION}"',
                   '"UninstallString"', 'Uninstall WatchLog.lnk" "$INSTDIR\\uninstall.exe"'):
        assert REPAIR.index(needle) > failure, needle


def test_both_installers_record_the_installed_component_set():
    for text in (FULL, REPAIR):
        assert 'WriteRegStr HKLM "${ARPKEY}" "ComponentsVersion" "${APPVERSION}"' in text


def test_start_menu_shortcuts_are_for_all_users_and_old_per_user_copies_go():
    for text in (FULL, REPAIR):
        body = text[:text.index('Section "Uninstall"')]
        first_shortcut = body.index("CreateShortcut")
        assert body.rindex("SetShellVarContext all", 0, first_shortcut) > body.rindex(
            "SetShellVarContext current", 0, first_shortcut)
    section = uninstall_section(FULL)
    assert section.count("SetShellVarContext") == 2 and 'Delete "${STARTMENU}\\WatchLog Setup.lnk"' in section


def test_uninstall_uses_the_uninstall_stage_and_removes_every_watchlog_task():
    section = uninstall_section(FULL)
    assert "-Stage uninstall" in section and "-Stage preflight" not in section
    assert '/Delete /TN "${TASKNAME} Upgrade Recovery" /F' in section
    # The helper also removes orphaned candidate preflight tasks (wildcard, PowerShell only).
    assert 'Get-ScheduledTask -TaskName "WatchLog Candidate Preflight *"' in HELPER


AUDIT_A_LEFTOVERS = {
    "$INSTDIR": ["watchlog.defaults.ini", "watchlog-agent.exe.remote.bak", "wl-upgrade-recover.ps1",
                 "watchlog-agent.next.verify"],
    "${DATAROOT}": ["analytics_spool.sqlite", "recorder_identity.json", "camera_profiles.json",
                    "recorder_auth_backoff.json", "recorders.json.quarantine-*",
                    "upgrade-in-progress.json"],
}


def test_uninstall_removes_the_leftovers_the_audit_listed():
    section = uninstall_section(FULL)
    for root, names in AUDIT_A_LEFTOVERS.items():
        for name in names:
            assert f'Delete "{root}\\{name}"' in section, name
    for folder in ("remote-update", "upgrade-backup", "repair-candidate", "recorders", "Secrets"):
        assert f'RMDir /r "${{DATAROOT}}\\{folder}"' in section, folder


def test_nsis_never_deletes_what_the_policy_keeps():
    keep = re.search(r"\$UninstallKeepNames = @\(([^)]*)\)", HELPER).group(1)
    kept = set(re.findall(r'"([^"]+)"', keep))
    assert kept == {"agent.log", "agent.log.old", "upgrade.log", "repair-upgrade.log",
                    "repair-upgrade-result.ini", "setup.log"}
    deleted = set(re.findall(r'Delete "\$\{DATAROOT\}\\([^"]+)"', uninstall_section(FULL)))
    assert not (kept & deleted)


def test_the_nsis_fallback_removes_the_5_1_1_site_data():
    # If PowerShell cannot run, NSIS's own deletes are the uninstall: they must name the site
    # stamp, another site's set-aside files and the rejected-row dead letter (5.1.1 additions).
    import sys
    sys.path.insert(0, str(ROOT / "prototype" / "agent"))
    import site_runtime
    deleted = set(re.findall(r'Delete "\$\{DATAROOT\}\\([^"]+)"', uninstall_section(FULL)))
    assert {site_runtime.STAMP_NAME, "*.site-*", "*.rejected.jsonl", ".recorders.*.tmp"} <= deleted
    for name in site_runtime.RUNTIME_FILES:
        assert name in deleted, name


def test_power_settings_are_restored_on_uninstall_from_the_install_record():
    reg = (ROOT / "prototype" / "installer" / "register-service.ps1").read_text(encoding="utf-8")
    save = reg.index("try { Save-PowerBaseline }")
    change = reg.index("try { Set-SiteAwakePower }")
    assert save < change
    assert "power baseline already recorded; not overwritten" in reg
    stage = HELPER.split("  'uninstall' {", 1)[1]
    assert stage.index("Restore-PowerBaseline") < stage.index("Remove-UninstallLeftovers")


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
