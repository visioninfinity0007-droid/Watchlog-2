#!/usr/bin/env python3
"""Safety contract: a generic Dahua AlarmServer is never repointed to WatchLog (field W2 925885a4).

Dahua's AlarmServer is a vendor alarm-centre protocol, not an HTTP webhook. Writing WatchLog's
HTTPS bridge into AlarmServer.Address/Port overwrote a customer's alarm-centre configuration
and delivered nothing. configure_push reads AlarmServer only and reports push unsupported.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from drivers.base import DriverError, NvrAuthFailed  # noqa: E402
from drivers.dahua import DahuaDriver  # noqa: E402

READ = "/cgi-bin/configManager.cgi?action=getConfig&name=AlarmServer"


class FakeDahua(DahuaDriver):
    def __init__(self, answer):
        self.calls = []
        self.answer = answer
        self.last_activity_monotonic = 0.0

    def _get(self, path: str, **kw):
        self.calls.append(path)
        if path != READ:
            raise AssertionError(f"unexpected recorder mutation/query: {path}")
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_generic_dahua_push_is_read_only_and_unsupported():
    d = FakeDahua("table.AlarmServer.Enable=true\ntable.AlarmServer.Address=10.0.0.9\n"
                  "table.AlarmServer.Port=37777\ntable.AlarmServer.Protocol=DAHUA\n")
    out = d.configure_push("https://watchlog-push.example/push/abc123")
    assert out == {"applied": False, "verified": False, "detail": out["detail"]}
    assert "proprietary alarm-centre protocol" in out["detail"] and "DAHUA" in out["detail"]
    assert d.calls == [READ]


def test_an_unreadable_alarm_server_is_still_left_untouched():
    d = FakeDahua(DriverError("HTTP 400"))
    out = d.configure_push("https://watchlog-push.example/push/abc123")
    assert out["applied"] is False and out["verified"] is False
    assert d.calls == [READ]


def test_a_rejected_login_still_surfaces_as_an_auth_fault():
    with pytest.raises(NvrAuthFailed):
        FakeDahua(NvrAuthFailed("rejected the password")).configure_push("https://x/push/t")
