#!/usr/bin/env python3
"""MNVR-035 (dahua_archive part): the clip window respects which clock stamped the event.

Incident windows are device_ts -10 s / +20 s. Dahua CGI events carry the AGENT's receive time, so
the window must move onto recorder wall time by the measured offset, drift included, unrounded.
Times the RECORDER's own clock stamped (ONVIF UtcTime, archive segment times) already contain the
drift, so they move by the recorder's zone only. Before the fix the offset was rounded to the
minute for both: a recorder 25 s fast lost the event off the end of the window, and a recorder
45 s fast had its drift counted twice.

Field-only (IMPLEMENTED_UNVERIFIED): how far a given site's recorder actually drifts, and whether
loadfile trims to the exact window.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dahua_fake_recorder import FakeDahua, FakeRecorder, continuous_files, local, pinned_datetime  # noqa: E402
import dahua_archive as da  # noqa: E402
from drivers.base import DriverError  # noqa: E402

PC_NOW = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)
EVENT = datetime(2026, 10, 4, 8, 0, tzinfo=timezone.utc)       # true UTC instant of the incident
FILES = continuous_files(local("2026-10-04 12:00:00"), local("2026-10-04 14:00:00"))
BEFORE, AFTER = timedelta(seconds=10), timedelta(seconds=20)


def clip_window(drift_seconds, device_ts, *, pc_now=PC_NOW, **kwargs):
    rec = FakeRecorder(pc_now, drift=timedelta(seconds=drift_seconds), files=FILES)
    with mock.patch.object(da, "datetime", pinned_datetime(pc_now)):
        da.get_clip(FakeDahua(rec), "1", device_ts - BEFORE, device_ts + AFTER, **kwargs)
    return rec, rec.loadfile_windows()[-1]


class AgentStampedEvents(unittest.TestCase):
    """Dahua CGI device_ts is the agent's receive time (true UTC, assuming an NTP-synced PC)."""

    def assert_contains(self, window, recorded_at):
        start, end = window
        self.assertLessEqual(start, recorded_at, f"window {window} starts after the event")
        self.assertGreaterEqual(end, recorded_at, f"window {window} ends before the event")

    def test_recorder_25s_fast_keeps_the_event_in_the_window(self):
        _rec, window = clip_window(25, EVENT)
        self.assert_contains(window, local("2026-10-04 13:00:25"))
        self.assertEqual(window, (local("2026-10-04 13:00:15"), local("2026-10-04 13:00:45")))

    def test_recorder_15s_slow_keeps_the_event_in_the_window(self):
        _rec, window = clip_window(-15, EVENT)
        self.assert_contains(window, local("2026-10-04 12:59:45"))
        self.assertEqual(window, (local("2026-10-04 12:59:35"), local("2026-10-04 13:00:05")))

    def test_recorder_45s_fast_is_not_rounded_to_a_minute(self):
        _rec, window = clip_window(45, EVENT)
        self.assertEqual(window, (local("2026-10-04 13:00:35"), local("2026-10-04 13:01:05")))

    def test_sub_second_offset_never_narrows_the_window(self):
        # The recorder prints whole seconds: at agent 10:00:00.6 it reads 15:00:00.
        pc_now = PC_NOW + timedelta(milliseconds=600)
        _rec, (start, end) = clip_window(0, EVENT, pc_now=pc_now)
        self.assertLessEqual(start, local("2026-10-04 12:59:50"))
        self.assertGreaterEqual(end, local("2026-10-04 13:00:20"))

    def test_one_clock_reading_serves_search_and_download(self):
        rec, _window = clip_window(25, EVENT)
        self.assertEqual(len(rec.calls_to("global.cgi")), 1)
        find = rec.calls_to("mediaFileFind.cgi", "findFile")[0][1]
        load = rec.calls_to("loadfile.cgi", "startLoad")[0][1]
        self.assertEqual((find["condition.StartTime"], find["condition.EndTime"]),
                         (load["startTime"], load["endTime"]))


class RecorderStampedEvents(unittest.TestCase):
    """ONVIF UtcTime and archive segment times come from the recorder's own (drifting) clock."""

    def test_recorder_clock_window_does_not_add_the_drift_twice(self):
        # Recorder 45 s fast: its UtcTime for the incident reads 08:00:45Z and its recording of the
        # same instant is stamped 13:00:45 local.
        _rec, window = clip_window(45, EVENT + timedelta(seconds=45), clock="recorder")
        self.assertEqual(window, (local("2026-10-04 13:00:35"), local("2026-10-04 13:01:05")))

    def test_unknown_clock_source_is_refused_before_any_download(self):
        rec = FakeRecorder(PC_NOW, files=FILES)
        with mock.patch.object(da, "datetime", pinned_datetime(PC_NOW)):
            with self.assertRaises(DriverError):
                da.get_clip(FakeDahua(rec), "1", EVENT - BEFORE, EVENT + AFTER, clock="pc")
        self.assertEqual(rec.calls_to("loadfile.cgi"), [])

    def test_recorder_clock_refused_when_zone_cannot_be_told_from_drift(self):
        rec = FakeRecorder(PC_NOW, drift=timedelta(minutes=6), files=FILES)
        with mock.patch.object(da, "datetime", pinned_datetime(PC_NOW)):
            with self.assertRaises(DriverError):
                da.get_clip(FakeDahua(rec), "1", EVENT - BEFORE, EVENT + AFTER, clock="recorder")
        self.assertEqual(rec.calls_to("loadfile.cgi"), [])

    def recorder_clock_clip(self, zone, drift, files):
        rec = FakeRecorder(PC_NOW, zone=zone, drift=drift, files=files)
        with mock.patch.object(da, "datetime", pinned_datetime(PC_NOW)):
            da.get_clip(FakeDahua(rec), "1", EVENT + drift - BEFORE, EVENT + drift + AFTER,
                        clock="recorder")
        return rec

    def test_recorder_clock_is_never_given_a_zone_no_clock_uses(self):
        # A UTC recorder 12 min fast is 3 min from "+00:15", which no civil clock uses. Taking it
        # as the zone would put the recorder-stamped incident window 15 min off the event.
        files = continuous_files(local("2026-10-04 07:00:00"), local("2026-10-04 09:00:00"))
        with self.assertRaises(DriverError):
            rec = self.recorder_clock_clip(timedelta(0), timedelta(minutes=12), files)
            self.fail(f"downloaded {rec.loadfile_windows()} with an invented zone")

    def test_recorder_clock_refusal_does_not_come_back_with_more_drift(self):
        # UTC+5: once 6 min of drift is refused, every larger drift short of the next civil offset
        # (+05:30, 30 min away) is refused too, instead of snapping to a "+05:15".
        for minutes in [m for m in range(-24, 25) if abs(m) >= 6]:
            with self.subTest(drift_minutes=minutes):
                with self.assertRaises(DriverError):
                    rec = self.recorder_clock_clip(timedelta(hours=5), timedelta(minutes=minutes), FILES)
                    self.fail(f"downloaded {rec.loadfile_windows()} with an invented zone")

    def test_recorder_clock_in_a_quarter_hour_zone_is_exact(self):
        # UTC+05:45 is a civil zone: a recorder there 45 s fast still gets its own window.
        files = continuous_files(local("2026-10-04 13:00:00"), local("2026-10-04 15:00:00"))
        rec = self.recorder_clock_clip(timedelta(hours=5, minutes=45), timedelta(seconds=45), files)
        self.assertEqual(rec.loadfile_windows()[-1],
                         (local("2026-10-04 13:45:35"), local("2026-10-04 13:46:05")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
