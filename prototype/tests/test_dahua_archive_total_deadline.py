#!/usr/bin/env python3
"""MNVR-034: the Dahua clip download has a total time budget and a (connect, read) timeout.

Before the fix loadfile used one scalar 90 s timeout for connect AND every socket read, and the
download loop had no clock check: a recorder that kept sending a little data every minute held
the footage worker (and every request queued behind it) until the 32 MiB cap. Hikvision got a
total budget in 5.0.27; Dahua did not.

Field-only (IMPLEMENTED_UNVERIFIED): whether real Dahua firmware ever trickles loadfile data, and
how long a real DH-XVR1B08-I takes to send the first byte.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dahua_fake_recorder import DHAV, FakeDahua, FakeRecorder, continuous_files, local, pinned_datetime  # noqa: E402
import dahua_archive as da  # noqa: E402
from drivers.base import DriverError  # noqa: E402

PC_NOW = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)
START = datetime(2026, 10, 4, 7, 59, 50, tzinfo=timezone.utc)
END = START + timedelta(seconds=30)
FILES = continuous_files(local("2026-10-04 12:00:00"), local("2026-10-04 14:00:00"))
BUDGET = getattr(da, "CLIP_TOTAL_SECONDS", 90)


class FakeMonotonic:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class TotalDeadline(unittest.TestCase):
    def setUp(self):
        self.clock = FakeMonotonic()
        self.rec = FakeRecorder(PC_NOW, files=FILES)
        self.drv = FakeDahua(self.rec)
        for patcher in (mock.patch.object(da, "datetime", pinned_datetime(PC_NOW)),
                        mock.patch("time.monotonic", self.clock)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def advance_on(self, action, seconds):
        def hook(_cgi, params):
            if params.get("action") == action:
                self.clock.now += seconds
        self.rec.on_call = hook

    def test_loadfile_uses_a_connect_read_timeout_tuple(self):
        self.assertTrue(da.get_clip(self.drv, "1", START, END).startswith(b"DHAV"))
        timeout = self.rec.calls_to("loadfile.cgi")[0][2]
        self.assertIsInstance(timeout, tuple, "loadfile must not use one scalar for connect and read")
        connect, read = timeout
        self.assertLessEqual(connect, 10)
        self.assertLessEqual(read, BUDGET)

    def test_trickling_download_is_cut_at_the_total_budget(self):
        consumed = []

        def trickle():
            for _ in range(10_000):                 # bounded so a regression cannot hang the suite
                self.clock.now += 60                # a little data every minute: never a read timeout
                consumed.append(1)
                yield DHAV if not consumed[1:] else b"\x00" * 16

        self.rec.clip_chunks = trickle
        with self.assertRaises(DriverError):
            da.get_clip(self.drv, "1", START, END)
        self.assertLessEqual(len(consumed), 3, "download kept reading long past the total budget")
        self.assertTrue(self.rec.responses[-1].closed, "streamed response must still be closed")

    def test_slow_search_leaves_no_budget_for_a_download(self):
        self.advance_on("findNextFile", BUDGET + 1)
        with self.assertRaises(DriverError):
            da.get_clip(self.drv, "1", START, END)
        self.assertEqual(self.rec.calls_to("loadfile.cgi"), [], "download started with no budget left")

    def test_read_timeout_never_outlives_the_remaining_budget(self):
        self.advance_on("findNextFile", BUDGET - 10)
        da.get_clip(self.drv, "1", START, END)
        _connect, read = self.rec.calls_to("loadfile.cgi")[0][2]
        self.assertLessEqual(read, 10)

    def test_read_timeout_mid_download_is_an_error_without_the_lan_address(self):
        # The shorter read timeout makes a stalled download end sooner; that failure must stay a
        # recorder error whose text (cloud-visible via incident_evidence) names no LAN host.
        def stall():
            yield DHAV
            raise requests.exceptions.ConnectionError(
                "HTTPConnectionPool(host='192.168.1.108', port=80): Read timed out.")

        self.rec.clip_chunks = stall
        with self.assertRaises(DriverError) as caught:
            da.get_clip(self.drv, "1", START, END)
        self.assertNotIn("192.168.", str(caught.exception))
        self.assertTrue(self.rec.responses[-1].closed)

    def test_budget_failure_is_an_error_not_unsupported(self):
        # incident_evidence turns a None/empty result into a terminal "unsupported"; running out of
        # time must surface as an error instead.
        self.advance_on("findNextFile", BUDGET + 1)
        try:
            result = da.get_clip(self.drv, "1", START, END)
        except DriverError:
            return
        self.fail(f"budget exhaustion returned {type(result).__name__} instead of raising")


if __name__ == "__main__":
    unittest.main(verbosity=2)
