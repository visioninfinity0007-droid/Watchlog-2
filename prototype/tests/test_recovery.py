#!/usr/bin/env python3
"""Automatic outage detection + NVR recovery orchestration (recovery.py, §1/§2/§5).

Proves against the hardware-free reference archive driver + a fake cloud: last-live persistence,
exact outage detection, bounded chunked backfill with per-chunk checkpoints, resume-from-cursor,
idempotent no-duplicate recovery via the seen-set, live-monitoring priority, truthful
recovered/unrecoverable status, and recorder_archive provenance on every recovered event.
"""
from __future__ import annotations

import json, sys, tempfile, unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import recovery  # noqa: E402
import backfill  # noqa: E402
import watchlog_agent as core  # noqa: E402
from drivers.onvif_driver import OnvifDriver  # noqa: E402

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
    def __init__(self, name, info=None, fail_probe=False, channels=("1", "2")):
        self.name = name
        self.info = info
        self.fail_probe = fail_probe
        self.channels = channels
        self.closed = False

    def probe(self):
        if self.fail_probe:
            raise RuntimeError("native probe unavailable")
        return self.info

    def list_channels(self):
        return [type("Ch", (), {"channel": c, "name": None})() for c in self.channels]

    def close(self):
        self.closed = True


class _LabelledOnvif(OnvifDriver):
    """A real ONVIF driver whose GetProfiles names the recorder's own channel per video source."""
    PROFILES = ("<Envelope>"
                "<Profiles token='p1'><Name>MediaProfile_Channel1_MainStream</Name>"
                "<VideoSourceConfiguration><SourceToken>000</SourceToken></VideoSourceConfiguration></Profiles>"
                "<Profiles token='p2'><Name>MediaProfile_Channel2_MainStream</Name>"
                "<VideoSourceConfiguration><SourceToken>001</SourceToken></VideoSourceConfiguration></Profiles>"
                "</Envelope>")

    def __init__(self):
        super().__init__("http://192.0.2.10", "local-user", "local-password", timeout=1)
        self.media_service = "http://192.0.2.10/onvif/media_service"
        self.closed = False

    def _call(self, *_a, **_k):
        return ET.fromstring(self.PROFILES)

    def close(self):
        self.closed = True
        super().close()


class ArchiveTransportRouting(unittest.TestCase):
    def test_onvif_dahua_live_path_uses_native_archive_reader(self):
        live_info = type("Info", (), {"vendor": "Dahua", "model": "DH-XVR1B08-I"})()
        native_info = type("Info", (), {"vendor": "Dahua", "model": "DH-XVR1B08-I"})()
        live = _LabelledOnvif()
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
        self.assertEqual(driver.name, "dahua-cgi")
        self.assertEqual(driver.channel_map, {"1": "1", "2": "2"})   # by label, never positional
        self.assertIs(info, native_info)
        self.assertTrue(live.closed)
        driver.close()
        self.assertTrue(native.closed)

    def test_onvif_without_verified_channel_map_keeps_live_driver(self):
        # MNVR-029: an ONVIF site whose profiles do not name the recorder's own channels has no
        # label-consistent ONVIF-to-native map, so the native reader is refused instead of guessed.
        live_info = type("Info", (), {"vendor": "Dahua", "model": "DH-XVR1B08-I"})()
        live = _ArchiveDriverStub("onvif", live_info)
        native = _ArchiveDriverStub("dahua-cgi", live_info)
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

    def test_caller_owned_live_driver_is_reused_and_left_open(self):
        # MNVR-063: acceptance and status pass their already-open live driver. No second
        # recorder login, and the caller's driver stays open even when recorded media moves to
        # the native reader (the caller still lists cameras and closes it).
        live_info = type("Info", (), {"vendor": "Dahua", "model": "DH-XVR1B08-I"})()
        native_info = type("Info", (), {"vendor": "Dahua", "model": "DH-XVR1B08-I"})()
        live = _LabelledOnvif()
        native = _ArchiveDriverStub("dahua-cgi", native_info)
        direct = _ArchiveDriverStub("dahua-cgi", live_info)
        cfg = type("Cfg", (), {
            "nvr_url": "http://192.0.2.10",
            "nvr_username": "local-user",
            "nvr_password": "local-password",
        })()
        opened = []
        old_open, old_build = core.open_driver, core.build
        try:
            core.open_driver = lambda _cfg: opened.append(_cfg) or (live, live_info)
            core.build = lambda name, *_a, **_kw: native
            driver, info = core.open_archive_driver(cfg, live=(live, live_info))
            same, same_info = core.open_archive_driver(cfg, live=(direct, live_info))
        finally:
            core.open_driver, core.build = old_open, old_build
        self.assertEqual(opened, [])
        self.assertEqual(driver.name, "dahua-cgi")
        self.assertEqual(driver.channel_map, {"1": "1", "2": "2"})
        self.assertIs(info, native_info)
        self.assertFalse(live.closed)
        self.assertIs(same, direct)                  # directly enrolled native site: same driver
        self.assertIs(same_info, live_info)
        self.assertFalse(direct.closed)


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
        # Recorded segments are footage, not recorder events: they feed the AI pass and are
        # never replayed as recorder_archive events of their own.
        sources = {e["source"] for e in events}
        self.assertEqual(sources, {"recovered"})
        # the recovered-intelligence events carry a historical snapshot + historical timestamp
        ai = [e for e in events if e["source"] == "recovered"]
        self.assertEqual(len(ai), 3)
        for e in ai:
            self.assertTrue(e.get("snapshot_b64"))
            self.assertEqual([o["label"] for o in e["payload"]["objects"]], ["person"])
            self.assertLess(datetime.fromisoformat(e["device_ts"]), T0 + timedelta(hours=3))
        self.assertEqual(out[0]["recovered"], 3)          # 3 AI frames; segments are not events

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
        # The archive answered, but the visual timeline was not recovered: never "recovered".
        self.assertEqual(out[0]["status"], "partial")
        self.assertEqual(events, [])

    def _footage_only(self, segments, frame_provider, **kw):
        cloud = FakeCloud([interval()])
        events = []
        runner = recovery.RecoveryRunner(cloud, "agent", "key", FootageOnlyArchive(segments),
                                         events.append, chunk_seconds=3600, detector=_FakeDetector(),
                                         frame_provider=frame_provider, log=lambda *a: None, **kw)
        return runner, cloud, events

    def test_footage_only_recorder_is_judged_by_its_footage(self):
        # MNVR-061 release gate. A recorder with searchable footage but no searchable event log
        # (Hikvision, Dahua) reports events 'unsupported' and segments 'supported'. The event
        # replay it cannot offer is not a part of the gap that went unrecovered, exactly as the
        # absent footage of an event-only recorder (segments 'unsupported') is not: footage
        # examined across the whole gap is a recovered interval. Before, this ended 'partial',
        # which moved recovered Hikvision time to UNVERIFIED once the capability became truthful.
        runner, cloud, events = self._footage_only(self._segments(), lambda d, c, ts: b"J")
        out = runner.run_once(limit=1)
        self.assertEqual(out[0]["status"], "recovered")
        self.assertEqual(cloud.completes[-1]["p_status"], "recovered")
        self.assertEqual(len(events), 3)
        self.assertTrue(all(e["source"] == "recovered" for e in events))

    def test_footage_only_recorder_whose_frames_fail_is_never_recovered(self):
        runner, cloud, events = self._footage_only(self._segments(), lambda d, c, ts: None)
        out = runner.run_once(limit=1)
        self.assertEqual(out[0]["status"], "unrecoverable")
        self.assertEqual(events, [])

    def test_footage_only_recorder_with_no_footage_in_the_gap_is_not_recovered(self):
        # The archive holds no recording of the gap: nothing was examined, so nothing may be
        # called recovered (and nothing was recovered, so it is not partial either).
        runner, cloud, events = self._footage_only([], lambda d, c, ts: b"J")
        out = runner.run_once(limit=1)
        self.assertEqual(out[0]["status"], "unrecoverable")
        self.assertEqual(cloud.completes[-1]["p_status"], "unrecoverable")
        self.assertEqual(cloud.completes[-1]["p_recovered_count"], 0)

    def test_footage_only_hours_without_recordings_do_not_block_a_recovered_gap(self):
        # Footage exists only in the first hour (motion recording). The search of every hour was
        # complete and every recording in the gap was examined.
        runner, cloud, events = self._footage_only(self._segments()[:1], lambda d, c, ts: b"J")
        out = runner.run_once(limit=1)
        self.assertEqual(out[0]["status"], "recovered")
        self.assertEqual(len(events), 1)

    def test_an_unreadable_hour_before_a_yield_is_not_forgotten(self):
        # Claim 1 cannot decode the first hour, then yields to live work; claim 2 recovers the
        # rest. The first hour was never examined, so the interval is partial, not recovered.
        first_hour = T0 + timedelta(hours=1)
        live = iter([False, False, True])
        cloud = FakeCloud([interval()])
        events = []
        runner = recovery.RecoveryRunner(
            cloud, "agent", "key", DeepArchiveDriver(self._segments()), events.append,
            chunk_seconds=3600, live_pending=lambda: next(live, False),
            frame_provider=lambda d, c, ts: None if ts < first_hour else b"J",
            log=lambda *a: None)
        first = runner.run_once(limit=1)
        self.assertTrue(first[0]["yielded"])
        second = runner.run_once(limit=1)
        self.assertEqual(second[0]["status"], "partial")
        self.assertEqual(cloud.completes[-1]["p_status"], "partial")
        self.assertEqual(len(events), 2)

    def test_a_recovered_hour_before_a_yield_is_not_forgotten(self):
        # Claim 1 recovers the only recording (first hour), then yields; the hours claim 2 reads
        # hold no recording. Footage of the gap was examined, so the interval is recovered.
        live = iter([False, False, True])
        runner, cloud, events = self._footage_only(self._segments()[:1], lambda d, c, ts: b"J",
                                                   live_pending=lambda: next(live, False))
        self.assertTrue(runner.run_once(limit=1)[0]["yielded"])
        self.assertEqual(runner.run_once(limit=1)[0]["status"], "recovered")
        self.assertEqual(len(events), 1)

    def _two_claims(self, frame_provider):
        """Claim 1: channel 3's search fails, so the claim backs off from the first hour after
        channel 1 was read. Claim 2 reads the whole interval again with channel 3 answering."""
        class Flaky(DeepArchiveDriver):
            claim = 1

            def enumerate_historical_events(self, channel, start, end, cursor=None, limit=500):
                if self.claim == 1 and str(channel) == "3":
                    raise ConnectionError("recorder stopped answering")
                return super().enumerate_historical_events(channel, start, end, cursor, limit)

        drv = Flaky(self._segments())
        iv = interval()
        iv["cameras"] = [CAM1, CAM3]
        cloud = FakeCloud([iv])
        runner = recovery.RecoveryRunner(
            cloud, "agent", "key", drv, [].append, chunk_seconds=3600,
            camera_channels={CAM1: "1", CAM3: "3"},
            frame_provider=lambda d, c, ts: frame_provider(drv.claim, c), log=lambda *a: None)
        self.assertEqual(runner.run_once(limit=1)[0]["status"], "in_progress")
        self.assertEqual(cloud.completes[-1]["p_checkpoint"]["cursor"], T0.isoformat())
        drv.claim = 2
        return runner.run_once(limit=1)[0]["status"]

    def test_a_chunk_read_again_after_a_failed_read_is_judged_by_its_second_read(self):
        # Channel 1's frames fail on claim 1 and decode on claim 2: the hours read again are
        # judged by that second read, not stuck as missed.
        self.assertEqual(self._two_claims(lambda claim, ch: b"J" if claim == 2 else None),
                         "recovered")

    def test_samples_whose_frames_failed_are_tried_again_not_taken_as_recovered(self):
        # Channel 1's frames never decode. Its failed samples are not "already recovered" on the
        # claim that reads them again, so the interval is never called recovered.
        self.assertEqual(self._two_claims(lambda claim, ch: None if ch == "1" else b"J"),
                         "partial")

    def test_events_sent_before_a_scan_stopped_short_make_the_interval_partial(self):
        # A search too large to page (Dahua) serves the rows it read, then ends 'partial'. The
        # events it already sent are recovered, the rest of the window was not read.
        class StopsShort(backfill.ReferenceArchiveDriver):
            def enumerate_historical_events(self, channel, start, end, cursor=None, limit=500):
                if cursor:
                    return {"status": "partial", "events": [], "next_cursor": None}
                page = super().enumerate_historical_events(channel, start, end, None, limit)
                return {**page, "next_cursor": "1"}

        cloud = FakeCloud([interval(hours=1)])
        events = []
        out = recovery.RecoveryRunner(cloud, "agent", "key", StopsShort(archive_events()),
                                      events.append, chunk_seconds=3600,
                                      log=lambda *a: None).run_once(limit=1)
        self.assertEqual(len(events), 1)
        self.assertEqual(out[0]["status"], "partial")
        self.assertEqual(cloud.completes[-1]["p_recovered_count"], 1)


class FrameBudget(unittest.TestCase):
    def test_an_interval_whose_frame_budget_ran_out_is_partial(self):
        # Two recordings in the first hour and a budget of one frame per chunk: the second
        # recording of that hour is never examined (MNVR-062 verification: it was 'recovered').
        segs = [{"start": (T0 + timedelta(minutes=m)).isoformat(),
                 "end": (T0 + timedelta(minutes=m + 2)).isoformat(), "id": f"S{m}"}
                for m in (10, 40)]
        cloud = FakeCloud([interval(hours=1)])
        events = []
        out = recovery.RecoveryRunner(cloud, "agent", "key", FootageOnlyArchive(segs),
                                      events.append, chunk_seconds=3600, ai_max_frames=1,
                                      frame_provider=lambda d, c, ts: b"J",
                                      log=lambda *a: None).run_once(limit=1)
        self.assertEqual(len(events), 1)
        self.assertEqual(out[0]["status"], "partial")


class FootageOnlyArchive(DeepArchiveDriver):
    """Searchable recorded footage, no searchable event log (the vendor archive shape)."""
    def historical_capability(self):
        return {"events": "unsupported", "segments": "supported", "snapshots": "unsupported"}


RECORDER_IP = "192.0.2.64"


class HikvisionShapedArchive(DeepArchiveDriver):
    """Rows shaped like hikvision_archive.enumerate_historical_events: the device_event_id and
    segment carry the recorder's playback URI, which embeds its LAN address."""
    def enumerate_historical_events(self, channel, start, end, cursor=None, limit=500):
        page = super().enumerate_historical_events(channel, start, end, cursor, limit)
        for ev in page["events"]:
            uri = (f"rtsp://{RECORDER_IP}/Streaming/tracks/{channel}01/"
                   f"?starttime={ev['ts']}&name=00010000123&size=4096")
            ev["device_event_id"] = uri
            ev["segment"] = {"start": ev["segment"]["start"], "end": ev["segment"]["end"],
                             "playback_uri": uri}
        return page


class RecordedSegmentsAreNotEvents(unittest.TestCase):
    def _segments(self):
        return DeepRecoveryRun._segments(self)

    def test_backfill_does_not_replay_recording_segments_as_events(self):
        got = []
        res = backfill.backfill_events(HikvisionShapedArchive(self._segments()), "1",
                                       T0, T0 + timedelta(hours=3), on_event=got.append)
        self.assertEqual(res["status"], backfill.SUPPORTED)
        self.assertEqual((res["recovered"], got), (0, []))

    def test_no_recorder_uri_or_address_reaches_events_or_the_checkpoint(self):
        cloud = FakeCloud([interval()])
        events = []
        runner = recovery.RecoveryRunner(cloud, "agent", "key",
                                         HikvisionShapedArchive(self._segments()), events.append,
                                         chunk_seconds=3600, detector=_FakeDetector(),
                                         frame_provider=lambda d, c, ts: b"JPEGFRAME",
                                         log=lambda *a: None)
        out = runner.run_once(limit=1)
        self.assertEqual(out[0]["status"], "recovered")
        self.assertEqual(len(events), 3, "the visual timeline is still recovered")
        self.assertFalse([e for e in events if e["event_type"] == "recorded_segment"])
        sent = json.dumps({"events": events, "completes": cloud.completes})
        self.assertNotIn("rtsp://", sent)
        self.assertNotIn(RECORDER_IP, sent)

    def test_hashed_segment_ids_still_deduplicate_across_claims(self):
        seen = set()
        cloud = FakeCloud([interval()])
        first = []
        recovery.RecoveryRunner(cloud, "agent", "key", HikvisionShapedArchive(self._segments()),
                                first.append, chunk_seconds=3600,
                                frame_provider=lambda d, c, ts: b"J",
                                log=lambda *a: None).run_once()
        seen.update(cloud.completes[-1]["p_checkpoint"]["seen_keys"])
        again = []
        cloud2 = FakeCloud([interval(checkpoint={"seen_keys": sorted(seen)})])
        recovery.RecoveryRunner(cloud2, "agent", "key", HikvisionShapedArchive(self._segments()),
                                again.append, chunk_seconds=3600,
                                frame_provider=lambda d, c, ts: b"J",
                                log=lambda *a: None).run_once()
        self.assertEqual(len(first), 3)
        self.assertEqual(again, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
