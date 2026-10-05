#!/usr/bin/env python3
"""Background workers outlive any exception, and Site Control never strands a claimed command.

recovery_worker and command_worker logged failures through nvr_health.redact, but nvr_health was
only imported locally inside the health functions. The first exception in either thread therefore
raised NameError inside its own except block and ended the thread for the life of the process:
recovery never ran again after a recorder outage, and Site Control stopped polling.

Site Control also built cfg.nvr_driver directly. 'auto' (the Config default, and what older
installers wrote) is not a registered driver, so build() raised KeyError after the command had
been claimed, and the command stayed claimed until it expired.
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


def _run(target, *args):
    stop = threading.Event()
    thread = threading.Thread(target=target, args=(*args[:3], stop, *args[3:]), daemon=True)
    thread.start()
    return thread, stop


def _finish(thread, stop):
    stop.set()
    thread.join(timeout=5)


class _Spool:
    def pending_recovery_gap(self):
        return None

    def clear_recovery_gap(self, *_gap):
        return True

    def count(self):
        return 0

    def add(self, _event):
        pass


def _recovery_cfg(tmp_name="last_live.json"):
    return SimpleNamespace(
        recovery_enabled=True, recovery_seconds=0.01, recovery_ai_enabled=False,
        last_live_path=Path(__file__).resolve().parent / ".does-not-exist" / tmp_name,
        recovery_threshold_seconds=180, recovery_chunk_seconds=3600,
        recovery_throttle_seconds=0.0, recovery_live_backlog=500,
        recovery_ai_max_frames=40, recovery_snapshot_seconds=300,
        nvr_driver="hikvision-isapi", nvr_url="http://192.0.2.10",
        nvr_username="local-user", nvr_password="local-password")


class _RecoveryCloud:
    """Hands out an interval on every claim: the recorder archive is opened only for claimed work,
    so each cycle must have some for the injected recorder fault to fire again."""

    def __init__(self):
        self.calls = []

    def call(self, fn, **params):
        self.calls.append(fn)
        if fn == "wl_sync_cameras":
            return {"1": "11111111-1111-4111-8111-111111111111"}
        if fn == "wl_agent_claim_recovery":
            return [{"id": "iv-1", "started_at": "2026-06-01T17:00:00+00:00",
                     "ended_at": "2026-06-01T18:00:00+00:00",
                     "cameras": ["11111111-1111-4111-8111-111111111111"],
                     "checkpoint": {}, "attempts": 1}]
        return {}


class NvrHealthIsModuleLevel(unittest.TestCase):
    def test_worker_log_helper_is_a_module_global(self):
        self.assertIn("nvr_health", vars(core),
                      "the worker except-handlers reference nvr_health as a module global")


class RecoveryWorkerSurvives(unittest.TestCase):
    def test_recorder_outage_does_not_end_the_recovery_thread(self):
        opens = []

        def outage(_cfg):
            opens.append(1)
            raise DriverError("http://192.0.2.10: recorder unreachable")

        with _Patch(core, open_archive_driver=outage):
            thread, stop = _run(core.recovery_worker, _recovery_cfg(), STATE, _RecoveryCloud(),
                                _Spool(), [{"channel": "1", "name": "Gate"}], {})
            try:
                self.assertTrue(_wait_for(lambda: len(opens) >= 3),
                                f"recovery stopped retrying after {len(opens)} attempt(s)")
                self.assertTrue(thread.is_alive(), "recovery thread died on a recorder outage")
            finally:
                _finish(thread, stop)

    def test_a_failing_logger_cannot_end_the_recovery_thread(self):
        opens = []

        def outage(_cfg):
            opens.append(1)
            raise DriverError("recorder unreachable")

        def broken_log(_msg):
            raise OSError("stdout is gone")

        with _Patch(core, open_archive_driver=outage, log=broken_log):
            thread, stop = _run(core.recovery_worker, _recovery_cfg(), STATE, _RecoveryCloud(),
                                _Spool(), [{"channel": "1", "name": "Gate"}], {})
            try:
                self.assertTrue(_wait_for(lambda: len(opens) >= 3),
                                f"recovery stopped after {len(opens)} attempt(s)")
                self.assertTrue(thread.is_alive())
            finally:
                _finish(thread, stop)

    def test_missing_recorder_config_does_not_end_the_recovery_thread(self):
        """open_driver -> cfg.require_nvr raises SystemExit, which is not an Exception."""
        opens = []

        def no_recorder(_cfg):
            opens.append(1)
            raise SystemExit("FATAL: no nvr_url")

        with _Patch(core, open_archive_driver=no_recorder):
            thread, stop = _run(core.recovery_worker, _recovery_cfg(), STATE, _RecoveryCloud(),
                                _Spool(), [{"channel": "1", "name": "Gate"}], {})
            try:
                self.assertTrue(_wait_for(lambda: len(opens) >= 3),
                                f"recovery stopped after {len(opens)} attempt(s)")
                self.assertTrue(thread.is_alive())
            finally:
                _finish(thread, stop)


def _site_control_cfg(driver="hikvision-isapi"):
    return SimpleNamespace(site_control_enabled=True, site_control_seconds=0.01,
                           nvr_driver=driver, nvr_url="http://192.0.2.10",
                           nvr_username="local-user", nvr_password="local-password")


class _ChannelDriver:
    def __init__(self):
        self.closed = False

    def list_channels(self):
        return [SimpleNamespace(channel="1", name="Gate", enabled=True),
                SimpleNamespace(channel="2", name="Yard", enabled=True)]

    def close(self):
        self.closed = True


class _CommandCloud:
    """Hands out queued commands once, then idles; records completions."""

    def __init__(self, commands=(), fail_claims=False):
        self.commands = list(commands)
        self.fail_claims = fail_claims
        self.claims = 0
        self.completions = []

    def call(self, fn, **params):
        if fn == "wl_agent_claim_command":
            self.claims += 1
            if self.fail_claims:
                raise requests.ConnectionError("network not ready")
            return {"command": self.commands.pop(0)} if self.commands else {"command": None}
        if fn == "wl_agent_complete_command":
            self.completions.append(params)
            return {"ok": True}
        raise AssertionError(fn)


class CommandWorkerSurvives(unittest.TestCase):
    def test_network_errors_do_not_end_the_site_control_thread(self):
        cloud = _CommandCloud(fail_claims=True)
        with _Patch(core, update_runtime_health=lambda **_k: None):
            thread, stop = _run(core.command_worker, _site_control_cfg(), STATE, cloud)
            try:
                self.assertTrue(_wait_for(lambda: cloud.claims >= 3),
                                f"Site Control stopped polling after {cloud.claims} claim(s)")
                self.assertTrue(thread.is_alive(), "Site Control thread died on a network error")
            finally:
                _finish(thread, stop)

    def test_auto_driver_runs_the_claimed_command(self):
        cloud = _CommandCloud([{"id": "cmd-1", "action": "get_channels", "params": {}},
                               {"id": "cmd-2", "action": "get_channels", "params": {}}])
        recorder = _ChannelDriver()
        detected = []

        def autodetect(url, username, password, **_kw):
            detected.append(url)
            return recorder, SimpleNamespace(vendor="Hikvision", model="DS-7608")

        def no_build(name, *_a, **_kw):
            raise KeyError(f"unknown driver '{name}'")

        with _Patch(core, autodetect=autodetect, build=no_build,
                    update_runtime_health=lambda **_k: None):
            thread, stop = _run(core.command_worker, _site_control_cfg("auto"), STATE, cloud)
            try:
                self.assertTrue(_wait_for(lambda: len(cloud.completions) >= 2),
                                f"claimed commands completed: {cloud.completions}")
            finally:
                _finish(thread, stop)
        self.assertEqual([c["p_command_id"] for c in cloud.completions], ["cmd-1", "cmd-2"])
        self.assertTrue(all(c["p_status"] == "succeeded" for c in cloud.completions))
        self.assertEqual([ch["channel"] for ch in cloud.completions[0]["p_result"]], ["1", "2"])
        self.assertTrue(detected and recorder.closed)

    def test_unreachable_recorder_fails_the_claimed_command_instead_of_stranding_it(self):
        cloud = _CommandCloud([{"id": "cmd-1", "action": "get_channels", "params": {}}])

        def autodetect(url, *_a, **_kw):
            raise DriverError(f"no driver recognised the device at {url}")

        with _Patch(core, autodetect=autodetect, update_runtime_health=lambda **_k: None):
            thread, stop = _run(core.command_worker, _site_control_cfg("auto"), STATE, cloud)
            try:
                self.assertTrue(_wait_for(lambda: len(cloud.completions) >= 1),
                                "the claimed command was left claimed")
                self.assertTrue(_wait_for(lambda: cloud.claims >= 3),
                                "Site Control stopped polling after the failed command")
            finally:
                _finish(thread, stop)
        done = cloud.completions[0]
        self.assertEqual((done["p_command_id"], done["p_status"]), ("cmd-1", "failed"))
        self.assertTrue(done["p_error"])
        self.assertNotIn("192.0.2.10", done["p_error"], "recorder LAN address leaked to the cloud")


if __name__ == "__main__":
    unittest.main(verbosity=2)
