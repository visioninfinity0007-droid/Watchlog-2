#!/usr/bin/env python3
"""The Hikvision clip download keeps its total time budget on a slow, steady stream (MNVR-034).

_read_download_response iterated response.iter_content(256 KiB) and checked the deadline only
between chunks. urllib3 blocks until a whole chunk has arrived, so a recorder sending under about
2.9 KB/s (256 KiB / 90 s) never reached the check: the footage worker stayed held for minutes and
the 90 s budget meant nothing. Each read now returns whatever has arrived (urllib3 read1), and the
deadline is checked after every read.

These cases run against a 127.0.0.1 HTTP server and the real requests/urllib3 stack: a fake that
hands out ready-made chunks cannot show a read blocking. Field-only (IMPLEMENTED_UNVERIFIED):
whether real Hikvision firmware ever trickles a download.
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import hikvision_archive as ha  # noqa: E402
from drivers.hikvision import HikvisionDriver  # noqa: E402

SEARCH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<CMSearchResult xmlns="http://www.isapi.org/ver20/XMLSchema">
  <responseStatusStrg>OK</responseStatusStrg>
  <matchList><searchMatchItem>
    <trackID>101</trackID>
    <timeSpan><startTime>{start}</startTime><endTime>{end}</endTime></timeSpan>
    <mediaSegmentDescriptor><playbackURI>rtsp://127.0.0.1/Streaming/tracks/101/?starttime=20260925T080000Z&amp;endtime=20260925T090000Z&amp;name=ch01&amp;size=4096</playbackURI></mediaSegmentDescriptor>
  </searchMatchItem></matchList>
</CMSearchResult>"""


class LoopbackIsapi(BaseHTTPRequestHandler):
    """Just enough ISAPI on 127.0.0.1 for get_clip; the test shapes the first download body."""

    def log_message(self, *_args):
        pass

    def _download(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = self.server.download_bodies.pop(0) if self.server.download_bodies else None
        if body is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()
        try:
            body(self.wfile)
        except OSError:
            pass                                # the Agent hung up

    def do_GET(self):
        if self.path.endswith("/ISAPI/ContentMgmt/download"):
            self._download()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path.endswith("/ISAPI/ContentMgmt/search"):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            data = SEARCH_XML.format(start=self.server.window[0], end=self.server.window[1]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/xml")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path.endswith("/ISAPI/ContentMgmt/download"):
            self._download()
        else:
            self.send_error(404)


class LoopbackServer(ThreadingHTTPServer):
    block_on_close = False                      # a handler still sending must not hold teardown


class RealSocketDeadline(unittest.TestCase):
    def setUp(self):
        self.server = LoopbackServer(("127.0.0.1", 0), LoopbackIsapi)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        start = datetime(2026, 9, 25, 8, 0, 10, tzinfo=timezone.utc)
        self.window = (start, start + timedelta(seconds=30))
        self.server.window = (start.isoformat().replace("+00:00", "Z"),
                              (start + timedelta(hours=1)).isoformat().replace("+00:00", "Z"))
        self.server.download_bodies = []
        self.drv = HikvisionDriver(f"http://127.0.0.1:{self.server.server_address[1]}", "u", "p",
                                   timeout=5)
        self.drv.s.trust_env = False                # never send loopback traffic through a proxy
        patcher = mock.patch.object(ha, "HIKVISION_HTTP_LOCK", threading.RLock())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_slow_stream_is_cut_at_the_total_budget(self):
        # 20 bytes every 50 ms: never a read timeout, and a 256 KiB read would take 11 minutes.
        def trickle(wfile):
            stop = time.monotonic() + 8
            while time.monotonic() < stop:
                wfile.write(b"\x00" * 20)
                wfile.flush()
                time.sleep(0.05)

        self.server.download_bodies = [trickle]
        budget = 1.5
        started = time.monotonic()
        with mock.patch.object(ha, "CLIP_TOTAL_SECONDS", budget):
            with self.assertRaises(ha.ClipTimedOut):
                ha.get_clip(self.drv, "1", *self.window)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, budget + 1.0, f"download ran {elapsed:.1f} s on a {budget} s budget")

    def test_stalled_stream_is_a_transport_failure_without_the_lan_address(self):
        # urllib3's own read timeout names the host; it must not reach cloud-visible failure text.
        def stall(wfile):
            wfile.write(b"\x00" * 64)
            wfile.flush()
            time.sleep(7)

        self.server.download_bodies = [stall]       # the by-time fallback then gets a 404
        with mock.patch.object(ha, "DOWNLOAD_TIMEOUT", (5, 1)):
            with self.assertRaises(ha.ClipUnreachable) as caught:
                ha.get_clip(self.drv, "1", *self.window)
        self.assertNotIn("127.0.0.1", str(caught.exception))

    def test_a_steady_stream_inside_the_budget_is_read_whole(self):
        def body(wfile):
            for _ in range(5):
                wfile.write(b"\x00" * 1000)
                wfile.flush()
                time.sleep(0.02)

        self.server.download_bodies = [body]
        with mock.patch.object(ha, "_probe_clip", lambda _data: None):
            self.assertEqual(len(ha.get_clip(self.drv, "1", *self.window)), 5000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
