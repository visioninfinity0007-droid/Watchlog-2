#!/usr/bin/env python3
"""MNVR-005 / MNVR-008 (ONVIF): recorder liveness comes from the pull-point subscription.

OnvifDriver did not report its event stream, so the packaged collector kept the legacy
transport stamp for it: holder["recorder_live_at"] moved whenever GetDeviceInformation
answered, whatever PullMessages did. An ONVIF site (Al-Khalid) whose pull point failed on every
pull still showed a live recorder, the heartbeat advanced recorder_seen_at, and the shipped
last_live persistence (which only follows a reported stream) never ran, so no outage interval
could open for it.

OnvifDriver now declares reports_stream_activity and keeps last_activity_monotonic plus an
event_stream state {connected, connected_at, last_frame_at, last_error}. Every PullMessages
response that comes back counts, empty pulls included (they are the long-poll keep-alive); a
failed pull, subscribe or renew never does and marks the stream down.
"""
from __future__ import annotations

import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

import onvif_fake_recorder as fx  # noqa: E402
from native_collector_harness import run_collector  # noqa: E402
import analytics_agent  # noqa: E402
import recovery  # noqa: E402
import watchlog_agent as core  # noqa: E402
from drivers import onvif_driver  # noqa: E402
from drivers.base import DriverError  # noqa: E402

REAL_OPEN_DRIVER = core.open_driver


@pytest.fixture
def recorder(monkeypatch):
    rec = fx.FakeRecorder()
    rec.install(monkeypatch)
    return rec


def _open():
    driver, _info = REAL_OPEN_DRIVER(fx.FakeCfg())
    assert driver.name == "onvif"
    return driver


def test_onvif_driver_reports_its_event_stream():
    assert onvif_driver.OnvifDriver.reports_stream_activity is True
    driver = onvif_driver.OnvifDriver("http://192.0.2.10", "u", "p", timeout=1)
    try:
        assert driver.last_activity_monotonic == 0.0
        assert driver.event_stream == {"connected": False, "connected_at": None,
                                       "last_frame_at": None, "last_error": None}
    finally:
        driver.close()


def test_probe_alone_stamps_no_stream_activity(recorder):
    driver = _open()                       # GetDeviceInformation + GetCapabilities + GetProfiles
    try:
        assert driver.last_activity_monotonic == 0.0
        assert driver.event_stream["connected"] is False
    finally:
        driver.close()


def test_every_successful_pull_stamps_activity_empty_pulls_included(recorder, monkeypatch):
    ticks = iter(range(5000, 6000))
    monkeypatch.setattr(onvif_driver, "time",
                        SimpleNamespace(monotonic=lambda: float(next(ticks))))
    driver = _open()
    seen = []
    original = recorder._op_PullMessages

    def pull(body):
        seen.append(driver.last_activity_monotonic)    # the previous pull's stamp
        return original(body)

    monkeypatch.setattr(recorder, "_op_PullMessages", pull)
    recorder.queue()                       # three empty pulls, then the batches run dry
    recorder.queue()
    recorder.queue()
    try:
        assert fx.stream(driver, recorder) == []
    finally:
        driver.close()
    assert len(seen) == 3
    assert seen[0] == 0.0                  # subscribing is not activity
    assert seen[1] > seen[0] and seen[2] > seen[1]
    assert driver.last_activity_monotonic > seen[2]
    assert driver.event_stream["last_frame_at"] is not None
    assert driver.event_stream["connected_at"] is not None
    assert driver.event_stream["connected"] is False   # the stream ended (stop)
    assert driver.event_stream["last_error"] is None


def test_a_failing_pull_stamps_nothing_and_marks_the_stream_down(recorder):
    recorder.fail = {"PullMessages"}
    driver = _open()
    try:
        with pytest.raises(DriverError):
            list(driver.stream_events(threading.Event()))
    finally:
        driver.close()
    assert recorder.calls_of("CreatePullPointSubscription")      # the subscription worked
    assert driver.last_activity_monotonic == 0.0
    assert driver.event_stream["connected"] is False
    assert driver.event_stream["connected_at"] is None
    assert driver.event_stream["last_frame_at"] is None
    error = driver.event_stream["last_error"]
    assert "500" in error
    for leaked in ("http", "0.0.0.0", "192.0.2.10", fx.FakeCfg.nvr_password):
        assert leaked not in error


def test_a_failed_pull_after_good_ones_keeps_only_the_good_stamp(recorder, monkeypatch):
    driver = _open()
    original = recorder._op_PullMessages
    pulls = []

    def pull(body):
        pulls.append(1)
        if len(pulls) == 2:
            recorder.fail = {"PullMessages"}        # the third pull fails
        return original(body)

    monkeypatch.setattr(recorder, "_op_PullMessages", pull)
    try:
        with pytest.raises(DriverError):
            list(driver.stream_events(threading.Event()))
    finally:
        driver.close()
    stamp = driver.last_activity_monotonic
    assert stamp > 0
    assert time.monotonic() - stamp < 5
    assert driver.event_stream["connected"] is False
    assert "500" in driver.event_stream["last_error"]


def test_an_answer_without_a_pull_response_is_not_activity(recorder, monkeypatch):
    # HTTP 200 with a body that is not a PullMessagesResponse delivered nothing. It must not
    # count, and it must not be re-pulled in a tight loop either: it fails the stream.
    stop = threading.Event()
    answers = []

    def unexpected(body):
        answers.append(1)
        if len(answers) >= 3:
            stop.set()                     # bound the test if every answer were accepted
        return "<tev:Unexpected/>"

    monkeypatch.setattr(recorder, "_op_PullMessages", unexpected)
    driver = _open()
    try:
        with pytest.raises(DriverError):
            list(driver.stream_events(stop))
    finally:
        driver.close()
    assert driver.last_activity_monotonic == 0.0
    assert driver.event_stream["connected"] is False
    assert driver.event_stream["last_error"]


def test_a_failing_onvif_pull_stream_is_never_live(recorder, monkeypatch, tmp_path):
    """GetDeviceInformation answers on every reopen, PullMessages fails every time. Over more
    than 150 s of collector reopens the recorder must never read live and last_live must not
    move forward."""
    recorder.fail = {"PullMessages"}
    now = [1000.0]
    cfg = SimpleNamespace(recovery_enabled=True, last_live_path=tmp_path / "last_live.json",
                          recovery_threshold_seconds=180)
    seeded = datetime.now(timezone.utc) - timedelta(seconds=60)
    recovery.persist_last_live(cfg.last_live_path, seeded)
    holder, samples = {}, []

    def wait(stop, _cfg, auth_failures, last_gen, seconds=None):
        clock = time.monotonic()
        samples.append(analytics_agent._recorder_stream_live(holder, clock))
        analytics_agent._persist_stream_last_live(cfg, holder, clock)
        now[0] += core.DRIVER_RETRY_SECONDS if seconds is None else seconds
        if now[0] - 1000.0 > 200:
            stop.set()
            return "stop", last_gen
        return "timeout", last_gen

    _spool, holder, _ok = run_collector(
        monkeypatch, lambda cfg: REAL_OPEN_DRIVER(fx.FakeCfg()), holder=holder,
        reconnect_wait=wait, timeout=20.0)
    assert len(samples) > 5, "the collector stopped reopening the stream"
    assert not any(samples), "a pull point that never answered a pull was reported live"
    assert not holder.get("recorder_live_at")
    assert holder["event_stream"]["connected"] is False
    assert holder["event_stream"]["last_frame_at"] is None
    assert "500" in holder["event_stream"]["last_error"]
    assert recovery.read_last_live(cfg.last_live_path) == seeded


def test_a_quiet_onvif_stream_is_live_and_moves_last_live(recorder, monkeypatch, tmp_path):
    """Empty pulls only (a quiet site): the recorder is live and last_live follows the stream."""
    cfg = SimpleNamespace(recovery_enabled=True, last_live_path=tmp_path / "last_live.json",
                          recovery_threshold_seconds=180)
    seeded = datetime.now(timezone.utc) - timedelta(seconds=60)
    recovery.persist_last_live(cfg.last_live_path, seeded)
    checks = []

    def live(holder, _spool):
        stream = holder.get("event_stream") or {}
        if not stream.get("last_frame_at"):
            return False
        clock = time.monotonic()
        checks.append(analytics_agent._recorder_stream_live(holder, clock))
        analytics_agent._persist_stream_last_live(cfg, holder, clock)
        return True

    _spool, holder, ok = run_collector(
        monkeypatch, lambda cfg: REAL_OPEN_DRIVER(fx.FakeCfg()), until=live, timeout=10.0)
    assert ok and checks == [True]
    assert recovery.read_last_live(cfg.last_live_path) > seeded


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
