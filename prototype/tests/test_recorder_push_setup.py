#!/usr/bin/env python3
"""
Recorder-push setup: pointing the RECORDER at WatchLog so a site keeps reporting
with no PC running (the "PC-free" path from 0013_recorder_push.sql).

The dangerous failure here is not "it did not work" -- it is "it did not work but
we believed it did". A site that thinks it is covered and is not is worse than a
site we know needs an agent. So configure_push verifies by reading the config
back off the recorder and reports verified=False when the unit ignored the write.

    pytest -q prototype/tests/test_recorder_push_setup.py
"""

from __future__ import annotations

import io
import json
import contextlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

try:
    import drivers.dahua as dd
    from drivers.base import DriverError, NvrAuthFailed
except Exception as _exc:  # noqa: BLE001
    dd = None
    _IMPORT_ERR = _exc


PUSH_URL = "https://watchlog-push.example.io/push/abc123token"


def fake_dahua(responses):
    """A Dahua driver whose HTTP layer is replaced by a scripted dict.

    responses maps a substring of the request path -> response text, or an
    Exception instance to raise.
    """
    drv = dd.DahuaDriver.__new__(dd.DahuaDriver)
    drv.calls = []

    def _get(path, **kw):
        drv.calls.append(path)
        for needle, out in responses.items():
            if needle in path:
                if isinstance(out, Exception):
                    raise out
                return out
        return ""

    drv._get = _get
    return drv


@unittest.skipIf(dd is None, "dahua driver not importable here")
class DahuaConfigurePushTests(unittest.TestCase):
    def test_success_is_only_claimed_after_reading_the_config_back(self):
        drv = fake_dahua({
            "action=setConfig": "OK",
            "getConfig&name=AlarmServer":
                "table.AlarmServer.Enable=true\r\n"
                "table.AlarmServer.Address=watchlog-push.example.io\r\n"
                "table.AlarmServer.Port=443\r\n",
        })
        out = drv.configure_push(PUSH_URL)
        self.assertTrue(out["applied"])
        self.assertTrue(out["verified"])
        self.assertIn("watchlog-push.example.io", out["detail"])
        self.assertTrue(any("action=setConfig" in c for c in drv.calls))
        self.assertTrue(any("getConfig&name=AlarmServer" in c for c in drv.calls),
                        "must read the config back rather than trust the write")

    def test_a_recorder_that_silently_ignores_the_write_is_reported_unverified(self):
        """The XVR accepts the setConfig with 200 but keeps its old config."""
        drv = fake_dahua({
            "action=setConfig": "OK",
            "getConfig&name=AlarmServer":
                "table.AlarmServer.Enable=false\r\ntable.AlarmServer.Address=\r\n",
        })
        out = drv.configure_push(PUSH_URL)
        self.assertTrue(out["applied"])
        self.assertFalse(out["verified"], "silently-ignored config must NOT read as success")
        self.assertIn("agent", out["detail"].lower())

    def test_a_recorder_pointed_somewhere_else_is_not_verified(self):
        drv = fake_dahua({
            "action=setConfig": "OK",
            "getConfig&name=AlarmServer":
                "table.AlarmServer.Enable=true\r\n"
                "table.AlarmServer.Address=someone-elses-server.net\r\n",
        })
        self.assertFalse(drv.configure_push(PUSH_URL)["verified"])

    def test_unsupported_recorder_reports_instead_of_raising(self):
        """An old XVR with no alarm-server config is a normal answer, not a crash."""
        drv = fake_dahua({"action=setConfig": DriverError("HTTP 400 Bad Request")})
        out = drv.configure_push(PUSH_URL)
        self.assertFalse(out["applied"])
        self.assertFalse(out["verified"])
        self.assertIn("rejected", out["detail"].lower())

    def test_read_back_failure_does_not_claim_verification(self):
        drv = fake_dahua({
            "action=setConfig": "OK",
            "getConfig&name=AlarmServer": DriverError("HTTP 500"),
        })
        out = drv.configure_push(PUSH_URL)
        self.assertTrue(out["applied"])
        self.assertFalse(out["verified"])

    def test_bad_credentials_still_surface_as_an_auth_fault(self):
        """Auth failure must not be flattened into 'this model is unsupported'."""
        drv = fake_dahua({"action=setConfig": NvrAuthFailed("rejected the password")})
        with self.assertRaises(NvrAuthFailed):
            drv.configure_push(PUSH_URL)

    def test_a_url_with_no_host_is_refused_before_touching_the_recorder(self):
        drv = fake_dahua({})
        out = drv.configure_push("/push/abc123")
        self.assertFalse(out["applied"])
        self.assertEqual([], drv.calls, "must not write a nonsense config to the recorder")

    def test_the_site_token_path_is_sent_to_the_recorder(self):
        drv = fake_dahua({
            "action=setConfig": "OK",
            "getConfig&name=AlarmServer":
                "table.AlarmServer.Enable=true\r\n"
                "table.AlarmServer.Address=watchlog-push.example.io\r\n",
        })
        drv.configure_push(PUSH_URL)
        written = " ".join(c for c in drv.calls if "setConfig" in c)
        self.assertIn("abc123token", written,
                      "without the token in the path the recorder cannot be identified")
        self.assertIn("AlarmServer.Enable=true", written)



class ConfigurePushCommandTests(unittest.TestCase):
    """5.0: this logic MOVED from setup_backend into the agent's --configure-push command,
    which the wizard now runs as a CHILD PROCESS. The coverage moves with it.

    Why it moved: 0.4.11 configured the recorder inline and, the first time the feature
    was enabled on real hardware, a native crash inside it killed the whole installer.
    A try/except cannot catch that; only process isolation can. Containment itself is
    covered in test_recorder_push_live.OutOfProcessContainmentTests."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "agent"))
        import watchlog_agent as wa
        self.wa = wa
        self.cfg = type("Cfg", (), {
            "push_bridge_url": "https://push.example.io",
            "state_path": Path("nope.json"),
            "supabase_url": "https://x", "publishable_key": "k"})()
        self.state = {"agent_id": "a-1", "agent_key": "k-1"}

    def _run(self, **kw):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = self.wa.cmd_configure_push(self.cfg, **kw)
        out = buf.getvalue()
        payload = {}
        for line in out.splitlines():
            if line.startswith("PUSH_JSON "):
                payload = json.loads(line[len("PUSH_JSON "):])
        return rc, payload

    def _cloud(self, token="tok123", raises=None):
        outer = self

        class Cloud:
            def call(self, fn, **kw):
                outer.called = (fn, kw)
                if raises:
                    raise raises
                return {"ok": True, "token": token}
        return Cloud()

    def _driver(self, result):
        outer = self

        class Drv:
            def configure_push(self, url):
                outer.push_url = url
                return result
        return Drv()

    def test_verified_push_is_reported(self):
        rc, out = self._run(_state=self.state, _cloud_factory=lambda: self._cloud(),
                            _open_driver=lambda c: self._driver(
                                {"applied": True, "verified": True, "detail": "ok"}))
        self.assertEqual(0, rc)
        self.assertTrue(out["configured"])
        self.assertTrue(out["verified"])
        self.assertEqual("https://push.example.io/push/tok123", self.push_url)

    def test_token_is_requested_for_this_agent_only(self):
        self._run(_state=self.state, _cloud_factory=lambda: self._cloud(),
                  _open_driver=lambda c: self._driver({"applied": True, "verified": True}))
        fn, kw = self.called
        self.assertEqual("wl_agent_issue_push_token", fn)
        self.assertEqual("a-1", kw["p_agent_id"])
        self.assertNotIn("p_site_id", kw, "there must be no site to tamper with")

    def test_a_recorder_that_ignored_the_config_is_not_reported_verified(self):
        rc, out = self._run(_state=self.state, _cloud_factory=lambda: self._cloud(),
                            _open_driver=lambda c: self._driver(
                                {"applied": True, "verified": False, "detail": "did not retain"}))
        self.assertNotEqual(0, rc, "an unverified push must not exit success")
        self.assertTrue(out["configured"])
        self.assertFalse(out["verified"])

    def test_unsupported_recorder_is_a_normal_answer_not_a_failure(self):
        class NoPush:
            pass
        rc, out = self._run(_state=self.state, _cloud_factory=lambda: self._cloud(),
                            _open_driver=lambda c: NoPush())
        self.assertFalse(out["configured"])
        self.assertIn("does not support", out["detail"])

    def test_missing_token_is_not_treated_as_success(self):
        class Cloud:
            def call(self, fn, **kw):
                return {"ok": True}
        rc, out = self._run(_state=self.state, _cloud_factory=lambda: Cloud(),
                            _open_driver=lambda c: self._driver({"applied": True}))
        self.assertFalse(out["configured"])
        self.assertIn("did not issue", out["detail"])

    def test_cloud_failure_is_reported_never_raised(self):
        rc, out = self._run(_state=self.state,
                            _cloud_factory=lambda: self._cloud(raises=RuntimeError("down")),
                            _open_driver=lambda c: self._driver({"applied": True}))
        self.assertFalse(out["configured"])

    def test_driver_explosion_is_reported_never_raised(self):
        def boom(_cfg):
            raise OSError("recorder vanished")
        rc, out = self._run(_state=self.state, _cloud_factory=lambda: self._cloud(),
                            _open_driver=boom)
        self.assertFalse(out["configured"])

    def test_an_unenrolled_site_is_refused(self):
        rc, out = self._run(_state=None)
        self.assertFalse(out["configured"])
        self.assertIn("not enrolled", out["detail"])

    def test_no_push_bridge_configured_skips_quietly(self):
        self.cfg.push_bridge_url = ""
        rc, out = self._run(_state=self.state)
        self.assertFalse(out["configured"])
        self.assertIn("no push bridge", out["detail"])

    def test_it_always_emits_a_machine_readable_line(self):
        """The parent reads PUSH_JSON. Every exit path must print one, or a perfectly
        good install reports 'did not report back'."""
        for kwargs in ({"_state": None},
                       {"_state": self.state, "_cloud_factory": lambda: self._cloud(),
                        "_open_driver": lambda c: self._driver({"applied": True, "verified": True})}):
            _rc, out = self._run(**kwargs)
            self.assertIsInstance(out, dict)
            self.assertIn("detail", out)


if __name__ == "__main__":
    unittest.main(verbosity=1)
