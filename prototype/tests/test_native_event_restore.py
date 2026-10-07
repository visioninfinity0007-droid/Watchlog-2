#!/usr/bin/env python3
"""5.1.2: the END of a fault/safety alarm is an event, on both vendors and both paths.

Before 5.1.2 a Hikvision eventState=inactive and a Dahua action=Stop were dropped as
"not an occurrence", so a camera's video coming back, a tamper ending or an alarm input
resetting never reached WatchLog. Now:

  Hikvision inactive  videoloss -> video_restore, tamperdetection/shelteralarm -> tamper_end,
                      IO -> alarm_input_end, ipcDisconnect -> camera_reconnect
  Dahua Stop          VideoLoss -> video_restore, VideoBlind -> tamper_end,
                      AlarmLocal -> alarm_input_end

The heartbeat videoloss (activePostCount 0) is still not an event, the end of a plain
detection (VMD, VideoMotion Stop) is still not an event, and a Dahua action=State is a
keep-alive. Burst collapse never suppresses a restore right after a loss, an end is
admitted once per start, and a loss right after a restore is a new event. The Agent
drivers and the push bridge (byte-identical parser copy) agree on all of it.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "bridge"))

from drivers import alarm_parsing  # noqa: E402
from drivers.native_recorder import NativeDahuaDriver, NativeHikvisionDriver  # noqa: E402
import push_bridge as pb  # noqa: E402


def _bridge_copy():
    spec = importlib.util.spec_from_file_location(
        "bridge_alarm_parsing_restore", ROOT / "bridge" / "alarm_parsing.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


PARSERS = (alarm_parsing, _bridge_copy())


def _hik(etype: str, state: str = "active", count: str | None = "1", fields: str = "") -> bytes:
    post = f"<activePostCount>{count}</activePostCount>" if count is not None else ""
    return f"""<EventNotificationAlert xmlns="http://www.hikvision.com/ver20/XMLSchema">
      <eventType>{etype}</eventType><eventState>{state}</eventState>
      {fields}<dateTime>2026-10-04T21:00:00+05:00</dateTime>{post}
    </EventNotificationAlert>""".encode()


def test_parser_copies_are_byte_identical():
    assert (ROOT / "agent" / "drivers" / "alarm_parsing.py").read_bytes() == \
        (ROOT / "bridge" / "alarm_parsing.py").read_bytes()


# --- Hikvision -------------------------------------------------------------------------

@pytest.mark.parametrize("mod", PARSERS)
@pytest.mark.parametrize("etype,start,end", [
    ("videoloss", "video_loss", "video_restore"),
    ("tamperdetection", "tamper", "tamper_end"),
    ("shelteralarm", "tamper", "tamper_end"),
    ("ipcDisconnect", "camera_disconnect", "camera_reconnect"),
    ("IPCDisconnect", "camera_disconnect", "camera_reconnect"),
])
def test_hikvision_camera_fault_start_and_end(mod, etype, start, end):
    on = mod.parse_hikvision_alert(_hik(etype, "active", fields="<channelID>4</channelID>"))
    off = mod.parse_hikvision_alert(_hik(etype, "inactive", fields="<channelID>4</channelID>"))
    assert (on.event_type, on.channel, on.phase) == (start, "4", "start")
    assert (off.event_type, off.channel, off.phase) == (end, "4", "end")
    # The end pairs with the start it closes, and is burst-keyed by its own type.
    assert off.pair_key == on.burst_key
    assert off.burst_key != on.burst_key
    assert off.payload["eventState"] == "inactive"
    assert off.payload["native_ai"] is False


@pytest.mark.parametrize("mod", PARSERS)
def test_hikvision_io_is_alarm_input_and_its_end_is_recorder_scoped(mod):
    fields = "<channelID>1</channelID><inputIOPortID>3</inputIOPortID>"
    on = mod.parse_hikvision_alert(_hik("IO", "active", fields=fields))
    off = mod.parse_hikvision_alert(_hik("IO", "inactive", fields=fields))
    assert on.event_type == "alarm_input"
    assert off.event_type == "alarm_input_end"
    for alarm in (on, off):
        assert alarm.channel is None
        assert alarm.payload["recorder_scoped"] is True
        assert alarm.payload["native_input"] == "3"
    assert off.pair_key == on.burst_key


@pytest.mark.parametrize("mod", PARSERS)
def test_hikvision_heartbeat_videoloss_is_still_not_an_event(mod):
    for state in ("active", "inactive"):
        for count in ("0", None):
            assert mod.parse_hikvision_alert(
                _hik("videoloss", state, count, "<channelID>1</channelID>")) is None


@pytest.mark.parametrize("mod", PARSERS)
@pytest.mark.parametrize("etype", ["VMD", "linedetection", "fielddetection", "facedetection",
                                   "someVendorThing"])
def test_hikvision_end_of_a_detection_is_not_an_occurrence(mod, etype):
    assert mod.parse_hikvision_alert(_hik(etype, "inactive",
                                          fields="<channelID>2</channelID>")) is None


# --- Dahua -----------------------------------------------------------------------------

@pytest.mark.parametrize("mod", PARSERS)
@pytest.mark.parametrize("code,start,end,channel", [
    ("VideoLoss", "video_loss", "video_restore", "3"),
    ("VideoBlind", "tamper", "tamper_end", "3"),
    ("AlarmLocal", "alarm_input", "alarm_input_end", None),
])
def test_dahua_stop_is_the_end_of_a_fault(mod, code, start, end, channel):
    on = mod.parse_dahua_block(f"Code={code};action=Start;index=2")
    off = mod.parse_dahua_block(f"Code={code};action=Stop;index=2")
    assert (on.event_type, on.channel, on.phase) == (start, channel, "start")
    assert (off.event_type, off.channel, off.phase) == (end, channel, "end")
    assert off.pair_key == on.burst_key
    assert off.payload["action"] == "stop"
    if channel is None:
        assert off.payload["recorder_scoped"] is True
        assert off.payload["native_index"] == "2"


@pytest.mark.parametrize("mod", PARSERS)
def test_dahua_state_keepalive_and_other_stops_are_not_events(mod):
    for code in ("VideoLoss", "VideoBlind", "AlarmLocal", "VideoMotion"):
        assert mod.parse_dahua_block(f"Code={code};action=State;index=0") is None
    for code in ("VideoMotion", "CrossLineDetection", "SmartMotionHuman", "NetAbort"):
        assert mod.parse_dahua_block(f"Code={code};action=Stop;index=0") is None


# --- burst / pairing -------------------------------------------------------------------

def _seq(burst, alarms_at):
    return [burst.admit(a.burst_key, t, pair=a.pair_key, phase=a.phase) for a, t in alarms_at]


@pytest.mark.parametrize("mod", PARSERS)
def test_burst_never_suppresses_a_restore_and_rearms_the_loss(mod):
    loss = mod.parse_dahua_block("Code=VideoLoss;action=Start;index=0")
    restore = mod.parse_dahua_block("Code=VideoLoss;action=Stop;index=0")
    burst = mod.BurstFilter()
    admitted = _seq(burst, [
        (loss, 100.0),      # loss
        (loss, 101.0),      # its repeat: collapsed
        (restore, 102.0),   # restore right after: admitted
        (restore, 103.0),   # a repeat of the end: not a new occurrence
        (loss, 104.0),      # lost again inside the old window: a NEW loss
        (restore, 105.0),   # and its restore
    ])
    assert admitted == [True, False, True, False, True, True]


@pytest.mark.parametrize("mod", PARSERS)
def test_an_end_with_no_start_seen_is_admitted_once(mod):
    """A restore of a loss that began before the Agent started is still reported."""
    restore = mod.parse_hikvision_alert(_hik("videoloss", "inactive", "5",
                                             "<channelID>6</channelID>"))
    burst = mod.BurstFilter()
    assert _seq(burst, [(restore, 0.0), (restore, 60.0), (restore, 600.0)]) == \
        [True, False, False]


@pytest.mark.parametrize("mod", PARSERS)
def test_ends_pair_per_channel_and_per_fault(mod):
    burst = mod.BurstFilter()
    a = [mod.parse_dahua_block(f"Code={c};action={x};index={i}")
         for c, x, i in (("VideoLoss", "Start", 0), ("VideoBlind", "Start", 0),
                         ("VideoLoss", "Stop", 0), ("VideoLoss", "Stop", 1),
                         ("VideoBlind", "Stop", 0))]
    assert _seq(burst, [(x, float(n)) for n, x in enumerate(a)]) == [True] * 5


@pytest.mark.parametrize("mod", PARSERS)
def test_forget_undoes_an_end_admission(mod):
    restore = mod.parse_dahua_block("Code=VideoBlind;action=Stop;index=0")
    burst = mod.BurstFilter()
    assert _seq(burst, [(restore, 5.0)]) == [True]
    burst.forget(restore.burst_key, 5.0)
    assert _seq(burst, [(restore, 6.0)]) == [True]


# --- the drivers and the push bridge ---------------------------------------------------

def test_hikvision_driver_emits_loss_then_restore():
    d = NativeHikvisionDriver("http://127.0.0.1", "u", "p", timeout=1)
    d._utc_offset_asked = 0.0            # never ask a recorder in a unit test
    d._received = (1000.0, alarm_parsing.parse_iso_time("2026-10-04T16:00:00+00:00"))
    ch = "<channelID>2</channelID>"
    loss = d._parse_alert(_hik("videoloss", "active", "1", ch))
    d._received = (1001.0, d._received[1])
    restore = d._parse_alert(_hik("videoloss", "inactive", "2", ch))
    d._received = (1002.0, d._received[1])
    again = d._parse_alert(_hik("videoloss", "inactive", "3", ch))
    assert (loss.event_type, restore.event_type, again) == ("video_loss", "video_restore", None)
    assert restore.channel == "2"
    assert restore.payload["source"] == "recorder_event"


def test_dahua_driver_emits_tamper_then_tamper_end():
    d = NativeDahuaDriver("http://127.0.0.1", "u", "p", timeout=1)
    out = [d._parse_line(f"Code=VideoBlind;action={a};index=0")
           for a in ("Start", "State", "Stop", "Stop")]
    assert [e.event_type if e else None for e in out] == ["tamper", None, "tamper_end", None]
    assert out[2].channel == "1"


def test_push_bridge_delivers_restore_right_after_loss():
    sent = []
    original = pb._rpc
    pb._rpc = lambda fn, payload: (sent.append(payload["p_events"][0]["event_type"]) or True, "{}")
    try:
        pb.BURST = pb.BurstFilter()
        for body, t in ((b"Code=VideoLoss;action=Start;index=0", 10.0),
                        (b"Code=VideoLoss;action=Stop;index=0", 11.0),
                        (b"Code=VideoLoss;action=Stop;index=0", 12.0),
                        (b"Code=VideoLoss;action=Start;index=0", 13.0)):
            vendor, ev = pb.parse_any(body, "text/plain")
            assert pb.deliver("tokA", vendor, ev, mono=t) == 200
            assert "_pair" not in ev and "_phase" not in ev
    finally:
        pb._rpc = original
    assert sent == ["video_loss", "video_restore", "video_loss"]


def test_push_bridge_hikvision_restore_matches_the_agent():
    raw = _hik("tamperdetection", "inactive", "4", "<channelID>7</channelID>")
    bridge = pb.parse_hikvision(raw, "application/xml")
    d = NativeHikvisionDriver("http://127.0.0.1", "u", "p", timeout=1)
    agent = d._parse_alert(raw)
    assert (bridge["event_type"], bridge["channel"]) == (agent.event_type, agent.channel) \
        == ("tamper_end", "7")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
