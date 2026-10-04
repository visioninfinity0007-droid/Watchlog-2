#!/usr/bin/env python3
"""MNVR-024 (Dahua): a live attach event is stamped when its block arrives.

Dahua attach blocks carry no device clock, so device_ts is the agent's clock. It used to
be read when the generator parsed the line, and the collector fetches a still (up to 10 s)
and runs AI for one event before asking for the next. Blocks already sent by the recorder
therefore got progressively later timestamps. The attach body is now read on its own
thread and every block keeps the time it arrived.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest
import requests

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))

from drivers.native_recorder import NativeDahuaDriver  # noqa: E402


class Resp:
    status_code = 200

    def __init__(self, lines, error=None):
        self.lines = lines
        self.error = error
        self.closed = False

    def iter_lines(self, chunk_size=512):
        for line in self.lines:
            yield line
        if self.error is not None:
            raise self.error

    def close(self):
        self.closed = True


class Session:
    def __init__(self, resp):
        self.resp = resp
        self.auth = None

    def get(self, url, **kw):
        return self.resp

    def request(self, method, url, **kw):
        return self.resp

    def close(self):
        pass


def _driver(resp):
    d = NativeDahuaDriver("http://127.0.0.1", "admin", "x", timeout=1)
    d.s = Session(resp)
    return d


def test_blocks_keep_their_arrival_time_while_the_collector_is_busy():
    resp = Resp([b"--myboundary", b"Code=VideoMotion;action=Start;index=0", b"",
                 b"Code=VideoMotion;action=Start;index=1", b"Heartbeat"])
    d = _driver(resp)
    events = []
    try:
        for ev in d.stream_events(threading.Event()):
            events.append(ev)
            time.sleep(1.0)          # the collector's still/AI work for this event
    finally:
        d.close()
    assert [e.channel for e in events] == ["1", "2"]
    gap = (events[1].device_ts - events[0].device_ts).total_seconds()
    assert gap < 0.5, f"second block stamped {gap:.2f}s late"
    assert all(e.payload["clock_source"] == "agent_receive" for e in events)
    assert resp.closed


def test_a_read_error_mid_stream_reaches_the_collector():
    resp = Resp([b"Code=VideoMotion;action=Start;index=0"],
                error=requests.ConnectionError("Read timed out."))
    d = _driver(resp)
    seen = []
    try:
        with pytest.raises(requests.ConnectionError):
            for ev in d.stream_events(threading.Event()):
                seen.append(ev)
    finally:
        d.close()
    assert len(seen) == 1
    assert resp.closed


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
