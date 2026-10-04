#!/usr/bin/env python3
"""MNVR-023 (Dahua): the attach burst filter is timed on a monotonic receive clock.

Dahua stamps live events with the PC wall clock and compared consecutive wall-clock stamps
with no lower bound, so a backward PC clock step (Windows time sync correcting a fast
clock) dropped every later event of that (channel, code) until the clock caught up.
Repeats are now collapsed on the monotonic arrival time; device_ts is the wall-clock
arrival time of that same block.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))

from drivers.native_recorder import NativeDahuaDriver  # noqa: E402

WALL0 = datetime(2026, 10, 4, 16, 0, 0, tzinfo=timezone.utc)
MOTION = "Code=VideoMotion;action=Start;index=0"


def _at(driver, mono_seconds: float, wall: datetime, line: str = MOTION):
    driver._received = (1000.0 + mono_seconds, wall)
    return driver._parse_line(line)


def test_backward_pc_clock_step_does_not_suppress_events():
    d = NativeDahuaDriver("http://127.0.0.1", "admin", "x", timeout=1)
    try:
        first = _at(d, 0, WALL0)
        assert first is not None and first.device_ts == WALL0
        # Windows pulls the clock back 10 minutes; 40 s later a new motion is real.
        stepped = WALL0 + timedelta(seconds=40) - timedelta(minutes=10)
        later = _at(d, 40, stepped)
        assert later is not None
        assert later.device_ts == stepped
    finally:
        d.close()


def test_repeats_inside_the_window_still_collapse():
    d = NativeDahuaDriver("http://127.0.0.1", "admin", "x", timeout=1)
    try:
        assert _at(d, 0, WALL0) is not None
        assert _at(d, 5, WALL0 + timedelta(seconds=5)) is None
        assert _at(d, 31, WALL0 + timedelta(seconds=31)) is not None
        other = "Code=VideoMotion;action=Start;index=3"
        assert _at(d, 32, WALL0 + timedelta(seconds=32), other) is not None
    finally:
        d.close()


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
