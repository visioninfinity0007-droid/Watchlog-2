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


class _FakeDet:
    def __init__(self, label):
        self.label = label

    def as_dict(self):
        return {"label": self.label, "confidence": 0.9, "box": [0, 0, 10, 10]}


class _FakeDetector:
    model_name = "fake-yolo"

    def classify_event(self, jpeg):
        return True, [_FakeDet("person")]


class DeepArchiveDriver:
    """Serves segment-shaped recorded events; supports BOTH recorder-native replay and segment AI."""
    def __init__(self, segments, page_size=10):
        self._segs = sorted(segments, key=lambda s: s["start"])
        self._page = page_size

    def historical_capability(self):
        return {"events": "supported", "segments": "supported", "snapshots": "unsupported"}

    def enumerate_historical_events(self, channel, start, end, cursor=None, limit=500):
        s = datetime.fromisoformat(str(start).replace("Z", "+00:00")) if isinstance(start, str) else start
        e = datetime.fromisoformat(str(end).replace("Z", "+00:00")) if isinstance(end, str) else end
        win = [seg for seg in self._segs
               if s <= datetime.fromisoformat(seg["start"]) < e]
        off = int(cursor) if cursor else 0
        page = win[off:off + self._page]
        nxt = str(off + self._page) if off + self._page < len(win) else None
        events = [{"ts": seg["start"], "type": "recorded_segment",
                   "device_event_id": seg["id"], "segment": seg} for seg in page]
        return {"status": "supported", "events": events, "next_cursor": nxt}


class DeepRecoveryRun(unittest.TestCase):
    """§1 deep: RecoveryRunner runs WatchLog AI over recovered footage, not just event replay."""
    def _segments(self):
        return [{"start": (T0 + timedelta(minutes=30 + 60 * i)).isoformat(),
                 "end": (T0 + timedelta(minutes=35 + 60 * i)).isoformat(), "id": f"S{i}"}
                for i in range(3)]

    def test_deep_recovery_emits_ai_and_replay_with_history(self):
        cloud = FakeCloud([interval()])
        drv = DeepArchiveDriver(self._segments())
        events = []
        runner = recovery.RecoveryRunner(cloud, "agent", "key", drv, events.append,
                                         chunk_seconds=3600, detector=_FakeDetector(),
                                         frame_provider=lambda d, c, ts: b"JPEGFRAME",
                                         log=lambda *a: None)
        out = runner.run_once(limit=1)
        self.assertEqual(out[0]["status"], "recovered")
        # both intelligence sources present: recorder-native replay AND AI over footage
        sources = {e["source"] for e in events}
        self.assertIn("recorder_archive", sources)
        self.assertIn("recovered", sources)
        # the recovered-intelligence events carry a historical snapshot + historical timestamp
        ai = [e for e in events if e["source"] == "recovered"]
        self.assertEqual(len(ai), 3)
        for e in ai:
            self.assertTrue(e.get("snapshot_b64"))
            self.assertEqual([o["label"] for o in e["payload"]["objects"]], ["person"])
            self.assertLess(datetime.fromisoformat(e["device_ts"]), T0 + timedelta(hours=3))
        self.assertEqual(out[0]["recovered"], 6)          # 3 replay + 3 AI

    def test_partial_when_only_ai_supported(self):
        # events unsupported but segments supported -> partial (recovered SOME, not all sources)
        class OnlySegments(DeepArchiveDriver):
            def historical_capability(self):
                return {"events": "unsupported", "segments": "supported", "snapshots": "unsupported"}
        cloud = FakeCloud([interval()])
        events = []
        runner = recovery.RecoveryRunner(cloud, "agent", "key", OnlySegments(self._segments()),
                                         events.append, chunk_seconds=3600, detector=_FakeDetector(),
                                         frame_provider=lambda d, c, ts: b"J", log=lambda *a: None)
        out = runner.run_once(limit=1)
        self.assertEqual(out[0]["status"], "partial")
        self.assertTrue(all(e["source"] == "recovered" for e in events))


if __name__ == "__main__":
    unittest.main(verbosity=2)
