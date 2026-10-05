#!/usr/bin/env python3
"""WatchLog Setup, Site Status and Manage Recorders must run elevated.

They read the SYSTEM+Administrators-only Secrets store and (re)register the SYSTEM
background task. Under UAC a plain Start Menu launch gets a filtered token, so before this
gate every Secrets read failed with a raw "Access is denied" (or Setup crashed at launch).
Interactive windows relaunch through the Windows permission prompt; installer and CI modes
fail closed instead, because their caller waits on this exact process's exit code.

    python -m pytest -q prototype/tests/test_setup_gui_elevation.py
"""
from __future__ import annotations

import ctypes
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import programdata_sandbox  # noqa: E402,F401  (before any agent import: no writes to the real ProgramData)

from setup_gui_harness import AGENT, import_setup_gui  # noqa: E402

sg = import_setup_gui()
GUI = (AGENT / "setup_gui.py").read_text(encoding="utf-8")


class _Refused:
    """Any window/app construction before the elevation gate is a failure."""
    def __init__(self, *args, **kwargs):
        raise AssertionError("a window was opened without administrator rights")


class ElevationGate(unittest.TestCase):
    def _run(self, argv, *, elevated, relaunched=True):
        calls = {"relaunch": 0, "notice": 0}

        def relaunch():
            calls["relaunch"] += 1
            return relaunched

        def notice():
            calls["notice"] += 1

        status_stub = types.ModuleType("site_status_gui")
        status_stub.main = lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("Site Status opened without administrator rights"))
        with patch.object(sys, "argv", ["watchlog-setup-ui.exe", *argv]), \
             patch.object(sg, "_is_elevated", return_value=elevated), \
             patch.object(sg, "_relaunch_elevated", side_effect=relaunch), \
             patch.object(sg, "_show_admin_required", side_effect=notice), \
             patch.object(sg, "_emit_line"), \
             patch.object(sg, "QApplication", _Refused), \
             patch.object(sg, "SetupWindow", _Refused), \
             patch.object(sg, "RecorderManagerWindow", _Refused), \
             patch.dict(sys.modules, {"site_status_gui": status_stub}), \
             patch.object(sg.backend, "migrate_legacy_credentials",
                          side_effect=AssertionError("migrated without administrator rights")):
            code = sg.main()
        return code, calls

    def test_unelevated_interactive_windows_relaunch_through_the_permission_prompt(self):
        for argv in (["--manage-recorders", "--config", "C:/x/watchlog.ini"],
                     ["--status", "--config", "C:/x/watchlog.ini"],
                     ["--config", "C:/x/watchlog.ini"]):
            with self.subTest(argv=argv):
                code, calls = self._run(argv, elevated=False, relaunched=True)
                self.assertEqual(code, 0)
                self.assertEqual(calls["relaunch"], 1)
                self.assertEqual(calls["notice"], 0)

    def test_declined_permission_prompt_says_administrator_required(self):
        code, calls = self._run(["--manage-recorders"], elevated=False, relaunched=False)
        self.assertEqual(code, sg.ADMIN_REQUIRED_EXIT)
        self.assertEqual(calls["notice"], 1)

    def test_relaunched_copy_still_not_elevated_fails_closed_without_relaunching(self):
        # A standard user on a PC with UAC turned off: "runas" starts the copy without an
        # administrator token and no prompt. Relaunching again would loop forever and never
        # say why.
        code, calls = self._run(["--manage-recorders", "--elevated-relaunch"], elevated=False)
        self.assertEqual(code, sg.ADMIN_REQUIRED_EXIT)
        self.assertEqual(calls["relaunch"], 0)
        self.assertEqual(calls["notice"], 1)

    def test_relaunch_marks_the_copy_it_starts(self):
        started = []

        def shell_execute(_hwnd, verb, exe, params, _cwd, _show):
            started.append((verb, exe, params))
            return 42

        windll = types.SimpleNamespace(shell32=types.SimpleNamespace(ShellExecuteW=shell_execute))
        with patch.object(sys, "argv", ["C:/WL/watchlog-setup-ui.exe", "--status",
                                        "--config", "C:/x/watchlog.ini"]),              patch.object(sys, "frozen", True, create=True),              patch.object(sys, "executable", "C:/WL/watchlog-setup-ui.exe"),              patch.object(ctypes, "windll", windll, create=True):
            self.assertTrue(sg._relaunch_elevated())
        verb, exe, params = started[0]
        self.assertEqual((verb, exe), ("runas", "C:/WL/watchlog-setup-ui.exe"))
        self.assertEqual(params.split(), ["--status", "--config", "C:/x/watchlog.ini",
                                          "--elevated-relaunch"])

    def test_installer_modes_fail_closed_and_never_detach(self):
        # NSIS ExecWaits --migrate-only; a detached elevated copy would let it read
        # success from a process that did nothing.
        code, calls = self._run(["--migrate-only", "--config", "C:/x/watchlog.ini"],
                                elevated=False)
        self.assertEqual(code, sg.ADMIN_REQUIRED_EXIT)
        self.assertEqual(calls["relaunch"], 0)
        self.assertEqual(calls["notice"], 0)

    def test_elevated_launch_proceeds_without_relaunch(self):
        with patch.object(sys, "argv", ["watchlog-setup-ui.exe", "--migrate-only",
                                        "--config", "C:/x/watchlog.ini"]), \
             patch.object(sg, "_is_elevated", return_value=True), \
             patch.object(sg, "_relaunch_elevated",
                          side_effect=AssertionError("relaunched while elevated")), \
             patch.object(sg.backend, "migrate_legacy_credentials", return_value=False) as migrate:
            self.assertEqual(sg.main(), 0)
        migrate.assert_called_once()

    def test_version_needs_no_elevation(self):
        code, calls = self._run(["--version"], elevated=False)
        self.assertEqual(code, 0)
        self.assertEqual(calls["relaunch"], 0)

    def test_gate_runs_before_any_secrets_or_task_mode(self):
        body = GUI[GUI.index("def main() -> int:"):]
        gate = body.index("if not _is_elevated():")
        for marker in ("if args.status:", "if args.manage_recorders:",
                       "if args.migrate_only:", "SetupWindow(config_path"):
            self.assertLess(gate, body.index(marker), marker)
        self.assertIn('"runas"', GUI)
        self.assertIn("IsUserAnAdmin", GUI)


if __name__ == "__main__":
    unittest.main(verbosity=2)
