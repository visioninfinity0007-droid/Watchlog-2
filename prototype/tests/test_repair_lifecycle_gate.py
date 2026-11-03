#!/usr/bin/env python3
r"""5.1.1 Repair/Upgrade lifecycle: version-aware guard, per-recorder proof, honest rollback.

Runs the real wl-repair-upgrade.ps1 functions (PowerShell AST harness: the orchestrator body,
which pauses and replaces a live install, never runs) against a temporary ProgramData:

  * the 5.0.x multi-recorder downgrade guard applies only to a candidate older than 5.1.0;
  * the commit gate rejects stale AND future-dated heartbeat_at / remote_update_poll_at /
    recorder_seen_at, and judges each recorder against what was live before the update: the
    old Agent's own protected runtime-health just before the pause, or the candidate's probe;
  * a recorder already offline before may stay offline (one-recorder and multi-recorder sites)
    and is reported as still offline, never as healthy;
  * rollback: files restored first, the staged registry removed, then the previous Agent
    started and proven by a fresh heartbeat; every failure is a hard, explained failure;
  * stale remote-update state from before the Repair is neutralised while WatchLog is paused.

Windows PowerShell only; the Windows CI job (setup-ui-build) runs this file.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
from ps_function_harness import POWERSHELL, ps_quote, run_functions, run_script  # noqa: E402

ROOT = TESTS.parents[1]
REPAIR = ROOT / "prototype" / "installer" / "wl-repair-upgrade.ps1"

pytestmark = pytest.mark.skipif(os.name != "nt" or not POWERSHELL,
                                reason="Repair/Upgrade runs on Windows PowerShell")

A = "aaaaaaaa-0000-4000-8000-000000000001"
B = "bbbbbbbb-0000-4000-8000-000000000002"


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


@pytest.fixture()
def work(tmp_path):
    (tmp_path / "Secrets").mkdir()
    return tmp_path


def _vars(work: Path, **extra) -> str:
    lines = [
        f"$DataRoot = {ps_quote(work)}",
        f"$LogPath = {ps_quote(work / 'repair-upgrade.log')}",
        f"$ResultPath = {ps_quote(work / 'repair-upgrade-result.ini')}",
        f"$HealthPath = {ps_quote(work / 'Secrets' / 'runtime-health.json')}",
        f"$RemoteRoot = {ps_quote(work / 'remote-update')}",
        f"$MarkerPath = {ps_quote(work / 'upgrade-in-progress.json')}",
        f"$InstallDir = {ps_quote(work / 'install')}",
        "$ExpectedVersion = '5.1.1'",
        "$FutureSkewSec = 120",
        "$HealthTimeoutSec = 2",
        "$RollbackProofTimeoutSec = 5",
        "$script:RecorderBaseline = $null",
        "$script:RecorderReport = @()",
        "$script:RegistryState = ''",
        "$script:StagedRecorderId = ''",
        "$script:SingleRecorderRequired = $true",
        "$script:PrePause = $null",
    ]
    lines += [f"{k} = {v}" for k, v in extra.items()]
    return "\n".join(lines) + "\n"


def _health(work: Path, **fields) -> None:
    body = {"schema": "watchlog.runtime_health.v1", "agent_version": "5.1.1", **fields}
    (work / "Secrets" / "runtime-health.json").write_text(json.dumps(body), encoding="utf-8")


def _out(proc) -> str:
    return proc.stdout + proc.stderr


# --- 1. the downgrade guard is version-aware -----------------------------------------------

@pytest.mark.parametrize("version,below", [("5.0.28", True), ("5.0.26", True), ("v5.0.9", True),
                                           ("5.1.0", False), ("5.1.1", False),
                                           ("5.1.1+abc1234", False), ("6.0.0", False)])
def test_guard_applies_only_to_a_candidate_older_than_5_1_0(work, version, below):
    proc = run_functions(REPAIR, f"if (Test-VersionBelow {ps_quote(version)} '5.1.0') {{ 'BELOW' }} else {{ 'NOT' }}",
                         work=work)
    assert proc.stdout.strip().splitlines()[-1] == ("BELOW" if below else "NOT"), _out(proc)


def _row(n, **extra):
    row = {"local_id": f"00000000-0000-4000-8000-00000000000{n}", "display_name": f"NVR {n}",
           "is_primary": n == 1, "continuity_owner": n == 1, "is_configured": True}
    row.update(extra)
    return row


def _run_whole_repair(tmp_path: Path, version: str, registry: str | None):
    programdata = tmp_path / "ProgramData"
    data = programdata / "WatchLog"
    data.mkdir(parents=True)
    if registry is not None:
        (data / "recorders.json").write_text(registry, encoding="utf-8")
    (tmp_path / "candidate").mkdir()
    (tmp_path / "install").mkdir()
    env = dict(os.environ, PROGRAMDATA=str(programdata))
    proc = run_script(REPAIR, ["-CandidateDir", str(tmp_path / "candidate"),
                               "-InstallDir", str(tmp_path / "install"),
                               "-ExpectedVersion", version], env=env)
    result = (data / "repair-upgrade-result.ini")
    log = (data / "repair-upgrade.log")
    return (proc.returncode, result.read_text(encoding="ascii") if result.exists() else "",
            log.read_text(encoding="utf-8", errors="replace") if log.exists() else "")


def test_a_5_1_candidate_does_not_refuse_a_multi_recorder_site(tmp_path):
    """5.1.0 shipped the 5.0.28 guard unchanged: every two-recorder site got exit 24 and the
    text 'WatchLog 5.1.0 cannot manage it ... install 5.1.0 or later'. The sandbox has no
    enrolled site, so a run the guard lets through stops at the readiness check (20)."""
    registry = json.dumps({"schema": "watchlog.recorders.v1", "recorders": [_row(1), _row(2)]})
    code, result, log = _run_whole_repair(tmp_path, "5.1.1", registry)
    assert code == 20, log
    assert "stage=existing-site readiness checks" in result
    assert "cannot manage it" not in result
    assert "the 5.0.x downgrade guard does not apply" in log


def test_a_5_1_candidate_leaves_an_unreadable_registry_to_its_own_validation(tmp_path):
    code, result, log = _run_whole_repair(tmp_path, "5.1.1", "{not json")
    assert code == 20, log            # the candidate's registry preflight refuses it later (30)


def test_a_5_0_candidate_still_refuses_a_multi_recorder_site(tmp_path):
    registry = json.dumps({"schema": "watchlog.recorders.v1", "recorders": [_row(1), _row(2)]})
    code, result, _log = _run_whole_repair(tmp_path, "5.0.28", registry)
    assert code == 24
    assert "WatchLog 5.0.28 cannot manage it" in result and "install 5.1.0 or later" in result


# --- 2. the commit gate: fresh, not future-dated, per recorder --------------------------------

def _gate(work: Path, health: dict, *, single_required=True, registry=None, prepause=None):
    started = datetime.now(timezone.utc) - timedelta(seconds=60)
    _health(work, **health)
    scenario = work / "scenario.json"
    scenario.write_text(json.dumps({"registry": registry}), encoding="utf-8")
    body = _vars(work) + f"""
$script:SingleRecorderRequired = ${'true' if single_required else 'false'}
$s = Get-Content -LiteralPath {ps_quote(scenario)} -Raw | ConvertFrom-Json
{prepause or ''}
if ($s.registry) {{ Set-RecorderReport $s.registry; $null = Get-RecorderRegressions $s.registry }}
$started = [DateTimeOffset]::Parse({ps_quote(iso(started))}).UtcDateTime
$h = Wait-NewRuntimeHealth $started
Update-RecorderReportAfter $started
Write-Result "success" 0 "complete" "done" "kept"
if ($h) {{ "GATE=PASS" }} else {{ "GATE=ROLLBACK" }}
"""
    proc = run_functions(REPAIR, body, work=work)
    assert "GATE=" in proc.stdout, _out(proc)
    result = (work / "repair-upgrade-result.ini").read_text(encoding="ascii")
    return proc.stdout.strip().splitlines()[-1], result, started


def _now_plus(seconds: int) -> str:
    return iso(datetime.now(timezone.utc) + timedelta(seconds=seconds))


FRESH = -20          # seconds relative to now: after the start (now - 60 s)
STALE = -3600


@pytest.mark.parametrize("field", ["heartbeat_at", "remote_update_poll_at", "recorder_seen_at"])
def test_a_future_dated_marker_is_not_proof(work, field):
    health = {"heartbeat_at": _now_plus(FRESH), "remote_update_poll_at": _now_plus(FRESH),
              "recorder_seen_at": _now_plus(FRESH)}
    assert _gate(work, health)[0] == "GATE=PASS"
    health[field] = _now_plus(30 * 24 * 3600)
    assert _gate(work, health)[0] == "GATE=ROLLBACK"
    health[field] = _now_plus(600)            # beyond the 120 s skew allowance
    assert _gate(work, health)[0] == "GATE=ROLLBACK"


@pytest.mark.parametrize("field", ["heartbeat_at", "remote_update_poll_at", "recorder_seen_at"])
def test_a_marker_older_than_the_phase_start_is_not_proof(work, field):
    health = {"heartbeat_at": _now_plus(FRESH), "remote_update_poll_at": _now_plus(FRESH),
              "recorder_seen_at": _now_plus(FRESH)}
    health[field] = _now_plus(STALE)
    assert _gate(work, health)[0] == "GATE=ROLLBACK"


@pytest.mark.parametrize("field", ["heartbeat_at", "remote_update_poll_at"])
def test_an_absent_heartbeat_or_update_poll_rolls_back(work, field):
    health = {"heartbeat_at": _now_plus(FRESH), "remote_update_poll_at": _now_plus(FRESH),
              "recorder_seen_at": _now_plus(FRESH)}
    del health[field]
    assert _gate(work, health)[0] == "GATE=ROLLBACK"


def test_a_small_clock_skew_is_still_proof(work):
    health = {"heartbeat_at": _now_plus(60), "remote_update_poll_at": _now_plus(60),
              "recorder_seen_at": _now_plus(60)}
    assert _gate(work, health)[0] == "GATE=PASS"


def test_a_recorder_offline_before_may_stay_offline_on_a_one_recorder_site(work):
    """5.1.0 refused to repair a one-recorder site whose recorder was offline. Offline before
    the update is no regression: commit on heartbeat + update polling, and say so."""
    health = {"heartbeat_at": _now_plus(FRESH), "remote_update_poll_at": _now_plus(FRESH),
              "recorder_seen_at": _now_plus(STALE)}
    gate, result, _ = _gate(work, health, single_required=False,
                            prepause='Set-LegacyRecorderReport "offline before the update"')
    assert gate == "GATE=PASS"
    assert "not_live_after=1" in result
    assert "before=offline before the update | after=still offline" in result
    assert "after=live" not in result
    # ... and it still needs the cloud heartbeat and update polling.
    health["heartbeat_at"] = _now_plus(STALE)
    assert _gate(work, health, single_required=False)[0] == "GATE=ROLLBACK"


def test_a_recorder_live_before_must_be_live_again_on_a_one_recorder_site(work):
    health = {"heartbeat_at": _now_plus(FRESH), "remote_update_poll_at": _now_plus(FRESH),
              "recorder_seen_at": _now_plus(STALE)}
    assert _gate(work, health, single_required=True)[0] == "GATE=ROLLBACK"


def _registry(a_live, b_live, *, ok=True):
    return {"ok": ok, "registry": "present", "recorders": [
        {"local_id": A, "display_name": "Primary", "continuity_owner": True,
         "credential": "ok", "live": a_live, "detail": "8 channel(s)" if a_live else "timeout"},
        {"local_id": B, "display_name": "Warehouse", "continuity_owner": False,
         "credential": "ok", "live": b_live, "detail": "4 channel(s)" if b_live else "timeout"},
    ]}


def _prepause(a_live: bool, b_live: bool) -> str:
    return (f"$script:PrePause = @{{ judged = $true; single_live = $false; detail = 'test'; rows = @{{"
            f" '{A}' = @{{ live = ${str(a_live).lower()}; continuity = $true }};"
            f" '{B}' = @{{ live = ${str(b_live).lower()}; continuity = $false }} }} }}")


def _multi(a=None, b=None):
    rows = []
    for local_id, seen in ((A, a), (B, b)):
        rows.append({"local_id": local_id, "live": seen is not None and seen > STALE,
                     "last_live_at": _now_plus(seen) if seen is not None else None})
    return {"heartbeat_at": _now_plus(FRESH), "remote_update_poll_at": _now_plus(FRESH),
            "recorder_seen_at": _now_plus(STALE), "multi_recorder": True, "recorders": rows}


def test_each_recorder_is_judged_on_its_own_row(work):
    # B live before (old Agent's own proof), A live: both must be live again.
    reg = _registry(True, True)
    assert _gate(work, _multi(a=FRESH, b=FRESH), registry=reg, prepause=_prepause(True, True))[0] == "GATE=PASS"
    assert _gate(work, _multi(a=FRESH, b=STALE), registry=reg, prepause=_prepause(True, True))[0] == "GATE=ROLLBACK"
    # A future-dated row is not proof either.
    assert _gate(work, _multi(a=FRESH, b=30 * 24 * 3600), registry=reg,
                 prepause=_prepause(True, True))[0] == "GATE=ROLLBACK"


def test_the_continuity_recorder_offline_before_may_stay_offline(work):
    """Only what was live before must be live after. The candidate's 'original recorder did
    not answer' is accepted when the old Agent itself had it offline before the pause."""
    gate, result, _ = _gate(work, _multi(a=None, b=FRESH), registry=_registry(False, True, ok=False),
                            prepause=_prepause(False, True))
    assert gate == "GATE=PASS"
    assert "Primary | id=" + A in result
    assert "before=offline before the update (timeout) | after=still offline" in result


def test_without_recent_proof_from_the_old_agent_the_continuity_recorder_stays_required(work):
    gate, _result, _ = _gate(work, _multi(a=None, b=FRESH), registry=_registry(True, True))
    assert gate == "GATE=ROLLBACK"


def _regressions(work: Path, registry: dict, prepause: str) -> list[str]:
    scenario = work / "reg.json"
    scenario.write_text(json.dumps(registry), encoding="utf-8")
    body = _vars(work) + f"""
{prepause}
$reg = Get-Content -LiteralPath {ps_quote(scenario)} -Raw | ConvertFrom-Json
Set-RecorderReport $reg
$r = @(Get-RecorderRegressions $reg)
"REGRESSED=[" + ($r -join ",") + "]"
"""
    proc = run_functions(REPAIR, body, work=work)
    line = [x for x in proc.stdout.splitlines() if x.startswith("REGRESSED=")]
    assert line, _out(proc)
    inner = line[-1][len("REGRESSED=["):-1]
    return [x for x in inner.split(",") if x]


def test_a_secondary_recorder_live_before_and_unreachable_now_is_a_regression(work):
    """Fault row 'secondary recorder inaccessible when previously live': rolls back before
    any file is replaced."""
    assert _regressions(work, _registry(True, False), _prepause(True, True)) == ["Warehouse"]


def test_a_secondary_recorder_offline_before_is_not_a_regression(work):
    assert _regressions(work, _registry(True, False), _prepause(True, False)) == []


def test_an_unjudged_continuity_recorder_that_does_not_answer_is_a_regression(work):
    assert _regressions(work, _registry(False, True, ok=False), "$script:PrePause = $null") == ["Primary"]


def test_one_registry_row_uses_the_single_recorder_proof_of_the_old_agent(work):
    reg = {"ok": False, "recorders": [{"local_id": A, "display_name": "Primary",
                                       "continuity_owner": True, "credential": "ok",
                                       "live": False, "detail": "timeout"}]}
    offline = "$script:PrePause = @{ judged = $true; single_live = $false; rows = @{}; detail = 't' }"
    online = "$script:PrePause = @{ judged = $true; single_live = $true; rows = @{}; detail = 't' }"
    assert _regressions(work, reg, offline) == []
    assert _regressions(work, reg, online) == ["Primary"]


# --- the "before" half: what the old Agent last proved --------------------------------------

def _prepause_state(work: Path, health: dict | None) -> dict:
    if health is not None:
        _health(work, **health)
    body = _vars(work) + """
$p = Get-PrePauseState
$rows = @{}
foreach ($k in $p.rows.Keys) { $rows[$k] = [bool]$p.rows[$k].live }
"STATE=" + (@{ judged = [bool]$p.judged; single_live = [bool]$p.single_live; rows = $rows; offline_before = [bool](& { $script:PrePause = $p; Test-LegacyRecorderOfflineBefore }) } | ConvertTo-Json -Compress -Depth 4)
"""
    proc = run_functions(REPAIR, body, work=work)
    line = [x for x in proc.stdout.splitlines() if x.startswith("STATE=")]
    assert line, _out(proc)
    return json.loads(line[-1][len("STATE="):])


def test_prepause_state_reads_the_old_agents_own_proof(work):
    state = _prepause_state(work, {"heartbeat_at": _now_plus(-30), "recorder_seen_at": _now_plus(-30)})
    assert state["judged"] and state["single_live"] and not state["offline_before"]
    state = _prepause_state(work, {"heartbeat_at": _now_plus(-30), "recorder_seen_at": _now_plus(-3600)})
    assert state["judged"] and not state["single_live"] and state["offline_before"]


@pytest.mark.parametrize("beat", [None, -3600, 30 * 24 * 3600])
def test_without_a_recent_real_heartbeat_nothing_counts_as_offline_before(work, beat):
    health = {"recorder_seen_at": _now_plus(-3600)}
    if beat is not None:
        health["heartbeat_at"] = _now_plus(beat)
    state = _prepause_state(work, health)
    assert not state["judged"] and not state["offline_before"]


def test_prepause_rows_are_per_recorder(work):
    state = _prepause_state(work, {
        "heartbeat_at": _now_plus(-30), "multi_recorder": True,
        "recorders": [{"local_id": A, "live": True, "last_live_at": _now_plus(-30), "continuity_owner": True},
                      {"local_id": B, "live": True, "last_live_at": _now_plus(-7200)}]})
    assert state["rows"] == {A: True, B: False}
    assert not state["offline_before"]


# --- 3. rollback: order, proof, honest failure --------------------------------------------

def _restore(work: Path, restore_rc: int, start_rc: int):
    body = _vars(work) + f"""
function Invoke-UpgradeHelper([string]$Stage, [string[]]$Extra = @()) {{
  ("helper:" + $Stage + " " + ($Extra -join " ")).Trim() | Add-Content {ps_quote(work / 'calls.log')}
  if ($Stage -eq "rollback-restore") {{ return {restore_rc} }}
  return {start_rc}
}}
function Undo-RegistryStaging {{ "undo-staging" | Add-Content {ps_quote(work / 'calls.log')} }}
$ok = Restore-Previous "health gate failed"
"RESTORED=$ok"
"RECOVERY=$($script:RecoveryState)"
"""
    proc = run_functions(REPAIR, body, work=work)
    calls = (work / "calls.log").read_text(encoding="utf-8").splitlines()
    return proc.stdout, calls


def test_rollback_restores_then_removes_staging_then_starts_and_proves(work):
    out, calls = _restore(work, 0, 0)
    assert [c.split()[0] for c in calls] == ["helper:rollback-restore", "undo-staging",
                                             "helper:rollback-start"]
    assert "-ProveHealth" in calls[2]
    assert "RESTORED=True" in out
    assert "proven running: its Agent sent a fresh cloud heartbeat" in out


def test_rollback_without_a_fresh_heartbeat_is_not_reported_as_success(work):
    out, _ = _restore(work, 0, 15)
    assert "RESTORED=False" in out
    assert "recovery is NOT proven" in out and "Do not uninstall WatchLog" in out


def test_a_rollback_copy_error_still_restarts_and_is_a_hard_failure(work):
    out, calls = _restore(work, 16, 0)
    assert [c.split()[0] for c in calls][-1] == "helper:rollback-start"      # task re-enabled
    assert "RESTORED=False" in out and "AUTOMATIC RECOVERY FAILED" in out


def test_a_task_restart_error_is_a_hard_failure(work):
    out, _ = _restore(work, 0, 14)
    assert "RESTORED=False" in out and "could not be started" in out


# --- stale remote-update state ---------------------------------------------------------------

def test_repair_neutralises_a_stale_remote_update(work):
    remote = work / "remote-update"
    remote.mkdir()
    install = work / "install"
    install.mkdir()
    rid = "11111111-2222-4333-8444-555555555555"
    (remote / "pending.json").write_text(json.dumps({"request_id": rid}), encoding="utf-8")
    (remote / "watchlog-agent.next.exe").write_bytes(b"old staged")
    (remote / "baseline.json").write_text("{}", encoding="utf-8")
    (install / "watchlog-agent.exe.remote.bak").write_bytes(b"old image")
    proc = run_functions(REPAIR, _vars(work) + "Clear-StaleRemoteUpdate\n'DONE'", work=work)
    assert "DONE" in proc.stdout, _out(proc)
    assert sorted(p.name for p in remote.iterdir()) == ["result.json"]
    assert not (install / "watchlog-agent.exe.remote.bak").exists()
    result = json.loads((remote / "result.json").read_text(encoding="utf-8-sig"))
    assert result["request_id"] == rid and result["ok"] is False and result["superseded"] is True


def test_repair_leaves_a_committed_remote_update_result_to_be_reported(work):
    remote = work / "remote-update"
    remote.mkdir()
    (work / "install").mkdir()
    committed = {"request_id": "r", "ok": True, "committed": True, "detail": "committed"}
    (remote / "result.json").write_text(json.dumps(committed), encoding="utf-8")
    run_functions(REPAIR, _vars(work) + "Clear-StaleRemoteUpdate", work=work)
    assert json.loads((remote / "result.json").read_text(encoding="utf-8-sig")) == committed


def test_an_open_remote_update_result_is_closed_as_superseded(work):
    remote = work / "remote-update"
    remote.mkdir()
    (work / "install").mkdir()
    (remote / "result.json").write_text(json.dumps({"request_id": "r", "ok": True}), encoding="utf-8")
    run_functions(REPAIR, _vars(work) + "Clear-StaleRemoteUpdate", work=work)
    result = json.loads((remote / "result.json").read_text(encoding="utf-8-sig"))
    assert result["ok"] is False and "superseded by Repair/Upgrade" in result["detail"]


def test_the_staged_registry_is_recorded_for_the_recovery_task(work):
    marker = work / "upgrade-in-progress.json"
    marker.write_text(json.dumps({"schema": "watchlog.upgrade_in_progress.v1", "owner_pid": 1}), encoding="utf-8")
    body = _vars(work) + f"""
$script:CurrentStage = "recorder registry staging"
Note-RegistryStaging ([pscustomobject]@{{ ok = $true; migrated = $true; local_id = '{A}' }})
'DONE'
"""
    proc = run_functions(REPAIR, body, work=work)
    assert "DONE" in proc.stdout, _out(proc)
    assert json.loads(marker.read_text(encoding="utf-8-sig"))["registry_staged_id"] == A


# --- the orchestrator wires all of it (static order) -----------------------------------------

def test_the_orchestrator_order_of_the_new_steps():
    ps = REPAIR.read_text(encoding="utf-8").replace("\r\n", "\n")
    body = ps[ps.index("\ntry {\n"):]
    guard = body.index('if (Test-VersionBelow $ExpectedVersion "5.1.0")')
    prepause = body.index("$script:PrePause = Get-PrePauseState")
    pause = body.index('$rc = Invoke-UpgradeHelper "preflight" @("-ArmRecovery","-ProveHealth","-OwnerPid",[string]$PID)')
    stale = body.index("Clear-StaleRemoteUpdate")
    replace = body.index("Install-CandidatePayload")
    assert guard < prepause < pause < stale < replace
    assert body.index("Fail 24") > guard and body.index("Fail 25") > guard


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
