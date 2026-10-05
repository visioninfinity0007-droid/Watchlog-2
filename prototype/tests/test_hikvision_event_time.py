#!/usr/bin/env python3
"""MNVR-024 (Hikvision): alert time provenance is explicit, never a silent UTC guess.

ISAPI dateTime sometimes carries an offset and sometimes does not. A naive value used to
be stamped as UTC, so a recorder in PKT reporting 21:00 stored the event at 21:00Z, five
hours in the future; a missing value silently became the agent's clock; nothing compared
the recorder's stamp with the time the alert arrived.

Now a naive time is localised with the UTC offset the recorder itself states
(/ISAPI/System/time) when that is unambiguous, otherwise the event takes the agent's
receive time and says so (clock_source), keeping the recorder's raw text. A recorder
stamp far from the receive time is kept but flagged (clock_skew_seconds). The stated
offset is read again after CLOCK_OFFSET_RETRY_SECONDS (a DST change moves it), and a bare
POSIX zone with dstEnabled=true is not taken as the offset.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))

from drivers.base import DriverError  # noqa: E402
from drivers.hikvision import CLOCK_OFFSET_RETRY_SECONDS  # noqa: E402
from drivers.native_recorder import NativeHikvisionDriver  # noqa: E402

RECEIVED = datetime(2026, 10, 4, 16, 0, 5, tzinfo=timezone.utc)


class Recorder(NativeHikvisionDriver):
    """Serves /ISAPI/System/time from a fixture and counts how often it is asked."""

    def __init__(self, time_doc: str | None):
        super().__init__("http://127.0.0.1", "u", "p", timeout=1)
        self.time_doc = time_doc
        self.time_reads = 0

    def _xml(self, path):
        if path == "/ISAPI/System/time":
            self.time_reads += 1
            if self.time_doc is None:
                raise DriverError("HTTP 404")
            return ET.fromstring(self.time_doc)
        raise DriverError("unexpected path " + path)


def _time_doc(local_time: str, zone: str) -> str:
    return (f"<Time><timeMode>NTP</timeMode><localTime>{local_time}</localTime>"
            f"<timeZone>{zone}</timeZone></Time>")


def _alert(date_time: str | None, channel: str = "1") -> bytes:
    stamp = f"<dateTime>{date_time}</dateTime>" if date_time is not None else ""
    return f"""<EventNotificationAlert>
      <eventType>VMD</eventType><eventState>active</eventState>
      <channelID>{channel}</channelID>{stamp}
      <activePostCount>1</activePostCount>
    </EventNotificationAlert>""".encode()


def _parse(driver, raw: bytes, mono: float = 1000.0, received: datetime = RECEIVED):
    driver._received = (mono, received)
    return driver._parse_alert(raw)


def test_offset_time_is_the_recorder_clock_and_needs_no_lookup():
    d = Recorder(None)
    try:
        ev = _parse(d, _alert("2026-10-04T21:00:00+05:00"))
        assert ev.device_ts == datetime(2026, 10, 4, 16, 0, 0, tzinfo=timezone.utc)
        assert ev.payload["clock_source"] == "recorder"
        assert "clock_skew_seconds" not in ev.payload
        assert d.time_reads == 0
    finally:
        d.close()


def test_naive_time_is_localised_with_the_recorders_stated_offset():
    d = Recorder(_time_doc("2026-10-04T21:00:03+05:00", "CST-5:00:00"))
    try:
        ev = _parse(d, _alert("2026-10-04T21:00:00"))
        assert ev.device_ts == datetime(2026, 10, 4, 16, 0, 0, tzinfo=timezone.utc)
        assert ev.payload["clock_source"] == "recorder_local"
        # Cached: a second naive alert does not ask the recorder again.
        ev2 = _parse(d, _alert("2026-10-04T21:00:40", channel="2"), mono=1040.0,
                     received=RECEIVED + timedelta(seconds=40))
        assert ev2.device_ts == datetime(2026, 10, 4, 16, 0, 40, tzinfo=timezone.utc)
        assert d.time_reads == 1
    finally:
        d.close()


def test_stated_offset_is_re_read_not_trusted_for_the_life_of_the_driver():
    # A stream reopened on the same driver lives for weeks; the offset moves at a DST change.
    d = Recorder(_time_doc("2026-10-04T21:00:03+05:00", "CST-5:00:00"))
    try:
        ev = _parse(d, _alert("2026-10-04T21:00:00"))
        assert ev.device_ts == datetime(2026, 10, 4, 16, 0, 0, tzinfo=timezone.utc)
        d.time_doc = _time_doc("2026-10-04T22:10:03+06:00", "CST-6:00:00")
        later = 1000.0 + CLOCK_OFFSET_RETRY_SECONDS + 1
        ev = _parse(d, _alert("2026-10-04T22:10:00", channel="2"), mono=later,
                    received=RECEIVED + timedelta(seconds=CLOCK_OFFSET_RETRY_SECONDS + 1))
        assert d.time_reads == 2
        assert ev.device_ts == datetime(2026, 10, 4, 16, 10, 0, tzinfo=timezone.utc)
        assert ev.payload["clock_source"] == "recorder_local"
        assert "clock_skew_seconds" not in ev.payload
        # A re-read that fails leaves the offset unknown, not the old value.
        d.time_doc = None
        ev = _parse(d, _alert("2026-10-04T22:20:00", channel="3"),
                    mono=later + CLOCK_OFFSET_RETRY_SECONDS + 1, received=RECEIVED)
        assert d.time_reads == 3
        assert ev.payload["clock_source"] == "agent_receive"
        assert ev.payload["device_time_raw"] == "2026-10-04T22:20:00"
    finally:
        d.close()


def test_dst_enabled_zone_is_not_a_dst_free_offset_for_a_naive_time():
    doc = ("<Time><timeMode>NTP</timeMode><localTime>{}</localTime>"
           "<timeZone>CST-5:00:00</timeZone><dstEnabled>true</dstEnabled></Time>")
    d = Recorder(doc.format("2026-10-04T21:00:03"))
    try:
        ev = _parse(d, _alert("2026-10-04T21:00:00"))
        assert ev.device_ts == RECEIVED
        assert ev.payload["clock_source"] == "agent_receive"
        assert ev.payload["device_time_raw"] == "2026-10-04T21:00:00"
    finally:
        d.close()
    # localTime with an explicit offset already includes any DST: it is still used.
    d = Recorder(doc.format("2026-10-04T21:00:03+05:00"))
    try:
        ev = _parse(d, _alert("2026-10-04T21:00:00"))
        assert ev.device_ts == datetime(2026, 10, 4, 16, 0, 0, tzinfo=timezone.utc)
        assert ev.payload["clock_source"] == "recorder_local"
    finally:
        d.close()


def test_posix_zone_without_dst_is_used_when_local_time_is_naive():
    d = Recorder(_time_doc("2026-10-04T21:00:03", "CST-5:00:00"))
    try:
        ev = _parse(d, _alert("2026-10-04T21:00:00"))
        assert ev.device_ts == datetime(2026, 10, 4, 16, 0, 0, tzinfo=timezone.utc)
        assert ev.payload["clock_source"] == "recorder_local"
    finally:
        d.close()


def test_naive_time_with_unknown_offset_takes_receive_time_and_says_so():
    zones = ("CST+6:00:00DST01:00:00,M3.2.0/02:00:00,M11.1.0/02:00:00",   # DST rule: ambiguous
             "")
    for zone in zones:
        d = Recorder(_time_doc("2026-10-04T21:00:03", zone))
        try:
            ev = _parse(d, _alert("2026-10-04T21:00:00"))
            assert ev.device_ts == RECEIVED, zone
            assert ev.payload["clock_source"] == "agent_receive"
            assert ev.payload["device_time_raw"] == "2026-10-04T21:00:00"
        finally:
            d.close()


def test_unreadable_recorder_clock_is_not_asked_on_every_alert():
    d = Recorder(None)
    try:
        for i in range(4):
            ev = _parse(d, _alert("2026-10-04T21:00:00", channel=str(i + 1)),
                        mono=1000.0 + i, received=RECEIVED + timedelta(seconds=i))
            assert ev.device_ts == RECEIVED + timedelta(seconds=i)
            assert ev.payload["clock_source"] == "agent_receive"
        assert d.time_reads == 1
    finally:
        d.close()


def test_missing_or_garbled_time_takes_receive_time():
    d = Recorder(None)
    try:
        ev = _parse(d, _alert(None))
        assert ev.device_ts == RECEIVED
        assert ev.payload["clock_source"] == "agent_receive"
        ev = _parse(d, _alert("not-a-time", channel="2"), mono=1001.0)
        assert ev.device_ts == RECEIVED
        assert ev.payload["device_time_raw"] == "not-a-time"
    finally:
        d.close()


def test_recorder_clock_far_from_receive_time_is_flagged_not_trusted_silently():
    d = Recorder(None)
    try:
        ev = _parse(d, _alert("2026-10-04T21:10:05+05:00"))    # 10 min ahead of arrival
        assert ev.device_ts == datetime(2026, 10, 4, 16, 10, 5, tzinfo=timezone.utc)
        assert ev.payload["clock_source"] == "recorder"
        assert ev.payload["clock_skew_seconds"] == 600
    finally:
        d.close()


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
