#!/usr/bin/env python3
"""The Dahua CGI session moves from Digest to Basic only on a Basic-only challenge (MNVR-055 rule).

DahuaDriver._get switched the shared session to HTTPBasicAuth on ANY 401, whatever the
recorder's challenge said, and nothing switched it back: after a wrong password or a stray 401
every later request on that driver (probe, channel titles, snapshots, the attach event stream)
sent the password base64-encoded over plain HTTP, and a Digest-only recorder then rejected all
of them. Each rejected login was also two attempts against the recorder's lockout counter.

The session now moves to Basic only when the challenge offers Basic and NOT Digest. Unlike
HikvisionDriver._send (per request), the switch is kept for that recorder: snapshot.cgi and the
attach stream use the same session, and a Basic-only unit must keep serving both (RV-5028-01).
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

from drivers.base import DriverError, NvrAuthFailed, NvrUnreachable  # noqa: E402
from drivers.dahua import DahuaDriver  # noqa: E402

DIGEST = 'Digest realm="Login to 4L0C1A2B3C", qop="auth", nonce="abc"'
BASIC = 'Basic realm="Login to 4L0C1A2B3C"'


class Resp:
    def __init__(self, status=200, headers=None, text="ok", content=b"", lines=()):
        self.status_code = status
        self.headers = headers or {}
        self.text = text
        self.content = content
        self._lines = list(lines)
        self.closed = False

    def iter_lines(self, chunk_size=512):
        yield from self._lines

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


def test_basic_only_challenge_moves_this_recorder_to_basic_once():
    first = Resp(401, {"WWW-Authenticate": BASIC})
    driver, session = _driver([first, Resp(200, text="deviceType=XVR"), Resp(200)])
    assert driver._get("/cgi-bin/magicBox.cgi?action=getSystemInfo") == "deviceType=XVR"
    assert first.closed
    assert isinstance(session.auth, HTTPBasicAuth)
    driver._get("/cgi-bin/magicBox.cgi?action=getDeviceType")
    assert [auth for _u, auth, _t in session.sent] == [
        "HTTPDigestAuth", "HTTPBasicAuth", "HTTPBasicAuth"]


def test_wrong_password_on_a_basic_only_unit_is_one_attempt_per_call():
    # Already on Basic: a 401 is the password, not the scheme; no second login attempt.
    driver, session = _driver([Resp(401, {"WWW-Authenticate": BASIC}) for _ in range(3)]
                              + [Resp(200)])
    with pytest.raises(NvrAuthFailed):
        driver._get("/cgi-bin/magicBox.cgi?action=getSystemInfo")
    with pytest.raises(NvrAuthFailed):
        driver._get("/cgi-bin/magicBox.cgi?action=getSystemInfo")
    assert [auth for _u, auth, _t in session.sent] == [
        "HTTPDigestAuth", "HTTPBasicAuth", "HTTPBasicAuth"]


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


JPEG = b"\xff\xd8\xff\xe0fake-jpeg"


class BasicOnlyRecorder:
    """A Dahua-derived unit that offers and accepts Basic only: anything not sent with Basic
    gets 401 with a Basic-only challenge (requests' HTTPDigestAuth then sends nothing)."""

    def __init__(self):
        self.auth = None
        self.sent = []

    def get(self, url, auth=None, stream=False, **kw):
        used = auth if auth is not None else self.auth
        path = url.split("?", 1)[0].rsplit("/", 1)[-1]
        self.sent.append((path, type(used).__name__))
        if not isinstance(used, HTTPBasicAuth):
            return Resp(401, {"WWW-Authenticate": BASIC})
        if path == "snapshot.cgi":
            return Resp(200, content=JPEG)
        if path == "eventManager.cgi":
            return Resp(200, lines=[b"--myboundary", b"Code=VideoMotion;action=Start;index=0"])
        return Resp(200, text="deviceType=XVR\nserialNumber=4L0C1A2B3C\nvideoInChannel=8")

    def close(self):
        pass


def test_basic_only_recorder_serves_probe_attach_and_snapshot():
    # RV-5028-01: a Basic retry inside _get alone left the attach stream and snapshot.cgi on
    # Digest, so a Basic-only unit probed fine, then every still failed and attach got a 401
    # that the collector treats as a wrong password (lockout backoff with a correct one).
    driver = DahuaDriver("http://192.0.2.11", "admin", "secret", timeout=3)
    recorder = BasicOnlyRecorder()
    recorder.auth = driver.s.auth
    driver.s = recorder

    assert driver.probe().model == "XVR"

    stop = threading.Event()
    events = driver.stream_events(stop)
    first = next(events)
    stop.set()
    events.close()
    assert (first.channel, first.event_type) == ("1", "motion")

    assert driver.get_snapshot("1") == JPEG
    # One Basic-only challenge, then the driver stays on Basic for this recorder; no request
    # is ever retried as a second failed login.
    assert recorder.sent[:2] == [("magicBox.cgi", "HTTPDigestAuth"),
                                 ("magicBox.cgi", "HTTPBasicAuth")]
    assert all(auth == "HTTPBasicAuth" for _p, auth in recorder.sent[2:])
    assert [p for p, _a in recorder.sent[2:]] == ["magicBox.cgi", "eventManager.cgi",
                                                  "snapshot.cgi"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
