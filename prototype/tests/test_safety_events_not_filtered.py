#!/usr/bin/env python3
"""5.1.2: a fault or safety signal never depends on a person/vehicle being in frame.

The 5.1.1 collector took a still for every non-native camera event and dropped it when
the local model found no person or vehicle. A covered or blinded camera's tamper has no
person in it, so the tamper was usually discarded; so was a Dahua face event (not marked
native) and any code stored raw.

Now the local filter judges only detection types (motion, person, vehicle, line crossing,
intrusion, region entry/exit) the recorder did not classify itself. Tamper still takes a
still when it can (evidence of the covered lens) and is kept whatever it shows. Native
camera faults feed camera health: video loss / disconnect -> OFFLINE, tamper -> OFFLINE
(reason tamper), and the recorder's own restore / tamper end clears that fault.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_collector_harness import Info, run_collector, stop_after_first_wait  # noqa: E402
import watchlog_agent as core  # noqa: E402
from drivers.base import Event  # noqa: E402

WHEN = datetime(2026, 10, 7, 9, 0, 0, tzinfo=timezone.utc)
JPEG = b"\xff\xd8" + b"x" * 64


class EmptyFrameDetector:
    """The local model: it never sees a person or a vehicle."""
    model_name = "test-model"

    def __init__(self):
        self.judged = []

    def classify_event(self, raw):
        self.judged.append(raw)
        return (False, []) if raw else (True, None)


class Monitor:
    def __init__(self):
        self.calls = []

    def record_native_fault(self, channel):
        self.calls.append(("fault", channel))

    def record_native_tamper(self, channel):
        self.calls.append(("tamper", channel))

    def record_native_clear(self, channel, reason):
        self.calls.append(("clear", channel, reason))


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
        return JPEG

    def close(self):
        pass


def _ev(etype, channel="1", **payload):
    return Event(channel=channel, event_type=etype, device_ts=WHEN,
                 payload={"vendor": "dahua", **payload})


def _run(monkeypatch, events, monitor=None):
    driver = Driver(events)
    detector = EmptyFrameDetector()
    holder = {"monitor": monitor} if monitor is not None else {}
    spool, holder, _ok = run_collector(
        monkeypatch, lambda cfg: (driver, Info()), holder=holder, detector=detector,
        reconnect_wait=stop_after_first_wait([]))
    return spool, driver, detector, holder


@pytest.mark.parametrize("etype", sorted(core.SAFETY_EVENT_TYPES))
def test_every_safety_signal_is_exempt_from_the_local_filter(etype):
    assert core.local_ai_filter_applies(_ev(etype)) is False


@pytest.mark.parametrize("etype", ["disk_error", "disk_full", "disk_smart_warning"])
def test_every_disk_signal_is_exempt(etype):
    assert core.local_ai_filter_applies(_ev(etype, channel=None)) is False


def test_only_non_native_detections_are_judged():
    assert core.local_ai_filter_applies(_ev("motion")) is True
    assert core.local_ai_filter_applies(_ev("person")) is True
    assert core.local_ai_filter_applies(_ev("line_crossing")) is True
    assert core.local_ai_filter_applies(_ev("line_crossing", native_ai=True)) is False
    assert core.local_ai_filter_applies(_ev("intrusion", native_ai=True)) is False
    # A code stored raw is an unknown recorder signal: kept, never judged.
    assert core.local_ai_filter_applies(_ev("videounfocus")) is False
    assert core.local_ai_filter_applies(_ev("defocus")) is False


def test_tamper_is_kept_with_its_still_when_nobody_is_in_frame(monkeypatch):
    spool, driver, detector, _ = _run(monkeypatch, [
        _ev("tamper", "3"),
        _ev("motion", "4"),          # plain motion with nobody in frame: still discarded
    ])
    kept = [(r["event_type"], r["channel"]) for r in spool.rows]
    assert kept == [("tamper", "3")]
    assert spool.rows[0].get("snapshot_b64"), "tamper keeps the still of the covered lens"
    assert "3" in driver.snapshots
    assert detector.judged == [JPEG], "only the motion still was judged"


@pytest.mark.parametrize("etype,channel", [
    ("tamper_end", "1"), ("video_restore", "1"), ("alarm_input", None),
    ("alarm_input_end", None), ("camera_disconnect", "2"), ("camera_reconnect", "2"),
    ("recorder_restart", None), ("face", "1"), ("object_left", "1"),
    ("object_removed", "1"), ("loginfailure", None), ("videounfocus", "5"),
])
def test_safety_and_raw_signals_are_never_discarded(monkeypatch, etype, channel):
    spool, _driver, detector, _ = _run(monkeypatch, [_ev(etype, channel)])
    assert [r["event_type"] for r in spool.rows] == [etype]
    assert detector.judged == []


def test_no_still_is_taken_for_a_disconnected_camera(monkeypatch):
    _spool, driver, _det, _ = _run(monkeypatch, [_ev("camera_disconnect", "6"),
                                                 _ev("video_loss", "6")])
    assert driver.snapshots == []


def test_native_faults_feed_camera_health(monkeypatch):
    monitor = Monitor()
    _run(monkeypatch, [
        _ev("video_loss", "1"), _ev("video_restore", "1"),
        _ev("tamper", "2"), _ev("tamper_end", "2"),
        _ev("camera_disconnect", "3"), _ev("camera_reconnect", "3"),
        _ev("alarm_input", None), _ev("motion", "4"),
    ], monitor=monitor)
    assert monitor.calls == [
        ("fault", "1"), ("clear", "1", "video_loss"),
        ("tamper", "2"), ("clear", "2", "tamper"),
        ("fault", "3"), ("clear", "3", "video_loss"),
    ]


def test_collector_publishes_its_spool_for_health_derived_events(monkeypatch):
    _spool, _driver, _det, holder = _run(monkeypatch, [_ev("motion", "1")])
    assert holder.get("event_spool") is not None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
