#!/usr/bin/env python3
"""The Dahua CGI session never downgrades from Digest to Basic for good (MNVR-055 rule).

DahuaDriver._get switched the shared session to HTTPBasicAuth on ANY 401, whatever the
recorder's challenge said, and nothing switched it back: every later request on that driver
(probe, channel titles, snapshots, the attach event stream) sent the password base64-encoded
over plain HTTP, and a Digest-only recorder then rejected all of them. Each rejected login was
also two attempts against the recorder's lockout counter.

Basic is now used only when the challenge offers Basic and NOT Digest, and only for that one
retry; the session keeps Digest. Same rule as HikvisionDriver._send.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import requests
from requests.auth import HTTPDigestAuth

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))

from drivers.base import DriverError, NvrAuthFailed, NvrUnreachable  # noqa: E402
from drivers.dahua import DahuaDriver  # noqa: E402

DIGEST = 'Digest realm="Login to 4L0C1A2B3C", qop="auth", nonce="abc"'
BASIC = 'Basic realm="Login to 4L0C1A2B3C"'


class Resp:
    def __init__(self, status=200, headers=None, text="ok"):
        self.status_code = status
        self.headers = headers or {}
        self.text = text
        self.closed = False

    def close(self):
        self.closed = True


class Session:
    """Records the auth each GET was sent with (per-request auth wins over the session's)."""

    def __init__(self, script):
        self.script = list(script)
        self.auth = None
        self.sent = []

    def get(self, url, auth=None, **kw):
        used = auth if auth is not None else self.auth
        self.sent.append((url, type(used).__name__, kw.get("timeout")))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        pass


def _driver(script):
    driver = DahuaDriver("http://192.0.2.11", "admin", "secret", timeout=3)
    session = Session(script)
    session.auth = driver.s.auth
    driver.s = session
    return driver, session


def test_digest_only_401_never_sends_basic_and_keeps_digest():
    # A second scripted 401 lets an unconditional Basic retry run, so a regression fails on
    # the auth it sent rather than on an exhausted script.
    driver, session = _driver([Resp(401, {"WWW-Authenticate": DIGEST}), Resp(401)])
    with pytest.raises(NvrAuthFailed):
        driver._get("/cgi-bin/magicBox.cgi?action=getSystemInfo")
    assert [auth for _u, auth, _t in session.sent] == ["HTTPDigestAuth"]
    assert isinstance(session.auth, HTTPDigestAuth)


def test_basic_only_challenge_gets_one_basic_retry_and_session_stays_digest():
    first = Resp(401, {"WWW-Authenticate": BASIC})
    driver, session = _driver([first, Resp(200, text="deviceType=XVR"),
                               Resp(401, {"WWW-Authenticate": BASIC}), Resp(200)])
    assert driver._get("/cgi-bin/magicBox.cgi?action=getSystemInfo") == "deviceType=XVR"
    assert first.closed
    assert isinstance(session.auth, HTTPDigestAuth)
    driver._get("/cgi-bin/magicBox.cgi?action=getDeviceType")
    assert [auth for _u, auth, _t in session.sent] == [
        "HTTPDigestAuth", "HTTPBasicAuth", "HTTPDigestAuth", "HTTPBasicAuth"]
    assert isinstance(session.auth, HTTPDigestAuth)


def test_challenge_offering_both_schemes_is_not_downgraded():
    driver, session = _driver([Resp(401, {"WWW-Authenticate": f"{DIGEST}, {BASIC}"}),
                               Resp(401)])
    with pytest.raises(NvrAuthFailed):
        driver._get("/cgi-bin/magicBox.cgi?action=getSystemInfo")
    assert [auth for _u, auth, _t in session.sent] == ["HTTPDigestAuth"]


def test_basic_retry_keeps_the_callers_timeout_and_network_errors_stay_driver_errors():
    driver, session = _driver([Resp(401, {"WWW-Authenticate": BASIC}),
                               requests.ConnectionError("connection reset by peer")])
    with pytest.raises(NvrUnreachable):
        driver._get("/cgi-bin/configManager.cgi?action=getConfig&name=ChannelTitle", timeout=7)
    assert [t for _u, _a, t in session.sent] == [7, 7]


def test_other_errors_are_unchanged():
    driver, _session = _driver([Resp(404, text="Error")])
    with pytest.raises(DriverError) as raised:
        driver._get("/cgi-bin/unknown.cgi")
    assert not isinstance(raised.value, NvrAuthFailed)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
