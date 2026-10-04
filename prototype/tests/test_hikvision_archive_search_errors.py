#!/usr/bin/env python3
"""Hikvision archive search errors become typed recovery outcomes (MNVR-059, archive side).

enumerate_historical_events raised on every search error, so a firmware that permanently rejects
the search left recovery intervals in progress forever. Definitive answers are now statuses:
'unsupported' only for an affirmative rejection (404/405/501 or a notSupport status), 'unknown'
for any other definitive refusal (400, a login refusal, an unparseable answer). A transient
failure (unreachable, recorder busy) still raises so the interval stays retryable; the attempt
cap and checkpointing belong to the recovery runner.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import threading

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import hikvision_archive as ha  # noqa: E402
from drivers.base import DriverError  # noqa: E402
from drivers.hikvision import HikvisionDriver  # noqa: E402

T0 = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(minutes=10)
NOT_SUPPORTED = (b'<?xml version="1.0" encoding="UTF-8"?><ResponseStatus>'
                 b"<statusCode>4</statusCode><statusString>Invalid Operation</statusString>"
                 b"<subStatusCode>notSupport</subStatusCode></ResponseStatus>")
BAD_XML = (b'<?xml version="1.0" encoding="UTF-8"?><ResponseStatus><statusCode>6</statusCode>'
           b"<statusString>Invalid XML Content</statusString>"
           b"<subStatusCode>badXmlContent</subStatusCode></ResponseStatus>")


class Response:
    def __init__(self, status, content):
        self.status_code = status
        self.headers = {}
        self.content = content
        self.text = content.decode("utf-8", "replace")

    def iter_content(self, chunk_size=1):
        yield self.content

    def close(self):
        pass


class Session:
    def __init__(self, answer):
        self.auth = None
        self.calls = 0
        self._answer = answer

    def post(self, url, data=None, headers=None, stream=False, timeout=None):
        self.calls += 1
        if isinstance(self._answer, BaseException):
            raise self._answer
        return Response(*self._answer)

    def close(self):
        pass


def driver(answer) -> HikvisionDriver:
    d = HikvisionDriver("http://192.168.1.64", "admin", "secret", timeout=2)
    d.s = Session(answer)
    return d


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(ha, "HIKVISION_HTTP_LOCK", threading.RLock())


@pytest.mark.parametrize("answer", [
    (404, b"<html>Not Found</html>"),
    (405, b""),
    (501, b""),
    (403, NOT_SUPPORTED),
    (200, NOT_SUPPORTED),
])
def test_affirmative_rejection_is_unsupported(answer):
    page = ha.enumerate_historical_events(driver(answer), "1", T0, T1, None, 8)
    assert page["status"] == "unsupported"
    assert page["events"] == [] and page["next_cursor"] is None


@pytest.mark.parametrize("answer, reason", [
    ((400, BAD_XML), "rejected"),
    ((401, b""), "auth"),
    ((403, b"<ResponseStatus>forbidden</ResponseStatus>"), "auth"),
    ((200, b"<CMSearchResult><matchList><searchMatchItem>"), "invalid_response"),
])
def test_other_definitive_refusals_are_unknown(answer, reason):
    page = ha.enumerate_historical_events(driver(answer), "1", T0, T1, None, 8)
    assert page["status"] == "unknown"
    assert page["events"] == [] and page["next_cursor"] is None
    assert page["reason"] == reason


@pytest.mark.parametrize("answer", [
    requests.exceptions.ConnectTimeout("HTTPConnectionPool(host='192.168.1.64', port=80)"),
    (503, b"busy"),
])
def test_transient_failures_still_raise_so_recovery_retries(answer):
    with pytest.raises(DriverError):
        ha.enumerate_historical_events(driver(answer), "1", T0, T1, None, 8)


def test_archive_proof_keeps_auth_and_unsupported_distinct():
    auth = ha.prove_recorder_archive(driver((401, b"")), "1", now=T1)
    assert auth["status"] == "unknown"
    assert "login" in auth["detail"]
    rejected = ha.prove_recorder_archive(driver((404, b"")), "1", now=T1)
    assert rejected["status"] == "unsupported"
    unclear = ha.prove_recorder_archive(driver((400, BAD_XML)), "1", now=T1)
    assert unclear["status"] == "unknown"
    assert "not supported" not in unclear["detail"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
