#!/usr/bin/env python3
"""The recorder health thread outlives any fault, like the recovery and Site Control threads.

The multi-recorder fan-out runs one health_worker per recorder (and the single-recorder loop runs
one for the site). health_cycle catches Exception, but its own except handlers log, and anything
that escapes it (a logger that raises because stdout is gone, SystemExit from a recorder config
check) ended the health thread for the life of the process: that recorder then never reported
camera or recorder health again and its inventory was never re-synced after a reconnect.
5.0.28 gave recovery_worker and command_worker a last-resort worker_fault handler; this checks
health_worker has the same survival.
"""
from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import programdata_sandbox  # noqa: E402,F401  (before any agent import: no writes to the real ProgramData)

import watchlog_agent as core  # noqa: E402
from drivers.base import DriverError  # noqa: E402

STATE = {"agent_id": "agent-1", "agent_key": "key-1"}


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


def _cfg():
    return SimpleNamespace(
        health_seconds=0.01, health_batch=4, health_concurrency=1,
        recorder_cloud_id="5e1f0000-0000-4000-8000-000000000002",
        nvr_driver="hikvision-isapi", nvr_url="http://192.0.2.10",
        nvr_username="local-user", nvr_password="local-password")


class _DeadCloud:
    def call(self, fn, **_params):
        raise requests.ConnectionError("network not ready")


def _run_health_worker(cloud=None):
    stop = threading.Event()
    resume = threading.Event()
    thread = threading.Thread(target=core.health_worker,
                              args=(_cfg(), STATE, cloud or _DeadCloud(), {}, stop, resume),
                              daemon=True)
    thread.start()
    return thread, stop


def _no_jitter():
    return SimpleNamespace(uniform=lambda _a, _b: 0.0)


class HealthWorkerSurvives(unittest.TestCase):
    def test_a_failing_logger_cannot_end_the_health_thread(self):
        """The recorder is unreachable and every log line raises inside health_cycle's handlers."""
        probes = []

        def unreachable(*_args, **_kwargs):
            probes.append(1)
            raise DriverError("http://192.0.2.10: recorder unreachable")

        def broken_log(_msg):
            raise OSError("stdout is gone")

        with _Patch(core, build=unreachable, log=broken_log, random=_no_jitter()):
            thread, stop = _run_health_worker()
            try:
                self.assertTrue(_wait_for(lambda: len(probes) >= 3),
                                f"health stopped after {len(probes)} cycle(s)")
                self.assertTrue(thread.is_alive(), "health thread died on a logging failure")
            finally:
                stop.set()
                thread.join(timeout=5)

    def test_a_fault_escaping_the_cycle_does_not_end_the_health_thread(self):
        """SystemExit (a recorder config check) is not an Exception and escapes health_cycle."""
        cycles = []

        def exits(*_args, **_kwargs):
            cycles.append(1)
            raise SystemExit("FATAL: no nvr_url")

        with _Patch(core, health_cycle=exits, log=lambda _m: None, random=_no_jitter()):
            thread, stop = _run_health_worker()
            try:
                self.assertTrue(_wait_for(lambda: len(cycles) >= 3),
                                f"health stopped after {len(cycles)} cycle(s)")
                self.assertTrue(thread.is_alive())
            finally:
                stop.set()
                thread.join(timeout=5)

    def test_the_last_resort_log_redacts_the_recorder_address(self):
        lines = []

        def exits(*_args, **_kwargs):
            raise RuntimeError("login to http://admin:pw@192.0.2.10/ISAPI failed")

        with _Patch(core, health_cycle=exits, log=lines.append, random=_no_jitter()):
            thread, stop = _run_health_worker()
            try:
                self.assertTrue(_wait_for(lambda: len(lines) >= 1))
            finally:
                stop.set()
                thread.join(timeout=5)
        self.assertTrue(lines[0].startswith("health: RuntimeError"))
        self.assertNotIn("192.0.2.10", lines[0])
        self.assertNotIn("pw@", lines[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
