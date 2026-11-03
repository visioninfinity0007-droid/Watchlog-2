#!/usr/bin/env python3
"""Periodic stills run per recorder in the multi-recorder fan-out (5.0.28 NEW-L2, ported).

5.0.28 starts one periodic_still_worker from the single-recorder run loop. A site with two or
more recorders runs multi_recorder_fanout.run instead, which started no still producer at all,
so an upgrade to the multi-recorder Agent would have silently removed every timed still the
vision workers and restaurant reports consume. The fan-out now builds one producer per
recorder context, on that recorder's own spool, driver, credential and back-off.

Two fake recorders share channel 1, as two real recorders on one site do:
  * each recorder's stills are stamped with its own recorder_id, so the server dedupe
    identity (recorder namespace + channel + device_event_id, 0154) never collides even when
    the 5.0.28 device_event_id is identical; the production payload contract is unchanged;
  * each still comes from its own recorder;
  * one recorder failing (unreachable, or held for an unreadable login) does not stop the
    other recorder's stills;
  * each recorder samples by its own Monitor/Ignore choices;
  * the fan-out run loop starts one producer per recorder in run mode only.

The separate worker runs for Dahua and ONVIF recorders, so these recorders are Dahua. A
Hikvision recorder takes its stills between live alert-stream slices on its own session (field
Build 69; test_hikvision_field_safety.py), so the worker stands down for it and hands the
recorder's camera choices to that stream instead (last test).
"""
from __future__ import annotations

import base64
import json
import re
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import programdata_sandbox  # noqa: E402,F401  (before any agent import: no writes to the real ProgramData)

import multi_recorder_fanout as fanout  # noqa: E402
import periodic_stills as ps  # noqa: E402
import watchlog_agent as core  # noqa: E402
from drivers.base import NvrUnreachable  # noqa: E402

STATE = {"agent_id": "agent-1", "agent_key": "key-1"}
REC_A = "a0000000-0000-4000-8000-00000000000a"
REC_B = "b0000000-0000-4000-8000-00000000000b"
JPEG_A = b"\xff\xd8" + b"recorder-A" * 8 + b"\xff\xd9"
JPEG_B = b"\xff\xd8" + b"recorder-B" * 8 + b"\xff\xd9"
PAYLOAD = {"sample": True, "source": "periodic_snapshot", "vendor": "dahua"}


def _wait_for(predicate, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class _Driver:
    name = "dahua-cgi"

    def __init__(self, still, channels=("1",)):
        self.still = still
        self.channels = [{"channel": ch, "enabled": True} for ch in channels]
        self.calls = []

    def list_channels(self):
        return self.channels

    def get_snapshot(self, channel):
        self.calls.append(str(channel))
        return self.still

    def probe(self):
        return None

    def close(self):
        pass


def _prepared(root: Path, name: str, rid: str, *, continuity=False, profiles=None,
              credential_error=None):
    state = root / name
    state.mkdir(parents=True, exist_ok=True)
    cfg = SimpleNamespace(
        recorder_cloud_id=rid, recorder_local_id=f"local-{name}", recorder_display_name=name,
        recorder_state_dir=state, nvr_driver="dahua-cgi",
        nvr_url=f"http://192.0.2.{10 if name == 'A' else 11}",
        nvr_username="local-user", nvr_password="local-password",
        spool_path=state / "spool.sqlite", health_store_path=state / "health.sqlite",
        last_live_path=state / "last_live.json", spool_max_rows=1000,
        health_batch=4, health_concurrency=1, snapshots=True, snapshot_min_interval=1,
        camera_profiles=list(profiles or []), credential_error=credential_error,
        recovery_enabled=False)
    return SimpleNamespace(
        context=SimpleNamespace(config=cfg, holder={}, cloud_recorder_id=rid,
                                display_name=name, is_primary=continuity,
                                continuity_owner=continuity),
        device=None, channels=[{"channel": "1", "name": f"{name} gate"}],
        capabilities=None, camera_mapping={"1": f"{rid[:8]}-0000-4000-8000-000000000001"},
        error=None)


def _write_recorder_profiles(item, profiles):
    cfg = item.context.config
    (Path(cfg.recorder_state_dir) / ps.RECORDER_CAMERA_PROFILES_NAME).write_text(
        json.dumps({"schema": ps.RECORDER_CAMERA_PROFILES_SCHEMA,
                    "local_id": cfg.recorder_local_id, "display_name": cfg.recorder_display_name,
                    "profiles": profiles}), encoding="utf-8")


@pytest.fixture
def fast(monkeypatch):
    """One still per camera per second, the first one at once; logs captured."""
    monkeypatch.setenv("WATCHLOG_PERIODIC_STILL_SECONDS", "1")
    monkeypatch.delenv("WATCHLOG_PERIODIC_STILLS", raising=False)
    monkeypatch.setattr(ps, "MIN_CADENCE_SECONDS", 1)
    monkeypatch.setattr(ps, "STARTUP_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(core.credential_store, "recorder_credential_generation",
                        lambda local_id: f"gen-{local_id}")
    lines = []
    monkeypatch.setattr(core, "log", lambda message, *_a, **_k: lines.append(str(message)))
    return lines


def _rows(spool):
    _ids, rows = spool.take(500)
    return rows


class _Site:
    """build_worker_sets over the given recorders; starts only their still producers."""

    def __init__(self, monkeypatch, recorders, drivers):
        self.opens = {rid: 0 for rid in drivers}

        def open_driver(cfg):
            rid = cfg.recorder_cloud_id
            self.opens[rid] += 1
            made = drivers[rid]
            if isinstance(made, Exception):
                raise made
            return made, SimpleNamespace(vendor="TestVendor", model="T-1")

        monkeypatch.setattr(core, "open_driver", open_driver)
        self.stop = threading.Event()
        self.units = fanout.build_worker_sets(recorders, STATE, object(), self.stop)
        self.by_id = {unit.cfg.recorder_cloud_id: unit for unit in self.units}

    def __enter__(self):
        for unit in self.units:
            unit.stills.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        for unit in self.units:
            if unit.credential_ready is not None:
                unit.credential_ready.set()
            if unit.stills.is_alive():
                unit.stills.join(timeout=5)
        fanout._close(self.units)


def test_each_recorder_produces_its_own_stamped_stills_on_a_shared_channel(
        monkeypatch, tmp_path, fast):
    a = _prepared(tmp_path, "A", REC_A, continuity=True)
    b = _prepared(tmp_path, "B", REC_B)
    drivers = {REC_A: _Driver(JPEG_A), REC_B: _Driver(JPEG_B)}
    with _Site(monkeypatch, [a, b], drivers) as site:
        assert [u.stills.name for u in site.units] == [
            f"periodic-stills-{REC_A[:8]}", f"periodic-stills-{REC_B[:8]}"]
        assert _wait_for(lambda: all(u.spool.count() >= 1 for u in site.units)), fast[-5:]
        rows = {rid: _rows(site.by_id[rid].spool) for rid in (REC_A, REC_B)}

    for rid, jpeg in ((REC_A, JPEG_A), (REC_B, JPEG_B)):
        assert rows[rid], rid
        for row in rows[rid]:
            # The 5.0.28 production contract, plus this recorder's identity.
            assert set(row) == {"channel", "event_type", "device_event_id", "device_ts",
                                "agent_ts", "payload", "snapshot_b64", "recorder_id"}, row
            assert row["recorder_id"] == rid
            assert row["channel"] == "1" and row["event_type"] == "visual_sample"
            assert row["payload"] == PAYLOAD
            assert re.fullmatch(r"dahua-sample-1-\d+", row["device_event_id"])
            # The still came from this recorder, not the other one with the same channel.
            assert base64.b64decode(row["snapshot_b64"]) == jpeg
    assert drivers[REC_A].calls and set(drivers[REC_A].calls) == {"1"}
    assert drivers[REC_B].calls and set(drivers[REC_B].calls) == {"1"}

    # Server dedupe identity (wl_recorder_event_dedupe_key, 0154): the continuity recorder keeps
    # the historical channel namespace, every other recorder is namespaced by its id. Two stills
    # in the same 30 s bucket on channel 1 never collide.
    def identity(row, continuity):
        namespace = row["channel"] if continuity else f"{row['recorder_id']}:{row['channel']}"
        return namespace, row["device_event_id"]

    ids_a = {identity(r, True) for r in rows[REC_A]}
    ids_b = {identity(r, False) for r in rows[REC_B]}
    assert not ids_a & ids_b
    assert {r["recorder_id"] for r in rows[REC_A]} == {REC_A}
    assert {r["recorder_id"] for r in rows[REC_B]} == {REC_B}


def test_one_unreachable_recorder_does_not_stop_the_others_stills(monkeypatch, tmp_path, fast):
    a = _prepared(tmp_path, "A", REC_A, continuity=True)
    b = _prepared(tmp_path, "B", REC_B)
    drivers = {REC_A: NvrUnreachable("http://192.0.2.10/ISAPI timed out"),
               REC_B: _Driver(JPEG_B)}
    with _Site(monkeypatch, [a, b], drivers) as site:
        # B keeps producing while A is backed off.
        assert _wait_for(lambda: site.by_id[REC_B].spool.count() >= 2), fast[-5:]
        assert site.opens[REC_A] >= 1
        assert site.by_id[REC_A].spool.count() == 0
        assert all(u.stills.is_alive() for u in site.units)
        rows_b = _rows(site.by_id[REC_B].spool)
    assert {r["recorder_id"] for r in rows_b} == {REC_B}
    failures = [line for line in fast if "not reachable" in line]
    assert failures, fast
    # The back-off line names the recorder, never its LAN address.
    assert all("(A)" in line and "192.0.2.10" not in line for line in failures), failures


def test_a_recorder_held_for_its_login_takes_no_stills_until_released(
        monkeypatch, tmp_path, fast):
    a = _prepared(tmp_path, "A", REC_A, continuity=True)
    b = _prepared(tmp_path, "B", REC_B, credential_error="SecretError")
    drivers = {REC_A: _Driver(JPEG_A), REC_B: _Driver(JPEG_B)}
    with _Site(monkeypatch, [a, b], drivers) as site:
        assert _wait_for(lambda: site.by_id[REC_A].spool.count() >= 1)
        time.sleep(0.3)
        # Never contacted with an empty login.
        assert site.opens[REC_B] == 0 and site.by_id[REC_B].spool.count() == 0
        site.by_id[REC_B].credential_ready.set()        # Setup repaired the login
        assert _wait_for(lambda: site.by_id[REC_B].spool.count() >= 1)
        assert {r["recorder_id"] for r in _rows(site.by_id[REC_B].spool)} == {REC_B}


def test_each_recorder_samples_by_its_own_camera_choices(monkeypatch, tmp_path, fast):
    # A (continuity) keeps its choices in watchlog.ini: channel 2 ignored. B keeps its own in
    # its recorder folder: channel 1 ignored. Neither recorder's choice applies to the other.
    a = _prepared(tmp_path, "A", REC_A, continuity=True,
                  profiles=[{"channel": "2", "monitored": False}])
    b = _prepared(tmp_path, "B", REC_B, profiles=[{"channel": "2", "monitored": False}])
    _write_recorder_profiles(b, [{"channel": "1", "monitored": False},
                                 {"channel": "2", "monitored": True}])
    drivers = {REC_A: _Driver(JPEG_A, channels=("1", "2")),
               REC_B: _Driver(JPEG_B, channels=("1", "2"))}
    with _Site(monkeypatch, [a, b], drivers):
        assert _wait_for(lambda: "1" in drivers[REC_A].calls and "2" in drivers[REC_B].calls)
        time.sleep(2.5)                                  # past one more cadence
    assert set(drivers[REC_A].calls) == {"1"}, drivers[REC_A].calls
    assert set(drivers[REC_B].calls) == {"2"}, drivers[REC_B].calls


def test_recorder_profiles_fall_back_to_every_camera_without_its_own_file(tmp_path):
    item = _prepared(tmp_path, "B", REC_B, profiles=[{"channel": "1", "monitored": False}])
    cfg = item.context.config
    # The ini list belongs to the continuity recorder only.
    assert ps.recorder_camera_profiles(cfg, continuity_owner=True) == cfg.camera_profiles
    assert ps.recorder_camera_profiles(cfg, continuity_owner=False) == []
    _write_recorder_profiles(item, [{"channel": "3", "monitored": False}])
    assert ps.recorder_camera_profiles(cfg, continuity_owner=False) == [
        {"channel": "3", "monitored": False}]
    # Another recorder's file is not this recorder's choices.
    path = Path(cfg.recorder_state_dir) / ps.RECORDER_CAMERA_PROFILES_NAME
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["local_id"] = "someone-else"
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert ps.recorder_camera_profiles(cfg, continuity_owner=False) == []


def _run_loop(monkeypatch, recorders, *, once):
    started = []
    ready = threading.Event()

    def worker(cfg, spool, stop, channels, **kwargs):
        started.append({"rid": cfg.recorder_cloud_id, "spool": spool, "channels": channels,
                        "thread": threading.current_thread().name, "kwargs": kwargs})
        if len(started) == len(recorders):
            ready.set()

    def idle(*_args, **_kwargs):
        return None

    sleeps = {"n": 0}

    def sleep(_seconds):
        sleeps["n"] += 1
        ready.wait(5)
        if sleeps["n"] >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(ps, "periodic_still_worker", worker)
    for name in ("collector", "recovery_worker", "health_worker", "command_worker",
                 "health_cycle"):
        monkeypatch.setattr(core, name, idle)
    monkeypatch.setattr(core, "upload_once", lambda *a, **k: 0)
    monkeypatch.setattr(core, "heartbeat", lambda *a, **k: None)
    monkeypatch.setattr(core, "update_runtime_health", lambda **_k: None)
    monkeypatch.setattr(core, "ONCE_COLLECT_SECONDS", 0)
    monkeypatch.setattr(fanout.time, "sleep", sleep)
    fanout.run(SimpleNamespace(recovery_enabled=False, upload_seconds=15, heartbeat_seconds=0),
               dict(STATE), object(), once=once, prepared_recorders=recorders, detector=None,
               analytics_worker=idle, archive_worker=idle)
    return started


def test_the_fanout_run_loop_starts_one_still_producer_per_recorder(monkeypatch, tmp_path, fast):
    a = _prepared(tmp_path, "A", REC_A, continuity=True)
    b = _prepared(tmp_path, "B", REC_B)
    started = _run_loop(monkeypatch, [a, b], once=False)
    assert sorted(s["rid"] for s in started) == [REC_A, REC_B]
    for entry in started:
        assert entry["thread"] == f"periodic-stills-{entry['rid'][:8]}"
        assert entry["channels"] and entry["channels"][0]["channel"] == "1"
        assert entry["kwargs"]["label"] in ("A", "B")
    assert started[0]["spool"] is not started[1]["spool"]


def test_the_fanout_once_mode_takes_no_periodic_stills(monkeypatch, tmp_path, fast):
    a = _prepared(tmp_path, "A", REC_A, continuity=True)
    b = _prepared(tmp_path, "B", REC_B)
    assert _run_loop(monkeypatch, [a, b], once=True) == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))


def test_hikvision_recorders_take_stills_on_their_live_stream_not_a_second_session(
        monkeypatch, tmp_path, fast):
    # Field Build 69 (DS-7608NI-Q1): a still session beside the open alertStream made the
    # recorder refuse logins. Each Hikvision recorder's worker stands down without opening a
    # session, and its own camera choices go to that recorder's live-stream sampler.
    a = _prepared(tmp_path, "A", REC_A, continuity=True,
                  profiles=[{"channel": "2", "monitored": False}])
    b = _prepared(tmp_path, "B", REC_B)
    _write_recorder_profiles(b, [{"channel": "1", "monitored": False}])
    for item in (a, b):
        item.context.config.nvr_driver = "hikvision-isapi"
    drivers = {REC_A: _Driver(JPEG_A), REC_B: _Driver(JPEG_B)}
    with _Site(monkeypatch, [a, b], drivers) as site:
        for unit in site.units:
            unit.stills.join(timeout=5)
            assert not unit.stills.is_alive()
        assert site.opens == {REC_A: 0, REC_B: 0}
    assert a.context.holder["still_profiles"] == [{"channel": "2", "monitored": False}]
    assert b.context.holder["still_profiles"] == [{"channel": "1", "monitored": False}]
    assert sum("stills come from the live event stream" in line for line in fast) == 2
