#!/usr/bin/env python3
"""Incident clips are labelled by their bytes, not by the driver name (MNVR-060).

_upload labelled every non-Dahua clip as application/octet-stream with a '.dav' extension, so a
Hikvision export downloaded as WatchLog-incident-<id>.dav. The cloud accepts only 'mp4' and
'dav' (migration 0040), so a clip that is neither MP4 nor Dahua DAV is stream-copied into MP4
with the bundled FFmpeg; a clip that cannot be made playable is never given a false label.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import incident_evidence as ie  # noqa: E402
from drivers.base import DriverError  # noqa: E402

STATE = {"agent_id": "agent-1", "agent_key": "key-1"}
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
DAV = b"DHAV\xfd\x00\x00\x00" + b"\x00" * 64
PS = b"IMKH\x01\x01\x00\x00" + b"\x00" * 32 + b"\x00\x00\x01\xba" + b"\x00" * 64


class FakeCloud:
    def __init__(self):
        self.calls = []

    def call(self, name, **kw):
        self.calls.append((name, kw))
        return {"ok": True}

    def named(self, name):
        return [kw for call, kw in self.calls if call == name]


def upload(data, driver_name):
    cloud = FakeCloud()
    ie._upload(cloud, STATE, "req-1", data, driver_name)
    complete = cloud.named("wl_agent_complete_clip")
    assert len(complete) == 1
    return cloud, complete[0]


def test_mp4_clip_is_labelled_mp4_whatever_the_driver(monkeypatch):
    monkeypatch.setattr(ie, "_remux_mp4", lambda data: None, raising=False)
    for name in ("hikvision-isapi", "dahua-cgi", "onvif"):
        _, complete = upload(MP4, name)
        assert (complete["p_content_type"], complete["p_file_extension"]) == ("video/mp4", "mp4")


def test_dahua_dav_clip_keeps_the_dav_label(monkeypatch):
    monkeypatch.setattr(ie, "_remux_mp4", lambda data: None, raising=False)
    _, complete = upload(DAV, "dahua-cgi")
    assert (complete["p_content_type"], complete["p_file_extension"]) == ("video/x-dav", "dav")


def test_other_container_is_stream_copied_to_mp4_before_upload(monkeypatch):
    remuxed = b"\x00\x00\x00\x20ftypisom" + b"\x01" * 80
    monkeypatch.setattr(ie, "_remux_mp4", lambda data: remuxed if data == PS else None,
                        raising=False)
    cloud, complete = upload(PS, "hikvision-isapi")
    assert (complete["p_content_type"], complete["p_file_extension"]) == ("video/mp4", "mp4")
    assert complete["p_sha256"] == hashlib.sha256(remuxed).hexdigest()
    assert complete["p_total_bytes"] == len(remuxed)
    import base64
    sent = b"".join(base64.b64decode(kw["p_data_b64"])
                    for kw in cloud.named("wl_agent_upload_clip_chunk"))
    assert sent == remuxed


def test_clip_that_cannot_be_made_playable_is_never_labelled_dav(monkeypatch):
    monkeypatch.setattr(ie, "_remux_mp4", lambda data: None, raising=False)
    cloud = FakeCloud()
    with pytest.raises(DriverError):
        ie._upload(cloud, STATE, "req-1", PS, "hikvision-isapi")
    assert cloud.named("wl_agent_upload_clip_chunk") == []
    assert cloud.named("wl_agent_complete_clip") == []


def test_dahua_export_api_fallback_stays_dav(monkeypatch):
    # loadfile.cgi is Dahua's DAV export; an unrecognised header keeps that documented label.
    monkeypatch.setattr(ie, "_remux_mp4", lambda data: None, raising=False)
    _, complete = upload(b"\x00" * 64, "dahua-cgi")
    assert (complete["p_content_type"], complete["p_file_extension"]) == ("video/x-dav", "dav")


def _ffmpeg():
    try:
        import recovery_ai
        return recovery_ai._ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return shutil.which("ffmpeg")


# CI sets WATCHLOG_REQUIRE_FFMPEG=1 and installs FFmpeg: there a missing FFmpeg fails the test
# instead of skipping it silently.
@pytest.mark.skipif(not _ffmpeg() and not os.environ.get("WATCHLOG_REQUIRE_FFMPEG"),
                    reason="no FFmpeg available")
def test_real_mpeg_ps_export_becomes_a_playable_mp4():
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "clip.mpg"
        subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                        "-f", "lavfi", "-i", "color=c=gray:s=64x48:r=5:d=3",
                        "-c:v", "mpeg2video", "-f", "vob", str(src)], check=True, timeout=60)
        ps = src.read_bytes()
    assert ps[:4] == b"\x00\x00\x01\xba"           # MPEG program stream, not MP4 or DAV
    cloud, complete = upload(ps, "hikvision-isapi")
    assert (complete["p_content_type"], complete["p_file_extension"]) == ("video/mp4", "mp4")
    import base64
    sent = b"".join(base64.b64decode(kw["p_data_b64"])
                    for kw in cloud.named("wl_agent_upload_clip_chunk"))
    assert sent[4:8] == b"ftyp"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
