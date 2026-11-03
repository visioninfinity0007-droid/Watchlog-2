#!/usr/bin/env python3
"""5.1.2: a recorder restart is detected from an event-stream drop plus an uptime reset.

The collector reads the recorder's uptime each time it (re)opens the event stream. Only
positive evidence makes a recorder_restart event: the uptime went DOWN since the previous
read. The first read (no baseline), an unreadable uptime and an uptime that grew are never
a restart. The event is recorder-scoped (channel None), timed at the estimated boot, and
keeps both reads as evidence.

Hikvision: /ISAPI/System/status deviceUpTime. Dahua: magicBox/global getUpTime
(IMPLEMENTED_UNVERIFIED; a cumulative total is never read as uptime).
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_collector_harness import Info, run_collector  # noqa: E402

import recorder_restart  # noqa: E402
from drivers.base import DriverError, NvrUnreachable  # noqa: E402
from drivers.dahua import DahuaDriver, parse_uptime_kv  # noqa: E402
from drivers.hikvision import HikvisionDriver, _strip_ns  # noqa: E402

AT = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)


# --- the rule --------------------------------------------------------------------------

def test_first_read_is_only_a_baseline():
    watch = recorder_restart.UptimeWatch()
    assert watch.observe(5.0, AT) is None


def test_uptime_that_grew_or_is_unknown_is_not_a_restart():
    watch = recorder_restart.UptimeWatch()
    assert watch.observe(1000.0, AT) is None
    assert watch.observe(None, AT) is None
    assert watch.observe("garbage", AT) is None
    assert watch.observe(-1, AT) is None
    assert watch.observe(1500.0, AT) is None
    assert watch.observe(1497.0, AT) is None          # inside the rounding slack


def test_uptime_that_went_down_is_a_restart():
    watch = recorder_restart.UptimeWatch()
    watch.observe(86400.0, AT)
    ev = watch.observe(120.0, AT)
    assert ev is not None
    assert ev.event_type == "recorder_restart"
    assert ev.channel is None
    assert ev.device_event_id is None
    assert ev.device_ts == AT - timedelta(seconds=120)
    assert ev.payload["recorder_scoped"] is True
    assert ev.payload["source"] == "uptime_probe"
    assert ev.payload["previous_uptime_seconds"] == 86400
    assert ev.payload["uptime_seconds"] == 120
    # The new read is the new baseline: no second event for the same boot.
    assert watch.observe(180.0, AT) is None


def test_check_never_raises_on_a_broken_driver():
    class Broken:
        def uptime_seconds(self):
            raise NvrUnreachable("down")
    assert recorder_restart.check(Broken(), recorder_restart.UptimeWatch()) is None
    assert recorder_restart.check(object(), recorder_restart.UptimeWatch()) is None


# --- vendor reads ----------------------------------------------------------------------

def test_hikvision_uptime_from_device_status():
    d = HikvisionDriver("http://127.0.0.1", "u", "p", timeout=1)
    body = (b'<DeviceStatus xmlns="http://www.hikvision.com/ver20/XMLSchema">'
            b'<currentDeviceTime>2026-10-07T12:00:00+05:00</currentDeviceTime>'
            b'<deviceUpTime>3725</deviceUpTime></DeviceStatus>')
    asked = []
    d._xml = lambda path: (asked.append(path), _strip_ns(ET.fromstring(body)))[1]
    assert d.uptime_seconds() == 3725.0
    assert asked == ["/ISAPI/System/status"]


def test_hikvision_uptime_unknown_when_not_stated():
    d = HikvisionDriver("http://127.0.0.1", "u", "p", timeout=1)
    d._xml = lambda path: _strip_ns(ET.fromstring(b"<DeviceStatus><cpuList/></DeviceStatus>"))
    assert d.uptime_seconds() is None

    def fail(path):
        raise DriverError("HTTP 404")
    d._xml = fail
    assert d.uptime_seconds() is None


@pytest.mark.parametrize("kv,expected", [
    ({"up": "4000"}, 4000.0),
    ({"result": "77"}, 77.0),
    ({"info.last": "12"}, 12.0),
    ({"info.total": "999999"}, None),     # cumulative running time, never uptime
    ({"up": "soon"}, None),
    ({}, None),
])
def test_dahua_uptime_parse(kv, expected):
    assert parse_uptime_kv(kv) == expected


def test_dahua_uptime_falls_back_and_remembers_a_rejected_call():
    d = DahuaDriver("http://127.0.0.1", "u", "p", timeout=1)
    asked = []

    def fake_get(path, **kw):
        asked.append(path)
        if "magicBox" in path:
            raise DriverError(f"http://x{path}: HTTP 400 Bad Request")
        return "up=5400\r\n"

    d._get = fake_get
    assert d.uptime_seconds() == 5400.0
    assert d.uptime_seconds() == 5400.0
    assert asked == ["/cgi-bin/magicBox.cgi?action=getUpTime",
                     "/cgi-bin/global.cgi?action=getUpTime",
                     "/cgi-bin/global.cgi?action=getUpTime"]


def test_dahua_uptime_transient_errors_are_asked_again():
    d = DahuaDriver("http://127.0.0.1", "u", "p", timeout=1)
    calls = []

    def fake_get(path, **kw):
        calls.append(path)
        raise DriverError(f"http://x{path}: HTTP 503 busy")

    d._get = fake_get
    assert d.uptime_seconds() is None
    assert d.uptime_seconds() is None
    assert len(calls) == 4                 # both paths, both times: nothing was rejected


# --- the vendor stream hooks -----------------------------------------------------------

class _StreamResp:
    status_code = 200
    headers: dict = {}
    content = b""
    text = ""

    def __init__(self, chunk=b"", lines=()):
        self.chunk, self.lines = chunk, list(lines)

    def iter_content(self, chunk_size=1024):
        for _ in range(50):
            yield self.chunk

    def iter_lines(self, chunk_size=512):
        return iter(self.lines)

    def close(self):
        pass


class _Session:
    def __init__(self, resp):
        self.resp, self.auth, self.urls = resp, None, []

    def request(self, method, url, **kw):
        self.urls.append(url)
        return self.resp

    def get(self, url, **kw):
        self.urls.append(url)
        return self.resp

    def close(self):
        pass


def test_hikvision_runs_the_hook_on_a_fresh_open_not_on_planned_slices(monkeypatch):
    import time as _time
    from drivers import hikvision
    monkeypatch.setattr(hikvision, "HIKVISION_STREAM_SLICE_SECONDS", 0.0)
    keepalive = (b"<EventNotificationAlert><eventType>videoloss</eventType>"
                 b"<eventState>inactive</eventState><activePostCount>0</activePostCount>"
                 b"</EventNotificationAlert>")
    d = HikvisionDriver("http://192.0.2.77", "u", "p", timeout=1)
    d.s = _Session(_StreamResp(chunk=keepalive))
    held = []
    marker = object()

    def hook():
        held.append(hikvision.recorder_http_lock(d.base_url)._lock._is_owned())
        return [marker]

    d.on_stream_open = hook
    threading = __import__("threading")
    stop = threading.Event()

    def stop_after_three_slices():
        deadline = _time.monotonic() + 5
        while len(d.s.urls) < 3 and _time.monotonic() < deadline:
            _time.sleep(0.01)
        stop.set()

    threading.Thread(target=stop_after_three_slices, daemon=True).start()
    out = list(d.stream_events(stop))
    assert out[0] is marker and out.count(marker) == 1
    assert len(d.s.urls) >= 3, "several planned slices were opened"
    assert held == [True], "the uptime read runs inside the stream's recorder lock"


def test_dahua_runs_the_hook_before_each_attach():
    d = DahuaDriver("http://192.0.2.78", "u", "p", timeout=1)
    d.s = _Session(_StreamResp(lines=[]))
    marker = object()
    d.on_stream_open = lambda: [marker]
    out = list(d.stream_events(__import__("threading").Event()))
    assert out == [marker]
    d.on_stream_open = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    assert list(d.stream_events(__import__("threading").Event())) == []


# --- the collector ---------------------------------------------------------------------

class StreamDriver:
    """A stream-reporting driver whose stream drops at once, and whose uptime reads come
    from ``uptimes`` in order (the last one repeats)."""
    name = "fake-stream"
    verified_against_hardware = True
    reports_stream_activity = True

    def __init__(self, uptimes):
        self.uptimes = list(uptimes)
        self.reads = 0
        self.event_stream = {}
        self.last_activity_monotonic = 0.0

    def uptime_seconds(self):
        self.reads += 1
        return self.uptimes.pop(0) if len(self.uptimes) > 1 else self.uptimes[0]

    on_stream_open = None

    def stream_events(self, stop):
        # Like the vendor drivers: the collector's hook runs as the stream (re)opens.
        if self.on_stream_open is not None:
            yield from self.on_stream_open()
        return                              # the recorder drops the stream at once

    def get_snapshot(self, channel):
        return None

    def close(self):
        pass


def _fast_wait(stop, cfg, auth_failures, last_gen, *args, **kwargs):
    return ("stop" if stop.is_set() else "timeout"), last_gen


def _has_restart(_holder, spool):
    return any(r["event_type"] == "recorder_restart" for r in spool.rows)


def test_collector_reports_a_restart_after_a_drop(monkeypatch):
    driver = StreamDriver([90000.0, 90010.0, 45.0, 60.0])
    spool, holder, ok = run_collector(
        monkeypatch, lambda cfg: (driver, Info()), until=_has_restart,
        reconnect_wait=_fast_wait, timeout=8.0)
    assert ok, "no recorder_restart after the uptime went down"
    restarts = [r for r in spool.rows if r["event_type"] == "recorder_restart"]
    assert len(restarts) == 1
    row = restarts[0]
    assert row["channel"] is None
    assert row["payload"]["previous_uptime_seconds"] == 90010
    assert row["payload"]["uptime_seconds"] == 45
    assert row["payload"]["driver"] == "fake-stream"
    # An uptime read is not stream activity: it never makes the recorder look live.
    assert "recorder_live_at" not in holder


def test_collector_never_reports_a_restart_without_evidence(monkeypatch):
    driver = StreamDriver([100.0, 160.0, 220.0, 280.0, 340.0])
    spool, _holder, _ok = run_collector(
        monkeypatch, lambda cfg: (driver, Info()),
        until=lambda h, s: driver.reads >= 4, reconnect_wait=_fast_wait, timeout=8.0)
    assert driver.reads >= 4
    assert not _has_restart(None, spool)


def test_unreadable_uptime_is_never_a_restart(monkeypatch):
    driver = StreamDriver([None])
    spool, _holder, _ok = run_collector(
        monkeypatch, lambda cfg: (driver, Info()),
        until=lambda h, s: driver.reads >= 3, reconnect_wait=_fast_wait, timeout=8.0)
    assert driver.reads >= 3
    assert spool.rows == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
