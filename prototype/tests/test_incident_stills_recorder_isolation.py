#!/usr/bin/env python3
"""An unreachable recorder never delays another recorder's incident still (contract 8, 12).

stills_worker claimed up to two still tasks for the whole site and served them in one loop. A
still for an unreachable recorder B paid open_driver's whole connect timeout (configured URL,
autodetect and fallback ports) before recorder A's still, claimed in the same batch, was even
tried; B's task was re-claimed under 0058's attempt budget and paid the timeout every time.
Stills are now taken by each recorder's own worker, and a recorder that just failed to open
fails its next stills straight away for a short while.
"""
from __future__ import annotations

from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import incident_evidence as ie  # noqa: E402
from drivers.base import DeviceInfo, NvrDriver  # noqa: E402

A = "aaaaaaaa-0000-4000-8000-000000000001"
B = "bbbbbbbb-0000-4000-8000-000000000002"
STATE = {"agent_id": "agent-1", "agent_key": "key-1"}
JPEG = b"\xff\xd8\xff\xe0" + b"\x10" * 256


def _row(request_id, recorder_id):
    return {"request_id": request_id, "channel": "1", "recorder_id": recorder_id}


class Cloud:
    def __init__(self, rows):
        self.rows = list(rows)
        self.lock = threading.Lock()
        self.calls = []
        self.done = {}

    def call(self, name, **kw):
        with self.lock:
            self.calls.append((name, kw))
        if name == "wl_agent_claim_incident_stills":
            with self.lock:
                return [self.rows.pop(0)] if self.rows else []
        if name in ("wl_agent_upload_incident_still", "wl_agent_fail_incident_still"):
            self.done[kw["p_request_id"]] = (name, time.monotonic())
        return {"ok": True}


class Recorder(NvrDriver):
    name = "hikvision-isapi"

    def __init__(self):
        super().__init__("http://a.invalid", "admin", "secret")

    def get_snapshot(self, channel):
        return JPEG


def _start(monkeypatch, rows, b_open):
    stop = threading.Event()
    cloud = Cloud(rows)
    opens = []

    def open_driver(cfg):
        opens.append(cfg.rid)
        if cfg.rid == B:
            return b_open()
        return Recorder(), DeviceInfo(vendor="Hikvision", model="X")

    cfgs = {A: SimpleNamespace(nvr_url="http://a.invalid", rid=A),
            B: SimpleNamespace(nvr_url="http://b.invalid", rid=B)}
    monkeypatch.setattr(ie.core, "Cloud", lambda url, key: cloud)
    monkeypatch.setattr(ie.core, "log", lambda _m: None)
    monkeypatch.setattr(ie.recorder_runtime, "config_for_cloud_recorder",
                        lambda _cfg, rid: cfgs[rid])
    monkeypatch.setattr(ie.core, "open_driver", open_driver)
    monkeypatch.setattr(ie, "_site_recorder_ids", lambda: {A, B})
    monkeypatch.setattr(ie, "STILL_POLL_SECONDS", 0.05)
    monkeypatch.setattr(ie, "FOOTAGE_CAPACITY_WAIT_SECONDS", 0.05)
    base = SimpleNamespace(supabase_url="https://cloud.invalid", publishable_key="pk",
                           nvr_url="http://a.invalid")
    worker = threading.Thread(target=ie.stills_worker, args=(base, STATE, stop))
    worker.start()
    return stop, cloud, worker, opens


def test_an_unreachable_recorder_does_not_delay_another_recorders_still(monkeypatch):
    release_b = threading.Event()

    def b_open():
        release_b.wait(10)                     # B's connect timeouts
        raise ConnectionError("recorder B did not answer")

    stop, cloud, worker, _ = _start(monkeypatch, [_row("still-b", B), _row("still-a", A)], b_open)
    try:
        deadline = time.monotonic() + 3
        while "still-a" not in cloud.done and time.monotonic() < deadline:
            time.sleep(0.02)
        assert cloud.done.get("still-a", ("",))[0] == "wl_agent_upload_incident_still", (
            "A's still waited behind B's connect timeout")
        assert "still-b" not in cloud.done
    finally:
        release_b.set()
        stop.set()
        worker.join(15)
    assert not worker.is_alive()
    assert cloud.done["still-b"][0] == "wl_agent_fail_incident_still"


def test_a_recorder_that_just_failed_to_open_is_not_retried_for_each_still(monkeypatch):
    def b_open():
        raise ConnectionError("recorder B did not answer")

    rows = [_row("still-b1", B), _row("still-b2", B), _row("still-b3", B)]
    stop, cloud, worker, opens = _start(monkeypatch, rows, b_open)
    try:
        deadline = time.monotonic() + 3
        while len(cloud.done) < 3 and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        stop.set()
        worker.join(15)
    assert not worker.is_alive()
    assert {cloud.done[r][0] for r in ("still-b1", "still-b2", "still-b3")} == {
        "wl_agent_fail_incident_still"}
    assert opens == [B], "each still for a dead recorder paid the open timeout again"
    fails = [kw for name, kw in cloud.calls if name == "wl_agent_fail_incident_still"]
    assert all(kw["p_unsupported"] is False and kw["p_reason"] == ie.STILL_FAILED
               for kw in fails), "a skipped still stays retryable with the customer reason"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
