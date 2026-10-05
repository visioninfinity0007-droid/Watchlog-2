#!/usr/bin/env python3
"""Incident still failures are worded as still failures, in customer language.

stills_worker reused the footage helper _safe_reason. Any error whose text was empty came back
as "Recorder could not provide this footage." on a STILL request, and any other driver or
library text without an address went to the customer verbatim (endpoint names, HTTP codes,
exception class names, "snapshot"). A still failure now always reads as a still failure: one
plain retryable sentence, or the unsupported sentence when the recorder affirmatively has no
still path. The technical detail stays in the agent log.
"""
from __future__ import annotations

import re
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import incident_evidence as ie  # noqa: E402
from drivers.base import DeviceInfo, DriverError, NvrAuthFailed, NvrDriver  # noqa: E402

CFG = SimpleNamespace(supabase_url="https://cloud.invalid", publishable_key="pk",
                      nvr_url="http://192.168.1.64")
STATE = {"agent_id": "agent-1", "agent_key": "key-1"}
ROW = {"request_id": "99999999-2222-3333-4444-555555555555", "channel": "4"}
# Words a customer-visible still reason must not carry (footage wording on a still, and the
# technical vocabulary of ai-harness/core/customer-vocabulary.yaml that driver text contains).
FORBIDDEN = re.compile(r"footage|snapshot|frame|endpoint|http|rpc|timeouterror|runtimeerror"
                       r"|driver|192\.168", re.IGNORECASE)


class FakeCloud:
    def __init__(self, stop):
        self.calls, self._stop, self._rows = [], stop, [dict(ROW)]

    def call(self, name, **kw):
        self.calls.append((name, kw))
        if name == "wl_agent_claim_incident_stills":
            if self._rows:
                return [self._rows.pop(0)]
            self._stop.set()
            return []
        return {"ok": True}


def failure_for(monkeypatch, error):
    class Broken(NvrDriver):
        name = "hikvision-isapi"

        def get_snapshot(self, channel):
            raise error

    stop = threading.Event()
    cloud = FakeCloud(stop)
    logged = []
    monkeypatch.setattr(ie.core, "Cloud", lambda url, key: cloud)
    monkeypatch.setattr(ie.core, "open_driver",
                        lambda cfg: (Broken("http://192.168.1.64"), DeviceInfo(vendor="Hikvision")))
    monkeypatch.setattr(ie.core, "log", logged.append)
    ie.stills_worker(CFG, STATE, stop)
    [failure] = [kw for name, kw in cloud.calls if name == "wl_agent_fail_incident_still"]
    return failure, logged


@pytest.mark.parametrize("error", [
    DriverError(""),
    TimeoutError(),
    RuntimeError("worker state lost"),
    DriverError("snapshot endpoint answered HTTP 500"),
    NvrAuthFailed("http://192.168.1.64/ISAPI/Streaming/channels/401/picture: HTTP 401"),
    requests.ConnectionError("HTTPConnectionPool(host='192.168.1.64', port=80): Read timed out."),
], ids=["empty", "timeout", "runtime", "endpoint-text", "auth", "connection"])
def test_a_failed_still_is_worded_as_a_still_and_stays_retryable(monkeypatch, error):
    failure, logged = failure_for(monkeypatch, error)
    reason = failure["p_reason"]
    assert failure["p_unsupported"] is False
    assert "still" in reason.lower()
    assert not FORBIDDEN.search(reason), reason
    assert any("incident stills: request" in line for line in logged)   # detail stays local


def test_an_affirmatively_unsupported_still_says_so(monkeypatch):
    failure, _logged = failure_for(monkeypatch, NotImplementedError("no still path"))
    assert failure["p_unsupported"] is True
    assert "still" in failure["p_reason"].lower()
    assert not FORBIDDEN.search(failure["p_reason"])


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
