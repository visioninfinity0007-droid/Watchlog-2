#!/usr/bin/env python3
"""The analytics sampler backs off per recorder and never blocks the lease thread (MNVR-037).

The sampler re-opened a recorder (a full probe) on every failed sample, with no backoff
and no auth breaker, so a recorder with a wrong password took a failed login several times
a second. It also opened recorders on the thread that refreshes the site's single-authority
lease, so one slow recorder could let the 90 s lease lapse and pause uploads for all
recorders. Recorder transports are now opened in the background, one recorder at a time,
with a per-recorder backoff and an auth breaker that wakes on a repaired credential.
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
from drivers.base import DriverError, NvrAuthFailed, NvrUnreachable  # noqa: E402

A = "aaaaaaaa-0000-4000-8000-000000000001"
B = "bbbbbbbb-0000-4000-8000-000000000002"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Transport:
    def __init__(self, label):
        self.label = label
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


def test_a_slow_open_never_blocks_the_caller():
    release = threading.Event()
    opened = []

    def opener(recorder_id):
        opened.append(recorder_id)
        release.wait(5)
        return Transport(recorder_id)

    pool = aa._SamplerDrivers(opener, generation=lambda _rid: "g")
    started = time.monotonic()
    assert pool.get(A, A) is None
    assert pool.get(A, A) is None, "a second sample does not start a second open"
    assert time.monotonic() - started < 0.2
    release.set()
    assert wait_until(lambda: pool.get(A, A) is not None)
    assert opened == [A]
    pool.close_all()


def test_one_slow_recorder_does_not_hold_another():
    release = threading.Event()

    def opener(recorder_id):
        if recorder_id == A:
            release.wait(5)
        return Transport(recorder_id)

    pool = aa._SamplerDrivers(opener, generation=lambda _rid: "g")
    assert pool.get(A, A) is None
    pool.get(B, B)
    assert wait_until(lambda: pool.get(B, B) is not None)
    assert pool.get(A, A) is None
    release.set()
    pool.close_all()


def test_transient_failures_back_off_per_recorder_and_reset_on_success():
    clock = Clock()
    attempts = []
    fail = {"on": True}

    def opener(recorder_id):
        attempts.append(recorder_id)
        if fail["on"] and recorder_id == A:
            raise NvrUnreachable("recorder did not answer")
        return Transport(recorder_id)

    pool = aa._SamplerDrivers(opener, generation=lambda _rid: "g", clock=clock)
    pool.get(A, A)
    assert wait_until(lambda: not pool.opening(A))
    for _ in range(20):                       # many samples during the backoff
        assert pool.get(A, A) is None
    assert attempts == [A], "no re-open while the recorder is backing off"
    pool.get(B, B)                            # another recorder is unaffected
    assert wait_until(lambda: pool.get(B, B) is not None)

    clock.now += aa.SAMPLER_RETRY_SECONDS[0] + 1
    pool.get(A, A)
    assert wait_until(lambda: not pool.opening(A))
    assert attempts.count(A) == 2
    clock.now += aa.SAMPLER_RETRY_SECONDS[0] + 1
    assert pool.get(A, A) is None, "the second failure waits longer"
    assert attempts.count(A) == 2
    clock.now += aa.SAMPLER_RETRY_SECONDS[1]
    fail["on"] = False
    pool.get(A, A)
    assert wait_until(lambda: pool.get(A, A) is not None)
    assert pool.retry_at(A) is None
    pool.close_all()


def test_a_rejected_login_opens_the_auth_breaker_until_the_credential_changes():
    clock = Clock()
    attempts = []
    generation = {"A": "g1"}

    def opener(recorder_id):
        attempts.append(recorder_id)
        if generation["A"] == "g1":
            raise NvrAuthFailed("HTTP 401 — recorder rejected the username or password")
        return Transport(recorder_id)

    pool = aa._SamplerDrivers(opener, generation=lambda _rid: generation["A"], clock=clock)
    pool.get(A, A)
    assert wait_until(lambda: not pool.opening(A))
    clock.now += aa.SAMPLER_RETRY_SECONDS[0] * 4      # well past a transient backoff
    assert pool.get(A, A) is None
    assert attempts == [A], "a refused login is not retried for minutes"

    generation["A"] = "g2"                             # Setup repaired the login
    pool.get(A, A)
    assert wait_until(lambda: pool.get(A, A) is not None)
    assert attempts == [A, A]
    pool.close_all()


def test_a_failed_sample_closes_the_transport_and_backs_off():
    clock = Clock()
    attempts = []

    def opener(recorder_id):
        attempts.append(recorder_id)
        return Transport(recorder_id)

    pool = aa._SamplerDrivers(opener, generation=lambda _rid: "g", clock=clock)
    pool.get(A, A)
    assert wait_until(lambda: pool.get(A, A) is not None)
    transport = pool.get(A, A)
    pool.failed(A, DriverError("still request timed out"))
    assert transport.closed
    for _ in range(10):
        assert pool.get(A, A) is None
    assert attempts == [A], "no re-probe per failed sample"
    pool.close_all()


def test_a_slow_recorder_open_does_not_delay_the_lease_refresh(tmp_path, monkeypatch):
    stop = threading.Event()
    polls = []

    class Cloud:
        def call(self, name, **kw):
            if name == "wl_agent_analytics_config":
                polls.append(time.monotonic())
                return {"changed": False, "multi_agent_enabled": False}
            return {}

    class Mux:
        camera_count = 1

        def __init__(self, **_kw):
            pass

        def configure(self, _config):
            pass

        def sample_plan(self):
            return [("target-a", 0.25)]

        def resolve_target(self, _target):
            return A, "1"

        def on_frame(self, *_a):
            return []

    release = threading.Event()

    def slow_open(_cfg):
        release.wait(5)                      # a recorder that takes seconds to answer
        raise NvrUnreachable("recorder did not answer")

    monkeypatch.setattr(aa.core, "Cloud", lambda url, key: Cloud())
    monkeypatch.setattr(aa.core, "log", lambda _m: None)
    monkeypatch.setattr(aa.recorder_analytics, "RecorderAnalyticsMux", Mux)
    monkeypatch.setattr(aa.recorder_runtime, "config_for_cloud_recorder", lambda cfg, _r: cfg)
    monkeypatch.setattr(aa, "_open_analytics_driver", slow_open)
    cfg = SimpleNamespace(
        analytics_enabled=True, supabase_url="https://cloud.invalid", publishable_key="pk",
        analytics_spool_path=tmp_path / "analytics.sqlite",
        analytics_config_path=tmp_path / "analytics_config.json",
        analytics_status_path=tmp_path / "analytics_status.json",
        analytics_bootstrap_marker=tmp_path / "bootstrap.json",
        bootstrap_site_type="", bootstrap_camera_profiles=[],
        analytics_poll_seconds=0.05, analytics_upload_seconds=1000, analytics_max_fps=4.0,
    )
    detector = SimpleNamespace(available=True, detect=lambda _raw: None)
    worker = threading.Thread(target=aa.analytics_worker, args=(cfg, {
        "agent_id": "agent", "agent_key": "key"}, detector, stop, {"ok": True}))
    worker.start()
    try:
        time.sleep(1.0)
        assert len(polls) >= 4, f"only {len(polls)} config/lease poll(s) while a recorder opened"
    finally:
        release.set()
        stop.set()
        worker.join(10)
    assert not worker.is_alive()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
