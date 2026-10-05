#!/usr/bin/env python3
"""A bound recorder hands an unopenable claim back through the recorder recovery RPC.

5.0.28 opens the recorder archive only for a claimed interval and, when the archive cannot be
opened (recorder offline, login refused), hands the claim back as pending. Its hand-back called
wl_complete_recovery unconditionally. A recorder-scoped RecoveryRunner (multi-recorder fan-out,
or the one bound recorder) claims through wl_agent_claim_recorder_recovery, so its hand-back
must go through wl_complete_recorder_recovery with the same p_recorder_id: the recorder-less
RPC cannot complete a recorder-scoped claim, so the interval would stay in_progress under a
stale claim instead of being retried once the recorder answers.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "tests"))
import programdata_sandbox  # noqa: E402,F401  (keeps Agent state out of the real ProgramData)

import recovery  # noqa: E402
from drivers.base import NvrUnreachable  # noqa: E402

T0 = datetime(2026, 6, 1, 17, 0, tzinfo=timezone.utc)
REC_A = "a0000000-0000-4000-8000-00000000000a"
CAM1 = "11111111-1111-4111-8111-111111111111"


class _RecorderLedger:
    """Only the recorder-scoped recovery RPCs exist for a recorder-scoped runner."""

    def __init__(self):
        self.calls = []
        self.interval = {"id": "iv-1", "started_at": T0.isoformat(),
                         "ended_at": (T0 + timedelta(hours=1)).isoformat(),
                         "cameras": [CAM1], "channels": ["1"], "status": "pending",
                         "checkpoint": {"errors": 1, "progress_attempt": 2}, "attempts": 3}

    def call(self, fn, **kw):
        self.calls.append((fn, kw))
        if fn == "wl_agent_claim_recorder_recovery":
            assert kw.get("p_recorder_id") == REC_A, kw
            return [dict(self.interval)]
        if fn == "wl_complete_recorder_recovery":
            assert kw.get("p_recorder_id") == REC_A, kw
            return {"ok": True}
        raise AssertionError(f"recorder-scoped claim reached {fn}")


class RecorderClaimHandBack(unittest.TestCase):
    def test_an_unopenable_archive_hands_the_claim_back_on_the_recorder_rpc(self):
        ledger = _RecorderLedger()
        opens = []

        def offline():
            opens.append(1)
            raise NvrUnreachable("recorder did not answer")

        runner = recovery.RecoveryRunner(
            ledger, "agent", "key", None, [].append, recorder_id=REC_A,
            camera_channels={CAM1: "1"}, driver_factory=offline, log=lambda _m: None)
        out = runner.run_once(limit=1)

        self.assertEqual(len(opens), 1)
        self.assertEqual(out[0]["status"], "pending")
        completes = [kw for fn, kw in ledger.calls if fn == "wl_complete_recorder_recovery"]
        self.assertEqual(len(completes), 1, ledger.calls)
        sent = completes[0]
        self.assertEqual(sent["p_status"], "pending")
        self.assertEqual(sent["p_recovered_count"], 0)
        # The checkpoint is kept as claimed; only the no-progress count starts a claim later.
        self.assertEqual(sent["p_checkpoint"]["errors"], 1)
        self.assertEqual(sent["p_checkpoint"]["progress_attempt"], 3)
        self.assertNotIn("wl_complete_recovery", [fn for fn, _kw in ledger.calls])

    def test_the_recorder_less_runner_keeps_the_legacy_rpc(self):
        calls = []

        class Legacy:
            def call(self, fn, **kw):
                calls.append((fn, kw))
                if fn == "wl_agent_claim_recovery":
                    return [{"id": "iv-1", "started_at": T0.isoformat(),
                             "ended_at": (T0 + timedelta(hours=1)).isoformat(),
                             "cameras": [CAM1], "status": "pending", "checkpoint": {},
                             "attempts": 1}]
                if fn == "wl_complete_recovery":
                    return {"ok": True}
                raise AssertionError(fn)

        def offline():
            raise NvrUnreachable("recorder did not answer")

        runner = recovery.RecoveryRunner(
            Legacy(), "agent", "key", None, [].append, camera_channels={CAM1: "1"},
            driver_factory=offline, log=lambda _m: None)
        runner.run_once(limit=1)
        done = [kw for fn, kw in calls if fn == "wl_complete_recovery"]
        self.assertEqual(len(done), 1)
        self.assertNotIn("p_recorder_id", done[0])
        self.assertEqual(done[0]["p_status"], "pending")


if __name__ == "__main__":
    unittest.main(verbosity=2)
