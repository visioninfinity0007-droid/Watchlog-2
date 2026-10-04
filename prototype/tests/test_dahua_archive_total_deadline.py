#!/usr/bin/env python3
"""MNVR-034: the Dahua clip download has a total time budget and a (connect, read) timeout.

Before the fix loadfile used one scalar 90 s timeout for connect AND every socket read, and the
download loop had no clock check: a recorder that kept sending a little data every minute held
the footage worker (and every request queued behind it) until the 32 MiB cap. Hikvision got a
total budget in 5.0.27; Dahua did not.

The RealSocket cases run the same budget against a 127.0.0.1 HTTP server and the real
requests/urllib3 stack: a slow stream blocks inside one read until that read's whole size has
arrived, which a fake that hands out ready-made chunks cannot show.

Field-only (IMPLEMENTED_UNVERIFIED): whether real Dahua firmware ever trickles loadfile data, and
how long a real DH-XVR1B08-I takes to send the first byte.
"""
from __future__ import annotations

import sys
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import requests
import urllib3

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dahua_fake_recorder import (  # noqa: E402
    DHAV, FMT, FakeDahua, FakeRecorder, continuous_files, local, pinned_datetime)
import dahua_archive as da  # noqa: E402
from drivers.base import DriverError  # noqa: E402
from drivers.dahua import DahuaDriver  # noqa: E402

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

    def test_urllib3_read_timeout_is_an_error_without_the_lan_address(self):
        # read1 reads below requests, so urllib3's own errors are not wrapped into requests' ones.
        def stall():
            yield DHAV
            raise urllib3.exceptions.ReadTimeoutError(
                None, None, "HTTPConnectionPool(host='192.168.1.108', port=80): Read timed out.")

        self.rec.clip_chunks = stall
        with self.assertRaises(DriverError) as caught:
            da.get_clip(self.drv, "1", START, END)
        self.assertNotIn("192.168.", str(caught.exception))
        self.assertTrue(self.rec.responses[-1].closed)

    def test_without_read1_the_body_is_read_in_small_chunks(self):
        # A urllib3 without read1 leaves iter_content, which blocks until a whole chunk arrives.
        sizes = []

        class NoRead1:
            def iter_content(self, chunk_size=1):
                sizes.append(chunk_size)
                yield DHAV

        self.assertTrue(da._read_bounded(NoRead1()).startswith(b"DHAV"))
        self.assertLessEqual(sizes[0], 64 * 1024)

    def test_budget_failure_is_an_error_not_unsupported(self):
        # incident_evidence turns a None/empty result into a terminal "unsupported"; running out of
        # time must surface as an error instead.
        self.advance_on("findNextFile", BUDGET + 1)
        try:
            result = da.get_clip(self.drv, "1", START, END)
        except DriverError:
            return
        self.fail(f"budget exhaustion returned {type(result).__name__} instead of raising")



class LoopbackRecorder(BaseHTTPRequestHandler):
    """Just enough recorder CGI on 127.0.0.1 for get_clip; the test shapes the loadfile body."""

    def log_message(self, *_args):
        pass

    def _text(self, body: str):
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlsplit(self.path)
        action = parse_qs(url.query).get("action", [""])[0]
        if url.path.endswith("/global.cgi"):
            self._text(f"result={datetime.now(timezone.utc).strftime(FMT)}\r\n")
        elif url.path.endswith("/mediaFileFind.cgi"):
            if action == "factory.create":
                self._text("result=finder1\r\n")
            elif action == "findNextFile":
                self._text(FakeRecorder.items_text(
                    [("2000-01-01 00:00:00", "2100-01-01 00:00:00", "/mnt/dvr/all.dav")]))
            else:
                self._text("OK\r\n")
        elif url.path.endswith("/loadfile.cgi"):
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            try:
                self.server.loadfile_body(self.wfile)
            except OSError:
                pass                                # the Agent hung up
        else:
            self.send_error(404)


class LoopbackServer(ThreadingHTTPServer):
    block_on_close = False                          # a handler still sending must not hold teardown


class RealSocketDeadline(unittest.TestCase):
    def setUp(self):
        self.server = LoopbackServer(("127.0.0.1", 0), LoopbackRecorder)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.drv = DahuaDriver(f"http://127.0.0.1:{self.server.server_address[1]}", "u", "p", timeout=5)
        self.drv.s.trust_env = False                # never send loopback traffic through a proxy
        now = datetime.now(timezone.utc)
        self.window = (now - timedelta(seconds=120), now - timedelta(seconds=90))

    def test_slow_stream_is_cut_at_the_total_budget(self):
        # 20 bytes every 50 ms: never a read timeout, and a 256 KiB read would take 11 minutes.
        def trickle(wfile):
            wfile.write(DHAV[:64])
            stop = time.monotonic() + 8
            while time.monotonic() < stop:
                wfile.write(b"\x00" * 20)
                time.sleep(0.05)

        self.server.loadfile_body = trickle
        budget = 1.5
        started = time.monotonic()
        with mock.patch.object(da, "CLIP_TOTAL_SECONDS", budget):
            with self.assertRaises(DriverError):
                da.get_clip(self.drv, "1", *self.window)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, budget + 1.0, f"download ran {elapsed:.1f} s on a {budget} s budget")

    def test_stalled_stream_is_an_error_without_the_lan_address(self):
        # urllib3's own read timeout names the host; it must not reach cloud-visible failure text.
        def stall(wfile):
            wfile.write(DHAV[:64])
            time.sleep(3)

        self.server.loadfile_body = stall
        with mock.patch.object(da, "DOWNLOAD_TIMEOUT", (5, 1)):
            with self.assertRaises(DriverError) as caught:
                da.get_clip(self.drv, "1", *self.window)
        self.assertNotIn("127.0.0.1", str(caught.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
