#!/usr/bin/env python3
"""Phase A — increment 3: AGENT-side recorder health assessment.

assess_nvr_health() drives the vendor-authenticated driver (probe + list_channels ONLY —
never the event stream) and produces the cloud report: recorder reachable vs unreachable,
auth ok vs rejected, and the raw channel set with enable flags. It must classify
unreachable vs auth-failure distinctly, degrade to "not enumerated" when it cannot look,
and NEVER put a credential, URL, or Authorization header into the payload.

Red before nvr_health.py exists.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "prototype" / "agent"))

import nvr_health  # noqa: E402
from nvr_health import assess_nvr_health  # noqa: E402
from drivers.base import (Channel, DeviceInfo, DriverError,  # noqa: E402
                          NvrAuthFailed, NvrUnreachable)

SECRET_PW = "sup3rSecretRecorderPw!"
BASE_URL = "http://192.168.1.108:80"


class FakeDriver:
    """Stand-in recorder. stream_events explodes if touched — health must never infer
    anything from event activity."""
    name = "dahua-cgi"

    def __init__(self, *, info=None, channels=None, probe_exc=None, channels_exc=None):
        self._info = info
        self._channels = channels or []
        self._probe_exc = probe_exc
        self._channels_exc = channels_exc
        self.username = "admin"
        self.password = SECRET_PW
        self.base_url = BASE_URL

    def probe(self):
        if self._probe_exc:
            raise self._probe_exc
        return self._info

    def list_channels(self):
        if self._channels_exc:
            raise self._channels_exc
        return self._channels

    def stream_events(self, stop):
        raise AssertionError("health assessment must not read the event stream")

    def get_snapshot(self, channel):
        raise AssertionError("increment 3 does not probe snapshots")


GOOD_INFO = DeviceInfo(vendor="Dahua", model="NVR5216", firmware="4.001",
                       serial="ABC123", channel_count=8, driver="dahua-cgi")
EIGHT = [Channel(channel=str(i), name=f"Cam {i}") for i in range(1, 9)]


def test_good_nvr_eight_present_channels():
    r = assess_nvr_health(FakeDriver(info=GOOD_INFO, channels=EIGHT))
    assert r["nvr"]["reachable"] is True and r["nvr"]["auth_ok"] is True
    assert r["nvr"]["state"] == "ok" and r["nvr"]["reason"] == "ok"
    assert r["nvr"]["vendor"] == "Dahua" and r["nvr"]["model"] == "NVR5216"
    assert r["channels"]["enumerated"] is True
    assert [c["channel"] for c in r["channels"]["reported"]] == [str(i) for i in range(1, 9)]


def test_unreachable_nvr():
    r = assess_nvr_health(FakeDriver(probe_exc=NvrUnreachable("no route")))
    assert r["nvr"]["reachable"] is False and r["nvr"]["state"] == "unreachable"
    assert r["nvr"]["reason"] == "nvr_unreachable"
    assert r["channels"]["enumerated"] is False        # cannot enumerate through a dead recorder


def test_wrong_credentials_is_auth_failed_not_offline():
    r = assess_nvr_health(FakeDriver(probe_exc=NvrAuthFailed("HTTP 401 rejected")))
    assert r["nvr"]["reachable"] is True and r["nvr"]["auth_ok"] is False
    assert r["nvr"]["state"] == "auth_failed" and r["nvr"]["reason"] == "nvr_auth_failed"
    assert r["channels"]["enumerated"] is False


def test_plain_driver_error_401_classified_as_auth():
    # legacy drivers raise a bare DriverError; classify by message as a fallback
    r = assess_nvr_health(FakeDriver(probe_exc=DriverError("x: HTTP 401 unauthorized")))
    assert r["nvr"]["state"] == "auth_failed" and r["nvr"]["auth_ok"] is False


def test_channel_enumeration_failure_keeps_nvr_ok():
    r = assess_nvr_health(FakeDriver(info=GOOD_INFO,
                                     channels_exc=DriverError("ChannelTitle: HTTP 500")))
    assert r["nvr"]["state"] == "ok"                   # the recorder itself answered
    assert r["channels"]["enumerated"] is False        # but we could not list channels


def test_disabled_channel_reported_with_enable_flag():
    chans = [Channel("1", "Cam 1", enabled=True), Channel("2", "Cam 2", enabled=False)]
    r = assess_nvr_health(FakeDriver(info=GOOD_INFO, channels=chans))
    by = {c["channel"]: c for c in r["channels"]["reported"]}
    assert by["1"]["enabled"] is True and by["2"]["enabled"] is False


def test_no_secrets_anywhere_in_payload():
    # includes a driver error that embeds the recorder URL/IP — it must NOT be forwarded
    for d in (FakeDriver(info=GOOD_INFO, channels=EIGHT),
              FakeDriver(probe_exc=NvrAuthFailed(f"{BASE_URL}/cgi-bin/x: HTTP 401")),
              FakeDriver(probe_exc=NvrUnreachable(f"{BASE_URL}: connection refused")),
              FakeDriver(info=GOOD_INFO,
                         channels_exc=DriverError(f"{BASE_URL}/cgi-bin/y: HTTP 500"))):
        blob = json.dumps(assess_nvr_health(d)).lower()
        for secret in (SECRET_PW.lower(), "admin", "password", "authorization",
                       "192.168.1.108", "http://", "@"):
            assert secret not in blob, f"leaked {secret!r} into telemetry"


def test_repeated_report_is_identical():   # idempotency at the source
    d = FakeDriver(info=GOOD_INFO, channels=EIGHT)
    assert assess_nvr_health(d) == assess_nvr_health(d)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
