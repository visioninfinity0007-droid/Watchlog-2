#!/usr/bin/env python3
"""ONVIF stream liveness and event-stream reporting per recorder in the fan-out (5.0.28, ported).

5.0.28 made an ONVIF recorder live only from answered PullMessages (empty pulls included),
never from GetDeviceInformation, and reports the recorder's event-stream state (connected,
last frame, redacted last error, dropped_unmapped, last_clock_skew_s) at every heartbeat. The
single-recorder loop passes holder["event_stream"] to the heartbeat; the multi-recorder fan-out
heartbeat passed none, so on a multi-recorder site the local health proof never said which
recorder's event stream was down or why.

Two ONVIF recorders share channel 1, run through the real fan-out loop, the real packaged
collector and real OnvifDriver instances on fake SOAP sessions:
  A  pull point answers with empty pulls only (a quiet site)
  B  GetDeviceInformation answers, every PullMessages fails
A must count as live and keep its outage clock moving; B must not count as live and its clock
must not move. Each recorder's row in the protected runtime-health proof carries that
recorder's own event-stream state, with no recorder LAN address in it.
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
sys.path.insert(0, str(Path(__file__).resolve().parent))
import programdata_sandbox  # noqa: E402,F401  (before any agent import: no writes to the real ProgramData)

import onvif_fake_recorder as fx  # noqa: E402
import analytics_agent  # noqa: E402,F401  (the shipped loop that owns the last_live rule)
import multi_recorder_fanout as fanout  # noqa: E402
import native_event_collector  # noqa: E402
import periodic_stills  # noqa: E402
import recovery  # noqa: E402
import watchlog_agent as core  # noqa: E402
from drivers.onvif_driver import OnvifDriver  # noqa: E402

REC_A = "a0000000-0000-4000-8000-00000000000a"
REC_B = "b0000000-0000-4000-8000-00000000000b"
HOSTS = {REC_A: "192.0.2.10", REC_B: "192.0.2.11"}


class _Info:
    vendor = "TestVendor"
    model = "T-1"
    firmware = "1.0"


def _prepared(root: Path, name: str, rid: str):
    state = root / name
    cfg = SimpleNamespace(
        recorder_cloud_id=rid, recorder_local_id=f"local-{name}", recorder_display_name=name,
        spool_path=state / "spool.sqlite", health_store_path=state / "health.sqlite",
        last_live_path=state / "last_live.json", spool_max_rows=1000,
        health_batch=4, health_concurrency=1, health_seconds=300,
        recovery_enabled=True, recovery_seconds=300, recovery_threshold_seconds=180,
        snapshots=False, snapshot_min_interval=0, upload_seconds=15, heartbeat_seconds=0)
    return SimpleNamespace(
        context=SimpleNamespace(config=cfg, holder={}, cloud_recorder_id=rid,
                                display_name=name, is_primary=name == "A",
                                continuity_owner=name == "A", local_id=f"local-{name}"),
        device=SimpleNamespace(vendor=name, model="TEST", driver="onvif"),
        channels=[{"channel": "1"}], capabilities=None,
        camera_mapping={"1": f"{rid[:8]}-0000-4000-8000-000000000001"}, error=None)


def test_onvif_pull_liveness_and_event_stream_are_reported_per_recorder(monkeypatch, tmp_path):
    fakes = {REC_A: fx.FakeRecorder(), REC_B: fx.FakeRecorder()}
    fakes[REC_B].fail = {"PullMessages"}

    def open_driver(cfg):
        rid = cfg.recorder_cloud_id
        driver = OnvifDriver(f"http://{HOSTS[rid]}", "local-user", "local-password", timeout=1)
        driver.s = fx._Session(fakes[rid])
        driver.probe()                       # GetDeviceInformation answers on both
        return driver, _Info()

    recorders = [_prepared(tmp_path, "A", REC_A), _prepared(tmp_path, "B", REC_B)]
    holders = {item.context.cloud_recorder_id: item.context.holder for item in recorders}
    seeded = datetime.now(timezone.utc) - timedelta(seconds=60)
    for item in recorders:
        path = item.context.config.last_live_path
        path.parent.mkdir(parents=True, exist_ok=True)
        recovery.persist_last_live(path, seeded)

    def settled():
        a = holders[REC_A].get("event_stream") or {}
        b = holders[REC_B].get("event_stream") or {}
        return bool(a.get("last_frame_at") and b.get("last_error"))

    beats, health = [], []
    sleeps = {"n": 0}

    def sleep(_seconds):
        sleeps["n"] += 1
        if sleeps["n"] == 1:
            deadline = time.monotonic() + 5
            while not settled() and time.monotonic() < deadline:
                time.sleep(0.02)
        elif sleeps["n"] >= 3:
            raise KeyboardInterrupt

    def idle(*_args, **_kwargs):
        return None

    monkeypatch.setattr(core, "collector", native_event_collector.collector)
    monkeypatch.setattr(core, "open_driver", open_driver)
    monkeypatch.setattr(core.credential_store, "recorder_credential_generation",
                        lambda local_id: f"gen-{local_id}")
    monkeypatch.setattr(core.vision, "build", lambda _cfg, _log: None)
    for name in ("recovery_worker", "health_worker", "command_worker"):
        monkeypatch.setattr(core, name, idle)
    monkeypatch.setattr(core, "upload_once", lambda *a, **k: 0)
    monkeypatch.setattr(core, "heartbeat", lambda *a, **k: beats.append(k))
    monkeypatch.setattr(core, "update_runtime_health", lambda **k: health.append(k))
    monkeypatch.setattr(core, "log", lambda *_a, **_k: None)
    monkeypatch.setattr(fanout.time, "sleep", sleep)
    monkeypatch.setattr(periodic_stills, "periodic_still_worker", idle)
    fanout.run(SimpleNamespace(recovery_enabled=True, upload_seconds=15, heartbeat_seconds=0),
               {"agent_id": "agent", "agent_key": "key"}, object(), once=False,
               prepared_recorders=recorders, detector=None,
               analytics_worker=idle, archive_worker=idle)

    assert settled(), {rid: h.get("event_stream") for rid, h in holders.items()}
    assert beats and health
    last = health[-1]
    # Live by answered pulls, not by the probe: A yes, B no; the site's full set is not live.
    assert last["recorders_live"] == 1, last
    assert beats[-1]["recorder_live"] is False
    rows = {row["recorder_id"]: row for row in last["recorders"]}
    assert rows[REC_A]["live"] is True and rows[REC_B]["live"] is False

    # Each recorder's own event-stream state, in the 5.0.28 heartbeat shape.
    stream_a, stream_b = rows[REC_A]["event_stream"], rows[REC_B]["event_stream"]
    assert stream_a["connected"] is True and stream_a["last_frame_at"]
    assert stream_a["last_error"] is None
    assert stream_b["connected"] is False and stream_b["last_frame_at"] is None
    assert "500" in stream_b["last_error"]
    for row in rows.values():
        text = repr(row)
        assert all(host not in text for host in HOSTS.values()), row
        assert "local-password" not in text

    # The outage clocks: A follows its answered pulls, B stays where it was.
    moved = recovery.read_last_live(recorders[0].context.config.last_live_path)
    assert moved > seeded
    assert recovery.read_last_live(recorders[1].context.config.last_live_path) == seeded


def test_a_recorder_without_stream_reporting_adds_no_event_stream_row(tmp_path):
    item = _prepared(tmp_path, "A", REC_A)
    unit = SimpleNamespace(holder={"recorder_live_at": time.monotonic()},
                           prepared=item, cfg=item.context.config)
    _live, rows = fanout._recorder_live_state([unit], time.monotonic(), "2026-10-05T00:00:00Z")
    assert rows[0]["live"] is True
    assert "event_stream" not in rows[0]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
