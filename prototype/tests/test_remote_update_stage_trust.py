#!/usr/bin/env python3
"""T0-SEC1: a standard user must not be able to get code run as SYSTEM through remote update.

run-agent.ps1 (SYSTEM) applies %ProgramData%\\WatchLog\\remote-update\\pending.json and the
staged exe. %ProgramData% lets a standard user create files in a new folder, and the apply
script used to trust the SHA-256 written inside pending.json itself. Now:
  * the Agent stages only into a folder it has protected (SYSTEM + Administrators, verified),
    after removing leftovers it did not write;
  * apply-remote-update.ps1 refuses (exit 3, nothing read or run) unless the staging folder is
    owned by SYSTEM/Administrators with a protected DACL granting nobody else, and pending.json
    and the package are owned by SYSTEM/Administrators;
  * the package is copied into the install folder (administrators only) and the hash and
    ProductVersion are verified on that copy, which is what gets installed;
  * install/repair protect the whole %ProgramData%\\WatchLog folder (Users: read only).
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "prototype" / "agent"
INSTALLER = ROOT / "prototype" / "installer"
sys.path.insert(0, str(AGENT))

APPLY = (INSTALLER / "apply-remote-update.ps1").read_text(encoding="utf-8")
REGISTER = (INSTALLER / "register-service.ps1").read_text(encoding="utf-8")


def _pos(text: str, needle: str) -> int:
    i = text.find(needle)
    assert i >= 0, f"missing: {needle}"
    return i


def test_trust_gate_runs_before_anything_is_read():
    gate = _pos(APPLY, "$untrusted = Get-UntrustedStageReason")
    assert gate < _pos(APPLY, "Get-Content -LiteralPath $pendingPath")
    assert gate < _pos(APPLY, "Get-FileHash -LiteralPath $verifyPath")
    refused = APPLY[gate:gate + 700]
    assert "exit 3" in refused and "Remove-Item -LiteralPath $packagePath" in refused


def test_gate_checks_folder_owner_protection_aces_and_file_owners():
    body = APPLY[_pos(APPLY, "function Get-UntrustedStageReason"):]
    body = body[:body.find("\n}\n") + 3]
    assert "$TrustedSids -notcontains $owner" in body
    assert "AreAccessRulesProtected" in body
    assert "IdentityReference.Translate" in body
    assert "foreach ($file in @($pendingPath, $packagePath))" in body
    assert re.search(r"\$TrustedSids = @\('S-1-5-18', 'S-1-5-32-544'\)", APPLY)


def test_install_comes_from_the_verified_copy_in_program_files():
    assert '$verifyPath = Join-Path $InstallDir' in APPLY
    assert "Copy-Item -LiteralPath $packagePath -Destination $verifyPath" in APPLY
    assert "Copy-Item -LiteralPath $verifyPath -Destination $agentPath" in APPLY
    assert "Copy-Item -LiteralPath $packagePath -Destination $agentPath" not in APPLY
    assert "VersionInfo.ProductVersion" in APPLY[_pos(APPLY, "Get-FileHash -LiteralPath $verifyPath"):]


def test_install_and_repair_protect_the_whole_data_folder():
    call = _pos(REGISTER, "Protect-WatchLogData $data")
    assert call < _pos(REGISTER, "New-ScheduledTaskAction")
    fn = REGISTER[_pos(REGISTER, "function Protect-WatchLogData"):call]
    assert "SetAccessRuleProtection($true, $false)" in fn
    assert "@('S-1-5-32-545', 'ReadAndExecute')" in fn
    assert "FullControl" in fn and fn.count("FullControl") == 2
    assert "still lets $sid write" in fn


class _NtOs:
    """remote_update alone sees Windows; patching os.name itself makes pathlib build
    WindowsPath objects, which cannot exist on the Linux CI runner."""
    name = "nt"

    def __getattr__(self, attr):
        import os
        return getattr(os, attr)


def test_agent_refuses_to_stage_when_the_folder_cannot_be_protected(monkeypatch, tmp_path):
    import remote_update
    import windows_secret
    monkeypatch.setattr(remote_update, "os", _NtOs())

    def cannot(_path):
        raise windows_secret.SecretError("Set-Acl failed")
    monkeypatch.setattr(windows_secret, "ensure_secure_dir", cannot)
    with pytest.raises(RuntimeError, match="could not be protected"):
        remote_update._protect_staging(tmp_path / "remote-update")


def test_agent_removes_leftovers_it_did_not_write(monkeypatch, tmp_path):
    import remote_update
    import windows_secret
    root = tmp_path / "remote-update"
    root.mkdir()
    for name in ("pending.json", "watchlog-agent.next.exe", "watchlog-agent.next.exe.part"):
        (root / name).write_text("planted", encoding="utf-8")
    monkeypatch.setattr(remote_update, "os", _NtOs())
    monkeypatch.setattr(windows_secret, "ensure_secure_dir", lambda p: None)
    remote_update._protect_staging(root)
    assert sorted(p.name for p in root.iterdir()) == []


def test_download_protects_the_folder_before_writing(monkeypatch, tmp_path):
    import remote_update
    order = []
    monkeypatch.setattr(remote_update, "_protect_staging",
                        lambda root: (root.mkdir(parents=True, exist_ok=True), order.append("protect")))

    class Resp:
        def __enter__(self):
            order.append("download")
            return self

        def __exit__(self, *a):
            return False

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield b"MZ"
    monkeypatch.setattr(remote_update.requests, "get", lambda *a, **k: Resp())
    monkeypatch.setattr(remote_update.updater, "verify_payload", lambda *a: (True, ""))
    cfg = SimpleNamespace(state_path=tmp_path / "agent_state.json")
    remote_update._download_verified(cfg, {"url": "https://example/x.exe", "sha256": "0" * 64, "size": 2})
    assert order == ["protect", "download"]


def _is_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _is_admin(), reason="needs elevated Windows to set a non-admin owner")
def test_planted_stage_owned_by_a_standard_user_is_refused_and_nothing_runs(tmp_path):
    pdata = tmp_path / "ProgramData"
    stage = pdata / "WatchLog" / "remote-update"
    stage.mkdir(parents=True)
    install = tmp_path / "Program Files" / "WatchLog"
    install.mkdir(parents=True)
    agent = install / "watchlog-agent.exe"
    agent.write_bytes(b"ORIGINAL")
    payload = b"MZ planted"
    import hashlib
    (stage / "watchlog-agent.next.exe").write_bytes(payload)
    (stage / "pending.json").write_text(json.dumps({
        "request_id": "r1", "target_version": "9.9.9",
        "sha256": hashlib.sha256(payload).hexdigest()}), encoding="utf-8")
    for f in ("pending.json", "watchlog-agent.next.exe"):
        subprocess.run(["icacls", str(stage / f), "/setowner", "*S-1-5-32-545"],
                       check=True, capture_output=True)
    env = dict(os.environ, ProgramData=str(pdata))
    r = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                        str(INSTALLER / "apply-remote-update.ps1"), "-InstallDir", str(install)],
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 3, r.stdout + r.stderr
    assert agent.read_bytes() == b"ORIGINAL"
    assert not (stage / "pending.json").exists()
    result = json.loads((stage / "result.json").read_text(encoding="utf-8-sig"))
    assert result["ok"] is False and "refused" in result["detail"]
