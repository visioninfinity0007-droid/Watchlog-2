#!/usr/bin/env python3
"""0.4.4 §18 — support-bundle export (no cloud / recorder / database).

The bundle helps support without ever leaking a secret. These tests plant the recorder password,
the agent key, the enrollment code and a secret-shaped log line into every input and assert NONE
of them appear in ANY emitted file — the Secrets Gate, proven, not assumed. Config is allowlisted
(deny by default) so a future secret-shaped key cannot silently ride along.
"""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

import support_bundle as sb        # noqa: E402
import watchlog_agent as wa        # noqa: E402

PW = "R3corderPassw0rd!"
KEY = "agentkey-DEADBEEFdeadbeef"
CODE = "ENROLL-7Q2X-SECRET"

SECTION = {
    "supabase_url": "https://proj.supabase.co",
    "supabase_publishable_key": "pk_public_safe",
    "nvr_url": "http://10.0.0.7",
    "nvr_driver": "dahua",
    "nvr_username": "admin",              # credential half — must be dropped
    "nvr_password": PW,                   # secret — must be dropped
    "enrollment_code": CODE,              # sensitive — must be dropped
    "recovery_enabled": "true",
    "some_future_secret_token": "zzz-should-not-appear",
}
STATE = {"agent_id": "11111111-1111-1111-1111-111111111111",
         "site_id": "22222222-2222-2222-2222-222222222222",
         "tenant_id": "33333333-3333-3333-3333-333333333333",
         "agent_key": KEY}
LOG = "start ok\nrecorder password=%s used\nchannels listed ok\n" % PW


class RedactConfig(unittest.TestCase):
    def test_keeps_safe_drops_secret(self):
        red = sb.redact_config(SECTION)
        self.assertEqual(red["nvr_url"], "http://10.0.0.7")
        self.assertEqual(red["supabase_publishable_key"], "pk_public_safe")
        self.assertEqual(red["recovery_enabled"], "true")
        for banned in ("nvr_password", "enrollment_code", "nvr_username",
                       "some_future_secret_token"):
            self.assertNotIn(banned, red)

    def test_looks_secret(self):
        self.assertTrue(sb._looks_secret("nvr_password = x"))
        self.assertTrue(sb._looks_secret("AGENT_KEY leaked"))
        self.assertFalse(sb._looks_secret("channels listed ok"))


class Collect(unittest.TestCase):
    def setUp(self):
        self.files = sb.collect(SECTION, state=STATE, setup_log=LOG,
                                build_meta={"version": "0.4.4", "build_sha": "abc1234"},
                                spool_count=5)
        self.blob = "\n".join(self.files.values())

    def test_no_secret_anywhere(self):
        for secret in (PW, KEY, CODE, "some_future_secret_token", "zzz-should-not-appear"):
            self.assertNotIn(secret, self.blob, f"secret leaked into bundle: {secret}")

    def test_includes_useful_nonsecret_facts(self):
        self.assertIn("abc1234", self.files["versions.json"])
        self.assertIn(STATE["agent_id"], self.files["identity.json"])
        self.assertIn("http://10.0.0.7", self.files["config_redacted.ini"])
        self.assertIn("5", self.files["local_state.json"])

    def test_agent_key_never_in_identity(self):
        self.assertNotIn("agent_key", self.files["identity.json"])
        self.assertNotIn(KEY, self.files["identity.json"])

    def test_secret_shaped_log_line_dropped(self):
        self.assertNotIn(PW, self.files["setup_log.txt"])
        self.assertIn("channels listed ok", self.files["setup_log.txt"])


class WriteZip(unittest.TestCase):
    def test_zip_is_valid_and_secret_free(self):
        files = sb.collect(SECTION, state=STATE, setup_log=LOG,
                           build_meta={"version": "0.4.4"}, spool_count=0)
        with tempfile.TemporaryDirectory() as d:
            path = sb.write_zip(d, files)
            self.assertTrue(path.exists() and path.suffix == ".zip")
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
                self.assertIn("config_redacted.ini", names)
                self.assertIn("versions.json", names)
                whole = "\n".join(z.read(n).decode("utf-8", "replace") for n in names)
        for secret in (PW, KEY, CODE):
            self.assertNotIn(secret, whole)


class CmdSupportBundle(unittest.TestCase):
    def test_end_to_end_writes_secret_free_zip(self):
        cfg = SimpleNamespace(state_path=Path("/nonexistent/s.json"),
                              spool_path=Path("/nonexistent/spool.db"), spool_max_rows=0)
        with tempfile.TemporaryDirectory() as d:
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = wa.cmd_support_bundle(cfg, dest_dir=d, _section=SECTION, _state=STATE,
                                             _setup_log=LOG, _spool_count=4)
            self.assertEqual(code, 0)
            zips = list(Path(d).glob("watchlog-support-*.zip"))
            self.assertEqual(len(zips), 1)
            with zipfile.ZipFile(zips[0]) as z:
                whole = "\n".join(z.read(n).decode("utf-8", "replace") for n in z.namelist())
        for secret in (PW, KEY, CODE):
            self.assertNotIn(secret, whole)
        self.assertIn(STATE["site_id"], whole)


if __name__ == "__main__":
    unittest.main(verbosity=2)
