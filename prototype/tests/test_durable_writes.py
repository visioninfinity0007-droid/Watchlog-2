#!/usr/bin/env python3
"""Secrets, the recorder registry and the Agent identity reach the disk before they replace the
previous file (credential audit R-fsync). Without fsync, a power cut right after os.replace can
leave a zero-length or partial file where a good one was, and the site loses its identity or
recorder logins. Site PCs lose power.
"""
from __future__ import annotations

import inspect
import os
import sys
import uuid
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))

import agent_core  # noqa: E402
import recorder_registry as rr  # noqa: E402
import windows_secret  # noqa: E402


def _fsync_before_replace(monkeypatch, module):
    events = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(module.os, "fsync", lambda fd: (events.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(module.os, "replace",
                        lambda a, b: (events.append("replace"), real_replace(a, b))[1])
    return events


def test_the_registry_is_on_disk_before_it_replaces_the_old_one(_isolated_programdata, monkeypatch):
    monkeypatch.setattr(rr, "registry_owner_trusted", lambda _p: True)
    events = _fsync_before_replace(monkeypatch, rr)
    rr.save_registry({"schema": rr.REGISTRY_SCHEMA, "recorders": [
        {"local_id": str(uuid.uuid4()), "display_name": "NVR", "is_primary": True,
         "continuity_owner": True}]})
    assert events[:2] == ["fsync", "replace"]


def test_the_identity_is_on_disk_before_it_replaces_the_old_one(tmp_path, monkeypatch):
    synced = []
    real = os.fsync
    monkeypatch.setattr(agent_core.os, "fsync", lambda fd: (synced.append(fd), real(fd))[1])
    monkeypatch.setattr(agent_core.os, "name", "posix")      # no DPAPI agent key here
    monkeypatch.setattr(agent_core.os, "chmod", lambda *_a: None)
    agent_core.save_state(tmp_path / "agent_state.json", {"agent_id": "a1", "site_id": "s1"})
    assert synced


def test_a_secret_is_on_disk_before_it_replaces_the_old_one():
    src = inspect.getsource(windows_secret.write_secret)
    assert src.index("os.fsync(") < src.index("os.replace(tmp, path)")


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
