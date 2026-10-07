#!/usr/bin/env python3
"""Setup stops a running Agent before it tests the recorder login (field: HASCO, 2026-10-07).

The 5.1.1 upgrade stage had restarted the Agent, so it held the Hikvision DS-7608NI-Q1's live
event session while Setup tested the login (30 s timeouts until it "went through after many
tests") and enrolled a new identity, which could not prove itself and was rolled back. One
process may talk to the recorder at a time. Proves:
- the backend stops the scheduled task and only this install's watchlog-agent.exe;
- pause/resume never raise and report failures honestly;
- the Setup wizard pauses once, before the first login test, never in Manage Recorders;
- an unfinished Setup starts the previous Agent again; a connected one leaves it to finalize.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent / "agent"))

import qt_fake  # noqa: E402
import setup_backend as sb  # noqa: E402


@pytest.fixture
def nt(monkeypatch):
    class _NtOs:
        name = "nt"

        def __getattr__(self, attr):
            import os
            return getattr(os, attr)
    monkeypatch.setattr(sb, "os", _NtOs())
    monkeypatch.setattr(sb, "_setup_log", lambda _m: None)


def test_pause_stops_the_task_and_only_this_installs_agent(nt, tmp_path):
    seen = {}

    def run(cmd, timeout):
        import os
        seen["cmd"], seen["dir"] = cmd, os.environ.get("WL_INSTALL_DIR")
        return 0, "WL_PAUSED running=1 task=True\r\n"
    out = sb.pause_background_agent(tmp_path, _run=run)
    assert out["paused"] and out["was_running"]
    assert seen["dir"] == str(tmp_path)
    script = sb._PAUSE_SCRIPT
    assert "Stop-ScheduledTask" in script and "-ieq $want" in script
    assert "Join-Path $env:WL_INSTALL_DIR 'watchlog-agent.exe'" in script
    import os
    assert "WL_INSTALL_DIR" not in os.environ        # restored after the call


@pytest.mark.parametrize("result", [(1, ""), (0, "garbage"), (-1, "")])
def test_pause_reports_failure_without_raising(nt, tmp_path, result):
    out = sb.pause_background_agent(tmp_path, _run=lambda cmd, timeout: result)
    assert out["paused"] is False


def test_pause_survives_a_runner_error(nt, tmp_path):
    def boom(cmd, timeout):
        raise OSError("no powershell")
    assert sb.pause_background_agent(tmp_path, _run=boom)["paused"] is False


def test_resume_runs_the_scheduled_task(nt):
    seen = {}

    def run(cmd, timeout):
        seen["cmd"] = cmd
        return 0, ""
    assert sb.resume_background_agent(_run=run)["resumed"] is True
    assert seen["cmd"][1:] == ["/Run", "/TN", sb.AGENT_TASK_NAME]
    assert sb.resume_background_agent(_run=lambda c, t: (1, ""))["resumed"] is False


def test_nothing_happens_off_windows(monkeypatch):
    class _Posix:
        name = "posix"
    monkeypatch.setattr(sb, "os", _Posix())
    assert sb.pause_background_agent()["paused"] is False
    assert sb.resume_background_agent()["resumed"] is False


@pytest.fixture(scope="module")
def gui():
    return qt_fake.load_setup_gui_with_fake_qt()


@pytest.fixture
def calls(gui, monkeypatch):
    log = []
    monkeypatch.setattr(gui.backend, "pause_background_agent", lambda *a, **k: log.append("pause"))
    monkeypatch.setattr(gui.backend, "resume_background_agent", lambda *a, **k: log.append("resume"))
    monkeypatch.setattr(gui.backend, "test_recorder",
                        lambda *a, **k: log.append("test") or {"url": "http://10.0.0.5"})
    return log


def _window(gui, tmp_path, **kw):
    gui.QApplication.instance() or gui.QApplication([])
    window = gui.SetupWindow(tmp_path / "watchlog.ini", **kw)
    window.run_worker = lambda fn, args, ok, *a, **k: fn(*args)   # run inline
    window.recorder_address = "10.0.0.5"
    window.user_edit.setText("admin")
    window.password_edit.setText("pw")
    return window


def test_setup_pauses_once_before_the_first_login_test(gui, calls, tmp_path):
    window = _window(gui, tmp_path)
    window.test_connection()
    window.test_connection()
    assert calls == ["pause", "test", "test"]


def test_manage_recorders_never_pauses_monitoring(gui, calls, tmp_path):
    window = _window(gui, tmp_path)
    window.manage_recorders = True
    window.test_connection()
    assert calls == ["test"]


def test_an_unfinished_setup_restarts_the_previous_agent(gui, calls, tmp_path):
    window = _window(gui, tmp_path)
    window.test_connection()
    window._resume_agent_if_unfinished()
    window._resume_agent_if_unfinished()             # once only
    assert calls == ["pause", "test", "resume"]


def test_a_connected_site_leaves_the_restart_to_finalize(gui, calls, tmp_path):
    window = _window(gui, tmp_path)
    window.test_connection()
    window.site_connected = True
    window._resume_agent_if_unfinished()
    assert calls == ["pause", "test"]


def test_closing_the_window_resumes(gui):
    src = (TESTS.parent / "agent" / "setup_gui.py").read_text(encoding="utf-8")
    body = src.split("def closeEvent(self, event: QCloseEvent):", 1)[1]
    assert body.lstrip().startswith("self._resume_agent_if_unfinished()")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
