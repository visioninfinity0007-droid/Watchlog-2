#!/usr/bin/env python3
"""A finished Site Control command's result is never lost to a stale connection.

Field (Al-Khalid, 5.1.3, 2026-10-08): the full acceptance test ran for about three minutes, then
wl_agent_complete_command died with "Connection aborted ... ConnectionResetError 10054" on a
pooled keep-alive connection the server had closed while idle. The request never reached the
database, the command stayed 'claimed' and the run's result was lost.
"""
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

import agent_core  # noqa: E402
import watchlog_agent  # noqa: E402

RESET = requests.ConnectionError(
    "('Connection aborted.', ConnectionResetError(10054, 'An existing connection was "
    "forcibly closed by the remote host', None, 10054, None))")


class _Resp:
    status_code = 200
    text = '{"ok": true}'

    def json(self):
        return {"ok": True}


class _Session:
    def __init__(self, failures):
        self.failures = list(failures)
        self.posts = 0
        self.closed = 0
        self.headers = {}

    def post(self, url, json=None, timeout=None):
        self.posts += 1
        if self.failures:
            raise self.failures.pop(0)
        return _Resp()

    def close(self):
        self.closed += 1


def _cloud(failures):
    cloud = agent_core.Cloud("https://example.supabase.co", "sb_publishable_test")
    cloud.s = _Session(failures)
    return cloud


def test_a_stale_keepalive_connection_is_resent_once_on_a_fresh_one():
    cloud = _cloud([RESET])
    assert cloud.call("wl_agent_complete_command") == {"ok": True}
    assert cloud.s.posts == 2 and cloud.s.closed == 1


def test_a_timeout_is_never_resent_by_the_client():
    cloud = _cloud([requests.ReadTimeout("read timed out")])
    with pytest.raises(requests.ReadTimeout):
        cloud.call("wl_ingest_events")
    assert cloud.s.posts == 1


def test_two_resets_in_a_row_surface_to_the_caller():
    cloud = _cloud([RESET, RESET])
    with pytest.raises(requests.ConnectionError):
        cloud.call("wl_heartbeat")
    assert cloud.s.posts == 2


class _FlakyCloud:
    def __init__(self, failures):
        self.failures = list(failures)
        self.calls = []

    def call(self, fn, **params):
        self.calls.append((fn, params))
        if self.failures:
            raise self.failures.pop(0)
        return {"ok": True}


STATE = {"agent_id": "a", "agent_key": "k"}


def test_completion_is_retried_until_it_reaches_the_cloud():
    cloud, waits = _FlakyCloud([RESET, requests.ConnectionError("down")]), []
    watchlog_agent._complete_command(cloud, STATE, "cmd-1", "succeeded", {"summary": {}}, None,
                                     _sleep=waits.append)
    assert len(cloud.calls) == 3 and waits == list(watchlog_agent.COMPLETE_RETRY_WAITS[:2])
    fn, params = cloud.calls[-1]
    assert fn == "wl_agent_complete_command" and params["p_result"] == {"summary": {}}


def test_completion_gives_up_after_the_last_wait():
    cloud = _FlakyCloud([RESET] * 10)
    with pytest.raises(requests.ConnectionError):
        watchlog_agent._complete_command(cloud, STATE, "cmd-1", "failed", None, "x",
                                         _sleep=lambda _s: None)
    assert len(cloud.calls) == len(watchlog_agent.COMPLETE_RETRY_WAITS) + 1


def test_a_server_answer_is_never_retried():
    cloud = _FlakyCloud([agent_core.CloudError("wl_agent_complete_command", 400, "42501", "no")])
    with pytest.raises(agent_core.CloudError):
        watchlog_agent._complete_command(cloud, STATE, "cmd-1", "succeeded", {}, None,
                                         _sleep=lambda _s: None)
    assert len(cloud.calls) == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
