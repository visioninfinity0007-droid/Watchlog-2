#!/usr/bin/env python3
r"""Repair/Upgrade downgrade guard: 5.0.28 must not run over a multi-recorder site.

WatchLog 5.1.0+ keeps the site's recorders in %ProgramData%\WatchLog\recorders.json. The 5.0.28
Agent ignores that file and runs only the legacy single recorder, and a site with several
configured recorders refuses its legacy cloud calls while the heartbeat still looks online. So
wl-repair-upgrade.ps1 refuses, before the candidate runs or anything is paused:

  * exit 24 when recorders.json lists more than one configured recorder (a row without
    is_configured counts as configured, as in 5.1);
  * exit 25 when recorders.json exists but cannot be read or understood (the count cannot be
    proven, so it refuses rather than guess);
  * a missing file (a 5.0.x site), an empty list or one configured recorder is not blocked.

The static checks run everywhere. The behaviour checks run the real PowerShell code: the
registry reader under any PowerShell (pwsh on the Linux CI runner, Windows PowerShell 5.1 in the
Windows job), and the whole orchestrator against a sandbox ProgramData on Windows only, where
Repair runs.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REPAIR = ROOT / "prototype" / "installer" / "wl-repair-upgrade.ps1"
MULTI_MESSAGE = ("This site uses more than one recorder; WatchLog 5.0.28 cannot manage it. "
                 "Disable the extra recorders in Manage Recorders first, or install 5.1.0 "
                 "or later.")


def _source() -> str:
    return REPAIR.read_text(encoding="utf-8")


def _main_body() -> str:
    src = _source()          # read_text turns the CRLF source into \n lines
    return src[src.index("\ntry {\n"):]


def _powershell() -> str | None:
    found = shutil.which("pwsh") or shutil.which("powershell")
    if not found and os.environ.get("CI"):
        pytest.fail("CI must run the PowerShell guard; no pwsh/powershell on PATH")
    return found


def _row(n: int, **extra) -> dict:
    row = {"local_id": f"00000000-0000-4000-8000-00000000000{n}", "display_name": f"NVR {n}",
           "url": f"http://192.168.1.{60 + n}", "is_primary": n == 1,
           "continuity_owner": n == 1, "is_configured": True}
    row.update(extra)
    return row


def _registry(*rows: dict) -> str:
    return json.dumps({"schema": "watchlog.recorders.v1", "recorders": list(rows)})


# --- static: placement, codes, message --------------------------------------------------------

def test_guard_runs_before_any_candidate_or_site_change():
    body = _main_body()
    guard = body.index('$script:CurrentStage = "site recorder check"')
    for later in ('$script:CurrentStage = "existing-site readiness checks"',
                  "Protect-CandidateDirectory", "$result = Run-Candidate-AsSystem",
                  '$rc = Invoke-UpgradeHelper "preflight"', "Install-CandidatePayload"):
        assert guard < body.index(later), later


def test_guard_reads_the_programdata_registry_and_uses_distinct_exit_codes():
    src = _source()
    assert '$RecorderRegistryPath = Join-Path $DataRoot "recorders.json"' in src
    assert '"watchlog.recorders.v1"' in src
    codes = [int(c) for c in re.findall(r"\bFail (\d+) ", src)]
    assert codes.count(24) == 1 and codes.count(25) == 1, codes
    assert 'Fail 24 "This site uses more than one recorder; WatchLog $ExpectedVersion' in src
    assert "Disable the extra recorders in Manage Recorders first, or install 5.1.0" in src


# --- behaviour: the registry reader -----------------------------------------------------------

READER_CASES = {
    "absent": (None, "absent|0"),
    "empty_list": (_registry(), "ok|0"),
    "single": (_registry(_row(1)), "ok|1"),
    "single_with_disabled": (_registry(_row(1), _row(2, is_configured=False),
                                       _row(3, is_configured=False)), "ok|1"),
    "two_configured": (_registry(_row(1), _row(2)), "ok|2"),
    "flag_missing_counts": (_registry(_row(1), {k: v for k, v in _row(2).items()
                                                if k != "is_configured"}), "ok|2"),
    "not_json": ("{not json", "invalid|0"),
    "empty_file": ("", "invalid|0"),
    "wrong_schema": (json.dumps({"schema": "watchlog.recorders.v2",
                                 "recorders": [_row(1)]}), "invalid|0"),
    "no_list": (json.dumps({"schema": "watchlog.recorders.v1", "recorders": _row(1)}),
                "invalid|0"),
    "bad_row": (json.dumps({"schema": "watchlog.recorders.v1", "recorders": ["NVR 1"]}),
                "invalid|0"),
}


def _reader_function() -> str:
    src = _source().replace("\r\n", "\n")
    start = src.index("function Read-RecorderRegistry(")
    return src[start:src.index("\n}\n", start) + 3]


def test_registry_reader_counts_configured_recorders(tmp_path):
    shell = _powershell()
    if not shell:
        pytest.skip("no PowerShell on this machine")
    paths = {}
    for name, (content, _) in READER_CASES.items():
        path = tmp_path / f"{name}.json"
        if content is not None:
            path.write_text(content, encoding="utf-8")
        paths[name] = path
    script = tmp_path / "reader.ps1"
    calls = "\n".join(
        f"$r = Read-RecorderRegistry '{paths[name]}'; Write-Output ('{name}=' + $r.state + '|' + $r.configured)"
        for name in READER_CASES)
    script.write_text(_reader_function() + "\n" + calls + "\n", encoding="utf-8")
    out = subprocess.run([shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    got = dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line)
    assert got == {name: want for name, (_, want) in READER_CASES.items()}, out.stderr


# --- behaviour: the whole orchestrator on Windows ---------------------------------------------

def _run_repair(tmp_path: Path, registry: str | None) -> tuple[int, str, str]:
    shell = shutil.which("powershell")
    programdata = tmp_path / "ProgramData"
    data = programdata / "WatchLog"
    data.mkdir(parents=True)
    if registry is not None:
        (data / "recorders.json").write_text(registry, encoding="utf-8")
    candidate = tmp_path / "candidate"
    install = tmp_path / "install"
    candidate.mkdir()
    install.mkdir()
    env = dict(os.environ, PROGRAMDATA=str(programdata))
    proc = subprocess.run(
        [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(REPAIR),
         "-CandidateDir", str(candidate), "-InstallDir", str(install),
         "-ExpectedVersion", "5.0.28"],
        capture_output=True, text=True, timeout=120, env=env)
    result = data / "repair-upgrade-result.ini"
    log = data / "repair-upgrade.log"
    return (proc.returncode, result.read_text(encoding="ascii") if result.exists() else "",
            log.read_text(encoding="utf-8", errors="replace") if log.exists() else "")


windows_only = pytest.mark.skipif(os.name != "nt" or not shutil.which("powershell"),
                                  reason="Repair/Upgrade runs on Windows PowerShell")


@windows_only
def test_repair_refuses_a_site_with_two_configured_recorders(tmp_path):
    code, result, log = _run_repair(tmp_path, _registry(_row(1), _row(2)))
    assert code == 24, log
    assert "status=failed" in result and "code=24" in result
    assert "stage=site recorder check" in result
    assert f"message={MULTI_MESSAGE}" in result
    assert "recovery=Your installed WatchLog has not been replaced." in result
    assert "candidate directory ACL verified" not in log and "phase 1/2" not in log


@windows_only
def test_repair_refuses_an_unreadable_registry_with_its_own_code(tmp_path):
    code, result, log = _run_repair(tmp_path, "{not json")
    assert code == 25, log
    assert "code=25" in result and "could not read this site's recorder list" in result


@windows_only
@pytest.mark.parametrize("registry", [None, _registry(_row(1), _row(2, is_configured=False))],
                         ids=["no_registry_5_0_x_site", "one_configured_recorder"])
def test_repair_does_not_block_a_single_recorder_site(tmp_path, registry):
    # The sandbox has no enrolled site, so Repair goes past the guard and stops at the
    # existing-site readiness check (exit 20), which proves the guard let it through.
    code, result, log = _run_repair(tmp_path, registry)
    assert code == 20, log
    assert "stage=existing-site readiness checks" in result


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
