#!/usr/bin/env python3
"""A recorder registry from another site is never monitored or drained here (audit P8-a, G9-b).

site_runtime.json sets aside the continuity recorder's queue when the PC moves to another site.
The registry and the other recorders' data (recorders\\<local_id>: queue, health, camera choices)
and their logins (Secrets\\recorders) were not covered: the Agent trusted any administrator-owned
registry. The registry now carries the site it belongs to; the Agent stamps an unstamped one at
start and quarantines one stamped for another site, with its recorders' data and logins. Every
registry rewrite keeps the stamp.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))

import recorder_registry as rr  # noqa: E402


@pytest.fixture
def env(_isolated_programdata, monkeypatch):
    monkeypatch.setattr(rr, "registry_owner_trusted", lambda _path: True)
    root = Path(_isolated_programdata) / "WatchLog"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _rows(n=2):
    rows = []
    for i in range(n):
        rows.append({"local_id": str(uuid.uuid4()), "display_name": f"NVR {i + 1}",
                     "url": f"http://192.0.2.{10 + i}", "is_primary": i == 0,
                     "continuity_owner": i == 0, "cloud_recorder_id": str(uuid.uuid4())})
    return {"schema": rr.REGISTRY_SCHEMA, "recorders": rows}


def _seed_recorder_data(root: Path, registry: dict):
    secondary = registry["recorders"][1]["local_id"]
    data = root / "recorders" / secondary
    data.mkdir(parents=True)
    (data / "spool.sqlite").write_text("site A queued rows", encoding="utf-8")
    secrets = root / "Secrets" / "recorders"
    secrets.mkdir(parents=True)
    (secrets / f"{secondary}.dpapi").write_text("site A login", encoding="utf-8")
    return data, secrets


def test_an_unstamped_registry_is_adopted_by_this_site(env):
    rr.save_registry(_rows())
    assert rr.ensure_registry_belongs("site-A") == []
    assert json.loads(rr.registry_path().read_text(encoding="utf-8"))["site_id"] == "site-A"
    assert len(rr.recorders()) == 2


def test_the_stamp_survives_every_registry_rewrite(env):
    reg = _rows()
    rr.save_registry(reg)
    rr.ensure_registry_belongs("site-A")
    rr.rename_recorder(reg["recorders"][1]["local_id"], "Back office")
    assert json.loads(rr.registry_path().read_text(encoding="utf-8"))["site_id"] == "site-A"
    assert rr.load_registry()["site_id"] == "site-A"


def test_the_same_site_keeps_its_recorders(env):
    reg = _rows()
    rr.save_registry({**reg, "site_id": "site-A"})
    data, secrets = _seed_recorder_data(env, reg)
    assert rr.ensure_registry_belongs("site-A") == []
    assert rr.registry_path().exists() and (data / "spool.sqlite").exists()
    assert any(secrets.iterdir())


def test_another_sites_registry_queues_and_logins_are_set_aside_at_start(env):
    reg = _rows()
    rr.save_registry({**reg, "site_id": "site-A"})
    data, secrets = _seed_recorder_data(env, reg)
    logs = []
    moved = rr.ensure_registry_belongs("site-B", log=logs.append)
    assert len(moved) == 3 and all(p.exists() and ".quarantine-" in p.name for p in moved)
    assert not rr.registry_path().exists()
    assert not data.exists() and not secrets.exists()
    assert rr.recorders() == []                       # nothing of site A is monitored here
    kept = next(p for p in moved if p.name.startswith("recorders.quarantine-"))
    assert (kept / reg["recorders"][1]["local_id"] / "spool.sqlite").read_text(
        encoding="utf-8") == "site A queued rows"     # kept, never deleted
    assert logs and "another site" in logs[0]


def test_a_bad_stamp_is_rejected():
    with pytest.raises(ValueError):
        rr.validate_registry({**_rows(), "site_id": ""})


def test_the_agent_checks_the_registry_before_any_queue_is_opened():
    src = (AGENT / "watchlog_agent.py").read_text(encoding="utf-8")
    runtime = src.index("site_runtime.ensure_runtime_belongs(")
    registry = src.index("recorder_registry.ensure_registry_belongs(")
    assert runtime < registry < src.index('cloud.call("wl_sync_cameras"', runtime)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
