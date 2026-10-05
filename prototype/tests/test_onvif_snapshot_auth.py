#!/usr/bin/env python3
"""ONVIF stills follow the MNVR-055 rule: Basic only on a Basic-only challenge.

OnvifDriver.get_snapshot fetched the snapshot URI with Digest and, on ANY 401, sent the
same request again with HTTPBasicAuth. A Digest-only refusal (an account without snapshot
rights, a Digest-only snapshot endpoint) therefore put the recorder password on the LAN in
base64 cleartext and cost a second failed login. 5.0.28 runs this path for every camera
about every 300 s (periodic stills). It also swallowed the refusal as None, so the stills
worker counted a quiet camera fault and never used the auth back-off.

Basic is now sent only when the challenge offers Basic and not Digest, and a final 401/403
raises NvrAuthFailed like the Hikvision driver.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from requests.auth import HTTPBasicAuth

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))

from drivers.base import NvrAuthFailed  # noqa: E402
from drivers.onvif_driver import JPEG_MAGIC, OnvifDriver  # noqa: E402

DIGEST = 'Digest realm="onvif", qop="auth", nonce="abc"'
BASIC = 'Basic realm="onvif"'
JPEG = JPEG_MAGIC + b"\xe0fake-jpeg"


class Resp:
    def __init__(self, status=200, headers=None, content=b""):
        self.status_code = status
        self.headers = headers or {}
        self.content = content
        self.closed = False

    def close(self):
        self.closed = True


class Session:
    def __init__(self, script):
        self.script = list(script)
        self.sent = []

    def get(self, url, auth=None, **kw):
        self.sent.append(type(auth).__name__)
        return self.script.pop(0)

    def close(self):
        pass


def _driver(script):
    d = OnvifDriver("http://192.0.2.10", "local-user", "local-password", timeout=1)
    d._profile_tokens = {"1": "P1"}
    d.media_service = "http://192.0.2.10/onvif/media_service"
    d._call = lambda _svc, _body: ET.fromstring(
        "<r><Uri>http://192.0.2.10/onvif/snapshot?profile=P1</Uri></r>")
    session = Session(script)
    d.s = session
    return d, session


def test_digest_only_401_never_sends_basic_and_is_an_auth_failure():
    # A second scripted answer lets an unconditional Basic retry run, so a regression fails
    # on the auth it sent rather than on an exhausted script.
    d, session = _driver([Resp(401, {"WWW-Authenticate": DIGEST}), Resp(200, content=JPEG)])
    with pytest.raises(NvrAuthFailed):
        d.get_snapshot("1")
    assert session.sent == ["HTTPDigestAuth"]


def test_challenge_offering_both_schemes_is_not_downgraded():
    d, session = _driver([Resp(401, {"WWW-Authenticate": f"{DIGEST}, {BASIC}"}),
                          Resp(200, content=JPEG)])
    with pytest.raises(NvrAuthFailed):
        d.get_snapshot("1")
    assert session.sent == ["HTTPDigestAuth"]


def test_basic_only_challenge_gets_one_basic_retry():
    first = Resp(401, {"WWW-Authenticate": BASIC})
    d, session = _driver([first, Resp(200, content=JPEG)])
    assert d.get_snapshot("1") == JPEG
    assert session.sent == ["HTTPDigestAuth", "HTTPBasicAuth"]
    assert first.closed


def test_basic_only_unit_refusing_the_password_is_an_auth_failure():
    d, session = _driver([Resp(401, {"WWW-Authenticate": BASIC}),
                          Resp(401, {"WWW-Authenticate": BASIC})])
    with pytest.raises(NvrAuthFailed):
        d.get_snapshot("1")
    assert session.sent == ["HTTPDigestAuth", "HTTPBasicAuth"]


def test_forbidden_snapshot_is_an_auth_failure():
    d, _session = _driver([Resp(403)])
    with pytest.raises(NvrAuthFailed):
        d.get_snapshot("1")


def test_a_still_and_a_non_jpeg_are_unchanged():
    d, _session = _driver([Resp(200, content=JPEG)])
    assert d.get_snapshot("1") == JPEG
    d, _session = _driver([Resp(404)])
    assert d.get_snapshot("1") is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
