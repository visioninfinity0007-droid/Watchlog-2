"""Site Control and recovery threads survive failures (MNVR-004 claimed-command part).

A claimed Site Control command must always be completed, as failed when recorder
routing, driver construction or the executor raises. The except handlers in
command_worker and recovery_worker must never raise themselves (the historical
NameError on nvr_health killed both threads for the rest of the process).
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

import recorder_runtime  # noqa: E402
import site_control  # noqa: E402
import watchlog_agent as core  # noqa: E402


def _cfg(**overrides):
    cfg = SimpleNamespace(
        recorder_cloud_id=None,
        recorder_local_id=None,
        nvr_driver="onvif",
        nvr_url="http://192.0.2.10",
        nvr_username="user",
        nvr_password="pw",
        site_control_enabled=True,
        site_control_seconds=0.01,
        recovery_enabled=True,
        recovery_seconds=0.01,
        recovery_ai_enabled=False,
        recovery_threshold_seconds=180,
        recovery_chunk_seconds=3600,
        recovery_throttle_seconds=0.0,
        recovery_live_backlog=500,
        recovery_ai_max_frames=4,
        recovery_snapshot_seconds=300,
        last_live_path=Path("unused-last-live.json"),
    )
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def test_nvr_health_is_a_module_global_of_the_agent():
    # Every except handler in command_worker/recovery_worker calls
    # nvr_health.redact(); it must resolve at module scope.
    assert "nvr_health" in vars(core)


class _CommandCloud:
    """Hands out N claimed commands, records completions, then stops the worker."""

    def __init__(self, stop, commands, fail_complete=0):
        self.stop = stop
        self.commands = list(commands)
        self.completed = []
        self.claims = 0
        self.fail_complete = fail_complete

    def call(self, name, **kwargs):
        if name == "wl_agent_claim_command":
            self.claims += 1
            if not self.commands:
                self.stop.set()
                return {"enabled": True, "authority": True, "command": None}
            return {"enabled": True, "authority": True, "command": self.commands.pop(0)}
        if name == "wl_agent_complete_command":
            self.completed.append(kwargs)
            if self.fail_complete:
                self.fail_complete -= 1
                raise core.CloudError(name, 503, None, "temporarily unavailable")
            return {"ok": True}
        raise AssertionError(name)


def _command(cid, recorder_id="11111111-1111-1111-1111-111111111111"):
    return {"id": cid, "recorder_id": recorder_id, "action": "get_channels",
            "params": {}, "tier": "read"}


def _run_command_worker(cfg, cloud, stop):
    worker = threading.Thread(
        target=core.command_worker,
        args=(cfg, {"agent_id": "agent", "agent_key": "key"}, cloud, stop),
        daemon=True,
    )
    worker.start()
    worker.join(timeout=10)
    assert not worker.is_alive(), "command_worker did not finish"


def test_routing_failure_completes_the_claimed_command_as_failed(monkeypatch):
    stop = threading.Event()
    monkeypatch.setattr(core, "update_runtime_health", lambda **_k: None)

    def unroutable(_cfg, _rid):
        raise ValueError(
            "cloud recorder target is not mapped to exactly one local recorder "
            "(http://admin:pw@192.0.2.10/)"
        )

    monkeypatch.setattr(recorder_runtime, "config_for_cloud_recorder", unroutable)
    monkeypatch.setattr(core, "build", lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("must not build a driver for an unroutable command")))

    cloud = _CommandCloud(stop, [_command("cmd-1"), _command("cmd-2")])
    _run_command_worker(_cfg(), cloud, stop)

    # Both claimed commands were completed (the thread survived the first
    # failure), each as failed, with no recorder address or secret in the
    # cloud-visible error.
    assert [c["p_command_id"] for c in cloud.completed] == ["cmd-1", "cmd-2"]
    assert all(c["p_status"] == "failed" for c in cloud.completed)
    for c in cloud.completed:
        assert c["p_result"] is None
        assert "192.0.2.10" not in str(c["p_error"])
        assert "pw" not in str(c["p_error"])
        assert c["p_error"]


def test_driver_build_failure_completes_the_claimed_command_as_failed(monkeypatch):
    stop = threading.Event()
    monkeypatch.setattr(core, "update_runtime_health", lambda **_k: None)
    cfg = _cfg(nvr_driver="not-a-driver")
    monkeypatch.setattr(recorder_runtime, "config_for_cloud_recorder", lambda c, _rid: c)

    def build_unknown(name, *_a, **_k):
        raise KeyError(name)  # an unregistered driver name ('auto' is resolved by autodetect)

    monkeypatch.setattr(core, "build", build_unknown)
    cloud = _CommandCloud(stop, [_command("cmd-auto")])
    _run_command_worker(cfg, cloud, stop)

    assert len(cloud.completed) == 1
    assert cloud.completed[0]["p_status"] == "failed"


def test_executor_crash_completes_as_failed_and_worker_keeps_polling(monkeypatch):
    stop = threading.Event()
    monkeypatch.setattr(core, "update_runtime_health", lambda **_k: None)
    monkeypatch.setattr(recorder_runtime, "config_for_cloud_recorder", lambda c, _rid: c)

    class Driver:
        closed = 0

        def close(self):
            Driver.closed += 1

    monkeypatch.setattr(core, "build", lambda *_a, **_k: Driver())

    def explode(*_a, **_k):
        raise RuntimeError("executor bug")

    monkeypatch.setattr(site_control, "execute_read", explode)
    # The first completion RPC also fails: the worker must log it and carry on.
    cloud = _CommandCloud(stop, [_command("cmd-a"), _command("cmd-b")], fail_complete=1)
    _run_command_worker(_cfg(), cloud, stop)

    assert [c["p_command_id"] for c in cloud.completed] == ["cmd-a", "cmd-b"]
    assert all(c["p_status"] == "failed" for c in cloud.completed)
    assert Driver.closed == 2
    assert cloud.claims == 3


def test_recovery_worker_survives_repeated_failures(monkeypatch):
    stop = threading.Event()
    attempts = []

    def failing_archive(_cfg):
        attempts.append(1)
        if len(attempts) >= 3:
            stop.set()
        raise core.DriverError("recorder at http://192.0.2.10 did not answer")

    monkeypatch.setattr(core, "open_archive_driver", failing_archive)

    class Spool:
        def pending_recovery_gap(self):
            return None

        def count(self):
            return 0

    class Cloud:
        # The recorder-less contract opens and reads intervals by camera UUID, so the
        # worker reaches the archive only once the camera mapping exists, and (5.0.28) only
        # for an interval it claimed; the unopenable archive hands the claim back.
        def call(self, name, **_kwargs):
            if name == "wl_sync_cameras":
                return {"1": "11111111-1111-4111-8111-111111111111"}
            if name == "wl_agent_claim_recovery":
                return [{"id": "iv-1", "started_at": "2026-10-04T10:00:00Z",
                         "ended_at": "2026-10-04T11:00:00Z",
                         "cameras": ["11111111-1111-4111-8111-111111111111"],
                         "status": "in_progress", "checkpoint": {}, "attempts": 1}]
            return []

    worker = threading.Thread(
        target=core.recovery_worker,
        args=(_cfg(), {"agent_id": "agent", "agent_key": "key"}, Cloud(), stop,
              Spool(), [{"channel": "1"}], {}),
        daemon=True,
    )
    worker.start()
    worker.join(timeout=10)
    assert not worker.is_alive(), "recovery_worker did not finish"
    assert len(attempts) == 3
