#!/usr/bin/env python3
"""Recorded footage over the recorder's RTSP playback service (rtsp_playback + both archives).

Field proof that motivated it (5.1.6, 2026-10-29):
* Al-Khalid DH-XVR1B08-I: mediaFileFind found every requested window, loadfile startLoad answered
  every one with an empty body (8 channels, windows from the same hour back to 21 days).
* HASCO DS-7608NI-Q1: ContentMgmt/download sent the whole recording file for every 20 s window,
  over 32 MiB on both streams.

Hardware-free: FFmpeg is replaced by a fake subprocess that writes the output file. Field-only
(IMPLEMENTED_UNVERIFIED): that the real recorders serve these playback URLs, and that Hikvision
playback reads its times as recorder wall time.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from dahua_fake_recorder import (  # noqa: E402
    FakeDahua, FakeRecorder, continuous_files, local, pinned_datetime)
import dahua_archive as da  # noqa: E402
import hikvision_archive as ha  # noqa: E402
import rtsp_playback  # noqa: E402
from drivers.base import NvrAuthFailed  # noqa: E402
from drivers.hikvision import HikvisionDriver  # noqa: E402

MP4 = b"\x00\x00\x00\x20ftypisom" + b"\x00" * (8 * 1024)


class FakeFFmpeg:
    """Stands in for subprocess.run: records the command and writes ``payload`` to the output."""

    def __init__(self, payload=MP4, returncode=0, stderr=b"", raises=None):
        self.payload, self.returncode, self.stderr, self.raises = payload, returncode, stderr, raises
        self.commands = []

    def __call__(self, command, **kwargs):
        self.commands.append((command, kwargs))
        if self.raises:
            raise self.raises
        with open(command[-1], "wb") as handle:
            handle.write(self.payload(command) if callable(self.payload) else self.payload)
        return subprocess.CompletedProcess(command, self.returncode, b"", self.stderr)

    def input_url(self, index=-1) -> str:
        command = self.commands[index][0]
        return command[command.index("-i") + 1]


@pytest.fixture
def ffmpeg(monkeypatch):
    fake = FakeFFmpeg()
    monkeypatch.setattr(rtsp_playback, "_ffmpeg", lambda: "ffmpeg.exe")
    monkeypatch.setattr(rtsp_playback.subprocess, "run", fake)
    return fake


def deadline(seconds=90):
    return rtsp_playback.time.monotonic() + seconds


# ---------- rtsp_playback ----------

def test_url_quotes_the_login_and_brackets_ipv6():
    assert (rtsp_playback.url("10.0.0.5", 554, "/x?a=1", "ad min", "p@ss:w/rd")
            == "rtsp://ad%20min:p%40ss%3Aw%2Frd@10.0.0.5:554/x?a=1")
    assert rtsp_playback.url("fe80::1", 0, "/x") == "rtsp://[fe80::1]:554/x"


def test_fetch_reads_exactly_the_window_into_mp4_with_bounds(ffmpeg):
    assert rtsp_playback.fetch("rtsp://h/x", 20, deadline=deadline(), max_bytes=1000000) == MP4
    command, kwargs = ffmpeg.commands[0]
    assert command[command.index("-rtsp_transport") + 1] == "tcp"
    assert command[command.index("-t") + 1] == "20.000"
    assert command[command.index("-fs") + 1] == "1000000"
    assert command[command.index("-c:v") + 1] == "copy"     # never re-encoded
    assert kwargs["timeout"] <= 90


def test_fetch_refuses_a_window_that_cannot_arrive_in_time(ffmpeg):
    with pytest.raises(rtsp_playback.PlaybackTimedOut):
        rtsp_playback.fetch("rtsp://h/x", 30, deadline=deadline(10), max_bytes=10**6)
    assert ffmpeg.commands == []


def test_fetch_at_the_byte_cap_is_too_large(ffmpeg):
    ffmpeg.payload = b"\x00" * 5000
    with pytest.raises(rtsp_playback.PlaybackTooLarge):
        rtsp_playback.fetch("rtsp://h/x", 5, deadline=deadline(), max_bytes=5000)


def test_fetch_with_almost_nothing_written_is_a_failure(ffmpeg):
    ffmpeg.payload = b"\x00" * 100
    with pytest.raises(rtsp_playback.PlaybackFailed):
        rtsp_playback.fetch("rtsp://h/x", 5, deadline=deadline(), max_bytes=10**6)


def test_ffmpeg_text_is_classified_and_never_passed_on(ffmpeg):
    secret = "rtsp://admin:hunter2@192.168.1.64:554/x"
    ffmpeg.payload, ffmpeg.returncode = b"", 1
    ffmpeg.stderr = f"[rtsp @ 0x1] method DESCRIBE failed: 401 Unauthorized {secret}".encode()
    with pytest.raises(NvrAuthFailed) as error:
        rtsp_playback.fetch(secret, 5, deadline=deadline(), max_bytes=10**6)
    assert "hunter2" not in str(error.value) and "192.168" not in str(error.value)
    ffmpeg.stderr = f"{secret}: Connection timed out".encode()
    with pytest.raises(rtsp_playback.PlaybackUnreachable) as error:
        rtsp_playback.fetch(secret, 5, deadline=deadline(), max_bytes=10**6)
    assert "hunter2" not in str(error.value)


def test_ffmpeg_overrunning_the_budget_is_a_timeout(ffmpeg):
    ffmpeg.raises = subprocess.TimeoutExpired("ffmpeg", 5)
    with pytest.raises(rtsp_playback.PlaybackTimedOut):
        rtsp_playback.fetch("rtsp://h/x", 5, deadline=deadline(), max_bytes=10**6)


def test_missing_bundled_ffmpeg_is_a_plain_failure(monkeypatch):
    monkeypatch.setattr(rtsp_playback, "_ffmpeg", lambda: None)
    with pytest.raises(rtsp_playback.PlaybackFailed):
        rtsp_playback.fetch("rtsp://h/x", 5, deadline=deadline(), max_bytes=10**6)


# ---------- Dahua: loadfile first, playback when it answers empty ----------

PC_NOW = datetime(2026, 10, 29, 13, 10, tzinfo=timezone.utc)
START = datetime(2026, 10, 29, 13, 2, 48, tzinfo=timezone.utc)
END = START + timedelta(seconds=20)


@pytest.fixture
def dahua(monkeypatch):
    monkeypatch.setattr(da, "datetime", pinned_datetime(PC_NOW))
    recorder = FakeRecorder(PC_NOW, files=continuous_files(local("2026-10-29 17:30:00"),
                                                           local("2026-10-29 19:00:00")))
    return recorder, FakeDahua(recorder)


def test_dahua_empty_loadfile_falls_back_to_playback_of_the_same_window(dahua, ffmpeg):
    recorder, driver = dahua
    recorder.clip_chunks = lambda: []               # loadfile answers 200 with no body
    assert da.get_clip(driver, "3", START, END) == MP4
    url = ffmpeg.input_url()
    # Recorder wall time (UTC+5), the same window mediaFileFind proved, main stream.
    assert url == ("rtsp://u:p@recorder.invalid:554/cam/playback?channel=3&subtype=0"
                   "&starttime=2026_10_29_18_02_48&endtime=2026_10_29_18_03_08")
    assert ffmpeg.commands[0][0][ffmpeg.commands[0][0].index("-t") + 1] == "20.000"

    # The recorder is remembered: the next clip goes straight to playback.
    loadfile_calls = len(recorder.calls_to("loadfile.cgi"))
    assert da.get_clip(driver, "3", START, END) == MP4
    assert len(recorder.calls_to("loadfile.cgi")) == loadfile_calls


def test_dahua_playback_uses_the_recorders_configured_rtsp_port(dahua, ffmpeg):
    recorder, driver = dahua
    recorder.clip_chunks = lambda: []
    plain_get = recorder.get

    def get(url, params=None, **kw):
        if url.endswith("configManager.cgi"):
            from dahua_fake_recorder import FakeResponse
            recorder.calls.append(("configManager.cgi", dict(params or {}), None, False))
            return FakeResponse(text="table.RTSP.Enable=true\r\ntable.RTSP.Port=8554\r\n")
        return plain_get(url, params=params, **kw)

    recorder.get = get
    da.get_clip(driver, "1", START, END)
    assert ":8554/cam/playback?" in ffmpeg.input_url()


def test_dahua_working_loadfile_never_starts_playback(dahua, ffmpeg):
    _recorder, driver = dahua
    assert da.get_clip(driver, "1", START, END).startswith(b"DHAV")
    assert ffmpeg.commands == []


def test_dahua_window_without_recording_never_starts_playback(dahua, ffmpeg):
    recorder, driver = dahua
    recorder.files = []
    assert da.get_clip(driver, "1", START, END) is None
    assert ffmpeg.commands == []


def test_dahua_oversize_main_stream_retries_the_sub_stream(dahua, ffmpeg):
    recorder, driver = dahua
    recorder.clip_chunks = lambda: []
    ffmpeg.payload = lambda command: (b"\x00" * da.MAX_CLIP_BYTES if "subtype=0" in
                                      command[command.index("-i") + 1] else MP4)
    assert da.get_clip(driver, "1", START, END) == MP4
    assert "subtype=1" in ffmpeg.input_url()


# ---------- Hikvision: playback first, ISAPI export as the fallback ----------

SEARCH = """<?xml version="1.0" encoding="UTF-8"?>
<CMSearchResult xmlns="http://www.isapi.org/ver20/XMLSchema">
  <responseStatusStrg>OK</responseStatusStrg>
  <matchList><searchMatchItem><trackID>101</trackID>
    <timeSpan><startTime>2026-10-29T12:30:00Z</startTime><endTime>2026-10-29T13:30:00Z</endTime></timeSpan>
    <mediaSegmentDescriptor><playbackURI>rtsp://192.168.1.64/Streaming/tracks/101/?starttime=20261029T123000Z&amp;endtime=20261029T133000Z&amp;name=ch01_0800&amp;size=900000000</playbackURI></mediaSegmentDescriptor>
  </searchMatchItem></matchList>
</CMSearchResult>"""
TIME_XML = ("<Time><timeMode>NTP</timeMode><localTime>2026-10-29T18:10:00+05:00</localTime>"
            "<timeZone>CST-5:00:00</timeZone></Time>")
ACCESS_XML = ("<AdminAccessProtocolList><AdminAccessProtocol><protocol>HTTP</protocol><portNo>80"
              "</portNo></AdminAccessProtocol><AdminAccessProtocol><protocol>RTSP</protocol>"
              "<portNo>10554</portNo></AdminAccessProtocol></AdminAccessProtocolList>")


class HikResponse:
    def __init__(self, status=200, body=b""):
        self.status_code, self.content, self.headers = status, body, {}
        self.text = body.decode("utf-8", "replace")
        self.raw = None

    def iter_content(self, chunk_size=1):
        yield self.content

    def close(self):
        pass


class HikSession:
    def __init__(self, *, time_xml=TIME_XML, export=b"\x00" * (33 * 1024 * 1024)):
        self.time_xml, self.export, self.urls = time_xml, export, []
        self.auth = None

    def request(self, method, url, data=None, headers=None, stream=False, timeout=None, **_kw):
        self.urls.append((method, url))
        if url.endswith("/ISAPI/ContentMgmt/search"):
            return HikResponse(body=SEARCH.encode())
        if url.endswith("/ISAPI/System/time"):
            return HikResponse(body=self.time_xml.encode()) if self.time_xml else HikResponse(404)
        if url.endswith("/ISAPI/Security/adminAccesses"):
            return HikResponse(body=ACCESS_XML.encode())
        if url.endswith("/ISAPI/ContentMgmt/download"):
            return HikResponse(body=self.export)
        return HikResponse(404)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def get(self, url, **kw):
        return self.request("GET", url, **kw)


def hik(session) -> HikvisionDriver:
    driver = HikvisionDriver("http://192.168.1.64", "admin", "pw", timeout=5)
    driver.s = session
    return driver


H_START = datetime(2026, 10, 29, 13, 2, 48, tzinfo=timezone.utc)
H_END = H_START + timedelta(seconds=20)


@pytest.mark.recorder_playback
def test_hikvision_reads_the_window_from_playback_in_recorder_wall_time(ffmpeg, monkeypatch):
    monkeypatch.setattr(ha, "_check_clip", lambda data, start, end: data)
    session = HikSession()
    assert ha.get_clip(hik(session), "3", H_START, H_END) == MP4
    # The recorder's clock names +05:00, so 13:02:48Z is 18:02:48 on the recorder; its own port.
    assert ffmpeg.input_url() == ("rtsp://admin:pw@192.168.1.64:10554/Streaming/tracks/301"
                                  "?starttime=20261029T180248Z&endtime=20261029T180308Z")
    assert not any(url.endswith("/download") for _m, url in session.urls), \
        "the whole-file export must not run once playback served the window"


@pytest.mark.recorder_playback
def test_hikvision_without_a_clock_offset_skips_playback_rather_than_guess(ffmpeg, monkeypatch):
    monkeypatch.setattr(ha, "_check_clip", lambda data, start, end: data)
    session = HikSession(time_xml="<Time><localTime>2026-10-29T18:10:00</localTime></Time>")
    with pytest.raises(ha.ClipTooLarge):
        ha.get_clip(hik(session), "1", H_START, H_END)
    assert ffmpeg.commands == []


@pytest.mark.parametrize("local_time, minutes", [
    ("2026-10-29T18:10:00+05:00", 300), ("2026-10-29T08:10:00-0500", -300),
    ("2026-10-29T13:10:00Z", 0), ("2026-10-29T18:10:00", None)])
def test_hikvision_clock_offset_comes_from_the_recorders_own_local_time(local_time, minutes):
    driver = hik(HikSession(time_xml=f"<Time><localTime>{local_time}</localTime></Time>"))
    offset = ha._device_offset(driver)
    assert offset == (None if minutes is None else timedelta(minutes=minutes))


@pytest.mark.recorder_playback
def test_hikvision_oversize_sub_stream_export_still_reaches_the_by_time_source(monkeypatch):
    monkeypatch.setattr(ha, "_playback_clip",
                        lambda *a, **k: (_ for _ in ()).throw(ha.ClipNotReturned(detail="off")))
    session = HikSession()
    with pytest.raises(ha.ClipTooLarge):
        ha.get_clip(hik(session), "1", H_START, H_END)
    downloads = [url for _m, url in session.urls if url.endswith("/download")]
    # main export, its sub stream, then the generic by-time URI: three distinct sources tried
    assert len(downloads) >= 3


@pytest.mark.recorder_playback
def test_hikvision_playback_with_no_footage_falls_back_to_the_export(ffmpeg, monkeypatch):
    monkeypatch.setattr(ha, "_check_clip", lambda data, start, end: data)
    ffmpeg.payload, ffmpeg.returncode = b"", 1
    ffmpeg.stderr = b"method DESCRIBE failed: 404 Not Found"
    session = HikSession(export=b"\x00\x00\x01\xba" + b"\x00" * 4096)
    assert ha.get_clip(hik(session), "1", H_START, H_END).startswith(b"\x00\x00\x01\xba")


def test_playback_failure_is_never_a_capability_verdict():
    with mock.patch.object(rtsp_playback, "_ffmpeg", return_value=None):
        with pytest.raises(rtsp_playback.PlaybackFailed) as error:
            rtsp_playback.fetch("rtsp://h/x", 5, deadline=deadline(), max_bytes=10**6)
    assert getattr(error.value, "unsupported", False) is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
