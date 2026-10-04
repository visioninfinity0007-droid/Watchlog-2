"""Single-recorder sites run every recorder-routed job (MNVR-002).

Since the recorder foundation migration every camera belongs to a recorder, and
the job-routing migration sends that recorder_id with every clip, still,
configuration snapshot, archive scan and Site Control command. These tests run
the real workers and the real resolver (nothing in recorder_runtime is
monkeypatched) for the two single-recorder shapes a 5.1 Agent meets:

  (a) no recorders.json: the 5.0.x singleton runtime must keep working;
  (b) one configured registry row that has never been bound: the recorder-aware
      startup binds it through wl_sync_recorders before any worker needs it.

A row that is already bound keeps monitoring on its saved identity when WatchLog
cannot be reached at start, and finishes the recorder check in the background.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

import analytics_agent  # noqa: E402
import credential_store as cs  # noqa: E402
import incident_evidence  # noqa: E402
import multi_recorder_orchestrator as mro  # noqa: E402
import recorder_registry as rr  # noqa: E402
import watchlog_agent as core  # noqa: E402
import windows_secret as ws  # noqa: E402
import pytest  # noqa: E402
import recorder_runtime  # noqa: E402
import requests  # noqa: E402
from drivers.base import Channel, DeviceInfo, Event  # noqa: E402
from spool import Spool  # noqa: E402

SITE_RECORDER = "5e1f0000-0000-4000-8000-000000000001"   # the site's only cloud recorder
OTHER_RECORDER = "5e1f0000-0000-4000-8000-000000000002"
STATE = {"agent_id": "agent", "agent_key": "key", "site_id": "site", "tenant_id": "tenant"}


class _Env:
    def __enter__(self):
        self.saved = os.environ.get("PROGRAMDATA")
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["PROGRAMDATA"] = self.tmp.name
        self.saved_crypto = (cs.write_json_secret, cs.read_json_secret)

        def wjs(path, obj):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text("JSON:" + json.dumps(obj), encoding="utf-8")

        def rjs(path):
            text = Path(path).read_text(encoding="utf-8")
            if not text.startswith("JSON:"):
                raise ws.SecretError("corrupt")
            return json.loads(text[5:])

        cs.write_json_secret, cs.read_json_secret = wjs, rjs
        self.root = Path(self.tmp.name) / "WatchLog"
        self.root.mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(self, *_args):
        cs.write_json_secret, cs.read_json_secret = self.saved_crypto
        if self.saved is None:
            os.environ.pop("PROGRAMDATA", None)
        else:
            os.environ["PROGRAMDATA"] = self.saved
        self.tmp.cleanup()


def _base_cfg(root: Path):
    """The process Config as watchlog.ini describes the legacy singleton."""
    return SimpleNamespace(
        supabase_url="https://example.invalid",
        publishable_key="test",
        state_path=root / "agent_state.json",
        spool_path=root / "spool.sqlite",
        spool_max_rows=1000,
        health_store_path=root / "health.sqlite",
        last_live_path=root / "last_live.json",
        nvr_url="http://192.0.2.10",
        nvr_driver="hikvision",
        nvr_username="legacy-user",
        nvr_password="legacy-pw",
        snapshots=False,
        site_control_enabled=True,
        site_control_seconds=0.01,
        analytics_enabled=False,
        analytics_config_path=root / "analytics_config.json",
        recovery_enabled=False,
        recovery_ai_max_frames=2,
        heartbeat_seconds=60,
        upload_seconds=15,
        health_seconds=300,
        health_batch=4,
        health_concurrency=2,
    )


class _Driver:
    name = "hikvision"
    verified_against_hardware = False

    def __init__(self, cfg):
        self.cfg = cfg
        self.closed = False

    def get_clip(self, channel, start, end):
        return b"clip-bytes"

    def get_snapshot(self, channel):
        return b"\xff\xd8jpeg"

    def list_channels(self):
        return [Channel("3", "Gate")]

    def capabilities(self):
        return {"channels": []}

    def enumerate_historical_events(self, channel, start, end, cursor=None, limit=100):
        return {"status": "supported",
                "events": [{"segment": {"start": "2026-10-02T12:00:00Z"}}],
                "next_cursor": None}

    def close(self):
        self.closed = True


class _JobCloud:
    """One claim of each job kind for the site's single recorder, plus the
    recorder-binding RPCs a contract-v4 database answers."""

    def __init__(self, stop=None):
        self.stop = stop
        self.calls = []
        self.clips = [{
            "request_id": "clip-1", "camera_id": "cam-3", "recorder_id": SITE_RECORDER,
            "channel": "3", "start_at": "2026-10-02T12:00:00Z",
            "end_at": "2026-10-02T12:00:30Z",
        }]
        self.stills = [{
            "request_id": "still-1", "camera_id": "cam-3", "recorder_id": SITE_RECORDER,
            "channel": "3", "occurred_at": "2026-10-02T12:00:00Z",
        }]
        self.commands = [{
            "id": "cmd-1", "recorder_id": SITE_RECORDER, "action": "get_channels",
            "params": {}, "tier": "read",
        }]

    def __call__(self, *_a, **_k):     # stands in for core.Cloud(url, key)
        return self

    def names(self):
        return [name for name, _ in self.calls]

    def _pop(self, queue):
        if queue:
            return [queue.pop(0)]
        if self.stop is not None:
            self.stop.set()
        return []

    def call(self, name, **kw):
        self.calls.append((name, kw))
        if name == "wl_multi_recorder_agent_contract":
            return {"ok": True, "version": mro.MULTI_RECORDER_CONTRACT_VERSION,
                    "configured_recorders": 1,
                    "features": sorted(mro.MULTI_RECORDER_REQUIRED_FEATURES)}
        if name == "wl_sync_recorders":
            # The site already owns one 'legacy-default' recorder; the Agent's
            # primary row adopts it.
            return {str(row["local_key"]): SITE_RECORDER for row in kw["p_recorders"]}
        if name == "wl_sync_recorder_cameras":
            assert kw["p_recorder_id"] == SITE_RECORDER
            return {str(c["channel"]): f"cam-{c['channel']}" for c in kw["p_cameras"]}
        if name == "wl_sync_recorder_capabilities":
            return {"ok": True}
        if name == "wl_agent_claim_clip_requests":
            return self._pop(self.clips)
        if name == "wl_agent_claim_incident_stills":
            return self._pop(self.stills)
        if name == "wl_agent_claim_command":
            cmd = self._pop(self.commands)
            return {"enabled": True, "authority": True, "command": cmd[0] if cmd else None}
        if name in ("wl_agent_upload_clip_chunk", "wl_agent_complete_clip",
                    "wl_agent_fail_clip", "wl_agent_upload_incident_still",
                    "wl_agent_fail_incident_still", "wl_agent_complete_command",
                    "wl_upload_config_snapshot", "wl_heartbeat", "wl_ingest_events"):
            return {"ok": True, "received": 0, "inserted": 0, "skipped": 0}
        raise AssertionError(f"unexpected RPC {name}")


def _patch_recorder_io(monkeypatch, opened):
    def open_driver(cfg):
        opened.append(cfg)
        return _Driver(cfg), DeviceInfo(vendor="Hikvision", model="DS-TEST", serial="SER-1",
                                        driver="hikvision")

    monkeypatch.setattr(core, "open_driver", open_driver)
    monkeypatch.setattr(core, "open_archive_driver", open_driver)
    monkeypatch.setattr(core, "build", lambda name, url, user, pw, *a, **k: (
        opened.append(SimpleNamespace(nvr_driver=name, nvr_url=url, nvr_username=user))
        or _Driver(None)))
    monkeypatch.setattr(core, "update_runtime_health", lambda **_k: None)


def _run_job_workers(monkeypatch, cfg):
    """Run each recorder-routed worker once against the site's recorder id."""
    stop = threading.Event()
    cloud = _JobCloud(stop)
    monkeypatch.setattr(core, "Cloud", cloud)

    incident_evidence.footage_worker(cfg, STATE, stop)
    stop.clear()
    incident_evidence.stills_worker(cfg, STATE, stop)
    stop.clear()
    core.command_worker(cfg, STATE, cloud, stop)

    snapshots = analytics_agent._service_snapshot_requests(
        cloud, STATE, cfg, None,
        [{"request_id": "snap-1", "camera_id": "cam-3", "recorder_id": SITE_RECORDER,
          "channel": "3"}],
    )

    monkeypatch.setattr(analytics_agent.analytics, "load_config", lambda _p: {"config": {
        "cameras": [{"id": "cam-3", "recorder_id": SITE_RECORDER, "channel": "3",
                     "rules": []}],
    }})
    import recovery_ai
    monkeypatch.setattr(recovery_ai, "recovered_frame", lambda *_a, **_k: b"frame")
    retrieve, _analyze = analytics_agent._archive_scan_handlers(cfg, None, threading.Event())
    frames = retrieve("cam-3", datetime(2026, 10, 2, 11, tzinfo=timezone.utc),
                      datetime(2026, 10, 2, 13, tzinfo=timezone.utc))
    return cloud, snapshots, frames


def _assert_every_job_succeeded(cloud, snapshots, frames):
    names = cloud.names()
    assert "wl_agent_complete_clip" in names and "wl_agent_fail_clip" not in names
    assert "wl_agent_upload_incident_still" in names
    assert "wl_agent_fail_incident_still" not in names
    completed = [kw for name, kw in cloud.calls if name == "wl_agent_complete_command"]
    assert [c["p_status"] for c in completed] == ["succeeded"]
    assert snapshots == 1
    assert frames and frames[0][0] == b"frame"


def test_no_registry_singleton_runs_every_recorder_routed_job(monkeypatch):
    with _Env() as env:
        assert not rr.registry_path().exists()
        cfg = _base_cfg(env.root)
        opened = []
        _patch_recorder_io(monkeypatch, opened)

        cloud, snapshots, frames = _run_job_workers(monkeypatch, cfg)

        _assert_every_job_succeeded(cloud, snapshots, frames)
        # Every job ran on the singleton recorder; no registry was invented.
        assert {getattr(c, "nvr_url", None) for c in opened} == {"http://192.0.2.10"}
        assert not rr.registry_path().exists()
        assert "wl_sync_recorders" not in cloud.names()


def _stage_one_unbound_row():
    local_id = str(uuid.uuid4())
    cs.save_recorder_credential(local_id, "registry-user", "registry-pw")
    rr.save_registry({
        "schema": rr.REGISTRY_SCHEMA,
        "recorders": [{
            "local_id": local_id, "display_name": "Primary Recorder",
            "url": "http://192.0.2.10", "driver": "hikvision",
            "is_primary": True, "continuity_owner": True, "is_configured": True,
        }],
    })
    return local_id


def _run_startup_once(monkeypatch, cfg, cloud):
    seen = {}

    def collector(run_cfg, spool, stop, holder=None):
        seen["collector_recorder_id"] = getattr(run_cfg, "recorder_cloud_id", None)
        seen["collector_holder_recorder_id"] = (holder or {}).get("recorder_cloud_id")

    def health_cycle(_cloud, _state, run_cfg, holder):
        seen["health_recorder_id"] = getattr(run_cfg, "recorder_cloud_id", None)
        seen["holder_channels"] = list(holder.get("synced_channels") or [])

    monkeypatch.setattr(core, "collector", collector)
    monkeypatch.setattr(core, "health_cycle", health_cycle)
    monkeypatch.setattr(core, "ONCE_COLLECT_SECONDS", 0.05)
    monkeypatch.setattr(core.vision, "build", lambda *_a, **_k: None)
    analytics_agent.enhanced_cmd_run(cfg, STATE, cloud, once=True)
    return seen


def test_one_unbound_registry_row_binds_at_startup_then_runs_every_job(monkeypatch):
    with _Env() as env:
        local_id = _stage_one_unbound_row()
        cfg = _base_cfg(env.root)
        cfg.nvr_url = "http://192.0.2.99"          # stale legacy ini address
        opened = []
        _patch_recorder_io(monkeypatch, opened)
        startup_cloud = _JobCloud()

        seen = _run_startup_once(monkeypatch, cfg, startup_cloud)

        # Recorder-aware startup: identity bound before any worker ran, through the
        # full-registry sync, and published on the shared process Config.
        assert "wl_sync_recorders" in startup_cloud.names()
        assert rr.recorder(local_id)["cloud_recorder_id"] == SITE_RECORDER
        assert cfg.recorder_cloud_id == SITE_RECORDER
        assert cfg.recorder_local_id == local_id
        # The registry is the authority for the recorder address and login.
        assert cfg.nvr_url == "http://192.0.2.10"
        assert (cfg.nvr_username, cfg.nvr_password) == ("registry-user", "registry-pw")
        assert seen["collector_recorder_id"] == SITE_RECORDER
        assert seen["collector_holder_recorder_id"] == SITE_RECORDER
        assert seen["health_recorder_id"] == SITE_RECORDER
        assert seen["holder_channels"] == [{"channel": "3", "name": "Gate", "camera_id": "cam-3"}]
        assert "wl_sync_recorder_cameras" in startup_cloud.names()

        # Every recorder-routed job now resolves to the bound process Config.
        opened.clear()
        cloud, snapshots, frames = _run_job_workers(monkeypatch, cfg)
        _assert_every_job_succeeded(cloud, snapshots, frames)
        assert {getattr(c, "nvr_url", None) for c in opened} == {"http://192.0.2.10"}


def test_one_unbound_row_job_before_startup_waits_then_routes(monkeypatch):
    """A clip claimed by the incident worker while startup is still binding."""
    with _Env() as env:
        local_id = _stage_one_unbound_row()
        cfg = _base_cfg(env.root)
        opened = []
        _patch_recorder_io(monkeypatch, opened)
        import recorder_runtime
        monkeypatch.setattr(recorder_runtime, "UNBOUND_BINDING_POLL_SECONDS", 0.01)

        binder = threading.Timer(0.2, lambda: rr.apply_cloud_mapping({local_id: SITE_RECORDER}))
        binder.start()
        try:
            stop = threading.Event()
            cloud = _JobCloud(stop)
            monkeypatch.setattr(core, "Cloud", cloud)
            incident_evidence.footage_worker(cfg, STATE, stop)
        finally:
            binder.cancel()
        assert "wl_agent_complete_clip" in cloud.names()
        assert "wl_agent_fail_clip" not in cloud.names()
        assert opened and opened[0].nvr_username == "registry-user"


def test_command_for_an_unknown_recorder_completes_as_failed(monkeypatch):
    with _Env() as env:
        local_id = _stage_one_unbound_row()
        rr.apply_cloud_mapping({local_id: SITE_RECORDER})
        cfg = _base_cfg(env.root)
        opened = []
        _patch_recorder_io(monkeypatch, opened)
        stop = threading.Event()
        cloud = _JobCloud(stop)
        cloud.commands[0]["recorder_id"] = OTHER_RECORDER

        core.command_worker(cfg, STATE, cloud, stop)

        completed = [kw for name, kw in cloud.calls if name == "wl_agent_complete_command"]
        assert [(c["p_command_id"], c["p_status"]) for c in completed] == [("cmd-1", "failed")]
        assert opened == []          # no recorder was guessed


class _PreRecorderDatabase(_JobCloud):
    """A database from before the multi-recorder foundation: no contract RPC."""

    def call(self, name, **kw):
        if name == "wl_multi_recorder_agent_contract":
            self.calls.append((name, kw))
            raise core.CloudError(name, 404, "PGRST202",
                                  "Could not find the function public.wl_multi_recorder_agent_contract")
        if name.startswith("wl_sync_recorder"):
            raise AssertionError(f"{name} does not exist on this database")
        return super().call(name, **kw)


def test_single_registry_on_a_database_without_recorders_runs_unbound(monkeypatch):
    """Such a database has no recorder concept and sends no recorder_id, so the
    one-recorder runtime is unambiguous: it keeps monitoring on the legacy RPCs,
    with the registry still the authority for address and login."""
    with _Env() as env:
        local_id = _stage_one_unbound_row()
        cfg = _base_cfg(env.root)
        cfg.nvr_url = "http://192.0.2.99"
        opened = []
        _patch_recorder_io(monkeypatch, opened)
        cloud = _PreRecorderDatabase()

        seen = _run_startup_once(monkeypatch, cfg, cloud)

        assert getattr(cfg, "recorder_cloud_id", None) is None
        assert seen["collector_recorder_id"] is None
        assert cfg.recorder_local_id == local_id
        assert cfg.nvr_url == "http://192.0.2.10"
        assert (cfg.nvr_username, cfg.nvr_password) == ("registry-user", "registry-pw")
        assert rr.recorder(local_id)["cloud_recorder_id"] is None
        assert "wl_heartbeat" in cloud.names()


def test_multi_registry_on_a_database_without_recorders_stops(monkeypatch):
    with _Env() as env:
        _stage_one_unbound_row()
        rr.add_recorder(display_name="Second", url="http://192.0.2.20", driver="dahua-cgi",
                        username="b", password="b-pw")
        opened = []
        _patch_recorder_io(monkeypatch, opened)
        try:
            _run_startup_once(monkeypatch, _base_cfg(env.root), _PreRecorderDatabase())
            assert False, "several recorders cannot run on a database without recorders"
        except SystemExit as exc:
            assert "preflight did not complete" in str(exc)


# --- an already-bound recorder when WatchLog cannot be reached at start -------

TRANSIENT = [
    pytest.param(lambda name: requests.ConnectionError("network not ready"), id="offline"),
    pytest.param(lambda name: core.CloudError(name, 503, None, "temporarily unavailable"),
                 id="5xx"),
    pytest.param(lambda name: core.CloudError(name, 403, "42501",
                                              "agent is not current site authority"),
                 id="standby"),
]


class _Unreachable:
    """Every call fails the way a network that is not ready, a WatchLog outage or
    a standby PC (not the site's current Agent) does."""

    def __init__(self, error):
        self.error = error
        self.calls = []

    def names(self):
        return [name for name, _ in self.calls]

    def call(self, name, **kw):
        self.calls.append((name, kw))
        raise self.error(name)


class _LiveDriver(_Driver):
    def stream_events(self, stop):
        yield Event("3", "motion", datetime(2026, 10, 2, 12, 5, tzinfo=timezone.utc),
                    payload={"source": "test"})
        stop.wait(5)


def _stage_one_bound_row():
    local_id = _stage_one_unbound_row()
    rr.apply_cloud_mapping({local_id: SITE_RECORDER})
    return local_id


@pytest.mark.parametrize("error", TRANSIENT)
def test_bound_row_keeps_monitoring_when_watchlog_cannot_be_reached(monkeypatch, error):
    """Boot with the network not ready (or a WatchLog outage, or a standby PC): the
    recorder's identity is already saved, so events are collected and queued
    under it instead of the Agent exiting before it monitors anything."""
    with _Env() as env:
        local_id = _stage_one_bound_row()
        cfg = _base_cfg(env.root)
        cfg.nvr_url = "http://192.0.2.99"          # stale legacy ini address
        cfg.site_control_enabled = False
        opened = []
        _patch_recorder_io(monkeypatch, opened)
        monkeypatch.setattr(core, "open_driver", lambda run_cfg: (
            opened.append(run_cfg) or _LiveDriver(run_cfg),
            DeviceInfo(vendor="Hikvision", model="DS-TEST", driver="hikvision")))
        monkeypatch.setattr(core, "health_cycle", lambda *a, **k: None)
        monkeypatch.setattr(core, "ONCE_COLLECT_SECONDS", 0.3)
        monkeypatch.setattr(core.vision, "build", lambda *_a, **_k: None)
        spools = []

        class _Tracked(Spool):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                spools.append(self)

        monkeypatch.setattr(analytics_agent, "Spool", _Tracked)
        cloud = _Unreachable(error)

        try:
            analytics_agent.enhanced_cmd_run(cfg, STATE, cloud, once=True)
        except (requests.RequestException, RuntimeError):
            pass      # a one-shot run still reports its failed upload or heartbeat
        try:
            _ids, queued = spools[0].take(10) if spools else ([], [])
        finally:
            for queue in spools:
                queue.close()

        assert cloud.names()[0] == "wl_multi_recorder_agent_contract"
        assert cfg.recorder_cloud_id == SITE_RECORDER
        assert cfg.recorder_local_id == local_id
        assert cfg.nvr_url == "http://192.0.2.10"
        assert (cfg.nvr_username, cfg.nvr_password) == ("registry-user", "registry-pw")
        assert opened and opened[0].nvr_url == "http://192.0.2.10"
        assert [e.get("recorder_id") for e in queued] == [SITE_RECORDER]
        assert rr.recorder(local_id)["cloud_recorder_id"] == SITE_RECORDER


def test_unbound_row_still_stops_when_watchlog_cannot_be_reached(monkeypatch):
    with _Env() as env:
        _stage_one_unbound_row()
        _patch_recorder_io(monkeypatch, [])
        offline = _Unreachable(lambda name: requests.ConnectionError("network not ready"))
        with pytest.raises(SystemExit, match="preflight did not complete"):
            _run_startup_once(monkeypatch, _base_cfg(env.root), offline)


def test_bound_row_still_stops_on_a_definitive_contract_mismatch(monkeypatch):
    class _OldContract(_JobCloud):
        def call(self, name, **kw):
            if name == "wl_multi_recorder_agent_contract":
                self.calls.append((name, kw))
                return {"ok": True, "version": mro.MULTI_RECORDER_CONTRACT_VERSION - 1,
                        "features": sorted(mro.MULTI_RECORDER_REQUIRED_FEATURES)}
            return super().call(name, **kw)

    with _Env() as env:
        _stage_one_bound_row()
        _patch_recorder_io(monkeypatch, [])
        with pytest.raises(SystemExit, match="preflight did not complete"):
            _run_startup_once(monkeypatch, _base_cfg(env.root), _OldContract())


class _ComesBack(_JobCloud):
    """Unreachable for the first ``outage`` calls, then a contract-v4 database."""

    def __init__(self, outage, recorder=SITE_RECORDER):
        super().__init__()
        self.outage = outage
        self.recorder = recorder

    def call(self, name, **kw):
        if self.outage > 0:
            self.outage -= 1
            self.calls.append((name, kw))
            raise requests.ConnectionError("network not ready")
        if name == "wl_sync_recorders":
            self.calls.append((name, kw))
            return {str(row["local_key"]): self.recorder for row in kw["p_recorders"]}
        return super().call(name, **kw)


def test_background_check_confirms_the_saved_identity_once_watchlog_answers(monkeypatch):
    with _Env() as env:
        local_id = _stage_one_bound_row()
        cfg = _base_cfg(env.root)
        logs = []
        monkeypatch.setattr(core, "log", lambda msg: logs.append(str(msg)))
        monkeypatch.setattr(analytics_agent, "RECORDER_RECHECK_SECONDS", (0.01,))
        cloud = _ComesBack(outage=2)
        restart = {}

        analytics_agent._retry_recorder_preflight(cfg, STATE, cloud, threading.Event(),
                                                  restart, "bind")

        assert restart == {}
        synced = [kw["p_recorders"] for name, kw in cloud.calls if name == "wl_sync_recorders"]
        assert [[r["local_key"] for r in rows] for rows in synced] == [[local_id]]
        assert rr.recorder(local_id)["cloud_recorder_id"] == SITE_RECORDER
        assert any("confirmed" in line for line in logs)


def test_background_check_restarts_when_watchlog_refuses_the_saved_identity(monkeypatch):
    with _Env() as env:
        local_id = _stage_one_bound_row()
        monkeypatch.setattr(analytics_agent, "RECORDER_RECHECK_SECONDS", (0.01,))
        cloud = _ComesBack(outage=1, recorder=OTHER_RECORDER)
        restart = {}

        analytics_agent._retry_recorder_preflight(_base_cfg(env.root), STATE, cloud,
                                                  threading.Event(), restart, "bind")

        assert "restart" in restart.get("reason", "")
        assert rr.recorder(local_id)["cloud_recorder_id"] == SITE_RECORDER


def test_run_loop_restarts_cleanly_when_the_background_check_asks(monkeypatch):
    """Run mode: started on the saved identity while offline; WatchLog then answers
    with a different recorder identity, so the run loop exits for a clean start
    (which fails closed) instead of running on an identity WatchLog rejects."""
    with _Env() as env:
        _stage_one_bound_row()
        cfg = _base_cfg(env.root)
        cfg.site_control_enabled = False
        _patch_recorder_io(monkeypatch, [])

        def idle(*_a, **_k):
            return None

        for name in ("collector", "recovery_worker", "health_worker", "command_worker"):
            monkeypatch.setattr(core, name, idle)
        monkeypatch.setattr(analytics_agent, "analytics_worker", idle)
        monkeypatch.setattr(analytics_agent, "archive_worker", idle)
        monkeypatch.setattr(core.vision, "build", lambda *_a, **_k: None)
        monkeypatch.setattr(analytics_agent, "RECORDER_RECHECK_SECONDS", (0.01,))
        real_sleep = time.sleep
        monkeypatch.setattr(analytics_agent.time, "sleep", lambda _s: real_sleep(0.01))
        # Startup's contract call fails; the background check then gets an answer.
        cloud = _ComesBack(outage=1, recorder=OTHER_RECORDER)

        with pytest.raises(SystemExit, match="restart"):
            analytics_agent.enhanced_cmd_run(cfg, STATE, cloud, once=False)

        assert cfg.recorder_cloud_id == SITE_RECORDER

