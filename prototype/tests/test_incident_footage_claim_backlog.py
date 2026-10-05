#!/usr/bin/env python3
"""A clip backlog on one recorder does not stop another recorder's clip being claimed (MNVR-034, RV-2).

Clip requests are claimed oldest first for the whole site (the claim cannot select by
recorder). Claiming paused at a site-wide in-flight limit, so once recorder A had that many
requests waiting (an incident across several of A's cameras), B's request was not even
claimed until A's worker finished a clip. While a known recorder of the site is idle,
claiming now continues past that limit, up to a hard ceiling.
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
from drivers.base import DeviceInfo  # noqa: E402
from test_incident_footage_recorder_isolation import (  # noqa: E402
    A, B, STATE, Cloud, Recorder, _row)


def _start(monkeypatch, rows, known, gate_a):
    stop = threading.Event()
    cloud = Cloud(stop, rows)
    cfgs = {A: SimpleNamespace(nvr_url="http://a.invalid", rid=A),
            B: SimpleNamespace(nvr_url="http://b.invalid", rid=B)}
    monkeypatch.setattr(ie.core, "Cloud", lambda url, key: cloud)
    monkeypatch.setattr(ie.core, "log", lambda _m: None)
    monkeypatch.setattr(ie.recorder_runtime, "config_for_cloud_recorder",
                        lambda _cfg, rid: cfgs[rid])
    monkeypatch.setattr(ie.core, "open_archive_driver", lambda cfg: (
        Recorder(cfg.rid[:1], gate=gate_a if cfg.rid == A else None),
        DeviceInfo(vendor="Dahua", model="X")))
    monkeypatch.setattr(ie, "_site_recorder_ids", lambda: set(known), raising=False)
    monkeypatch.setattr(ie, "POLL_SECONDS", 0.05)
    monkeypatch.setattr(ie, "FOOTAGE_CAPACITY_WAIT_SECONDS", 0.05)
    base = SimpleNamespace(supabase_url="https://cloud.invalid", publishable_key="pk",
                           nvr_url="http://a.invalid")
    worker = threading.Thread(target=ie.footage_worker, args=(base, STATE, stop))
    worker.start()
    return stop, cloud, worker


def test_b_is_claimed_and_served_behind_a_backlog_on_a(monkeypatch):
    release_a = threading.Event()
    rows = [_row(f"req-a{n}", A) for n in range(ie.FOOTAGE_MAX_IN_FLIGHT + 1)]
    rows.append(_row("req-b", B))
    stop, cloud, worker = _start(monkeypatch, rows, {A, B}, release_a)
    try:
        deadline = time.monotonic() + 3
        while "req-b" not in cloud.completed and time.monotonic() < deadline:
            time.sleep(0.02)
        assert "req-b" in cloud.completed, "B's clip waited behind A's backlog"
    finally:
        release_a.set()
        stop.set()
        worker.join(10)
    assert not worker.is_alive()
    assert len(cloud.completed) == len(rows), "A's backlog still finished"


def test_claiming_stops_at_the_hard_ceiling(monkeypatch):
    release_a = threading.Event()
    rows = [_row(f"req-a{n}", A) for n in range(ie.FOOTAGE_MAX_CLAIMED + 5)]
    stop, cloud, worker = _start(monkeypatch, rows, {A, B}, release_a)
    try:
        time.sleep(0.8)
        claims = [n for n, _ in cloud.calls if n == "wl_agent_claim_clip_requests"]
        assert len(claims) == ie.FOOTAGE_MAX_CLAIMED
    finally:
        release_a.set()
        stop.set()
        worker.join(10)
    assert not worker.is_alive()


def test_claiming_pauses_at_the_limit_when_every_recorder_is_busy(monkeypatch):
    release_a = threading.Event()
    rows = [_row(f"req-a{n}", A) for n in range(ie.FOOTAGE_MAX_IN_FLIGHT + 5)]
    stop, cloud, worker = _start(monkeypatch, rows, {A}, release_a)
    try:
        time.sleep(0.8)
        claims = [n for n, _ in cloud.calls if n == "wl_agent_claim_clip_requests"]
        assert len(claims) == ie.FOOTAGE_MAX_IN_FLIGHT
    finally:
        release_a.set()
        stop.set()
        worker.join(10)
    assert not worker.is_alive()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
