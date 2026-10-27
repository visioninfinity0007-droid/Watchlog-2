"""Server move (5.1.5): a site still on a retired backend is moved to the build's server.

Covers both implementations of the one deliberate overwrite of a site's config:
  * setup_backend.merge_public_defaults (Python, the installer --migrate-only path);
  * Move-RetiredServer in wl-repair-upgrade.ps1 (the Repair/Upgrade path existing sites take),
    extracted from the script and run against temporary files (skipped without PowerShell).
"""
from __future__ import annotations

import configparser
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "prototype" / "agent"))

import setup_backend  # noqa: E402

OLD = "https://old-project.supabase.co"
NEW = "https://supa.example-server.test"

SITE_INI = f"""[watchlog]
supabase_url = {OLD}
supabase_publishable_key = old-public-key-0000000000
nvr_url = http://192.168.1.10
nvr_username = admin
site_name = Main site
"""

DEFAULTS = f"""[watchlog]
supabase_url = {NEW}
supabase_publishable_key = new-public-key-1111111111
enrollment_code =
nvr_driver = auto
retired_supabase_urls = {OLD}/
"""


def _ini(path: Path) -> dict:
    cp = configparser.ConfigParser()
    cp.read(path, encoding="utf-8-sig")
    return dict(cp["watchlog"])


def test_merge_moves_site_off_retired_backend(tmp_path):
    cfg = tmp_path / "watchlog.ini"; cfg.write_text(SITE_INI, encoding="utf-8")
    (tmp_path / "watchlog.defaults.ini").write_text(DEFAULTS, encoding="utf-8")
    added = setup_backend.merge_public_defaults(cfg)
    got = _ini(cfg)
    assert got["supabase_url"] == NEW
    assert got["supabase_publishable_key"] == "new-public-key-1111111111"
    assert got["nvr_url"] == "http://192.168.1.10" and got["nvr_username"] == "admin"
    assert "retired_supabase_urls" not in got and "supabase_url" in added


def test_merge_leaves_other_servers_alone(tmp_path):
    cfg = tmp_path / "watchlog.ini"
    cfg.write_text(SITE_INI.replace(OLD, "https://some-other-server.test"), encoding="utf-8")
    (tmp_path / "watchlog.defaults.ini").write_text(DEFAULTS, encoding="utf-8")
    setup_backend.merge_public_defaults(cfg)
    got = _ini(cfg)
    assert got["supabase_url"] == "https://some-other-server.test"
    assert got["supabase_publishable_key"] == "old-public-key-0000000000"


def test_merge_without_retired_list_never_overwrites(tmp_path):
    cfg = tmp_path / "watchlog.ini"; cfg.write_text(SITE_INI, encoding="utf-8")
    (tmp_path / "watchlog.defaults.ini").write_text(DEFAULTS.replace(f"retired_supabase_urls = {OLD}/", ""), encoding="utf-8")
    setup_backend.merge_public_defaults(cfg)
    assert _ini(cfg)["supabase_url"] == OLD


POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")


def _run_move(tmp_path: Path, defaults_text: str) -> subprocess.CompletedProcess:
    script = (ROOT / "prototype" / "installer" / "wl-repair-upgrade.ps1").read_text(encoding="utf-8")
    start = script.index("function Read-IniMap")
    end = script.index("try {", script.index("function Move-RetiredServer"))
    functions = script[start:end]
    cand = tmp_path / "cand"; cand.mkdir()
    (cand / "watchlog.defaults.ini").write_text(defaults_text, encoding="utf-8")
    harness = tmp_path / "harness.ps1"
    harness.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f"$CandidateDir = '{cand}'\n$ConfigPath = '{tmp_path / 'watchlog.ini'}'\n"
        "function Write-Repair([string]$m) { Write-Output \"LOG $m\" }\n"
        "function Fail([int]$c, [string]$m) { Write-Output \"FAIL $c $m\"; exit $c }\n"
        + functions + "\nMove-RetiredServer\n", encoding="utf-8")
    return subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
                          capture_output=True, text=True, timeout=60)


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell not available")
def test_repair_upgrade_moves_retired_site(tmp_path):
    (tmp_path / "watchlog.ini").write_text(SITE_INI, encoding="utf-8")
    run = _run_move(tmp_path, DEFAULTS)
    assert run.returncode == 0, run.stdout + run.stderr
    got = _ini(tmp_path / "watchlog.ini")
    assert got["supabase_url"] == NEW and got["supabase_publishable_key"] == "new-public-key-1111111111"
    assert got["nvr_url"] == "http://192.168.1.10" and got["site_name"] == "Main site"
    backups = list(tmp_path.glob("watchlog.ini.before-server-move-*"))
    assert len(backups) == 1 and OLD in backups[0].read_text(encoding="utf-8")


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell not available")
def test_repair_upgrade_skips_site_already_moved(tmp_path):
    (tmp_path / "watchlog.ini").write_text(SITE_INI.replace(OLD, NEW), encoding="utf-8")
    run = _run_move(tmp_path, DEFAULTS)
    assert run.returncode == 0 and "not needed" in run.stdout
    assert not list(tmp_path.glob("watchlog.ini.before-server-move-*"))


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell not available")
def test_repair_upgrade_refuses_package_retiring_its_own_server(tmp_path):
    (tmp_path / "watchlog.ini").write_text(SITE_INI, encoding="utf-8")
    run = _run_move(tmp_path, DEFAULTS.replace(f"retired_supabase_urls = {OLD}/", f"retired_supabase_urls = {NEW}"))
    assert run.returncode == 26
    assert _ini(tmp_path / "watchlog.ini")["supabase_url"] == OLD
