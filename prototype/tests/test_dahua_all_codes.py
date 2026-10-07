#!/usr/bin/env python3
"""5.1.2: the Dahua attach subscribes to codes=[All] and nothing it sends is silently lost.

The 5.1.1 attach named the 14 mapped codes, so anything else the recorder raised (network
abort, login failure, defocus, analytics we do not map) never reached WatchLog. Now every
code arrives: mapped codes keep their vocabulary, an unmapped code is stored raw
(lowercased) like Hikvision's, and chatter that is not an occurrence is still dropped.

An unmapped code is camera-scoped only when it is a camera analytic (its index is a video
channel); a recorder-level code is recorder-scoped; a code we do not know is never guessed
onto camera index+1 (channel None + channel_unknown, the index kept as native_index).
"""
from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "bridge"))

from drivers import alarm_parsing  # noqa: E402
from drivers import dahua  # noqa: E402
from drivers.native_recorder import NativeDahuaDriver  # noqa: E402
import push_bridge as pb  # noqa: E402


def _bridge_copy():
    spec = importlib.util.spec_from_file_location(
        "bridge_alarm_parsing_allcodes", ROOT / "bridge" / "alarm_parsing.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


PARSERS = (alarm_parsing, _bridge_copy())


class _Resp:
    status_code = 200

    def __init__(self):
        self.closed = False

    def iter_lines(self, chunk_size=512):
        return iter(())

    def close(self):
        self.closed = True


def test_attach_subscribes_to_all_codes():
    assert dahua.SUBSCRIBE_CODES == "All"
    d = dahua.DahuaDriver("http://10.0.0.9", "u", "p", timeout=1)
    urls = []

    def fake_get(url, **kw):
        urls.append(url)
        return _Resp()

    d.s.get = fake_get
    list(d.stream_events(threading.Event()))
    assert len(urls) == 1
    assert "eventManager.cgi?action=attach&codes=[All]&heartbeat=5" in urls[0]


@pytest.mark.parametrize("mod", PARSERS)
@pytest.mark.parametrize("code,expected", [
    ("VideoMotion", "motion"), ("SmartMotionHuman", "person"),
    ("SmartMotionVehicle", "vehicle"), ("CrossLineDetection", "line_crossing"),
    ("CrossRegionDetection", "intrusion"), ("LeftDetection", "object_left"),
    ("TakenAwayDetection", "object_removed"), ("VideoLoss", "video_loss"),
    ("VideoBlind", "tamper"), ("FaceDetection", "face"),
])
def test_known_camera_codes_still_map(mod, code, expected):
    alarm = mod.parse_dahua_block(f"Code={code};action=Start;index=4")
    assert (alarm.event_type, alarm.channel) == (expected, "5")


@pytest.mark.parametrize("mod", PARSERS)
@pytest.mark.parametrize("code", ["VideoUnFocus", "VideoAbnormalDetection", "AudioMutation",
                                  "WanderDetection", "SceneChangeDetection"])
def test_unmapped_camera_analytics_are_kept_raw_on_their_camera(mod, code):
    alarm = mod.parse_dahua_block(f"Code={code};action=Start;index=1")
    assert alarm.event_type == code.lower()
    assert alarm.channel == "2"
    assert alarm.payload["code"] == code


@pytest.mark.parametrize("mod", PARSERS)
@pytest.mark.parametrize("code", ["NetAbort", "IPConflict", "LoginFailure", "PowerFault",
                                  "ChassisIntruded", "AlarmOutput"])
def test_recorder_level_codes_are_recorder_scoped(mod, code):
    alarm = mod.parse_dahua_block(f"Code={code};action=Start;index=0")
    assert alarm.event_type == code.lower()
    assert alarm.channel is None
    assert alarm.payload["recorder_scoped"] is True
    assert alarm.payload["native_index"] == "0"


@pytest.mark.parametrize("mod", PARSERS)
def test_an_unknown_code_is_never_guessed_onto_camera_one(mod):
    alarm = mod.parse_dahua_block("Code=SomethingNew;action=Pulse;index=0")
    assert alarm.event_type == "somethingnew"
    assert alarm.channel is None
    assert alarm.payload["channel_unknown"] is True
    assert alarm.payload["native_index"] == "0"


@pytest.mark.parametrize("mod", PARSERS)
@pytest.mark.parametrize("code", ["Heartbeat", "KeepAlive", "TimeChange", "NTPAdjustTime",
                                  "NewFile", "MDResult", "VideoMotionInfo", "IntelliFrame",
                                  "RtspSessionDisconnect", "SnapManual"])
def test_chatter_is_not_an_event(mod, code):
    for action in ("Start", "Pulse", "Stop"):
        assert mod.parse_dahua_block(f"Code={code};action={action};index=0") is None


def test_native_face_detection_is_recorder_native_ai():
    """FaceDetection is the recorder's own classification: native_ai, never gated on a
    local person/vehicle pass."""
    d = NativeDahuaDriver("http://127.0.0.1", "u", "p", timeout=1)
    ev = d._parse_line("Code=FaceDetection;action=Start;index=2")
    assert ev.event_type == "face" and ev.channel == "3"
    assert ev.payload["native_ai"] is True
    assert ev.payload["source"] == "recorder_native_ai"


def test_driver_spools_unknown_and_recorder_level_codes():
    d = NativeDahuaDriver("http://127.0.0.1", "u", "p", timeout=1)
    a = d._parse_line("Code=NetAbort;action=Start;index=0")
    b = d._parse_line("Code=VideoUnFocus;action=Start;index=3")
    c = d._parse_line("Code=NewFile;action=Pulse;index=0")
    assert (a.event_type, a.channel, a.payload["recorder_scoped"]) == ("netabort", None, True)
    assert (b.event_type, b.channel) == ("videounfocus", "4")
    assert c is None


def test_push_bridge_parses_unmapped_codes_like_the_agent():
    d = NativeDahuaDriver("http://127.0.0.1", "u", "p", timeout=1)
    for line in ("Code=LoginFailure;action=Pulse;index=0",
                 "Code=VideoAbnormalDetection;action=Start;index=6",
                 "Code=SomethingNew;action=Start;index=2"):
        bridge = pb.parse_dahua(line.encode(), "text/plain")
        agent = d._parse_line(line)
        assert (bridge["event_type"], bridge["channel"]) == (agent.event_type, agent.channel)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
