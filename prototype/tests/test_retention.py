#!/usr/bin/env python3
"""0.4.4 P7 — bounded retention-depth discovery (no hardware).

Proves estimate_retention finds the deepest look-back point that still has retrievable footage
without a deep scan, reports it honestly (measured / at_least / empty / unsupported / unknown), is
bounded (early-stops once footage runs out), and never raises.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

import retention  # noqa: E402

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


class RetDriver:
    """Footage exists at a probe point `start` iff start >= (NOW - retention_days). Counts calls."""
    def __init__(self, retention_days, *, cap="supported", raise_after=None):
        self._cutoff = NOW - timedelta(days=retention_days)
        self._cap = cap
        self._raise_after = raise_after
        self.calls = 0

    def historical_capability(self):
        return {"segments": self._cap, "events": self._cap, "snapshots": "unsupported"}

    def enumerate_historical_events(self, channel, start, end, cursor=None, limit=500):
        self.calls += 1
        if self._raise_after is not None and self.calls > self._raise_after:
            raise RuntimeError("recorder reset")
        if self._cap != "supported":
            return {"status": self._cap, "events": [], "next_cursor": None}
        start = start if isinstance(start, datetime) else datetime.fromisoformat(str(start))
        has = start >= self._cutoff
        return {"status": "supported", "events": ([{"ts": start.isoformat()}] if has else []),
                "next_cursor": None}


class NoArchiveDriver:
    def historical_capability(self):
        return {"segments": "unsupported", "events": "unsupported"}


class Retention(unittest.TestCase):
    def test_measured_within_horizon(self):
        r = retention.estimate_retention(RetDriver(10), "1", now=NOW)
        self.assertEqual(r["status"], "measured")
        self.assertEqual(r["retention_days"], 10.0)
        self.assertEqual(datetime.fromisoformat(r["oldest_recording"]), NOW - timedelta(days=10))

    def test_at_least_when_beyond_deepest_probe(self):
        r = retention.estimate_retention(RetDriver(200), "1", now=NOW)
        self.assertEqual(r["status"], "at_least")
        self.assertEqual(r["retention_days"], float(max(retention.DEFAULT_PROBE_DAYS)))

    def test_empty_when_no_footage(self):
        r = retention.estimate_retention(RetDriver(0), "1", now=NOW)   # cutoff = NOW -> even d=1 is older
        self.assertEqual(r["status"], "empty")
        self.assertIsNone(r["retention_days"])

    def test_bounded_early_stop(self):
        drv = RetDriver(3)
        retention.estimate_retention(drv, "1", now=NOW)
        # probes 1,2,3 hit, 5 misses -> stop. Should be ~4 calls, never the whole probe set.
        self.assertLessEqual(drv.calls, 5)

    def test_unsupported(self):
        self.assertEqual(retention.estimate_retention(NoArchiveDriver(), "1", now=NOW)["status"],
                         "unsupported")

    def test_never_raises_on_error(self):
        r = retention.estimate_retention(RetDriver(30, raise_after=2), "1", now=NOW)
        self.assertEqual(r["status"], "unknown")     # degrades, does not propagate

    def test_an_archive_that_cannot_say_is_unknown_not_empty(self):
        # The recorder is reachable for the capability read, but every search answers 'unknown'
        # (a refused login, an ambiguous answer): that is not proof the archive holds nothing.
        class CannotSay(RetDriver):
            def enumerate_historical_events(self, channel, start, end, cursor=None, limit=500):
                self.calls += 1
                return {"status": "unknown", "events": [], "next_cursor": None}

        r = retention.estimate_retention(CannotSay(30), "1", now=NOW)
        self.assertEqual(r["status"], "unknown")
        self.assertIsNone(r["retention_days"])

    def test_a_probe_that_cannot_say_after_footage_is_unknown(self):
        class StopsAnswering(RetDriver):
            def enumerate_historical_events(self, channel, start, end, cursor=None, limit=500):
                if self.calls >= 2:
                    self.calls += 1
                    return {"status": "unknown", "events": [], "next_cursor": None}
                return super().enumerate_historical_events(channel, start, end, cursor, limit)

        self.assertEqual(retention.estimate_retention(StopsAnswering(30), "1", now=NOW)["status"],
                         "unknown")

    def test_footage_served_by_a_search_that_stopped_short_still_counts(self):
        # A search too large to page serves the rows it read, then says 'partial': footage exists.
        class Partial(RetDriver):
            def enumerate_historical_events(self, channel, start, end, cursor=None, limit=500):
                page = super().enumerate_historical_events(channel, start, end, cursor, limit)
                return {**page, "status": "partial"} if page["events"] else page

        r = retention.estimate_retention(Partial(10), "1", now=NOW)
        self.assertEqual((r["status"], r["retention_days"]), ("measured", 10.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
