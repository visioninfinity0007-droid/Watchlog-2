#!/usr/bin/env python3
"""0.4.4 installer wave P2/P3 — Site Status controller (the panel's action bindings).

The Qt window is a thin view; this proves the controller that backs every button by driving a fake
agent CLI: snapshot parse, acceptance verdict mapping, update check/apply/rollback messaging,
support-bundle export, and agent restart — no subprocess, recorder or cloud.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

from status_controller import StatusController  # noqa: E402


def controller(responses):
    """responses: {first-arg: (exit_code, stdout)}."""
    def run_agent(args, timeout=None):
        return responses.get(args[0], (1, ""))
    return StatusController(run_agent=run_agent)


def tag(name, obj):
    return f"{name} " + json.dumps(obj)


class Snapshot(unittest.TestCase):
    def test_parses_status_json(self):
        snap = {"schema": "watchlog.site_status.v1", "recorder": {"connection": "connected"},
                "cameras": {"summary": {"headline": "3/3 monitored"}}, "archive": {"archive_access": "verified"}}
        c = controller({"--status-json": (0, "noise\n" + tag("STATUS_JSON", snap))})
        r = c.snapshot()
        self.assertTrue(r["ok"])
        self.assertEqual(r["status"]["cameras"]["summary"]["headline"], "3/3 monitored")

    def test_focused_refreshers(self):
        snap = {"recorder": {"connection": "connected"}, "cameras": {"x": 1},
                "recording": {"y": 1}, "archive": {"archive_access": "empty"}}
        c = controller({"--status-json": (0, tag("STATUS_JSON", snap))})
        self.assertTrue(c.test_recorder()["ok"])
        self.assertEqual(c.rediscover_cameras()["cameras"], {"x": 1})

    def test_recheck_archive_drives_dedicated_fresh_proof(self):
        rep = {"state": "ARCHIVE AVAILABLE — FRAME DECODE UNVERIFIED", "frame_decoded": False,
               "diagnostics": {"kind": "dav", "media_size": 44, "decoded": False}}
        c = controller({"--recheck-archive-json": (0, tag("ARCHIVE_JSON", rep))})
        r = c.recheck_archive()
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"], "ARCHIVE AVAILABLE — FRAME DECODE UNVERIFIED")
        self.assertFalse(r["frame_decoded"])
        self.assertEqual(r["diagnostics"]["kind"], "dav")


class Acceptance(unittest.TestCase):
    def test_ready(self):
        rep = {"ready": True, "checks": [{"key": "recorder", "label": "Recorder reachable",
                                          "hard": True, "status": "pass"}]}
        c = controller({"--accept": (0, tag("ACCEPTANCE_JSON", rep))})
        r = c.run_acceptance()
        self.assertEqual(r["verdict"], "WATCHLOG READY")
        self.assertTrue(r["ready"])
        self.assertEqual(r["failed"], [])
        self.assertEqual(r["warnings"], [])

    def test_ready_with_warnings(self):
        rep = {"ready": True, "checks": [{"key": "ai", "label": "On-site AI filter", "hard": False,
                                          "status": "warn"}]}
        c = controller({"--accept": (0, tag("ACCEPTANCE_JSON", rep))})
        r = c.run_acceptance()
        self.assertEqual(r["verdict"], "WATCHLOG READY WITH WARNINGS")
        self.assertEqual(r["warnings"], ["On-site AI filter"])

    def test_blocked_lists_failed_checks(self):
        rep = {"ready": False, "checks": [{"key": "security", "label": "No plaintext password",
                                           "hard": True, "status": "blocked"}]}
        c = controller({"--accept": (2, tag("ACCEPTANCE_JSON", rep))})
        r = c.run_acceptance()
        self.assertEqual(r["verdict"], "SETUP INCOMPLETE — ACTION REQUIRED")
        self.assertFalse(r["ready"])
        self.assertEqual(r["failed"], ["No plaintext password"])

    # --- fail-closed (P1.1): could-not-verify must NEVER be Ready ---
    def test_no_report_fails_closed_even_if_exit_zero(self):
        c = controller({"--accept": (0, "some noise but no ACCEPTANCE_JSON line")})
        r = c.run_acceptance()
        self.assertFalse(r["ready"])
        self.assertFalse(r["verified"])
        self.assertEqual(r["verdict"], "SETUP INCOMPLETE — WATCHLOG COULD NOT VERIFY THIS INSTALLATION")

    def test_malformed_report_fails_closed(self):
        c = controller({"--accept": (0, "ACCEPTANCE_JSON {not valid json")})
        r = c.run_acceptance()
        self.assertFalse(r["ready"])
        self.assertFalse(r["verified"])

    def test_launch_exception_fails_closed(self):
        def boom(args, timeout=None):
            raise RuntimeError("agent binary not found")
        c = StatusController(run_agent=boom)
        r = c.run_acceptance()
        self.assertFalse(r["ready"])
        self.assertEqual(r["exit"], -1)
        self.assertIn("COULD NOT VERIFY", r["verdict"])


class Updates(unittest.TestCase):
    def test_check_update_available(self):
        plan = {"action": "update", "target": "0.4.5"}
        c = controller({"--check-update": (0, tag("UPDATE_JSON", plan))})
        r = c.check_update()
        self.assertEqual(r["action"], "update")
        self.assertIn("0.4.5", r["headline"])

    def test_check_up_to_date(self):
        c = controller({"--check-update": (0, tag("UPDATE_JSON", {"action": "up-to-date"}))})
        self.assertEqual(c.check_update()["headline"], "WatchLog is up to date.")

    def test_apply_success(self):
        res = {"ok": True, "stage": "commit", "rolled_back": False, "detail": "0.4.5"}
        c = controller({"--update": (0, tag("UPDATE_APPLY_JSON", res))})
        r = c.apply_update()
        self.assertTrue(r["ok"])
        self.assertIn("updated", r["message"].lower())

    def test_apply_rolled_back_message(self):
        res = {"ok": False, "stage": "commit", "rolled_back": True, "detail": "verify failed"}
        c = controller({"--update": (2, tag("UPDATE_APPLY_JSON", res))})
        r = c.apply_update()
        self.assertFalse(r["ok"])
        self.assertTrue(r["rolled_back"])
        self.assertIn("restored the previous version", r["message"])


class SupportAndRestart(unittest.TestCase):
    def test_support_bundle_export_copies(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "watchlog-support-x.zip"
            src.write_bytes(b"PK\x03\x04zipdata")
            dest = Path(d) / "saved" / "bundle.zip"
            dest.parent.mkdir()
            c = controller({"--support-bundle": (0, f"support bundle written: {src}\n")})
            r = c.export_support_bundle(dest_path=dest)
            self.assertTrue(r["ok"])
            self.assertEqual(r["path"], str(dest))
            self.assertTrue(dest.exists())

    def test_support_bundle_failure(self):
        c = controller({"--support-bundle": (1, "error")})
        self.assertFalse(c.export_support_bundle()["ok"])

    def test_restart_agent_injected(self):
        c = controller({})
        self.assertTrue(c.restart_agent(_runner=lambda: True)["ok"])
        self.assertFalse(c.restart_agent(_runner=lambda: False)["ok"])
        self.assertFalse(c.restart_agent(_runner=lambda: (_ for _ in ()).throw(RuntimeError("x")))["ok"])

    def test_open_logs_returns_path(self):
        c = controller({})
        self.assertIn("WatchLog", c.open_logs()["path"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
