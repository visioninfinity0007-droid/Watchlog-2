#!/usr/bin/env python3
"""Remote maintenance through Site Control (5.1.2, workstream D).

* every maintenance action is claimed and completed on the Agent runtime path, never on a
  bare driver, and a claimed command always completes (succeeded or failed);
* collect_diagnostics returns the allowlisted support bundle and a <= 64 KiB agent.log tail
  with no secret, URL or recorder address;
* reconnect_recorder makes the live collector drop and reopen its session, and reports
  success only when a NEW session is observed;
* restart_agent completes the command first, then the run loop exits with
  RESTART_EXIT_CODE (run-agent.ps1 restarts on any exit code); it refuses while a remote
  update is staged or inside its commit window;
* refresh_inventory / refresh_capabilities use the existing sync RPCs for the right recorder;
* the acceptance suite targets every configured recorder with its own Config;
* --acceptance-json writes the same suite's JSON locally.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

TESTS = Path(__file__).resolve().parent
AGENT = TESTS.parents[0] / "agent"
sys.path.insert(0, str(AGENT))
sys.path.insert(0, str(TESTS))

import credential_store as cs  # noqa: E402
import full_acceptance as fa  # noqa: E402
import recorder_registry as rr  # noqa: E402
import site_control  # noqa: E402
import site_maintenance as sm  # noqa: E402
import watchlog_agent as core  # noqa: E402
import windows_secret as ws  # noqa: E402
from drivers.base import Channel, DeviceInfo  # noqa: E402

REC_A = "11111111-1111-1111-1111-111111111111"
REC_B = "22222222-2222-2222-2222-222222222222"
LOCAL_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
LOCAL_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
STATE = {"agent_id": "agent-1", "agent_key": "agent-secret-key-value", "site_id": "site-1"}


@pytest.fixture(autouse=True)
def clean_registry():
    sm.unregister_all()
    sm._reset_restart_for_tests()
    yield
    sm.unregister_all()
    sm._reset_restart_for_tests()


def test_catalog_matches_site_control():
    assert tuple(site_control.MAINTENANCE_ACTIONS) == tuple(sm.MAINTENANCE_ACTIONS)
    assert set(sm.MAINTENANCE_ACTIONS) <= set(site_control.READ_ACTIONS)
    deny = re.compile(r"(firmware|format|factory|reset|reboot|deleterec|delete_rec|adduser"
                      r"|user_|network_|password|wipe|erase)", re.I)
    assert not [a for a in sm.MAINTENANCE_ACTIONS if deny.search(a)]


# ---------------------------------------------------------------------------
# claimed-command routing
# ---------------------------------------------------------------------------

class _Cloud:
    def __init__(self, stop, commands):
        self.stop = stop
        self.commands = list(commands)
        self.completed = []
        self.events = []

    def call(self, name, **kwargs):
        if name == "wl_agent_claim_command":
            if not self.commands:
                self.stop.set()
                return {"enabled": True, "authority": True, "command": None}
            return {"enabled": True, "authority": True, "command": self.commands.pop(0)}
        if name == "wl_agent_complete_command":
            self.events.append("complete")
            self.completed.append(kwargs)
            return {"ok": True}
        raise AssertionError(name)


def _work(cfg, commands):
    stop = threading.Event()
    cloud = _Cloud(stop, commands)
    worker = threading.Thread(target=core.command_worker, args=(cfg, dict(STATE), cloud, stop),
                              daemon=True)
    worker.start()
    worker.join(timeout=20)
    assert not worker.is_alive()
    return cloud


def _cfg(tmp_path):
    root = tmp_path / "WatchLog"
    return SimpleNamespace(
        recorder_cloud_id=None, recorder_local_id=None, recorder_backend_absent=False,
        nvr_driver="dahua-cgi", nvr_url="http://192.0.2.99", nvr_username="u",
        nvr_password="p", site_control_enabled=True, site_control_seconds=0.01,
        state_path=root / "agent_state.json", spool_path=root / "spool.sqlite",
        health_store_path=root / "health.sqlite", last_live_path=root / "last_live.json",
    )


def test_maintenance_actions_route_to_the_runtime_and_always_complete(monkeypatch, tmp_path):
    seen = []

    def execute(cfg, state, cloud, cmd, log=print):
        seen.append(cmd["action"])
        if cmd["action"] == "refresh_capabilities":
            raise RuntimeError("boom at http://192.168.1.108/")
        return {"ok": cmd["action"] != "reconnect_recorder", "data": {"action": cmd["action"]},
                "error": None if cmd["action"] != "reconnect_recorder" else "not observed"}

    monkeypatch.setattr(sm, "execute", execute)
    monkeypatch.setattr(core, "_site_control_driver",
                        lambda _cfg: (_ for _ in ()).throw(AssertionError("bare driver opened")))
    cmds = [{"id": f"c-{a}", "recorder_id": REC_A, "action": a, "params": {}, "tier": "read"}
            for a in sm.MAINTENANCE_ACTIONS]
    cloud = _work(_cfg(tmp_path), cmds)
    assert seen == list(sm.MAINTENANCE_ACTIONS)
    done = {c["p_command_id"]: c for c in cloud.completed}
    assert set(done) == {f"c-{a}" for a in sm.MAINTENANCE_ACTIONS}
    assert done["c-run_full_acceptance_test"]["p_status"] == "succeeded"
    assert done["c-reconnect_recorder"]["p_status"] == "failed"
    failed = done["c-refresh_capabilities"]
    assert failed["p_status"] == "failed" and failed["p_error"] == "RuntimeError"


def test_restart_runs_only_after_the_completion_reached_the_cloud(monkeypatch, tmp_path):
    order = []
    cloud_holder = {}

    def execute(cfg, state, cloud, cmd, log=print):
        cloud_holder["cloud"] = cloud
        return {"ok": True, "data": {"restart_scheduled": True},
                "after_complete": lambda: order.append(("after", list(cloud.events)))}

    monkeypatch.setattr(sm, "execute", execute)
    _work(_cfg(tmp_path), [{"id": "c1", "recorder_id": REC_A, "action": "restart_agent",
                            "params": {}, "tier": "read"}])
    assert order == [("after", ["complete"])]


def test_full_acceptance_command_params(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(sm, "run_acceptance", lambda *a, **kw: calls.append(kw) or {"ok": 1})
    out = sm.execute(_cfg(tmp_path), STATE, None, {
        "id": "c1", "recorder_id": REC_A, "action": "run_full_acceptance_test",
        "params": {"all_recorders": True, "include_clip": False}})
    assert out == {"ok": True, "data": {"ok": 1}}
    assert calls[0]["all_recorders"] is True and calls[0]["include_clip"] is False
    assert calls[0]["only"] is None and calls[0]["command_id"] == "c1"

    sm.execute(_cfg(tmp_path), STATE, None, {"id": "c2", "recorder_id": REC_A,
                                             "action": "run_recording_check", "params": {}})
    assert calls[1]["only"] == fa.RECORDING_CHECK_ONLY and calls[1]["include_clip"] is False
    sm.execute(_cfg(tmp_path), STATE, None, {"id": "c3", "recorder_id": REC_A,
                                             "action": "run_archive_check",
                                             "params": {"channel": "3"}})
    assert calls[2]["only"] == fa.ARCHIVE_CHECK_ONLY and calls[2]["channels"] == ["3"]
    assert calls[2]["budget_seconds"] <= fa.MAX_BUDGET_SECONDS


# ---------------------------------------------------------------------------
# collect_diagnostics
# ---------------------------------------------------------------------------

def test_diagnostics_are_redacted_and_bounded(tmp_path):
    ini = tmp_path / "watchlog.ini"
    ini.write_text("[watchlog]\nnvr_url = http://192.168.1.108\nnvr_password = hunter2\n"
                   "nvr_username = admin\nenrollment_code = ENROLL-123\n"
                   "supabase_url = https://x.supabase.co\nspool_max_rows = 5000\n",
                   encoding="utf-8")
    log = tmp_path / "agent.log"
    lines = []
    for i in range(6000):
        lines.append(f"2026-10-07T10:00:{i % 60:02d}Z [agent] heartbeat ok {i}")
        if i % 500 == 0:
            lines.append("password=hunter2 should never leave")
            lines.append("driver: GET http://admin:hunter2@192.168.1.108/cgi-bin/x failed")
            lines.append("recorder at 192.168.1.108 timed out")
            lines.append("agent key secret-agent-key loaded")
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cfg = SimpleNamespace(_ini_path=ini, state_path=tmp_path / "agent_state.json",
                          spool_path=tmp_path / "missing.sqlite", spool_max_rows=0)

    out = sm.collect_diagnostics(cfg, dict(STATE), log_path=log, setup_log="setup ok\n")
    text = json.dumps(out)
    for secret in ("hunter2", "ENROLL-123", "agent-secret-key-value", "192.168.1.108",
                   "secret-agent-key", "admin:"):
        assert secret not in text, secret
    tail = out["agent_log"]
    assert tail["present"] and tail["truncated"] is True
    assert len(tail["tail"].encode("utf-8")) <= sm.DIAGNOSTICS_LOG_MAX_BYTES
    assert "heartbeat ok 5999" in tail["tail"]                 # newest lines kept
    assert "[ip]" in tail["tail"]
    names = {m["name"] for m in out["manifest"]}
    assert {"versions.json", "config_redacted.ini", "identity.json",
            "local_state.json", "setup_log.txt"} <= names
    assert all(len(m["sha256"]) == 64 for m in out["manifest"])
    assert "nvr_password" not in out["files"]["config_redacted.ini"]
    assert "agent_key" not in out["files"]["identity.json"]


def test_diagnostics_without_a_log(tmp_path):
    cfg = SimpleNamespace(_ini_path=tmp_path / "none.ini", state_path=tmp_path / "s.json",
                          spool_path=tmp_path / "missing.sqlite", spool_max_rows=0)
    out = sm.collect_diagnostics(cfg, {}, log_path=tmp_path / "agent.log", setup_log="")
    assert out["agent_log"]["present"] is False and out["agent_log"]["tail"] == ""


# ---------------------------------------------------------------------------
# reconnect_recorder against the packaged collector
# ---------------------------------------------------------------------------

class QuietStream:
    """A live session that stays open until its stop (the SessionStop) is set."""

    name = "fake-stream"
    verified_against_hardware = True

    def __init__(self, reports_stream):
        self.reports_stream_activity = reports_stream
        self.event_stream = {}
        self.last_activity_monotonic = 0.0
        self.closed = False

    def stream_events(self, stop):
        self.last_activity_monotonic = time.monotonic()
        if self.reports_stream_activity:
            self.event_stream.update(connected=True, connected_at=str(time.monotonic()))
        while not stop.is_set():
            stop.wait(0.05)
        return
        yield  # noqa: unreachable — a generator

    def get_snapshot(self, channel):
        return None

    def close(self):
        self.closed = True


@pytest.mark.parametrize("reports_stream", [True, False])
def test_reconnect_drops_and_reopens_the_live_session(monkeypatch, reports_stream):
    import native_event_collector as nec
    from native_collector_harness import Cfg, Info, Spool

    drivers = []

    def open_driver(_cfg):
        drv = QuietStream(reports_stream)
        drivers.append(drv)
        return drv, Info()

    monkeypatch.setattr(core, "open_driver", open_driver)
    monkeypatch.setattr(core.vision, "build", lambda _cfg, _log: None)
    monkeypatch.setattr(core, "_credential_generation_for_cfg", lambda _cfg: "gen-1")
    holder, stop = {}, threading.Event()
    worker = threading.Thread(target=nec.collector, args=(Cfg(), Spool(), stop, holder),
                              daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while not holder.get("live_driver") and time.monotonic() < deadline:
            time.sleep(0.02)
        assert holder.get("live_driver") is drivers[0]
        out = sm.reconnect_recorder(None, confirm_seconds=5)
        assert out["ok"] is True, out
        assert out["data"]["session_dropped"] and out["data"]["session_reopened"]
        assert len(drivers) == 2 and drivers[0].closed and holder["live_driver"] is drivers[1]
        assert not stop.is_set(), "a reconnect must never stop the Agent"
    finally:
        stop.set()
        worker.join(timeout=10)
    assert not worker.is_alive()


def test_reconnect_without_a_live_collector_fails_honestly():
    out = sm.reconnect_recorder(REC_A, confirm_seconds=0)
    assert out["ok"] is False and out["data"]["requested"] is False


def test_reconnect_not_observed_is_not_success():
    holder = {"live_driver": object()}
    sm.register_runtime(SimpleNamespace(recorder_cloud_id=REC_A), holder)
    out = sm.reconnect_recorder(REC_A, confirm_seconds=0.6)
    assert out["ok"] is False and out["data"]["session_reopened"] is False


def test_session_stop_only_ends_one_session():
    stop = threading.Event()
    holder = {}
    sm.register_runtime(SimpleNamespace(recorder_cloud_id=REC_A), holder)
    session = sm.SessionStop(stop, holder)
    assert not session.is_set()
    holder["reconnect_event"].set()
    assert session.is_set() and session.wait(0.01) and not stop.is_set()
    assert sm.take_reconnect(holder) is True and holder["reconnect_count"] == 1
    assert not sm.SessionStop(stop, holder).is_set()
    assert sm.runtime_holder(REC_A) is holder and sm.runtime_holder(REC_B) is None


# ---------------------------------------------------------------------------
# restart_agent
# ---------------------------------------------------------------------------

def test_restart_request_ends_the_run_loop_with_the_restart_code():
    exits = []
    assert not sm.restart_requested()
    sm.check_restart()                                   # no request: nothing happens
    sm.request_restart(log=lambda _m: None, force_after=0.2, _exit=exits.append)
    with pytest.raises(SystemExit) as raised:
        sm.check_restart()
    assert raised.value.code == sm.RESTART_EXIT_CODE == 75
    time.sleep(0.5)
    assert exits == [75]                                 # a wedged loop is ended hard


def _update_cfg(tmp_path):
    return SimpleNamespace(state_path=tmp_path / "agent_state.json")


def test_restart_is_refused_during_a_remote_update(monkeypatch, tmp_path):
    import remote_update
    cfg = _update_cfg(tmp_path)
    monkeypatch.setattr(remote_update, "_backup_path", lambda: tmp_path / "agent.remote.bak")
    out = sm.execute(cfg, STATE, None, {"id": "c", "action": "restart_agent", "params": {}})
    assert out["ok"] is True and out["data"]["exit_code"] == 75
    assert callable(out["after_complete"])

    root = tmp_path / "remote-update"
    root.mkdir()
    (root / "pending.json").write_text("{}", encoding="utf-8")
    out = sm.execute(cfg, STATE, None, {"id": "c", "action": "restart_agent", "params": {}})
    assert out["ok"] is False and "staged" in out["error"] and "after_complete" not in out

    (root / "pending.json").unlink()
    (root / "result.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    (tmp_path / "agent.remote.bak").write_text("x", encoding="utf-8")
    out = sm.execute(cfg, STATE, None, {"id": "c", "action": "restart_agent", "params": {}})
    assert out["ok"] is False and "commit window" in out["error"]

    (root / "result.json").write_text(json.dumps({"ok": True, "committed": True}),
                                      encoding="utf-8")
    assert sm.execute(cfg, STATE, None, {"id": "c", "action": "restart_agent",
                                         "params": {}})["ok"] is True


def test_launcher_restarts_the_agent_after_any_exit_code():
    """run-agent.ps1: the loop has no exit path, so exit code 75 (and any other) restarts."""
    text = (TESTS.parents[0] / "installer" / "run-agent.ps1").read_text(encoding="utf-8")
    loop = text[text.index("while ($true) {"):]
    assert "& $agent 2>&1" in loop and "$code = $LASTEXITCODE" in loop
    assert "restarting in 15s" in loop and "Start-Sleep -Seconds 15" in loop
    # No statement inside the loop leaves it: no break/exit/return/throw, as a statement or
    # inside a one-line block, and no branch on the exit code.
    for line in loop.splitlines():
        code = line.split("#", 1)[0]
        code = re.sub(r'"[^"]*"', '""', code)               # string contents are not code
        assert not re.search(r"(^|[{;]\s*)(break|exit|return|throw)\b", code.strip(), re.I), line
    assert not re.search(r"\$code\s*-(eq|ne|lt|gt)", loop)


def test_every_run_loop_honours_the_restart_request():
    for name in ("analytics_agent.py", "multi_recorder_fanout.py", "watchlog_agent.py"):
        assert "site_maintenance.check_restart()" in (AGENT / name).read_text(encoding="utf-8"), name


# ---------------------------------------------------------------------------
# refresh_inventory / refresh_capabilities
# ---------------------------------------------------------------------------

class SyncCloud:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def call(self, name, **kwargs):
        self.calls.append((name, kwargs))
        if name in ("wl_sync_recorder_cameras", "wl_sync_cameras"):
            return self.mapping
        return {"ok": True}


class InvDriver:
    name = "dahua-cgi"

    def __init__(self, channels=("1", "2")):
        self.channels = channels
        self.closed = False

    def list_channels(self):
        return [Channel(c, f"Cam {c}") for c in self.channels]

    def capabilities(self):
        return {"channels": [{"channel": "1", "analytics": []}]}

    def close(self):
        self.closed = True


def test_refresh_inventory_registry_recorder_updates_the_runtime():
    cloud = SyncCloud({"1": "cam-1", "2": "cam-2"})
    holder = {}
    drv = InvDriver()
    out = sm.refresh_inventory(SimpleNamespace(recorder_cloud_id=REC_A), dict(STATE), cloud,
                               holder=holder, open_driver=lambda _c: (drv, None))
    assert out["ok"] is True and out["data"]["mapped"] == 2
    name, kwargs = cloud.calls[0]
    assert name == "wl_sync_recorder_cameras" and kwargs["p_recorder_id"] == REC_A
    assert holder["camera_mapping"] == {"1": "cam-1", "2": "cam-2"} and drv.closed


def test_refresh_inventory_legacy_and_incomplete_mapping():
    cloud = SyncCloud({"1": "cam-1"})
    out = sm.refresh_inventory(SimpleNamespace(recorder_cloud_id=None), dict(STATE), cloud,
                               holder={}, open_driver=lambda _c: (InvDriver(), None))
    assert cloud.calls[0][0] == "wl_sync_cameras"
    assert out["ok"] is False and out["data"]["unmapped_channels"] == ["2"]


def test_refresh_capabilities(monkeypatch):
    cloud = SyncCloud({})
    out = sm.refresh_capabilities(SimpleNamespace(recorder_cloud_id=REC_A), dict(STATE), cloud,
                                  open_driver=lambda _c: (InvDriver(), None))
    assert out["ok"] is True and cloud.calls[0][0] == "wl_sync_recorder_capabilities"

    class Empty(InvDriver):
        def capabilities(self):
            return {"channels": []}
    out = sm.refresh_capabilities(SimpleNamespace(recorder_cloud_id=REC_A), dict(STATE),
                                  SyncCloud({}), open_driver=lambda _c: (Empty(), None))
    assert out["ok"] is False


# ---------------------------------------------------------------------------
# targets: every configured recorder with its own bound Config
# ---------------------------------------------------------------------------

def _registry(monkeypatch, tmp_path):
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))

    def wjs(path, obj):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("JSON:" + json.dumps(obj), encoding="utf-8")

    def rjs(path):
        text = Path(path).read_text(encoding="utf-8")
        if not text.startswith("JSON:"):
            raise ws.SecretError("corrupt")
        return json.loads(text[5:])

    monkeypatch.setattr(cs, "write_json_secret", wjs)
    monkeypatch.setattr(cs, "read_json_secret", rjs)
    cs.save_recorder_credential(LOCAL_A, "user-a", "pw-a")
    cs.save_recorder_credential(LOCAL_B, "user-b", "pw-b")
    rr.save_registry({"schema": rr.REGISTRY_SCHEMA, "recorders": [
        {"local_id": LOCAL_A, "cloud_recorder_id": REC_A, "display_name": "Front",
         "url": "http://192.0.2.10", "driver": "hikvision-isapi", "is_primary": True,
         "is_configured": True, "identity_fingerprint": "serial:AAA"},
        {"local_id": LOCAL_B, "cloud_recorder_id": REC_B, "display_name": "Back",
         "url": "http://192.0.2.20", "driver": "dahua-cgi", "is_primary": False,
         "is_configured": True},
    ]})


def test_targets_cover_every_configured_recorder(monkeypatch, tmp_path):
    _registry(monkeypatch, tmp_path)
    base = _cfg(tmp_path)
    targets = sm.build_targets(base, REC_A, all_recorders=True)
    assert [(t.recorder_id, t.name, t.primary) for t in targets] == [
        (REC_A, "Front", True), (REC_B, "Back", False)]
    assert [t.cfg.nvr_url for t in targets] == ["http://192.0.2.10", "http://192.0.2.20"]
    assert [t.cfg.nvr_username for t in targets] == ["user-a", "user-b"]
    assert targets[0].cfg.recorder_identity_fingerprint == "serial:AAA"

    one = sm.build_targets(base, REC_B, all_recorders=False)
    assert [(t.recorder_id, t.cfg.nvr_password) for t in one] == [(REC_B, "pw-b")]


# ---------------------------------------------------------------------------
# --acceptance-json
# ---------------------------------------------------------------------------

class TinyEnv(fa.Env):
    def __init__(self):
        self.command_id = None

    def agent_meta(self):
        return {"version": "5.1.2", "build_sha": "abc"}

    def state(self):
        return STATE

    def cloud_auth(self):
        return {"agent_id": "agent-1", "site_id": "site-1"}

    def runtime_health(self):
        return {"heartbeat_at": fa._iso(self.now())}

    def base_config(self):
        return SimpleNamespace(heartbeat_seconds=60, analytics_enabled=False, update_url="")

    def open_driver(self, cfg):
        raise core.DriverError("unreachable")

    def spool_probe(self, cfg):
        return {"exists": True, "depth": 0, "max_rows": 10, "overflow": False}


def test_acceptance_json_cli_writes_the_suite_result(monkeypatch, tmp_path, capsys):
    cfg = _cfg(tmp_path)
    cfg.supabase_url = cfg.publishable_key = ""
    monkeypatch.setattr(sm, "build_targets", lambda *_a, **_k: [
        fa.Target(REC_A, "Front", SimpleNamespace(recorder_cloud_id=REC_A, recovery_enabled=False,
                                                   spool_path=tmp_path / "s.sqlite"), True)])
    out = tmp_path / "out" / "acceptance.json"
    code = core.cmd_acceptance_json(cfg, str(out), _state=dict(STATE), _env=TinyEnv())
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["schema"] == fa.SCHEMA and result["summary"]["failed"] >= 1
    assert code == 2                                    # Recorder auth FAIL
    printed = capsys.readouterr().out
    assert "Agent                    PASS" in printed
    assert re.search(r"\d+/\d+ PASS \(", printed) and "Tested: " in printed
    assert "agent-secret-key-value" not in out.read_text(encoding="utf-8")


def test_acceptance_json_is_a_cli_flag():
    text = (AGENT / "watchlog_agent.py").read_text(encoding="utf-8")
    assert '"--acceptance-json"' in text and "cmd_acceptance_json(cfg, args.acceptance_json)" in text


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
