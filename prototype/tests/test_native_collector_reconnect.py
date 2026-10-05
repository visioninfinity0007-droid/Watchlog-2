#!/usr/bin/env python3
"""MNVR-022: a dropped Hikvision/Dahua event stream is reopened at once, not after 20 s.

Every alertStream EOF, 90 s read timeout or reset was handled like a driver failure: a
fixed DRIVER_RETRY_SECONDS (20 s) wait, then a full open_driver re-probe before the stream
was requested again. The stream has no replay, so each drop left 20 s or more in which no
event could arrive. A stream-reporting driver now has its stream reopened on the same
driver after a short jittered delay that escalates only on consecutive failed reopens;
auth failures keep the 5/15/30 min lockout guard, and exhausted reopens fall back to the
full re-probe path.
"""
from __future__ import annotations

import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_collector_harness import Info, run_collector  # noqa: E402
import native_event_collector as nec  # noqa: E402
import watchlog_agent as core  # noqa: E402
from drivers.base import DriverError, Event  # noqa: E402

WHEN = datetime(2026, 10, 4, 16, 0, 0, tzinfo=timezone.utc)


class StreamDriver:
    """A stream-reporting driver whose stream_events follows a per-call script.

    Each script item is "eof" (stream up, one event, then the recorder closes it),
    "timeout" (stream up, then a read timeout), "503" (the stream request is refused) or
    "401" (the stream request is rejected for credentials)."""

    name = "fake-stream"
    verified_against_hardware = True
    reports_stream_activity = True

    def __init__(self, script):
        self.script = list(script)
        self.opens = []
        self.last_activity_monotonic = 0.0
        self.event_stream = {"connected": False, "connected_at": None,
                             "last_frame_at": None, "last_error": None}

    def stream_events(self, stop):
        self.opens.append(time.monotonic())
        step = self.script.pop(0) if self.script else "eof"
        if step == "503":
            self.event_stream.update(connected=False, last_error="HTTP 503")
            raise DriverError("alertStream: HTTP 503")
        if step == "401":
            self.event_stream.update(connected=False, last_error="HTTP 401")
            raise DriverError("alertStream: HTTP 401")
        self.last_activity_monotonic = time.monotonic()
        self.event_stream.update(connected=True, last_error=None,
                                 connected_at=f"open-{len(self.opens)}")
        yield Event(channel="1", event_type="motion", device_ts=WHEN, payload={})
        self.event_stream["connected"] = False
        if step == "timeout":
            self.event_stream["last_error"] = "timed out"
            raise requests.ConnectionError("Read timed out.")
        self.event_stream["last_error"] = "event stream ended by the recorder"

    def get_snapshot(self, channel):
        return None

    def close(self):
        pass


class Opener:
    def __init__(self, make):
        self.make = make
        self.drivers = []

    def __call__(self, cfg):
        driver = self.make()
        self.drivers.append(driver)
        return driver, Info()


def test_eof_reopens_the_same_driver_within_seconds_without_a_reprobe(monkeypatch):
    monkeypatch.setattr(nec, "STREAM_STABLE_SECONDS", 0.0, raising=False)
    opener = Opener(lambda: StreamDriver(["eof", "eof", "eof"]))
    spool, _holder, ok = run_collector(
        monkeypatch, opener, timeout=6.0,
        until=lambda h, s: opener.drivers and len(opener.drivers[0].opens) >= 3)
    assert ok, "stream was not reopened within 6 s"
    assert len(opener.drivers) == 1, "a dropped stream must not trigger a full re-probe"
    opens = opener.drivers[0].opens
    assert opens[1] - opens[0] < 2.0 and opens[2] - opens[1] < 2.0
    assert len(spool.rows) >= 3


def test_read_timeout_reopens_without_a_reprobe(monkeypatch):
    monkeypatch.setattr(nec, "STREAM_STABLE_SECONDS", 0.0, raising=False)
    opener = Opener(lambda: StreamDriver(["timeout", "timeout"]))
    _spool, _holder, ok = run_collector(
        monkeypatch, opener, timeout=6.0,
        until=lambda h, s: opener.drivers and len(opener.drivers[0].opens) >= 2)
    assert ok, "stream was not reopened within 6 s after a read timeout"
    assert len(opener.drivers) == 1


def test_auth_failure_on_the_stream_keeps_the_lockout_backoff(monkeypatch):
    calls = []

    def wait(stop, cfg, auth_failures, last_gen, *args, **kwargs):
        calls.append((auth_failures, kwargs.get("seconds")))
        stop.set()
        return "stop", last_gen
    opener = Opener(lambda: StreamDriver(["401"]))
    run_collector(monkeypatch, opener, reconnect_wait=wait)
    assert calls == [(1, None)]


def test_failed_reopens_escalate_then_fall_back_to_a_full_reprobe(monkeypatch):
    calls = []

    def wait(stop, cfg, auth_failures, last_gen, *args, **kwargs):
        calls.append(kwargs.get("seconds"))
        return "timeout", last_gen
    opener = Opener(lambda: StreamDriver(["503"] * 20))
    _spool, _holder, ok = run_collector(
        monkeypatch, opener, reconnect_wait=wait, timeout=6.0,
        until=lambda h, s: len(opener.drivers) >= 2)
    assert ok, "exhausted reopens never fell back to the full re-probe path"
    fast = calls[:nec.STREAM_REOPEN_MAX_ATTEMPTS]
    assert all(isinstance(s, float) and 0 < s <= core.DRIVER_RETRY_SECONDS for s in fast)
    ceilings = [nec.STREAM_REOPEN_BASE_SECONDS * 2 ** n
                for n in range(1, nec.STREAM_REOPEN_MAX_ATTEMPTS + 1)]
    for delay, ceiling in zip(fast, ceilings):
        assert min(ceiling, core.DRIVER_RETRY_SECONDS) / 2 <= delay <= ceiling
    assert calls[nec.STREAM_REOPEN_MAX_ATTEMPTS] is None      # then the full retry wait
    assert len(opener.drivers[0].opens) == nec.STREAM_REOPEN_MAX_ATTEMPTS + 1


def test_credential_change_during_a_reopen_reopens_the_recorder_now(monkeypatch):
    calls = []

    def wait(stop, cfg, auth_failures, last_gen, *args, **kwargs):
        calls.append(kwargs.get("seconds"))
        if len(calls) == 1:
            return "reload", "gen-2"
        stop.set()
        return "stop", last_gen
    opener = Opener(lambda: StreamDriver(["503", "503"]))
    _spool, _holder, ok = run_collector(
        monkeypatch, opener, reconnect_wait=wait, timeout=6.0,
        until=lambda h, s: len(opener.drivers) >= 2)
    assert ok
    assert calls[0] is not None and len(calls) == 2


def test_driver_without_stream_reporting_keeps_the_full_retry_path(monkeypatch):
    class Legacy(StreamDriver):
        reports_stream_activity = False
    calls = []

    def wait(stop, cfg, auth_failures, last_gen, *args, **kwargs):
        calls.append(kwargs.get("seconds"))
        stop.set()
        return "stop", last_gen
    opener = Opener(lambda: Legacy(["eof"]))
    run_collector(monkeypatch, opener, reconnect_wait=wait)
    assert calls == [None]
    assert len(opener.drivers[0].opens) == 1


def test_reopen_delay_is_jittered_escalating_and_capped():
    for failures in range(0, 12):
        ceiling = min(float(core.DRIVER_RETRY_SECONDS),
                      nec.STREAM_REOPEN_BASE_SECONDS * 2 ** failures)
        for _ in range(20):
            delay = nec._stream_reopen_delay(failures)
            assert ceiling / 2 <= delay <= ceiling
    assert nec._stream_reopen_delay(0) <= 0.5


def test_reconnect_wait_honours_a_short_wait_and_a_credential_change(monkeypatch):
    stop = threading.Event()
    monkeypatch.setattr(core.credential_store, "credential_generation", lambda: "gen-1")
    started = time.monotonic()
    outcome, gen = core._reconnect_wait(stop, object(), 0, "gen-1", seconds=0.2)
    assert outcome == "timeout" and gen == "gen-1"
    assert time.monotonic() - started < 2.0

    class Cfg:
        reloaded = False

        def load_recorder_credential(self):
            Cfg.reloaded = True
    monkeypatch.setattr(core.credential_store, "credential_generation", lambda: "gen-2")
    outcome, gen = core._reconnect_wait(stop, Cfg(), 0, "gen-1", seconds=0.2)
    assert outcome == "reload" and gen == "gen-2" and Cfg.reloaded


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
