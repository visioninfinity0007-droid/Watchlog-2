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
import watchlog_agent as core  # noqa: E402

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


class _ArchiveDriverStub:
    def __init__(self, name, info=None, fail_probe=False):
        self.name = name
        self.info = info
        self.fail_probe = fail_probe
        self.closed = False

    def probe(self):
        if self.fail_probe:
            raise RuntimeError("native probe unavailable")
        return self.info

    def close(self):
        self.closed = True


class ArchiveTransportRouting(unittest.TestCase):
    def test_onvif_dahua_live_path_uses_native_archive_reader(self):
        live_info = type("Info", (), {"vendor": "Dahua", "model": "DH-XVR1B08-I"})()
        native_info = type("Info", (), {"vendor": "Dahua", "model": "DH-XVR1B08-I"})()
        live = _ArchiveDriverStub("onvif", live_info)
        native = _ArchiveDriverStub("dahua-cgi", native_info)
        cfg = type("Cfg", (), {
            "nvr_url": "http://192.0.2.10",
            "nvr_username": "local-user",
            "nvr_password": "local-password",
        })()
        old_open, old_build = core.open_driver, core.build
        try:
            core.open_driver = lambda _cfg: (live, live_info)
            core.build = lambda name, *_a, **_kw: native if name == "dahua-cgi" else None
            driver, info = core.open_archive_driver(cfg)
        finally:
            core.open_driver, core.build = old_open, old_build
        self.assertIs(driver, native)
        self.assertIs(info, native_info)
        self.assertTrue(live.closed)

    def test_native_archive_probe_failure_preserves_live_onvif_driver(self):
        live_info = type("Info", (), {"vendor": "Dahua", "model": "DH-XVR1B08-I"})()
        live = _ArchiveDriverStub("onvif", live_info)
        native = _ArchiveDriverStub("dahua-cgi", fail_probe=True)
        cfg = type("Cfg", (), {
            "nvr_url": "http://192.0.2.10",
            "nvr_username": "local-user",
            "nvr_password": "local-password",
        })()
        old_open, old_build = core.open_driver, core.build
        try:
            core.open_driver = lambda _cfg: (live, live_info)
            core.build = lambda name, *_a, **_kw: native
            driver, info = core.open_archive_driver(cfg)
        finally:
            core.open_driver, core.build = old_open, old_build
        self.assertIs(driver, live)
        self.assertIs(info, live_info)
        self.assertFalse(live.closed)
        self.assertTrue(native.closed)


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
        res = r.report_outage(T0, T0 + timedelta(hours=16), cameras=[CAM1, CAM3])
        self.assertTrue(res["ok"])
        self.assertEqual(cloud.opens[0]["p_cameras"], [CAM1, CAM3])


# recovery_intervals.cameras is uuid[]: intervals carry cloud camera UUIDs, never channels.
CAM1 = "11111111-1111-4111-8111-111111111111"
CAM3 = "33333333-3333-4333-8333-333333333333"
CAM_GONE = "99999999-9999-4999-8999-999999999999"


class _ChannelRecordingDriver(backfill.ReferenceArchiveDriver):
    """Reference archive that records which recorder channels it was asked to read."""
    def __init__(self, events, **kw):
        super().__init__(events, **kw)
        self.channels = []

    def enumerate_historical_events(self, channel, start, end, cursor=None, limit=500):
        self.channels.append(str(channel))
        return super().enumerate_historical_events(channel, start, end, cursor, limit)


class IntervalCameraIdentity(unittest.TestCase):
    """The runner reads recorder channels; the interval names cameras by cloud UUID."""

    def _run(self, cameras, camera_channels=None):
        iv = interval()
        iv["cameras"] = cameras
        cloud = FakeCloud([iv])
        drv = _ChannelRecordingDriver(archive_events(), page_size=10)
        kw = {} if camera_channels is None else {"camera_channels": camera_channels}
        runner = recovery.RecoveryRunner(cloud, "agent", "key", drv, [].append,
                                         chunk_seconds=3600, log=lambda *a: None, **kw)
        return runner.run_once(limit=1), cloud, drv

    def test_camera_uuids_are_read_as_their_recorder_channels(self):
        out, cloud, drv = self._run([CAM1, CAM3], {CAM1: "1", CAM3: "3"})
        self.assertEqual(out[0]["status"], "recovered")
        self.assertEqual(set(drv.channels), {"1", "3"})

    def test_a_camera_uuid_is_never_sent_to_the_recorder_as_a_channel(self):
        out, cloud, drv = self._run([CAM1, CAM_GONE], {CAM1: "1"})
        self.assertEqual(set(drv.channels), {"1"})
        # One camera could not be read, so the interval is not fully recovered.
        self.assertEqual(out[0]["status"], "partial")
        self.assertEqual(cloud.completes[-1]["p_status"], "partial")

    def test_empty_camera_list_reads_every_known_camera_never_a_guessed_channel_1(self):
        out, cloud, drv = self._run([], {CAM3: "3", CAM_GONE: "5"})
        self.assertNotIn("1", drv.channels)
        self.assertEqual(set(drv.channels), {"3", "5"})
        self.assertEqual(out[0]["status"], "recovered")

    def test_empty_camera_list_without_inventory_is_never_recovered(self):
        out, cloud, drv = self._run([])
        self.assertEqual(drv.channels, [], "no recorder channel may be guessed")
        self.assertEqual(out[0]["status"], "unrecoverable")
        final = cloud.completes[-1]
        self.assertEqual(final["p_status"], "unrecoverable")
        self.assertEqual(final["p_detail"], {"reason": "missing_channels"})


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

    def test_visual_backfill_runs_without_detector(self):
        cloud = FakeCloud([interval()])
        drv = DeepArchiveDriver(self._segments())
        events = []
        runner = recovery.RecoveryRunner(
            cloud, "agent", "key", drv, events.append,
            chunk_seconds=3600, detector=None,
            frame_provider=lambda d, c, ts: b"JPEGFRAME",
            snapshot_interval_seconds=300,
            log=lambda *a: None)
        out = runner.run_once(limit=1)
        visual = [e for e in events if e.get("source") == "recovered"]
        self.assertEqual(len(visual), 3)
        self.assertTrue(all(e["event_type"] == "recovered_snapshot" for e in visual))
        self.assertTrue(all(e.get("snapshot_b64") for e in visual))
        self.assertEqual(out[0]["status"], "recovered")

    def test_segment_archive_with_zero_decodable_frames_is_partial(self):
        cloud = FakeCloud([interval()])
        drv = DeepArchiveDriver(self._segments())
        events = []
        runner = recovery.RecoveryRunner(
            cloud, "agent", "key", drv, events.append,
            chunk_seconds=3600, detector=None,
            frame_provider=lambda d, c, ts: None,
            log=lambda *a: None)
        out = runner.run_once(limit=1)
        # Native archive replay recovered evidence, but the visual timeline did not.
        self.assertEqual(out[0]["status"], "partial")
        self.assertTrue(any(e.get("source") == "recorder_archive" for e in events))
        self.assertFalse(any(e.get("source") == "recovered" for e in events))

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
