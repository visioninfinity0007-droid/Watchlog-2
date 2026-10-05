#!/usr/bin/env python3
"""Health and recovery pick up a repaired recorder login without a restart (MNVR-012).

Only the live collector reloaded the recorder credential when Setup rewrote it, and only
while it was waiting to reconnect. The health and recovery workers kept the login they
were started with, so after a password change they went on failing (and reporting an
authentication fault) until the Agent restarted. Both now reload the credential whenever
its generation changes.
"""
from __future__ import annotations

from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import watchlog_agent as core  # noqa: E402
from drivers.base import DriverError  # noqa: E402

A = "aaaaaaaa-0000-4000-8000-000000000001"


class Cloud:
    def call(self, name, **kw):
        return {}


@pytest.fixture
def rotated(monkeypatch):
    """Setup has replaced the recorder login since the worker loaded it."""
    state = {"generation": "g2", "reloads": 0}

    def reload(cfg):
        state["reloads"] += 1
        cfg.nvr_username, cfg.nvr_password = "new-user", "new-pw"

    monkeypatch.setattr(core, "_credential_generation_for_cfg", lambda _cfg: state["generation"])
    monkeypatch.setattr(core, "_reload_credential_for_cfg", reload)
    monkeypatch.setattr(core, "log", lambda _m: None)
    return state


def _cfg(**extra):
    cfg = SimpleNamespace(
        nvr_driver="hikvision-isapi", nvr_url="http://recorder.invalid",
        nvr_username="old-user", nvr_password="old-pw", recorder_local_id="local-a",
        credential_generation_seen="g1", recorder_cloud_id=None,
        health_batch=4, health_concurrency=1)
    cfg.__dict__.update(extra)
    return cfg


def test_health_cycle_uses_a_login_replaced_after_start(monkeypatch, rotated):
    used = []

    def build(name, url, user, password, *a, **k):
        used.append((user, password))
        raise DriverError("recorder did not answer")

    monkeypatch.setattr(core, "build", build)
    cfg = _cfg()
    core.health_cycle(Cloud(), {"agent_id": "agent", "agent_key": "key"}, cfg, {})
    assert used == [("new-user", "new-pw")]
    assert cfg.credential_generation_seen == "g2"

    core.health_cycle(Cloud(), {"agent_id": "agent", "agent_key": "key"}, cfg, {})
    assert rotated["reloads"] == 1, "an unchanged credential is not decrypted again"


def test_an_unreadable_new_login_keeps_the_old_one_and_retries(monkeypatch, rotated):
    used = []

    def broken(_cfg):
        raise RuntimeError("credential unreadable")

    monkeypatch.setattr(core, "_reload_credential_for_cfg", broken)
    monkeypatch.setattr(core, "build", lambda name, url, user, pw, *a, **k: (
        used.append(user) or (_ for _ in ()).throw(DriverError("down"))))
    cfg = _cfg()
    core.health_cycle(Cloud(), {"agent_id": "agent", "agent_key": "key"}, cfg, {})
    assert used == ["old-user"]
    assert cfg.credential_generation_seen == "g1", "retried on the next cycle"


def test_recovery_worker_uses_a_login_replaced_after_start(monkeypatch, rotated):
    stop = threading.Event()
    used = []

    def open_archive(cfg):
        used.append(cfg.nvr_password)
        stop.set()
        raise DriverError("recorder did not answer")

    monkeypatch.setattr(core, "open_archive_driver", open_archive)
    spool = SimpleNamespace(pending_recovery_gap=lambda: None, count=lambda: 0)
    cfg = _cfg(recorder_cloud_id=A, recovery_enabled=True, recovery_seconds=0.01,
               recovery_ai_enabled=False, last_live_path=Path("unused-last-live.json"),
               recovery_threshold_seconds=180)
    channels = [{"channel": "1", "camera_id": "cam-1"}]
    core.recovery_worker(cfg, {"agent_id": "agent", "agent_key": "key"}, Cloud(), stop,
                         spool, channels, {})
    assert used == ["new-pw"]


def test_the_adopted_single_recorder_config_keeps_its_login_generation():
    """The singleton loop runs on the process config: it must carry the generation its
    registry login was loaded at, or a rewrite before the first health cycle is missed."""
    import analytics_agent

    bound = SimpleNamespace(
        nvr_url="http://recorder.invalid", nvr_driver="onvif", nvr_username="u",
        nvr_password="p", recorder_local_id="local-a", recorder_cloud_id=A,
        recorder_display_name="A", recorder_state_dir=Path("state"),
        spool_path=Path("spool"), health_store_path=Path("health"),
        last_live_path=Path("last_live"), credential_generation_seen="g-registry")
    prepared = SimpleNamespace(context=SimpleNamespace(config=bound), channels=[],
                               camera_mapping=None)
    process_cfg = SimpleNamespace()
    analytics_agent._adopt_single_recorder(process_cfg, prepared)
    assert process_cfg.credential_generation_seen == "g-registry"


def test_a_legacy_site_keeps_its_login_when_the_new_one_cannot_be_read(monkeypatch):
    """A recorder-less (5.0.x) config reloads through Config.load_recorder_credential, which
    raises SystemExit on an unreadable store: that must not stop health or recovery."""
    generations = iter(["g1", "g2", "g2"])
    monkeypatch.setattr(core, "_credential_generation_for_cfg", lambda _cfg: next(generations))
    monkeypatch.setattr(core, "log", lambda _m: None)
    readable = {"ok": False}

    class LegacyCfg:
        nvr_username, nvr_password = "old-user", "old-pw"

        def load_recorder_credential(self):
            if not readable["ok"]:
                raise SystemExit("FATAL: the recorder credential could not be read")
            self.nvr_username, self.nvr_password = "new-user", "new-pw"

    cfg = LegacyCfg()
    assert core._reload_credential_if_changed(cfg) is False      # baseline g1
    assert core._reload_credential_if_changed(cfg) is False      # g2 unreadable: kept
    assert (cfg.nvr_username, cfg.nvr_password) == ("old-user", "old-pw")
    assert cfg.credential_generation_seen == "g1", "retried next cycle"
    readable["ok"] = True
    assert core._reload_credential_if_changed(cfg) is True
    assert (cfg.nvr_username, cfg.nvr_password) == ("new-user", "new-pw")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
