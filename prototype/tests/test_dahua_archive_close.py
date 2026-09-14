#!/usr/bin/env python3
"""dahua_archive streamed-response close regression (0.4.4 Section 2).

The bounded clip download opens a STREAMED HTTP response. A streamed response holds the
recorder connection open until consumed or closed, so it MUST be closed on every path:
success, empty download, oversized download, an error body, and an exception mid-stream.
Leaking it would exhaust the recorder's session pool.
"""
from __future__ import annotations

import sys, unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import dahua_archive as da  # noqa: E402
from drivers.base import DriverError  # noqa: E402


class FakeResponse:
    def __init__(self, status=200, text="", chunks=None, raise_on_iter=None):
        self.status_code = status
        self._text = text
        self._chunks = chunks or []
        self._raise = raise_on_iter
        self.closed = False

    @property
    def text(self):
        return self._text

    def iter_content(self, chunk_size=0):
        for c in self._chunks:
            yield c
        if self._raise:
            raise self._raise

    def close(self):
        self.closed = True


class FakeSession:
    """Routes the archive CGI calls; hands out a configured STREAMED response for loadfile."""
    def __init__(self, stream_resp):
        self.auth = None
        self.stream_resp = stream_resp

    def get(self, url, params=None, timeout=None, stream=False):
        params = params or {}
        action = str(params.get("action", ""))
        if "global.cgi" in url:
            # Return the recorder clock as ~now so the plausibility/offset check passes.
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            return FakeResponse(text=f"result={now}")
        if "mediaFileFind.cgi" in url:
            if action == "factory.create":
                return FakeResponse(text="result=finder1")
            if action == "findFile":
                return FakeResponse(text="OK")
            if action == "findNextFile":
                return FakeResponse(text="items[0].Channel=0\r\nitems[0].StartTime=2026-06-01 09:59:00")
            return FakeResponse(text="OK")            # close/destroy
        if "loadfile.cgi" in url:
            assert stream is True, "loadfile must be a streamed request"
            return self.stream_resp
        return FakeResponse(text="OK")


class FakeDriver:
    def __init__(self, stream_resp):
        self.base_url = "http://recorder"
        self.username, self.password = "u", "p"
        self.timeout = 5
        self.s = FakeSession(stream_resp)


DHAV = b"DHAV" + b"\x00" * 1024                        # binary clip, not an error body
START = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
END = datetime(2026, 6, 1, 10, 5, tzinfo=timezone.utc)


class StreamCloses(unittest.TestCase):
    def test_closed_on_success(self):
        resp = FakeResponse(chunks=[DHAV])
        data = da.get_clip(FakeDriver(resp), "1", START, END)
        self.assertTrue(data.startswith(b"DHAV"))
        self.assertTrue(resp.closed, "streamed response must be closed after a successful download")

    def test_closed_on_empty(self):
        resp = FakeResponse(chunks=[])
        with self.assertRaises(DriverError):
            da.get_clip(FakeDriver(resp), "1", START, END)
        self.assertTrue(resp.closed, "streamed response must be closed after an empty download")

    def test_closed_on_oversized(self):
        # One chunk larger than the 32 MiB pilot limit -> _read_bounded raises before appending.
        resp = FakeResponse(chunks=[b"x" * (da.MAX_CLIP_BYTES + 4096)])
        with self.assertRaises(DriverError):
            da.get_clip(FakeDriver(resp), "1", START, END)
        self.assertTrue(resp.closed, "streamed response must be closed after an oversized download")

    def test_closed_on_exception_midstream(self):
        resp = FakeResponse(chunks=[DHAV], raise_on_iter=ConnectionError("connection reset mid-stream"))
        with self.assertRaises(ConnectionError):
            da.get_clip(FakeDriver(resp), "1", START, END)
        self.assertTrue(resp.closed, "streamed response must be closed when iteration raises")

    def test_closed_on_error_body(self):
        resp = FakeResponse(chunks=[b"<html>error</html>"])
        with self.assertRaises(DriverError):
            da.get_clip(FakeDriver(resp), "1", START, END)
        self.assertTrue(resp.closed, "streamed response must be closed after an error body")

    def test_source_uses_finally_close(self):
        # Guard against a future refactor dropping the finally-close.
        src = (ROOT / "agent" / "dahua_archive.py").read_text(encoding="utf-8")
        block = src.split("def get_clip", 1)[1]
        self.assertIn("finally:", block)
        self.assertIn("response.close()", block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
