#!/usr/bin/env python3
"""A failing archive read never leaves a recovery interval in progress forever (MNVR-059).

hikvision_archive.search_recordings raises on an HTTP 4xx, a 401/403 or invalid XML, and
enumerate_historical_events does not catch it. RecoveryRunner had no handler either, so the
exception escaped run_once without any wl_complete_recovery call: the interval stayed
in_progress, wl_agent_claim_recovery reclaimed it every 15 minutes with attempts+1, and the same
rejected search was replayed against the recorder forever.

Now an archive failure checkpoints the interval from the first chunk that failed and backs off (the
server re-offers it once the claim goes stale); after a few consecutive failed claims, or once
too many claims in a row have not moved the cursor, the interval is completed with a terminal
status. A claim that yields to live monitoring still counts its failed read, and a long interval
that yields after every chunk is not cut short by the attempts cap.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import backfill  # noqa: E402
import hikvision_archive as ha  # noqa: E402
import recovery  # noqa: E402
from drivers.hikvision import HikvisionDriver  # noqa: E402

T0 = datetime(2026, 6, 1, 17, 0, tzinfo=timezone.utc)
CAM1 = "11111111-1111-4111-8111-111111111111"
CAM3 = "33333333-3333-4333-8333-333333333333"

SEARCH_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<CMSearchResult xmlns="http://www.isapi.org/ver20/XMLSchema">
  <responseStatusStrg>OK</responseStatusStrg>
  <matchList><searchMatchItem>
    <trackID>%d</trackID>
    <timeSpan><startTime>2026-06-01T17:10:00Z</startTime><endTime>2026-06-01T17:12:00Z</endTime></timeSpan>
    <mediaSegmentDescriptor><playbackURI>rtsp://192.0.2.64/Streaming/tracks/%d/?starttime=20260601T171000Z</playbackURI></mediaSegmentDescriptor>
  </searchMatchItem></matchList>
</CMSearchResult>"""


class _Response:
    def __init__(self, content=b"", status=200):
        self.content, self.status_code, self.headers = content, status, {}
        self.text = content.decode("utf-8", "replace")

    def close(self):
        pass


class _Session:
    """ISAPI search endpoint. ``behaviour(track)`` returns 'ok', 'reject' (HTTP 400) or 'down'."""

    def __init__(self, behaviour):
        self.behaviour, self.searches, self.auth = behaviour, [], None

    def post(self, url, data=None, headers=None, stream=False, timeout=None):
        assert url.endswith("/ISAPI/ContentMgmt/search"), url
        body = data.decode()
        track = int(body.split("<trackID>", 1)[1].split("<", 1)[0])
        self.searches.append(track)
        verdict = self.behaviour(track)
        if verdict == "down":
            raise requests.ConnectionError("recorder stopped answering")
        if verdict == "reject":
            return _Response(b"<ResponseStatus><statusString>Invalid XML Format</statusString>"
                             b"</ResponseStatus>", status=400)
        return _Response(SEARCH_XML % (track, track))

    def close(self):
        pass


class _Ledger:
    """0098 recovery ledger: a claim takes pending or stale in_progress rows and adds an attempt.
    Every in_progress row counts as stale here, i.e. each claim is one stale-window later."""

    def __init__(self, attempts=0):
        self.iv = {"id": "iv-1", "started_at": T0.isoformat(),
                   "ended_at": (T0 + timedelta(hours=1)).isoformat(),
                   "cameras": [CAM1, CAM3], "status": "pending", "checkpoint": {},
                   "attempts": attempts, "detail": {}}
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


def _driver(behaviour):
    ha.install()
    d = HikvisionDriver("http://192.0.2.64", "admin", "secret", timeout=2)
    d.s = _Session(behaviour)
    return d


def _runner(ledger, driver, **kw):
    return recovery.RecoveryRunner(ledger, "agent", "key", driver, [].append, chunk_seconds=3600,
                                   camera_channels={CAM1: "1", CAM3: "3"},
                                   frame_provider=lambda d, c, t: b"\xff\xd8\xffJPEG",
                                   log=lambda *a: None, **kw)


def _claim_until_terminal(ledger, runner, claims=10):
    for n in range(1, claims + 1):
        runner.run_once(limit=1)
        if ledger.iv["status"] not in ("pending", "in_progress"):
            return n
    return None


class ArchiveSearchRejection(unittest.TestCase):
    def test_a_rejected_search_ends_in_a_terminal_status(self):
        ledger = _Ledger()
        driver = _driver(lambda track: "reject")
        claims = _claim_until_terminal(ledger, _runner(ledger, driver))
        self.assertEqual(claims, recovery.DEFAULT_MAX_ERROR_ATTEMPTS,
                         "the interval must end after a bounded number of failed claims")
        self.assertEqual(ledger.iv["status"], "unrecoverable")
        self.assertEqual(ledger.iv["detail"].get("reason"), "archive_error")
        # One failing search per camera per claim, not one per chunk and step.
        self.assertLessEqual(len(driver.s.searches), 2 * claims)

    def test_first_failed_claim_backs_off_instead_of_giving_up(self):
        ledger = _Ledger()
        _runner(ledger, _driver(lambda track: "reject")).run_once(limit=1)
        self.assertEqual(ledger.iv["status"], "in_progress")
        self.assertEqual(ledger.iv["checkpoint"]["cursor"], T0.isoformat())
        self.assertEqual(ledger.iv["checkpoint"].get("errors"), 1)

    def test_one_rejected_camera_does_not_block_the_others(self):
        ledger = _Ledger()
        driver = _driver(lambda track: "reject" if track == 101 else "ok")
        _claim_until_terminal(ledger, _runner(ledger, driver))
        self.assertEqual(ledger.iv["status"], "partial")
        self.assertIn(301, driver.s.searches)

    def test_a_transient_failure_is_retried_and_then_recovered(self):
        calls = {"n": 0}

        def flaky(track):
            calls["n"] += 1
            return "down" if calls["n"] <= 2 else "ok"

        ledger = _Ledger()
        runner = _runner(ledger, _driver(flaky))
        runner.run_once(limit=1)
        self.assertEqual(ledger.iv["status"], "in_progress", "a transient failure is not terminal")
        runner.run_once(limit=1)
        self.assertEqual(ledger.iv["status"], "recovered")
        self.assertNotIn("errors", ledger.iv["checkpoint"])


class ClaimAttemptCap(unittest.TestCase):
    def test_an_interval_out_of_attempts_is_closed_without_touching_the_recorder(self):
        ledger = _Ledger(attempts=recovery.DEFAULT_MAX_ATTEMPTS)
        driver = _driver(lambda track: "ok")
        _runner(ledger, driver).run_once(limit=1)
        self.assertEqual(ledger.iv["status"], "unrecoverable")
        self.assertEqual(ledger.iv["detail"].get("reason"), "attempts_exhausted")
        self.assertEqual(driver.s.searches, [])

    def test_an_interval_with_earlier_progress_is_closed_as_partial(self):
        ledger = _Ledger(attempts=recovery.DEFAULT_MAX_ATTEMPTS)
        ledger.iv["checkpoint"] = {"cursor": (T0 + timedelta(minutes=30)).isoformat(),
                                   "seen_keys": ["ai:seg:2026-06-01T17:10:00+00:00"]}
        _runner(ledger, _driver(lambda track: "ok")).run_once(limit=1)
        self.assertEqual(ledger.iv["status"], "partial")
        self.assertEqual(ledger.iv["detail"].get("reason"), "attempts_exhausted")


class _YieldingLedger(_Ledger):
    """A longer interval, and a live backlog that builds up after every chunk a claim reads and
    drains before the next claim (``drain``)."""

    def __init__(self, hours, attempts=0):
        super().__init__(attempts)
        self.iv["ended_at"] = (T0 + timedelta(hours=hours)).isoformat()
        self.chunks = 0

    def call(self, fn, **kw):
        if fn == "wl_complete_recovery" and kw["p_status"] == "in_progress":
            self.chunks += 1
        return super().call(fn, **kw)

    def drain(self):
        self.chunks = 0

    def live_backlog(self):
        return self.chunks >= 1


def _claim_draining(ledger, runner, claims=40):
    for n in range(1, claims + 1):
        ledger.drain()
        runner.run_once(limit=1)
        if ledger.iv["status"] not in ("pending", "in_progress"):
            return n
    return None


class AttemptsCountOnlyClaimsWithoutProgress(unittest.TestCase):
    """The attempts cap is for claims that get nowhere (stale claims, crash loops), not for a long
    interval that yields to live monitoring after every chunk it reads."""

    def test_a_long_interval_that_yields_every_chunk_is_read_to_the_end(self):
        hours = 6
        ledger = _YieldingLedger(hours)
        ledger.iv["cameras"] = [CAM1]
        events = []
        driver = backfill.ReferenceArchiveDriver(
            [{"ts": (T0 + timedelta(minutes=30 + 60 * h)).isoformat(), "type": "person",
              "device_event_id": f"E{h}"} for h in range(hours)], page_size=10)
        runner = recovery.RecoveryRunner(ledger, "agent", "key", driver, events.append,
                                         chunk_seconds=3600, camera_channels={CAM1: "1"},
                                         live_pending=ledger.live_backlog, max_attempts=3,
                                         log=lambda *a: None)
        claims = _claim_draining(ledger, runner)
        self.assertEqual(ledger.iv["status"], "recovered", ledger.iv["detail"])
        self.assertEqual((claims, len(events)), (hours, hours))

    def test_claims_since_the_last_progress_are_still_capped(self):
        ledger = _Ledger(attempts=10)
        ledger.iv["checkpoint"] = {"cursor": (T0 + timedelta(minutes=30)).isoformat(),
                                   "progress_attempt": 6}
        driver = _driver(lambda track: "ok")
        _runner(ledger, driver, max_attempts=4).run_once(limit=1)
        self.assertEqual(ledger.iv["detail"].get("reason"), "attempts_exhausted")
        self.assertEqual(driver.s.searches, [])

    def test_recent_progress_keeps_an_often_claimed_interval_going(self):
        ledger = _Ledger(attempts=10)
        ledger.iv["checkpoint"] = {"cursor": T0.isoformat(), "progress_attempt": 8}
        driver = _driver(lambda track: "ok")
        _runner(ledger, driver, max_attempts=4).run_once(limit=1)
        self.assertEqual(ledger.iv["status"], "recovered")
        self.assertTrue(driver.s.searches)

    def test_a_claim_that_moves_the_cursor_records_its_attempt(self):
        ledger = _YieldingLedger(3, attempts=4)
        _runner(ledger, _driver(lambda track: "ok"), live_pending=ledger.live_backlog).run_once()
        self.assertEqual(ledger.iv["status"], "in_progress")
        self.assertEqual(ledger.iv["checkpoint"]["cursor"], (T0 + timedelta(hours=1)).isoformat())
        self.assertEqual(ledger.iv["checkpoint"]["progress_attempt"], 5)


class FailedReadsCountWhenTheClaimYields(unittest.TestCase):
    def test_a_failed_read_is_counted_even_when_the_claim_then_yields(self):
        ledger = _YieldingLedger(2)
        runner = _runner(ledger, _driver(lambda track: "reject" if track == 101 else "ok"),
                         live_pending=ledger.live_backlog)
        runner.run_once(limit=1)
        self.assertEqual(ledger.iv["status"], "in_progress")
        self.assertEqual(ledger.iv["checkpoint"]["cursor"], T0.isoformat())
        self.assertEqual(ledger.iv["checkpoint"].get("errors"), 1)

    def test_consecutive_failed_claims_end_the_interval_even_if_each_yields(self):
        ledger = _YieldingLedger(2)
        driver = _driver(lambda track: "reject" if track == 101 else "ok")
        claims = _claim_draining(ledger, _runner(ledger, driver, live_pending=ledger.live_backlog))
        self.assertEqual(claims, recovery.DEFAULT_MAX_ERROR_ATTEMPTS)
        self.assertEqual(ledger.iv["status"], "partial")
        self.assertEqual(ledger.iv["detail"].get("reason"), "archive_error")

    def test_a_claim_that_reads_without_failure_resets_the_count(self):
        ledger = _YieldingLedger(3)
        ledger.iv["checkpoint"] = {"cursor": T0.isoformat(), "errors": 2}
        _runner(ledger, _driver(lambda track: "ok"), live_pending=ledger.live_backlog).run_once()
        self.assertEqual(ledger.iv["status"], "in_progress")
        self.assertNotIn("errors", ledger.iv["checkpoint"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
