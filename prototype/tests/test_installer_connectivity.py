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
        self.assertIn("ensure_background_agent()", self._finalize_body(),
                      "connectivity must not depend on any later step completing")

    def test_it_is_started_on_the_worker_thread_not_the_gui_thread(self):
        self.assertNotIn("backend.ensure_background_agent()", self.GUI,
                         "blocking the GUI thread freezes the window; finalize_install "
                         "already starts the agent on the worker thread")

    def test_it_runs_after_the_heartbeat_proves_the_site_but_before_optional_work(self):
        body = self._finalize_body()
        beat = body.find("core.heartbeat(")
        start = body.find("ensure_background_agent()")
        push = body.find("provision_recorder_push(")
        self.assertNotEqual(-1, beat)
        self.assertLess(beat, start, "start the agent only once the site is proven reachable")
        self.assertLess(start, push, "start the agent before any optional recorder work")

    def test_the_outcome_is_logged(self):
        """A silent fail-open layer is one nobody can debug — 0.4.7 shipped exactly that."""
        body = self._finalize_body()
        window = body[body.find("ensure_background_agent()"):][:400]
        self.assertIn("core.log", window,
                      "the registration result must reach setup.log")

    def test_the_result_is_reported_to_the_caller(self):
        self.assertIn('"agent_start"', self.BACKEND,
                      "finalize_install must surface whether the agent actually started")


if __name__ == "__main__":
    unittest.main(verbosity=1)
