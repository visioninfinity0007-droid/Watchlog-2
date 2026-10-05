#!/usr/bin/env python3
"""A network failure during a Hikvision footage transfer stays inside get_clip (MNVR-057).

iter_content raises requests exceptions (read timeout, dropped connection, truncated chunked
body) that are not DriverError. They escaped get_clip, skipped the remaining candidate and the
bounded by-time fallback, and carried "HTTPConnectionPool(host='192.168.x.x', ...)" into
tenant-visible error text. These contracts pin: the next source is tried, and a final transport
failure is a typed, redacted NvrUnreachable that is never 'unsupported'.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import threading
from xml.sax.saxutils import escape

import pytest
import requests
import urllib3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import hikvision_archive as ha  # noqa: E402
from drivers.base import DriverError, NvrUnreachable  # noqa: E402
from drivers.hikvision import HikvisionDriver  # noqa: E402

T0 = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(seconds=30)
CAND1 = "rtsp://192.168.1.64/Streaming/tracks/101/?starttime=20260925T080000Z&endtime=20260925T080015Z&name=cand1"
CAND2 = "rtsp://192.168.1.64/Streaming/tracks/101/?starttime=20260925T080015Z&endtime=20260925T080100Z&name=cand2"
SEARCH = ('<?xml version="1.0" encoding="UTF-8"?>'
          '<CMSearchResult xmlns="http://www.isapi.org/ver20/XMLSchema">'
          "<responseStatusStrg>OK</responseStatusStrg><matchList>"
          + "".join(
              "<searchMatchItem><trackID>101</trackID><timeSpan>"
              f"<startTime>{st}</startTime><endTime>{et}</endTime></timeSpan>"
              f"<mediaSegmentDescriptor><playbackURI>{escape(uri)}</playbackURI>"
              "</mediaSegmentDescriptor></searchMatchItem>"
              for st, et, uri in (("2026-09-25T08:00:00Z", "2026-09-25T08:00:15Z", CAND1),
                                  ("2026-09-25T08:00:15Z", "2026-09-25T08:01:00Z", CAND2)))
          + "</matchList></CMSearchResult>").encode()
CLIP = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def read_timeout():
    pool = urllib3.HTTPConnectionPool("192.168.1.64", 80)
    return requests.exceptions.ConnectionError(
        urllib3.exceptions.ReadTimeoutError(pool, None, "Read timed out."))


def chunked_error():
    return requests.exceptions.ChunkedEncodingError(
        "('Connection broken: IncompleteRead(10 bytes read, 90 more expected)', "
        "IncompleteRead(10 bytes read, 90 more expected))")


class Response:
    def __init__(self, status=200, chunks=(), content=b""):
        self.status_code = status
        self.headers = {}
        self._chunks = list(chunks)
        self._content = content
        self.text = content.decode("utf-8", "replace") if isinstance(content, bytes) else ""

    @property
    def content(self):
        if isinstance(self._content, BaseException):
            raise self._content
        return self._content

    def iter_content(self, chunk_size=1):
        for chunk in self._chunks:
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk

    def close(self):
        pass


class Session:
    def __init__(self, download, search=SEARCH):
        self.calls = []
        self.auth = None
        self._download = download
        self._search = search

    def post(self, url, data=None, headers=None, stream=False, timeout=None):
        self.calls.append(("POST", url, data.decode()))
        return Response(200, content=self._search)

    def request(self, method, url, data=None, headers=None, stream=False, timeout=None):
        body = data.decode()
        self.calls.append((method, url, body))
        return self._download(method, body)

    def close(self):
        pass

    def download_bodies(self):
        return [row[2] for row in self.calls if row[1].endswith("/ISAPI/ContentMgmt/download")]


def driver(session) -> HikvisionDriver:
    d = HikvisionDriver("http://192.168.1.64", "admin", "secret", timeout=2)
    d.s = session
    return d


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(sys.modules["drivers.hikvision"], "_HTTP_LOCKS", {})
    monkeypatch.setattr(ha, "_probe_clip", lambda data: None, raising=False)


def assert_redacted_transport_failure(error):
    assert isinstance(error, NvrUnreachable)
    assert getattr(error, "unsupported", False) is False
    text = str(error)
    assert "192.168" not in text and "host=" not in text.lower() and "://" not in text


@pytest.mark.parametrize("broken", [read_timeout, chunked_error])
def test_mid_transfer_failure_moves_on_to_the_next_candidate(broken):
    def download(method, body):
        if "name=cand1" in body:
            return Response(chunks=[b"partial", broken()])
        return Response(chunks=[CLIP])
    s = Session(download)
    assert ha.get_clip(driver(s), "1", T0, T1) == CLIP
    assert any("name=cand2" in body for body in s.download_bodies())


@pytest.mark.parametrize("broken", [read_timeout, chunked_error])
def test_mid_transfer_failure_everywhere_is_a_redacted_transport_failure(broken):
    s = Session(lambda method, body: Response(chunks=[b"partial", broken()]))
    with pytest.raises(DriverError) as caught:
        ha.get_clip(driver(s), "1", T0, T1)
    assert_redacted_transport_failure(caught.value)
    bodies = s.download_bodies()
    assert any("name=cand2" in body for body in bodies), "second candidate still tried"
    assert any("name=" not in body for body in bodies), "bounded by-time fallback still tried"


def test_broken_search_body_still_reaches_the_by_time_fallback():
    s = Session(lambda method, body: Response(chunks=[CLIP]), search=chunked_error())
    assert ha.get_clip(driver(s), "1", T0, T1) == CLIP


def test_broken_search_body_is_a_typed_transport_error():
    s = Session(lambda method, body: Response(chunks=[CLIP]), search=read_timeout())
    with pytest.raises(NvrUnreachable):
        ha.search_recordings(driver(s), "1", T0, T1)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
