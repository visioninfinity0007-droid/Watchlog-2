#!/usr/bin/env python3
"""A recorder-aware recovery claim follows the 5.0.28 recovery rules.

The multi-recorder Agent claims and completes recovery through the recorder RPCs
(wl_agent_claim_recorder_recovery / wl_complete_recorder_recovery), which return the interval's
archive channels. The 5.0.28 rules must hold on that path too:

  * a channel is never guessed: an interval whose cameras have no channel is unrecoverable;
  * a whole-site interval (no cameras) covers every camera the recorder knows. On a one-recorder
    site the recorder RPCs also hand a bound Agent the legacy intervals opened before it was
    bound, and such an interval names no cameras when the old Agent opened it site-wide;
  * a camera the claim gives no channel for stays unread, so the interval is never "recovered";
  * a failed archive read backs off, then ends the interval; a claim replayed too often without
    progress is closed. Every complete names the recorder.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import backfill  # noqa: E402
import recovery  # noqa: E402
from drivers.base import NvrUnreachable  # noqa: E402

RID = "5e1f0000-0000-4000-8000-000000000001"
CAM3 = "33333333-3333-4333-8333-333333333333"
CAM5 = "55555555-5555-4555-8555-555555555555"
T0 = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


class _RecorderLedger:
    """wl_agent_claim_recorder_recovery / wl_complete_recorder_recovery over one interval."""

    def __init__(self, *, cameras, channels, recorder_id=RID, attempts=0, checkpoint=None):
        self.iv = {"id": "iv-1", "recorder_id": recorder_id, "started_at": T0.isoformat(),
                   "ended_at": (T0 + timedelta(hours=1)).isoformat(), "cameras": list(cameras),
                   "channels": list(channels), "checkpoint": dict(checkpoint or {}),
                   "attempts": attempts, "status": "pending", "detail": {}}
        self.calls = []

    def call(self, fn, **kw):
        self.calls.append((fn, kw))
        if fn == "wl_agent_claim_recorder_recovery":
            assert kw["p_recorder_id"] == RID
            if self.iv["status"] not in ("pending", "in_progress"):
                return []
            self.iv["status"], self.iv["attempts"] = "in_progress", self.iv["attempts"] + 1
            return [{k: self.iv[k] for k in ("id", "recorder_id", "started_at", "ended_at",
                                             "cameras", "channels", "checkpoint", "attempts")}]
        if fn == "wl_complete_recorder_recovery":
            assert kw["p_recorder_id"] == RID
            self.iv["status"], self.iv["checkpoint"] = kw["p_status"], kw["p_checkpoint"]
            self.iv["detail"].update(kw.get("p_detail") or {})
            return {"ok": True}
        raise AssertionError(f"the recorder path must not call {fn}")

    def completes(self):
        return [kw for fn, kw in self.calls if fn == "wl_complete_recorder_recovery"]


class _Archive(backfill.ReferenceArchiveDriver):
    """Recorded events on every channel; remembers which channels were read."""

    def __init__(self, fail=False):
        super().__init__([{"ts": (T0 + timedelta(minutes=5)).isoformat(), "type": "person",
                           "device_event_id": "evt-1"}])
        self.read, self.fail = [], fail

    def enumerate_historical_events(self, channel, start, end, cursor=None, limit=500):
        self.read.append(str(channel))
        if self.fail:
            raise NvrUnreachable("recorder stopped answering")
        return super().enumerate_historical_events(channel, start, end, cursor, limit)


def _runner(ledger, driver, events, **kw):
    return recovery.RecoveryRunner(ledger, "agent", "key", driver, events.append,
                                   recorder_id=RID, chunk_seconds=3600, log=lambda *a: None, **kw)


class RecorderClaimChannels(unittest.TestCase):
    def test_a_whole_site_interval_reads_every_camera_the_recorder_knows(self):
        # A legacy interval the old Agent opened site-wide, handed to the now-bound Agent.
        ledger = _RecorderLedger(cameras=[], channels=[], recorder_id=None)
        driver, events = _Archive(), []
        out = _runner(ledger, driver, events,
                      camera_channels={CAM3: "3", CAM5: "5"}).run_once(limit=1)
        self.assertEqual(out[0]["status"], "recovered")
        self.assertEqual(sorted(set(driver.read)), ["3", "5"])
        self.assertTrue(events and all(e["recorder_id"] == RID for e in events))
        self.assertEqual(ledger.completes()[-1]["p_status"], "recovered")

    def test_a_whole_site_interval_without_known_cameras_is_never_guessed(self):
        ledger = _RecorderLedger(cameras=[], channels=[], recorder_id=None)
        driver, events = _Archive(), []
        out = _runner(ledger, driver, events).run_once(limit=1)
        self.assertEqual((out[0]["status"], out[0]["reason"]), ("unrecoverable", "missing_channels"))
        self.assertEqual(driver.read, [])
        self.assertEqual(ledger.completes()[-1]["p_detail"], {"reason": "missing_channels"})

    def test_a_camera_without_a_channel_keeps_the_interval_from_recovered(self):
        # Two cameras, but the claim names a channel for one of them only.
        ledger = _RecorderLedger(cameras=[CAM3, CAM5], channels=["3"])
        driver, events = _Archive(), []
        out = _runner(ledger, driver, events).run_once(limit=1)
        self.assertEqual(sorted(set(driver.read)), ["3"])
        self.assertEqual(out[0]["status"], "partial")
        self.assertEqual(ledger.completes()[-1]["p_status"], "partial")


class RecorderClaimFailures(unittest.TestCase):
    def test_a_failed_archive_read_backs_off_then_ends_the_interval(self):
        ledger = _RecorderLedger(cameras=[CAM3], channels=["3"])
        runner = _runner(ledger, _Archive(fail=True), [])
        first = runner.run_once(limit=1)
        self.assertEqual(first[0]["status"], "in_progress")
        self.assertEqual(ledger.iv["checkpoint"].get("errors"), 1)
        for _ in range(recovery.DEFAULT_MAX_ERROR_ATTEMPTS - 1):
            runner.run_once(limit=1)
        self.assertEqual(ledger.iv["status"], "unrecoverable")
        self.assertEqual(ledger.iv["detail"].get("reason"), "archive_error")
        self.assertEqual(ledger.iv["attempts"], recovery.DEFAULT_MAX_ERROR_ATTEMPTS)

    def test_a_claim_replayed_too_often_without_progress_is_closed(self):
        ledger = _RecorderLedger(cameras=[CAM3], channels=["3"],
                                 attempts=recovery.DEFAULT_MAX_ATTEMPTS)
        driver = _Archive()
        out = _runner(ledger, driver, []).run_once(limit=1)
        self.assertEqual((out[0]["status"], out[0]["reason"]),
                         ("unrecoverable", "attempts_exhausted"))
        self.assertEqual(driver.read, [])
        self.assertEqual(ledger.iv["detail"].get("reason"), "attempts_exhausted")


if __name__ == "__main__":
    unittest.main(verbosity=2)
