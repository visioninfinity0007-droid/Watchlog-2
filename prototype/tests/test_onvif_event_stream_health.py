#!/usr/bin/env python3
"""ONVIF diagnostics reach the agent log and the per-recorder event_stream health block.

The ONVIF driver drops (and counts) events whose Source token matches no camera, and measures
the recorder clock against the PC on every stamped notification. Both are the cheapest field
proof for an ONVIF site (which Source token the recorder really sends; whether its clock is
off), but the packaged collector never set the driver's log hook, so the drop and skew lines
were a no-op, and nothing reached the heartbeat's local health proof.

The collector now routes driver.log to the agent log, and the driver writes dropped_unmapped and
last_clock_skew_s into the event_stream state the heartbeat publishes (counts and seconds only:
no token, address or credential).
"""
from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

import onvif_fake_recorder as fx  # noqa: E402
import native_event_collector  # noqa: E402
import watchlog_agent as core  # noqa: E402
import agent_core  # noqa: E402

MOTION_ALARM = "tns1:VideoSource/MotionAlarm"
T0 = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)


class Spool:
    def __init__(self):
        self.rows = []

    def add(self, row):
        self.rows.append(row)

    def trim(self):
        return 0


def _run_collector(monkeypatch, recorder):
    lines = []
    monkeypatch.setattr(core, "log", lines.append)
    monkeypatch.setattr(core.credential_store, "credential_generation", lambda: "absent")
    holder, spool, stop = {}, Spool(), threading.Event()
    recorder.stop = stop
    native_event_collector.collector(fx.FakeCfg(), spool, stop, holder)
    return holder, spool, lines


def test_collector_routes_onvif_drops_and_clock_to_the_log_and_event_stream(monkeypatch):
    rec = fx.FakeRecorder()
    rec.install(monkeypatch)
    fx.WallClock.install(monkeypatch, T0)
    rec.queue(fx.notification(MOTION_ALARM, fx.iso(T0), {"Source": "VideoSourceToken_099"},
                              {"State": "true"}),
              fx.notification(MOTION_ALARM, fx.iso(T0), {"Source": "VideoSourceToken_098"},
                              {"State": "true"}),
              fx.notification(MOTION_ALARM, fx.iso(T0 + timedelta(hours=5)),
                              {"Source": fx.source_token(2)}, {"State": "true"}))
    holder, spool, lines = _run_collector(monkeypatch, rec)

    assert [(r["channel"], r["event_type"]) for r in spool.rows] == [("2", "motion")]
    dropped = [line for line in lines if "onvif: dropped motion event" in line]
    assert len(dropped) == 1 and "VideoSourceToken_099" in dropped[0]
    assert any("onvif: recorder clock is +18000 s from this PC" in line for line in lines)
    stream = holder["event_stream"]
    assert stream["dropped_unmapped"] == 2
    assert stream["last_clock_skew_s"] == 5 * 3600
    for line in lines:
        assert fx.FakeCfg.nvr_password not in line


def test_last_clock_skew_follows_the_latest_stamped_notification(monkeypatch):
    rec = fx.FakeRecorder()
    rec.install(monkeypatch)
    fx.WallClock.install(monkeypatch, T0)
    rec.queue(fx.notification(MOTION_ALARM, fx.iso(T0 + timedelta(hours=5)),
                              {"Source": fx.source_token(2)}, {"State": "true"}),
              fx.notification(MOTION_ALARM, fx.iso(T0 - timedelta(seconds=7)),
                              {"Source": fx.source_token(3)}, {"State": "true"}))
    holder, _spool, _lines = _run_collector(monkeypatch, rec)
    assert holder["event_stream"]["last_clock_skew_s"] == -7
    assert "dropped_unmapped" not in holder["event_stream"]


class Cloud:
    def call(self, fn, **kw):
        return {}


def test_heartbeat_publishes_the_onvif_counters(monkeypatch, tmp_path):
    path = tmp_path / "runtime-health.json"
    monkeypatch.setattr(agent_core, "runtime_health_path", lambda: path)  # heartbeat lives in agent_core (shared with Setup); patch its own collaborator
    stream = {"connected": True, "connected_at": "2026-10-04T10:00:00+00:00",
              "last_frame_at": "2026-10-04T10:00:30+00:00", "last_error": None,
              "dropped_unmapped": 3, "last_clock_skew_s": -42}
    core.heartbeat(Cloud(), {"agent_id": "a", "agent_key": "k"}, None,
                   recorder_live=True, event_stream=stream)
    health = json.loads(path.read_text(encoding="utf-8"))["event_stream"]
    assert health["dropped_unmapped"] == 3
    assert health["last_clock_skew_s"] == -42


def test_heartbeat_shape_is_unchanged_for_drivers_without_the_counters(monkeypatch, tmp_path):
    path = tmp_path / "runtime-health.json"
    monkeypatch.setattr(agent_core, "runtime_health_path", lambda: path)  # heartbeat lives in agent_core (shared with Setup); patch its own collaborator
    stream = {"connected": True, "connected_at": None, "last_frame_at": None, "last_error": None}
    core.heartbeat(Cloud(), {"agent_id": "a", "agent_key": "k"}, None,
                   recorder_live=True, event_stream=stream)
    health = json.loads(path.read_text(encoding="utf-8"))["event_stream"]
    assert set(health) == {"connected", "connected_at", "last_frame_at", "last_error"}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
