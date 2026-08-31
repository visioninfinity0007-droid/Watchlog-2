#!/usr/bin/env python3
"""
Push-bridge parser tests.

The bridge turns a recorder's native alarm POST into a WatchLog event. If
it mis-parses, we either drop real events or invent fake ones — both bad —
so the parser is pinned here against the exact shapes Hikvision sends: a
bare XML alert, and the multipart form with a JPEG attached. Namespaced
XML (Hikvision uses one) and keep-alive/inactive alerts are covered too.

    python prototype/tests/test_push_bridge.py
    pytest -q prototype/tests/test_push_bridge.py
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
import push_bridge as pb          # noqa: E402


ALERT_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<EventNotificationAlert xmlns="http://www.hikvision.com/ver20/XMLSchema">
  <ipAddress>192.168.1.108</ipAddress>
  <channelID>3</channelID>
  <dateTime>2026-08-29T21:14:07+05:00</dateTime>
  <activePostCount>1</activePostCount>
  <eventType>linedetection</eventType>
  <eventState>active</eventState>
  <eventDescription>Line Crossing detected</eventDescription>
</EventNotificationAlert>"""

KEEPALIVE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<EventNotificationAlert xmlns="http://www.hikvision.com/ver20/XMLSchema">
  <channelID>1</channelID>
  <dateTime>2026-08-29T21:15:00+05:00</dateTime>
  <eventType>videoloss</eventType>
  <eventState>inactive</eventState>
</EventNotificationAlert>"""

NOT_AN_ALERT = b"<?xml version='1.0'?><Something><x>1</x></Something>"

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


@case("bare XML line-crossing alert parses to a mapped event")
def t_line():
    ev = pb.parse_hikvision(ALERT_XML, "application/xml")
    assert ev is not None, "returned None on a valid alert"
    assert ev["event_type"] == "line_crossing", ev["event_type"]
    assert ev["channel"] == "3", ev["channel"]
    assert ev["device_ts"].startswith("2026-08-29T16:14:07"), ev["device_ts"]
    assert "snapshot_b64" not in ev
    return f"{ev['event_type']} ch{ev['channel']} @ {ev['device_ts'][:19]}Z"


@case("multipart with a JPEG attaches the snapshot")
def t_multipart():
    jpeg = bytes([0xFF, 0xD8, 0xFF, 0xE0]) + b"fake-jpeg-body" * 8
    boundary = "MIME_boundary"
    body = (
        f"--{boundary}\r\nContent-Type: application/xml\r\n\r\n".encode()
        + ALERT_XML + b"\r\n"
        + f"--{boundary}\r\nContent-Type: image/jpeg\r\n\r\n".encode()
        + jpeg + b"\r\n"
        + f"--{boundary}--\r\n".encode()
    )
    ev = pb.parse_hikvision(body, f"multipart/form-data; boundary={boundary}")
    assert ev is not None, "returned None on a valid multipart alert"
    assert ev["event_type"] == "line_crossing"
    assert "snapshot_b64" in ev, "JPEG part was not attached"
    assert base64.b64decode(ev["snapshot_b64"])[:2] == bytes([0xFF, 0xD8]), \
        "snapshot is not a JPEG"
    return "event + snapshot recovered from multipart"


@case("an inactive keep-alive is dropped, not recorded")
def t_keepalive():
    ev = pb.parse_hikvision(KEEPALIVE_XML, "application/xml")
    assert ev is None, "a keep-alive was turned into an event"
    return "dropped"


@case("a non-alert POST is refused")
def t_not_alert():
    assert pb.parse_hikvision(NOT_AN_ALERT, "application/xml") is None
    assert pb.parse_hikvision(b"garbage", "application/xml") is None
    return "None on non-alert and on garbage"


@case("an unknown eventType is kept under its own name, not dropped")
def t_unknown_type():
    xml = ALERT_XML.replace(b"linedetection", b"someNewThing")
    ev = pb.parse_hikvision(xml, "application/xml")
    assert ev is not None and ev["event_type"] == "somenewthing", ev
    return "kept as 'somenewthing'"


def run():
    print("Push-bridge parser")
    print("=" * 60)
    p = f = 0
    for name, fn in CASES:
        try:
            print(f"  PASS  {name}\n          {fn()}"); p += 1
        except AssertionError as e:
            print(f"  FAIL  {name}\n          {e}"); f += 1
        except Exception as e:                          # noqa: BLE001
            print(f"  ERROR {name}\n          {type(e).__name__}: {e}"); f += 1
    print("=" * 60)
    print(f"  {p} passed, {f} failed")
    return 1 if f else 0


def test_push_bridge():
    assert run() == 0


if __name__ == "__main__":
    sys.exit(run())
