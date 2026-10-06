#!/usr/bin/env python3
"""Release performance gate: measured start-up of the packaged Setup UI and Agent.

Runs the frozen EXEs several times and gates percentiles against committed budgets
(prototype/packaging/perf-budgets.json), instead of one wall-clock run against a loose limit:

  setup_ui.unpack_s         launch -> Python running (one-file unpack; the 5.0.26 regression)
  setup_ui.first_visible_s  launch -> the setup window is shown
  setup_ui.lifecycle_s      launch -> the installer-child lifecycle self-test has exited
  setup_ui.cold.*           the same, for the first (cold) launch alone
  agent.version_s           launch -> `watchlog-agent.exe --version` has exited
  agent.selftest_s          launch -> `watchlog-agent.exe --selftest` has exited (once)

Phase times inside the Setup UI come from WATCHLOG_UI_TIMING_PATH (setup_gui._mark). Every
run must also succeed (exit 0). Writes WatchLog-Perf-Report.json (samples, p50/p95/max, sizes,
verdicts) and exits 1 on any breached budget or failed run. Budgets marked provisional are
enforced too; they are ceilings until a 5.1.1 CI baseline exists.

    python tools/release_perf_gate.py --setup-ui prototype/dist/watchlog-setup-ui.exe \\
        --agent prototype/dist/watchlog-agent.exe --json dist-installer/WatchLog-Perf-Report.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUDGETS = ROOT / "prototype" / "packaging" / "perf-budgets.json"


def percentile(samples: list[float], pct: float) -> float:
    """Nearest-rank percentile (no interpolation: with 5 runs, p95 is the slowest run)."""
    ordered = sorted(samples)
    rank = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return ordered[rank - 1]


def summarize(samples: list[float]) -> dict:
    return {"n": len(samples), "samples": [round(s, 3) for s in samples],
            "p50": round(percentile(samples, 50), 3), "p95": round(percentile(samples, 95), 3),
            "max": round(max(samples), 3)}


def evaluate(metrics: dict[str, dict], budgets: dict[str, dict]) -> list[str]:
    """Breaches, as readable lines. A budgeted metric that was not measured is a breach."""
    breaches = []
    for name, budget in sorted(budgets.items()):
        got = metrics.get(name)
        if not got or not got.get("n"):
            breaches.append(f"{name}: not measured")
            continue
        for stat in ("p50", "p95", "max"):
            if stat in budget and got[stat] > float(budget[stat]):
                tag = " (provisional budget)" if budget.get("provisional") else ""
                breaches.append(f"{name}: {stat} {got[stat]:.2f} s > {float(budget[stat]):.2f} s{tag}")
    return breaches


def _timed(cmd: list[str], env: dict, timeout: float) -> tuple[float, float, int]:
    launched = time.time()
    proc = subprocess.run(cmd, env=env, capture_output=True, timeout=timeout)
    return launched, time.time() - launched, proc.returncode


def measure_setup_ui(exe: Path, runs: int) -> tuple[dict[str, list[float]], list[str]]:
    # The first launch is cold (one-file unpack of ~50 MB onto a fresh disk, scanned by Defender:
    # what a technician sees once); the rest are warm. They are gated separately: mixing them
    # makes p95 just "the cold run" and the gate flaky.
    out: dict[str, list[float]] = {f"setup_ui{tag}.{name}": []
                                   for tag in (".cold", "")
                                   for name in ("unpack_s", "first_visible_s", "lifecycle_s")}
    failures = []
    for i in range(runs):
        key = "setup_ui.cold" if i == 0 else "setup_ui"
        with tempfile.TemporaryDirectory(prefix="wl-perf-ui-") as td:
            timing = Path(td) / "timing.json"
            env = {**os.environ, "QT_QPA_PLATFORM": "offscreen",
                   "WATCHLOG_UI_TIMING_PATH": str(timing)}
            launched, wall, code = _timed([str(exe), "--ui-selftest", "--installer-child"], env, 120)
            if code != 0:
                failures.append(f"setup UI run {i + 1}: exit {code}")
                continue
            try:
                phases = json.loads(timing.read_text(encoding="utf-8"))["phases"]
                out[f"{key}.unpack_s"].append(phases["python_start"] - launched)
                out[f"{key}.first_visible_s"].append(phases["first_visible"] - launched)
            except (OSError, ValueError, KeyError) as exc:
                failures.append(f"setup UI run {i + 1}: no phase timings ({type(exc).__name__})")
            out[f"{key}.lifecycle_s"].append(wall)
    return out, failures


def measure_agent(exe: Path, runs: int) -> tuple[dict[str, list[float]], list[str]]:
    out: dict[str, list[float]] = {"agent.version_s": [], "agent.selftest_s": []}
    failures = []
    env = dict(os.environ)
    for i in range(runs):
        _launched, wall, code = _timed([str(exe), "--version"], env, 120)
        if code != 0:
            failures.append(f"agent --version run {i + 1}: exit {code}")
        out["agent.version_s"].append(wall)
    _launched, wall, code = _timed([str(exe), "--selftest"], env, 600)
    if code != 0:
        failures.append(f"agent --selftest: exit {code}")
    out["agent.selftest_s"].append(wall)
    return out, failures


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--setup-ui", type=Path, required=True)
    ap.add_argument("--agent", type=Path, required=True)
    ap.add_argument("--budgets", type=Path, default=DEFAULT_BUDGETS)
    ap.add_argument("--runs", type=int, default=0, help="default: the budgets file's runs")
    ap.add_argument("--size", action="append", default=[], type=Path,
                    help="another artifact whose size is recorded (repeatable)")
    ap.add_argument("--json", type=Path, required=True)
    args = ap.parse_args(argv)

    spec = json.loads(args.budgets.read_text(encoding="utf-8"))
    runs = args.runs or int(spec.get("runs", 5))
    samples, failures = measure_setup_ui(args.setup_ui, runs)
    agent_samples, agent_failures = measure_agent(args.agent, runs)
    samples.update(agent_samples)
    failures += agent_failures
    metrics = {name: summarize(vals) for name, vals in samples.items() if vals}
    breaches = evaluate(metrics, spec["budgets"])
    report = {
        "schema": "watchlog.perf_report.v1",
        "runs": runs,
        "runner": {"image_os": os.environ.get("ImageOS"), "image_version": os.environ.get("ImageVersion")},
        "metrics": metrics,
        "budgets": spec["budgets"],
        "sizes": {p.name: p.stat().st_size for p in [args.setup_ui, args.agent, *args.size] if p.exists()},
        "failures": failures,
        "breaches": breaches,
        "ok": not failures and not breaches,
    }
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for name, m in sorted(metrics.items()):
        print(f"{name:28s} p50 {m['p50']:7.2f}  p95 {m['p95']:7.2f}  max {m['max']:7.2f}  (n={m['n']})")
    for line in failures + breaches:
        print(f"FAIL {line}", file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
