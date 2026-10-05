#!/usr/bin/env python3
"""The channel-keyed installer analytics profile is never sent on a multi-recorder site.

wl_agent_bootstrap_analytics (0025) updates cameras by site and channel only. Setup pre-writes
the "sent" marker on a multi-recorder first install for that reason, but a recorder added later
through Manage Recorders never wrote it, and _send_bootstrap_once never looked at the registry.
An Agent whose marker was still missing (analytics off at install, or the call kept failing)
then sent the continuity recorder's profiles and renamed and re-purposed every other
recorder's channel 1..N cameras. It now writes a "skipped: multi-recorder" marker instead.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "agent"))
sys.path.insert(0, str(TESTS))
import programdata_sandbox  # noqa: E402,F401  (before any agent import)

import analytics_agent  # noqa: E402

STATE = {"agent_id": "agent", "agent_key": "key", "site_id": "site-1"}


class Cloud:
    def __init__(self):
        self.calls = []

    def call(self, name, **kw):
        self.calls.append(name)
        return {"version": 1, "updated_cameras": 2}


def _cfg(tmp_path):
    return SimpleNamespace(
        analytics_bootstrap_marker=tmp_path / "analytics_bootstrap_sent.json",
        bootstrap_site_type="retail",
        bootstrap_camera_profiles=[{"channel": "1", "name": "Till", "purpose": "pos"}])


def _registry(monkeypatch, rows):
    monkeypatch.setattr(analytics_agent.recorder_registry, "recorders", lambda: list(rows))
    monkeypatch.setattr(analytics_agent.core, "log", lambda _m: None)


def test_a_second_recorder_stops_the_channel_keyed_profile(monkeypatch, tmp_path):
    _registry(monkeypatch, [{"local_id": "a", "is_configured": True},
                            {"local_id": "b", "is_configured": True}])
    cloud, cfg = Cloud(), _cfg(tmp_path)
    assert analytics_agent._send_bootstrap_once(cloud, STATE, cfg) is False
    assert cloud.calls == []
    marker = json.loads(cfg.analytics_bootstrap_marker.read_text(encoding="utf-8"))
    assert marker["skipped"] is True and "multi-recorder" in marker["reason"]


def test_a_disabled_second_recorder_still_counts(monkeypatch, tmp_path):
    # A disabled recorder's cameras stay in WatchLog and still share channel numbers.
    _registry(monkeypatch, [{"local_id": "a", "is_configured": True},
                            {"local_id": "b", "is_configured": False}])
    cloud = Cloud()
    assert analytics_agent._send_bootstrap_once(cloud, STATE, _cfg(tmp_path)) is False
    assert cloud.calls == []


def test_an_unreadable_registry_sends_nothing_and_asks_again(monkeypatch, tmp_path):
    def broken():
        raise ValueError("could not read recorder registry")
    monkeypatch.setattr(analytics_agent.recorder_registry, "recorders", broken)
    cloud, cfg = Cloud(), _cfg(tmp_path)
    assert analytics_agent._send_bootstrap_once(cloud, STATE, cfg) is False
    assert cloud.calls == [] and not cfg.analytics_bootstrap_marker.exists()


@pytest.mark.parametrize("rows", [[], [{"local_id": "a", "is_configured": True}]],
                         ids=["legacy", "single-recorder"])
def test_a_single_recorder_site_still_sends_it_once(monkeypatch, tmp_path, rows):
    _registry(monkeypatch, rows)
    cloud, cfg = Cloud(), _cfg(tmp_path)
    assert analytics_agent._send_bootstrap_once(cloud, STATE, cfg) is True
    assert cloud.calls == ["wl_agent_bootstrap_analytics"]
    assert analytics_agent._send_bootstrap_once(cloud, STATE, cfg) is False
    assert cloud.calls == ["wl_agent_bootstrap_analytics"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
