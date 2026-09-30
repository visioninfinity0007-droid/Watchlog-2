#!/usr/bin/env python3
"""0.4.6 regression — the field install that hung forever on "Running final acceptance checks".

Two compounding defects put a customer's installer in an unbreakable spinner:

1. ``cmd_accept`` ran unbounded probes, so one wedged probe stalled the whole suite.
2. ``StatusController`` enforced its deadline with
   ``subprocess.run(capture_output=True, timeout=N)``. On timeout CPython kills the process it
   launched and then calls ``communicate()`` a SECOND time with NO timeout to drain the pipes.
   watchlog-agent.exe is a PyInstaller --onefile build, so the process we launch is only a
   bootloader; the surviving Python grandchild kept the pipe's write end open, so that drain
   blocked for as long as the survivor lived rather than for the deadline. MEASURED on the old
   code: a 3s deadline against a 25s survivor returned after 25.6s. A probe wedged indefinitely
   therefore pinned the GUI indefinitely, and the ``except Exception -> CANNOT_VERIFY``
   fail-closed path never ran.

These tests pin both fixes, and the important one below spawns a REAL surviving grandchild.
"""
from __future__ import annotations

import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

import acceptance  # noqa: E402
from status_controller import StatusController  # noqa: E402


def _boom():
    raise RuntimeError("boom")


class CheckBudgetTests(unittest.TestCase):
    """A probe may be slow; it may never be able to stall the suite."""

    def test_a_wedged_soft_check_warns_and_the_suite_still_finishes(self):
        started = time.monotonic()
        report = acceptance.run_checks([
            {"key": "fast", "label": "Fast", "hard": True, "run": lambda: "pass"},
            {"key": "wedged", "label": "Wedged soft probe", "hard": False,
             "run": lambda: time.sleep(30), "budget": 1},
            {"key": "after", "label": "Runs after", "hard": True, "run": lambda: "pass"},
        ])
        self.assertLess(time.monotonic() - started, 15, "a wedged probe stalled the suite")
        by_key = {c["key"]: c for c in report["checks"]}
        self.assertEqual("warn", by_key["wedged"]["status"])
        self.assertIn("within 1s", by_key["wedged"]["detail"])
        # A soft probe can never block Ready, and checks after it still run.
        self.assertEqual("pass", by_key["after"]["status"])
        self.assertTrue(report["ready"])

    def test_a_wedged_hard_check_fails_closed(self):
        report = acceptance.run_checks([
            {"key": "wedged", "label": "Wedged required probe", "hard": True,
             "run": lambda: time.sleep(30), "budget": 1},
        ])
        self.assertEqual("blocked", report["checks"][0]["status"])
        self.assertFalse(report["ready"], "a required check that timed out must NOT be accepted")

    def test_budget_does_not_disturb_normal_results(self):
        report = acceptance.run_checks([
            {"key": "ok", "label": "Ok", "hard": True, "run": lambda: ("pass", "fine"),
             "budget": 30},
            {"key": "raises", "label": "Raises", "hard": False, "run": _boom, "budget": 30},
        ])
        self.assertEqual("pass", report["checks"][0]["status"])
        self.assertEqual("fine", report["checks"][0]["detail"])
        self.assertEqual("blocked", report["checks"][1]["status"])
        self.assertNotIn("boom", report["checks"][1]["detail"], "raw exception text leaked")

    def test_progress_is_announced_per_check(self):
        seen = []
        acceptance.run_checks(
            [{"key": "a", "label": "Recorder reachable", "hard": True, "run": lambda: "pass"}],
            on_progress=seen.append)
        self.assertTrue(any("Recorder reachable" in m for m in seen),
                        "installer would show an indeterminate bar with no changing text")


class AcceptanceSuiteShapeTests(unittest.TestCase):
    """Structural guarantees of the real check list built by cmd_accept."""

    def _checks(self):
        import watchlog_agent
        captured = {}

        def fake_run_checks(checks, **kwargs):
            captured["checks"] = checks
            return {"ready": False, "checks": [],
                    "summary": {"passed": 0, "warned": 0, "hard_failures": 1}}

        real = acceptance.run_checks
        acceptance.run_checks = fake_run_checks
        try:
            cfg = type("Cfg", (), {"nvr_url": "", "supabase_url": "", "publishable_key": "",
                                   "state_path": Path("nope.json"), "spool_path": Path("nope.db"),
                                   "spool_max_rows": 10, "detect": False})()
            try:
                watchlog_agent.cmd_accept(cfg, _state={}, live_seconds=0)
            except SystemExit:
                pass
        finally:
            acceptance.run_checks = real
        return captured.get("checks") or []

    def test_every_required_check_runs_before_every_optional_one(self):
        """Ready is decided from fast required probes; slow optional probes cannot delay it."""
        checks = self._checks()
        self.assertTrue(checks, "cmd_accept built no checks")
        hard_at = [i for i, c in enumerate(checks) if c.get("hard")]
        soft_at = [i for i, c in enumerate(checks) if not c.get("hard")]
        self.assertTrue(hard_at and soft_at)
        self.assertLess(max(hard_at), min(soft_at),
                        "a soft check runs before a required one and delays the verdict")

    def test_the_slow_probes_are_optional_and_can_never_block_ready(self):
        by_key = {c["key"]: c for c in self._checks()}
        for key in ("archive", "live", "ai"):
            self.assertIn(key, by_key)
            self.assertFalse(by_key[key]["hard"], f"{key} is slow; it must never gate Ready")

    def test_no_probe_is_allowed_to_run_unbounded(self):
        missing = [c["key"] for c in self._checks() if not c.get("budget")]
        self.assertFalse(missing, f"these probes could wedge the installer: {missing}")

    def test_required_checks_still_gate_ready(self):
        """The fix must not have quietly softened the security-relevant gates."""
        by_key = {c["key"]: c for c in self._checks()}
        for key in ("config", "identity", "cloud", "recorder", "cameras", "spool", "security"):
            self.assertIn(key, by_key)
            self.assertTrue(by_key[key]["hard"], f"{key} must remain a required check")


class LauncherCannotWedgeTests(unittest.TestCase):
    """THE field bug: the deadline must hold even when a grandchild outlives the process we kill."""

    @staticmethod
    def _agent_that_leaves_a_survivor(tmp: Path) -> list:
        """Stand-in for the --onefile bootloader: spawns a child that inherits our output handle
        and outlives us, which is exactly what defeated subprocess.run(capture_output=True)."""
        script = tmp / "fake_agent.py"
        script.write_text(textwrap.dedent(
            """
            import subprocess, sys, time
            print("bootloader up", flush=True)
            # inherits stdout/stderr and keeps them open past the caller's deadline
            subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            time.sleep(60)
            """), encoding="utf-8")
        return [sys.executable, str(script)]

    def test_runner_returns_within_its_deadline_despite_a_surviving_grandchild(self):
        with tempfile.TemporaryDirectory() as raw:
            ctrl = StatusController(agent_cmd=self._agent_that_leaves_a_survivor(Path(raw)),
                                    timeout=3)
            started = time.monotonic()
            code, out = ctrl._default_runner(["--accept"])
            elapsed = time.monotonic() - started
        # 0.4.5 measured 25.6s here against a 25s survivor and would measure ~60s against
        # this one: its deadline was worth only as long as the grandchild held the pipe.
        self.assertLess(elapsed, 20,
                        "the runner hung — this is the 0.4.5 'spinning forever' field bug")
        self.assertEqual(-1, code, "a timed-out run must report failure, never success")
        self.assertIn("bootloader up", out, "output written before the timeout should survive")

    def test_run_acceptance_fails_closed_when_the_agent_times_out(self):
        """A run that could not produce a report must BLOCK Ready, never green-light the site."""
        ctrl = StatusController(run_agent=lambda args, timeout=None: (-1, "partial output"))
        result = ctrl.run_acceptance()
        self.assertFalse(result["ready"])
        self.assertFalse(result["verified"])
        self.assertEqual(StatusController.CANNOT_VERIFY, result["verdict"])

    def test_timeout_is_a_real_backstop_above_the_sum_of_check_budgets(self):
        """The outer deadline must not fire before the checks get their own budgeted time —
        that mismatch is what made 0.4.5 kill a healthy run mid-flight."""
        self.assertGreaterEqual(StatusController()._timeout, 180)


if __name__ == "__main__":
    unittest.main(verbosity=1)
