#!/usr/bin/env python3
"""MNVR-028 (Hikvision alarm inputs) on the shared alarm parser (MNVR-026).

An ISAPI IO alert (<channelID>1</channelID><inputIOPortID>3</inputIOPortID>) names a
recorder alarm input, not a video input. 5.0.28 scoped it to the recorder inside the
driver's own parser; on the multi-recorder Agent the driver and the push bridge share
alarm_parsing, so the rule lives there and both paths apply it: "io" alerts and any
alert carrying inputIOPortID have channel None, recorder_scoped, native_input = the
port, and bursts collapse per port.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "bridge"))

from drivers import alarm_parsing  # noqa: E402
from drivers.native_recorder import NativeHikvisionDriver  # noqa: E402
import push_bridge as pb  # noqa: E402


def _bridge_copy():
    spec = importlib.util.spec_from_file_location(
        "bridge_alarm_parsing_copy", ROOT / "bridge" / "alarm_parsing.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod      # dataclasses resolve their module by name
    spec.loader.exec_module(mod)
    return mod


def _alert(etype: str, fields: str = "") -> bytes:
    return f"""<EventNotificationAlert xmlns="http://www.hikvision.com/ver20/XMLSchema">
      <eventType>{etype}</eventType><eventState>active</eventState>
      {fields}<dateTime>2026-10-04T21:00:00+05:00</dateTime>
      <activePostCount>1</activePostCount>
    </EventNotificationAlert>""".encode()


IO_ALERT = _alert("IO", "<channelID>1</channelID><inputIOPortID>3</inputIOPortID>")
NEW_INPUT = _alert("someNewInputAlarm", "<channelID>2</channelID><inputIOPortID>5</inputIOPortID>")


def test_shared_parser_scopes_io_alert_to_the_recorder_with_the_port():
    for mod in (alarm_parsing, _bridge_copy()):
        alarm = mod.parse_hikvision_alert(IO_ALERT)
        assert alarm is not None
        assert alarm.channel is None
        assert alarm.payload["recorder_scoped"] is True
        assert alarm.payload["native_input"] == "3"
        assert alarm.payload["native_channel"] == "1"
        assert "io" in mod.HIK_RECORDER_SCOPED_TYPES


def test_shared_parser_scopes_any_alert_carrying_an_input_port():
    alarm = alarm_parsing.parse_hikvision_alert(NEW_INPUT)
    assert alarm.channel is None
    assert alarm.payload["recorder_scoped"] is True
    assert alarm.payload["native_input"] == "5"


def test_shared_parser_bursts_alarm_inputs_per_port():
    one = alarm_parsing.parse_hikvision_alert(
        _alert("IO", "<channelID>1</channelID><inputIOPortID>1</inputIOPortID>"))
    two = alarm_parsing.parse_hikvision_alert(
        _alert("IO", "<channelID>1</channelID><inputIOPortID>2</inputIOPortID>"))
    assert one.burst_key != two.burst_key
    burst = alarm_parsing.BurstFilter()
    assert burst.admit(one.burst_key, 0.0)
    assert burst.admit(two.burst_key, 1.0)
    assert not burst.admit(one.burst_key, 2.0)


def test_push_bridge_scopes_alarm_inputs_like_the_agent():
    for raw in (IO_ALERT, NEW_INPUT):
        bridge = pb.parse_hikvision(raw, "application/xml")
        d = NativeHikvisionDriver("http://127.0.0.1", "u", "p", timeout=1)
        try:
            agent = d._parse_alert(raw)
        finally:
            d.close()
        assert bridge["channel"] is None and agent.channel is None
        assert bridge["payload"]["recorder_scoped"] is True
        assert bridge["payload"]["native_input"] == agent.payload["native_input"]
        assert bridge["event_type"] == agent.event_type


def test_camera_alert_without_an_input_port_keeps_its_channel():
    alarm = alarm_parsing.parse_hikvision_alert(_alert("VMD", "<channelID>4</channelID>"))
    assert alarm.channel == "4"
    assert "recorder_scoped" not in alarm.payload and "native_input" not in alarm.payload


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
