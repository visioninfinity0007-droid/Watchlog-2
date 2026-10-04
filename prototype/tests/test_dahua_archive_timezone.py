#!/usr/bin/env python3
"""MNVR-019: Dahua archive segment times are recorder-local wall time, never UTC.

A UTC+5 recorder misses 07:30Z-08:30Z. mediaFileFind is asked for 12:30-13:30 local and answers
with recorder-local StartTime/EndTime. Those times must come back as UTC (13:00 local -> 08:00Z) so
backfill device_ts, visual-recovery sampling and archive-scan replay all land on the gap footage.
Before the fix the naive local strings were read as UTC: the segment that starts 13:00 local was
stored as 13:00Z, frames were requested at 18:00 local, and the interval was marked recovered
without any gap footage being examined.

Hardware boundary: whether a real DH-XVR1B08-I reports mediaFileFind times in local wall time is
field-only (IMPLEMENTED_UNVERIFIED); this test pins the module's working model with a fake.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dahua_fake_recorder import (  # noqa: E402
    JPEG, FakeCloud, FakeDahua, FakeRecorder, continuous_files, local, pinned_datetime)
import backfill  # noqa: E402
import dahua_archive as da  # noqa: E402
import recovery  # noqa: E402
import recovery_ai  # noqa: E402

PC_NOW = datetime(2026, 10, 4, 20, 0, tzinfo=timezone.utc)        # the gap is 11+ hours old
G0 = datetime(2026, 10, 4, 7, 30, tzinfo=timezone.utc)
G1 = datetime(2026, 10, 4, 8, 30, tzinfo=timezone.utc)
GAP_LOCAL = (local("2026-10-04 12:30:00"), local("2026-10-04 13:30:00"))
# Continuous recording all day, so footage from the WRONG window would also decode.
FILES = continuous_files(local("2026-10-04 00:00:00"), local("2026-10-05 01:00:00"))


def utc(hour, minute=0):
    return datetime(2026, 10, 4, hour, minute, tzinfo=timezone.utc)


def aware(text):
    parsed = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise AssertionError(f"archive time {text!r} is naive; consumers would read it as UTC")
    return parsed.astimezone(timezone.utc)


def decode(_clip):
    return JPEG


class Utc5Recorder(unittest.TestCase):
    def setUp(self):
        da.install()
        self.rec = FakeRecorder(PC_NOW, files=FILES)
        self.drv = FakeDahua(self.rec)
        patcher = mock.patch.object(da, "datetime", pinned_datetime(PC_NOW))
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_inside_gap(self, windows):
        self.assertTrue(windows, "no footage was requested at all")
        for start, end in windows:
            self.assertGreaterEqual(start, GAP_LOCAL[0], f"footage requested outside the gap: {start}")
            self.assertLessEqual(end, GAP_LOCAL[1], f"footage requested outside the gap: {end}")

    def test_gap_search_uses_the_recorder_local_window(self):
        da.enumerate_historical_events(self.drv, "1", G0, G1)
        find = self.rec.calls_to("mediaFileFind.cgi", "findFile")[0][1]
        self.assertEqual((find["condition.StartTime"], find["condition.EndTime"]),
                         ("2026-10-04 12:30:00", "2026-10-04 13:30:00"))

    def test_segment_times_come_back_as_utc(self):
        res = da.enumerate_historical_events(self.drv, "1", G0, G1)
        self.assertEqual(res["status"], "supported")
        self.assertEqual([aware(e["ts"]) for e in res["events"]], [utc(7, 30), utc(8, 0)])
        seg = res["events"][1]["segment"]
        self.assertEqual((aware(seg["start"]), aware(seg["end"])), (utc(8, 0), utc(8, 30)))
        self.assertTrue(seg["start"].endswith("Z"))

    def test_segment_identity_stays_recorder_native(self):
        res = da.enumerate_historical_events(self.drv, "1", G0, G1)
        self.assertEqual(res["events"][1]["device_event_id"], "/mnt/dvr/20261004130000.dav")

    def test_backfill_device_ts_is_utc(self):
        out = []
        res = backfill.backfill_events(self.drv, "1", G0, G1, on_event=out.append)
        self.assertEqual(res["status"], "supported")
        self.assertEqual(sorted(aware(e["device_ts"]) for e in out), [utc(7, 30), utc(8, 0)])

    def test_visual_recovery_samples_the_gap_footage(self):
        out = []
        res = recovery_ai.backfill_intelligence(self.drv, None, "1", G0, G1,
                                                on_event=out.append, decoder=decode)
        self.assertEqual(res["status"], "supported")
        windows = self.rec.loadfile_windows()
        self.assertEqual(windows[0][0], local("2026-10-04 12:30:00"))
        self.assert_inside_gap(windows)
        for event in out:
            self.assertTrue(G0 <= aware(event["device_ts"]) < G1, event["device_ts"])

    def test_recovery_runner_marks_recovered_only_from_gap_footage(self):
        cloud = FakeCloud([{"id": "iv1", "started_at": G0.isoformat(), "ended_at": G1.isoformat(),
                            "cameras": ["1"], "status": "pending", "checkpoint": {}}])
        out = []
        runner = recovery.RecoveryRunner(
            cloud, "agent", "key", self.drv, out.append,
            frame_provider=lambda drv, ch, ts: recovery_ai.recovered_frame(drv, ch, ts, decoder=decode))
        runner.run_once()
        self.assertEqual(cloud.completes[-1]["p_status"], "recovered")
        self.assert_inside_gap(self.rec.loadfile_windows())
        frames = [e for e in out if e.get("snapshot_b64")]
        self.assertTrue(frames)
        for event in frames:
            self.assertTrue(G0 <= aware(event["device_ts"]) < G1, event["device_ts"])

    def test_archive_scan_replays_a_segment_start(self):
        # analytics_agent hands the segment start string straight to recovered_frame.
        res = da.enumerate_historical_events(self.drv, "1", G0, G1)
        frame = recovery_ai.recovered_frame(self.drv, "1", res["events"][1]["segment"]["start"],
                                            decoder=decode)
        self.assertEqual(frame, JPEG)
        self.assertEqual(self.rec.loadfile_windows()[-1][0], local("2026-10-04 13:00:00"))


class DriftedRecorder(unittest.TestCase):
    """Segment times are stamped by the recorder's own clock, so replaying them must land on the
    same recorder wall time even when that clock drifts from the agent clock."""

    def setUp(self):
        da.install()
        patcher = mock.patch.object(da, "datetime", pinned_datetime(PC_NOW))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_replay_is_exact_under_drift(self):
        rec = FakeRecorder(PC_NOW, drift=timedelta(seconds=25), files=FILES)
        drv = FakeDahua(rec)
        res = da.enumerate_historical_events(drv, "1", G0, G1)
        self.assertEqual(res["status"], "supported")
        # The gap is agent-clock time: it maps onto recorder wall time WITH the drift.
        find = rec.calls_to("mediaFileFind.cgi", "findFile")[0][1]
        self.assertEqual(find["condition.StartTime"], "2026-10-04 12:30:25")
        start = res["events"][1]["segment"]["start"]
        self.assertEqual(aware(start), utc(8, 0))      # the recorder's own reading, in UTC
        self.assertEqual(recovery_ai.recovered_frame(drv, "1", start, decoder=decode), JPEG)
        self.assertEqual(rec.loadfile_windows()[-1][0], local("2026-10-04 13:00:00"))

    def test_zone_cannot_be_told_from_large_drift(self):
        # 6 minutes off: too close to a quarter-hour zone boundary to tell zone from drift.
        rec = FakeRecorder(PC_NOW, drift=timedelta(minutes=6), files=FILES)
        res = da.enumerate_historical_events(FakeDahua(rec), "1", G0, G1)
        self.assertEqual(res["status"], "unknown")
        self.assertEqual(res["events"], [])

    def test_unparseable_segment_time_fails_closed(self):
        rec = FakeRecorder(PC_NOW, files=FILES)
        rec.items_text = lambda page: ("found=1\r\nitems[0].StartTime=13:00 04/10/2026\r\n"
                                       "items[0].EndTime=13:30 04/10/2026\r\n"
                                       "items[0].FilePath=/mnt/dvr/x.dav\r\n")
        res = da.enumerate_historical_events(FakeDahua(rec), "1", G0, G1)
        self.assertEqual(res["status"], "unknown")
        self.assertEqual(res["events"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
