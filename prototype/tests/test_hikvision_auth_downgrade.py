#!/usr/bin/env python3
"""MNVR-055: the Hikvision ISAPI session never downgrades from Digest to Basic for good.

A final 401 used to switch the shared session to HTTPBasicAuth whatever the recorder's
challenge said, and nothing switched it back: every later snapshot, probe and config
read sent the password base64-encoded over plain HTTP, and Digest-only units then
rejected every request on that driver. The retry also sat outside the try, so a network
error on it escaped as a raw requests exception instead of DriverError.

Basic is now used only when the challenge offers Basic and NOT Digest, and only for that
one retry; the session keeps Digest.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest
import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))

from drivers.base import DriverError  # noqa: E402
from drivers.hikvision import HikvisionDriver  # noqa: E402

DIGEST = 'Digest realm="IP Camera", qop="auth", nonce="abc"'
BASIC = 'Basic realm="IP Camera"'


class Resp:
    def __init__(self, status=200, headers=None, content=b"<ok/>", chunks=()):
        self.status_code = status
        self.headers = headers or {}
        self.content = content
        self.text = content.decode("utf-8", "replace")
        self._chunks = list(chunks)
        self.closed = False

    def iter_content(self, chunk_size=1024):
        yield from self._chunks

    def close(self):
        self.closed = True


class Session:
    """Records the auth each request was sent with (per-request auth wins)."""

    def __init__(self, script):
        self.script = list(script)        # Resp or Exception per request, in order
        self.auth = None
        self.sent = []

    def request(self, method, url, auth=None, **kw):
        used = auth if auth is not None else self.auth
        self.sent.append((method, url, type(used).__name__))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def put(self, url, **kw):
        return self.request("PUT", url, **kw)

    def close(self):
        pass


def _driver(script):
    driver = HikvisionDriver("http://192.0.2.10", "admin", "secret", timeout=1)
    session = Session(script)
    session.auth = driver.s.auth
    driver.s = session
    return driver, session


def test_digest_only_401_never_sends_basic_and_keeps_digest():
    # A second scripted 401 lets the old code's unconditional Basic retry run, so a
    # regression fails on the auth it sent rather than on an exhausted script.
    driver, session = _driver([Resp(401, {"WWW-Authenticate": DIGEST}), Resp(401)])
    with pytest.raises(DriverError):
        driver._get("/ISAPI/System/deviceInfo")
    assert [auth for _m, _u, auth in session.sent] == ["HTTPDigestAuth"]
    assert isinstance(session.auth, HTTPDigestAuth)


def test_basic_only_challenge_gets_one_basic_retry_and_session_stays_digest():
    driver, session = _driver([
        Resp(401, {"WWW-Authenticate": BASIC}), Resp(200),
        Resp(401, {"WWW-Authenticate": BASIC}), Resp(200),
    ])
    assert driver._get("/ISAPI/System/deviceInfo").status_code == 200
    assert isinstance(session.auth, HTTPDigestAuth)
    assert driver._get("/ISAPI/System/time").status_code == 200
    assert [auth for _m, _u, auth in session.sent] == [
        "HTTPDigestAuth", "HTTPBasicAuth", "HTTPDigestAuth", "HTTPBasicAuth"]
    assert isinstance(session.auth, HTTPDigestAuth)


def test_challenge_offering_both_schemes_is_not_downgraded():
    driver, session = _driver([Resp(401, {"WWW-Authenticate": f"{DIGEST}, {BASIC}"}),
                               Resp(401)])
    with pytest.raises(DriverError):
        driver._get("/ISAPI/System/deviceInfo")
    assert [auth for _m, _u, auth in session.sent] == ["HTTPDigestAuth"]


def test_network_error_on_the_basic_retry_is_a_driver_error():
    driver, session = _driver([
        Resp(401, {"WWW-Authenticate": BASIC}),
        requests.ConnectionError("connection reset by peer"),
    ])
    with pytest.raises(DriverError):
        driver._get("/ISAPI/System/deviceInfo")
    assert isinstance(session.auth, HTTPDigestAuth)


def test_put_follows_the_same_rule():
    driver, session = _driver([Resp(401, {"WWW-Authenticate": DIGEST}), Resp(401)])
    with pytest.raises(DriverError):
        driver._put("/ISAPI/Event/notification/httpHosts/1", "<x/>")
    assert [auth for _m, _u, auth in session.sent] == ["HTTPDigestAuth"]
    assert isinstance(session.auth, HTTPDigestAuth)


def test_basic_only_recorder_still_opens_the_event_stream():
    # The session no longer flips to Basic after the probe, so the stream request must
    # make the same one-off Basic retry itself or a Basic-only unit would deliver nothing.
    driver, session = _driver([Resp(401, {"WWW-Authenticate": BASIC}), Resp(200, chunks=[b""])])
    assert list(driver.stream_events(threading.Event())) == []
    assert [auth for _m, _u, auth in session.sent] == ["HTTPDigestAuth", "HTTPBasicAuth"]
    assert isinstance(session.auth, HTTPDigestAuth)
    assert not isinstance(session.auth, HTTPBasicAuth)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
