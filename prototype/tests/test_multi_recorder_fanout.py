"""True multi-recorder worker fan-out contracts."""
from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

import multi_recorder_fanout as fanout  # noqa: E402
import watchlog_agent as core  # noqa: E402


def _cfg(root: Path, name: str, recorder_id: str):
    state_dir = root / name
    return SimpleNamespace(
        recorder_cloud_id=recorder_id,
        recorder_display_name=name,
        spool_path=state_dir / "spool.sqlite",
        health_store_path=state_dir / "health.sqlite",
        last_live_path=state_dir / "last_live.json",
        spool_max_rows=1000,
        health_batch=4,
        health_concurrency=1,
        health_seconds=1,
        recovery_enabled=True,
        recovery_seconds=1,
        upload_seconds=1,
        heartbeat_seconds=1,
    )


def _prepared(root: Path, name: str, rid: str, *, primary=False,
              channels=None, mapping=None, error=None):
    cfg = _cfg(root, name, rid)
    ctx = SimpleNamespace(
        config=cfg,
        holder={},
        cloud_recorder_id=rid,
        display_name=name,
        is_primary=primary,
    )
    device = SimpleNamespace(vendor=name, model="TEST", driver="onvif")
    return SimpleNamespace(
        context=ctx,
        device=device,
        channels=list(channels or []),
        capabilities=None,
        camera_mapping=mapping,
        error=error,
    )


def test_build_worker_sets_isolates_spools_holders_and_thread_names(tmp_path):
    a = _prepared(
        tmp_path, "A", "11111111-1111-1111-1111-111111111111",
        primary=True,
        channels=[{"channel": "1", "name": "A1"}],
        mapping={"1": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"},
    )
    b = _prepared(
        tmp_path, "B", "22222222-2222-2222-2222-222222222222",
        channels=[{"channel": "1", "name": "B1"}],
        mapping={"1": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"},
    )
    stop = threading.Event()
    units = fanout.build_worker_sets(
        [a, b], {"agent_id": "agent"}, object(), stop
    )
    try:
        assert len(units) == 2
        assert units[0].spool.path != units[1].spool.path
        assert units[0].spool.max_rows == a.context.config.spool_max_rows
        assert units[1].spool.max_rows == b.context.config.spool_max_rows
        assert units[0].holder is not units[1].holder
        assert units[0].holder["recorder_cloud_id"] != units[1].holder["recorder_cloud_id"]
        assert units[0].channels[0]["camera_id"].startswith("aaaaaaaa")
        assert units[1].channels[0]["camera_id"].startswith("bbbbbbbb")
        assert units[0].collector.name != units[1].collector.name
        assert units[0].health.name != units[1].health.name
        assert units[0].recovery is not None and units[1].recovery is not None
    finally:
        fanout._close(units)


def test_failed_preflight_recorder_gets_live_health_but_no_guessed_recovery(tmp_path):
    bad = _prepared(
        tmp_path, "B", "22222222-2222-2222-2222-222222222222",
        channels=[], mapping=None, error="DriverError: offline",
    )
    units = fanout.build_worker_sets(
        [bad], {"agent_id": "agent"}, object(), threading.Event()
    )
    try:
        assert units[0].collector is not None
        assert units[0].health is not None
        assert units[0].recovery is None
        assert units[0].channels == []
    finally:
        fanout._close(units)


def test_duplicate_cloud_recorder_identity_fails_closed(tmp_path):
    rid = "11111111-1111-1111-1111-111111111111"
    a = _prepared(tmp_path, "A", rid, primary=True)
    b = _prepared(tmp_path, "B", rid)
    with pytest.raises(RuntimeError, match="duplicate cloud recorder"):
        fanout.build_worker_sets(
            [a, b], {"agent_id": "agent"}, object(), threading.Event()
        )


def test_once_mode_starts_both_collectors_and_uploads_both_spools(
        tmp_path, monkeypatch):
    a = _prepared(
        tmp_path, "A", "11111111-1111-1111-1111-111111111111",
        primary=True,
        channels=[{"channel": "1"}],
        mapping={"1": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"},
    )
    b = _prepared(
        tmp_path, "B", "22222222-2222-2222-2222-222222222222",
        channels=[{"channel": "1"}],
        mapping={"1": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"},
    )

    started = []
    uploaded = []
    health = []
    heartbeats = []
    runtime_health = []

    def fake_collector(cfg, spool, stop, holder):
        started.append(cfg.recorder_cloud_id)
        holder["recorder_live_at"] = time.monotonic()
        spool.add({
            "recorder_id": cfg.recorder_cloud_id,
            "channel": "1",
            "event_type": "test",
            "device_ts": "2026-10-03T09:00:00Z",
            "agent_ts": "2026-10-03T09:00:00Z",
            "payload": {},
        })
        stop.wait()

    def fake_analytics(_cfg, _state, _detector, stop, _authority):
        stop.wait()

    monkeypatch.setattr(core, "collector", fake_collector)
    monkeypatch.setattr(core, "ONCE_COLLECT_SECONDS", 0.05)
    monkeypatch.setattr(
        core, "upload_once",
        lambda _cloud, _state, spool: uploaded.append(spool.path) or 1,
    )
    monkeypatch.setattr(
        core, "health_cycle",
        lambda _cloud, _state, cfg, _holder: health.append(cfg.recorder_cloud_id),
    )
    monkeypatch.setattr(
        core, "heartbeat",
        lambda _cloud, _state, device, recorder_live=None:
            heartbeats.append((device.vendor if device else None, recorder_live)),
    )
    monkeypatch.setattr(
        core, "update_runtime_health",
        lambda **kwargs: runtime_health.append(kwargs),
    )

    fanout.run(
        SimpleNamespace(recovery_enabled=True),
        {"agent_id": "agent", "agent_key": "key"},
        object(),
        once=True,
        prepared_recorders=[a, b],
        detector=None,
        analytics_worker=fake_analytics,
        archive_worker=lambda *_args: None,
    )

    assert set(started) == {
        a.context.cloud_recorder_id, b.context.cloud_recorder_id
    }
    assert len(uploaded) == 2 and uploaded[0] != uploaded[1]
    assert set(health) == {
        a.context.cloud_recorder_id, b.context.cloud_recorder_id
    }
    assert heartbeats == [("A", True)]
    assert runtime_health[-1]["recorders_total"] == 2
    assert runtime_health[-1]["recorders_live"] == 2
    assert runtime_health[-1]["multi_recorder"] is True


def test_locked_detector_serializes_detect_and_classify():
    class Detector:
        available = True
        model_name = "test"
        def __init__(self):
            self.calls = []
        def detect(self, value):
            self.calls.append(("detect", value))
            return [value]
        def classify_event(self, value):
            self.calls.append(("classify", value))
            return True, [value]

    raw = Detector()
    locked = fanout.LockedDetector(raw)
    assert locked.detect("a") == ["a"]
    assert locked.classify_event("b") == (True, ["b"])
    assert raw.calls == [("detect", "a"), ("classify", "b")]


def test_single_recorder_cannot_enter_multi_fanout(tmp_path):
    a = _prepared(
        tmp_path, "A", "11111111-1111-1111-1111-111111111111", primary=True
    )
    with pytest.raises(RuntimeError, match="at least two"):
        fanout.run(
            SimpleNamespace(), {}, object(), once=True,
            prepared_recorders=[a], detector=None,
            analytics_worker=lambda *_args: None,
            archive_worker=lambda *_args: None,
        )
