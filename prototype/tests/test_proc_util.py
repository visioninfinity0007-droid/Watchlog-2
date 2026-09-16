#!/usr/bin/env python3
"""
0.4.8 — the bounded-subprocess primitive, and the two mistakes that shipped without it.

``subprocess.run(capture_output=True, timeout=N)`` does not bound anything when the
child leaves a survivor holding the pipe: on timeout CPython kills the process it
started, then calls communicate() AGAIN WITH NO TIMEOUT, which then waits on the
SURVIVOR instead of on the deadline.

That shipped twice:
  * 0.4.5 — installer hung forever on "Running final acceptance checks" (onefile
    bootloader leaves a real Python grandchild behind).
  * 0.4.7 — fixed in status_controller, then REINTRODUCED verbatim in
    setup_backend.ensure_background_agent, hanging the wizard on step 06 again.

So there is now one implementation, and these tests pin both that it works and that
nobody quietly writes the broken version a third time.

    pytest -q prototype/tests/test_proc_util.py
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import proc_util  # noqa: E402


def survivor_script(tmp: Path, seconds: int = 60) -> list:
    """A child that spawns a grandchild inheriting its output handles and outlives it —
    i.e. a PyInstaller --onefile bootloader, which is what we actually launch."""
    script = tmp / "survivor.py"
    script.write_text(textwrap.dedent(f"""
        import subprocess, sys, time
        print("child up", flush=True)
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep({seconds})"])
        time.sleep({seconds})
        """), encoding="utf-8")
    return [sys.executable, str(script)]


class RunBoundedTests(unittest.TestCase):
    def test_deadline_holds_despite_a_surviving_grandchild(self):
        with tempfile.TemporaryDirectory() as raw:
            started = time.monotonic()
            code, out = proc_util.run_bounded(survivor_script(Path(raw)), 3)
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 20, "run_bounded hung — this is the 0.4.5/0.4.7 field bug")
        self.assertEqual(-1, code, "a timed-out run must report failure, never success")
        self.assertIn("child up", out, "output written before the timeout should survive")

    def test_a_normal_command_returns_its_code_and_output(self):
        code, out = proc_util.run_bounded(
            [sys.executable, "-c", "print('hello'); raise SystemExit(3)"], 30)
        self.assertEqual(3, code)
        self.assertIn("hello", out)

    def test_it_never_raises_for_a_child_that_cannot_start(self):
        try:
            code, out = proc_util.run_bounded(["definitely-not-a-real-binary-xyz"], 5)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"run_bounded raised instead of reporting: {type(exc).__name__}")
        self.assertNotEqual(0, code)

    def test_live_output_is_streamed_while_the_child_runs(self):
        seen = []
        proc_util.run_bounded(
            [sys.executable, "-c",
             "import time,sys\nfor i in range(3):\n print('line%d'%i, flush=True); time.sleep(0.5)"],
            30, on_output=seen.append)
        self.assertTrue(any("line0" in s for s in seen), f"no live output: {seen}")

    def test_skip_prefix_keeps_machine_lines_out_of_the_progress_stream(self):
        seen = []
        proc_util.run_bounded(
            [sys.executable, "-c",
             "import time,sys\nprint('ACCEPTANCE_JSON {}', flush=True)\n"
             "print('visible', flush=True)\ntime.sleep(0.6)"],
            30, on_output=seen.append, skip_prefix="ACCEPTANCE_JSON")
        self.assertFalse(any("ACCEPTANCE_JSON" in s for s in seen))

    def test_a_callback_that_raises_cannot_break_the_run(self):
        def boom(_line):
            raise RuntimeError("bad ui callback")
        code, _ = proc_util.run_bounded(
            [sys.executable, "-c",
             "import time\nprint('x', flush=True)\ntime.sleep(0.6)"], 30, on_output=boom)
        self.assertEqual(0, code)


class NoOneRewritesTheBrokenVersionTests(unittest.TestCase):
    """Source-level guard. Both shipped hangs were this exact call, so it is worth
    failing the build rather than discovering it in the field a third time."""

    AGENT = ROOT / "agent"

    # windows_secret runs a short-lived PowerShell that spawns nothing and must keep
    # stdout and stderr SEPARATE (it json-parses stdout). run_bounded deliberately merges
    # the two, so converting it would break the parse for no safety gain. Documented as a
    # known exception rather than silently allowlisted.
    EXCEPT = {"proc_util.py", "windows_secret.py"}

    @staticmethod
    def _strip_comments(text: str) -> list:
        out = []
        for line in text.splitlines():
            code = line.split("#", 1)[0] if not line.lstrip().startswith("#") else ""
            out.append(code)
        return out

    def test_no_agent_module_bounds_a_subprocess_with_capture_output_plus_timeout(self):
        offenders = []
        for path in sorted(self.AGENT.rglob("*.py")):
            if path.name in self.EXCEPT:
                continue
            code_lines = self._strip_comments(
                path.read_text(encoding="utf-8", errors="replace"))
            for num, line in enumerate(code_lines, 1):
                if "subprocess.run(" not in line:
                    continue
                window = " ".join(code_lines[num - 1:num + 2])
                if "capture_output=True" in window and "timeout=" in window:
                    offenders.append(f"{path.name}:{num}")
        self.assertFalse(
            offenders,
            "subprocess.run(capture_output=True, timeout=...) does NOT bound a child that "
            "leaves a survivor holding the pipe — use proc_util.run_bounded. Found: "
            + ", ".join(offenders))

    def test_the_wizard_does_not_start_the_agent_on_the_gui_thread(self):
        """Blocking the GUI thread freezes the window mid-repaint, which is why 0.4.7
        looked stuck on 'Confirming the WatchLog connection' while running our own code."""
        gui = (self.AGENT / "setup_gui.py").read_text(encoding="utf-8")
        self.assertNotIn(
            "backend.ensure_background_agent()", gui,
            "the GUI thread must not run the registration subprocess — finalize_install "
            "already does it on the worker thread")

    def test_the_agent_is_started_inside_finalize_install(self):
        backend = (self.AGENT / "setup_backend.py").read_text(encoding="utf-8")
        body = backend[backend.find("def finalize_install("):]
        body = body[:body.find("\ndef ", 1)] if "\ndef " in body[1:] else body
        self.assertIn("ensure_background_agent(", body,
                      "connectivity must not depend on any later step completing")
        self.assertLess(body.find("ensure_background_agent("),
                        body.find("provision_recorder_push("),
                        "start the agent before the optional recorder-push step")


if __name__ == "__main__":
    unittest.main(verbosity=1)
