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
        assert units[0].holder["synced_channels"][0]["camera_id"].startswith("aaaaaaaa")
        assert units[1].holder["synced_channels"][0]["camera_id"].startswith("bbbbbbbb")
    finally:
        fanout._close(units)


def test_failed_preflight_recorder_gets_dormant_dynamic_recovery(tmp_path):
    bad = _prepared(
        tmp_path, "B", "22222222-2222-2222-2222-222222222222",
        channels=[], mapping=None, error="DriverError: offline",
    )
    units = fanout.build_worker_sets(
        [bad], {"agent_id": "agent"}, object(), threading.Event()
    )
    try:
        unit = units[0]
        assert unit.collector is not None
        assert unit.health is not None
        assert unit.recovery is not None
        assert unit.channels == []
        assert unit.holder["synced_channels"] == []

        # The recovery worker receives a provider rather than a frozen list.
        provider = unit.recovery._args[5]
        assert callable(provider)
        assert provider() == []

        unit.holder["synced_channels"] = [{
            "channel": "1",
            "camera_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        }]
        assert provider()[0]["channel"] == "1"
    finally:
        fanout._close(units)


def test_reconnect_inventory_sync_is_idempotent_and_enables_channels():
    rid = "22222222-2222-2222-2222-222222222222"
    holder = {}
    calls = []

    class Cloud:
        def call(self, name, **kwargs):
            calls.append((name, kwargs))
            if name == "wl_sync_recorder_cameras":
                return {
                    "1": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1",
                    "2": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb2",
                }
            if name == "wl_sync_recorder_capabilities":
                return {"ok": True}
            raise AssertionError(name)

    class Driver:
        def capabilities(self):
            return {"channels": [{"channel": "1"}, {"channel": "2"}]}

    assessment = {
        "channels": {
            "enumerated": True,
            "reported": [
                {"channel": "1", "enabled": True},
                {"channel": "2", "enabled": True},
            ],
        }
    }
    cfg = SimpleNamespace(recorder_cloud_id=rid)
    state = {"agent_id": "agent", "agent_key": "key"}

    core._retry_recorder_cloud_inventory(
        Cloud(), state, cfg, holder, assessment, Driver()
    )
    assert holder["camera_sync_signature"] == ("1", "2")
    assert [x["channel"] for x in holder["synced_channels"]] == ["1", "2"]
    assert len(holder["camera_mapping"]) == 2
    first_count = len(calls)

    # Same enumerated channel signature does not resync every health cycle.
    core._retry_recorder_cloud_inventory(
        Cloud(), state, cfg, holder, assessment, Driver()
    )
    assert len(calls) == first_count


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


# --- failure isolation in the fan-out (MNVR-009, MNVR-021) -------------------------

def _patch_once(monkeypatch, started, health, runtime_health):
    def fake_collector(cfg, spool, stop, holder):
        started.append(cfg.recorder_cloud_id)
        holder["recorder_live_at"] = time.monotonic()
        stop.wait()

    monkeypatch.setattr(core, "collector", fake_collector)
    monkeypatch.setattr(core, "ONCE_COLLECT_SECONDS", 0.1)
    monkeypatch.setattr(core, "upload_once", lambda *_a, **_k: 0)
    monkeypatch.setattr(core, "health_cycle",
                        lambda _c, _s, cfg, _h: health.append(cfg.recorder_cloud_id))
    monkeypatch.setattr(core, "heartbeat", lambda *_a, **_k: None)
    monkeypatch.setattr(core, "update_runtime_health",
                        lambda **kwargs: runtime_health.append(kwargs))


def test_degraded_recorder_is_held_while_its_sibling_runs(tmp_path, monkeypatch):
    a = _prepared(tmp_path, "A", "11111111-1111-1111-1111-111111111111", primary=True,
                  channels=[{"channel": "1"}], mapping={"1": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"})
    b = _prepared(tmp_path, "B", "22222222-2222-2222-2222-222222222222",
                  error="recorder login unavailable on this PC (SecretError)")
    b.context.config.credential_error = "SecretError"
    started, health, runtime_health = [], [], []
    _patch_once(monkeypatch, started, health, runtime_health)

    fanout.run(SimpleNamespace(recovery_enabled=True), {"agent_id": "agent", "agent_key": "key"},
               object(), once=True, prepared_recorders=[a, b], detector=None,
               analytics_worker=lambda *_a: None, archive_worker=lambda *_a: None)

    assert started == [a.context.cloud_recorder_id], "B is never contacted without its login"
    assert health == [a.context.cloud_recorder_id], "no health report from an empty login"
    assert runtime_health[-1]["recorders_live"] == 1
    assert runtime_health[-1]["recorders_total"] == 2


def test_credential_watch_releases_a_degraded_recorder(monkeypatch):
    cfg = SimpleNamespace(recorder_display_name="B", credential_error="SecretError",
                          credential_generation_seen="absent", nvr_password="")
    holder = {"credential_unavailable": True}
    generations = iter(["absent", "1:2:abc", "1:2:abc"])
    reloaded = []
    monkeypatch.setattr(fanout, "CREDENTIAL_RECHECK_SECONDS", 0.01)
    monkeypatch.setattr(core, "_credential_generation_for_cfg", lambda _cfg: next(generations))
    monkeypatch.setattr(core, "_reload_credential_for_cfg",
                        lambda c: reloaded.append(c) or setattr(c, "nvr_password", "pw"))
    monkeypatch.setattr(core, "log", lambda _m: None)
    ready, stop = threading.Event(), threading.Event()

    fanout._watch_credential(cfg, holder, ready, stop)

    assert ready.is_set() and reloaded == [cfg]
    assert cfg.credential_error is None and cfg.nvr_password == "pw"
    assert cfg.credential_generation_seen == "1:2:abc"
    assert "credential_unavailable" not in holder


def test_gated_worker_waits_for_the_login_and_stops_cleanly():
    ran = []
    ready, stop = threading.Event(), threading.Event()
    worker = threading.Thread(target=fanout._gated(lambda *a: ran.append(a), ready, stop),
                              args=("x",))
    worker.start()
    time.sleep(0.2)
    assert ran == []
    ready.set()
    worker.join(3)
    assert ran == [("x",)]

    ran.clear()
    ready, stop = threading.Event(), threading.Event()
    worker = threading.Thread(target=fanout._gated(lambda *a: ran.append(a), ready, stop))
    worker.start()
    stop.set()
    worker.join(3)
    assert not worker.is_alive() and ran == []


def test_late_probe_result_seeds_the_recorder_inventory(tmp_path):
    import multi_recorder_orchestrator as mro

    base = _prepared(tmp_path, "B", "22222222-2222-2222-2222-222222222222")
    slot = mro.PreparedRecorder(context=base.context, device=None, channels=[],
                                capabilities=None, camera_mapping=None,
                                pending=True, ready=threading.Event())
    a = _prepared(tmp_path, "A", "11111111-1111-1111-1111-111111111111", primary=True)
    units = fanout.build_worker_sets([a, slot], {"agent_id": "agent"}, object(),
                                     threading.Event())
    try:
        holder = units[1].holder
        assert holder["synced_channels"] == [] and holder["monitor"] is None
        slot._resolve(mro.PreparedRecorder(
            context=base.context, device=SimpleNamespace(vendor="B", model="X", driver="x"),
            channels=[{"channel": "1", "name": "Gate"}], capabilities=None,
            camera_mapping={"1": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"}))
        assert holder["camera_mapping"] == {"1": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"}
        assert holder["synced_channels"][0]["camera_id"].startswith("bbbbbbbb")
        assert holder["camera_sync_signature"] == ("1",)
        assert holder["monitor"] is not None
    finally:
        fanout._close(units)


def test_run_loop_restarts_cleanly_when_the_background_check_asks(tmp_path, monkeypatch):
    a = _prepared(tmp_path, "A", "11111111-1111-1111-1111-111111111111", primary=True)
    b = _prepared(tmp_path, "B", "22222222-2222-2222-2222-222222222222")

    def idle(*_a, **_k):
        return None

    for name in ("collector", "recovery_worker", "health_worker", "command_worker"):
        monkeypatch.setattr(core, name, idle)
    monkeypatch.setattr(core, "heartbeat", idle)
    monkeypatch.setattr(core, "update_runtime_health", idle)
    real_sleep = time.sleep
    monkeypatch.setattr(fanout.time, "sleep", lambda _s: real_sleep(0.01))

    def refuse(stop, restart):
        restart["reason"] = "recorder check refused the saved recorder identity; restarting"

    base = SimpleNamespace(recovery_enabled=False, upload_seconds=60, heartbeat_seconds=60)
    with pytest.raises(SystemExit, match="restarting"):
        fanout.run(base, {"agent_id": "agent", "agent_key": "key"}, object(), once=False,
                   prepared_recorders=[a, b], detector=None, analytics_worker=idle,
                   archive_worker=idle, recorder_check=refuse)


# --- per-recorder live state for Repair/Upgrade (MNVR-040, Agent side) -------------

def _live_only_a(a_rid):
    def fake_collector(cfg, spool, stop, holder):
        if cfg.recorder_cloud_id == a_rid:
            holder["recorder_live_at"] = time.monotonic()
        stop.wait()
    return fake_collector


def test_once_mode_publishes_each_recorders_live_state(tmp_path, monkeypatch):
    a = _prepared(tmp_path, "A", "11111111-1111-1111-1111-111111111111", primary=True)
    a.context.local_id, a.context.continuity_owner = "local-a", True
    b = _prepared(tmp_path, "B", "22222222-2222-2222-2222-222222222222")
    b.context.local_id, b.context.continuity_owner = "local-b", False
    runtime_health = []
    monkeypatch.setattr(core, "collector", _live_only_a(a.context.cloud_recorder_id))
    monkeypatch.setattr(core, "ONCE_COLLECT_SECONDS", 0.1)
    monkeypatch.setattr(core, "upload_once", lambda *_a, **_k: 0)
    monkeypatch.setattr(core, "health_cycle", lambda *_a, **_k: None)
    monkeypatch.setattr(core, "heartbeat", lambda *_a, **_k: None)
    monkeypatch.setattr(core, "update_runtime_health", lambda **kw: runtime_health.append(kw))

    fanout.run(SimpleNamespace(recovery_enabled=False), {"agent_id": "agent", "agent_key": "key"},
               object(), once=True, prepared_recorders=[a, b], detector=None,
               analytics_worker=lambda *_a: None, archive_worker=lambda *_a: None)

    rows = {row["local_id"]: row for row in runtime_health[-1]["recorders"]}
    assert rows["local-a"]["live"] is True and rows["local-a"]["last_live_at"]
    assert rows["local-a"]["continuity_owner"] is True
    assert rows["local-a"]["recorder_id"] == a.context.cloud_recorder_id
    assert rows["local-b"]["live"] is False and rows["local-b"]["last_live_at"] is None
    assert rows["local-b"]["continuity_owner"] is False
    text = repr(runtime_health[-1])
    assert "192.0.2" not in text and "nvr_url" not in text


def test_heartbeat_publishes_live_state_and_markers_with_recovery_off(tmp_path, monkeypatch):
    a = _prepared(tmp_path, "A", "11111111-1111-1111-1111-111111111111", primary=True)
    a.context.local_id, a.context.continuity_owner = "local-a", True
    b = _prepared(tmp_path, "B", "22222222-2222-2222-2222-222222222222")
    b.context.local_id, b.context.continuity_owner = "local-b", False
    for item in (a, b):
        item.context.config.recovery_enabled = False
    runtime_health = []

    def idle(*_a, **_k):
        return None

    def cloud_down(*_a, **_k):
        raise RuntimeError("cloud unreachable")

    monkeypatch.setattr(core, "collector", _live_only_a(a.context.cloud_recorder_id))
    for name in ("recovery_worker", "health_worker", "command_worker"):
        monkeypatch.setattr(core, name, idle)
    monkeypatch.setattr(core, "heartbeat", cloud_down)
    monkeypatch.setattr(core, "upload_once", idle)
    monkeypatch.setattr(core, "update_runtime_health", lambda **kw: runtime_health.append(kw))
    real_sleep, ticks = time.sleep, []

    def sleep(_seconds):
        ticks.append(1)
        if len(ticks) > 3:
            raise KeyboardInterrupt
        real_sleep(0.05)

    monkeypatch.setattr(fanout.time, "sleep", sleep)
    base = SimpleNamespace(recovery_enabled=False, upload_seconds=60, heartbeat_seconds=0.01)
    fanout.run(base, {"agent_id": "agent", "agent_key": "key"}, object(), once=False,
               prepared_recorders=[a, b], detector=None, analytics_worker=idle,
               archive_worker=idle)

    published = [kw for kw in runtime_health if "recorders" in kw]
    assert published, "per-recorder live state is published even while the cloud is down"
    rows = {row["local_id"]: row for row in published[-1]["recorders"]}
    assert rows["local-a"]["live"] is True and rows["local-b"]["live"] is False
    assert published[-1]["recorders_live"] == 1 and published[-1]["recorders_total"] == 2
    # Per-recorder last_live markers are written even with recovery disabled.
    import recovery
    assert recovery.read_last_live(a.context.config.last_live_path) is not None
    assert recovery.read_last_live(b.context.config.last_live_path) is None


# --- one recorder's local queue fault never stops the others ----------------------------

def _corrupt_row(spool_path: Path) -> None:
    import sqlite3
    db = sqlite3.connect(spool_path)
    db.execute("insert into spool (payload) values (?)", ("{not json",))
    db.commit()
    db.close()


def _event(rid):
    return {"recorder_id": rid, "channel": "1", "event_type": "test",
            "device_ts": "2026-10-03T09:00:00Z", "agent_ts": "2026-10-03T09:00:00Z",
            "payload": {}}


def test_corrupt_spool_row_on_b_degrades_only_b_and_a_keeps_uploading(tmp_path, monkeypatch):
    a_rid, b_rid = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
    a = _prepared(tmp_path, "A", a_rid, primary=True)
    a.context.local_id = "local-a"
    b = _prepared(tmp_path, "B", b_rid)
    b.context.local_id = "local-b"
    ingested, runtime_health = [], []

    def idle(*_a, **_k):
        return None

    def collector(cfg, spool, stop, holder):
        holder["recorder_live_at"] = time.monotonic()
        if cfg.recorder_cloud_id == b_rid:
            _corrupt_row(cfg.spool_path)        # a row no JSON parser can read
        spool.add(_event(cfg.recorder_cloud_id))
        stop.wait()

    class Cloud:
        def call(self, fn, **kw):
            assert fn == "wl_ingest_events"
            ingested.extend(ev["recorder_id"] for ev in kw["p_events"])
            return {"received": len(kw["p_events"]), "inserted": len(kw["p_events"]),
                    "skipped": 0}

    monkeypatch.setattr(core, "collector", collector)
    for name in ("recovery_worker", "health_worker", "command_worker", "heartbeat"):
        monkeypatch.setattr(core, name, idle)
    monkeypatch.setattr(core, "update_runtime_health", lambda **kw: runtime_health.append(kw))
    monkeypatch.setattr(fanout.periodic_stills, "periodic_still_worker", idle)
    real_sleep, ticks = time.sleep, []

    def sleep(_seconds):
        ticks.append(1)
        if len(ticks) > 4:
            raise KeyboardInterrupt
        real_sleep(0.05)

    monkeypatch.setattr(fanout.time, "sleep", sleep)
    base = SimpleNamespace(recovery_enabled=False, upload_seconds=0.01, heartbeat_seconds=0.01)
    # Before the fix the ValueError escaped run() and its finally stopped every recorder.
    fanout.run(base, {"agent_id": "agent", "agent_key": "key"}, Cloud(), once=False,
               prepared_recorders=[a, b], detector=None, analytics_worker=idle,
               archive_worker=idle)

    assert a_rid in ingested and b_rid not in ingested
    assert len(ticks) > 4, "the run loop kept going after B's upload failed"
    rows = {row["local_id"]: row for row in runtime_health[-1]["recorders"]}
    assert rows["local-a"].get("upload") is None and rows["local-a"]["live"] is True
    assert rows["local-b"]["upload"] == "degraded"
    assert rows["local-b"]["upload_reason"].startswith("JSONDecodeError")
    assert rows["local-b"]["live"] is False, "B's events are not reaching WatchLog"
    assert runtime_health[-1]["recorders_live"] == 1


def test_a_spool_that_cannot_open_degrades_only_that_recorder(tmp_path, monkeypatch):
    a_rid, b_rid = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
    a = _prepared(tmp_path, "A", a_rid, primary=True)
    a.context.local_id = "local-a"
    b = _prepared(tmp_path, "B", b_rid)
    b.context.local_id = "local-b"
    real_spool = fanout.Spool

    def spool(path, max_rows=0):
        if Path(path) == b.context.config.spool_path:
            import sqlite3
            raise sqlite3.DatabaseError("file is not a database")
        return real_spool(path, max_rows)

    monkeypatch.setattr(fanout, "Spool", spool)
    started, health, runtime_health = [], [], []
    _patch_once(monkeypatch, started, health, runtime_health)
    uploaded = []
    monkeypatch.setattr(core, "upload_once", lambda _c, _s, sp: uploaded.append(sp.path) or 0)

    fanout.run(SimpleNamespace(recovery_enabled=True), {"agent_id": "agent", "agent_key": "key"},
               object(), once=True, prepared_recorders=[a, b], detector=None,
               analytics_worker=lambda *_a: None, archive_worker=lambda *_a: None)

    assert started == [a_rid], "B has nowhere to queue events: its collector is not started"
    assert uploaded == [a.context.config.spool_path]
    rows = {row["local_id"]: row for row in runtime_health[-1]["recorders"]}
    assert rows["local-b"]["upload"] == "degraded"
    assert rows["local-b"]["upload_reason"].startswith("DatabaseError")
    assert rows["local-a"].get("upload") is None
