#!/usr/bin/env python3
"""Tests for the standalone recorder hardware-validation harness. Verifies the honest capability
classification (field-proven / implemented-unverified / unsupported / unknown), that an
unreachable recorder degrades gracefully (never crashes, never over-claims), and that the tool
REFUSES to run without explicit authorization."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "agent"))
import hardware_probe as hp          # noqa: E402
from drivers.base import NvrDriver   # noqa: E402


class FakeDriver(NvrDriver):
    name = "fake"

    def __init__(self):                                  # skip base __init__ (no live transport)
        pass

    def get_snapshot(self, channel):
        return b"\xff\xd8jpeg"

    def list_channels(self):
        return [1, 2, 3]

    def recording_status(self):
        raise RuntimeError("recorder refused the record-config query")


class HardwareProbeTests(unittest.TestCase):
    def test_capability_classification(self):
        d = FakeDriver()
        got = hp._cap(d, "get_snapshot", lambda x: (bool(x.get_snapshot("1")), "captured", None))
        self.assertEqual(hp.FIELD, got["status"])                       # worked here
        inv = hp._cap(d, "list_channels", lambda x: (len(x.list_channels()) > 0, "enumerated", None))
        self.assertEqual(hp.FIELD, inv["status"])
        # implemented but returned nothing -> implemented-unverified (not over-claimed)
        empty = hp._cap(d, "get_snapshot", lambda x: (False, "no image", None))
        self.assertEqual(hp.IMPL, empty["status"])
        # implemented but raised -> unknown (inconclusive, honest)
        unk = hp._cap(d, "recording_status", lambda x: (x.recording_status() is not None, "", None))
        self.assertEqual(hp.UNKNOWN, unk["status"])
        # not overridden (base no-op) -> unsupported, without ever calling it
        unsup = hp._cap(d, "capabilities", lambda x: (True, "should not run", None))
        self.assertEqual(hp.UNSUP, unsup["status"])

    def test_unreachable_recorder_degrades_gracefully(self):
        r = hp.probe_recorder("http://127.0.0.1:59999", "u", "p", driver_name="dahua-cgi", channel="1")
        self.assertIn(r["connection"]["status"], (hp.UNKNOWN, "auth-required"))
        self.assertIn("capabilities", r)                               # structure always present
        text = hp.render(r)
        self.assertIn("legend", text)                                  # honest legend always shown

    def test_refuses_without_authorization(self):
        self.assertEqual(2, hp.main(["--url", "http://10.0.0.1"]))     # must not probe unapproved


if __name__ == "__main__":
    unittest.main(verbosity=1)
