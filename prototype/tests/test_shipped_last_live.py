#!/usr/bin/env python3
"""MNVR-005: the SHIPPED run loop keeps last_live.json current from event-stream activity.

The packaged agent runs analytics_agent.enhanced_cmd_run, which replaces core.cmd_run, the
only loop that persisted last_live on the heartbeat cadence. recovery_worker writes it only
after detecting an outage, which needs an existing last_live, so the file was never
created: restarts, reboots and recorder outages were never opened for recovery.

This drives the real enhanced_cmd_run loop (workers stubbed, collector faked) and checks:
  * a live event stream seeds last_live on first start, at the stream's last activity;
  * it then advances on the heartbeat while the stream stays live;
  * a stored value older than the outage threshold is left for recovery_worker to open;
  * a dead stream, a driver that cannot report its stream, or recovery disabled never
    write it (liveness is never guessed from a probe).
"""
from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))

import analytics_agent  # noqa: E402
import recovery  # noqa: E402
import watchlog_agent as core  # noqa: E402

FRAME = datetime(2026, 10, 4, 16, 0, 0, tzinfo=timezone.utc)


class FakeSpool:
    def __init__(self, *args, **kwargs):
        self.args = args

    def count(self):
        return 0

    def close(self):
        pass


class LiveDriver:
    def __init__(self, activity):
        self.last_activity_monotonic = activity


def _run(monkeypatch, tmp_path, holder_state, *, recovery_enabled=True):
    """Run enhanced_cmd_run for three loop iterations with a heartbeat on each."""
    beats = []
    ready = threading.Event()

    def collector(cfg, spool, stop, holder):
        holder.update(holder_state())
        ready.set()
        stop.wait(10)

    def idle(*args, **kwargs):
        return None

    sleeps = {"n": 0}

    def sleep(_seconds):
        sleeps["n"] += 1
        if sleeps["n"] == 1:
            ready.wait(5)
        elif sleeps["n"] >= 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(core.vision, "build", lambda _cfg, _log: None)
    monkeypatch.setattr(core, "collector", collector)
    for name in ("recovery_worker", "health_worker", "command_worker", "health_cycle"):
        monkeypatch.setattr(core, name, idle)
    monkeypatch.setattr(core, "upload_once", lambda *a, **k: 0)
    monkeypatch.setattr(core, "heartbeat", lambda *a, **k: beats.append(k))
    monkeypatch.setattr(analytics_agent, "analytics_worker", idle)
    monkeypatch.setattr(analytics_agent, "archive_worker", idle)
    monkeypatch.setattr(analytics_agent, "Spool", FakeSpool)
    monkeypatch.setattr(analytics_agent.time, "sleep", sleep)

    cfg = SimpleNamespace(
        spool_path=tmp_path / "spool.sqlite", spool_max_rows=0,
        health_batch=4, health_concurrency=2, upload_seconds=15, heartbeat_seconds=0,
        health_seconds=300, recovery_enabled=recovery_enabled,
        last_live_path=tmp_path / "last_live.json", recovery_threshold_seconds=180)
    analytics_agent.enhanced_cmd_run(cfg, {"agent_id": "a", "agent_key": "k"}, object(),
                                     once=False, device=None, channels=[])
    return cfg, beats


def _live_stream():
    return {"live_driver": LiveDriver(time.monotonic()),
            "event_stream": {"connected": True,
                             "connected_at": (FRAME - timedelta(minutes=5)).isoformat(),
                             "last_frame_at": FRAME.isoformat(), "last_error": None}}


def test_live_stream_seeds_last_live_on_first_start(monkeypatch, tmp_path):
    cfg, beats = _run(monkeypatch, tmp_path, _live_stream)
    assert cfg.last_live_path.exists(), "the shipped loop never wrote last_live.json"
    assert recovery.read_last_live(cfg.last_live_path) == FRAME
    assert beats and beats[-1]["recorder_live"] is True
    assert beats[-1]["event_stream"]["connected"] is True


def test_last_live_advances_while_the_stream_stays_live(monkeypatch, tmp_path):
    path = tmp_path / "last_live.json"
    recovery.persist_last_live(path, FRAME - timedelta(seconds=60))
    cfg, _beats = _run(monkeypatch, tmp_path, _live_stream)
    assert recovery.read_last_live(cfg.last_live_path) == FRAME


def test_an_outage_older_than_the_threshold_is_left_for_recovery(monkeypatch, tmp_path):
    path = tmp_path / "last_live.json"
    before = FRAME - timedelta(hours=1)
    recovery.persist_last_live(path, before)
    cfg, _beats = _run(monkeypatch, tmp_path, _live_stream)
    # recovery_worker opens before..now from this value and then moves it on itself.
    assert recovery.read_last_live(cfg.last_live_path) == before


def test_dead_stream_writes_nothing_and_is_not_live(monkeypatch, tmp_path):
    def dead():
        return {"live_driver": LiveDriver(0.0),
                "event_stream": {"connected": False, "connected_at": None,
                                 "last_frame_at": None, "last_error": "HTTP 503"}}
    cfg, beats = _run(monkeypatch, tmp_path, dead)
    assert not cfg.last_live_path.exists()
    assert beats and all(b["recorder_live"] is False for b in beats)


def test_probe_only_liveness_never_writes_last_live(monkeypatch, tmp_path):
    def legacy():
        return {"live_driver": object(), "recorder_live_at": time.monotonic(),
                "event_stream": {"connected": None, "connected_at": None,
                                 "last_frame_at": None, "last_error": None}}
    cfg, beats = _run(monkeypatch, tmp_path, legacy)
    assert not cfg.last_live_path.exists()
    assert beats[-1]["recorder_live"] is True       # legacy transport stamp, unchanged


def test_recovery_disabled_writes_nothing(monkeypatch, tmp_path):
    cfg, _beats = _run(monkeypatch, tmp_path, _live_stream, recovery_enabled=False)
    assert not cfg.last_live_path.exists()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
