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
import programdata_sandbox  # noqa: E402,F401  (keeps Agent state out of the real ProgramData)

from dahua_fake_recorder import (  # noqa: E402
    FMT, JPEG, FakeCloud, FakeDahua, FakeRecorder, FakeResponse, continuous_files, local,
    pinned_datetime)
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


class _Ledger:
    """0098 recovery ledger: a claim takes pending or (stale) in_progress rows and adds an attempt."""

    def __init__(self):
        self.iv = dict(_interval(), attempts=0, detail={})
        self.completes = []

    def call(self, fn, **kw):
        if fn == "wl_agent_claim_recovery":
            if self.iv["status"] not in ("pending", "in_progress"):
                return []
            self.iv["status"], self.iv["attempts"] = "in_progress", self.iv["attempts"] + 1
            return [{k: self.iv[k] for k in ("id", "started_at", "ended_at", "cameras",
                                             "checkpoint", "attempts")}]
        if fn == "wl_complete_recovery":
            self.completes.append(kw)
            self.iv["status"], self.iv["checkpoint"] = kw["p_status"], kw["p_checkpoint"]
            self.iv["detail"].update(kw.get("p_detail") or {})
            return {"ok": True}
        raise AssertionError(fn)


class _Answers:
    """A recorder session that answers every CGI call the same way."""

    def __init__(self, answer):
        self.answer, self.auth, self.calls = answer, None, 0

    def get(self, url, params=None, timeout=None, stream=False):
        self.calls += 1
        return self.answer()


class _Status:
    def __init__(self, status, text="Error"):
        self.status_code, self.text = status, text

    def close(self):
        pass


def _offline():
    import requests
    raise requests.ConnectionError("recorder offline")


class TransientArchiveErrors(unittest.TestCase):
    """An unreachable or busy recorder is retried later, like the Hikvision archive; a refused login
    or an answer that cannot be read ends the interval on the claim that saw it."""

    def setUp(self):
        da.install()

    def _claims(self, answer, claims=5):
        drv = FakeDahua(FakeRecorder(PC_NOW))
        drv.s = _Answers(answer)
        ledger = _Ledger()
        runner = recovery.RecoveryRunner(ledger, "agent", "key", drv, [].append,
                                         frame_provider=lambda d, c, ts: JPEG,
                                         log=lambda *a: None)
        for n in range(1, claims + 1):
            runner.run_once()
            if ledger.iv["status"] not in ("pending", "in_progress"):
                return ledger, n
        return ledger, None

    def test_transient_failures_raise_from_the_search(self):
        from drivers.base import NvrUnreachable
        for answer, error in ((_offline, NvrUnreachable), (lambda: _Status(503), da.RecorderBusy),
                              (lambda: _Status(429), da.RecorderBusy)):
            drv = FakeDahua(FakeRecorder(PC_NOW))
            drv.s = _Answers(answer)
            with self.assertRaises(error):
                da.enumerate_historical_events(drv, "1", G0, G1)

    def test_an_unreachable_recorder_backs_off_instead_of_ending(self):
        ledger, _ = self._claims(_offline, claims=1)
        self.assertEqual(ledger.iv["status"], "in_progress")
        self.assertEqual(ledger.iv["checkpoint"]["cursor"], G0.isoformat())
        self.assertEqual(ledger.iv["checkpoint"].get("errors"), 1)

    def test_a_busy_recorder_backs_off_then_ends_after_bounded_claims(self):
        ledger, claims = self._claims(lambda: _Status(503))
        self.assertEqual(claims, recovery.DEFAULT_MAX_ERROR_ATTEMPTS)
        self.assertEqual(ledger.iv["status"], "unrecoverable")
        self.assertEqual(ledger.iv["detail"].get("reason"), "archive_error")

    def test_a_refused_login_ends_on_the_first_claim(self):
        ledger, claims = self._claims(lambda: _Status(401))
        self.assertEqual((claims, ledger.iv["status"]), (1, "unrecoverable"))

    def test_an_unreadable_recorder_clock_ends_on_the_first_claim(self):
        ledger, claims = self._claims(lambda: _Status(200, "result=not-a-time"))
        self.assertEqual((claims, ledger.iv["status"]), (1, "unrecoverable"))

    def test_setup_proof_and_retention_still_report_unknown(self):
        import retention
        drv = FakeDahua(FakeRecorder(PC_NOW))
        drv.s = _Answers(_offline)
        self.assertEqual(da.prove_recorder_archive(drv, "1")["status"], "unknown")
        self.assertEqual(retention.estimate_retention(drv, "1")["status"], "unknown")


class ClockWithUtcOffset(unittest.TestCase):
    """A getCurrentTime value that names its UTC offset ("15:00:00+05:00") still gives the
    recorder's wall time: that wall time is what stamps its recordings. Read as UTC instead, the
    measured offset was ~0 and every search asked for the wrong five hours."""

    class OffsetClockRecorder(FakeRecorder):
        def get(self, url, params=None, timeout=None, stream=False):
            if url.endswith("global.cgi"):
                self.calls.append(("global.cgi", dict(params or {}), timeout, stream))
                return FakeResponse(text=f"result={self.wall_clock().strftime(FMT)}+05:00\r\n")
            return super().get(url, params=params, timeout=timeout, stream=stream)

    def setUp(self):
        da.install()
        patcher = mock.patch.object(da, "datetime", pinned_datetime(PC_NOW))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_device_clock_keeps_the_wall_time_it_names(self):
        self.assertEqual(da._parse_device_clock("result=2026-10-04 15:00:00+05:00"),
                         local("2026-10-04 15:00:00"))

    def test_search_uses_the_recorder_wall_clock_window(self):
        rec = self.OffsetClockRecorder(PC_NOW, zone=timedelta(hours=5), files=continuous_files(
            local("2026-10-04 12:00:00"), local("2026-10-04 14:00:00")))
        res = da.enumerate_historical_events(FakeDahua(rec), "1", G0 - timedelta(minutes=30),
                                             G0 + timedelta(minutes=30))
        find = rec.calls_to("mediaFileFind.cgi", "findFile")[0][1]
        self.assertEqual((find["condition.StartTime"], find["condition.EndTime"]),
                         ("2026-10-04 12:30:00", "2026-10-04 13:30:00"))
        self.assertEqual([e["ts"] for e in res["events"]],
                         ["2026-10-04T07:30:00Z", "2026-10-04T08:00:00Z"])


class SegmentTimesReplayOnTheSameClock(unittest.TestCase):
    """Segment times come back on the agent clock (recorder wall time minus the measured offset,
    drift included). recovery_ai fetches a sample either through the installed
    get_recorded_segment or, where a driver offers no segment getter, through get_clip; both must
    land on the recorder wall time the segment was found at, even on a drifting recorder."""

    def setUp(self):
        da.install()
        patcher = mock.patch.object(da, "datetime", pinned_datetime(PC_NOW))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.rec = FakeRecorder(PC_NOW, zone=timedelta(hours=5), drift=timedelta(seconds=40),
                                files=continuous_files(local("2026-10-04 12:00:00"),
                                                       local("2026-10-04 14:00:00")))
        self.drv = FakeDahua(self.rec)
        rows = da.enumerate_historical_events(self.drv, "1", G0 - timedelta(minutes=30),
                                              G0 + timedelta(minutes=30))["events"]
        [self.segment] = [r["segment"] for r in rows if r["segment"]["path"].endswith("130000.dav")]

    def _fetch(self, driver):
        import recovery_ai
        frame = recovery_ai.recovered_frame(driver, "1", self.segment["start"],
                                            decoder=lambda clip, *_offset: JPEG)
        self.assertEqual(frame, JPEG)
        return self.rec.loadfile_windows()[-1][0]

    def test_the_segment_getter_replays_the_recorder_wall_time(self):
        self.assertEqual(self.segment["start"], "2026-10-04T07:59:20Z")
        self.assertEqual(self._fetch(self.drv), local("2026-10-04 13:00:00"))

    def test_the_get_clip_fallback_replays_the_recorder_wall_time(self):
        class ClipOnly:                     # no segment getter: recovery_ai falls back to get_clip
            def __init__(self, drv):
                self.get_clip = drv.get_clip

        self.assertEqual(self._fetch(ClipOnly(self.drv)), local("2026-10-04 13:00:00"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
