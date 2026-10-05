#!/usr/bin/env python3
"""MNVR-023 (Hikvision): the burst filter is timed on the agent's receive clock.

The filter compared the recorder's dateTime with the last emitted one and dropped anything
less than 30 s later, with no lower bound. After the recorder clock stepped backwards (NTP
correcting a fast clock, a manual set) every later event of that (channel, type) was
silently dropped until the recorder clock caught up. Repeats are now collapsed on the
monotonic time the alert ARRIVED, which never steps.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))

from drivers.native_recorder import NativeHikvisionDriver  # noqa: E402

WALL0 = datetime(2026, 10, 4, 16, 0, 0, tzinfo=timezone.utc)


def _alert(device_time: str, channel: str = "1", etype: str = "VMD") -> bytes:
    return f"""<EventNotificationAlert>
      <eventType>{etype}</eventType><eventState>active</eventState>
      <channelID>{channel}</channelID><dateTime>{device_time}</dateTime>
      <activePostCount>1</activePostCount>
    </EventNotificationAlert>""".encode()


def _at(driver, seconds: float, raw: bytes):
    """Parse `raw` as if its bytes arrived `seconds` after the first alert."""
    driver._received = (1000.0 + seconds, WALL0 + timedelta(seconds=seconds))
    return driver._parse_alert(raw)


def test_backward_recorder_clock_step_does_not_suppress_events():
    d = NativeHikvisionDriver("http://127.0.0.1", "u", "p", timeout=1)
    try:
        assert _at(d, 0, _alert("2026-10-04T21:00:00+05:00")) is not None
        # NTP pulls the recorder clock back 10 minutes; a new alarm 40 s later is real.
        later = _at(d, 40, _alert("2026-10-04T20:50:30+05:00"))
        assert later is not None
        assert later.device_ts == datetime(2026, 10, 4, 15, 50, 30, tzinfo=timezone.utc)
        # Another channel/type is independent, and a second step back is still kept.
        assert _at(d, 80, _alert("2026-10-04T20:40:00+05:00")) is not None
    finally:
        d.close()


def test_repeats_inside_the_window_still_collapse_on_receive_time():
    d = NativeHikvisionDriver("http://127.0.0.1", "u", "p", timeout=1)
    try:
        assert _at(d, 0, _alert("2026-10-04T21:00:00+05:00")) is not None
        assert _at(d, 1, _alert("2026-10-04T21:00:01+05:00")) is None
        assert _at(d, 29, _alert("2026-10-04T21:00:29+05:00")) is None
        assert _at(d, 31, _alert("2026-10-04T21:00:31+05:00")) is not None
        assert _at(d, 32, _alert("2026-10-04T21:00:32+05:00", channel="2")) is not None
    finally:
        d.close()


def test_forward_recorder_clock_jump_does_not_split_one_burst():
    # The recorder clock jumps forward an hour mid-alarm; the 1 s repeat is still a repeat.
    d = NativeHikvisionDriver("http://127.0.0.1", "u", "p", timeout=1)
    try:
        assert _at(d, 0, _alert("2026-10-04T21:00:00+05:00")) is not None
        assert _at(d, 1, _alert("2026-10-04T22:00:01+05:00")) is None
    finally:
        d.close()


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
