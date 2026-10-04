#!/usr/bin/env python3
"""ONVIF event clocks.

MNVR-024  device_ts is the notification's own UtcTime. WS-BaseNotification
          wraps the ONVIF tt:Message (which carries UtcTime) inside a
          wsnt:Message; with namespaces stripped both are "Message", and the
          first one found is the wrapper, which has no UtcTime, so the PC
          clock was used instead.
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
from drivers import onvif_driver  # noqa: E402

MOTION_ALARM = "tns1:VideoSource/MotionAlarm"
T0 = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)


class _WallClock(datetime):
    """onvif_driver.datetime with a settable now(), to step the PC clock."""

    current = T0

    @classmethod
    def now(cls, tz=None):
        return cls.current if tz is None else cls.current.astimezone(tz)


@pytest.fixture
def driver(monkeypatch):
    rec = fx.FakeRecorder()
    rec.install(monkeypatch)
    monkeypatch.setattr(onvif_driver, "datetime", _WallClock)
    _WallClock.current = T0
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
    _WallClock.current = wall
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


def test_fractional_and_offset_utc_time(driver):
    events = _feed(driver, 1, "2026-10-04T15:00:00.250+05:00", T0, 0)
    assert [e.device_ts for e in events] == [datetime(2026, 10, 4, 10, 0, 0, 250000,
                                                      tzinfo=timezone.utc)]


def test_unparseable_utc_time_falls_back_to_receive_time(driver):
    events = _feed(driver, 1, "not-a-time", T0 + timedelta(seconds=3), 0)
    assert [e.device_ts for e in events] == [T0 + timedelta(seconds=3)]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
