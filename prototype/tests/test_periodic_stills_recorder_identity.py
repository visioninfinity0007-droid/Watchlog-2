#!/usr/bin/env python3
"""Periodic stills on the multi-recorder Agent: recorder identity and recorder credential.

5.0.28's producer was written for the singleton Agent. On the multi-recorder Agent:
  * core._credential_generation takes the recorder's config (its own DPAPI blob for a registry
    recorder, the legacy singleton credential otherwise). The 5.0.28 producer called it with no
    argument, so the still thread died with a TypeError the moment it started, on every path;
  * a recorder bound to a cloud recorder (a one-recorder registry, or one recorder of a fan-out)
    stamps every event it spools with that recorder_id, so its stills must carry it too, or the
    server cannot tell two recorders' channel 1 apart;
  * a refused login waits for THIS recorder's credential to change and reloads that one.
The no-registry path keeps the 5.0.28 row exactly: no recorder_id key at all.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import programdata_sandbox  # noqa: E402,F401  (before any agent import: no writes to the real ProgramData)

import periodic_stills as ps  # noqa: E402
import watchlog_agent as core  # noqa: E402
from drivers.base import NvrAuthFailed  # noqa: E402
from test_periodic_stills import FakeDriver, ListSpool, SimClock, SimStop, _cfg  # noqa: E402

REC_B = "b0000000-0000-4000-8000-00000000000b"
CONTRACT_KEYS = {"channel", "event_type", "device_event_id", "device_ts", "agent_ts", "payload",
                 "snapshot_b64"}


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.delenv("WATCHLOG_PERIODIC_STILLS", raising=False)
    monkeypatch.delenv("WATCHLOG_PERIODIC_STILL_SECONDS", raising=False)
    monkeypatch.setattr(core, "log", lambda _m: None)


def _run(cfg, seconds, *, opener=None, clock=None, driver=None):
    clock = clock or SimClock()
    driver = driver or FakeDriver(clock)
    spool = ListSpool()
    ps.periodic_still_worker(cfg, spool, SimStop(clock, seconds), None,
                             open_driver=opener or (lambda _c: (driver, None)),
                             clock=clock, wall=clock.wall, rng=random.Random(3))
    return spool, driver


def _singleton_generation(monkeypatch):
    calls = []
    monkeypatch.setattr(core.credential_store, "credential_generation",
                        lambda: calls.append(1) or "legacy-gen")
    return calls


def test_the_no_registry_path_keeps_the_5028_row_exactly(monkeypatch):
    legacy = _singleton_generation(monkeypatch)
    spool, _driver = _run(_cfg(), 80)          # real core._credential_generation(cfg)
    assert spool.rows
    assert all(set(row) == CONTRACT_KEYS for row in spool.rows), spool.rows[0].keys()
    assert legacy, "the singleton credential's generation was never read"


def test_a_bound_recorder_stamps_its_recorder_id_on_every_still(monkeypatch):
    monkeypatch.setattr(core.credential_store, "recorder_credential_generation",
                        lambda local_id: f"gen-{local_id}")
    cfg = _cfg(recorder_cloud_id=REC_B, recorder_local_id="local-b")
    spool, _driver = _run(cfg, 80)
    assert spool.rows
    assert all(row["recorder_id"] == REC_B for row in spool.rows)
    assert all(set(row) == CONTRACT_KEYS | {"recorder_id"} for row in spool.rows)


def test_a_refused_login_waits_for_this_recorders_credential_and_reloads_it(monkeypatch):
    clock = SimClock()
    changed_at = clock.t + 120.0
    asked = []

    def recorder_generation(local_id):
        asked.append(local_id)
        return "gen-1" if clock.t >= changed_at else "gen-0"

    def singleton_generation():
        raise AssertionError("a registry recorder never reads the singleton credential")

    monkeypatch.setattr(core.credential_store, "recorder_credential_generation",
                        recorder_generation)
    monkeypatch.setattr(core.credential_store, "credential_generation", singleton_generation)
    monkeypatch.setattr(core.credential_store, "load_recorder_credential",
                        lambda local_id: {"username": "tech", "password": f"new-{local_id}"})
    driver = FakeDriver(clock)
    cfg = _cfg(recorder_cloud_id=REC_B, recorder_local_id="local-b",
               nvr_username="tech", nvr_password="old")
    attempts = []

    def opener(c):
        attempts.append(clock.t)
        if c.nvr_password != "new-local-b":
            raise NvrAuthFailed("HTTP 401 - recorder rejected the username or password")
        return driver, None

    spool, _driver = _run(cfg, 600, opener=opener, clock=clock, driver=driver)
    assert set(asked) == {"local-b"}
    assert cfg.nvr_password == "new-local-b"
    assert len(attempts) == 2 and attempts[1] <= changed_at + 6, attempts
    assert spool.rows and all(row["recorder_id"] == REC_B for row in spool.rows)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
