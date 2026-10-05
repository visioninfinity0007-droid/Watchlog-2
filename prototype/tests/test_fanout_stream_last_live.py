#!/usr/bin/env python3
"""The multi-recorder fan-out keeps each recorder's last_live by the 5.0.28 rule.

last_live.json is a recorder's outage clock: recovery_worker opens last_live..now as a recovery
interval once the recorder is live again. The single-recorder loop writes it only from the
recorder's event-stream activity (never a probe), at the time of that activity, and never over a
stored value older than the outage threshold, because that value is an outage recovery has not
opened yet. The fan-out heartbeat wrote now_utc() for any recorder whose probe stamp was fresh,
so a recorder back from an outage had its gap overwritten within one heartbeat, before its
recovery worker (one cycle every few minutes) could read it, and a recorder with a dead event
stream still looked observed.

This drives the real multi_recorder_fanout.run loop (workers stubbed, collectors faked) over
three recorders and checks each recorder's own last_live.json.
"""
from __future__ import annotations

import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))

import analytics_agent  # noqa: E402,F401  (the shipped loop that owns the last_live rule)
import multi_recorder_fanout as fanout  # noqa: E402
import recovery  # noqa: E402
import watchlog_agent as core  # noqa: E402

FRAME = datetime(2026, 10, 4, 16, 0, 0, tzinfo=timezone.utc)


class _StreamDriver:
    def __init__(self, activity):
        self.last_activity_monotonic = activity


def _cfg(root: Path, name: str, rid: str):
    state = root / name
    return SimpleNamespace(
        recorder_cloud_id=rid, recorder_display_name=name,
        spool_path=state / "spool.sqlite", health_store_path=state / "health.sqlite",
        last_live_path=state / "last_live.json", spool_max_rows=1000,
        health_batch=4, health_concurrency=1, health_seconds=300,
        recovery_enabled=True, recovery_seconds=300, recovery_threshold_seconds=180,
        upload_seconds=15, heartbeat_seconds=0)


def _prepared(root: Path, name: str, rid: str, primary=False):
    cfg = _cfg(root, name, rid)
    return SimpleNamespace(
        context=SimpleNamespace(config=cfg, holder={}, cloud_recorder_id=rid,
                                display_name=name, is_primary=primary),
        device=SimpleNamespace(vendor=name, model="TEST", driver="hikvision"),
        channels=[{"channel": "1"}], capabilities=None,
        camera_mapping={"1": f"{rid[:8]}-cam"}, error=None)


def _live_stream():
    return {"live_driver": _StreamDriver(time.monotonic()),
            "event_stream": {"connected": True,
                             "connected_at": (FRAME - timedelta(minutes=5)).isoformat(),
                             "last_frame_at": FRAME.isoformat(), "last_error": None}}


def _probe_only():
    # A recorder whose probe answers but whose driver reports no event stream.
    return {"live_driver": object(), "recorder_live_at": time.monotonic()}


def _run(monkeypatch, recorders, states, *, recovery_enabled=True):
    ready = threading.Event()
    seen = set()

    def collector(cfg, spool, stop, holder):
        holder.update(states[cfg.recorder_cloud_id]())
        seen.add(cfg.recorder_cloud_id)
        if len(seen) == len(states):
            ready.set()
        stop.wait(10)

    def idle(*_args, **_kwargs):
        return None

    sleeps = {"n": 0}

    def sleep(_seconds):
        sleeps["n"] += 1
        if sleeps["n"] == 1:
            ready.wait(5)
        elif sleeps["n"] >= 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(core, "collector", collector)
    for name in ("recovery_worker", "health_worker", "command_worker"):
        monkeypatch.setattr(core, name, idle)
    monkeypatch.setattr(core, "upload_once", lambda *a, **k: 0)
    monkeypatch.setattr(core, "heartbeat", lambda *a, **k: None)
    monkeypatch.setattr(core, "update_runtime_health", lambda **_k: None)
    monkeypatch.setattr(fanout.time, "sleep", sleep)
    fanout.run(SimpleNamespace(recovery_enabled=recovery_enabled, upload_seconds=15,
                               heartbeat_seconds=0),
               {"agent_id": "agent", "agent_key": "key"}, object(), once=False,
               prepared_recorders=recorders, detector=None,
               analytics_worker=idle, archive_worker=idle)


def test_each_recorder_keeps_its_own_stream_based_outage_clock(monkeypatch, tmp_path):
    a = _prepared(tmp_path, "A", "11111111-1111-1111-1111-111111111111", primary=True)
    b = _prepared(tmp_path, "B", "22222222-2222-2222-2222-222222222222")
    c = _prepared(tmp_path, "C", "33333333-3333-3333-3333-333333333333")
    # A came back from an hour-long outage its recovery worker has not opened yet.
    outage_start = FRAME - timedelta(hours=1)
    a_path = a.context.config.last_live_path
    a_path.parent.mkdir(parents=True, exist_ok=True)
    recovery.persist_last_live(a_path, outage_start)

    _run(monkeypatch, [a, b, c], {
        a.context.cloud_recorder_id: _live_stream,
        b.context.cloud_recorder_id: _probe_only,
        c.context.cloud_recorder_id: _live_stream,
    })

    # A: the gap is left for recovery_worker to open (it then moves last_live on itself).
    assert recovery.read_last_live(a_path) == outage_start
    # B: a probe is not stream activity, so B is not observed and nothing is written.
    assert not b.context.config.last_live_path.exists()
    # C: seeded at the stream's last activity, not at the heartbeat's wall clock.
    assert recovery.read_last_live(c.context.config.last_live_path) == FRAME


def test_recovery_disabled_keeps_only_a_live_recorders_marker(monkeypatch, tmp_path):
    # With recovery off nothing opens outage intervals, but the marker is still each
    # recorder's live proof for the Repair/Upgrade gate (MNVR-040): kept for a live
    # recorder, never written for one whose event stream is down.
    a = _prepared(tmp_path, "A", "11111111-1111-1111-1111-111111111111", primary=True)
    b = _prepared(tmp_path, "B", "22222222-2222-2222-2222-222222222222")
    for item in (a, b):
        item.context.config.recovery_enabled = False

    _run(monkeypatch, [a, b], {
        a.context.cloud_recorder_id: _live_stream,
        b.context.cloud_recorder_id: dict,          # no event-stream activity at all
    }, recovery_enabled=False)

    assert recovery.read_last_live(a.context.config.last_live_path) is not None
    assert not b.context.config.last_live_path.exists()
