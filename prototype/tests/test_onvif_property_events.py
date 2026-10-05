#!/usr/bin/env python3
"""ONVIF property events: state is not an occurrence, and recorder faults are
not camera faults.

MNVR-027  PropertyOperation=Initialized reports CURRENT state on every new
          subscription (and so on every resubscribe); it is not something that
          just happened. A boolean false (StorageFailure Failed=false) is a
          cleared state, not a disk_error.
MNVR-028  Storage topics are recorder-scoped: channel None plus a flag, never
          a camera channel.
"""
from __future__ import annotations

import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

import onvif_fake_recorder as fx  # noqa: E402
import watchlog_agent as core  # noqa: E402
import native_event_collector  # noqa: E402

MOTION_RULE = "tns1:RuleEngine/CellMotionDetector/Motion"
DARK = "tns1:VideoSource/ImageTooDark/ImagingService"
TAMPER = "tns1:RuleEngine/TamperDetector/Tamper"
LINE = "tns1:RuleEngine/LineDetector/Crossed"
STORAGE = "tns1:Device/HardwareFailure/StorageFailure"


@pytest.fixture
def driver(monkeypatch):
    rec = fx.FakeRecorder()
    rec.install(monkeypatch)
    # The PC clock agrees with the recorder's, so its stamps are trusted.
    fx.WallClock.install(monkeypatch, rec.device_now)
    drv, _info = core.open_driver(fx.FakeCfg())
    drv.recorder = rec
    yield drv
    drv.close()


def _events(driver, *messages):
    # Each call is a separate pull; distinct cameras keep the burst filter out
    # of the way unless a test means to exercise it.
    for message in messages:
        driver.recorder.queue(message)
    return fx.stream(driver, driver.recorder)


def _motion(cam, operation, utc="2026-10-04T10:00:00Z", state="true"):
    return fx.notification(MOTION_RULE, utc,
                           {"VideoSourceConfigurationToken": fx.config_token(cam)},
                           {"IsMotion": state}, operation=operation)


def _storage(failed, operation="Changed"):
    return fx.notification(STORAGE, "2026-10-04T10:00:00Z", {"Token": "Storage_001"},
                           {"Failed": failed}, operation=operation)


def test_initialized_state_is_not_an_occurrence(driver):
    events = _events(driver,
                     _motion(1, "Initialized"),
                     fx.notification(DARK, "2026-10-04T10:00:00Z",
                                     {"Source": fx.source_token(2)}, {"State": "true"},
                                     operation="Initialized"),
                     _storage("true", operation="Initialized"))
    assert events == []


def test_resubscribe_does_not_re_emit_initialized_state(driver):
    # The camera is already in motion when the subscription starts, then a real
    # Changed edge. Eight minutes later a new subscription (a reconnect) gets
    # the same current state reported again as Initialized.
    clock = fx.FakeClock()
    driver._monotonic = clock
    fx.WallClock.current = datetime(2026, 10, 4, 10, 5, tzinfo=timezone.utc)
    first = _events(driver,
                    _motion(1, "Initialized"),
                    _motion(1, "Changed", utc="2026-10-04T10:05:00Z"))
    clock.advance(480)
    fx.WallClock.current = datetime(2026, 10, 4, 10, 13, tzinfo=timezone.utc)
    second = _events(driver, _motion(1, "Initialized", utc="2026-10-04T10:13:00Z"))

    assert [(e.channel, e.device_ts) for e in first] == [
        ("1", datetime(2026, 10, 4, 10, 5, tzinfo=timezone.utc))]
    assert second == []
    assert driver.recorder.subscriptions == 2


def test_deleted_property_is_not_an_occurrence(driver):
    assert _events(driver, _motion(1, "Deleted")) == []


def test_changed_and_plain_events_are_occurrences(driver):
    events = _events(driver, _motion(1, "Changed"), _motion(2, None))
    assert [(e.channel, e.event_type) for e in events] == [("1", "motion"), ("2", "motion")]


def test_storage_failure_false_is_cleared_state(driver):
    assert _events(driver, _storage("false")) == []


def test_storage_failure_is_recorder_scoped_never_a_camera_channel(driver):
    events = _events(driver, _storage("true"))
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "disk_error"
    assert ev.channel is None
    assert ev.payload.get("recorder_scoped") is True
    assert driver.dropped_unmapped == 0


def test_storage_topic_without_state_is_recorder_scoped(driver):
    events = _events(driver, fx.notification(STORAGE, "2026-10-04T10:00:00Z",
                                             {}, {}, operation=None))
    assert [(e.channel, e.event_type, e.payload.get("recorder_scoped")) for e in events] == [
        (None, "disk_error", True)]


def test_collector_keeps_a_storage_fault_off_every_camera(monkeypatch):
    class Spool:
        def __init__(self):
            self.rows = []

        def add(self, row):
            self.rows.append(row)

        def trim(self):
            return 0

    rec = fx.FakeRecorder()
    rec.install(monkeypatch)
    monkeypatch.setattr(core.credential_store, "credential_generation", lambda: "absent")
    rec.queue(_storage("true"))
    spool = Spool()
    rec.stop = threading.Event()
    native_event_collector.collector(fx.FakeCfg(), spool, rec.stop)

    assert len(spool.rows) == 1
    row = spool.rows[0]
    assert row["event_type"] == "disk_error"
    assert row["channel"] is None          # JSON null: no camera, never the string "None"
    assert row["payload"]["recorder_scoped"] is True
    assert "snapshot_b64" not in row
    assert rec.calls_of("GetSnapshotUri") == []


def test_generic_boolean_false_is_cleared(driver):
    events = _events(driver, fx.notification(TAMPER, "2026-10-04T10:00:00Z",
                                             {"VideoSourceConfigurationToken": fx.config_token(3)},
                                             {"IsTamper": "false"}))
    assert events == []


def test_numeric_zero_is_not_a_cleared_state(driver):
    # ObjectId=0 is an identifier, not a boolean: the crossing still happened.
    events = _events(driver, fx.notification(LINE, "2026-10-04T10:00:00Z",
                                             {"VideoSourceConfigurationToken": fx.config_token(4)},
                                             {"ObjectId": "0"}))
    assert [(e.channel, e.event_type) for e in events] == [("4", "line_crossing")]


def test_falling_edge_still_dropped(driver):
    assert _events(driver, _motion(1, "Changed", state="false")) == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
