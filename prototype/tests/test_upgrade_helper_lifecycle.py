#!/usr/bin/env python3
r"""5.1.1 upgrade-helper and machine lifecycle (wl-upgrade.ps1, register-service.ps1).

Real runs of nsis/wl-upgrade.ps1 against a temporary install folder and ProgramData
(-DataRootOverride) and a task name no PC has, plus function-level runs where a step needs
elevation (scheduled-task registration) and is stood in:

  * rollback PROVES the previous Agent: exit 0 only with a fresh runtime-health heartbeat from
    the restored version; 15 without it, 17 when no proof was possible, 14 when the task cannot
    run; never a bare "task Running";
  * a copy error during rollback still re-enables the task, keeps the backup for another
    attempt and is a hard failure (16);
  * an interrupted upgrade (owner process gone: killed, power loss) is recovered by the
    recovery stage the armed task runs; a live owner is left alone; attempts are bounded;
  * uninstall removes tasks, restores the recorded power settings and applies the documented
    data policy (support logs kept, everything else removed);
  * register-service.ps1 records the power settings once, before changing them, and never
    overwrites that record.

Windows PowerShell only; the Windows CI job (setup-ui-build) runs this file. Registering a
SYSTEM task needs elevation: on a non-elevated PC those steps log a warning or fail exactly as
asserted below, and the elevated CI runner registers them for real.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
from ps_function_harness import POWERSHELL, ps_quote, run_functions, run_script  # noqa: E402

ROOT = TESTS.parents[1]
HELPER = ROOT / "prototype" / "installer" / "nsis" / "wl-upgrade.ps1"
REGISTER = ROOT / "prototype" / "installer" / "register-service.ps1"

pytestmark = pytest.mark.skipif(os.name != "nt" or not POWERSHELL,
                                reason="the upgrade helper runs on Windows PowerShell")


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


@pytest.fixture()
def site(tmp_path):
    install = tmp_path / "Program Files" / "WatchLog"
    data = tmp_path / "ProgramData" / "WatchLog"
    install.mkdir(parents=True)
    (data / "Secrets").mkdir(parents=True)
    task = "WatchLog Test " + uuid.uuid4().hex[:8]
    return {"install": install, "data": data, "task": task, "root": tmp_path}


def helper(site, stage, *extra, timeout=240):
    return run_script(HELPER, ["-Stage", stage, "-InstallDir", str(site["install"]),
                               "-DataRootOverride", str(site["data"]), "-TaskName", site["task"],
                               "-StopTimeoutSec", "3", "-StartTimeoutSec", "3", *extra],
                      timeout=timeout)


def _log(site) -> str:
    path = site["data"] / "upgrade.log"
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _backup(site, files: dict[str, bytes], **meta):
    root = site["data"] / "upgrade-backup"
    root.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (root / name).write_bytes(content)
    manifest = {"schema": "watchlog.upgrade_backup.v2", "created_at": iso(datetime.now(timezone.utc)),
                "task_present": True, "task_enabled": True, "existing_files": list(files),
                "previous_version": "5.0.26", "health_before": True, **meta}
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


class _ExclusiveLock:
    """Hold a file open with no sharing, like a running exe or antivirus scan."""

    def __init__(self, path: Path):
        self.path = path

    def __enter__(self):
        k32 = ctypes.windll.kernel32
        k32.CreateFileW.restype = ctypes.c_void_p
        self.handle = k32.CreateFileW(str(self.path), 0x80000000 | 0x40000000, 0, None, 3, 0x80, None)
        assert self.handle not in (None, ctypes.c_void_p(-1).value), "could not lock the file"
        return self

    def __exit__(self, *a):
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(self.handle))


# --- rollback: copy error, proof ---------------------------------------------------------------

def test_a_rollback_copy_error_re_enables_the_task_and_keeps_the_backup(site):
    """5.1.0: Copy-Item threw inside the rollback switch, the script exited 1 and the task
    stayed Disabled; NSIS then advised a reboot, which does not start a disabled task."""
    agent = site["install"] / "watchlog-agent.exe"
    agent.write_bytes(b"NEW-5.1.1")
    _backup(site, {"watchlog-agent.exe": b"OLD-5.0.26"})
    with _ExclusiveLock(agent):
        proc = helper(site, "rollback")
    log = _log(site)
    assert proc.returncode == 16, proc.stdout + proc.stderr
    assert "payload restore attempt 3/3 failed" in log
    assert "ensuring WatchLog background task: rollback recovery" in log      # re-enable tried
    assert (site["data"] / "upgrade-backup" / "manifest.json").exists()       # kept for retry
    meta = json.loads((site["data"] / "upgrade-backup" / "manifest.json").read_text(encoding="utf-8-sig"))
    assert meta["restore_failed"] is True


def test_rollback_restore_then_start_keeps_the_task_suspended_in_between(site):
    agent = site["install"] / "watchlog-agent.exe"
    agent.write_bytes(b"NEW-5.1.1")
    _backup(site, {"watchlog-agent.exe": b"OLD-5.0.26"})
    proc = helper(site, "rollback-restore")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert agent.read_bytes() == b"OLD-5.0.26"
    assert "ensuring WatchLog background task" not in _log(site)             # not started yet
    # No task can run here (not elevated, no register-service.ps1): start reports 14 and keeps
    # the backup for the recovery task to retry.
    proc = helper(site, "rollback-start", "-ProveHealth", "-ProofTimeoutSec", "5")
    assert proc.returncode == 14, proc.stdout + proc.stderr
    assert (site["data"] / "upgrade-backup" / "manifest.json").exists()


def _start(site, *, health_before=True, prove=False, writer="", ensure=True,
           previous="5.0.26") -> int:
    """Invoke-RollbackStart with the task start stood in. ``writer`` is what the restored
    Agent writes to runtime-health once its task starts."""
    health = site["data"] / "Secrets" / "runtime-health.json"
    body = f"""
$InstallDir = {ps_quote(site['install'])}
$DataRoot = {ps_quote(site['data'])}
$UpgradeLog = Join-Path $DataRoot "upgrade.log"
$HealthPath = {ps_quote(health)}
$Stage = "rollback-start"; $PayloadProfile = "repair"
$ProofTimeoutSec = 4; $FutureSkewSec = 120
function Ensure-WatchLogBackgroundTask([string]$Reason) {{
  {writer}
  return ${'true' if ensure else 'false'}
}}
$meta = [pscustomobject]@{{ previous_version = {ps_quote(previous)}; health_before = ${'true' if health_before else 'false'} }}
"CODE=" + (Invoke-RollbackStart $meta ${'true' if prove else 'false'})
"""
    proc = run_functions(HELPER, body, work=site["root"])
    line = [x for x in proc.stdout.splitlines() if x.startswith("CODE=")]
    assert line, proc.stdout + proc.stderr
    return int(line[-1][5:])


def _writer(site, version="5.0.26", offset=0) -> str:
    health = site["data"] / "Secrets" / "runtime-health.json"
    return (f"@{{ agent_version = {ps_quote(version)}; heartbeat_at = "
            f"[DateTimeOffset]::UtcNow.AddSeconds({offset}).ToString('o') }} | ConvertTo-Json | "
            f"Set-Content -LiteralPath {ps_quote(health)} -Encoding UTF8")


def test_rollback_is_proven_only_by_a_fresh_heartbeat_from_the_previous_version(site):
    assert _start(site, writer=_writer(site)) == 0


def test_task_running_without_a_heartbeat_is_not_proof(site):
    """Fault rows 'Agent exits immediately' / 'heartbeat absent' after a rollback: the task (the
    launcher loop) runs, the Agent never heartbeats."""
    assert _start(site, writer="") == 15


def test_a_stale_or_future_or_wrong_version_heartbeat_is_not_proof(site):
    assert _start(site, writer=_writer(site, offset=-3600)) == 15
    assert _start(site, writer=_writer(site, offset=30 * 24 * 3600)) == 15
    assert _start(site, writer=_writer(site, version="5.1.1")) == 15


def test_no_proof_possible_is_reported_as_such(site):
    # The site was not heartbeating before the upgrade and the caller did not demand proof.
    assert _start(site, health_before=False, writer="") == 17
    # Repair demands proof (-ProveHealth) regardless.
    assert _start(site, health_before=False, prove=True, writer="") == 15


def test_a_task_that_cannot_be_started_is_a_hard_failure(site):
    assert _start(site, ensure=False, writer=_writer(site)) == 14


def test_the_backup_records_what_a_rollback_must_prove(site):
    (site["install"] / "watchlog-agent.exe").write_bytes(b"OLD")
    health = site["data"] / "Secrets" / "runtime-health.json"
    health.write_text(json.dumps({"agent_version": "5.0.26",
                                  "heartbeat_at": iso(datetime.now(timezone.utc))}), encoding="utf-8")
    proc = helper(site, "preflight")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    meta = json.loads((site["data"] / "upgrade-backup" / "manifest.json").read_text(encoding="utf-8-sig"))
    assert meta["health_before"] is True
    assert "previous_version" in meta


# --- interrupted upgrade recovery ---------------------------------------------------------------

def _arm(site):
    (site["install"] / "watchlog-agent.exe").write_bytes(b"OLD-5.0.26")
    proc = helper(site, "preflight", "-ArmRecovery", "-ProveHealth", "-OwnerPid", str(os.getpid()),
                  "-PayloadProfile", "repair")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return site["data"] / "upgrade-in-progress.json"


def test_preflight_arms_recovery_with_the_owner_and_a_protected_script_copy(site):
    marker = _arm(site)
    m = json.loads(marker.read_text(encoding="utf-8-sig"))
    assert m["owner_pid"] == os.getpid() and m["owner_started_at"]
    assert m["prove_health"] is True and m["attempts"] == 0
    # The recovery task runs a copy in the install folder (administrators only), never one in
    # ProgramData.
    assert (site["install"] / "wl-upgrade-recover.ps1").exists()
    log = _log(site)
    assert ("recovery armed" in log) or ("recovery task could not be registered" in log)


def test_recovery_leaves_a_live_upgrade_alone(site):
    _arm(site)
    agent = site["install"] / "watchlog-agent.exe"
    agent.write_bytes(b"NEW-5.1.1")
    proc = helper(site, "recover")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "still running; nothing to recover" in _log(site)
    assert agent.read_bytes() == b"NEW-5.1.1"


def test_recovery_restores_the_previous_watchlog_when_the_owner_is_gone(site):
    """Power loss / killed orchestrator mid phase 2: the task is Disabled and nothing used to
    bring it back. The recovery stage restores the payload and re-enables the task."""
    marker = _arm(site)
    agent = site["install"] / "watchlog-agent.exe"
    agent.write_bytes(b"NEW-5.1.1")
    m = json.loads(marker.read_text(encoding="utf-8-sig"))
    m["owner_pid"] = 4_000_000                 # no such process
    m["registry_staged_id"] = "aaaaaaaa-0000-4000-8000-000000000001"
    marker.write_text(json.dumps(m), encoding="utf-8")
    proc = helper(site, "recover", "-ProofTimeoutSec", "3")
    log = _log(site)
    assert agent.read_bytes() == b"OLD-5.0.26", log
    assert "INTERRUPTED UPGRADE" in log
    assert "ensuring WatchLog background task: rollback recovery" in log
    assert "registry staged by the interrupted Repair" in log
    # Not elevated: the task cannot be registered, so the recovery keeps itself armed (14).
    assert proc.returncode == 14, proc.stdout + proc.stderr
    assert json.loads(marker.read_text(encoding="utf-8-sig"))["attempts"] == 1
    result = (site["data"] / "repair-upgrade-result.ini").read_text(encoding="ascii")
    assert "stage=interrupted upgrade recovery" in result and "status=failed" in result


def test_recovery_attempts_are_bounded(site):
    marker = _arm(site)
    m = json.loads(marker.read_text(encoding="utf-8-sig"))
    m.update(owner_pid=4_000_000, attempts=3)
    marker.write_text(json.dumps(m), encoding="utf-8")
    proc = helper(site, "recover")
    assert proc.returncode == 18, proc.stdout + proc.stderr
    assert not marker.exists()
    assert not (site["install"] / "wl-upgrade-recover.ps1").exists()


def test_no_marker_means_nothing_to_recover(site):
    proc = helper(site, "recover")
    assert proc.returncode == 0


def test_commit_and_every_rollback_outcome_disarm_or_keep_recovery_deliberately():
    src = HELPER.read_text(encoding="utf-8").replace("\r\n", "\n")
    commit = src.split("  'commit' {", 1)[1].split("  'rollback' {", 1)[0]
    assert "Disarm-Recovery" in commit
    complete = src[src.index("function Complete-Rollback"):]
    complete = complete[:complete.index("\n}\n")]
    # Kept on 16 (restore failed) and 14 (task not running) so the task retries; cleared once
    # the task runs (0, 15, 17).
    assert complete.index("Fail 16") < complete.index("switch ($StartCode)")
    for code in ("0 {", "15 {", "default {"):
        branch = complete[complete.index(code):]
        assert "Disarm-Recovery" in branch[:branch.index("}")+400]
    fourteen = complete[complete.index("14 {"):complete.index("15 {")]
    assert "Disarm-Recovery" not in fourteen


# --- uninstall: tasks, power, data policy -------------------------------------------------------

POWERCFG_STUB = r"""@echo off
>>"{log}" echo %*
if /I "%~1"=="/getactivescheme" (
  echo Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  ^(Balanced^)
  exit /b 0
)
if /I "%~1"=="/query" goto query
exit /b 0
:query
set "AC=0x00000000"
if /I "%~4"=="STANDBYIDLE" set "AC={standby}"
if /I "%~4"=="HIBERNATEIDLE" set "AC={hibernate}"
if /I "%~4"=="DISKIDLE" set "AC={disk}"
echo       Minimum Possible Setting: 0x00000000
echo       Maximum Possible Setting: 0xffffffff
echo       Possible Settings increment: 0x00000001
echo     Current AC Power Setting Index: %AC%
echo     Current DC Power Setting Index: 0x00000258
exit /b 0
"""


def _powercfg(folder: Path, standby="0x00000708", hibernate="0x00002a30", disk="0x000004b0"):
    log = folder / "powercfg.log"
    stub = folder / "powercfg-stub.cmd"
    stub.write_text(POWERCFG_STUB.format(log=log, standby=standby, hibernate=hibernate, disk=disk),
                    encoding="ascii")
    return stub, log


def _register_power(site, stub: Path, hibernate_enabled=1) -> str:
    body = f"""
$data = {ps_quote(site['data'])}
$PowerCfg = {ps_quote(stub)}
$PowerBaselinePath = Join-Path $data "Secrets\\power-baseline.json"
$PowerSettings = @(
  @{{ name = "standby-timeout-ac";   subgroup = "SUB_SLEEP"; setting = "STANDBYIDLE" }},
  @{{ name = "hibernate-timeout-ac"; subgroup = "SUB_SLEEP"; setting = "HIBERNATEIDLE" }},
  @{{ name = "disk-timeout-ac";      subgroup = "SUB_DISK";  setting = "DISKIDLE" }}
)
function Get-HibernateEnabled {{ return {hibernate_enabled} }}
Save-PowerBaseline
Set-SiteAwakePower
"DONE"
"""
    proc = run_functions(REGISTER, body, work=site["root"])
    assert "DONE" in proc.stdout, proc.stdout + proc.stderr
    return proc.stdout


def test_register_service_records_the_power_settings_once_before_changing_them(site):
    stub, log = _powercfg(site["root"])
    _register_power(site, stub)
    baseline = json.loads((site["data"] / "Secrets" / "power-baseline.json").read_text(encoding="utf-8-sig"))
    values = {s["name"]: s["ac_value"] for s in baseline["settings"]}
    assert values == {"standby-timeout-ac": 1800, "hibernate-timeout-ac": 10800, "disk-timeout-ac": 1200}
    assert baseline["scheme_guid"] == "381b4222-f694-41f0-9685-ff5bb260df2e"
    assert baseline["hibernate_enabled"] == 1
    calls = log.read_text(encoding="ascii").splitlines()
    first_change = next(i for i, c in enumerate(calls) if c.startswith("/change"))
    assert all(c.startswith("/query") or c.startswith("/getactivescheme") for c in calls[:first_change])
    assert "/change standby-timeout-ac 0" in calls and "/hibernate off" in calls
    # Repair, rollback and every re-run call register-service again: the record is never
    # overwritten with WatchLog's own values.
    stub2, _ = _powercfg(site["root"], standby="0x00000000", hibernate="0x00000000", disk="0x00000000")
    out = _register_power(site, stub2, hibernate_enabled=0)
    assert "already recorded; not overwritten" in out
    again = json.loads((site["data"] / "Secrets" / "power-baseline.json").read_text(encoding="utf-8-sig"))
    assert again == baseline


def test_uninstall_restores_power_removes_tasks_and_applies_the_data_policy(site):
    stub, log = _powercfg(site["root"])
    _register_power(site, stub)                                   # install-time record
    data, install = site["data"], site["install"]
    keep = ["agent.log", "agent.log.old", "upgrade.log", "repair-upgrade.log", "repair-upgrade-result.ini", "setup.log"]
    for name in keep:
        (data / name).write_text("log", encoding="utf-8")
    remove_files = ["agent_state.json", "spool.sqlite", "health.sqlite", "analytics_spool.sqlite",
                    "analytics_config.json", "recorder_identity.json", "camera_profiles.json",
                    "recorder_auth_backoff.json", "recorders.json", "recorders.json.quarantine-20261001",
                    "last_live.json", "run-agent.pid", "upgrade-in-progress.json", "background-ready.json"]
    for name in remove_files:
        (data / name).write_text("x", encoding="utf-8")
    for folder in ("Secrets/recorders", "recorders/abc", "recorders.quarantine-20261001",
                   "remote-update", "upgrade-backup", "repair-candidate"):
        (data / folder).mkdir(parents=True, exist_ok=True)
        (data / folder / "f").write_text("x", encoding="utf-8")
    (data / "Secrets" / "agent_key.dpapi").write_bytes(b"k")
    for name in ("watchlog.defaults.ini", "watchlog-agent.exe.remote.bak", "wl-upgrade-recover.ps1",
                 "watchlog-agent.next.verify", "watchlog-agent.exe.wlbak"):
        (install / name).write_text("x", encoding="utf-8")
    log.unlink()
    proc = helper(site, "uninstall", "-PowerCfgPath", str(stub))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert sorted(p.name for p in data.iterdir()) == sorted(keep)
    assert sorted(p.name for p in install.iterdir()) == []
    calls = log.read_text(encoding="ascii").splitlines()
    guid = "381b4222-f694-41f0-9685-ff5bb260df2e"
    assert f"/setacvalueindex {guid} SUB_SLEEP STANDBYIDLE 1800" in calls
    assert f"/setacvalueindex {guid} SUB_SLEEP HIBERNATEIDLE 10800" in calls
    assert f"/setacvalueindex {guid} SUB_DISK DISKIDLE 1200" in calls
    assert f"/setactive {guid}" in calls
    assert "/hibernate on" in calls
    assert "uninstall: WatchLog stopped; tasks, power settings and data removed" in _log(site)


def test_uninstall_without_a_baseline_leaves_power_settings_alone(site):
    stub, log = _powercfg(site["root"])
    proc = helper(site, "uninstall", "-PowerCfgPath", str(stub))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not log.exists() or "/setacvalueindex" not in log.read_text(encoding="ascii")
    assert "no power baseline recorded" in _log(site)


def test_uninstall_refuses_to_clean_a_folder_that_is_not_watchlogs(site, tmp_path):
    other = tmp_path / "NotWatchLog"
    other.mkdir()
    (other / "keep.txt").write_text("x", encoding="utf-8")
    proc = run_script(HELPER, ["-Stage", "uninstall", "-InstallDir", str(site["install"]),
                               "-DataRootOverride", str(other), "-TaskName", site["task"],
                               "-StopTimeoutSec", "2"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (other / "keep.txt").exists()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
