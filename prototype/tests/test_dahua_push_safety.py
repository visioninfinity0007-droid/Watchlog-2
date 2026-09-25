#!/usr/bin/env python3
"""Safety contract: generic Dahua AlarmServer must never be repointed to WatchLog HTTP."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "agent"
sys.path.insert(0, str(AGENT))

from drivers.dahua import DahuaDriver  # noqa: E402


class FakeDahua(DahuaDriver):
    def __init__(self):
        self.calls = []
        self.last_activity_monotonic = 0.0
    def _get(self, path: str, **kw):
        self.calls.append(path)
        if "getConfig&name=AlarmServer" in path:
            return (
                "table.AlarmServer.Enable=true\n"
                "table.AlarmServer.Address=10.0.0.9\n"
                "table.AlarmServer.Port=37777\n"
                "table.AlarmServer.Protocol=DAHUA\n"
            )
        raise AssertionError(f"unexpected recorder mutation/query: {path}")


def test_generic_dahua_push_is_read_only_and_unsupported():
    d = FakeDahua()
    out = d.configure_push("https://watchlog-push.example/push/abc123")
    assert out["applied"] is False
    assert out["verified"] is False
    assert "proprietary alarm-centre protocol" in out["detail"]
    assert d.calls == ["/cgi-bin/configManager.cgi?action=getConfig&name=AlarmServer"]
    assert all("setConfig" not in call for call in d.calls)


if __name__ == "__main__":
    test_generic_dahua_push_is_read_only_and_unsupported()
    print("OK: generic Dahua AlarmServer remains untouched")
