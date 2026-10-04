#!/usr/bin/env python3
"""MNVR-030 (get_snapshot part): a transient Hikvision still failure is an error, not "no image".

HikvisionDriver.get_snapshot turned every DriverError into None. The incident-still worker
reads None as "this recorder returned no still" and fails the request as UNSUPPORTED, so a
timeout, a 5xx or a rejected login became a terminal verdict that bypassed 0058's retry
budget. get_snapshot now raises when a path failed transiently and no path produced a JPEG;
None stays reserved for the recorder affirmatively having no still at those paths.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import requests

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))

from drivers.base import DriverError, NvrAuthFailed  # noqa: E402
from drivers.hikvision import HikvisionDriver  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


class Resp:
    def __init__(self, status=200, content=b"", headers=None):
        self.status_code = status
        self.content = content
        self.text = content.decode("utf-8", "replace")
        self.headers = headers or {}

    def close(self):
        pass


class Session:
    def __init__(self, script):
        self.script = list(script)
        self.auth = None
        self.urls = []

    def request(self, method, url, auth=None, **kw):
        self.urls.append(url)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def close(self):
        pass


def _snapshot(script, channel="2"):
    driver = HikvisionDriver("http://192.0.2.10", "admin", "secret", timeout=1)
    session = Session(script)
    driver.s = session
    return driver.get_snapshot(channel), session


def test_timeout_on_every_path_raises_so_the_still_is_retried():
    timeout = requests.ConnectionError("Read timed out. (read timeout=10)")
    with pytest.raises(DriverError):
        _snapshot([timeout, timeout])


def test_server_error_raises():
    with pytest.raises(DriverError):
        _snapshot([Resp(503, b"busy"), Resp(503, b"busy")])


def test_rejected_login_raises_auth_failure_not_unsupported():
    digest = {"WWW-Authenticate": 'Digest realm="x", nonce="n"'}
    with pytest.raises(NvrAuthFailed):
        _snapshot([Resp(401, headers=digest), Resp(401, headers=digest)])


def test_transient_failure_on_one_path_still_raises_when_the_other_is_absent():
    with pytest.raises(DriverError):
        _snapshot([Resp(404, b"<ResponseStatus/>"), requests.ConnectionError("reset")])


def test_both_paths_absent_is_an_affirmative_no_still():
    image, session = _snapshot([Resp(404, b"<ResponseStatus/>"), Resp(404, b"<ResponseStatus/>")])
    assert image is None
    assert len(session.urls) == 2


def test_non_jpeg_body_is_an_affirmative_no_still():
    image, _session = _snapshot([Resp(200, b"<ResponseStatus>bad</ResponseStatus>"),
                                 Resp(404, b"")])
    assert image is None


def test_second_path_jpeg_wins_after_a_transient_first_path():
    image, session = _snapshot([requests.ConnectionError("Read timed out."), Resp(200, JPEG)])
    assert image == JPEG
    assert session.urls[0].endswith("/ISAPI/Streaming/channels/201/picture")
    assert session.urls[1].endswith("/ISAPI/Streaming/channels/2/picture")


def test_invalid_channel_is_none_without_a_request():
    image, session = _snapshot([], channel="Main Gate")
    assert image is None
    assert session.urls == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
