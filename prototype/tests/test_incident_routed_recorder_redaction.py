#!/usr/bin/env python3
"""Incident footage and still failures never name the recorder a job was routed to.

5.0.28 redacts recorder URLs, LAN addresses and the configured recorder's host name from the
reason text the cloud stores for a failed clip or still. In the multi-recorder Agent a job runs
on the recorder it names (job_cfg from recorder_runtime.config_for_cloud_recorder), but the
workers built their host list once from the process config, i.e. the first recorder. A failure
on another recorder whose address is a host name (not a URL or an IP) was stored and shown with
that recorder's name in it.
"""
from __future__ import annotations

import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import incident_evidence  # noqa: E402
import recorder_runtime  # noqa: E402
import watchlog_agent as core  # noqa: E402
from drivers.base import DeviceInfo, DriverError  # noqa: E402

STATE = {"agent_id": "agent", "agent_key": "key"}
RECORDER_B = "5e1f0000-0000-4000-8000-00000000000b"
T0 = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
B_HOST = "lobby-nvr.site.lan"

PROCESS_CFG = SimpleNamespace(supabase_url="https://example.invalid", publishable_key="test",
                              nvr_url="http://192.0.2.10")          # recorder A (continuity)
JOB_CFG_B = SimpleNamespace(nvr_url=f"http://{B_HOST}:8080")       # the job's recorder


def _route(cfg, recorder_id):
    if recorder_id != RECORDER_B:
        raise ValueError("cloud recorder target is not mapped to exactly one local recorder")
    return JOB_CFG_B


class _Cloud:
    """Hands out one request of ``kind`` once, then stops the worker; records failures."""

    def __init__(self, stop, claim_fn, row):
        self.stop, self.claim_fn, self.row = stop, claim_fn, row
        self.claims = 0
        self.calls = []

    def __call__(self, *_args):            # stands in for the core.Cloud constructor
        return self

    def call(self, name, **kwargs):
        self.calls.append((name, kwargs))
        if name == self.claim_fn:
            self.claims += 1
            if self.claims > 1:
                self.stop.set()
                return []
            return [dict(self.row)]
        return {"ok": True}

    def reasons(self, fn):
        return [kw["p_reason"] for name, kw in self.calls if name == fn]


class _FailingRecorder:
    """Recorder B: every request fails with a message that names its host."""
    name = "hikvision-isapi"

    def get_clip(self, channel, start, end):
        raise DriverError(f"{B_HOST} did not answer in time")

    def get_snapshot(self, channel):
        raise DriverError(f"{B_HOST} did not answer in time")

    def close(self):
        pass


def _open(_cfg):
    return _FailingRecorder(), DeviceInfo(vendor="Hikvision", model="DS-TEST", driver="hikvision")


class RoutedRecorderRedaction(unittest.TestCase):
    def _run(self, worker, claim_fn, row):
        stop = threading.Event()
        cloud = _Cloud(stop, claim_fn, row)
        with mock.patch.object(core, "Cloud", cloud), \
                mock.patch.object(recorder_runtime, "config_for_cloud_recorder", _route), \
                mock.patch.object(core, "open_archive_driver", _open), \
                mock.patch.object(core, "open_driver", _open), \
                mock.patch.object(core, "log", lambda *_a, **_k: None):
            worker(PROCESS_CFG, STATE, stop)
        return cloud

    def test_a_failed_clip_does_not_name_the_routed_recorder(self):
        cloud = self._run(incident_evidence.footage_worker, "wl_agent_claim_clip_requests", {
            "request_id": "clip-1", "channel": "3", "recorder_id": RECORDER_B,
            "start_at": T0.isoformat(), "end_at": (T0 + timedelta(seconds=30)).isoformat()})
        reasons = cloud.reasons("wl_agent_fail_clip")
        self.assertEqual(len(reasons), 1)
        self.assertNotIn(B_HOST, reasons[0].lower())
        self.assertEqual(reasons[0], "Recorder could not export the requested footage window.")

    def test_a_failed_still_does_not_name_the_routed_recorder(self):
        cloud = self._run(incident_evidence.stills_worker, "wl_agent_claim_incident_stills", {
            "request_id": "still-1", "channel": "3", "recorder_id": RECORDER_B})
        reasons = cloud.reasons("wl_agent_fail_incident_still")
        self.assertEqual(len(reasons), 1)
        self.assertNotIn(B_HOST, reasons[0].lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
