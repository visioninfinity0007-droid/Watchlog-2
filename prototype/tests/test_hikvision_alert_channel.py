#!/usr/bin/env python3
"""MNVR-028 (Hikvision): an alert is never attributed to a guessed camera.

The channel used to be channelID, else dynChannelID, else channelName, else "1". A disk
alert with no channel became camera 1's event, and a name-only alert became channel
"Main Gate", which joins no camera and lands in a bogus dedupe namespace. Recorder-level
alert types are now recorder-scoped (channel None plus recorder_scoped), a camera alert
without a channel id has channel None plus channel_unknown, and channelName is kept in the
payload only.
"""
from __future__ import annotations

import sys
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))

from drivers.native_recorder import NativeHikvisionDriver  # noqa: E402


def _alert(etype: str, fields: str = "") -> bytes:
    return f"""<EventNotificationAlert>
      <eventType>{etype}</eventType><eventState>active</eventState>
      {fields}<dateTime>2026-10-04T21:00:00+05:00</dateTime>
      <activePostCount>1</activePostCount>
    </EventNotificationAlert>""".encode()


def _parse(raw: bytes):
    d = NativeHikvisionDriver("http://127.0.0.1", "u", "p", timeout=1)
    try:
        return d._parse_alert(raw)
    finally:
        d.close()


def test_disk_alert_without_channel_is_recorder_scoped_not_camera_1():
    ev = _parse(_alert("diskfull"))
    assert ev.channel is None
    assert ev.event_type == "disk_full"
    assert ev.payload["recorder_scoped"] is True


def test_recorder_level_types_ignore_a_channel_field():
    for etype in ("diskerror", "illAccess", "ipConflict", "nicBroken"):
        ev = _parse(_alert(etype, "<channelID>1</channelID>"))
        assert ev.channel is None, etype
        assert ev.payload["recorder_scoped"] is True
        assert ev.payload["native_channel"] == "1"


def test_name_only_alert_has_unknown_channel_and_keeps_the_name_in_payload():
    ev = _parse(_alert("VMD", "<channelName>Main Gate</channelName>"))
    assert ev.channel is None
    assert ev.payload["channel_unknown"] is True
    assert ev.payload["channelName"] == "Main Gate"
    assert "recorder_scoped" not in ev.payload


def test_camera_alerts_keep_their_channel_id():
    ev = _parse(_alert("VMD", "<channelID>3</channelID><channelName>Till</channelName>"))
    assert ev.channel == "3"
    assert "channel_unknown" not in ev.payload and "recorder_scoped" not in ev.payload
    ev = _parse(_alert("linedetection", "<dynChannelID>5</dynChannelID>"))
    assert ev.channel == "5"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
