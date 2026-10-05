#!/usr/bin/env python3
"""Each fanned-out recorder's recovery thread outlives any fault, like the 5.0.28 singleton one.

The multi-recorder fan-out starts one recovery_worker per bound recorder, fed that recorder's
synced inventory. Before the 5.0.28 merge the bound path read the inventory outside any handler
and caught only Exception through a log call, so SystemExit (open_driver's recorder config check)
or a logger that raises (stdout gone) ended that recorder's recovery for the life of the process:
its outages were never reconciled again, while the other recorders' threads kept going. 5.0.28
gave the singleton worker a last-resort worker_fault handler; the recorder variant runs through
the same loop and must survive the same faults, each recorder on its own.

This builds the fan-out's real worker sets (multi_recorder_fanout.build_worker_sets) and runs only
their recovery threads, with the archive transport and the cloud faked.
"""
from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import programdata_sandbox  # noqa: E402,F401  (before any agent import: no writes to the real ProgramData)

import multi_recorder_fanout as fanout  # noqa: E402
import watchlog_agent as core  # noqa: E402
from drivers.base import DriverError  # noqa: E402

STATE = {"agent_id": "agent-1", "agent_key": "key-1"}
REC_A = "a0000000-0000-4000-8000-00000000000a"
REC_B = "b0000000-0000-4000-8000-00000000000b"


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class _Patch:
    """Swap module attributes for one test and always restore them."""

    def __init__(self, module, **attrs):
        self.module, self.attrs, self.saved = module, attrs, {}

    def __enter__(self):
        for name, value in self.attrs.items():
            self.saved[name] = getattr(self.module, name)
            setattr(self.module, name, value)
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(self.module, name, value)


def _prepared(root: Path, name: str, rid: str, host: str):
    state = root / name
    cfg = SimpleNamespace(
        recorder_cloud_id=rid, recorder_display_name=name,
        spool_path=state / "spool.sqlite", health_store_path=state / "health.sqlite",
        last_live_path=state / "last_live.json", spool_max_rows=1000,
        health_batch=4, health_concurrency=1,
        recovery_enabled=True, recovery_seconds=0.01, recovery_ai_enabled=False,
        recovery_threshold_seconds=180, recovery_chunk_seconds=3600,
        recovery_throttle_seconds=0.0, recovery_live_backlog=500,
        recovery_ai_max_frames=40, recovery_snapshot_seconds=300,
        nvr_driver="hikvision-isapi", nvr_url=f"http://{host}",
        nvr_username="local-user", nvr_password="local-password")
    return SimpleNamespace(
        context=SimpleNamespace(config=cfg, holder={}, cloud_recorder_id=rid,
                                display_name=name, is_primary=name == "A"),
        device=None, channels=[{"channel": "1", "name": f"{name} gate"}],
        capabilities=None, camera_mapping={"1": f"{rid[:8]}-0000-4000-8000-000000000001"},
        error=None)


class _Cloud:
    def __init__(self):
        self.claims = []
        self._lock = threading.Lock()

    def call(self, fn, **params):
        if fn == "wl_agent_claim_recorder_recovery":
            with self._lock:
                self.claims.append(params.get("p_recorder_id"))
            return []
        return {}

    def claims_for(self, rid):
        with self._lock:
            return self.claims.count(rid)


class _Archive:
    name = "hikvision-isapi"

    def close(self):
        pass


class FanoutRecoveryThreadsSurvive(unittest.TestCase):
    def _run(self, open_archive_driver, log, check):
        cloud = _Cloud()
        stop = threading.Event()
        with TemporaryDirectory() as tmp, \
                _Patch(core, open_archive_driver=open_archive_driver, log=log):
            root = Path(tmp)
            units = fanout.build_worker_sets(
                [_prepared(root, "A", REC_A, "192.0.2.10"),
                 _prepared(root, "B", REC_B, "192.0.2.11")],
                STATE, cloud, stop)
            try:
                for unit in units:
                    unit.recovery.start()
                check(units, cloud)
            finally:
                stop.set()
                for unit in units:
                    if unit.recovery.is_alive():
                        unit.recovery.join(timeout=5)
                fanout._close(units)

    def test_a_recorder_config_exit_does_not_end_its_recovery_thread(self):
        """open_driver -> cfg.require_nvr raises SystemExit, which is not an Exception."""
        opens = {REC_A: 0, REC_B: 0}

        def no_recorder(cfg):
            opens[cfg.recorder_cloud_id] += 1
            raise SystemExit("FATAL: no nvr_url")

        def check(units, _cloud):
            self.assertTrue(_wait_for(lambda: min(opens.values()) >= 3),
                            f"recovery stopped retrying: {opens}")
            for unit in units:
                self.assertTrue(unit.recovery.is_alive(),
                                f"{unit.cfg.recorder_display_name}'s recovery thread died")

        self._run(no_recorder, lambda _m: None, check)

    def test_a_failing_logger_cannot_end_a_recorder_recovery_thread(self):
        opens = {REC_A: 0, REC_B: 0}

        def outage(cfg):
            opens[cfg.recorder_cloud_id] += 1
            raise DriverError(f"{cfg.nvr_url}: recorder unreachable")

        def broken_log(_msg):
            raise OSError("stdout is gone")

        def check(units, _cloud):
            self.assertTrue(_wait_for(lambda: min(opens.values()) >= 3),
                            f"recovery stopped retrying: {opens}")
            for unit in units:
                self.assertTrue(unit.recovery.is_alive())

        self._run(outage, broken_log, check)

    def test_one_recorders_fault_leaves_the_others_recovery_claiming(self):
        """A keeps failing; B keeps claiming its own intervals through the recorder RPC."""
        opens = {REC_A: 0, REC_B: 0}
        lines = []

        def a_broken(cfg):
            opens[cfg.recorder_cloud_id] += 1
            if cfg.recorder_cloud_id == REC_A:
                raise SystemExit(f"FATAL: {cfg.nvr_url} has no recorder login")
            return _Archive(), None

        def check(units, cloud):
            self.assertTrue(_wait_for(lambda: opens[REC_A] >= 3 and cloud.claims_for(REC_B) >= 3),
                            f"opens={opens} claims={cloud.claims}")
            self.assertTrue(all(unit.recovery.is_alive() for unit in units))
            self.assertEqual(cloud.claims_for(REC_A), 0, "A never reached its archive")

        self._run(a_broken, lines.append, check)
        # The last-resort line names the fault, never the recorder's address.
        faults = [line for line in lines if "SystemExit" in line]
        self.assertTrue(faults, lines[:5])
        self.assertFalse(any("192.0.2.10" in line for line in faults), faults[:3])


if __name__ == "__main__":
    unittest.main(verbosity=2)
