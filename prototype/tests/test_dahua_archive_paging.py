#!/usr/bin/env python3
"""MNVR-062: Dahua archive enumeration pages past one finder page instead of stopping at 100.

Before the fix find_recordings asked findNextFile once for at most 100 files and
enumerate_historical_events returned next_cursor=None, so a channel-hour with 140 files reported
SUPPORTED with 40 files never examined, and recovery could mark the interval recovered. Now every
page is read; a search too large to page within the hard cap is reported partial, never supported.

Field-only: whether any deployed recorder produces more than 100 files per channel-hour.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dahua_fake_recorder import JPEG, FakeCloud, FakeDahua, FakeRecorder, local, pinned_datetime  # noqa: E402
import backfill  # noqa: E402
import dahua_archive as da  # noqa: E402
import recovery  # noqa: E402
import recovery_ai  # noqa: E402

PC_NOW = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)
# A UTC recorder, so this test isolates paging from the time-zone handling (MNVR-019).
W0 = datetime(2026, 10, 4, 8, 0, tzinfo=timezone.utc)
W1 = W0 + timedelta(hours=1)
FIRST = local("2026-10-04 08:00:00")
FILES_140 = [(FIRST + timedelta(seconds=20 * i), FIRST + timedelta(seconds=20 * (i + 1)),
              f"/mnt/dvr/event{i:03d}.dav") for i in range(140)]


class Paging(unittest.TestCase):
    def setUp(self):
        da.install()
        self.rec = FakeRecorder(PC_NOW, zone=timedelta(0), files=FILES_140)
        self.drv = FakeDahua(self.rec)
        patcher = mock.patch.object(da, "datetime", pinned_datetime(PC_NOW))
        patcher.start()
        self.addCleanup(patcher.stop)

    def finder_pages(self):
        return [int(c[1]["count"]) for c in self.rec.calls_to("mediaFileFind.cgi", "findNextFile")]

    def test_enumeration_reads_every_page(self):
        res = da.enumerate_historical_events(self.drv, "1", W0, W1)
        self.assertEqual(res["status"], "supported")
        self.assertEqual(len(res["events"]), 140)
        self.assertEqual(len({e["device_event_id"] for e in res["events"]}), 140)
        self.assertIsNone(res["next_cursor"])
        # 100 + 40 files, then the empty page that proves the archive has no more.
        self.assertEqual(len(self.finder_pages()), 3)

    def test_backfill_recovers_every_file(self):
        out = []
        res = backfill.backfill_events(self.drv, "1", W0, W1, on_event=out.append)
        self.assertEqual(res["status"], "supported")
        self.assertEqual(res["recovered"], 140)
        self.assertEqual(len(out), 140)

    def test_small_pages_continue_by_cursor_without_gaps_or_duplicates(self):
        first = da.enumerate_historical_events(self.drv, "1", W0, W1, limit=50)
        self.assertEqual(first["status"], "supported")
        self.assertEqual(len(first["events"]), 50)
        self.assertEqual(first["next_cursor"], "50")
        out = []
        res = backfill.backfill_events(self.drv, "1", W0, W1, page_limit=50, on_event=out.append)
        self.assertEqual((res["status"], res["recovered"], res["duplicates"], res["pages"]),
                         ("supported", 140, 0, 3))
        self.assertEqual(sorted(e["device_event_id"] for e in out), sorted(f[2] for f in FILES_140))

    def test_search_too_large_to_page_ends_partial_not_supported(self):
        out = []
        with mock.patch.object(da, "MAX_FINDER_PAGES", 1, create=True):
            first = da.enumerate_historical_events(self.drv, "1", W0, W1)
            res = backfill.backfill_events(self.drv, "1", W0, W1, on_event=out.append)
        # What was read is still served, but the scan cannot end as a complete "supported".
        self.assertEqual(first["status"], "supported")
        self.assertIsNotNone(first["next_cursor"])
        self.assertEqual(len(out), 100)
        self.assertEqual(res["status"], "partial")

    def test_recorder_that_serves_short_pages_is_read_to_the_end(self):
        # A page shorter than asked for does not prove the archive is exhausted; only an empty one
        # does.
        self.rec.page_cap = 50
        res = da.enumerate_historical_events(self.drv, "1", W0, W1)
        self.assertEqual((res["status"], len(res["events"]), res["next_cursor"]), ("supported", 140, None))

    def test_short_pages_past_the_cap_end_partial_not_supported(self):
        self.rec.page_cap = 50
        out = []
        with mock.patch.object(da, "MAX_FINDER_PAGES", 2):
            first = da.enumerate_historical_events(self.drv, "1", W0, W1)
            res = backfill.backfill_events(self.drv, "1", W0, W1, on_event=out.append)
        self.assertIsNotNone(first["next_cursor"])
        self.assertEqual(len(out), 100)
        self.assertEqual(res["status"], "partial")

    def test_recovery_interval_with_unpaged_files_is_never_recovered(self):
        cloud = FakeCloud([{"id": "iv1", "started_at": W0.isoformat(), "ended_at": W1.isoformat(),
                            "cameras": ["1"], "status": "pending", "checkpoint": {}}])
        runner = recovery.RecoveryRunner(
            cloud, "agent", "key", self.drv, lambda _ev: None,
            frame_provider=lambda drv, ch, ts: recovery_ai.recovered_frame(
                drv, ch, ts, decoder=lambda _clip: JPEG))
        with mock.patch.object(da, "MAX_FINDER_PAGES", 1, create=True):
            runner.run_once()
        self.assertNotEqual(cloud.completes[-1]["p_status"], "recovered")

    def test_existence_check_still_reads_one_row(self):
        self.assertTrue(da.has_recording(self.drv, "1", W0, W1))
        self.assertEqual(self.finder_pages(), [1])

    def test_single_row_probe_returns_one_row_and_a_cursor(self):
        res = da.enumerate_historical_events(self.drv, "1", W0, W1, limit=1)
        self.assertEqual(res["status"], "supported")
        self.assertEqual(len(res["events"]), 1)
        self.assertEqual(res["events"][0]["device_event_id"], "/mnt/dvr/event000.dav")
        self.assertEqual(res["next_cursor"], "1")

    def test_pages_are_ordered_even_when_the_recorder_is_not(self):
        self.rec.files = list(reversed(FILES_140))
        res = backfill.backfill_events(self.drv, "1", W0, W1, page_limit=50, on_event=lambda _e: None)
        first = da.enumerate_historical_events(self.drv, "1", W0, W1, limit=50)
        self.assertEqual(res["recovered"], 140)
        self.assertEqual(first["events"][0]["device_event_id"], "/mnt/dvr/event000.dav")

if __name__ == "__main__":
    unittest.main(verbosity=2)
