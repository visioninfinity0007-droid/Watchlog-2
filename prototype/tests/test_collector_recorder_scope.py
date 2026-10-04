#!/usr/bin/env python3
"""MNVR-028 (collector): a recorder-scoped event reaches the spool with no camera.

Event.to_json writes str(channel), so a recorder-scoped event (channel None) would be
spooled as channel "None", and the collector would fetch a still for "camera None" and
feed a channel-less video loss to the camera health monitor. The packaged collector now
spools JSON null, so wl_ingest_events joins no camera, and skips camera-only work.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_collector_harness import Info, run_collector, stop_after_first_wait  # noqa: E402
from drivers.base import Event  # noqa: E402

WHEN = datetime(2026, 10, 4, 16, 0, 0, tzinfo=timezone.utc)


class Monitor:
    def __init__(self):
        self.faults = []

    def record_native_fault(self, channel):
        self.faults.append(channel)


class Driver:
    name = "fake"
    verified_against_hardware = True

    def __init__(self, events):
        self.events = events
        self.snapshots = []

    def stream_events(self, stop):
        yield from self.events

    def get_snapshot(self, channel):
        self.snapshots.append(channel)
        return None

    def close(self):
        pass


def test_recorder_scoped_and_channel_less_events_spool_with_null_channel(monkeypatch):
    driver = Driver([
        Event(channel=None, event_type="alarm_input", device_ts=WHEN,
              payload={"vendor": "dahua", "recorder_scoped": True, "native_index": "3"}),
        Event(channel=None, event_type="video_loss", device_ts=WHEN,
              payload={"vendor": "hikvision", "channel_unknown": True}),
        Event(channel="2", event_type="motion", device_ts=WHEN, payload={"vendor": "dahua"}),
    ])
    monitor = Monitor()
    waits = []
    spool, _holder, _ok = run_collector(
        monkeypatch, lambda cfg: (driver, Info()), holder={"monitor": monitor},
        reconnect_wait=stop_after_first_wait(waits))
    channels = [row["channel"] for row in spool.rows]
    assert channels == [None, None, "2"]
    assert spool.rows[0]["payload"]["recorder_scoped"] is True
    # No still is fetched for a recorder-scoped or channel-less event, only for camera 2.
    assert driver.snapshots == ["2"]
    # A channel-less video loss cannot mark any camera offline.
    assert monitor.faults == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
