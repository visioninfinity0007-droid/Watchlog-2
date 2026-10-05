#!/usr/bin/env python3
"""Recovery opens the recorder archive only for work it claimed (MNVR-036 remainder).

recovery_worker called open_archive_driver(cfg) every recovery cycle (300 s) before
RecoveryRunner.run_once had claimed anything. On an ONVIF Dahua or Hikvision site every idle cycle
therefore sent the ONVIF open, the vendor-native probe (a credentialed login), two GetProfiles and
a channel list. Now the runner is given a driver factory and opens the archive only once
wl_agent_claim_recovery returned an interval that needs reading, and closes it after the claim.
An archive that cannot be opened is a failed read of the claimed interval: it backs off, and ends
after the same bounded number of claims as any other archive failure.
"""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "tests"))

import backfill  # noqa: E402
import recovery  # noqa: E402
import watchlog_agent as core  # noqa: E402
from drivers.base import NvrUnreachable  # noqa: E402
from test_recovery_rpc_contract import (  # noqa: E402
    CHANNELS, STATE, StrictCloud, _Archive, _Cycles, _Patch, _Recorder, _Spool)

T0 = datetime(2026, 6, 1, 17, 0, tzinfo=timezone.utc)
CAM1 = "11111111-1111-4111-8111-111111111111"


class _Ledger:
    def __init__(self, intervals=()):
        self.intervals = [dict(iv) for iv in intervals]
        self.completes = []

    def call(self, fn, **kw):
        if fn == "wl_agent_claim_recovery":
            due = [iv for iv in self.intervals if iv["status"] in ("pending", "in_progress")]
            for iv in due[:kw["p_limit"]]:
                iv["status"], iv["attempts"] = "in_progress", iv["attempts"] + 1
            return [dict(iv) for iv in due[:kw["p_limit"]]]
        if fn == "wl_complete_recovery":
            self.completes.append(kw)
            for iv in self.intervals:
                if iv["id"] == kw["p_id"]:
                    iv["status"], iv["checkpoint"] = kw["p_status"], kw["p_checkpoint"]
                    iv.setdefault("detail", {}).update(kw.get("p_detail") or {})
            return {"ok": True}
        raise AssertionError(fn)


def _interval(cameras=(CAM1,), attempts=0):
    return {"id": "iv-1", "started_at": T0.isoformat(),
            "ended_at": (T0 + timedelta(hours=1)).isoformat(), "cameras": list(cameras),
            "status": "pending", "checkpoint": {}, "attempts": attempts}


class _ClosingArchive(backfill.ReferenceArchiveDriver):
    def __init__(self):
        super().__init__([{"ts": (T0 + timedelta(minutes=10)).isoformat(), "type": "person",
                           "device_event_id": "E1"}], page_size=10)
        self.closed = False

    def close(self):
        self.closed = True


class LazyArchive(unittest.TestCase):
    def _runner(self, ledger, factory, **kw):
        return recovery.RecoveryRunner(ledger, "agent", "key", None, [].append,
                                       driver_factory=factory, camera_channels={CAM1: "1"},
                                       log=lambda *a: None, **kw)

    def test_nothing_claimed_opens_nothing(self):
        opened = []
        self._runner(_Ledger(), lambda: opened.append(1)).run_once()
        self.assertEqual(opened, [])

    def test_claimed_work_opens_the_archive_once_and_closes_it(self):
        archives = []

        def factory():
            archives.append(_ClosingArchive())
            return archives[-1]

        ledger = _Ledger([_interval()])
        out = self._runner(ledger, factory).run_once()
        self.assertEqual(out[0]["status"], "recovered")
        self.assertEqual(len(archives), 1)
        self.assertTrue(archives[0].closed, "the runner closes the archive it opened")

    def test_an_interval_settled_without_reading_opens_nothing(self):
        opened = []
        for iv in (_interval(cameras=["99999999-9999-4999-8999-999999999999"]),
                   _interval(attempts=recovery.DEFAULT_MAX_ATTEMPTS + 1)):
            ledger = _Ledger([iv])
            self._runner(ledger, lambda: opened.append(1)).run_once()
            self.assertNotIn(ledger.intervals[0]["status"], ("pending", "in_progress"))
        self.assertEqual(opened, [])

    def test_an_archive_that_cannot_be_opened_backs_off_then_ends(self):
        def unreachable():
            raise NvrUnreachable("recorder offline")

        ledger = _Ledger([_interval()])
        runner = self._runner(ledger, unreachable)
        runner.run_once()
        iv = ledger.intervals[0]
        self.assertEqual(iv["status"], "in_progress")
        self.assertEqual((iv["checkpoint"]["cursor"], iv["checkpoint"].get("errors")),
                         (T0.isoformat(), 1))
        for _ in range(recovery.DEFAULT_MAX_ERROR_ATTEMPTS - 1):
            runner.run_once()
        self.assertEqual(iv["status"], "unrecoverable")
        self.assertEqual(iv["detail"].get("reason"), "archive_error")

    def test_an_open_driver_is_still_used_as_given(self):
        archive = _ClosingArchive()
        ledger = _Ledger([_interval()])
        out = recovery.RecoveryRunner(ledger, "agent", "key", archive, [].append,
                                      camera_channels={CAM1: "1"},
                                      log=lambda *a: None).run_once()
        self.assertEqual(out[0]["status"], "recovered")
        self.assertFalse(archive.closed, "a driver the caller passed in is the caller's to close")


class RecoveryWorkerOpensOnlyForClaimedWork(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = SimpleNamespace(
            recovery_enabled=True, recovery_seconds=300, recovery_ai_enabled=False,
            last_live_path=Path(self.tmp.name) / "last_live.json",
            recovery_threshold_seconds=180, recovery_chunk_seconds=3600,
            recovery_throttle_seconds=0.0, recovery_live_backlog=500,
            recovery_ai_max_frames=40, recovery_snapshot_seconds=300,
            nvr_driver="hikvision-isapi", nvr_url="http://192.0.2.10",
            nvr_username="local-user", nvr_password="local-password")
        self.opens = 0
        self.archive = _Archive()

    def _open(self, _cfg):
        self.opens += 1
        return self.archive, None

    def _work(self, cloud, spool, cycles):
        with _Patch(core, open_archive_driver=self._open, open_driver=_Recorder().open,
                    log=lambda *_a: None):
            core.recovery_worker(self.cfg, STATE, cloud, _Cycles(cycles), spool, CHANNELS,
                                 {"recorder_live_at": time.monotonic()})

    def test_idle_cycles_never_open_the_recorder_archive(self):
        cloud = StrictCloud()
        self._work(cloud, _Spool(), cycles=3)
        self.assertIn("wl_agent_claim_recovery", cloud.calls)
        self.assertEqual(self.opens, 0)

    def test_a_cycle_with_claimed_work_opens_it_once(self):
        gap = (T0.isoformat(), (T0 + timedelta(hours=1)).isoformat())
        cloud = StrictCloud()
        self._work(cloud, _Spool(gap), cycles=2)
        self.assertEqual(len(cloud.opened), 1)
        self.assertEqual(cloud.completes[-1]["p_status"], "recovered")
        self.assertEqual(self.opens, 1, "opened for the claimed interval only, not every cycle")


if __name__ == "__main__":
    unittest.main(verbosity=2)
