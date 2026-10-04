#!/usr/bin/env python3
"""ONVIF event clocks.

MNVR-024  device_ts is the notification's own UtcTime. WS-BaseNotification
          wraps the ONVIF tt:Message (which carries UtcTime) inside a
          wsnt:Message; with namespaces stripped both are "Message", and the
          first one found is the wrapper, which has no UtcTime, so the PC
          clock was used instead. The stamp is trusted only while the
          recorder clock agrees with the PC's: a clock reset by a power loss,
          or local time sent as UTC, would move every event by its error, so
          beyond the tolerance the receive time is used and the stamp and the
          offset are kept in the payload.
MNVR-023  The burst filter runs on receive-time monotonic seconds. A
          wall-clock comparison has no lower bound: when the PC clock (or the
          recorder clock) steps backwards, (ts - last) is negative, passes
          "< 30 s", and every event of that (channel, type) is dropped
          silently until the clock catches up. Repeats are a property of when
          WE received them, so the filter must not depend on either clock.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

import onvif_fake_recorder as fx  # noqa: E402
import watchlog_agent as core  # noqa: E402

MOTION_ALARM = "tns1:VideoSource/MotionAlarm"
T0 = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def driver(monkeypatch):
    rec = fx.FakeRecorder()
    rec.install(monkeypatch)
    fx.WallClock.install(monkeypatch, T0)
    drv, _info = core.open_driver(fx.FakeCfg())
    drv.recorder = rec
    drv.clock = fx.FakeClock()
    drv._monotonic = drv.clock
    yield drv
    drv.close()


def _feed(driver, cam, device_utc, wall, after_seconds):
    """One motion notification received `after_seconds` (monotonic) after the
    previous one, while the PC wall clock reads `wall`."""
    driver.clock.advance(after_seconds)
    fx.WallClock.current = wall
    utc = device_utc if isinstance(device_utc, str) else fx.iso(device_utc)
    driver.recorder.queue(fx.notification(MOTION_ALARM, utc,
                                          {"Source": fx.source_token(cam)},
                                          {"State": "true"}))
    return fx.stream(driver, driver.recorder)


def test_device_ts_is_the_inner_messages_utc_time(driver):
    # The recorder stamped the event 79 s before the PC received it.
    stamped = T0 - timedelta(seconds=79)
    events = _feed(driver, 7, stamped, T0, 0)
    assert [(e.channel, e.device_ts) for e in events] == [("7", stamped)]
    assert "clock_skew_s" not in events[0].payload
    assert "device_utc" not in events[0].payload


def test_reset_recorder_clock_is_not_trusted(driver):
    # A power loss reset the recorder clock. Its stamp would move the event by
    # 26 years; the receive time is used and the stamp is kept, flagged.
    events = _feed(driver, 4, "2000-01-01T00:00:00Z", T0, 0)
    reset = datetime(2000, 1, 1, tzinfo=timezone.utc)
    assert [(e.channel, e.device_ts) for e in events] == [("4", T0)]
    assert events[0].payload["device_utc"] == "2000-01-01T00:00:00Z"
    assert events[0].payload["clock_skew_s"] == round((reset - T0).total_seconds())


def test_recorder_clock_five_hours_off_is_not_trusted(driver):
    # Local time (UTC+5) written as UTC: every stamp is 5 h in the future.
    events = _feed(driver, 3, T0 + timedelta(hours=5), T0, 0)
    assert [(e.channel, e.device_ts) for e in events] == [("3", T0)]
    assert events[0].payload["device_utc"] == "2026-10-04T15:00:00Z"
    assert events[0].payload["clock_skew_s"] == 5 * 3600


def test_skewed_recorder_clock_is_logged_not_silent(driver):
    lines = []
    driver.log = lines.append
    _feed(driver, 1, T0 + timedelta(hours=5), T0, 0)
    _feed(driver, 2, T0 + timedelta(hours=5), T0, 0)
    # Reported once, not once per event; no recorder address in the line.
    assert len(lines) == 1
    assert "+18000" in lines[0]
    assert fx.FakeCfg.nvr_url.split("//")[1] not in lines[0]


def test_fractional_and_offset_utc_time(driver):
    events = _feed(driver, 1, "2026-10-04T15:00:00.250+05:00", T0, 0)
    assert [e.device_ts for e in events] == [datetime(2026, 10, 4, 10, 0, 0, 250000,
                                                      tzinfo=timezone.utc)]


def test_unparseable_utc_time_falls_back_to_receive_time(driver):
    events = _feed(driver, 1, "not-a-time", T0 + timedelta(seconds=3), 0)
    assert [e.device_ts for e in events] == [T0 + timedelta(seconds=3)]


def test_backward_clock_step_does_not_suppress_later_events(driver):
    assert len(_feed(driver, 2, T0, T0, 0)) == 1
    # NTP steps both clocks back 15 minutes; 40 s really elapsed.
    stepped = T0 - timedelta(minutes=15)
    events = _feed(driver, 2, stepped, stepped, 40)
    assert [(e.channel, e.device_ts) for e in events] == [("2", stepped)]
    # And the next one 40 s later still gets through.
    later = stepped + timedelta(seconds=40)
    assert len(_feed(driver, 2, later, later, 40)) == 1


def test_repeat_inside_the_window_is_collapsed_whatever_the_clocks_say(driver):
    assert len(_feed(driver, 5, T0, T0, 0)) == 1
    # Only 10 s really elapsed, although both clocks jumped an hour.
    jumped = T0 + timedelta(hours=1)
    assert _feed(driver, 5, jumped, jumped, 10) == []


def test_burst_key_is_the_resolved_camera(driver):
    assert len(_feed(driver, 5, T0, T0, 0)) == 1
    assert len(_feed(driver, 6, T0 + timedelta(seconds=5), T0 + timedelta(seconds=5), 5)) == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
