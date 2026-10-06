#!/usr/bin/env python3
"""Fresh install: Setup claims only what the background Agent proved (field Build 41/69).

Canonical register-service.ps1 returned success once the scheduled task reached Running, and
Setup told the installer the background connector "reached WatchLog". Build 41 field evidence:
an Agent can run and even heartbeat while it never reached the recorder. The field builds
required a fresh proof written by THIS background process. Restored, non-blocking:
  * the Agent's heartbeat writes recorder_identified_at only when this process proved the
    recorder identity at startup (Secrets\\runtime-health.json, SYSTEM/Administrators-only);
  * register-service.ps1 -RequireRecorderReadiness (fresh install only) waits for a fresh
    heartbeat_at plus recorder_identified_at or recorder_seen_at, rejects stamps from before
    the task start or far in the future, checks the version, and exits 3 when the Agent runs
    but did not prove it (the Agent keeps running);
  * Setup maps exit 3 to "running, not proven" and the Ready text says so.
Repair/Upgrade and rollback never pass the switch (they have their own proofs and may restore
an older Agent).
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "prototype" / "agent"
INSTALLER = ROOT / "prototype" / "installer"
sys.path.insert(0, str(AGENT))

REGISTER = (INSTALLER / "register-service.ps1").read_text(encoding="utf-8")


def _block():
    start = REGISTER.index("if ($RequireRecorderReadiness) {")
    return REGISTER[start:]


def test_readiness_is_opt_in_and_measured_from_the_task_start():
    assert "[switch]$RequireRecorderReadiness" in REGISTER
    assert REGISTER.index("$taskStartUtc = [DateTime]::UtcNow") < REGISTER.index("Start-ScheduledTask")
    assert REGISTER.index("Start-ScheduledTask") < REGISTER.index("if ($RequireRecorderReadiness) {")


def test_readiness_needs_this_runs_heartbeat_and_recorder_and_rejects_future_stamps():
    block = _block()
    assert 'Join-Path $data "Secrets\\runtime-health.json"' in block
    assert "[string]$h.agent_version -eq $expected" in block
    assert "Get-Stamp $h.heartbeat_at" in block
    assert "Get-Stamp $h.recorder_identified_at" in block and "Get-Stamp $h.recorder_seen_at" in block
    assert "$stamp -ge $notBefore -and $stamp -le $notAfter" in block
    assert "AddMinutes(2)" in block
    assert "exit 3" in block


def test_repair_and_upgrade_never_require_it():
    for name in ("wl-repair-upgrade.ps1", "nsis/wl-upgrade.ps1"):
        text = (INSTALLER / name).read_text(encoding="utf-8")
        assert "RequireRecorderReadiness" not in text, name


@pytest.fixture
def backend(monkeypatch, tmp_path):
    import setup_backend
    (tmp_path / "register-service.ps1").write_text("# stub", encoding="utf-8")
    monkeypatch.setattr(setup_backend.os, "name", "nt")
    return setup_backend, tmp_path


@pytest.mark.parametrize("code,started,proven", [(0, True, True), (3, True, False), (2, False, None)])
def test_setup_maps_the_readiness_outcome(backend, code, started, proven):
    sb, base = backend
    seen = {}

    def run(cmd, timeout):
        seen["cmd"] = cmd
        return code, "out"
    out = sb.ensure_background_agent(base, 30, _run=run, require_readiness=True)
    assert "-RequireRecorderReadiness" in seen["cmd"]
    assert out["started"] is started
    if proven is not None:
        assert out["proven"] is proven


def test_other_background_starts_do_not_wait_for_readiness(backend):
    sb, base = backend
    seen = {}
    sb.ensure_background_agent(base, 30, _run=lambda cmd, t: seen.setdefault("cmd", cmd) and (0, ""))
    assert "-RequireRecorderReadiness" not in seen["cmd"]


def test_finalize_install_requires_readiness():
    text = (AGENT / "setup_backend.py").read_text(encoding="utf-8")
    fn = text[text.index("def finalize_install"):]
    assert "require_readiness=True" in fn.split("\ndef ")[0]


def test_heartbeat_stamps_identity_only_when_this_run_proved_the_recorder(monkeypatch):
    import agent_core
    import watchlog_agent as core
    written = []
    monkeypatch.setattr(agent_core, "update_runtime_health", lambda **f: written.append(f))  # heartbeat lives in agent_core (shared with Setup); patch its own collaborator
    cloud = SimpleNamespace(call=lambda *a, **k: {})
    state = {"agent_id": "a", "agent_key": "k"}
    core.heartbeat(cloud, state, SimpleNamespace(vendor="Hikvision", model="M", driver="hikvision-isapi"))
    core.heartbeat(cloud, state, None)
    assert written[0]["recorder_identified_at"] and written[1]["recorder_identified_at"] is None


def test_ready_text_never_claims_an_unproven_connection():
    import importlib
    try:
        gui = importlib.import_module("setup_gui")
    except Exception:  # noqa: BLE001 — Qt not installed in this environment
        pytest.skip("setup_gui needs PySide6")
    line = gui.SetupWindow._background_line
    unproven = line(SimpleNamespace(agent_start={"started": True, "proven": False}))
    proven = line(SimpleNamespace(agent_start={"started": True, "proven": True}))
    assert unproven.startswith("!") and "not yet confirmed" in unproven
    assert proven.startswith("✓")
