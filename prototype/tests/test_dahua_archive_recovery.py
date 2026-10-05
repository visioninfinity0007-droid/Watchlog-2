#!/usr/bin/env python3
"""Dahua archive as recovery sees it (MNVR-061, MNVR-059).

Recorded segments are footage, not recorder events. The Dahua archive answers a search with
recording files only, so it reports historical events 'unsupported' and segments 'supported', like
the Hikvision archive. Recovery then judges a Dahua interval by the footage it examined: a gap the
archive holds no recording of is never called recovered, and the archive is not searched a second
time for recorder events it does not have.

Field-only (IMPLEMENTED_UNVERIFIED): how a real DH-XVR1B08-I answers mediaFileFind; the fake
recorder pins the module's working model only.
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

PC_NOW = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)
# A UTC recorder, so these cases isolate recovery's verdict from the time-zone handling.
G0 = datetime(2026, 10, 4, 8, 0, tzinfo=timezone.utc)
G1 = G0 + timedelta(hours=1)


def _interval():
    return {"id": "iv1", "started_at": G0.isoformat(), "ended_at": G1.isoformat(),
            "cameras": ["1"], "status": "pending", "checkpoint": {}}


class FootageNotEvents(unittest.TestCase):
    def setUp(self):
        da.install()
        patcher = mock.patch.object(da, "datetime", pinned_datetime(PC_NOW))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _recorder(self, files):
        self.rec = FakeRecorder(PC_NOW, zone=timedelta(0), files=files)
        return FakeDahua(self.rec)

    def _recover(self, files):
        cloud, events = FakeCloud([_interval()]), []
        recovery.RecoveryRunner(cloud, "agent", "key", self._recorder(files), events.append,
                                frame_provider=lambda drv, ch, ts: JPEG,
                                log=lambda *a: None).run_once()
        return cloud.completes[-1], events

    def test_capability_reports_segments_not_events(self):
        cap = self._recorder([]).historical_capability()
        self.assertEqual((cap["events"], cap["segments"]), ("unsupported", "supported"))

    def test_the_archive_is_not_searched_for_recorder_events(self):
        out = []
        drv = self._recorder(continuous_files(local("2026-10-04 07:00:00"),
                                              local("2026-10-04 10:00:00")))
        res = backfill.backfill_events(drv, "1", G0, G1, on_event=out.append)
        self.assertEqual((res["status"], out), ("unsupported", []))
        self.assertEqual(self.rec.calls_to("mediaFileFind.cgi", "findFile"), [])

    def test_a_gap_whose_footage_was_examined_is_recovered(self):
        final, events = self._recover(continuous_files(local("2026-10-04 07:00:00"),
                                                       local("2026-10-04 10:00:00")))
        self.assertEqual(final["p_status"], "recovered")
        self.assertEqual(len(events), 12)
        self.assertFalse([e for e in events if e["event_type"] == "recorded_segment"])

    def test_a_gap_the_archive_holds_no_recording_of_is_never_recovered(self):
        # Recordings exist only after the gap: nothing of the gap was examined.
        final, events = self._recover(continuous_files(local("2026-10-04 09:30:00"),
                                                       local("2026-10-04 10:00:00")))
        self.assertEqual(events, [])
        self.assertEqual(final["p_status"], "unrecoverable")


if __name__ == "__main__":
    unittest.main(verbosity=2)
