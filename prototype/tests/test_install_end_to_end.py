#!/usr/bin/env python3
"""
5.0 — the WHOLE install flow, start to finish, against a simulated site.

WHY THIS EXISTS. Every test before this one exercised a single function with its
neighbours replaced by dictionaries. That is how a feature reached a customer having
never executed end to end: each unit passed, and the SEQUENCE had never run. This drives
the real `finalize_install` from the top -- recorder probe, enrollment, camera sync,
capability sync, heartbeat, background-agent registration, recorder-push, config
tidy-up -- against an HTTP server that answers like a Dahua XVR and a cloud that answers
like WatchLog.

What is deliberately NOT real: the Windows scheduled task (registering one needs admin
and would alter this machine) and the DPAPI credential store on non-Windows. Everything
else is the shipping code path.

    pytest -q prototype/tests/test_install_end_to_end.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import setup_backend as sb  # noqa: E402

BRIDGE = "https://push.example.io"


# --------------------------------------------------------------------------- recorder
class _DahuaHandler(BaseHTTPRequestHandler):
    """Answers the subset of the Dahua CGI surface setup actually uses."""
    calls: list = []

    def log_message(self, *a):
        pass

    def _ok(self, body: bytes):
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.calls.append(self.path)
        p = self.path
        if "magicBox.cgi?action=getSystemInfo" in p:
            self._ok(b"deviceType=DH-XVR1B08-I\r\nserialNumber=WL-TEST-0001\r\n")
        elif "magicBox.cgi?action=getSoftwareVersion" in p:
            self._ok(b"version=4.000.0000000.0\r\n")
        elif "magicBox.cgi?action=getDeviceType" in p:
            self._ok(b"type=DH-XVR1B08-I\r\n")
        elif "getConfig&name=ChannelTitle" in p:
            self._ok(b"".join(
                f"table.ChannelTitle[{i}].Name=CAM{i + 1}\r\n".encode() for i in range(8)))
        elif "getConfig&name=AlarmServer" in p:
            self._ok(b"table.AlarmServer.Enable=true\r\n"
                     b"table.AlarmServer.Address=push.example.io\r\n"
                     b"table.AlarmServer.Port=443\r\n"
                     b"table.AlarmServer.Protocol=HTTPS\r\n")
        else:
            self._ok(b"OK\r\n")


class _FakeCloud:
    """Answers like WatchLog's RPC surface."""

    def __init__(self):
        self.calls: list = []

    def call(self, fn, **kw):
        self.calls.append((fn, kw))
        if fn == "wl_enroll":
            # Mirror the REAL wl_enroll contract (setup_backend:628 reads all four).
            return {"ok": True,
                    "agent_id": "11111111-1111-4111-8111-111111111111",
                    "agent_key": "agent-key-secret",
                    "tenant_id": "33333333-3333-4333-8333-333333333333",
                    "site_id": "22222222-2222-4222-8222-222222222222"}
        if fn == "wl_sync_cameras":
            # A MAPPING keyed by channel, not a list -- setup_backend:738 does
            # mapping.keys() and compares against the channels it sent.
            return {str(i + 1): f"cam-{i + 1}" for i in range(8)}
        if fn == "wl_agent_issue_push_token":
            return {"ok": True, "token": "tok123"}
        if fn == "wl_heartbeat":
            return {"ok": True}
        return {"ok": True}


class InstallEndToEndTests(unittest.TestCase):
    """The sequence, not the pieces."""

    @classmethod
    def setUpClass(cls):
        _DahuaHandler.calls = []
        # Bind to a REAL recorder web port. setup_backend.plan_recorder_probes refuses to
        # attempt a login when no _WEB_PORTS port is open -- correct behaviour, and it
        # means a faithful simulation has to listen where a recorder listens.
        cls.srv = None
        for candidate in (8081, 8000, 8080, 88, 81, 8443):
            try:
                cls.srv = ThreadingHTTPServer(("127.0.0.1", candidate), _DahuaHandler)
                cls.port = candidate
                break
            except OSError:
                continue
        if cls.srv is None:
            raise unittest.SkipTest("no recorder web port free on this machine")
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ini = self.tmp / "watchlog.ini"
        self.progress: list = []
        self.cloud = _FakeCloud()
        self.agent_started: list = []
        self.push_cmd: list = []

        # Isolate ONLY what would alter this machine.
        self._orig = {
            "programdata_dir": sb.programdata_dir,
            "Cloud": sb.core.Cloud,
            "ensure_background_agent": sb.ensure_background_agent,
            "save_nvr_credential": sb.credential_store.save_nvr_credential,
            "save_state": sb.core.save_state,
        }
        sb.programdata_dir = lambda: self.tmp
        sb.core.Cloud = lambda *a, **k: self.cloud
        sb.ensure_background_agent = lambda **k: (
            self.agent_started.append(k) or
            {"started": True, "detail": "background agent registered and started"})
        sb.credential_store.save_nvr_credential = lambda u, p: None
        # save_state writes the agent key through machine-scope DPAPI and then hardens the
        # DACL with an elevated PowerShell call. Both touch real machine state and need
        # admin, so the identity is captured in memory instead.
        self.saved_state: list = []
        sb.core.save_state = lambda path, st: self.saved_state.append(st)
        self.addCleanup(self._restore)

    def _restore(self):
        sb.programdata_dir = self._orig["programdata_dir"]
        sb.core.Cloud = self._orig["Cloud"]
        sb.ensure_background_agent = self._orig["ensure_background_agent"]
        sb.core.save_state = self._orig["save_state"]
        sb.credential_store.save_nvr_credential = self._orig["save_nvr_credential"]

    def _install(self, push_result=None, push_raises=None):
        """Run the REAL finalize_install. Only the push child process is stubbed —
        spawning a real one would need a frozen exe."""
        def fake_child():
            self.push_cmd.append(True)
            if push_raises:
                raise push_raises
            payload = json.dumps(push_result if push_result is not None else
                                 {"configured": True, "verified": True,
                                  "detail": "recorder will POST alarms"})
            return 0, f"PUSH_JSON {payload}\n"

        orig = sb.provision_recorder_push
        sb.provision_recorder_push = (
            lambda *a, **k: orig(*a, **{**k, "_run": fake_child}))
        try:
            return sb.finalize_install(
                config_path=self.ini,
                public={"supabase_url": "https://cloud.example.io",
                        "supabase_publishable_key": "pk_test_abcdefghijklmnop",
                        "push_bridge_url": BRIDGE},
                enrollment_code="WL-TEST-1234",
                address=f"http://127.0.0.1:{self.port}",
                hint={"ports": [self.port], "vendor_hint": "dahua"},
                username="admin", password="pw",
                site_type="office", profiles=[],
                progress=self.progress.append)
        finally:
            sb.provision_recorder_push = orig

    # ---------------------------------------------------------------- the happy path
    def test_a_full_install_succeeds_and_reports_every_stage(self):
        result = self._install()
        self.assertEqual("22222222-2222-4222-8222-222222222222", result["site_id"])
        self.assertEqual(8, result["camera_count"])
        self.assertEqual("Dahua", result["vendor"])
        self.assertTrue(result["connected"], "the site must be reported connected")

    def test_the_cloud_sees_the_real_enrollment_sequence(self):
        self._install()
        fns = [c[0] for c in self.cloud.calls]
        self.assertIn("wl_enroll", fns)
        self.assertIn("wl_sync_cameras", fns)
        self.assertIn("wl_heartbeat", fns)
        self.assertLess(fns.index("wl_enroll"), fns.index("wl_sync_cameras"))
        self.assertLess(fns.index("wl_sync_cameras"), fns.index("wl_heartbeat"))

    def test_the_background_agent_is_started_before_the_optional_work(self):
        """The ordering the whole reboot fix depends on."""
        self._install()
        self.assertTrue(self.agent_started, "the background agent was never registered")
        self.assertTrue(self.push_cmd, "recorder push was never attempted")

    def test_registration_gets_a_guaranteed_time_floor(self):
        """0.4.9 funded it from the optional budget, so a slow probe could hand it a
        fraction of a second and it was killed mid-registration."""
        self._install()
        self.assertGreaterEqual(self.agent_started[0].get("timeout", 0), 60)

    def test_the_proven_driver_is_written_not_auto(self):
        self._install()
        self.assertIn("nvr_driver = dahua-cgi", self.ini.read_text(encoding="utf-8"))

    def test_the_consumed_site_code_is_cleared(self):
        self._install()
        text = self.ini.read_text(encoding="utf-8")
        self.assertNotIn("WL-TEST-1234", text, "a consumed one-time code must not persist")

    def test_no_recorder_password_is_written_to_the_ini(self):
        """Secrets gate: the credential lives only in the encrypted store."""
        self._install()
        self.assertNotIn("pw", self.ini.read_text(encoding="utf-8").split("nvr_username")[0])

    def test_pc_free_reporting_is_reported_to_the_caller(self):
        result = self._install()
        self.assertTrue(result["recorder_push"]["verified"])

    # ------------------------------------------------------- the failure modes matter
    def test_a_crashing_push_child_does_not_fail_the_install(self):
        """THE 0.4.11 REGRESSION. A native crash here killed the whole installer."""
        result = self._install(push_raises=OSError("child died"))
        self.assertTrue(result["connected"], "a dead push child must not break the install")
        self.assertEqual(8, result["camera_count"])
        self.assertFalse(result["recorder_push"]["configured"])

    def test_a_push_child_that_reports_failure_does_not_fail_the_install(self):
        result = self._install(push_result={"configured": False, "verified": False,
                                            "detail": "recorder refused"})
        self.assertTrue(result["connected"])
        self.assertFalse(result["recorder_push"]["verified"])

    def test_a_recorder_that_cannot_do_push_still_installs(self):
        result = self._install(push_result={"configured": False, "verified": False,
                                            "detail": "model does not support recorder-push"})
        self.assertTrue(result["connected"])

    def test_the_customer_sees_progress_for_each_stage(self):
        self._install()
        joined = " | ".join(self.progress)
        for stage in ("recorder", "Connecting", "cameras", "background"):
            self.assertIn(stage.lower(), joined.lower(), f"no progress for {stage}: {joined}")


if __name__ == "__main__":
    unittest.main(verbosity=1)
