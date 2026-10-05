#!/usr/bin/env python3
"""MNVR-008 in the multi-recorder fan-out: a recorder is live by its event stream, not its probe.

The fan-out counts a recorder as observed through _fresh(holder) and keeps each recorder's
last_live by the same rule. Before the 5.0.28 merge the packaged collector stamped
recorder_live_at as soon as open_driver's probe answered, and Hikvision keep-alives / Dahua
heartbeats never counted as activity. So a recorder whose deviceInfo answered while alertStream
returned 503 counted as live (and kept its outage clock moving), and a quiet but connected
recorder went stale after 150 s although its stream was healthy.

This runs the real fan-out loop over three recorders with the real packaged collector and real
Hikvision/Dahua drivers on fake sessions:
  A  Hikvision, alertStream open, keep-alives only (no event all night)
  B  Dahua, attach open, heartbeats only
  C  Hikvision, deviceInfo answers, alertStream refused with 503
A and B must count as live from their keep-alives and keep their outage clocks at the stream's
own activity; C must not count as live and its outage clock must not move.

ONVIF has no stream-activity reporting in 5.0.28, so its empty-pull variant is not covered here.
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
import native_event_collector  # noqa: E402
import recovery  # noqa: E402
import watchlog_agent as core  # noqa: E402
from drivers.dahua import DahuaDriver  # noqa: E402
from drivers.hikvision import HikvisionDriver  # noqa: E402

REC_A = "a0000000-0000-4000-8000-00000000000a"
REC_B = "b0000000-0000-4000-8000-00000000000b"
REC_C = "c0000000-0000-4000-8000-00000000000c"

KEEPALIVE = (b"--boundary\r\nContent-Type: application/xml\r\n\r\n"
             b"<EventNotificationAlert><eventType>videoloss</eventType>"
             b"<eventState>inactive</eventState><channelID>1</channelID>"
             b"<activePostCount>0</activePostCount></EventNotificationAlert>")


class _Resp:
    """A streamed HTTP response that keeps sending keep-alives until ``end`` is set."""

    def __init__(self, status, end: threading.Event, chunk=None, lines=()):
        self.status_code = status
        self.headers = {}
        self.content = b""
        self.text = ""
        self.end, self.chunk, self.lines = end, chunk, list(lines)

    def iter_content(self, chunk_size=1024):
        while not self.end.is_set():
            yield self.chunk
            self.end.wait(0.05)

    def iter_lines(self, chunk_size=512):
        while not self.end.is_set():
            yield from self.lines
            self.end.wait(0.05)

    def close(self):
        pass


class _Session:
    def __init__(self, resp):
        self.resp = resp
        self.auth = None

    def request(self, method, url, **kw):
        return self.resp

    def get(self, url, **kw):
        return self.resp

    def close(self):
        pass


class _Info:
    vendor = "TestVendor"
    model = "T-1"
    firmware = "1.0"


def _hikvision(resp):
    driver = HikvisionDriver("http://192.0.2.10", "admin", "x", timeout=1)
    driver.s = _Session(resp)
    return driver


def _dahua(resp):
    driver = DahuaDriver("http://192.0.2.11", "admin", "x", timeout=1)
    driver.s = _Session(resp)
    return driver


def _prepared(root: Path, name: str, rid: str):
    state = root / name
    cfg = SimpleNamespace(
        recorder_cloud_id=rid, recorder_display_name=name,
        spool_path=state / "spool.sqlite", health_store_path=state / "health.sqlite",
        last_live_path=state / "last_live.json", spool_max_rows=1000,
        health_batch=4, health_concurrency=1, health_seconds=300,
        recovery_enabled=True, recovery_seconds=300, recovery_threshold_seconds=180,
        snapshots=False, snapshot_min_interval=0,
        upload_seconds=15, heartbeat_seconds=0)
    return SimpleNamespace(
        context=SimpleNamespace(config=cfg, holder={}, cloud_recorder_id=rid,
                                display_name=name, is_primary=name == "A"),
        device=SimpleNamespace(vendor=name, model="TEST", driver="hikvision"),
        channels=[{"channel": "1"}], capabilities=None,
        camera_mapping={"1": f"{rid[:8]}-0000-4000-8000-000000000001"}, error=None)


def test_quiet_streams_stay_live_and_a_refused_stream_is_not(monkeypatch, tmp_path):
    end = threading.Event()
    drivers = {
        REC_A: lambda: _hikvision(_Resp(200, end, chunk=KEEPALIVE)),
        REC_B: lambda: _dahua(_Resp(200, end, lines=[b"--myboundary", b"Heartbeat", b""])),
        REC_C: lambda: _hikvision(_Resp(503, end)),
    }
    recorders = [_prepared(tmp_path, "A", REC_A), _prepared(tmp_path, "B", REC_B),
                 _prepared(tmp_path, "C", REC_C)]
    holders = {item.context.cloud_recorder_id: item.context.holder for item in recorders}
    # Each recorder was last seen a minute ago: within the outage threshold, so a stream that
    # is live moves its clock on, and one that is not leaves it where it is.
    seeded = datetime.now(timezone.utc) - timedelta(seconds=60)
    for item in recorders:
        path = item.context.config.last_live_path
        path.parent.mkdir(parents=True, exist_ok=True)
        recovery.persist_last_live(path, seeded)

    def streams_settled():
        a = holders[REC_A].get("event_stream") or {}
        b = holders[REC_B].get("event_stream") or {}
        c = holders[REC_C].get("event_stream") or {}
        return bool(a.get("last_frame_at") and b.get("last_frame_at") and c.get("last_error"))

    beats, health = [], []
    sleeps = {"n": 0}

    def sleep(_seconds):
        sleeps["n"] += 1
        if sleeps["n"] == 1:
            deadline = time.monotonic() + 5
            while not streams_settled() and time.monotonic() < deadline:
                time.sleep(0.02)
        elif sleeps["n"] >= 3:
            end.set()
            raise KeyboardInterrupt

    def idle(*_args, **_kwargs):
        return None

    monkeypatch.setattr(core, "collector", native_event_collector.collector)
    monkeypatch.setattr(core, "open_driver",
                        lambda cfg: (drivers[cfg.recorder_cloud_id](), _Info()))
    monkeypatch.setattr(core.credential_store, "credential_generation", lambda: "gen-1")
    for name in ("recovery_worker", "health_worker", "command_worker"):
        monkeypatch.setattr(core, name, idle)
    monkeypatch.setattr(core, "upload_once", lambda *a, **k: 0)
    monkeypatch.setattr(core, "heartbeat", lambda *a, **k: beats.append(k))
    monkeypatch.setattr(core, "update_runtime_health", lambda **k: health.append(k))
    monkeypatch.setattr(core, "log", lambda *_a, **_k: None)
    monkeypatch.setattr(fanout.time, "sleep", sleep)
    try:
        fanout.run(SimpleNamespace(recovery_enabled=True, upload_seconds=15, heartbeat_seconds=0),
                   {"agent_id": "agent", "agent_key": "key"}, object(), once=False,
                   prepared_recorders=recorders, detector=None,
                   analytics_worker=idle, archive_worker=idle)
    finally:
        end.set()

    assert beats and health
    # Two of three recorders are observed, by their streams. C's probe answered, but no event
    # can arrive on a refused stream, so the site's full recorder set is not live.
    assert health[-1]["recorders_live"] == 2, health[-1]
    assert beats[-1]["recorder_live"] is False
    # A quiet stream is not a gap: A's and B's clocks follow their keep-alives.
    for rid in (REC_A, REC_B):
        item = next(i for i in recorders if i.context.cloud_recorder_id == rid)
        moved = recovery.read_last_live(item.context.config.last_live_path)
        assert moved > seeded, (rid, moved, seeded)
    # C was not observed, so its outage clock stays where it was.
    assert recovery.read_last_live(recorders[2].context.config.last_live_path) == seeded
    # Each recorder keeps its own event-stream state for the heartbeat.
    assert streams_settled(), {rid: h.get("event_stream") for rid, h in holders.items()}
    assert holders[REC_C]["event_stream"]["connected"] is False
    assert "503" in holders[REC_C]["event_stream"]["last_error"]
