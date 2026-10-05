#!/usr/bin/env python3
"""ONVIF events carry the same provenance keys as the Hikvision and Dahua drivers.

Integration of the 5.0.28 driver work: Hikvision and Dahua events name the clock that stamped
device_ts (payload.clock_source = recorder / recorder_local / agent_receive) and flag a
recorder-level event with payload.recorder_scoped. The ONVIF driver used its own key,
recorder_scope, and named no clock at all, so an ONVIF event stamped with the recorder's
UtcTime could not be told from one stamped with the PC's receive time (an event without a
UtcTime carries no skew flag either). Anything that has to place the event in recorder footage
needs that difference.
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
STORAGE = "tns1:Device/HardwareFailure/StorageFailure"
T0 = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def driver(monkeypatch):
    rec = fx.FakeRecorder()
    rec.install(monkeypatch)
    fx.WallClock.install(monkeypatch, T0)
    drv, _info = core.open_driver(fx.FakeCfg())
    drv.recorder = rec
    yield drv
    drv.close()


def _motion(driver, utc):
    driver.recorder.queue(fx.notification(MOTION_ALARM, utc, {"Source": fx.source_token(2)},
                                          {"State": "true"}))
    return fx.stream(driver, driver.recorder)


def test_a_trusted_recorder_stamp_is_named_as_the_recorder_clock(driver):
    [ev] = _motion(driver, fx.iso(T0 + timedelta(seconds=40)))
    assert ev.device_ts == T0 + timedelta(seconds=40)
    assert ev.payload["clock_source"] == "recorder"


def test_a_skewed_recorder_stamp_falls_back_to_the_receive_clock(driver):
    [ev] = _motion(driver, fx.iso(T0 + timedelta(hours=5)))
    assert ev.device_ts == T0
    assert ev.payload["clock_source"] == "agent_receive"
    assert ev.payload["clock_skew_s"] == 5 * 3600


def test_an_event_without_a_readable_stamp_is_named_as_the_receive_clock(driver):
    [ev] = _motion(driver, "not-a-time")
    assert ev.device_ts == T0
    assert ev.payload["clock_source"] == "agent_receive"
    assert "clock_skew_s" not in ev.payload


def test_recorder_scoped_flag_matches_the_other_drivers(driver):
    driver.recorder.queue(fx.notification(STORAGE, fx.iso(T0), {}, {"Failed": "true"}))
    [ev] = fx.stream(driver, driver.recorder)
    assert ev.channel is None
    assert ev.payload["recorder_scoped"] is True
    assert "recorder_scope" not in ev.payload


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
