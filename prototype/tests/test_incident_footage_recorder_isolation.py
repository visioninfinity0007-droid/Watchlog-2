#!/usr/bin/env python3
"""Incident footage for one recorder never waits behind another recorder (MNVR-034, INT part).

The packaged Agent ran one site-level footage worker that served every recorder's clip
requests one after another, so a slow export on recorder A (a Dahua clip may take its whole
~90 s budget) delayed a clip on recorder B by that long. Requests are still claimed by one
site poller, but each recorder's clips are fetched by that recorder's own worker.
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
CLIP = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def _row(request_id, recorder_id):
    return {"request_id": request_id, "channel": "1", "recorder_id": recorder_id,
            "start_at": "2026-10-05T08:00:00Z", "end_at": "2026-10-05T08:00:30Z"}


class Cloud:
    def __init__(self, stop, rows):
        self.stop = stop
        self.rows = list(rows)
        self.calls = []
        self.lock = threading.Lock()
        self.completed = {}

    def call(self, name, **kw):
        with self.lock:
            self.calls.append((name, kw))
        if name == "wl_agent_claim_clip_requests":
            if self.rows:
                return [self.rows.pop(0)]
            return []
        if name == "wl_agent_complete_clip":
            self.completed[kw["p_request_id"]] = time.monotonic()
        return {"ok": True}


class Recorder(NvrDriver):
    name = "dahua-cgi"

    def __init__(self, label, gate=None):
        super().__init__(f"http://{label}.invalid", "admin", "secret")
        self.label = label
        self.gate = gate

    def get_clip(self, channel, start, end):
        if self.gate is not None:
            self.gate.wait(10)              # a slow export on this recorder
        return CLIP


def test_a_slow_clip_on_one_recorder_does_not_delay_another_recorders_clip(monkeypatch):
    stop = threading.Event()
    cloud = Cloud(stop, [_row("req-a", A), _row("req-b", B)])
    release_a = threading.Event()
    cfgs = {A: SimpleNamespace(nvr_url="http://a.invalid", rid=A),
            B: SimpleNamespace(nvr_url="http://b.invalid", rid=B)}
    monkeypatch.setattr(ie.core, "Cloud", lambda url, key: cloud)
    monkeypatch.setattr(ie.core, "log", lambda _m: None)
    monkeypatch.setattr(ie.recorder_runtime, "config_for_cloud_recorder",
                        lambda _cfg, rid: cfgs[rid])
    monkeypatch.setattr(ie.core, "open_archive_driver", lambda cfg: (
        Recorder(cfg.rid[:1], gate=release_a if cfg.rid == A else None),
        DeviceInfo(vendor="Dahua", model="X")))
    monkeypatch.setattr(ie, "POLL_SECONDS", 0.05)
    base = SimpleNamespace(supabase_url="https://cloud.invalid", publishable_key="pk",
                           nvr_url="http://a.invalid")

    worker = threading.Thread(target=ie.footage_worker, args=(base, STATE, stop))
    t0 = time.monotonic()
    worker.start()
    try:
        deadline = time.monotonic() + 3
        while "req-b" not in cloud.completed and time.monotonic() < deadline:
            time.sleep(0.02)
        assert "req-b" in cloud.completed, "B's clip waited behind A's slow export"
        assert "req-a" not in cloud.completed
        assert cloud.completed["req-b"] - t0 < 2
    finally:
        release_a.set()
        stop.set()
        worker.join(10)
    assert not worker.is_alive()
    assert set(cloud.completed) == {"req-a", "req-b"}, "A's clip still finished"


def test_claiming_pauses_while_the_in_flight_limit_is_reached(monkeypatch):
    stop = threading.Event()
    rows = [_row(f"req-{n}", A) for n in range(ie.FOOTAGE_MAX_IN_FLIGHT + 3)]
    cloud = Cloud(stop, rows)
    release = threading.Event()
    monkeypatch.setattr(ie.core, "Cloud", lambda url, key: cloud)
    monkeypatch.setattr(ie.core, "log", lambda _m: None)
    monkeypatch.setattr(ie.recorder_runtime, "config_for_cloud_recorder",
                        lambda _cfg, rid: SimpleNamespace(nvr_url="http://a.invalid"))
    monkeypatch.setattr(ie.core, "open_archive_driver", lambda cfg: (
        Recorder("a", gate=release), DeviceInfo(vendor="Dahua", model="X")))
    monkeypatch.setattr(ie, "_site_recorder_ids", lambda: {A})
    monkeypatch.setattr(ie, "POLL_SECONDS", 0.05)
    base = SimpleNamespace(supabase_url="https://cloud.invalid", publishable_key="pk",
                           nvr_url="http://a.invalid")
    worker = threading.Thread(target=ie.footage_worker, args=(base, STATE, stop))
    worker.start()
    try:
        time.sleep(0.5)
        claims = [n for n, _ in cloud.calls if n == "wl_agent_claim_clip_requests"]
        assert len(claims) == ie.FOOTAGE_MAX_IN_FLIGHT, (
            "claimed requests wait on this PC only up to the in-flight limit")
    finally:
        release.set()
        stop.set()
        worker.join(10)
    assert not worker.is_alive()


def test_a_worker_survives_a_request_that_exits(monkeypatch):
    stop = threading.Event()
    cloud = Cloud(stop, [_row("req-1", A), _row("req-2", A)])
    calls = []

    def open_archive(cfg):
        calls.append(1)
        if len(calls) == 1:
            raise SystemExit("recorder address not configured")
        return Recorder("a"), DeviceInfo(vendor="Dahua", model="X")

    monkeypatch.setattr(ie.core, "Cloud", lambda url, key: cloud)
    monkeypatch.setattr(ie.core, "log", lambda _m: None)
    monkeypatch.setattr(ie.recorder_runtime, "config_for_cloud_recorder",
                        lambda _cfg, rid: SimpleNamespace(nvr_url="http://a.invalid"))
    monkeypatch.setattr(ie.core, "open_archive_driver", open_archive)
    monkeypatch.setattr(ie, "POLL_SECONDS", 0.05)
    base = SimpleNamespace(supabase_url="https://cloud.invalid", publishable_key="pk",
                           nvr_url="http://a.invalid")
    worker = threading.Thread(target=ie.footage_worker, args=(base, STATE, stop))
    worker.start()
    try:
        deadline = time.monotonic() + 3
        while "req-2" not in cloud.completed and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        stop.set()
        worker.join(10)
    assert "req-2" in cloud.completed, "the recorder's worker died with the first request"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
