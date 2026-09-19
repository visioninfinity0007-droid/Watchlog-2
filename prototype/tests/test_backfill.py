#!/usr/bin/env python3
"""Historical backfill runtime (item 6) — recovered intelligence, vendor-neutral.

Proves against a hardware-free reference driver: bounded + paginated retrieval, range bounding,
duplicate-safe reprocessing, restart (resume with the same seen-set), unsupported and unknown
recorders (honest status, nothing fabricated), and that every recovered event is tagged
"recovered from archive" and NEVER as a live observation.
"""
from __future__ import annotations

import sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import backfill as bf  # noqa: E402
from drivers.base import NvrDriver  # noqa: E402


def sample(n=5, ch="1", with_ids=True):
    return [{"ts": f"2026-06-01T10:{i:02d}:00+00:00", "type": "person",
             **({"device_event_id": f"E{i}"} if with_ids else {})} for i in range(n)]


class Backfill(unittest.TestCase):
    def test_bounded_paginated_recovery(self):
        drv = bf.ReferenceArchiveDriver(sample(5), page_size=2)
        got = []
        s = bf.backfill_events(drv, "1", "2026-06-01T00:00:00+00:00", "2026-06-02T00:00:00+00:00",
                               window_seconds=3600, on_event=got.append)
        self.assertEqual(s["status"], bf.SUPPORTED)
        self.assertEqual(s["recovered"], 5)
        self.assertGreaterEqual(s["pages"], 3)         # 5 events / page 2 -> multiple pages
        self.assertEqual(len(got), 5)

    def test_range_is_bounded(self):
        drv = bf.ReferenceArchiveDriver(sample(5), page_size=10)
        # window covering only the first 3 events (10:00,10:01,10:02)
        s = bf.backfill_events(drv, "1", "2026-06-01T10:00:00+00:00", "2026-06-01T10:03:00+00:00")
        self.assertEqual(s["recovered"], 3)

    def test_provenance_tagging(self):
        drv = bf.ReferenceArchiveDriver(sample(2), page_size=5)
        got = []
        bf.backfill_events(drv, "3", "2026-06-01T00:00:00+00:00", "2026-06-02T00:00:00+00:00", on_event=got.append)
        for ev in got:
            self.assertEqual(ev["source"], "recorder_archive")
            self.assertTrue(ev["recovered"])
            self.assertEqual(ev["provenance"], "Recovered from recorder archive")
            self.assertEqual(ev["channel"], "3")
            self.assertNotIn(ev.get("source"), ("live", "recorder_event"))   # never live

    def test_duplicate_safe_reprocessing(self):
        drv = bf.ReferenceArchiveDriver(sample(5), page_size=2)
        seen = set()
        r1 = bf.backfill_events(drv, "1", "2026-06-01T00:00:00+00:00", "2026-06-02T00:00:00+00:00", seen=seen)
        r2 = bf.backfill_events(drv, "1", "2026-06-01T00:00:00+00:00", "2026-06-02T00:00:00+00:00", seen=seen)
        self.assertEqual(r1["recovered"], 5)
        self.assertEqual(r2["recovered"], 0)            # nothing new on reprocess
        self.assertEqual(r2["duplicates"], 5)

    def test_restart_resumes_without_dupes(self):
        drv = bf.ReferenceArchiveDriver(sample(6), page_size=2)
        seen = set()
        got = []
        # first pass interrupted after 3 events
        r1 = bf.backfill_events(drv, "1", "2026-06-01T00:00:00+00:00", "2026-06-02T00:00:00+00:00",
                                seen=seen, on_event=got.append, max_events=3)
        self.assertTrue(r1.get("stopped_at_limit"))
        self.assertEqual(r1["recovered"], 3)
        # restart with the SAME seen-set -> recovers only the remaining 3, no duplicates
        r2 = bf.backfill_events(drv, "1", "2026-06-01T00:00:00+00:00", "2026-06-02T00:00:00+00:00",
                                seen=seen, on_event=got.append)
        self.assertEqual(r2["recovered"], 3)
        self.assertEqual(r2["duplicates"], 3)
        self.assertEqual(len({e.get("device_event_id") for e in got}), 6)   # all 6 unique, once each

    def test_dedupe_falls_back_to_ts_type_without_ids(self):
        drv = bf.ReferenceArchiveDriver(sample(4, with_ids=False), page_size=2)
        seen = set()
        bf.backfill_events(drv, "1", "2026-06-01T00:00:00+00:00", "2026-06-02T00:00:00+00:00", seen=seen)
        r2 = bf.backfill_events(drv, "1", "2026-06-01T00:00:00+00:00", "2026-06-02T00:00:00+00:00", seen=seen)
        self.assertEqual(r2["recovered"], 0)            # ts+type dedupe still idempotent

    def test_unsupported_recorder_is_honest(self):
        s = bf.backfill_events(NvrDriver("http://127.0.0.1"), "1",
                               "2026-06-01T00:00:00+00:00", "2026-06-02T00:00:00+00:00")
        self.assertEqual(s["status"], "unsupported")
        self.assertEqual(s["recovered"], 0)             # no fabricated support

    def test_unknown_capability_is_honest(self):
        drv = bf.ReferenceArchiveDriver(sample(5), events_status="unknown")
        s = bf.backfill_events(drv, "1", "2026-06-01T00:00:00+00:00", "2026-06-02T00:00:00+00:00")
        self.assertEqual(s["status"], "unknown")
        self.assertEqual(s["recovered"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
