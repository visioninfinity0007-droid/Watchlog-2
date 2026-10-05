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
from datetime import datetime, timedelta, timezone
from pathlib import Path

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

import recovery_ai  # noqa: E402
from drivers.base import DriverError  # noqa: E402


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
# One hour, all of it within the hourly search window that holds SEGS: an hour of the window
# with no recording would make the pass partial (its footage was never examined).
WINDOW = ("2026-09-14T22:00:00+00:00", "2026-09-14T23:00:00+00:00")


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
        # The second segment was never examined: the window is not recovered (MNVR-062).
        self.assertEqual(summary["status"], "partial")
        self.assertEqual(summary["recovered"], 1)

    def test_a_frame_budget_that_covers_every_sample_is_not_a_cut(self):
        summary, got = self._run(SegDriver(SEGS), None, frame_provider=lambda d, c, t: b"J",
                                 max_frames=2)
        self.assertEqual((summary["status"], len(got)), ("supported", 2))
        self.assertFalse(summary.get("stopped_at_limit"))

    def test_samples_already_recovered_do_not_use_up_the_budget_check(self):
        seen = set()
        self._run(SegDriver(SEGS[:1]), None, frame_provider=lambda d, c, t: b"J", seen=seen)
        summary, got = self._run(SegDriver(SEGS), None, frame_provider=lambda d, c, t: b"J",
                                 seen=seen, max_frames=1)
        self.assertEqual((summary["status"], summary["duplicates"], len(got)), ("supported", 1, 1))


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


class _AnswersAnyWindow(SegDriver):
    """A recorder that answers the search for the gap with segments stamped hours away from it,
    as when its local time is read as UTC (MNVR-019): every row comes back, whatever the window."""
    def enumerate_historical_events(self, channel, start, end, cursor=None, limit=500):
        return {"status": "supported", "next_cursor": None,
                "events": [{"ts": seg["start"], "type": "recorded_segment",
                            "device_event_id": seg.get("id"), "segment": seg}
                           for seg in self._segs]}


class SegmentsOutsideTheWindow(unittest.TestCase):
    """Segments that do not overlap the window after clipping are not footage of the gap."""
    GAP = GapClippedSampling.GAP
    SHIFTED = [{"start": "2026-09-14T15:00:00+00:00", "end": "2026-09-14T16:00:00+00:00",
                "id": "seg-local-hour"}]

    def test_the_window_stays_unknown_and_nothing_is_fetched(self):
        asked = []
        summary = recovery_ai.backfill_intelligence(
            _AnswersAnyWindow(self.SHIFTED), None, "1", *self.GAP, on_event=[].append,
            frame_provider=lambda d, c, t: asked.append(t) or b"JPEG")
        self.assertEqual(summary["status"], recovery_ai.UNKNOWN)
        self.assertEqual((summary["frames"], asked), (0, []))
        self.assertIn("window", summary["reason"])

    def test_an_interval_with_no_footage_examined_is_not_recovered(self):
        import recovery

        class Cloud:
            def __init__(self):
                self.completes = []

            def call(self, fn, **kw):
                if fn == "wl_agent_claim_recovery":
                    return [] if self.completes else [{
                        "id": "iv-1", "started_at": GapClippedSampling.GAP[0],
                        "ended_at": GapClippedSampling.GAP[1], "cameras": ["1"],
                        "checkpoint": {}, "attempts": 1}]
                self.completes.append(kw)
                return {"ok": True}

        cloud = Cloud()
        out = recovery.RecoveryRunner(cloud, "agent", "key", _AnswersAnyWindow(self.SHIFTED),
                                      [].append, frame_provider=lambda d, c, t: b"JPEG",
                                      log=lambda *a: None).run_once(limit=1)
        self.assertEqual(out[0]["status"], "partial")
        self.assertEqual(cloud.completes[-1]["p_status"], "partial")

    def test_rows_already_recovered_in_an_earlier_claim_still_count_as_inside(self):
        seg = [{"start": "2026-09-14T10:00:00+00:00", "end": "2026-09-14T11:00:00+00:00",
                "id": "seg-hour"}]
        seen = set()
        for _ in range(2):
            summary = recovery_ai.backfill_intelligence(
                _AnswersAnyWindow(seg), None, "1", *self.GAP, seen=seen, on_event=[].append,
                frame_provider=lambda d, c, t: b"JPEG")
        self.assertEqual(summary["status"], recovery_ai.SUPPORTED)
        self.assertEqual(summary["duplicates"], 4)


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



class _WrappedClipDriver:
    """Mirrors hikvision_archive/dahua_archive install(): get_recorded_segment IS get_clip."""
    def __init__(self, outcome):
        self.outcome, self.calls = outcome, 0

    def get_clip(self, channel, start, end):
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    def get_recorded_segment(self, channel, start, end):
        return {"status": "supported", "bytes": self.get_clip(channel, start, end)}


class SingleClipAttempt(unittest.TestCase):
    """A failed recorded-clip download is not repeated through get_clip for the same sample."""
    TS = SEGS[0]["start"]

    def test_recovered_frame_does_not_repeat_a_failed_download(self):
        for outcome in (None, DriverError("archive download timed out")):
            drv = _WrappedClipDriver(outcome)
            self.assertIsNone(recovery_ai.recovered_frame(drv, "1", self.TS, decoder=lambda b: b"J"))
            self.assertEqual(drv.calls, 1, f"get_clip ran {drv.calls}x for {outcome!r}")

    def test_inspect_and_decode_does_not_repeat_a_failed_download(self):
        for outcome in (None, DriverError("archive download timed out")):
            drv = _WrappedClipDriver(outcome)
            frame, diag = recovery_ai.inspect_and_decode(drv, "1", self.TS, decoder=lambda b: b"J")
            self.assertIsNone(frame)
            self.assertFalse(diag["decoded"])
            self.assertEqual(drv.calls, 1, f"get_clip ran {drv.calls}x for {outcome!r}")

    def test_an_unsupported_segment_getter_still_falls_back_to_get_clip(self):
        class ClipOnly:
            calls = 0

            def get_recorded_segment(self, channel, start, end):
                return {"status": "unsupported", "bytes": None}

            def get_clip(self, channel, start, end):
                self.calls += 1
                return b"clip"

        drv = ClipOnly()
        self.assertEqual(recovery_ai.recovered_frame(drv, "1", self.TS, decoder=lambda b: b"J"), b"J")
        self.assertEqual(drv.calls, 1)

    def test_vendor_archive_wrappers_download_once_per_failing_sample(self):
        import dahua_archive
        import hikvision_archive
        from drivers.dahua import DahuaDriver
        from drivers.hikvision import HikvisionDriver

        for module, cls in ((hikvision_archive, HikvisionDriver), (dahua_archive, DahuaDriver)):
            calls = []
            original = module.get_clip

            def counting(driver, channel, start, end, **_clock):
                calls.append(channel)
                return None

            module.get_clip = counting
            try:
                module.install()
                drv = cls("http://192.0.2.10", "local-user", "local-password", timeout=2)
                recovery_ai.recovered_frame(drv, "1", self.TS, decoder=lambda b: b"J")
                recovery_ai.inspect_and_decode(drv, "1", self.TS, decoder=lambda b: b"J")
            finally:
                module.get_clip = original
                module.install()
            self.assertEqual(len(calls), 2, f"{module.__name__}: {len(calls)} downloads for 2 samples")



def _media(start, seconds):
    """Fake recorded media: where it starts and how long it runs (no codec needed)."""
    return f"MEDIA|{start.isoformat()}|{int(seconds)}".encode()


def _media_decoder(clip, offset=0.0):
    """Decode the frame ``offset`` seconds into fake media; None past its end."""
    _tag, start, seconds = clip.decode().split("|")
    if offset > int(seconds):
        return None
    return b"FRAME@" + (datetime.fromisoformat(start) + timedelta(seconds=offset)).isoformat().encode()


class _MediaArchive(OverlapDriver):
    """mode='segment': the download is the WHOLE recorded segment, whatever window was asked for
    (a Hikvision playbackURI names the segment). mode='window': the download is cut to the window,
    starting ``slack`` seconds early (at the key frame before it). mode='short': the whole-segment
    download is cut short (2 minutes)."""
    def __init__(self, segments, mode, slack=0):
        super().__init__(segments)
        self.mode, self.slack = mode, slack

    def get_recorded_frame(self, channel, ts):
        return None

    def get_recorded_segment(self, channel, start, end):
        if self.mode == "window":
            begins = start - timedelta(seconds=self.slack)
            return {"status": "supported", "bytes": _media(begins, (end - begins).total_seconds())}
        for seg in self._segs:
            s, e = datetime.fromisoformat(seg["start"]), datetime.fromisoformat(seg["end"])
            if s <= start < e:
                length = 120 if self.mode == "short" else (e - s).total_seconds()
                return {"status": "supported", "bytes": _media(s, length)}
        return {"status": "supported", "bytes": None}


class RecoveredFramePosition(unittest.TestCase):
    """A recovered snapshot is stamped with the footage time of the frame it actually holds."""
    SEG = [{"start": "2026-09-14T07:30:00+00:00", "end": "2026-09-14T07:50:00+00:00", "id": "seg-20m"}]

    def _run(self, mode, slack=0):
        got = []
        summary = recovery_ai.backfill_intelligence(
            _MediaArchive(self.SEG, mode, slack), FakeDetector(keep=False), "1",
            "2026-09-14T07:00:00+00:00", "2026-09-14T08:00:00+00:00",
            on_event=got.append, decoder=_media_decoder, snapshot_interval_seconds=300)
        frames = [base64.b64decode(e["snapshot_b64"]).decode() for e in got]
        return summary, got, frames

    def test_whole_segment_download_is_sampled_at_each_sample_position(self):
        summary, got, frames = self._run("segment")
        self.assertEqual(frames, ["FRAME@2026-09-14T07:30:00+00:00", "FRAME@2026-09-14T07:35:00+00:00",
                                  "FRAME@2026-09-14T07:40:00+00:00", "FRAME@2026-09-14T07:45:00+00:00"])
        for event, frame in zip(got, frames):
            self.assertEqual(frame, "FRAME@" + event["device_ts"])

    def test_window_cut_download_keeps_its_first_frame(self):
        summary, got, frames = self._run("window")
        self.assertEqual(len(frames), 4)
        for event, frame in zip(got, frames):
            self.assertEqual(frame, "FRAME@" + event["device_ts"])

    def test_window_cut_download_from_an_earlier_key_frame_keeps_its_first_frame(self):
        # Cut to the request but starting at the preceding key frame: a few seconds longer than
        # asked for, still a clip of the sample, never mistaken for media of unknown start.
        for slack in (2, 3, 4):
            summary, got, frames = self._run("window", slack)
            self.assertEqual((len(frames), summary["no_frame"]), (4, 0), f"slack {slack}s")
            for event, frame in zip(got, frames):
                shows = datetime.fromisoformat(frame.split("@", 1)[1])
                self.assertEqual(shows, datetime.fromisoformat(event["device_ts"])
                                 - timedelta(seconds=slack))

    def test_media_of_unknown_start_is_not_stamped_with_the_sample_time(self):
        summary, got, frames = self._run("short")
        # Only the sample at the segment start can be placed; the rest stay honest no-frames.
        self.assertEqual(frames, ["FRAME@2026-09-14T07:30:00+00:00"])
        self.assertEqual(summary["no_frame"], 3)

    def test_recovered_frame_seeks_into_a_whole_segment(self):
        drv = _MediaArchive(self.SEG, "segment")
        ts = datetime(2026, 9, 14, 7, 42, 30, tzinfo=timezone.utc)
        frame = recovery_ai.recovered_frame(drv, "1", ts, decoder=_media_decoder,
                                            segment_start=self.SEG[0]["start"])
        self.assertEqual(frame, b"FRAME@" + ts.isoformat().encode())


    @unittest.skipUnless(recovery_ai._ffmpeg_exe(), "FFmpeg not available")
    def test_ffmpeg_decode_follows_the_same_rules(self):
        """The production decoder: seek inside media, nothing past its end."""
        import os
        import subprocess
        import tempfile

        def synth(seconds):
            fd, path = tempfile.mkstemp(suffix=".mpg")
            os.close(fd)
            try:
                subprocess.run([recovery_ai._ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
                                "-nostdin", "-y", "-f", "lavfi",
                                "-i", f"testsrc=size=320x240:rate=10:duration={seconds}",
                                "-c:v", "mpeg2video", "-f", "mpeg", path],
                               check=True, timeout=60)
                return open(path, "rb").read()
            finally:
                os.unlink(path)

        seg_start = datetime(2026, 9, 14, 7, 30, tzinfo=timezone.utc)
        whole = synth(20)                                  # the whole 20 s "segment"
        at_12 = recovery_ai._frame_at(whole, seg_start + timedelta(seconds=12), seg_start, 6, None)
        self.assertTrue(at_12 and at_12.startswith(b"\xff\xd8\xff"))
        self.assertNotEqual(at_12, recovery_ai.decode_jpeg_frame(whole))
        # Past the end of media that is longer than the request: its start is unknown.
        self.assertIsNone(recovery_ai._frame_at(whole, seg_start + timedelta(seconds=40),
                                                seg_start, 6, None))
        # Media cut to the 6 s request: its first frame is the sample.
        window = synth(5)
        self.assertEqual(recovery_ai._frame_at(window, seg_start + timedelta(seconds=300),
                                               seg_start, 6, None),
                         recovery_ai.decode_jpeg_frame(window))
        # Cut to the request from the key frame 2-4 s before it: still its first frame.
        for slack in (2, 3, 4):
            window = synth(6 + slack)
            self.assertEqual(recovery_ai._frame_at(window, seg_start + timedelta(seconds=300),
                                                   seg_start, 6, None),
                             recovery_ai.decode_jpeg_frame(window), f"slack {slack}s")


if __name__ == "__main__":
    unittest.main(verbosity=2)
