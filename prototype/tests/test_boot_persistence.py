#!/usr/bin/env python3
"""
0.4.10 regression — the site that did not survive a PC reboot.

FIELD EVIDENCE: a 0.4.8 agent enrolled at 11:02:24, heartbeated and shipped events until
11:17:39 (915s, capabilities reported), the customer restarted the PC, and it never came
back. agent.log contained nothing explaining it.

ROOT CAUSE (audited, and reproduced): run-agent.ps1 sets $ErrorActionPreference = "Stop"
for the whole script and runs the agent as a native command with its stderr redirected.
In Windows PowerShell 5.1 a native command's redirected stderr arrives as an ErrorRecord,
and under EAP=Stop that ErrorRecord is a TERMINATING error (NativeCommandError). So the
agent's FIRST stderr line - any of its 17 `raise SystemExit("FATAL: ...")` sites, any
uncaught traceback, any library warning - aborted the script, killed the `while ($true)`
supervision loop, and ended the scheduled task instance. With -AtStartup as the only
trigger, nothing restarted it until the next reboot.

Worse, the abort happened mid-pipe, so the stderr line never reached agent.log either:
the defect erased its own evidence. That is why the log showed nothing.

The behavioural test below runs BOTH the broken and the fixed loop shape through a real
powershell.exe. It fails on the old pattern and passes on the new one, so it cannot pass
vacuously.

    pytest -q prototype/tests/test_boot_persistence.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "installer"

POWERSHELL = (Path(os.environ.get("SYSTEMROOT", "C:/Windows"))
              / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
HAVE_PS = os.name == "nt" and POWERSHELL.exists()

# A stand-in for the agent: writes one stdout line, one STDERR line, then exits non-zero -
# exactly the shape of `raise SystemExit("FATAL: ...")` on a boot with no network yet.
CHILD = textwrap.dedent("""
    import sys
    print("agent up", flush=True)
    sys.stderr.write("FATAL: cannot reach WatchLog\\n")
    sys.stderr.flush()
    raise SystemExit(3)
""")


def _run_loop(tmp: Path, *, guarded: bool) -> tuple:
    """Run 3 iterations of the launcher loop shape and report how many completed.

    guarded=False reproduces the shipped 0.4.5-0.4.9 shape (EAP=Stop around the native
    call, no try/catch). guarded=True is the 0.4.10 shape.
    """
    child = tmp / "fake_agent.py"
    child.write_text(CHILD, encoding="utf-8")
    log = tmp / "agent.log"
    body_open = "try {" if guarded else ""
    body_close = "} catch { }" if guarded else ""
    eap_inner = '$ErrorActionPreference = "Continue"' if guarded else ""
    script = tmp / "loop.ps1"
    script.write_text(textwrap.dedent(f"""
        $ErrorActionPreference = "Stop"
        $log = "{str(log).replace(chr(92), '/')}"
        {eap_inner}
        $i = 0
        while ($i -lt 3) {{
          Add-Content -Path $log -Value "==== iteration $i ===="
          {body_open}
            & "{sys.executable.replace(chr(92), '/')}" "{str(child).replace(chr(92), '/')}" 2>&1 |
              ForEach-Object {{ Add-Content -Path $log -Value ([string]$_) }}
          {body_close}
          $i = $i + 1
        }}
        Add-Content -Path $log -Value "==== LOOP SURVIVED ===="
    """), encoding="utf-8")

    subprocess.run([str(POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(script)], capture_output=True, text=True, timeout=120)
    text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    return text.count("==== iteration"), ("LOOP SURVIVED" in text), text


@unittest.skipUnless(HAVE_PS, "needs Windows PowerShell 5.1")
class LauncherLoopSurvivesAgentStderrTests(unittest.TestCase):
    """The behaviour itself, through a real powershell.exe."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_the_OLD_unguarded_loop_really_does_die_on_the_first_stderr_line(self):
        """Pins the defect. If this ever stops failing, the test below proves nothing."""
        iterations, survived, _text = _run_loop(self.tmp, guarded=False)
        self.assertEqual(1, iterations,
                         "expected the unguarded loop to abort during iteration 0")
        self.assertFalse(survived, "the unguarded loop was supposed to be killed by stderr")

    def test_the_GUARDED_loop_survives_and_keeps_restarting_the_agent(self):
        iterations, survived, text = _run_loop(self.tmp, guarded=True)
        self.assertEqual(3, iterations, f"loop died early; log was:\n{text}")
        self.assertTrue(survived, f"loop did not reach the end; log was:\n{text}")

    def test_the_agents_stderr_reaches_the_log_instead_of_vanishing(self):
        """The old shape aborted mid-pipe, so the FATAL line never reached agent.log -
        the defect erased its own evidence. Support needs that line."""
        _i, _s, text = _run_loop(self.tmp, guarded=True)
        self.assertIn("FATAL: cannot reach WatchLog", text)


class LauncherSourceGuardTests(unittest.TestCase):
    """Source-level guards, so the shape cannot regress even where PowerShell is absent."""

    RUN = (INSTALLER / "run-agent.ps1").read_text(encoding="utf-8")
    REG = (INSTALLER / "register-service.ps1").read_text(encoding="utf-8")

    def test_the_loop_body_is_wrapped_in_try_catch(self):
        body = self.RUN[self.RUN.find("while ($true)"):]
        self.assertIn("try {", body)
        self.assertIn("catch", body)

    def test_error_action_preference_is_not_stop_inside_the_loop(self):
        before = self.RUN[:self.RUN.find("while ($true)")]
        self.assertIn('$ErrorActionPreference = "Continue"', before,
                      "EAP must be relaxed before the loop; under Stop a native command's "
                      "stderr is a TERMINATING error in PowerShell 5.1")

    def test_log_writes_cannot_propagate_an_error(self):
        self.assertIn("function Write-AgentLog", self.RUN)
        fn = self.RUN[self.RUN.find("function Write-AgentLog"):][:400]
        self.assertIn("catch", fn, "a full disk or a locked log must not kill the agent")

    def test_log_rotation_happens_inside_the_loop(self):
        """Rotating once before a loop that runs for months lets agent.log grow unbounded."""
        idx_loop = self.RUN.find("while ($true)")
        idx_rot = self.RUN.find("5000000")
        self.assertGreater(idx_rot, idx_loop, "rotation must be inside the supervision loop")

    def test_there_is_a_watchdog_trigger_not_just_at_startup(self):
        """-AtStartup alone means any agent death is unrecoverable without a reboot."""
        self.assertIn("RepetitionInterval", self.REG,
                      "a repeating watchdog trigger is required; it is a no-op while healthy "
                      "because -MultipleInstances IgnoreNew refuses a second instance")

    def test_a_stale_recorded_instance_is_cleared_before_registering(self):
        """An unclean shutdown can leave Task Scheduler believing an instance is running;
        with IgnoreNew that would make the next -AtStartup trigger a silent no-op."""
        self.assertIn("Stop-ScheduledTask", self.REG)
        self.assertIn("watchlog-agent", self.REG,
                      "an orphaned agent process must be cleared too")


class BootToleranceTests(unittest.TestCase):
    """The agent must not die when the WAN is not up yet at boot."""

    SRC = (ROOT / "agent" / "watchlog_agent.py").read_text(encoding="utf-8")

    def test_startup_cloud_calls_tolerate_transport_errors(self):
        """Cloud.call raises requests.ConnectionError (an OSError) on a dead WAN, which is
        NOT a RuntimeError. -AtStartup fires before the network stack is ready."""
        window = self.SRC[self.SRC.find('cloud.call("wl_sync_cameras"'):][:1400]
        self.assertIn("requests.RequestException", window,
                      "camera sync at startup must survive a not-yet-ready network")
        window2 = self.SRC[self.SRC.find('cloud.call("wl_sync_capabilities"'):][:900]
        self.assertIn("requests.RequestException", window2)

    def test_registration_is_not_funded_from_the_optional_budget(self):
        """Boot persistence is not optional post-connection work. 0.4.9 gave it whatever was
        left of 120s, so a slow probe could hand it a fraction of a second."""
        backend = (ROOT / "agent" / "setup_backend.py").read_text(encoding="utf-8")
        call = backend[backend.find("agent_start = ensure_background_agent("):][:160]
        self.assertIn("max(", call, "registration needs a guaranteed floor, not leftovers")

    def test_closing_the_window_does_not_abort_a_connected_install(self):
        """closeEvent (window X / Alt+F4) bypasses cancel(), so it kept exit_code=1 and NSIS
        skipped WriteUninstaller + the Add/Remove Programs keys on a working site."""
        gui = (ROOT / "agent" / "setup_gui.py").read_text(encoding="utf-8")
        ce = gui[gui.find("def closeEvent"):][:1200]
        self.assertIn("site_connected", ce)
        self.assertIn("self.exit_code = 0", ce)

    def test_the_proven_driver_is_persisted_not_discarded(self):
        backend = (ROOT / "agent" / "setup_backend.py").read_text(encoding="utf-8")
        self.assertNotIn('section["nvr_driver"] = "auto"', backend,
                         "writing 'auto' throws away the driver just proven against this "
                         "recorder and makes every later probe re-walk the vendor list")


if __name__ == "__main__":
    unittest.main(verbosity=1)
