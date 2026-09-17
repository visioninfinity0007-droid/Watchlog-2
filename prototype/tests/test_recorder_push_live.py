#!/usr/bin/env python3
"""
5.0 — recorder-push driven against REAL HTTP servers, not mocks.

WHY THIS FILE EXISTS. Every earlier test of configure_push replaced the driver's `_get`
with a dict. That proved the function's branching and nothing about whether the driver
can actually speak to a recorder: it never built a request, never went through digest
auth, never parsed a real response. So the feature shipped in 0.4.11 having never once
executed end to end -- and the first time it ran on real hardware it took the installer
down with a native crash.

These tests stand up an actual HTTP server that answers like a Dahua XVR and like a
Hikvision NVR, and assert on THE EXACT REQUESTS THE RECORDER RECEIVES. That is the part
that has to be right on the customer's box.

    pytest -q prototype/tests/test_recorder_push_live.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from drivers import build  # noqa: E402

BRIDGE = "https://watchlog-push.161.97.175.15.sslip.io/push/tok123"


class _Recorder:
    """A real HTTP server that answers like a recorder. Records every request."""

    def __init__(self, handler_cls):
        self.calls: list = []
        handler_cls.calls = self.calls
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def close(self):
        self.srv.shutdown()


def _dahua_handler(*, retains=True, reject: str | None = None, protocol="HTTPS"):
    class H(BaseHTTPRequestHandler):
        calls: list = []

        def log_message(self, *a):
            pass

        def do_GET(self):
            self.calls.append(self.path)
            if reject and reject in self.path:
                self.send_response(400); self.send_header("Content-Length", "0")
                self.end_headers(); return
            if "getConfig" in self.path and "AlarmServer" in self.path:
                if retains:
                    body = (b"table.AlarmServer.Enable=true\r\n"
                            b"table.AlarmServer.Address=watchlog-push.161.97.175.15.sslip.io\r\n"
                            b"table.AlarmServer.Port=443\r\n"
                            + f"table.AlarmServer.Protocol={protocol}\r\n".encode())
                else:
                    body = b"table.AlarmServer.Enable=false\r\ntable.AlarmServer.Address=\r\n"
            else:
                body = b"OK\r\n"
            self.send_response(200); self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
    return H


def _hik_handler(*, retains=True):
    XML = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<HttpHostNotification xmlns="http://www.hikvision.com/ver20/XMLSchema">'
           '<id>1</id><url>{url}</url><protocolType>HTTPS</protocolType>'
           '<ipAddress>{ip}</ipAddress><portNo>443</portNo>'
           '</HttpHostNotification>')

    class H(BaseHTTPRequestHandler):
        calls: list = []

        def log_message(self, *a):
            pass

        def _send(self, body: bytes):
            self.send_response(200); self.send_header("Content-Type", "application/xml")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)

        def do_PUT(self):
            n = int(self.headers.get("Content-Length", 0) or 0)
            self.calls.append("PUT " + self.path + " " + self.rfile.read(n).decode("utf-8", "replace"))
            self._send(b"<ResponseStatus><statusCode>1</statusCode></ResponseStatus>")

        def do_GET(self):
            self.calls.append("GET " + self.path)
            ip = "watchlog-push.161.97.175.15.sslip.io" if retains else "someone-else.net"
            self._send(XML.format(url="/push/tok123", ip=ip).encode())
    return H


class DahuaAgainstARealServerTests(unittest.TestCase):
    def _run(self, handler):
        rec = _Recorder(handler)
        self.addCleanup(rec.close)
        drv = build("dahua-cgi", rec.url, "admin", "pw", 5)
        return drv.configure_push(BRIDGE), rec.calls

    def test_it_verifies_against_a_recorder_that_accepts_the_config(self):
        out, _calls = self._run(_dahua_handler())
        self.assertTrue(out["applied"])
        self.assertTrue(out["verified"], out)

    def test_each_key_is_sent_as_its_own_request(self):
        """Dahua rejects an ENTIRE setConfig when any single key is unknown to that
        firmware, so batching five keys meant one unsupported key discarded all five."""
        _out, calls = self._run(_dahua_handler())
        sets = [c for c in calls if "setConfig" in c]
        self.assertEqual(5, len(sets), f"expected one request per key, got: {sets}")
        for c in sets:
            self.assertEqual(1, c.count("AlarmServer."), f"more than one key in {c}")

    def test_the_protocol_follows_the_bridge_scheme(self):
        """Hardcoding HTTP while computing port 443 told the recorder to open PLAINTEXT
        to a TLS port -- every alarm would die at the handshake."""
        _out, calls = self._run(_dahua_handler())
        self.assertTrue(any("AlarmServer.Protocol=HTTPS" in c for c in calls),
                        f"HTTPS bridge must configure HTTPS: {calls}")

    def test_the_token_path_reaches_the_recorder(self):
        _out, calls = self._run(_dahua_handler())
        joined = unquote(" ".join(calls))
        self.assertIn("/push/tok123", joined,
                      "without the token the recorder cannot be identified")

    def test_a_recorder_that_silently_ignores_the_write_is_not_verified(self):
        out, _calls = self._run(_dahua_handler(retains=False))
        self.assertTrue(out["applied"])
        self.assertFalse(out["verified"], "an ignored config must not read as success")

    def test_a_protocol_mismatch_is_not_verified(self):
        """Recorder kept HTTP for an https bridge: alarms would never be delivered."""
        out, _calls = self._run(_dahua_handler(protocol="HTTP"))
        self.assertFalse(out["verified"], out)

    def test_firmware_that_rejects_an_optional_key_still_succeeds(self):
        """UrlPath is the key most likely missing on entry-level firmware. Rejecting it
        must not lose the whole configuration."""
        out, calls = self._run(_dahua_handler(reject="UrlPath"))
        self.assertTrue(out["applied"])
        self.assertTrue(out["verified"], out)
        self.assertIn("firmware ignored", out["detail"])

    def test_firmware_that_rejects_a_required_key_fails_honestly(self):
        out, _calls = self._run(_dahua_handler(reject="AlarmServer.Enable"))
        self.assertFalse(out["applied"])
        self.assertFalse(out["verified"])

    def test_an_unreachable_recorder_reports_instead_of_raising(self):
        drv = build("dahua-cgi", "http://127.0.0.1:9", "admin", "pw", 2)
        try:
            out = drv.configure_push(BRIDGE)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"must not raise at the driver layer: {type(exc).__name__}")
        self.assertFalse(out["verified"])


class HikvisionAgainstARealServerTests(unittest.TestCase):
    def _run(self, handler):
        rec = _Recorder(handler)
        self.addCleanup(rec.close)
        drv = build("hikvision-isapi", rec.url, "admin", "pw", 5)
        return drv.configure_push(BRIDGE), rec.calls

    def test_it_verifies_against_a_recorder_that_accepts_the_config(self):
        """It used to return None, so a Hikvision push that worked was always reported
        as FAILED and the customer told PC-free reporting was unavailable."""
        out, _calls = self._run(_hik_handler())
        self.assertIsInstance(out, dict, "must return the shared contract, not None")
        self.assertTrue(out["applied"])
        self.assertTrue(out["verified"], out)

    def test_it_writes_then_reads_back(self):
        _out, calls = self._run(_hik_handler())
        self.assertTrue(any(c.startswith("PUT") for c in calls))
        self.assertTrue(any(c.startswith("GET") for c in calls),
                        "the recorder is the source of truth, not our request")

    def test_the_bridge_details_reach_the_recorder(self):
        _out, calls = self._run(_hik_handler())
        put = next(c for c in calls if c.startswith("PUT"))
        self.assertIn("/push/tok123", put)
        self.assertIn("HTTPS", put)
        self.assertIn("watchlog-push.161.97.175.15.sslip.io", put)

    def test_a_recorder_pointed_elsewhere_is_not_verified(self):
        out, _calls = self._run(_hik_handler(retains=False))
        self.assertFalse(out["verified"], out)


class OutOfProcessContainmentTests(unittest.TestCase):
    """5.0: the wizard delegates recorder configuration to a CHILD PROCESS.

    0.4.11 did it inline and a native crash inside it killed the whole installer --
    "WatchLog Setup has stopped working". A try/except cannot catch that. Only process
    isolation can, so these pin that the parent survives the worst cases."""

    def setUp(self):
        import setup_backend as sb
        self.sb = sb
        self.args = dict(cloud=None, state={"agent_id": "a", "agent_key": "k"},
                         recorder={"driver": "dahua-cgi", "url": "http://10.0.0.5",
                                   "vendor": "Dahua"},
                         public={"push_bridge_url": "https://push.example.io"},
                         username="u", password="p")

    def test_a_child_that_dies_without_reporting_does_not_fail_the_install(self):
        out = self.sb.provision_recorder_push(**self.args, _run=lambda: (-1, ""))
        self.assertFalse(out["configured"])
        self.assertIn("did not report back", out["detail"])

    def test_a_child_that_CRASHES_hard_does_not_fail_the_install(self):
        """A real segfault-style death: non-zero exit, partial output, no PUSH_JSON."""
        code, out = self._spawn("import sys; sys.stdout.write('starting\\n'); "
                                "sys.stdout.flush(); import os; os._exit(3)")
        res = self.sb.provision_recorder_push(**self.args, _run=lambda: (code, out))
        self.assertFalse(res["configured"])
        self.assertFalse(res["verified"])

    def test_a_verified_child_result_is_reported(self):
        payload = json.dumps({"configured": True, "verified": True, "detail": "ok"})
        out = self.sb.provision_recorder_push(
            **self.args, _run=lambda: (0, f"noise\nPUSH_JSON {payload}\n"))
        self.assertTrue(out["configured"])
        self.assertTrue(out["verified"])

    def test_malformed_child_output_is_survived(self):
        out = self.sb.provision_recorder_push(
            **self.args, _run=lambda: (0, "PUSH_JSON {not json"))
        self.assertFalse(out["configured"])

    def test_a_launcher_that_explodes_is_survived(self):
        def boom():
            raise OSError("cannot spawn")
        out = self.sb.provision_recorder_push(**self.args, _run=boom)
        self.assertFalse(out["configured"])
        self.assertIn("could not run", out["detail"])

    def test_no_bridge_configured_skips_without_touching_anything(self):
        called = []
        self.sb.provision_recorder_push(
            **{**self.args, "public": {}}, _run=lambda: called.append(1) or (0, ""))
        self.assertEqual([], called, "must not spawn anything when the feature is off")

    @staticmethod
    def _spawn(code: str):
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           timeout=30)
        return p.returncode, p.stdout


if __name__ == "__main__":
    unittest.main(verbosity=1)
