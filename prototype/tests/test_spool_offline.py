#!/usr/bin/env python3
"""Offline buffering — the spool + drain contract (item 13).

The product claim is: a site survives its internet link. Events accumulate on the site
PC's disk during an outage and go up when the link returns, with at-least-once delivery
(never acknowledged until the server has committed) and a bounded queue that a month-long
outage cannot use to fill the disk. These tests prove those properties rather than
asserting them.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import requests  # noqa: E402
import spool as spool_mod  # noqa: E402
from spool import Spool  # noqa: E402


class SpoolDurability(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "spool.sqlite"

    def tearDown(self):
        self.dir.cleanup()

    def test_wal_mode_is_on(self):
        s = Spool(self.path)
        self.assertEqual(s.db.execute("pragma journal_mode").fetchone()[0].lower(), "wal")
        s.close()

    def test_fifo_peek_does_not_remove(self):
        s = Spool(self.path)
        for i in range(5):
            s.add({"n": i})
        ids, evs = s.take(10)
        self.assertEqual([e["n"] for e in evs], [0, 1, 2, 3, 4])   # oldest first
        self.assertEqual(s.count(), 5)                             # take() peeks, never deletes
        s.close()

    def test_ack_removes_only_confirmed(self):
        s = Spool(self.path)
        for i in range(5):
            s.add({"n": i})
        ids, _ = s.take(3)
        s.ack(ids)
        self.assertEqual(s.count(), 2)
        _, evs = s.take(10)
        self.assertEqual([e["n"] for e in evs], [3, 4])            # remaining, still in order
        s.close()

    def test_survives_reopen(self):
        # A process restart (or power cut) must not lose queued events.
        s = Spool(self.path)
        for i in range(20):
            s.add({"n": i})
        s.close()
        s2 = Spool(self.path)
        self.assertEqual(s2.count(), 20)
        _, evs = s2.take(1)
        self.assertEqual(evs[0]["n"], 0)
        s2.close()

    def test_trim_bounds_the_queue_oldest_first(self):
        s = Spool(self.path)
        original = spool_mod.MAX_ROWS
        try:
            spool_mod.MAX_ROWS = 10
            for i in range(15):
                s.add({"n": i})
            dropped = s.trim()
            self.assertEqual(dropped, 5)
            self.assertEqual(s.count(), 10)
            _, evs = s.take(100)
            self.assertEqual([e["n"] for e in evs], list(range(5, 15)))  # oldest 5 dropped
            self.assertEqual(s.trim(), 0)                                # idempotent at cap
        finally:
            spool_mod.MAX_ROWS = original
        s.close()


class _FakeCloud:
    """Stands in for the Supabase RPC client. `offline=True` raises like a dead link."""
    def __init__(self, offline=False):
        self.offline = offline
        self.received_batches = []

    def call(self, name, **kwargs):
        if self.offline:
            raise requests.exceptions.ConnectionError("simulated offline link")
        evs = kwargs.get("p_events") or []
        self.received_batches.append(len(evs))
        return {"received": len(evs), "inserted": len(evs), "skipped": 0, "snapshots": 0}


class DrainContract(unittest.TestCase):
    """Exercises the REAL upload_once() — the at-least-once + size-bound guarantees."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "spool.sqlite"
        import watchlog_agent as core
        self.core = core
        self.state = {"agent_id": "a", "agent_key": "k"}

    def tearDown(self):
        self.dir.cleanup()

    def test_offline_keeps_everything_unacked(self):
        s = Spool(self.path)
        for i in range(5):
            s.add({"channel": "1", "event_type": "person", "n": i})
        with self.assertRaises(requests.exceptions.ConnectionError):
            self.core.upload_once(_FakeCloud(offline=True), self.state, s)
        self.assertEqual(s.count(), 5)         # nothing acknowledged on a failed POST
        s.close()

    def test_online_drains_and_acks(self):
        s = Spool(self.path)
        for i in range(5):
            s.add({"channel": "1", "event_type": "person", "n": i})
        cloud = _FakeCloud(offline=False)
        self.core.upload_once(cloud, self.state, s)
        self.assertEqual(s.count(), 0)
        self.assertEqual(cloud.received_batches, [5])
        s.close()

    def test_recovery_after_outage(self):
        # Offline attempt fails, link returns, the SAME buffered events go up intact.
        s = Spool(self.path)
        for i in range(3):
            s.add({"channel": "1", "event_type": "person", "n": i})
        with self.assertRaises(requests.exceptions.ConnectionError):
            self.core.upload_once(_FakeCloud(offline=True), self.state, s)
        cloud = _FakeCloud(offline=False)
        self.core.upload_once(cloud, self.state, s)
        self.assertEqual(s.count(), 0)
        self.assertEqual(cloud.received_batches, [3])
        s.close()

    def test_oversized_batch_is_cut_but_keeps_at_least_one(self):
        # Big stills must not produce a multi-megabyte POST; the batch is size-bounded,
        # yet a single oversized row can never wedge the queue forever.
        s = Spool(self.path)
        big = "x" * self.core.UPLOAD_MAX_BYTES
        for i in range(3):
            s.add({"channel": "1", "event_type": "person", "snapshot_b64": big, "n": i})
        cloud = _FakeCloud(offline=False)
        self.core.upload_once(cloud, self.state, s)
        self.assertEqual(cloud.received_batches[0], 1)   # cut to one oversized row
        self.assertEqual(s.count(), 2)                   # the rest remain, in order
        s.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
