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
import agent_core  # noqa: E402
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
    assert d._received is None                           # no stale receive clock after close


def test_hikvision_read_timeout_is_recorded_as_the_stream_error():
    d = _hik(Resp(200, chunks=[KEEPALIVE], error=requests.ConnectionError("Read timed out.")))
    with pytest.raises(requests.ConnectionError):
        list(d.stream_events(threading.Event()))
    assert d.event_stream["connected"] is False
    assert "timed out" in d.event_stream["last_error"]
    assert d.last_activity_monotonic > 0


class Sessions(Session):
    """Answers each stream request with the next response in turn (the last one repeats)."""

    def __init__(self, *resps):
        super().__init__(None)
        self.resps = list(resps)

    def request(self, method, url, **kw):
        return self.resps.pop(0) if len(self.resps) > 1 else self.resps[0]

    def get(self, url, **kw):
        return self.request("GET", url, **kw)


def test_hikvision_200_then_eof_takes_its_2xx_stamp_back():
    # The recorder accepts alertStream and closes it at once: no chunk ever arrived.
    d = _hik(Resp(200))
    assert list(d.stream_events(threading.Event())) == []
    assert d.last_activity_monotonic == 0.0
    assert d.event_stream["connected"] is False
    assert d.event_stream["last_frame_at"] is None
    assert d.event_stream["last_error"] == "event stream ended by the recorder"

    # After a stream that did deliver a frame, a frameless reopen keeps the frame's stamp.
    d = _hik(None)
    d.s = Sessions(Resp(200, chunks=[KEEPALIVE]), Resp(200))
    list(d.stream_events(threading.Event()))
    framed = d.last_activity_monotonic
    assert framed > 0
    time.sleep(0.02)
    list(d.stream_events(threading.Event()))
    assert d.last_activity_monotonic == framed


def test_dahua_200_then_eof_takes_its_2xx_stamp_back():
    d = _dahua(Resp(200, lines=[]))
    assert list(d.stream_events(threading.Event())) == []
    assert d.last_activity_monotonic == 0.0
    assert d.event_stream["connected"] is False
    assert d.event_stream["last_frame_at"] is None

    d = _dahua(None)
    d.s = Sessions(Resp(200, lines=[b"Heartbeat"]), Resp(200, lines=[]))
    list(d.stream_events(threading.Event()))
    framed = d.last_activity_monotonic
    assert framed > 0
    time.sleep(0.02)
    list(d.stream_events(threading.Event()))
    assert d.last_activity_monotonic == framed


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def monotonic(self):
        return self.now


@pytest.mark.parametrize("vendor", ["hikvision", "dahua"])
def test_a_200_then_eof_stream_reopened_for_minutes_is_not_live(monkeypatch, tmp_path, vendor):
    """deviceInfo answers and every stream request gets 200 headers then EOF. The collector
    reopens it every few seconds for over 150 s (patched clock): the recorder must end not
    live, and last_live must not move forward although every reopen answered 2xx."""
    import drivers.dahua as dahua_mod
    import drivers.hikvision as hik_mod
    import recovery
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    clock = FakeClock()
    module = hik_mod if vendor == "hikvision" else dahua_mod
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=clock.monotonic))

    def open_driver(cfg):
        d = (_hik(Resp(200)) if vendor == "hikvision" else _dahua(Resp(200, lines=[])))
        return d, Info()

    cfg = SimpleNamespace(recovery_enabled=True, last_live_path=tmp_path / "last_live.json",
                          recovery_threshold_seconds=180)
    seeded = datetime.now(timezone.utc) - timedelta(seconds=60)
    recovery.persist_last_live(cfg.last_live_path, seeded)
    holder, samples = {}, []

    def wait(stop, _cfg, auth_failures, last_gen, seconds=None):
        samples.append(analytics_agent._recorder_stream_live(holder, clock.now))
        analytics_agent._persist_stream_last_live(cfg, holder, clock.now)
        clock.now += core.DRIVER_RETRY_SECONDS if seconds is None else seconds
        if clock.now - 1000.0 > 200:
            stop.set()
            return "stop", last_gen
        return "timeout", last_gen

    _spool, holder, _ok = run_collector(monkeypatch, open_driver, holder=holder,
                                        reconnect_wait=wait, timeout=20.0)
    assert clock.now - 1000.0 > 200, "the collector stopped reopening before 150 s passed"
    assert len(samples) > 10
    assert not any(samples), "a stream that never delivered a byte was reported live"
    assert analytics_agent._recorder_stream_live(holder, clock.now) is False
    assert holder["event_stream"]["connected"] is False
    assert holder["event_stream"]["last_frame_at"] is None
    assert recovery.read_last_live(cfg.last_live_path) == seeded


def test_stream_seen_at_ignores_the_2xx_of_a_stream_that_has_ended():
    frame = "2026-10-04T16:00:00+00:00"
    later = "2026-10-04T16:05:00+00:00"
    ended = {"connected": False, "connected_at": later, "last_frame_at": frame}
    assert analytics_agent._stream_seen_at(ended).isoformat() == frame
    assert analytics_agent._stream_seen_at({**ended, "last_frame_at": None}) is None
    assert analytics_agent._stream_seen_at({**ended, "connected": True}).isoformat() == later


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
    monkeypatch.setattr(agent_core, "runtime_health_path", lambda: path)  # heartbeat lives in agent_core (shared with Setup); patch its own collaborator
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
