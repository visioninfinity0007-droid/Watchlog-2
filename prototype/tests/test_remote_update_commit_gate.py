#!/usr/bin/env python3
r"""5.1.1 remote-update success semantics: a verified swap is not success.

The new Agent commits a remote update only when, inside the bounded commit window, it runs the
applied version (and the released build when the manifest names one), has a cloud heartbeat and
an update poll written after the swap (not future-dated), and every recorder that was live
before the update (baseline captured by the old Agent at staging) is live again; a recorder
offline before may stay offline. Commit disarms the rollback image at once, so a cloud-report
failure can never revert a proven update and reports are retried for a bounded time only. Not
proven by the deadline: rollback is requested, run-agent.ps1 restores the VERIFIED image, and
the restored Agent reports the rollback only after proving itself.

The Python half runs everywhere; the run-agent.ps1 half needs Windows PowerShell and runs in
the Windows CI job.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parents[1]
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(ROOT / "prototype" / "agent"))

import remote_update  # noqa: E402
from ps_function_harness import POWERSHELL, ps_quote, run_functions  # noqa: E402

RUN_AGENT = ROOT / "prototype" / "installer" / "run-agent.ps1"
APPLY = ROOT / "prototype" / "installer" / "apply-remote-update.ps1"
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
A, B = "aaaaaaaa-0000-4000-8000-000000000001", "bbbbbbbb-0000-4000-8000-000000000002"


def at(seconds: int) -> str:
    return (NOW + timedelta(seconds=seconds)).isoformat()


class Cloud:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def call(self, name, **kw):
        if self.fail:
            raise RuntimeError("cloud unreachable")
        self.calls.append((name, kw))
        return {"ok": True}


STATE = {"agent_id": "a", "agent_key": "k"}


@pytest.fixture()
def site(tmp_path):
    cfg = SimpleNamespace(state_path=tmp_path / "agent_state.json")
    backup = tmp_path / "watchlog-agent.exe.remote.bak"
    backup.write_bytes(b"previous agent")
    return SimpleNamespace(cfg=cfg, backup=backup, root=tmp_path)


def applied(**extra):
    row = {"schema": "watchlog.remote_update_result.v1", "request_id": "req-1", "ok": True,
           "detail": "signed remote update applied", "applied_version": "5.1.2",
           "completed_at": at(-300), "applied_at": at(-300), "health_not_before": at(-240),
           "commit_deadline": at(600), "previous_version": "5.1.1", "committed": False}
    row.update(extra)
    return row


def healthy(**extra):
    h = {"agent_version": "5.1.2", "build_sha": "abc1234def", "heartbeat_at": at(-30),
         "remote_update_poll_at": at(-20), "recorder_seen_at": at(-30)}
    h.update(extra)
    return h


def write_result(site, row):
    path = remote_update._result_path(site.cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row), encoding="utf-8")
    return path


def report(site, cloud, *, health, baseline=None, now=NOW):
    return remote_update.report_previous_result(cloud, STATE, site.cfg, _now=now, _health=health,
                                                _baseline=baseline, _backup=site.backup)


# --- the verdict ---------------------------------------------------------------------------

SINGLE_LIVE = {"judged": True, "multi_recorder": False, "recorder_live": True, "recorders": []}


def test_commit_needs_version_heartbeat_polling_and_the_recorder():
    ok, failures, _ = remote_update.commit_verdict(applied(), SINGLE_LIVE, healthy(), NOW)
    assert ok, failures


@pytest.mark.parametrize("change,why", [
    ({"agent_version": "5.1.1"}, "running version"),                         # Agent version mismatch
    ({"heartbeat_at": at(-3600)}, "no cloud heartbeat"),                       # heartbeat absent/stale
    ({"heartbeat_at": None}, "no cloud heartbeat"),
    ({"heartbeat_at": at(30 * 24 * 3600)}, "no cloud heartbeat"),             # future-dated
    ({"remote_update_poll_at": None}, "remote-update polling"),               # updater polling absent
    ({"remote_update_poll_at": at(3600)}, "remote-update polling"),
    ({"recorder_seen_at": at(-3600)}, "recorder was live before"),             # recorder liveness lost
    ({"recorder_seen_at": at(30 * 24 * 3600)}, "recorder was live before"),
])
def test_each_missing_proof_blocks_the_commit(change, why):
    ok, failures, _ = remote_update.commit_verdict(applied(), SINGLE_LIVE, healthy(**change), NOW)
    assert not ok and any(why in f for f in failures), failures


def test_the_released_build_must_be_the_running_build():
    baseline = dict(SINGLE_LIVE, target_build_sha="abc1234")
    assert remote_update.commit_verdict(applied(), baseline, healthy(), NOW)[0]
    ok, failures, _ = remote_update.commit_verdict(applied(), baseline, healthy(build_sha="fff9999"), NOW)
    assert not ok and "released build" in failures[0]
    # No build named by the release, or none reported: the version decides.
    assert remote_update.commit_verdict(applied(), baseline, healthy(build_sha=""), NOW)[0]


def test_a_recorder_offline_before_may_stay_offline():
    baseline = {"judged": True, "multi_recorder": False, "recorder_live": False, "recorders": []}
    ok, failures, offline = remote_update.commit_verdict(
        applied(), baseline, healthy(recorder_seen_at=at(-3600)), NOW)
    assert ok, failures
    assert offline == ["recorder"]


def _multi(a_live, b_live, a_seen=-30, b_seen=-30):
    return healthy(multi_recorder=True, recorder_seen_at=at(-3600), recorders=[
        {"local_id": A, "live": a_live, "last_live_at": at(a_seen)},
        {"local_id": B, "live": b_live, "last_live_at": at(b_seen)}])


def test_every_recorder_live_before_must_be_live_again_each_on_its_own_row():
    baseline = {"judged": True, "multi_recorder": True, "recorders": [
        {"local_id": A, "live": True}, {"local_id": B, "live": True}]}
    assert remote_update.commit_verdict(applied(), baseline, _multi(True, True), NOW)[0]
    ok, failures, _ = remote_update.commit_verdict(applied(), baseline, _multi(True, False), NOW)
    assert not ok and B in failures[0]
    ok, _, _ = remote_update.commit_verdict(applied(), baseline, _multi(True, True, b_seen=-3600), NOW)
    assert not ok                                    # a row older than the swap is not proof
    ok, _, _ = remote_update.commit_verdict(applied(), baseline,
                                            _multi(True, True, b_seen=30 * 24 * 3600), NOW)
    assert not ok                                    # nor a future-dated one


def test_a_secondary_offline_before_is_reported_not_required():
    baseline = {"judged": True, "multi_recorder": True, "recorders": [
        {"local_id": A, "live": True}, {"local_id": B, "live": False}]}
    ok, failures, offline = remote_update.commit_verdict(applied(), baseline, _multi(True, False), NOW)
    assert ok, failures
    assert offline == [B]


def test_the_baseline_comes_from_the_old_agents_own_recent_proof():
    health = {"agent_version": "5.1.1", "build_sha": "aaa", "heartbeat_at": at(-30),
              "recorder_seen_at": at(-30), "multi_recorder": True,
              "recorders": [{"local_id": A, "live": True, "last_live_at": at(-40)},
                            {"local_id": B, "live": True, "last_live_at": at(-7200)}]}
    base = remote_update.capture_baseline(health, NOW)
    assert base["judged"] and base["multi_recorder"] and base["recorder_live"]
    assert base["recorders"] == [{"local_id": A, "live": True}, {"local_id": B, "live": False}]
    # No recent heartbeat (or a future-dated one): nothing can be required.
    for beat in (at(-3600), at(30 * 24 * 3600), None):
        stale = remote_update.capture_baseline(dict(health, heartbeat_at=beat), NOW)
        assert not stale["judged"] and not any(r["live"] for r in stale["recorders"])


# --- report_previous_result: commit, rollback request, proof, bounds ----------------------

def test_a_proven_update_is_committed_disarmed_and_reported(site):
    path = write_result(site, applied())
    cloud = Cloud()
    assert report(site, cloud, health=healthy(), baseline=SINGLE_LIVE) is True
    assert not site.backup.exists() and not path.exists()
    name, kw = cloud.calls[-1]
    assert name == "wl_agent_complete_update_request" and kw["p_ok"] is True
    assert "committed" in kw["p_detail"]


def test_a_failed_cloud_report_never_re_arms_the_rollback(site):
    """5.1.0: an ok result and .remote.bak stayed until the cloud accepted the report, so any
    sub-60 s exit days later silently reverted a healthy update."""
    path = write_result(site, applied())
    assert report(site, Cloud(fail=True), health=healthy(), baseline=SINGLE_LIVE) is False
    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["committed"] is True and not site.backup.exists()
    # Retried later; dropped after the bounded reporting time.
    assert report(site, Cloud(fail=True), health=healthy(), baseline=SINGLE_LIVE,
                  now=NOW + timedelta(days=8)) is False
    assert not path.exists()


def test_unproven_inside_the_window_waits(site):
    path = write_result(site, applied())
    cloud = Cloud()
    assert report(site, cloud, health=healthy(heartbeat_at=at(-3600)), baseline=SINGLE_LIVE) is False
    assert cloud.calls == [] and site.backup.exists() and path.exists()


def test_unproven_at_the_deadline_requests_a_rollback(site):
    path = write_result(site, applied(commit_deadline=at(-1)))
    cloud = Cloud()
    out = report(site, cloud, health=healthy(remote_update_poll_at=None), baseline=SINGLE_LIVE)
    assert out == "restart"
    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["ok"] is False and row["rollback_requested"] is True
    assert "remote-update polling" in row["detail"]
    assert cloud.calls == [] and site.backup.exists()          # the launcher needs the image


def test_unproven_without_a_rollback_image_is_reported_as_a_failure(site):
    site.backup.unlink()
    write_result(site, applied(commit_deadline=at(-1)))
    cloud = Cloud()
    assert report(site, cloud, health=None, baseline=SINGLE_LIVE) is True
    assert cloud.calls[-1][1]["p_ok"] is False
    assert "no rollback image" in cloud.calls[-1][1]["p_detail"]


def test_a_far_future_health_window_does_not_block_forever(site):
    write_result(site, applied(health_not_before=at(10 * 365 * 24 * 3600), commit_deadline=at(-1)))
    assert report(site, Cloud(), health=healthy(heartbeat_at=None), baseline=SINGLE_LIVE) == "restart"


def test_a_near_health_window_is_still_respected(site):
    write_result(site, applied(health_not_before=at(30)))
    cloud = Cloud()
    assert report(site, cloud, health=healthy(), baseline=SINGLE_LIVE) is False
    assert cloud.calls == []


def rolled_back(**extra):
    row = applied(ok=False, rollback_applied=True, rollback_at=at(-120), applied_version="",
                  detail="remote update rolled back: x; previous version restored")
    row.update(extra)
    return row


def test_a_rollback_is_reported_only_after_the_previous_agent_proves_itself(site):
    write_result(site, rolled_back())
    cloud = Cloud()
    previous = healthy(agent_version="5.1.1", heartbeat_at=at(-30), remote_update_poll_at=at(-10))
    assert report(site, cloud, health=previous) is True
    assert cloud.calls[-1][1]["p_ok"] is False
    assert "proven running" in cloud.calls[-1][1]["p_detail"]


def test_an_unproven_rollback_waits_then_is_reported_as_not_proven(site):
    path = write_result(site, rolled_back())
    cloud = Cloud()
    stale = healthy(agent_version="5.1.1", heartbeat_at=at(-3600), remote_update_poll_at=at(-3600))
    assert report(site, cloud, health=stale) is False and path.exists()
    assert report(site, cloud, health=stale, now=NOW + timedelta(minutes=15)) is True
    assert "NOT proven running" in cloud.calls[-1][1]["p_detail"]


def test_staging_records_the_baseline_and_the_commit_window(site, monkeypatch):
    health = remote_update._health_path(site.cfg)
    health.parent.mkdir(parents=True)
    health.write_text(json.dumps({"agent_version": "5.1.1",
                                  "heartbeat_at": datetime.now(timezone.utc).isoformat(),
                                  "recorder_seen_at": datetime.now(timezone.utc).isoformat()}),
                      encoding="utf-8")
    pkg = remote_update._package_path(site.cfg)
    pkg.parent.mkdir(parents=True, exist_ok=True)
    pkg.write_bytes(b"pkg")
    monkeypatch.setattr(remote_update, "_fetch_manifest", lambda cfg: {
        "action": "update", "target": "5.1.2", "url": "https://x/a.exe", "sha256": "a" * 64,
        "size": 3, "build_sha": "abc1234"})
    monkeypatch.setattr(remote_update, "_download_verified", lambda cfg, plan: pkg)
    out = remote_update.stage_latest(Cloud(), STATE, site.cfg, {"request_id": "req-9"})
    assert out["restart"] is True
    baseline = json.loads(remote_update._baseline_path(site.cfg).read_text(encoding="utf-8"))
    assert baseline["request_id"] == "req-9" and baseline["judged"] and baseline["recorder_live"]
    assert baseline["target_build_sha"] == "abc1234"
    pending = json.loads(remote_update._pending_path(site.cfg).read_text(encoding="utf-8"))
    assert pending["commit_window_sec"] == remote_update.COMMIT_WINDOW_SECONDS


def test_the_worker_exits_for_rollback_when_the_gate_fails(site, monkeypatch):
    calls = []
    monkeypatch.setattr(remote_update, "report_previous_result", lambda *a, **k: "restart")
    monkeypatch.setattr(remote_update._thread, "interrupt_main", lambda: calls.append("interrupt"))
    stop = SimpleNamespace(is_set=lambda: False, wait=lambda s: None)
    remote_update.update_worker(site.cfg, STATE, Cloud(), stop)
    assert calls == ["interrupt"]


def test_runtime_health_names_the_build():
    src = (ROOT / "prototype" / "agent" / "watchlog_agent.py").read_text(encoding="utf-8")
    fn = src[src.index("def update_runtime_health("):]
    assert '"build_sha": _runtime_build_sha()' in fn[:fn.index("\ndef ")]


def test_apply_records_the_commit_window_and_the_rollback_image_identity():
    src = APPLY.read_text(encoding="utf-8")
    for field in ("applied_at", "commit_deadline", "previous_version", "previous_sha256", "committed = $false"):
        assert field in src, field
    # The stage-trust gate (T0-SEC1) still runs before anything is read.
    assert src.index("$untrusted = Get-UntrustedStageReason") < src.index("Get-Content -LiteralPath $pendingPath")


# --- run-agent.ps1: when and how the launcher rolls back -----------------------------------

windows_ps = pytest.mark.skipif(os.name != "nt" or not POWERSHELL,
                                reason="the launcher runs on Windows PowerShell")


def _reason(tmp_path, row, runtime=600, backup=True, now=NOW) -> str:
    data = tmp_path / "r.json"
    data.write_text(json.dumps(row), encoding="utf-8")
    body = f"""
$rr = Get-Content -LiteralPath {ps_quote(data)} -Raw | ConvertFrom-Json
$why = Get-RemoteRollbackReason $rr {runtime} ([DateTimeOffset]::Parse({ps_quote(now.isoformat())}).UtcDateTime) ${'true' if backup else 'false'}
"WHY=[" + $why + "]"
"""
    proc = run_functions(RUN_AGENT, body, work=tmp_path)
    line = [x for x in proc.stdout.splitlines() if x.startswith("WHY=")]
    assert line, proc.stdout + proc.stderr
    return line[-1][5:-1]


@windows_ps
def test_launcher_rollback_triggers(tmp_path):
    assert "exited after 20 s" in _reason(tmp_path, applied(), runtime=20)          # Agent exits immediately
    assert "did not prove its health" in _reason(tmp_path, applied(ok=False, rollback_requested=True))
    assert "commit window ended" in _reason(tmp_path, applied(commit_deadline=at(-1)))
    assert _reason(tmp_path, applied()) == ""                                         # inside the window
    # Disarmed: committed, already rolled back, superseded by Repair, or no image.
    assert _reason(tmp_path, applied(committed=True), runtime=5) == ""
    assert _reason(tmp_path, rolled_back(), runtime=5) == ""
    assert _reason(tmp_path, applied(superseded=True), runtime=5) == ""
    assert _reason(tmp_path, applied(), runtime=5, backup=False) == ""


def _launcher_rollback(tmp_path, row, backup_bytes=b"previous agent"):
    agent = tmp_path / "watchlog-agent.exe"
    backup = tmp_path / "watchlog-agent.exe.remote.bak"
    result = tmp_path / "result.json"
    agent.write_bytes(b"new agent")
    backup.write_bytes(backup_bytes)
    result.write_text(json.dumps(row), encoding="utf-8")
    body = f"""
$agent = {ps_quote(agent)}
$remoteBackup = {ps_quote(backup)}
$remoteResult = {ps_quote(result)}
$log = {ps_quote(tmp_path / 'agent.log')}
$rr = Read-RemoteResult
Invoke-RemoteRollback $rr "test reason"
"DONE"
"""
    proc = run_functions(RUN_AGENT, body, work=tmp_path)
    assert "DONE" in proc.stdout, proc.stdout + proc.stderr
    return agent, backup, json.loads(result.read_text(encoding="utf-8-sig"))


@windows_ps
def test_launcher_restores_only_a_verified_rollback_image(tmp_path):
    good = hashlib.sha256(b"previous agent").hexdigest().upper()
    agent, backup, row = _launcher_rollback(tmp_path, applied(previous_sha256=good))
    assert agent.read_bytes() == b"previous agent" and not backup.exists()
    assert row["rollback_applied"] is True and row["ok"] is False and row["rollback_at"]


@windows_ps
def test_launcher_refuses_a_tampered_rollback_image(tmp_path):
    good = hashlib.sha256(b"previous agent").hexdigest().upper()
    agent, backup, row = _launcher_rollback(tmp_path, applied(previous_sha256=good),
                                            backup_bytes=b"something else")
    assert agent.read_bytes() == b"new agent" and backup.exists()
    assert row["rollback_failed"] is True and "does not match" in row["detail"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
