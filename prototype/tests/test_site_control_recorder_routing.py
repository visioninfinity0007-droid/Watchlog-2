#!/usr/bin/env python3
"""Site Control on the recorder-routed command path (U-2, MNVR-069).

U-2 (fixed in 5.0.28 for the singleton): build(cfg.nvr_driver) raised KeyError for
'auto' after the command was claimed, leaving it claimed until it expired. These tests
hold that on the multi-recorder path, end to end through the real recorders.json and
recorder_runtime.config_for_cloud_recorder (no routing stub):

  * a command naming a recorder whose registry driver is 'auto' autodetects THAT
    recorder (its own address and login), never the legacy or a sibling recorder;
  * an autodetect failure, an unknown recorder id, or a recorder-less command on a
    multi-recorder registry still completes the claimed command as failed, with no
    recorder address or secret in the cloud-visible error;
  * MNVR-069: a read action that only the 0063 catalog advertises (production before
    the recorder catalog) is completed as failed/unsupported_read_action, never left
    claimed and never touched on the recorder.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))

import credential_store as cs  # noqa: E402
import recorder_registry as rr  # noqa: E402
import site_control  # noqa: E402
import watchlog_agent as core  # noqa: E402
import windows_secret as ws  # noqa: E402
from drivers.base import DriverError  # noqa: E402

REC_A = "11111111-1111-1111-1111-111111111111"
REC_B = "22222222-2222-2222-2222-222222222222"
LOCAL_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
LOCAL_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def _registry(monkeypatch, tmp_path):
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))

    def wjs(path, obj):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("JSON:" + json.dumps(obj), encoding="utf-8")

    def rjs(path):
        text = Path(path).read_text(encoding="utf-8")
        if not text.startswith("JSON:"):
            raise ws.SecretError("corrupt")
        return json.loads(text[5:])

    monkeypatch.setattr(cs, "write_json_secret", wjs)
    monkeypatch.setattr(cs, "read_json_secret", rjs)
    cs.save_recorder_credential(LOCAL_A, "user-a", "pw-a")
    cs.save_recorder_credential(LOCAL_B, "user-b", "secret-b")
    rr.save_registry({"schema": rr.REGISTRY_SCHEMA, "recorders": [
        {"local_id": LOCAL_A, "cloud_recorder_id": REC_A, "display_name": "Front",
         "url": "http://192.0.2.10", "driver": "dahua-cgi", "is_primary": True,
         "is_configured": True},
        {"local_id": LOCAL_B, "cloud_recorder_id": REC_B, "display_name": "Back",
         "url": "http://192.0.2.20", "driver": "auto", "is_primary": False,
         "is_configured": True},
    ]})


def _base_cfg(tmp_path):
    root = tmp_path / "WatchLog"
    return SimpleNamespace(
        recorder_cloud_id=None, recorder_local_id=None, recorder_backend_absent=False,
        nvr_driver="auto", nvr_url="http://192.0.2.99", nvr_username="legacy",
        nvr_password="legacy-pw", site_control_enabled=True, site_control_seconds=0.01,
        state_path=root / "agent_state.json", spool_path=root / "spool.sqlite",
        health_store_path=root / "health.sqlite", last_live_path=root / "last_live.json",
    )


class _Cloud:
    def __init__(self, stop, commands):
        self.stop = stop
        self.commands = list(commands)
        self.completed = []

    def call(self, name, **kwargs):
        if name == "wl_agent_claim_command":
            if not self.commands:
                self.stop.set()
                return {"enabled": True, "authority": True, "command": None}
            return {"enabled": True, "authority": True, "command": self.commands.pop(0)}
        if name == "wl_agent_complete_command":
            self.completed.append(kwargs)
            return {"ok": True}
        raise AssertionError(name)


def _cmd(cid, recorder_id, action="get_channels"):
    return {"id": cid, "recorder_id": recorder_id, "action": action,
            "params": {}, "tier": "read"}


def _work(cfg, commands):
    stop = threading.Event()
    cloud = _Cloud(stop, commands)
    worker = threading.Thread(target=core.command_worker,
                              args=(cfg, {"agent_id": "a", "agent_key": "k"}, cloud, stop),
                              daemon=True)
    worker.start()
    worker.join(timeout=15)
    assert not worker.is_alive(), "command_worker did not finish"
    return cloud.completed


class _Driver:
    def list_channels(self):
        return []

    def close(self):
        pass


def test_auto_driver_autodetects_the_routed_recorder(monkeypatch, tmp_path):
    _registry(monkeypatch, tmp_path)
    monkeypatch.setattr(core, "update_runtime_health", lambda **_k: None)
    detected = []
    monkeypatch.setattr(core, "autodetect", lambda url, user, pw, **_k: (
        detected.append((url, user, pw)) or (_Driver(), None)))
    monkeypatch.setattr(core, "build", lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("'auto' must never reach build()")))

    done = _work(_base_cfg(tmp_path), [_cmd("cmd-b", REC_B)])

    assert detected == [("http://192.0.2.20", "user-b", "secret-b")]
    assert [(c["p_command_id"], c["p_status"]) for c in done] == [("cmd-b", "succeeded")]


def test_named_driver_builds_the_routed_recorder(monkeypatch, tmp_path):
    _registry(monkeypatch, tmp_path)
    monkeypatch.setattr(core, "update_runtime_health", lambda **_k: None)
    built = []
    monkeypatch.setattr(core, "build", lambda name, url, user, pw: (
        built.append((name, url, user)) or _Driver()))
    monkeypatch.setattr(core, "autodetect", lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("a named driver is built, not autodetected")))

    done = _work(_base_cfg(tmp_path), [_cmd("cmd-a", REC_A)])

    assert built == [("dahua-cgi", "http://192.0.2.10", "user-a")]
    assert done[0]["p_status"] == "succeeded"


def test_routed_failures_complete_the_command_and_never_leak(monkeypatch, tmp_path):
    _registry(monkeypatch, tmp_path)
    monkeypatch.setattr(core, "update_runtime_health", lambda **_k: None)

    def no_device(url, user, pw, **_k):
        raise DriverError(f"no driver recognised the device at http://{user}:{pw}@192.0.2.20/")

    monkeypatch.setattr(core, "autodetect", no_device)

    done = _work(_base_cfg(tmp_path), [
        _cmd("cmd-auto-fails", REC_B),
        _cmd("cmd-unknown", "33333333-3333-3333-3333-333333333333"),
        _cmd("cmd-no-target", None),
    ])

    assert [c["p_command_id"] for c in done] == ["cmd-auto-fails", "cmd-unknown",
                                                "cmd-no-target"]
    for c in done:
        assert c["p_status"] == "failed", c
        assert c["p_error"], c
        assert "192.0.2" not in str(c["p_error"]) and "secret-b" not in str(c["p_error"])


def test_legacy_catalog_only_reads_complete_as_unsupported(monkeypatch, tmp_path):
    """0063 (production before the recorder catalog) still accepts these two reads."""
    _registry(monkeypatch, tmp_path)
    monkeypatch.setattr(core, "update_runtime_health", lambda **_k: None)
    touched = []

    class Recorder(_Driver):
        def __getattr__(self, name):
            touched.append(name)
            raise AssertionError(f"recorder call {name} for an unsupported read")

    monkeypatch.setattr(core, "build", lambda *_a, **_k: Recorder())

    done = _work(_base_cfg(tmp_path), [
        _cmd("cmd-drift", REC_A, "get_configuration_drift"),
        _cmd("cmd-caps", REC_A, "get_recorder_capabilities"),
    ])

    assert [(c["p_command_id"], c["p_status"], c["p_error"]) for c in done] == [
        ("cmd-drift", "failed", "unsupported_read_action"),
        ("cmd-caps", "failed", "unsupported_read_action"),
    ]
    assert touched == []
    assert not {"get_configuration_drift", "get_recorder_capabilities"} & set(
        site_control.READ_ACTIONS)
