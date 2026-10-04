#!/usr/bin/env python3
"""0.4.4 §1 (deep) — WatchLog AI over RECOVERED footage (no hardware / no codec).

Proves the historical-intelligence pipeline: locate segment -> obtain a historical frame -> run the
detector -> emit RECOVERED intelligence with the FOOTAGE timestamp, a representative snapshot, and
recovered provenance; and that it degrades honestly (no frame -> no fabricated event; detector
discards a junk frame -> quiet; unsupported archive -> reported verbatim). Driver, detector, frame
provider and sink are injected, so nothing here needs a recorder or a video decoder.
"""
from __future__ import annotations

import base64
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

import recovery_ai  # noqa: E402


class FakeDet:
    def __init__(self, label):
        self.label = label

    def as_dict(self):
        return {"label": self.label, "confidence": 0.9, "box": [0, 0, 10, 10]}


class FakeDetector:
    model_name = "fake-yolo"

    def __init__(self, keep=True, dets=None):
        self._keep = keep
        self._dets = dets

    def classify_event(self, jpeg):
        return self._keep, (self._dets if self._dets is not None else [])


class SegDriver:
    """Serves recorded SEGMENTS (and optionally a recorded frame / clip)."""
    def __init__(self, segments, *, status="supported", frame=None, clip=None):
        self._segs = segments
        self._status = status
        self._frame = frame
        self._clip = clip

    def historical_capability(self):
        return {"segments": self._status, "events": "supported", "snapshots": "unsupported"}

    def enumerate_historical_events(self, channel, start, end, cursor=None, limit=500):
        if self._status != "supported":
            return {"status": self._status, "events": [], "next_cursor": None}
        # A real recorder returns only segments within the requested window; honor that so each
        # segment is enumerated in exactly one window (realistic dedupe accounting).
        s, e = datetime.fromisoformat(start) if isinstance(start, str) else start, \
               datetime.fromisoformat(end) if isinstance(end, str) else end
        events = [{"ts": seg["start"], "type": "recorded_segment",
                   "device_event_id": seg.get("id"), "segment": seg}
                  for seg in self._segs
                  if s <= datetime.fromisoformat(seg["start"]) < e]
        return {"status": "supported", "events": events, "next_cursor": None}

    # optional frame sources (presence is what the precedence test checks)
    def get_recorded_frame(self, channel, ts):
        return self._frame

    def get_recorded_segment(self, channel, start, end):
        return {"status": "supported", "bytes": self._clip} if self._clip else {"status": "unsupported"}


SEGS = [{"start": "2026-09-14T22:00:00+00:00", "end": "2026-09-14T22:05:00+00:00", "path": "/a.dav", "id": "seg-a"},
        {"start": "2026-09-14T22:05:00+00:00", "end": "2026-09-14T22:10:00+00:00", "path": "/b.dav", "id": "seg-b"}]
WINDOW = ("2026-09-14T21:00:00+00:00", "2026-09-14T23:00:00+00:00")


class DecodeFrame(unittest.TestCase):
    def test_injected_decoder_used(self):
        self.assertEqual(recovery_ai.decode_jpeg_frame(b"rawclip", decoder=lambda b: b"JPEG"), b"JPEG")

    def test_decoder_failure_is_none(self):
        def boom(_b):
            raise RuntimeError("codec")
        self.assertIsNone(recovery_ai.decode_jpeg_frame(b"rawclip", decoder=boom))

    def test_empty_clip_is_none(self):
        self.assertIsNone(recovery_ai.decode_jpeg_frame(b"", decoder=lambda b: b"x"))

    def test_junk_bytes_without_decoder_is_none(self):
        # no injected decoder; junk bytes are not decodable -> honest None (never fabricated)
        self.assertIsNone(recovery_ai.decode_jpeg_frame(b"not-a-video"))


class RecoveredFrame(unittest.TestCase):
    def test_prefers_recorded_frame(self):
        drv = SegDriver(SEGS, frame=b"FRAME")
        self.assertEqual(recovery_ai.recovered_frame(drv, "1", SEGS[0]["start"]), b"FRAME")

    def test_falls_back_to_clip_decode(self):
        drv = SegDriver(SEGS, frame=None, clip=b"davbytes")
        got = recovery_ai.recovered_frame(drv, "1", SEGS[0]["start"], decoder=lambda b: b"DECODED")
        self.assertEqual(got, b"DECODED")

    def test_none_when_no_source(self):
        drv = SegDriver(SEGS, frame=None, clip=None)
        self.assertIsNone(recovery_ai.recovered_frame(drv, "1", SEGS[0]["start"]))


class AnalyzeSegment(unittest.TestCase):
    def test_no_frame(self):
        status, ev = recovery_ai.analyze_segment(FakeDetector(), None, channel="1", ts=SEGS[0]["start"])
        self.assertEqual(status, "no_frame")
        self.assertIsNone(ev)

    def test_quiet_detector_still_preserves_recovered_snapshot(self):
        status, ev = recovery_ai.analyze_segment(FakeDetector(keep=False), b"JPEG",
                                                 channel="1", ts=SEGS[0]["start"])
        self.assertEqual(status, "snapshot")
        self.assertIsNotNone(ev)
        self.assertEqual(ev["event_type"], "recovered_snapshot")
        self.assertFalse(ev["payload"]["activity_detected"])
        self.assertEqual(base64.b64decode(ev["snapshot_b64"]), b"JPEG")

    def test_recovered_event_has_history_and_provenance(self):
        det = FakeDetector(keep=True, dets=[FakeDet("person")])
        status, ev = recovery_ai.analyze_segment(det, b"JPEGBYTES", channel="3",
                                                 ts=SEGS[0]["start"], device_event_id="seg-a",
                                                 segment=SEGS[0])
        self.assertEqual(status, "recovered")
        self.assertEqual(ev["source"], "recovered")
        self.assertTrue(ev["recovered"])
        self.assertEqual(ev["channel"], "3")
        self.assertEqual(ev["event_type"], "recovered_activity")
        # HISTORICAL footage time is device_ts, not recovery time
        self.assertEqual(datetime.fromisoformat(ev["device_ts"]),
                         datetime(2026, 9, 14, 22, 0, tzinfo=timezone.utc))
        # provenance travels in payload so it survives wl_ingest_events
        self.assertEqual(ev["payload"]["source"], "recovered")
        self.assertTrue(ev["payload"]["recovered"])
        self.assertEqual([o["label"] for o in ev["payload"]["objects"]], ["person"])
        self.assertEqual(base64.b64decode(ev["snapshot_b64"]), b"JPEGBYTES")

    def test_detector_absent_keeps_frame_as_recovered_snapshot(self):
        status, ev = recovery_ai.analyze_segment(None, b"JPEG", channel="1", ts=SEGS[0]["start"])
        self.assertEqual(status, "snapshot")
        self.assertEqual(ev["event_type"], "recovered_snapshot")
        self.assertEqual(ev["payload"]["objects"], [])


class BackfillIntelligence(unittest.TestCase):
    def _run(self, driver, detector, **kw):
        got = []
        summary = recovery_ai.backfill_intelligence(driver, detector, "1", *WINDOW,
                                                    on_event=got.append, **kw)
        return summary, got

    def test_unsupported_archive_reported(self):
        summary, got = self._run(SegDriver(SEGS, status="unsupported"), FakeDetector(),
                                 frame_provider=lambda d, c, t: b"JPEG")
        self.assertEqual(summary["status"], "unsupported")
        self.assertEqual(summary["recovered"], 0)
        self.assertEqual(got, [])

    def test_recovers_intelligence_per_segment(self):
        det = FakeDetector(keep=True, dets=[FakeDet("car")])
        summary, got = self._run(SegDriver(SEGS), det, frame_provider=lambda d, c, t: b"JPEG")
        self.assertEqual(summary["status"], "supported")
        self.assertEqual(summary["recovered"], 2)
        self.assertTrue(all(e["source"] == "recovered" for e in got))
        # historical timestamps preserved (as device_ts) and distinct per segment
        self.assertEqual(sorted(e["device_ts"] for e in got),
                         ["2026-09-14T22:00:00+00:00", "2026-09-14T22:05:00+00:00"])

    def test_no_frame_segments_do_not_emit(self):
        summary, got = self._run(SegDriver(SEGS), FakeDetector(), frame_provider=lambda d, c, t: None)
        self.assertEqual(summary["no_frame"], 2)
        self.assertEqual(summary["recovered"], 0)
        self.assertEqual(got, [])

    def test_recovers_visual_timeline_on_cadence_even_when_quiet(self):
        long_seg = [{
            "start": "2026-09-14T22:00:00+00:00",
            "end": "2026-09-14T22:16:00+00:00",
            "path": "/long.dav", "id": "seg-long",
        }]
        got = []
        summary = recovery_ai.backfill_intelligence(
            SegDriver(long_seg), FakeDetector(keep=False), "1",
            "2026-09-14T22:00:00+00:00", "2026-09-14T22:20:00+00:00",
            on_event=got.append, frame_provider=lambda d, c, t: b"JPEG",
            snapshot_interval_seconds=300,
        )
        self.assertEqual(summary["status"], "supported")
        self.assertEqual(summary["snapshots"], 4)
        self.assertEqual(summary["activity"], 0)
        self.assertEqual([e["device_ts"] for e in got], [
            "2026-09-14T22:00:00+00:00",
            "2026-09-14T22:05:00+00:00",
            "2026-09-14T22:10:00+00:00",
            "2026-09-14T22:15:00+00:00",
        ])
        self.assertTrue(all(e["event_type"] == "recovered_snapshot" for e in got))

    def test_dedupe_across_restart(self):
        det = FakeDetector(keep=True, dets=[FakeDet("person")])
        seen = set()
        s1, g1 = self._run(SegDriver(SEGS), det, frame_provider=lambda d, c, t: b"J", seen=seen)
        s2, g2 = self._run(SegDriver(SEGS), det, frame_provider=lambda d, c, t: b"J", seen=seen)
        self.assertEqual(s1["recovered"], 2)
        self.assertEqual(s2["recovered"], 0)           # re-run recovers nothing new
        self.assertEqual(s2["duplicates"], 2)

    def test_bounded_by_max_frames(self):
        det = FakeDetector(keep=True, dets=[FakeDet("person")])
        summary, got = self._run(SegDriver(SEGS), det, frame_provider=lambda d, c, t: b"J", max_frames=1)
        self.assertEqual(len(got), 1)
        self.assertTrue(summary.get("stopped_at_limit"))


class OverlapDriver(SegDriver):
    """Like the Hikvision/Dahua archive search: returns every segment OVERLAPPING the window,
    with its own start/end, not clipped to the window."""
    def enumerate_historical_events(self, channel, start, end, cursor=None, limit=500):
        s = datetime.fromisoformat(start) if isinstance(start, str) else start
        e = datetime.fromisoformat(end) if isinstance(end, str) else end
        events = [{"ts": seg["start"], "type": "recorded_segment",
                   "device_event_id": seg.get("id"), "segment": seg}
                  for seg in self._segs
                  if datetime.fromisoformat(seg["start"]) < e
                  and (not seg.get("end") or datetime.fromisoformat(seg["end"]) > s)]
        return {"status": "supported", "events": events, "next_cursor": None}


class GapClippedSampling(unittest.TestCase):
    """Only footage inside the recovery window is sampled: the rest was live-monitored."""
    GAP = ("2026-09-14T10:20:00+00:00", "2026-09-14T10:40:00+00:00")

    def _run(self, segments, **kw):
        got, asked = [], []
        summary = recovery_ai.backfill_intelligence(
            OverlapDriver(segments), FakeDetector(keep=False), "1", *self.GAP,
            on_event=got.append, frame_provider=lambda d, c, t: asked.append(t) or b"JPEG",
            snapshot_interval_seconds=300, **kw)
        return summary, got, asked

    def test_an_overlapping_segment_is_sampled_only_inside_the_gap(self):
        summary, got, asked = self._run([{"start": "2026-09-14T10:00:00+00:00",
                                          "end": "2026-09-14T11:00:00+00:00", "id": "seg-hour"}])
        self.assertEqual([e["device_ts"] for e in got], [
            "2026-09-14T10:20:00+00:00", "2026-09-14T10:25:00+00:00",
            "2026-09-14T10:30:00+00:00", "2026-09-14T10:35:00+00:00"])
        lo, hi = (datetime.fromisoformat(v) for v in self.GAP)
        self.assertTrue(all(lo <= t < hi for t in asked), "a frame outside the gap was fetched")

    def test_a_long_segment_does_not_starve_the_frame_cap(self):
        summary, got, asked = self._run([{"start": "2026-09-14T00:00:00+00:00",
                                          "end": "2026-09-14T23:59:00+00:00", "id": "seg-day"}],
                                        max_frames=40)
        self.assertEqual(summary["frames"], 4)
        self.assertFalse(summary.get("stopped_at_limit"))
        self.assertEqual(got[0]["device_ts"], "2026-09-14T10:20:00+00:00")

    def test_a_segment_without_an_end_before_the_gap_is_not_sampled(self):
        summary, got, asked = self._run([{"start": "2026-09-14T10:00:00+00:00", "id": "seg-open"}])
        self.assertEqual((got, asked), ([], []))


class MediaInspect(unittest.TestCase):
    def test_media_kind_by_magic(self):
        self.assertEqual(recovery_ai.media_kind(b"\xff\xd8\xff\xe0blah"), "jpeg")
        self.assertEqual(recovery_ai.media_kind(b"\x00\x00\x00\x18ftypmp42"), "mp4")
        self.assertEqual(recovery_ai.media_kind(b"DHAV....."), "dav")
        self.assertEqual(recovery_ai.media_kind(b"random-bytes-here"), "unknown")
        self.assertEqual(recovery_ai.media_kind(b""), "empty")

    def test_inspect_prefers_recorded_frame(self):
        drv = SegDriver(SEGS, frame=b"\xff\xd8\xffJPEGFRAME")
        frame, diag = recovery_ai.inspect_and_decode(drv, "1", SEGS[0]["start"])
        self.assertEqual(frame, b"\xff\xd8\xffJPEGFRAME")
        self.assertEqual(diag["decoder"], "recorder_frame")
        self.assertTrue(diag["decoded"])
        self.assertEqual(diag["kind"], "jpeg")

    def test_inspect_decodes_clip_with_diagnostics(self):
        drv = SegDriver(SEGS, frame=None, clip=b"DHAV" + b"\x00" * 40)
        frame, diag = recovery_ai.inspect_and_decode(drv, "1", SEGS[0]["start"],
                                                     decoder=lambda b: b"DECODED")
        self.assertEqual(frame, b"DECODED")
        self.assertEqual(diag["kind"], "dav")
        self.assertEqual(diag["media_size"], 44)
        self.assertTrue(diag["decoded"])
        self.assertEqual(diag["decoder"], "injected")

    def test_inspect_no_source_is_not_decoded(self):
        drv = SegDriver(SEGS, frame=None, clip=None)
        frame, diag = recovery_ai.inspect_and_decode(drv, "1", SEGS[0]["start"])
        self.assertIsNone(frame)
        self.assertFalse(diag["decoded"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
