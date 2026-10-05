#!/usr/bin/env python3
"""One failing camera does not pause its sibling cameras on the same recorder (MNVR-037, RV-1).

The per-recorder sampler backoff was started by a failed sample on any single channel: one
camera whose snapshot timed out closed the recorder's transport and stopped sampling every
camera on that recorder for 15 s or more, on every pass of the rotation. A failed sample now
backs off only that (recorder, channel). The whole recorder backs off when its transport
cannot be opened, or when failures repeat across channels (or run on) with no success.
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

import analytics_agent as aa  # noqa: E402
from drivers.base import NvrAuthFailed, NvrUnreachable  # noqa: E402

A = "aaaaaaaa-0000-4000-8000-000000000001"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Transport:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def wait_until(predicate, seconds=3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _open_pool(clock):
    opened = []

    def opener(_recorder_id):
        transport = Transport()
        opened.append(transport)
        return transport

    pool = aa._SamplerDrivers(opener, generation=lambda _rid: "g", clock=clock)
    pool.get(A, A)
    assert wait_until(lambda: pool.get(A, A) is not None)
    return pool, opened


def test_one_failing_channel_backs_off_alone():
    clock = Clock()
    pool, opened = _open_pool(clock)
    transport = pool.get(A, A)
    delay, recorder_wide = pool.sample_failed(A, "2", NvrUnreachable("snapshot timed out"), A)
    assert not recorder_wide
    assert delay == aa.SAMPLER_RETRY_SECONDS[0]
    assert not transport.closed, "a sibling camera's transport stays open"
    assert pool.get(A, A) is transport, "the recorder is not backing off"
    assert pool.channel_waiting(A, "2")
    assert not pool.channel_waiting(A, "1")
    clock.now += aa.SAMPLER_RETRY_SECONDS[0] + 1
    assert not pool.channel_waiting(A, "2")
    assert len(opened) == 1
    pool.close_all()


def test_a_refused_channel_does_not_open_the_recorder_auth_breaker():
    clock = Clock()
    pool, _opened = _open_pool(clock)
    pool.succeeded(A, "1")
    _delay, recorder_wide = pool.sample_failed(A, "2", NvrAuthFailed("HTTP 401"), A)
    assert not recorder_wide
    assert pool.retry_at(A) is None
    assert pool.get(A, A) is not None
    pool.close_all()


def test_failures_across_channels_back_the_whole_recorder_off():
    clock = Clock()
    pool, _opened = _open_pool(clock)
    transport = pool.get(A, A)
    _d, wide = pool.sample_failed(A, "1", NvrUnreachable("no answer"), A)
    assert not wide
    _d, wide = pool.sample_failed(A, "2", NvrUnreachable("no answer"), A)
    assert wide, "two different channels failing with no success in between is the recorder"
    assert transport.closed
    assert pool.get(A, A) is None
    assert pool.retry_at(A) is not None
    pool.close_all()


def test_a_run_of_failures_on_a_single_camera_recorder_reaches_the_recorder():
    clock = Clock()
    pool, _opened = _open_pool(clock)
    transport = pool.get(A, A)
    results = []
    for _ in range(aa.SAMPLER_RECORDER_FAILURE_STREAK):
        _d, wide = pool.sample_failed(A, "1", NvrUnreachable("no answer"), A)
        results.append(wide)
    assert results[-1] is True and not any(results[:-1])
    assert transport.closed


def test_a_sibling_success_keeps_one_bad_channel_from_reaching_the_recorder():
    clock = Clock()
    pool, _opened = _open_pool(clock)
    for _ in range(aa.SAMPLER_RECORDER_FAILURE_STREAK * 3):
        _d, wide = pool.sample_failed(A, "2", NvrUnreachable("snapshot timed out"), A)
        assert not wide
        pool.succeeded(A, "1")
        pool.succeeded(A, "3")
    assert pool.retry_at(A) is None
    pool.close_all()


def test_healthy_cameras_keep_their_rate_while_a_sibling_fails(tmp_path, monkeypatch):
    """The real sampling loop: channel 2 always fails, channels 1 and 3 keep sampling."""
    stop = threading.Event()
    shots = {"1": 0, "2": 0, "3": 0}

    class Cloud:
        def call(self, name, **_kw):
            if name == "wl_agent_analytics_config":
                return {"changed": False, "multi_agent_enabled": False}
            return {}

    class Mux:
        camera_count = 3

        def __init__(self, **_kw):
            pass

        def configure(self, _config):
            pass

        def sample_plan(self):
            return [("t1", 0.05), ("t2", 0.05), ("t3", 0.05)]

        def resolve_target(self, target):
            return A, target[1:]

        def on_frame(self, *_a):
            return []

    class Driver:
        def get_snapshot(self, channel):
            shots[channel] += 1
            if channel == "2":
                raise NvrUnreachable("snapshot timed out")
            return b"jpeg"

        def close(self):
            pass

    monkeypatch.setattr(aa.core, "Cloud", lambda url, key: Cloud())
    monkeypatch.setattr(aa.core, "log", lambda _m: None)
    monkeypatch.setattr(aa.recorder_analytics, "RecorderAnalyticsMux", Mux)
    monkeypatch.setattr(aa.recorder_runtime, "config_for_cloud_recorder", lambda cfg, _r: cfg)
    monkeypatch.setattr(aa, "_open_analytics_driver", lambda _cfg: Driver())
    cfg = SimpleNamespace(
        analytics_enabled=True, supabase_url="https://cloud.invalid", publishable_key="pk",
        analytics_spool_path=tmp_path / "analytics.sqlite",
        analytics_config_path=tmp_path / "analytics_config.json",
        analytics_status_path=tmp_path / "analytics_status.json",
        analytics_bootstrap_marker=tmp_path / "bootstrap.json",
        bootstrap_site_type="", bootstrap_camera_profiles=[],
        analytics_poll_seconds=60, analytics_upload_seconds=1000, analytics_max_fps=30.0,
    )
    detector = SimpleNamespace(available=True, detect=lambda _raw: None)
    worker = threading.Thread(target=aa.analytics_worker, args=(cfg, {
        "agent_id": "agent", "agent_key": "key"}, detector, stop, {"ok": True}))
    worker.start()
    try:
        time.sleep(2.0)
    finally:
        stop.set()
        worker.join(10)
    assert not worker.is_alive()
    assert shots["2"] == 1, "the failing camera is backed off, not retried every pass"
    assert shots["1"] >= 5 and shots["3"] >= 5, (
        f"healthy sibling cameras were paused by one failing camera: {shots}")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
