#!/usr/bin/env python3
"""Hikvision footage is bounded to the requested window (MNVR-031, archive side).

Search rows describe whole recording segments. Downloading the recorder's playbackURI unchanged
exports the entire segment, and a row that does not even overlap the request was downloaded too.
These contracts pin: starttime/endtime rewritten to the request (recorder metadata kept), rows
outside the window rejected, and a downloaded clip checked with the bundled FFmpeg before it is
returned as footage for that window. Whether real firmware honours the rewritten times is a field
question; the guard is visible here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from xml.sax.saxutils import escape

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import hikvision_archive as ha  # noqa: E402
from drivers.base import DriverError  # noqa: E402
from drivers.hikvision import HikvisionDriver  # noqa: E402

SEGMENT_URI = ("rtsp://192.168.1.64/Streaming/tracks/101/"
               "?starttime=20260925T073000Z&endtime=20260925T075000Z&name=ch01_1&size=9000000")


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


class Response:
    def __init__(self, status=200, content=b""):
        self.status_code = status
        self.headers = {}
        self.content = content
        self.text = content.decode("utf-8", "replace")

    def iter_content(self, chunk_size=1):
        yield self.content

    def close(self):
        pass


def starttime_of(body: str) -> str:
    match = re.search(r"starttime=([0-9TZ:-]+)", body)
    return match.group(1) if match else "NONE"


class Session:
    """The fake recorder answers each download with b'SEG-<starttime it was asked for>'."""

    def __init__(self, rows, payload=None):
        self.calls = []
        self.auth = None
        self._search = search_xml(rows)
        self._payload = payload or (lambda body: b"SEG-" + starttime_of(body).encode())

    def post(self, url, data=None, headers=None, stream=False, timeout=None):
        self.calls.append(("POST", url, data.decode()))
        return Response(200, self._search)

    def request(self, method, url, data=None, headers=None, stream=False, timeout=None):
        body = data.decode()
        self.calls.append((method, url, body))
        return Response(200, self._payload(body))

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


def test_playback_uri_is_rewritten_to_the_requested_window():
    s = Session([("2026-09-25T07:30:00Z", "2026-09-25T07:50:00Z", SEGMENT_URI)])
    start = datetime(2026, 9, 25, 7, 40, tzinfo=timezone.utc)
    data = ha.get_clip(driver(s), "1", start, start + timedelta(seconds=30))
    assert data == b"SEG-20260925T074000Z"
    first = s.download_bodies()[0]
    assert "starttime=20260925T074000Z" in first
    assert "endtime=20260925T074030Z" in first
    # The recorder's own locator metadata is kept.
    assert "name=ch01_1" in first and "size=9000000" in first
    assert "/Streaming/tracks/101/" in first


def test_rows_that_do_not_overlap_the_request_are_never_downloaded():
    # A firmware that ignores the search time span and answers with an older segment.
    old = SEGMENT_URI.replace("073000Z", "070000Z").replace("075000Z", "071000Z")
    s = Session([("2026-09-25T07:00:00Z", "2026-09-25T07:10:00Z", old)])
    start = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
    data = ha.get_clip(driver(s), "1", start, start + timedelta(seconds=30))
    assert data == b"SEG-20260925T080000Z"
    assert all("name=ch01_1" not in body for body in s.download_bodies()), \
        "the non-overlapping segment must not be fetched"


def test_clip_much_longer_than_the_window_is_rejected(monkeypatch):
    # The recorder ignored the bounded times and exported a 20-minute segment.
    monkeypatch.setattr(ha, "_probe_clip", lambda data: {"video": True, "duration": 1200.0},
                        raising=False)
    s = Session([("2026-09-25T07:30:00Z", "2026-09-25T07:50:00Z", SEGMENT_URI)])
    start = datetime(2026, 9, 25, 7, 40, tzinfo=timezone.utc)
    with pytest.raises(DriverError) as caught:
        ha.get_clip(driver(s), "1", start, start + timedelta(seconds=30))
    assert getattr(caught.value, "unsupported", False) is False
    assert "192.168" not in str(caught.value)


def test_clip_without_a_video_stream_is_rejected(monkeypatch):
    monkeypatch.setattr(ha, "_probe_clip", lambda data: {"video": False, "duration": None},
                        raising=False)
    s = Session([("2026-09-25T07:30:00Z", "2026-09-25T07:50:00Z", SEGMENT_URI)])
    start = datetime(2026, 9, 25, 7, 40, tzinfo=timezone.utc)
    with pytest.raises(DriverError) as caught:
        ha.get_clip(driver(s), "1", start, start + timedelta(seconds=30))
    assert getattr(caught.value, "unsupported", False) is False


def test_implausible_probe_duration_leaves_timing_unknown(monkeypatch):
    # A 26-hour 'duration' for a clip under 32 MiB is a timestamp wrap, not a whole segment.
    monkeypatch.setattr(ha, "_probe_clip", lambda data: {"video": True, "duration": 95443.7},
                        raising=False)
    s = Session([("2026-09-25T07:30:00Z", "2026-09-25T07:50:00Z", SEGMENT_URI)])
    start = datetime(2026, 9, 25, 7, 40, tzinfo=timezone.utc)
    assert ha.get_clip(driver(s), "1", start, start + timedelta(seconds=30)) == \
        b"SEG-20260925T074000Z"


def test_clip_within_the_window_tolerance_is_kept(monkeypatch):
    monkeypatch.setattr(ha, "_probe_clip", lambda data: {"video": True, "duration": 34.0},
                        raising=False)
    s = Session([("2026-09-25T07:30:00Z", "2026-09-25T07:50:00Z", SEGMENT_URI)])
    start = datetime(2026, 9, 25, 7, 40, tzinfo=timezone.utc)
    assert ha.get_clip(driver(s), "1", start, start + timedelta(seconds=30)) == \
        b"SEG-20260925T074000Z"


# --- the real bundled-FFmpeg probe (skipped where no FFmpeg is installed) -------------------

def _ffmpeg():
    try:
        import recovery_ai
        return recovery_ai._ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return shutil.which("ffmpeg")


def _synth_mp4(seconds: int) -> bytes:
    exe = _ffmpeg()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "clip.mp4"
        subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                        "-f", "lavfi", "-i", f"color=c=gray:s=32x32:r=2:d={seconds}",
                        "-pix_fmt", "yuv420p", str(out)], check=True, timeout=60)
        return out.read_bytes()


needs_ffmpeg = pytest.mark.skipif(not _ffmpeg(), reason="no FFmpeg available")


@needs_ffmpeg
def test_real_probe_reads_video_and_duration(monkeypatch):
    monkeypatch.undo()
    probe = ha._probe_clip(_synth_mp4(4))
    assert probe["video"] is True
    assert abs(probe["duration"] - 4.0) < 1.0
    junk = ha._probe_clip(b"<ResponseStatus>not video</ResponseStatus>" * 10)
    assert junk is not None and junk["video"] is False


@needs_ffmpeg
def test_real_probe_rejects_a_whole_segment_and_keeps_a_bounded_clip(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(sys.modules["drivers.hikvision"], "_HTTP_LOCKS", {})
    rows = [("2026-09-25T07:30:00Z", "2026-09-25T07:50:00Z", SEGMENT_URI)]
    start = datetime(2026, 9, 25, 7, 40, tzinfo=timezone.utc)
    bounded = _synth_mp4(10)
    assert ha.get_clip(driver(Session(rows, payload=lambda body: bounded)),
                       "1", start, start + timedelta(seconds=10)) == bounded
    whole = _synth_mp4(90)
    with pytest.raises(DriverError):
        ha.get_clip(driver(Session(rows, payload=lambda body: whole)),
                    "1", start, start + timedelta(seconds=10))


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
