#!/usr/bin/env python3
"""An interval in flight when the site upgrades keeps an honest verdict.

5.0.28 carries the verdict so far ('examined' / 'incomplete') in the checkpoint, because time behind
the saved cursor is not read again. A checkpoint written by an earlier Agent has a cursor and a
seen-set but no verdict keys. Read as "nothing examined", an interval whose recovered samples lie
behind the cursor ended 'unrecoverable' when the rest of the gap held no recording, although
recovered events were already sent. Read as fully examined it could be called 'recovered' for time
whose verdict was lost (old seen-sets also held keys of samples that failed). Such a checkpoint now
ends 'partial' at most, and 5.0.28 always writes both keys so its own checkpoints are told apart.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import programdata_sandbox  # noqa: E402,F401  (keeps Agent state out of the real ProgramData)

import recovery  # noqa: E402

T0 = datetime(2026, 6, 1, 17, 0, tzinfo=timezone.utc)
JPEG = b"\xff\xd8\xff\xe0frame"


def _iso(dt):
    return dt.isoformat()


class _FootageOnly:
    """A recorder whose archive offers recorded footage (segments) but no event log."""

    def __init__(self, segments):
        self.segments = segments                  # [(start, end)] in UTC

    def historical_capability(self):
        return {"events": "unsupported", "segments": "supported", "snapshots": "unsupported"}

    def enumerate_historical_events(self, channel, start, end, cursor=None, limit=500):
        start, end = recovery._as_dt(start), recovery._as_dt(end)
        rows = [{"ts": _iso(s), "type": "recorded_segment", "device_event_id": f"S{i}",
                 "segment": {"start": _iso(s), "end": _iso(e)}}
                for i, (s, e) in enumerate(self.segments, 1) if start <= s < end]
        return {"status": "supported", "events": rows, "next_cursor": None}


class _Ledger:
    def __init__(self, checkpoint, hours=3, attempts=1):
        self.iv = {"id": "iv-1", "started_at": _iso(T0), "ended_at": _iso(T0 + timedelta(hours=hours)),
                   "cameras": ["1"], "checkpoint": checkpoint, "attempts": attempts,
                   "status": "pending"}
        self.completes = []

    def call(self, fn, **kw):
        if fn == "wl_agent_claim_recovery":
            if self.iv["status"] not in ("pending", "in_progress"):
                return []
            self.iv["status"], self.iv["attempts"] = "in_progress", self.iv["attempts"] + 1
            return [dict(self.iv)]
        if fn == "wl_complete_recovery":
            self.completes.append(kw)
            self.iv["status"], self.iv["checkpoint"] = kw["p_status"], kw["p_checkpoint"]
            return {"ok": True}
        raise AssertionError(fn)


def _runner(ledger, segments, **kw):
    return recovery.RecoveryRunner(ledger, "agent", "key", _FootageOnly(segments), [].append,
                                   frame_provider=lambda d, c, t: JPEG, log=lambda *a: None, **kw)


# One recording, in the first hour of a three-hour gap; the rest of the gap holds none.
HOUR_ONE = [(T0 + timedelta(minutes=10), T0 + timedelta(minutes=15))]
SAMPLE_KEY = f"ai:S1:{_iso(T0 + timedelta(minutes=10))}"


class LegacyCheckpoint(unittest.TestCase):
    def test_recovered_samples_behind_the_cursor_end_partial_not_unrecoverable(self):
        ledger = _Ledger({"cursor": _iso(T0 + timedelta(hours=1)), "seen_keys": [SAMPLE_KEY],
                          "progress_attempt": 1})
        out = _runner(ledger, HOUR_ONE).run_once()
        self.assertEqual(out[0]["status"], "partial")
        self.assertEqual(ledger.iv["status"], "partial")

    def test_a_legacy_checkpoint_is_never_called_recovered(self):
        # The rest of the gap is fully examined, but the verdict behind the cursor was not carried.
        rest = [(T0 + timedelta(hours=h, minutes=10), T0 + timedelta(hours=h, minutes=15))
                for h in (1, 2)]
        for seen in ([SAMPLE_KEY], []):
            with self.subTest(seen=seen):
                ledger = _Ledger({"cursor": _iso(T0 + timedelta(hours=1)), "seen_keys": seen,
                                  "progress_attempt": 1})
                self.assertEqual(_runner(ledger, HOUR_ONE + rest).run_once()[0]["status"],
                                 "partial")

    def test_a_checkpoint_carrying_the_verdict_is_read_as_written(self):
        ledger = _Ledger({"cursor": _iso(T0 + timedelta(hours=1)), "seen_keys": [SAMPLE_KEY],
                          "progress_attempt": 1, "examined": True, "incomplete": False})
        self.assertEqual(_runner(ledger, HOUR_ONE).run_once()[0]["status"], "recovered")


class CheckpointCarriesTheVerdict(unittest.TestCase):
    def test_a_resumed_claim_is_not_mistaken_for_a_legacy_one(self):
        # Hour one holds no recording, so nothing behind the cursor was examined or missed. The
        # claim yields to live work after that hour; the next claim examines hours two and three.
        later = [(T0 + timedelta(hours=h, minutes=10), T0 + timedelta(hours=h, minutes=15))
                 for h in (1, 2)]
        busy = iter([False, True])                     # live work arrives after the first chunk
        ledger = _Ledger({})
        first = _runner(ledger, later, live_pending=lambda: next(busy, False))
        first._recover_interval(ledger.call("wl_agent_claim_recovery", p_limit=1)[0])
        saved = ledger.iv["checkpoint"]
        self.assertEqual(ledger.iv["status"], "in_progress")
        self.assertEqual(saved["cursor"], _iso(T0 + timedelta(hours=1)))
        self.assertEqual((saved.get("examined"), saved.get("incomplete")), (False, False))
        self.assertEqual(_runner(ledger, later).run_once()[0]["status"], "recovered")


if __name__ == "__main__":
    unittest.main(verbosity=2)
