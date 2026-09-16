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
    """The ordering guarantee itself, read off the wizard source.

    This is deliberately a source-level assertion: the behaviour it protects is an
    ORDER between two calls, and getting it backwards silently returns us to a
    fleet of sites that enrol once and never report again.
    """

    SRC = (ROOT / "agent" / "setup_gui.py").read_text(encoding="utf-8")

    def test_the_agent_is_started_before_acceptance_runs(self):
        start = self.SRC.find("ensure_background_agent")
        accept = self.SRC.find("run_acceptance")
        self.assertNotEqual(-1, start, "the wizard never starts the background agent")
        self.assertNotEqual(-1, accept)
        self.assertLess(start, accept,
                        "acceptance runs before the agent is started — a wedged or "
                        "abandoned verification would leave the site permanently offline")

    def test_starting_the_agent_is_not_conditional_on_acceptance_passing(self):
        window = self.SRC[self.SRC.find("def finalize_ok"):self.SRC.find("def acceptance_done")]
        self.assertIn("ensure_background_agent", window,
                      "the agent must be started in finalize_ok, where enrollment is "
                      "already proven — not gated behind the acceptance verdict")


if __name__ == "__main__":
    unittest.main(verbosity=1)
