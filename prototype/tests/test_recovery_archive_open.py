#!/usr/bin/env python3
"""Recovery opens the recorder archive only for work it claimed (MNVR-036 remainder).

recovery_worker called open_archive_driver(cfg) every recovery cycle (300 s) before
RecoveryRunner.run_once had claimed anything. On an ONVIF Dahua or Hikvision site every idle cycle
therefore sent the ONVIF open, the vendor-native probe (a credentialed login), two GetProfiles and
a channel list. Now the runner is given a driver factory and opens the archive only once
wl_agent_claim_recovery returned an interval that needs reading, and closes it after the claim.
An archive that cannot be opened (recorder offline, login refused) is not a failed read: nothing of
the interval was examined, so the claim is handed back as pending with its checkpoint, failed-read
count and no-progress count untouched, and the interval is read once the recorder answers again
(as before, when the worker skipped the cycle). It is never ended unrecoverable for it.
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
import programdata_sandbox  # noqa: E402,F401  (keeps Agent state out of the real ProgramData)

import backfill  # noqa: E402
import recovery  # noqa: E402
import watchlog_agent as core  # noqa: E402
from drivers.base import NvrAuthFailed, NvrUnreachable  # noqa: E402
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

    def test_an_archive_that_cannot_be_opened_leaves_the_interval_pending(self):
        # More claims than the failed-read budget and the no-progress cap together: neither is
        # spent on a recorder that could not be opened, because nothing of the gap was examined.
        claims = recovery.DEFAULT_MAX_ATTEMPTS + recovery.DEFAULT_MAX_ERROR_ATTEMPTS + 2
        for fault in (NvrUnreachable("recorder offline"), NvrAuthFailed("login refused")):
            with self.subTest(fault=type(fault).__name__):
                def unopenable():
                    raise fault

                ledger = _Ledger([_interval()])
                runner = self._runner(ledger, unopenable)
                for _ in range(claims):
                    self.assertEqual(runner.run_once()[0]["status"], "pending")
                iv = ledger.intervals[0]
                self.assertEqual(iv["status"], "pending")
                self.assertIsNone(iv["checkpoint"].get("cursor"))
                self.assertEqual(iv["checkpoint"].get("seen_keys") or [], [])
                self.assertNotIn("errors", iv["checkpoint"])
                self.assertNotIn("reason", iv.get("detail") or {})
                self.assertEqual({c["p_status"] for c in ledger.completes}, {"pending"})
                # The recorder answers again: the interval is read and recovered.
                runner.driver_factory = _ClosingArchive
                self.assertEqual(runner.run_once()[0]["status"], "recovered")
                self.assertEqual(iv["status"], "recovered")

    def test_an_unopenable_archive_keeps_the_progress_already_made(self):
        cursor = (T0 + timedelta(minutes=30)).isoformat()
        checkpoint = {"cursor": cursor, "seen_keys": ["ev:E0"], "errors": 1,
                      "progress_attempt": 2, "examined": True, "incomplete": False}

        def unreachable():
            raise NvrUnreachable("recorder offline")

        ledger = _Ledger([dict(_interval(attempts=2), checkpoint=dict(checkpoint))])
        self._runner(ledger, unreachable).run_once()
        iv = ledger.intervals[0]
        self.assertEqual(iv["status"], "pending")
        kept = {k: v for k, v in iv["checkpoint"].items() if k != "progress_attempt"}
        self.assertEqual(kept, {k: v for k, v in checkpoint.items() if k != "progress_attempt"})
        # The claim that could not open the archive is not counted toward the no-progress cap.
        self.assertEqual(iv["attempts"] - iv["checkpoint"]["progress_attempt"],
                         2 - checkpoint["progress_attempt"])

    def test_an_open_driver_is_still_used_as_given(self):
        archive = _ClosingArchive()
        ledger = _Ledger([_interval()])
        out = recovery.RecoveryRunner(ledger, "agent", "key", archive, [].append,
                                      camera_channels={CAM1: "1"},
                                      log=lambda *a: None).run_once()
        self.assertEqual(out[0]["status"], "recovered")
        self.assertFalse(archive.closed, "a driver the caller passed in is the caller's to close")


class _StaleReoffer(StrictCloud):
    """The 0098 claim also re-offers an in_progress row once its claim went stale (900 s): every
    cycle here is treated as past that, as when the recorder stays offline for hours."""

    def call(self, fn, **params):
        if fn == "wl_agent_claim_recovery":
            self._check(fn, params)
            self.calls.append(fn)
            due = [iv for iv in self.intervals
                   if iv["status"] in ("pending", "in_progress")][:params["p_limit"]]
            for iv in due:
                iv["status"], iv["attempts"] = "in_progress", iv["attempts"] + 1
            return [dict(iv) for iv in due]
        return super().call(fn, **params)


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

    def test_an_offline_recorder_never_ends_a_claimed_interval(self):
        # The recorder is offline (no live stream, archive open refused) for many cycles while the
        # ledger re-offers stale claims: the interval stays pending, then recovers once it answers.
        def offline(_cfg):
            self.opens += 1
            raise NvrUnreachable("recorder offline")

        cloud = _StaleReoffer()
        cloud.intervals.append({"id": "iv-1", "started_at": T0.isoformat(),
                                "ended_at": (T0 + timedelta(hours=1)).isoformat(),
                                "cameras": [CAM1], "checkpoint": {}, "attempts": 0,
                                "status": "pending"})
        cycles = recovery.DEFAULT_MAX_ERROR_ATTEMPTS + 3
        with _Patch(core, open_archive_driver=offline, open_driver=_Recorder().open,
                    log=lambda *_a: None):
            core.recovery_worker(self.cfg, STATE, cloud, _Cycles(cycles), _Spool(), CHANNELS,
                                 {"recorder_live_at": 0.0})
        iv = cloud.intervals[0]
        self.assertEqual(self.opens, cycles)
        self.assertEqual(iv["status"], "pending")
        self.assertNotIn("errors", iv["checkpoint"])
        self.assertFalse({"recovered", "partial", "unrecoverable"}
                         & {c["p_status"] for c in cloud.completes})
        self._work(cloud, _Spool(), cycles=1)
        self.assertEqual(iv["status"], "recovered")

    def test_a_cycle_with_claimed_work_opens_it_once(self):
        gap = (T0.isoformat(), (T0 + timedelta(hours=1)).isoformat())
        cloud = StrictCloud()
        self._work(cloud, _Spool(gap), cycles=2)
        self.assertEqual(len(cloud.opened), 1)
        self.assertEqual(cloud.completes[-1]["p_status"], "recovered")
        self.assertEqual(self.opens, 1, "opened for the claimed interval only, not every cycle")


if __name__ == "__main__":
    unittest.main(verbosity=2)
