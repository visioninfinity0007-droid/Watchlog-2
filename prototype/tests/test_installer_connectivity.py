#!/usr/bin/env python3
"""
0.4.7 regression — "it connected, then went offline again".

THE FIELD BUG. The NSIS installer runs the setup wizard under ExecWait and only
runs register-service.ps1 AFTERWARDS. So anything that stopped the wizard from
exiting -- a wedged probe, a customer closing the window -- meant the scheduled
task was never created and the background agent never ran. The site enrolled,
heartbeated exactly once from setup itself, and was then offline forever.

The live evidence was unambiguous: agents at 0.4.1, 0.4.5 and 0.4.6 were each
last seen 3-20 SECONDS after enrolling, with capabilities never reported, while
the 0.4.3 agent -- whose wizard completed -- ran for three days and reported
seven capabilities.

The fix is an ordering guarantee: once enrollment and the recorder credential are
proven, START THE AGENT. Acceptance is a report, and a report must never decide
whether a site reports.

    pytest -q prototype/tests/test_installer_connectivity.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import setup_backend as sb  # noqa: E402


class EnsureBackgroundAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "register-service.ps1").write_text("# stub", encoding="utf-8")
        self.seen = []

    def _runner(self, code=0, out="", raises=None):
        def run(cmd, timeout):
            self.seen.append((cmd, timeout))
            if raises:
                raise raises
            return code, out
        return run

    @unittest.skipUnless(os.name == "nt", "background registration is Windows-only")
    def test_success_is_reported(self):
        out = sb.ensure_background_agent(self.tmp, _run=self._runner(0))
        self.assertTrue(out["started"])

    @unittest.skipUnless(os.name == "nt", "background registration is Windows-only")
    def test_it_invokes_the_real_registration_script(self):
        sb.ensure_background_agent(self.tmp, _run=self._runner(0))
        cmd, _timeout = self.seen[0]
        self.assertIn("powershell.exe", cmd[0].lower())
        self.assertIn(str(self.tmp / "register-service.ps1"), cmd)
        self.assertIn("-InstallDir", cmd)

    @unittest.skipUnless(os.name == "nt", "background registration is Windows-only")
    def test_powershell_path_is_well_formed(self):
        """A mangled interpreter path would silently mean 'never starts'."""
        sb.ensure_background_agent(self.tmp, _run=self._runner(0))
        exe = self.seen[0][0][0]
        self.assertTrue(exe.lower().endswith("powershell.exe"), exe)
        self.assertIn("v1.0", exe, f"the v1.0 path segment was lost: {exe}")
        self.assertNotIn("\v", exe, "a \\v escape mangled the interpreter path")

    @unittest.skipUnless(os.name == "nt", "background registration is Windows-only")
    def test_a_failed_registration_is_reported_not_claimed(self):
        out = sb.ensure_background_agent(self.tmp, _run=self._runner(2, "Access denied"))
        self.assertFalse(out["started"])
        self.assertIn("Access denied", out["detail"])

    @unittest.skipUnless(os.name == "nt", "background registration is Windows-only")
    def test_a_missing_script_is_reported_not_raised(self):
        empty = Path(tempfile.mkdtemp())
        out = sb.ensure_background_agent(empty, _run=self._runner(0))
        self.assertFalse(out["started"])
        self.assertIn("not found", out["detail"])
        self.assertEqual([], self.seen, "must not shell out when there is nothing to run")

    @unittest.skipUnless(os.name == "nt", "background registration is Windows-only")
    def test_an_exploding_subprocess_cannot_break_setup(self):
        out = sb.ensure_background_agent(
            self.tmp, _run=self._runner(raises=OSError("no shell")))
        self.assertFalse(out["started"])

    @unittest.skipUnless(os.name == "nt", "background registration is Windows-only")
    def test_registration_is_bounded(self):
        sb.ensure_background_agent(self.tmp, timeout=45, _run=self._runner(0))
        self.assertEqual(45, self.seen[0][1], "an unbounded call could wedge the wizard")


class ConnectBeforeVerifyTests(unittest.TestCase):
    """The ordering guarantee itself, read off the source.

    Deliberately source-level: the behaviour is an ORDER between calls, and getting it
    backwards silently returns us to a fleet of sites that enrol once and never report.

    0.4.8 MOVED this. 0.4.7 started the agent in the GUI callback finalize_ok, which was
    wrong twice over: it ran AFTER finalize_install (so a wedge in there skipped it
    entirely), and it ran on the GUI thread (so it froze the window mid-repaint and the
    label still read "Confirming the WatchLog connection" while our own code was running).
    It now runs inside finalize_install, on the worker thread.
    """

    BACKEND = (ROOT / "agent" / "setup_backend.py").read_text(encoding="utf-8")
    GUI = (ROOT / "agent" / "setup_gui.py").read_text(encoding="utf-8")

    def _finalize_body(self) -> str:
        body = self.BACKEND[self.BACKEND.find("def finalize_install("):]
        cut = body.find(chr(10) + "def ", 1)
        return body[:cut] if cut != -1 else body

    def test_the_agent_is_started_inside_finalize_install(self):
        self.assertIn("ensure_background_agent(", self._finalize_body(),
                      "connectivity must not depend on any later step completing")

    def test_it_is_started_on_the_worker_thread_not_the_gui_thread(self):
        self.assertNotIn("backend.ensure_background_agent()", self.GUI,
                         "blocking the GUI thread freezes the window; finalize_install "
                         "already starts the agent on the worker thread")

    def test_it_runs_after_the_heartbeat_proves_the_site_but_before_optional_work(self):
        body = self._finalize_body()
        beat = body.find("core.heartbeat(")
        start = body.find("ensure_background_agent(")
        push = body.find("provision_recorder_push(")
        self.assertNotEqual(-1, beat)
        self.assertLess(beat, start, "start the agent only once the site is proven reachable")
        self.assertLess(start, push, "start the agent before any optional recorder work")

    def test_the_outcome_is_logged(self):
        """A silent fail-open layer is one nobody can debug — 0.4.7 shipped exactly that."""
        body = self._finalize_body()
        window = body[body.find("ensure_background_agent("):][:400]
        self.assertIn("core.log", window,
                      "the registration result must reach setup.log")

    def test_the_result_is_reported_to_the_caller(self):
        self.assertIn('"agent_start"', self.BACKEND,
                      "finalize_install must surface whether the agent actually started")



class ConfirmBackgroundAgentTests(unittest.TestCase):
    """"Task Running" is not "site reporting". Three releases shipped a green screen while
    the site went silent seconds later, because setup only ever proved its OWN heartbeat."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.log = self.tmp / "agent.log"

    def test_a_fresh_heartbeat_confirms(self):
        self.log.write_text("==== agent starting ====\nheartbeat ok\n", encoding="utf-8")
        out = sb.confirm_background_agent(timeout=5, since_offset=0, log_path=self.log,
                                          _sleep=lambda _s: None)
        self.assertTrue(out["confirmed"])

    def test_an_OLD_heartbeat_does_not_confirm(self):
        """The heartbeat setup itself sent must not be mistaken for the background agent."""
        old = "heartbeat ok\n"
        self.log.write_text(old, encoding="utf-8")
        out = sb.confirm_background_agent(timeout=2, since_offset=len(old),
                                          log_path=self.log, _sleep=lambda _s: None)
        self.assertFalse(out["confirmed"],
                         "only a beat written AFTER we started the task counts")

    def test_silence_times_out_honestly(self):
        self.log.write_text("==== agent starting ====\n", encoding="utf-8")
        out = sb.confirm_background_agent(timeout=2, since_offset=0, log_path=self.log,
                                          _sleep=lambda _s: None)
        self.assertFalse(out["confirmed"])
        self.assertIn("no background heartbeat", out["detail"])

    def test_a_missing_log_is_not_an_error(self):
        out = sb.confirm_background_agent(timeout=2, since_offset=0,
                                          log_path=self.tmp / "nope.log",
                                          _sleep=lambda _s: None)
        self.assertFalse(out["confirmed"])

    def test_it_is_bounded(self):
        started = time.monotonic()
        sb.confirm_background_agent(timeout=1, since_offset=0, log_path=self.log,
                                    _sleep=lambda _s: None)
        self.assertLess(time.monotonic() - started, 30, "confirmation must be bounded")


class ReadyScreenTellsTheTruthTests(unittest.TestCase):
    GUI = (ROOT / "agent" / "setup_gui.py").read_text(encoding="utf-8")

    def test_the_ready_screen_reports_background_state(self):
        self.assertIn("_background_line", self.GUI,
                      "the green screen must say whether the BACKGROUND agent is reporting")

    def test_an_agent_that_is_not_running_is_not_presented_as_fine(self):
        body = self.GUI[self.GUI.find("def _background_line"):][:1200]
        self.assertIn("NOT running", body)
        self.assertIn("will not", body)

    def test_it_does_not_claim_reporting_it_cannot_verify(self):
        """0.4.8 claimed/denied "reporting" from a local log the PowerShell redirection
        does not write promptly, and wrongly failed a healthy agent. Only claim what
        register-service.ps1 actually verified: the service is running."""
        body = self.GUI[self.GUI.find("def _background_line"):][:1200]
        ok = body[body.find("if info.get(\"started\")"):][:220]
        self.assertNotIn("reporting", ok,
                         "the success line must not assert reporting; it is not verified here")

class PostConnectPhaseIsBoundedTests(unittest.TestCase):
    """The structural guarantee, and the reason it exists.

    Four separate hangs shipped in the stretch of setup that runs AFTER the site is
    connected: the acceptance suite (0.4.5), a pipe deadlock (0.4.7), a blocked GUI
    thread (0.4.7), and an ini lock (0.4.9). Fixing them one at a time kept failing
    because the defect is the SHAPE -- optional work was allowed to pin the wizard
    forever. A total budget makes "setup always reaches a final screen" a property of
    the design instead of something that has to be got right in four places.
    """

    BACKEND = (ROOT / "agent" / "setup_backend.py").read_text(encoding="utf-8")

    def _finalize_body(self) -> str:
        body = self.BACKEND[self.BACKEND.find("def finalize_install("):]
        cut = body.find(chr(10) + "def ", 1)
        return body[:cut] if cut != -1 else body

    def test_there_is_a_total_budget_for_optional_work(self):
        self.assertIn("POST_CONNECT_BUDGET_SECONDS", self.BACKEND)
        self.assertIn("optional_deadline", self._finalize_body())

    def test_the_budget_is_generous_but_finite(self):
        import setup_backend as backend
        self.assertGreaterEqual(backend.POST_CONNECT_BUDGET_SECONDS, 60,
                                "too tight: normal slow sites would be cut short")
        self.assertLessEqual(backend.POST_CONNECT_BUDGET_SECONDS, 300,
                             "too loose: a customer stares at a spinner that long")

    def test_every_optional_step_draws_from_the_shared_deadline(self):
        body = self._finalize_body()
        after = body[body.find("optional_deadline"):]
        self.assertIn("_remaining(", after,
                      "optional steps must be capped by the remaining budget, not their own")

    def test_clearing_the_enrollment_code_cannot_fail_the_install(self):
        """The background agent now holds watchlog.ini open, so the atomic rewrite can
        raise PermissionError. A cosmetic tidy-up must never take down a connected site."""
        src = self.BACKEND[self.BACKEND.find("def _clear_consumed_code("):][:1800]
        self.assertIn("except Exception", src)
        self.assertIn("-> bool", src, "it must report success, not raise")

    def test_a_connected_site_exits_zero_so_the_install_is_recorded(self):
        """Exiting non-zero makes NSIS abort and skip the uninstaller + Add/Remove
        Programs entries, leaving a working site Windows does not know is installed."""
        gui = (ROOT / "agent" / "setup_gui.py").read_text(encoding="utf-8")
        self.assertIn("self.exit_code = 0 if getattr(self, \"site_connected\", False) else 1", gui)


if __name__ == "__main__":
    unittest.main(verbosity=1)
