#!/usr/bin/env python3
"""A busy recorders.json during cloud binding is not a refusal of the recorder identities.

apply_cloud_mapping rewrote recorders.json with os.replace on every binding, even when nothing
changed, and nothing retried it. On Windows that raises PermissionError while any thread or
process has the file open (a site worker routing a job, Manage Recorders). _preflight_transient
did not know PermissionError, so the background recheck asked for a restart logging that
WatchLog "refused the saved recorder identity", and the startup bind exited FATAL. An unchanged
binding is no longer rewritten, a changed one is retried after short pauses, and a file that
stays busy is transient.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "agent"))
sys.path.insert(0, str(TESTS))

import analytics_agent  # noqa: E402
import multi_recorder_orchestrator as mro  # noqa: E402
import recorder_registry as rr  # noqa: E402
import recorder_runtime  # noqa: E402
from test_multi_recorder_cloud_binding import Env, FakeCloud, seed_two  # noqa: E402

STATE = {"agent_id": "agent", "agent_key": "key"}
IDS = ("aaaaaaaa-0000-4000-8000-000000000001", "bbbbbbbb-0000-4000-8000-000000000002")


def _busy(*_a, **_k):
    raise PermissionError(5, "Access is denied", "recorders.json")


def test_an_unchanged_binding_is_not_rewritten(monkeypatch):
    with Env() as env:
        a, b, _base = seed_two(env.root)
        rr.apply_cloud_mapping({a: IDS[0], b: IDS[1]})
        monkeypatch.setattr(rr, "save_registry", _busy)
        rr.apply_cloud_mapping({a: IDS[0], b: IDS[1]})      # nothing to save: no replace
        assert rr.recorder(a)["cloud_recorder_id"] == IDS[0]


def test_a_briefly_busy_registry_still_saves_a_new_binding(monkeypatch):
    with Env() as env:
        a, b, base = seed_two(env.root)
        real = rr.save_registry
        attempts = []

        def flaky(payload):
            attempts.append(1)
            if len(attempts) == 1:
                _busy()
            return real(payload)

        monkeypatch.setattr(rr, "save_registry", flaky)
        monkeypatch.setattr(mro, "_IDENTITY_WRITE_RETRY_SECONDS", (0.0, 0.0))
        mro.bind_cloud_identities(FakeCloud({a: IDS[0], b: IDS[1]}), STATE,
                                  recorder_runtime.load_contexts(base))
        assert rr.recorder(a)["cloud_recorder_id"] == IDS[0]
        assert rr.recorder(b)["cloud_recorder_id"] == IDS[1]


def test_a_registry_that_stays_busy_is_transient_not_a_refusal(monkeypatch):
    with Env() as env:
        a, b, base = seed_two(env.root)
        monkeypatch.setattr(rr, "save_registry", _busy)
        monkeypatch.setattr(mro, "_IDENTITY_WRITE_RETRY_SECONDS", (0.0, 0.0))
        monkeypatch.setattr(analytics_agent, "RECORDER_RECHECK_SECONDS", (0.0,))
        monkeypatch.setattr(analytics_agent.core, "log", lambda _m: None)
        stop, restart, checks = threading.Event(), {}, []
        real_contract = mro.require_cloud_contract

        def contract(cloud, state):
            checks.append(1)
            if len(checks) >= 3:
                stop.set()
            return real_contract(cloud, state)

        monkeypatch.setattr(mro, "require_cloud_contract", contract)
        analytics_agent._retry_recorder_preflight(
            base, STATE, FakeCloud({a: IDS[0], b: IDS[1]}), stop, restart, "bind")
        assert "reason" not in restart, restart.get("reason")
        assert len(checks) >= 2, "the recheck kept retrying"
        assert analytics_agent._preflight_transient(PermissionError(5, "Access is denied"))


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
