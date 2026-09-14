#!/usr/bin/env python3
"""Automatic outage detection + NVR recovery orchestration (recovery.py, §1/§2/§5).

Proves against the hardware-free reference archive driver + a fake cloud: last-live persistence,
exact outage detection, bounded chunked backfill with per-chunk checkpoints, resume-from-cursor,
idempotent no-duplicate recovery via the seen-set, live-monitoring priority, truthful
recovered/unrecoverable status, and recorder_archive provenance on every recovered event.
"""
from __future__ import annotations

import sys, tempfile, unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import recovery  # noqa: E402
import backfill  # noqa: E402

T0 = datetime(2026, 6, 1, 17, 0, tzinfo=timezone.utc)


def archive_events():
    # one event in each of the three hourly chunks 17-18, 18-19, 19-20
    return [{"ts": (T0 + timedelta(minutes=30 + 60 * i)).isoformat(), "type": "person",
             "device_event_id": f"A{i}"} for i in range(3)]


class FakeCloud:
    def __init__(self, intervals=None):
        self.intervals = intervals or []
        self.completes = []
        self.opens = []

    def call(self, name, **kw):
        if name == "wl_agent_claim_recovery":
            due = [iv for iv in self.intervals if iv.get("status", "pending") in ("pending", "in_progress")]
            claimed = due[: kw.get("p_limit", 1)]
            for iv in claimed:
                iv["status"] = "in_progress"
            return claimed
        if name == "wl_complete_recovery":
            self.completes.append(kw)
            for iv in self.intervals:
                if iv["id"] == kw["p_id"]:
                    iv["status"] = kw["p_status"]
                    iv["checkpoint"] = kw["p_checkpoint"]
            return {"ok": True}
        if name == "wl_open_recovery_interval":
            self.opens.append(kw)
            return {"ok": True, "id": "iv-open", "status": "pending"}
        return {}


def interval(iv_id="iv1", start=T0, hours=3, status="pending", checkpoint=None):
    return {"id": iv_id, "started_at": start.isoformat(),
            "ended_at": (start + timedelta(hours=hours)).isoformat(),
            "cameras": ["1"], "status": status, "checkpoint": checkpoint or {}}


class OutageDetection(unittest.TestCase):
    def test_no_last_live(self):
        self.assertIsNone(recovery.detect_outage(None, datetime.now(timezone.utc)))

    def test_small_gap_is_not_outage(self):
        now = T0 + timedelta(seconds=60)
        self.assertIsNone(recovery.detect_outage(T0, now, threshold_seconds=180))

    def test_real_outage(self):
        now = T0 + timedelta(hours=16)
        out = recovery.detect_outage(T0, now, threshold_seconds=180)
        self.assertIsNotNone(out)
        self.assertEqual(out[0], T0)

    def test_last_live_persistence_roundtrip(self):
        d = tempfile.mkdtemp()
        p = Path(d) / "last_live.json"
        recovery.persist_last_live(p, T0)
        got = recovery.read_last_live(p)
        self.assertEqual(got, T0)


class RecoveryRun(unittest.TestCase):
    def _runner(self, cloud, driver, events, live_pending=None):
        return recovery.RecoveryRunner(cloud, "agent", "key", driver, events.append,
                                       chunk_seconds=3600, live_pending=live_pending, log=lambda *a: None)

    def test_full_recovery_with_provenance(self):
        cloud = FakeCloud([interval()])
        drv = backfill.ReferenceArchiveDriver(archive_events(), page_size=10)
        events = []
        out = self._runner(cloud, drv, events).run_once(limit=1)
        self.assertEqual(out[0]["status"], "recovered")
        self.assertEqual(out[0]["recovered"], 3)
        self.assertEqual(len(events), 3)
        for e in events:
            self.assertEqual(e["source"], "recorder_archive")
            self.assertTrue(e["recovered"])
            self.assertEqual(e["provenance"], "Recovered from recorder archive")

    def test_bounded_chunks_checkpoint_each(self):
        cloud = FakeCloud([interval()])
        drv = backfill.ReferenceArchiveDriver(archive_events(), page_size=10)
        self._runner(cloud, drv, []).run_once()
        # 3 hourly chunks -> 3 in_progress checkpoints + 1 terminal complete
        statuses = [c["p_status"] for c in cloud.completes]
        self.assertEqual(statuses.count("in_progress"), 3)
        self.assertEqual(statuses[-1], "recovered")

    def test_idempotent_no_duplicates_on_rerun(self):
        # seen-set carried in the checkpoint prevents re-emitting already-recovered events.
        seen_keys = ["dev:A0", "dev:A1", "dev:A2"]
        cloud = FakeCloud([interval(status="pending", checkpoint={"cursor": T0.isoformat(), "seen_keys": seen_keys})])
        drv = backfill.ReferenceArchiveDriver(archive_events(), page_size=10)
        events = []
        out = self._runner(cloud, drv, events).run_once()
        self.assertEqual(out[0]["recovered"], 0)      # all three already seen
        self.assertEqual(len(events), 0)

    def test_resume_from_cursor(self):
        # resume at 18:00 with the 17:30 event already seen -> only the last two chunks recover.
        cp = {"cursor": (T0 + timedelta(hours=1)).isoformat(), "seen_keys": ["dev:A0"]}
        cloud = FakeCloud([interval(status="pending", checkpoint=cp)])
        drv = backfill.ReferenceArchiveDriver(archive_events(), page_size=10)
        events = []
        out = self._runner(cloud, drv, events).run_once()
        self.assertEqual(out[0]["recovered"], 2)
        self.assertEqual({e["device_event_id"] for e in events}, {"A1", "A2"})

    def test_live_monitoring_has_priority(self):
        cloud = FakeCloud([interval()])
        drv = backfill.ReferenceArchiveDriver(archive_events(), page_size=10)
        out = self._runner(cloud, drv, [], live_pending=lambda: True).run_once()
        self.assertEqual(out, [])                      # never starts recovery while live is pending
        self.assertEqual(cloud.completes, [])

    def test_unsupported_archive_is_unrecoverable(self):
        cloud = FakeCloud([interval()])
        drv = backfill.ReferenceArchiveDriver(archive_events(), events_status="unsupported")
        out = self._runner(cloud, drv, []).run_once()
        self.assertEqual(out[0]["status"], "unrecoverable")
        self.assertEqual(out[0]["recovered"], 0)

    def test_report_outage_opens_interval(self):
        cloud = FakeCloud()
        r = self._runner(cloud, backfill.ReferenceArchiveDriver([]), [])
        res = r.report_outage(T0, T0 + timedelta(hours=16), cameras=["1", "3"])
        self.assertTrue(res["ok"])
        self.assertEqual(cloud.opens[0]["p_cameras"], ["1", "3"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
