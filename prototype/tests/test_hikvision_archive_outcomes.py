#!/usr/bin/env python3
"""Hikvision incident-footage outcomes are typed, never a silent 'unsupported' (MNVR-030).

get_clip used to return None for a used-up time budget (including time spent waiting for the
shared archive lock), an oversize export, a 401/403 and an empty body. The footage worker turns
every None into a terminal 'unsupported' verdict. These contracts pin the typed outcomes:
'unsupported' only when the recorder affirmatively rejects the API, the deadline starts once the
lock is held, and an oversize main-stream export gets exactly one sub-stream retry.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import threading
import time
from xml.sax.saxutils import escape

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import hikvision_archive as ha  # noqa: E402
from drivers.base import DriverError, NvrAuthFailed  # noqa: E402
from drivers.hikvision import HikvisionDriver  # noqa: E402

T0 = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(seconds=30)
URI = ("rtsp://192.168.1.64/Streaming/tracks/101/"
       "?starttime=20260925T080000Z&endtime=20260925T080100Z&name=ch01_0800&size=4096")
CLIP = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
MIB = b"\x00" * (1024 * 1024)


def search_xml(rows) -> bytes:
    items = "".join(
        "<searchMatchItem><trackID>101</trackID><timeSpan>"
        f"<startTime>{st}</startTime><endTime>{et}</endTime></timeSpan>"
        f"<mediaSegmentDescriptor><playbackURI>{escape(uri)}</playbackURI>"
        "</mediaSegmentDescriptor></searchMatchItem>"
        for st, et, uri in rows)
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<CMSearchResult xmlns="http://www.isapi.org/ver20/XMLSchema">'
            f"<responseStatusStrg>OK</responseStatusStrg><matchList>{items}</matchList>"
            "</CMSearchResult>").encode()


ONE_ROW = search_xml([("2026-09-25T08:00:00Z", "2026-09-25T08:01:00Z", URI)])
NOT_SUPPORTED = (b'<?xml version="1.0" encoding="UTF-8"?><ResponseStatus>'
                 b"<statusCode>4</statusCode><statusString>Invalid Operation</statusString>"
                 b"<subStatusCode>notSupport</subStatusCode></ResponseStatus>")


class Response:
    def __init__(self, status=200, content=b"", chunks=None, delay=0.0):
        self.status_code = status
        self.headers = {}
        self.content = content
        self.text = content.decode("utf-8", "replace")
        self._chunks = chunks if chunks is not None else [content]
        self._delay = delay

    def iter_content(self, chunk_size=1):
        for chunk in self._chunks:
            if self._delay:
                time.sleep(self._delay)
            yield chunk

    def close(self):
        pass


class Session:
    """Records every request; search and download answers are scripted per test."""

    def __init__(self, search=ONE_ROW, search_status=200, download=None):
        self.calls = []
        self.auth = None
        self._search = search
        self._search_status = search_status
        self._download = download or (lambda method, body: Response(chunks=[CLIP]))

    def post(self, url, data=None, headers=None, stream=False, timeout=None):
        body = data.decode() if isinstance(data, bytes) else str(data or "")
        self.calls.append(("POST", url, body))
        assert url.endswith("/ISAPI/ContentMgmt/search"), url
        return Response(self._search_status, self._search)

    def request(self, method, url, data=None, headers=None, stream=False, timeout=None):
        body = data.decode() if isinstance(data, bytes) else str(data or "")
        self.calls.append((method, url, body))
        assert url.endswith("/ISAPI/ContentMgmt/download"), url
        return self._download(method, body)

    def close(self):
        pass

    def downloads(self):
        return [row for row in self.calls if row[1].endswith("/ISAPI/ContentMgmt/download")]


def driver(session: Session) -> HikvisionDriver:
    d = HikvisionDriver("http://192.168.1.64", "admin", "secret", timeout=2)
    d.s = session
    return d


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    # A private lock per test, and no FFmpeg probe: these fakes are not real media.
    monkeypatch.setattr(ha, "HIKVISION_HTTP_LOCK", threading.RLock())
    monkeypatch.setattr(ha, "_probe_clip", lambda data: None, raising=False)


def assert_failed_not_unsupported(error: Exception) -> None:
    assert isinstance(error, DriverError)
    assert getattr(error, "unsupported", False) is False
    text = str(error)
    assert text and "192.168" not in text and "://" not in text


def test_oversize_main_stream_retries_once_on_the_sub_stream():
    def download(method, body):
        if "/tracks/102/" in body:
            return Response(chunks=[CLIP])
        return Response(chunks=[MIB] * 33)          # main stream exceeds the 32 MiB cap
    s = Session(download=download)
    assert ha.get_clip(driver(s), "1", T0, T1) == CLIP
    sub = [row for row in s.downloads() if "/tracks/102/" in row[2]]
    assert len(sub) == 1, "exactly one sub-stream retry"


def test_oversize_on_both_streams_is_a_size_failure_not_unsupported():
    s = Session(download=lambda method, body: Response(chunks=[MIB] * 33))
    with pytest.raises(DriverError) as caught:
        ha.get_clip(driver(s), "1", T0, T1)
    assert_failed_not_unsupported(caught.value)
    assert "32" in str(caught.value)
    assert len([row for row in s.downloads() if "/tracks/102/" in row[2]]) == 1


def test_download_permission_rejection_is_an_auth_failure_and_stops():
    s = Session(download=lambda method, body: Response(403, b"<ResponseStatus>forbidden"))
    with pytest.raises(NvrAuthFailed) as caught:
        ha.get_clip(driver(s), "1", T0, T1)
    assert_failed_not_unsupported(caught.value)
    # Same credential on every source: no second failed login against the recorder.
    assert len(s.downloads()) == 1


def test_lock_wait_does_not_spend_the_download_budget(monkeypatch):
    monkeypatch.setattr(ha, "CLIP_TOTAL_SECONDS", 1)
    held = threading.Event()

    def holder():
        with ha.HIKVISION_HTTP_LOCK:     # e.g. recovery or the archive scan mid-download
            held.set()
            time.sleep(1.5)

    worker = threading.Thread(target=holder)
    worker.start()
    held.wait(2)
    try:
        assert ha.get_clip(driver(Session()), "1", T0, T1) == CLIP
    finally:
        worker.join()


def test_lock_wait_is_bounded_and_reported_as_a_retryable_timeout(monkeypatch):
    monkeypatch.setattr(ha, "CLIP_LOCK_WAIT_SECONDS", 0.2, raising=False)
    held, release = threading.Event(), threading.Event()

    def holder():
        with ha.HIKVISION_HTTP_LOCK:
            held.set()
            release.wait(3)

    worker = threading.Thread(target=holder)
    worker.start()
    held.wait(2)
    s = Session()
    try:
        with pytest.raises(DriverError) as caught:
            ha.get_clip(driver(s), "1", T0, T1)
    finally:
        release.set()
        worker.join()
    assert_failed_not_unsupported(caught.value)
    assert s.calls == [], "nothing is sent to the recorder without the lock"


def test_budget_exhausted_mid_transfer_is_a_timeout_not_unsupported(monkeypatch):
    monkeypatch.setattr(ha, "CLIP_TOTAL_SECONDS", 0.3)
    s = Session(download=lambda method, body: Response(chunks=[b"x" * 1024] * 50, delay=0.05))
    with pytest.raises(DriverError) as caught:
        ha.get_clip(driver(s), "1", T0, T1)
    assert_failed_not_unsupported(caught.value)


def test_empty_bodies_are_a_failure_not_unsupported():
    s = Session(download=lambda method, body: Response(chunks=[b""]))
    with pytest.raises(DriverError) as caught:
        ha.get_clip(driver(s), "1", T0, T1)
    assert_failed_not_unsupported(caught.value)


def test_no_matching_recording_is_reported_as_no_footage():
    s = Session(search=search_xml([]), download=lambda method, body: Response(404, b"not found"))
    with pytest.raises(DriverError) as caught:
        ha.get_clip(driver(s), "1", T0, T1)
    assert_failed_not_unsupported(caught.value)
    assert "no recorded footage" in str(caught.value).lower()


def test_affirmative_rejection_everywhere_is_unsupported():
    def download(method, body):
        return Response(405 if method == "GET" else 501, b"")
    s = Session(search=b"<html>not found</html>", search_status=404, download=download)
    with pytest.raises(DriverError) as caught:
        ha.get_clip(driver(s), "1", T0, T1)
    assert getattr(caught.value, "unsupported", False) is True
    assert "192.168" not in str(caught.value) and "://" not in str(caught.value)


def test_not_supported_status_body_is_an_affirmative_rejection():
    s = Session(search=NOT_SUPPORTED, search_status=403,
                download=lambda method, body: Response(403, NOT_SUPPORTED))
    with pytest.raises(DriverError) as caught:
        ha.get_clip(driver(s), "1", T0, T1)
    assert not isinstance(caught.value, NvrAuthFailed)
    assert getattr(caught.value, "unsupported", False) is True


def test_recorded_segment_reports_a_typed_status_instead_of_supported_none():
    ha.install()
    d = driver(Session(download=lambda method, body: Response(chunks=[b""])))
    result = d.get_recorded_segment("1", T0, T1)
    assert result["status"] != "supported"
    assert result["bytes"] is None
    ok = driver(Session()).get_recorded_segment("1", T0, T1)
    assert ok == {"status": "supported", "bytes": CLIP}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
