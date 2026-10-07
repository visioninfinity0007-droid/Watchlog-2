#!/usr/bin/env python3
"""Release performance gate (A-Z sections 33/34): percentiles of measured start-up, not one
wall-clock run against a loose limit.

* the statistics and budget verdicts are exact (nearest-rank p95, a missing metric fails);
* the Setup UI records its start-up phases only when asked (WATCHLOG_UI_TIMING_PATH), and the
  installer-child self-test records them in order;
* the Windows Release workflow runs the gate on the frozen EXEs and keeps its report;
* the committed budgets are strict (the Setup UI lifecycle is gated well under the old 60 s).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import release_perf_gate as gate  # noqa: E402


def test_nearest_rank_percentiles():
    s = [4.1, 3.9, 7.7, 4.7, 4.2]
    assert gate.percentile(s, 50) == 4.2
    assert gate.percentile(s, 95) == 7.7          # with 5 runs, p95 is the slowest
    assert gate.summarize(s)["max"] == 7.7


def test_budgets_breach_and_a_missing_metric_fails():
    metrics = {"setup_ui.first_visible_s": gate.summarize([4.0, 4.5, 9.0, 4.2, 4.1])}
    breaches = gate.evaluate(metrics, {"setup_ui.first_visible_s": {"p95": 8.0, "max": 12.0},
                                       "agent.version_s": {"p95": 15.0, "provisional": True}})
    assert breaches == ["agent.version_s: not measured",
                        "setup_ui.first_visible_s: p95 9.00 s > 8.00 s"]
    assert gate.evaluate(metrics, {"setup_ui.first_visible_s": {"p95": 10.0}}) == []


def test_the_committed_budgets_are_strict():
    spec = json.loads((ROOT / "prototype" / "packaging" / "perf-budgets.json").read_text(encoding="utf-8"))
    b = spec["budgets"]
    assert spec["runs"] >= 6                      # one cold launch + at least five warm ones
    assert b["setup_ui.lifecycle_s"]["p95"] <= 12.0 and b["setup_ui.first_visible_s"]["p95"] <= 8.0
    assert b["setup_ui.unpack_s"]["p95"] <= 6.0
    assert b["setup_ui.cold.lifecycle_s"]["max"] <= 15.0      # never looser than the old gate
    assert b["agent.version_s"]["p50"] <= 12.0 and b["agent.version_s"]["max"] <= 30.0
    assert b["agent.cold.version_s"]["max"] <= 30.0
    assert b["agent.selftest_s"]["p50"] <= 20.0 and b["agent.selftest_s"]["max"] <= 30.0
    assert not any(budget.get("provisional") for budget in b.values())


def test_the_release_workflow_runs_the_gate_on_the_frozen_exes():
    wf = (ROOT / ".github" / "workflows" / "windows-release.yml").read_text(encoding="utf-8")
    assert "python tools/release_perf_gate.py" in wf
    assert "dist-installer/WatchLog-Perf-Report.json" in wf
    assert "release performance gate failed" in wf


def test_the_setup_ui_records_its_phases_only_when_asked():
    src = (ROOT / "prototype" / "agent" / "setup_gui.py").read_text(encoding="utf-8")
    assert '_TIMING_PATH = os.environ.get("WATCHLOG_UI_TIMING_PATH")' in src
    assert "if not _TIMING_PATH or phase in _PHASES:" in src


@pytest.mark.skipif(os.name != "nt", reason="the Setup UI self-test runs on Windows")
def test_the_installer_child_selftest_records_its_phases_in_order(tmp_path):
    pytest.importorskip("PySide6")
    timing = tmp_path / "timing.json"
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "WATCHLOG_UI_TIMING_PATH": str(timing),
           "PROGRAMDATA": str(tmp_path / "pd")}
    proc = subprocess.run([sys.executable, str(ROOT / "prototype" / "agent" / "setup_gui.py"),
                           "--ui-selftest", "--installer-child"], env=env, timeout=300)
    assert proc.returncode == 0
    phases = json.loads(timing.read_text(encoding="utf-8"))["phases"]
    order = ["python_start", "qt_imported", "backend_imported", "qapplication",
             "window_constructed", "first_visible", "installer_child_closed", "selftest_done"]
    assert [p for p in order if p in phases] == order
    assert [phases[p] for p in order] == sorted(phases[p] for p in order)


@pytest.mark.skipif(os.name != "nt", reason="exercises the gate end to end with stand-in EXEs")
def test_the_gate_end_to_end_with_stand_in_exes(tmp_path):
    # Stand-ins: a "Setup UI" that writes its phases, an "Agent" that answers quickly.
    ui_py = tmp_path / "ui.py"
    ui_py.write_text(
        "import json, os, sys, time\n"
        "t = time.time()\n"
        "json.dump({'phases': {'python_start': t, 'first_visible': t + 0.01}},"
        " open(os.environ['WATCHLOG_UI_TIMING_PATH'], 'w'))\n", encoding="utf-8")
    ui = tmp_path / "ui.cmd"
    ui.write_text(f'@"{sys.executable}" "{ui_py}" %*\r\n', encoding="ascii")
    agent = tmp_path / "agent.cmd"
    agent.write_text("@exit /b 0\r\n", encoding="ascii")
    report = tmp_path / "perf.json"
    assert gate.main(["--setup-ui", str(ui), "--agent", str(agent), "--runs", "2",
                      "--json", str(report)]) == 0
    body = json.loads(report.read_text(encoding="utf-8"))
    assert body["ok"] and body["metrics"]["setup_ui.cold.lifecycle_s"]["n"] == 1
    assert body["metrics"]["setup_ui.lifecycle_s"]["n"] == 1     # the first launch is the cold one
    assert body["metrics"]["agent.cold.version_s"]["n"] == 1 and body["metrics"]["agent.version_s"]["n"] == 1
    assert body["metrics"]["agent.selftest_s"]["n"] == 3          # median of three, not one sample
    agent.write_text("@exit /b 3\r\n", encoding="ascii")
    assert gate.main(["--setup-ui", str(ui), "--agent", str(agent), "--runs", "1",
                      "--json", str(report)]) == 1
    assert "exit 3" in " ".join(json.loads(report.read_text(encoding="utf-8"))["failures"])


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
