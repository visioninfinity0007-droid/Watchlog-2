#!/usr/bin/env python3
"""MNVR-008: recorder liveness comes from the event stream, not the deviceInfo probe.

holder["recorder_live_at"] used to be stamped as soon as open_driver's probe answered,
before alertStream was even requested, and refreshed only on yielded events. A recorder
whose deviceInfo works while alertStream returns 4xx/5xx or EOF was re-stamped live every
reconnect: the heartbeat advanced recorder_seen_at, Repair/Upgrade committed, and nothing
recorded that no event could arrive. Keep-alive frames never counted either, so a healthy
quiet stream and a dead one looked the same.

Now Hikvision and Dahua stamp last_activity_monotonic only after a 2xx stream response and
on every received chunk (keep-alives included), keep an event_stream state
{connected, connected_at, last_frame_at, last_error}, and the heartbeat writes that state
into the local health proof.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_collector_harness import Info, run_collector, stop_after_first_wait  # noqa: E402
import analytics_agent  # noqa: E402
import watchlog_agent as core  # noqa: E402
from drivers.dahua import DahuaDriver  # noqa: E402
from drivers.hikvision import HikvisionDriver  # noqa: E402

KEEPALIVE = (b"--boundary\r\nContent-Type: application/xml\r\n\r\n"
             b"<EventNotificationAlert><eventType>videoloss</eventType>"
             b"<eventState>inactive</eventState><channelID>1</channelID>"
             b"<activePostCount>0</activePostCount></EventNotificationAlert>")


class Resp:
    def __init__(self, status=200, chunks=(), lines=(), error=None, gate=None):
        self.status_code = status
        self.headers = {}
        self.content = b""
        self.text = ""
        self.chunks, self.lines, self.error, self.gate = list(chunks), list(lines), error, gate

    def iter_content(self, chunk_size=1024):
        for chunk in self.chunks:
            yield chunk
            if self.gate is not None:
                self.gate.wait(5)
        if self.error is not None:
            raise self.error

    def iter_lines(self, chunk_size=512):
        for line in self.lines:
            yield line
        if self.error is not None:
            raise self.error

    def close(self):
        pass


class Session:
    def __init__(self, resp):
        self.resp = resp
        self.auth = None

    def request(self, method, url, **kw):
        return self.resp

    def get(self, url, **kw):
        return self.resp

    def close(self):
        pass


def _hik(resp):
    d = HikvisionDriver("http://192.0.2.10", "admin", "x", timeout=1)
    d.s = Session(resp)
    return d


def _dahua(resp):
    d = DahuaDriver("http://192.0.2.11", "admin", "x", timeout=1)
    d.s = Session(resp)
    return d


def test_hikvision_alertstream_error_stamps_no_liveness():
    d = _hik(Resp(503))
    with pytest.raises(core.DriverError):
        list(d.stream_events(threading.Event()))
    assert d.last_activity_monotonic == 0.0
    assert d.event_stream["connected"] is False
    assert d.event_stream["connected_at"] is None
    assert "503" in d.event_stream["last_error"]


def test_hikvision_keepalives_keep_the_stream_fresh_without_events():
    gate = threading.Event()
    d = _hik(Resp(200, chunks=[KEEPALIVE, KEEPALIVE], gate=gate))
    seen = []
    worker = threading.Thread(target=lambda: seen.extend(d.stream_events(threading.Event())))
    worker.start()
    deadline = time.monotonic() + 5
    while d.event_stream.get("last_frame_at") is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert d.event_stream["connected"] is True          # mid-stream: connected
    assert time.monotonic() - d.last_activity_monotonic < 5
    gate.set()
    worker.join(5)
    assert seen == []                                    # keep-alives are not events
    assert d.event_stream["last_frame_at"] is not None
    assert d.event_stream["connected"] is False          # EOF: no longer connected
    assert d.event_stream["last_error"]


def test_hikvision_read_timeout_is_recorded_as_the_stream_error():
    d = _hik(Resp(200, chunks=[KEEPALIVE], error=requests.ConnectionError("Read timed out.")))
    with pytest.raises(requests.ConnectionError):
        list(d.stream_events(threading.Event()))
    assert d.event_stream["connected"] is False
    assert "timed out" in d.event_stream["last_error"]
    assert d.last_activity_monotonic > 0


def test_dahua_attach_error_stamps_no_liveness_and_heartbeats_count():
    d = _dahua(Resp(503))
    with pytest.raises(core.DriverError):
        list(d.stream_events(threading.Event()))
    assert d.last_activity_monotonic == 0.0
    assert d.event_stream["connected"] is False

    d = _dahua(Resp(200, lines=[b"--myboundary", b"Heartbeat", b"", b"Heartbeat"]))
    assert list(d.stream_events(threading.Event())) == []
    assert d.last_activity_monotonic > 0
    assert d.event_stream["last_frame_at"] is not None


class DeadStreamHik(HikvisionDriver):
    """deviceInfo answers; alertStream returns 503."""

    def __init__(self):
        super().__init__("http://192.0.2.10", "admin", "x", timeout=1)
        self.s = Session(Resp(503))


def test_collector_does_not_stamp_a_probe_as_recorder_liveness(monkeypatch):
    waits = []
    _spool, holder, _ok = run_collector(
        monkeypatch, lambda cfg: (DeadStreamHik(), Info()),
        reconnect_wait=stop_after_first_wait(waits))
    assert waits, "collector never reached its reconnect wait"
    assert not holder.get("recorder_live_at")
    assert holder["event_stream"]["connected"] is False
    assert "503" in holder["event_stream"]["last_error"]
    assert analytics_agent._recorder_stream_live(holder, time.monotonic()) is False


def test_stream_liveness_window():
    class Drv:
        last_activity_monotonic = 0.0
    now = time.monotonic()
    holder = {"live_driver": Drv()}
    assert analytics_agent._recorder_stream_live(holder, now) is False
    Drv.last_activity_monotonic = now - 10
    assert analytics_agent._recorder_stream_live(holder, now) is True
    Drv.last_activity_monotonic = now - 151
    assert analytics_agent._recorder_stream_live(holder, now) is False
    # After the collector drops the driver, its last activity is carried in recorder_live_at.
    assert analytics_agent._recorder_stream_live({"recorder_live_at": now - 20}, now) is True


class Cloud:
    def __init__(self):
        self.calls = []

    def call(self, fn, **kw):
        self.calls.append((fn, kw))
        return {}


def test_heartbeat_writes_redacted_event_stream_state(monkeypatch, tmp_path):
    path = tmp_path / "runtime-health.json"
    monkeypatch.setattr(core, "runtime_health_path", lambda: path)
    stream = {"connected": False, "connected_at": None, "last_frame_at": "2026-10-04T16:00:00+00:00",
              "last_error": "alertStream: http://admin:pw@192.168.1.64/ISAPI/Event/x: HTTP 503"}
    core.heartbeat(Cloud(), {"agent_id": "a", "agent_key": "k"}, None,
                   recorder_live=False, event_stream=stream)
    health = json.loads(path.read_text(encoding="utf-8"))
    assert health["event_stream"]["connected"] is False
    assert health["event_stream"]["last_frame_at"] == "2026-10-04T16:00:00+00:00"
    error = health["event_stream"]["last_error"]
    assert "503" in error and "192.168" not in error and "pw" not in error
    assert "recorder_seen_at" not in health                 # a dead stream is not live


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
