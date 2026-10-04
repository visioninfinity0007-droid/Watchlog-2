#!/usr/bin/env python3
"""MNVR-028 (Dahua): disk and alarm-input events are recorder-scoped, not camera events.

The attach parser turned index into channel index+1 for every code. For StorageNotExist,
StorageFailure, StorageLowSpace and AlarmLocal the index names a disk or an alarm input, so
an HDD fault was stored as a camera_fault on camera 1 and alarm input 3 as an event (with a
still) on camera 4. Those codes now carry channel None plus recorder_scoped, and a camera
code without a usable index has channel None plus channel_unknown instead of camera 1.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))

from drivers.native_recorder import NativeDahuaDriver  # noqa: E402

WALL0 = datetime(2026, 10, 4, 16, 0, 0, tzinfo=timezone.utc)


def _driver():
    return NativeDahuaDriver("http://127.0.0.1", "admin", "x", timeout=1)


def _at(d, seconds, line):
    d._received = (1000.0 + seconds, WALL0 + timedelta(seconds=seconds))
    return d._parse_line(line)


def test_storage_and_alarm_input_codes_are_recorder_scoped():
    d = _driver()
    try:
        cases = [("Code=StorageLowSpace;action=Start;index=0", "disk_full", "0"),
                 ("Code=StorageFailure;action=Start;index=1", "disk_error", "1"),
                 ("Code=StorageNotExist;action=Pulse;index=0", "disk_error", "0"),
                 ("Code=AlarmLocal;action=Start;index=3", "alarm_input", "3")]
        for i, (line, etype, index) in enumerate(cases):
            ev = _at(d, i * 40, line)
            assert ev is not None, line
            assert ev.channel is None, line
            assert ev.event_type == etype
            assert ev.payload["recorder_scoped"] is True
            assert ev.payload["native_index"] == index
    finally:
        d.close()


def test_recorder_scoped_bursts_collapse_per_disk_not_across_disks():
    d = _driver()
    try:
        assert _at(d, 0, "Code=StorageLowSpace;action=Start;index=0") is not None
        assert _at(d, 1, "Code=StorageLowSpace;action=Start;index=1") is not None
        assert _at(d, 2, "Code=StorageLowSpace;action=Pulse;index=0") is None
    finally:
        d.close()


def test_camera_codes_keep_index_plus_one_and_never_guess_camera_1():
    d = _driver()
    try:
        ev = _at(d, 0, "Code=VideoMotion;action=Start;index=1")
        assert ev.channel == "2"
        assert "recorder_scoped" not in ev.payload
        ev = _at(d, 1, "Code=VideoLoss;action=Start")
        assert ev.channel is None
        assert ev.payload["channel_unknown"] is True
    finally:
        d.close()


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
