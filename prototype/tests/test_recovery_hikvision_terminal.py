#!/usr/bin/env python3
"""A failing archive read never leaves a recovery interval in progress forever (MNVR-059).

hikvision_archive.search_recordings raises on an HTTP 4xx, a 401/403 or invalid XML, and
enumerate_historical_events does not catch it. RecoveryRunner had no handler either, so the
exception escaped run_once without any wl_complete_recovery call: the interval stayed
in_progress, wl_agent_claim_recovery reclaimed it every 15 minutes with attempts+1, and the same
rejected search was replayed against the recorder forever.

Now an archive failure checkpoints the interval from the first chunk that failed and backs off (the
server re-offers it once the claim goes stale); after a few consecutive failed claims, or once
the claim attempts run out, the interval is completed with a terminal status.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
