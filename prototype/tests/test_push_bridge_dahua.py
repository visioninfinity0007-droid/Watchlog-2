#!/usr/bin/env python3
"""
Push-bridge Dahua parser tests (PC-free / recorder-push path).

The bridge turns a recorder's native alarm POST into a WatchLog event with no
agent on site. Hikvision was already covered; this pins Dahua, which is what the
Al-Khalid fleet actually runs (DH-XVR1B08-I).

Dahua posts the same ``Code=VideoMotion;action=Start;index=0`` vocabulary it
streams over eventManager attach, so these tests also pin PARITY with
``drivers/dahua.py`` — if the two ever disagree, the same alarm would mean two
different things depending on whether it arrived via the agent or via push.

    pytest -q prototype/tests/test_push_bridge_dahua.py
"""

from __future__ import annotations

import base64
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bridge"))
sys.path.insert(0, str(ROOT / "agent"))

import push_bridge as pb  # noqa: E402

JPEG = bytes([0xFF, 0xD8]) + b"fake-jpeg-body" + bytes([0xFF, 0xD9])


def multipart(text: bytes, jpeg: bytes | None, boundary: str = "AaB03x") -> tuple:
    body = b"--" + boundary.encode() + b"\r\nContent-Type: text/plain\r\n\r\n" + text + b"\r\n"
    if jpeg:
        body += (b"--" + boundary.encode()
                 + b"\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n")
    body += b"--" + boundary.encode() + b"--\r\n"
    return body, f"multipart/form-data; boundary={boundary}"


class DahuaParserTests(unittest.TestCase):
    def test_plain_alarm_is_parsed(self):
        ev = pb.parse_dahua(b"Code=VideoMotion;action=Start;index=0", "text/plain")
        self.assertIsNotNone(ev)
        self.assertEqual("motion", ev["event_type"])

    def test_wire_channel_is_zero_based_and_converted(self):
        """index=0 is channel 1. Getting this wrong files events against the
        wrong camera, which on a security product is worse than dropping them."""
        for index, expected in (("0", "1"), ("3", "4"), ("7", "8")):
            ev = pb.parse_dahua(
                f"Code=VideoMotion;action=Start;index={index}".encode(), "text/plain")
            self.assertEqual(expected, ev["channel"], f"index={index}")

    def test_stop_and_state_are_not_occurrences(self):
        for action in ("Stop", "State"):
            self.assertIsNone(
                pb.parse_dahua(f"Code=VideoMotion;action={action};index=0".encode(),
                               "text/plain"), action)

    def test_keepalive_chatter_is_ignored(self):
        for code in ("Heartbeat", "KeepAlive", "TimeChange", "NTPAdjustTime"):
            self.assertIsNone(
                pb.parse_dahua(f"Code={code};action=Start;index=0".encode(),
                               "text/plain"), code)

    def test_smart_events_map_to_our_vocabulary(self):
        for code, expected in (("SmartMotionHuman", "person"),
                               ("SmartMotionVehicle", "vehicle"),
                               ("CrossLineDetection", "line_crossing"),
                               ("CrossRegionDetection", "intrusion"),
                               ("VideoBlind", "tamper"),
                               ("VideoLoss", "video_loss")):
            ev = pb.parse_dahua(f"Code={code};action=Start;index=0".encode(), "text/plain")
            self.assertEqual(expected, ev["event_type"], code)

    def test_unknown_code_is_kept_under_its_own_name_not_dropped(self):
        ev = pb.parse_dahua(b"Code=SomeNewThing;action=Start;index=0", "text/plain")
        self.assertEqual("somenewthing", ev["event_type"])

    def test_data_payload_containing_semicolons_does_not_break_parsing(self):
        ev = pb.parse_dahua(
            b'Code=VideoMotion;action=Start;index=2;data={"a":1;"b":2}', "text/plain")
        self.assertEqual("motion", ev["event_type"])
        self.assertEqual("3", ev["channel"])

    def test_json_bodied_firmware_is_parsed(self):
        ev = pb.parse_dahua(b'{"Code":"VideoMotion","action":"Start","index":1}',
                            "application/json")
        self.assertIsNotNone(ev)
        self.assertEqual("motion", ev["event_type"])
        self.assertEqual("2", ev["channel"])

    def test_multipart_snapshot_is_attached(self):
        body, ctype = multipart(b"Code=VideoMotion;action=Start;index=0", JPEG)
        ev = pb.parse_dahua(body, ctype)
        self.assertIsNotNone(ev)
        self.assertEqual(JPEG, base64.b64decode(ev["snapshot_b64"]))

    def test_a_body_with_no_code_is_not_an_event(self):
        for junk in (b"", b"hello world", b"<html>login</html>", b'{"ok":true}'):
            self.assertIsNone(pb.parse_dahua(junk, "text/plain"), junk)


class ParserParityTests(unittest.TestCase):
    """The push path and the agent's attach path must agree on what an alarm means."""

    def test_bridge_map_covers_every_code_the_driver_knows(self):
        try:
            import drivers.dahua as dd
        except Exception as exc:  # noqa: BLE001 - driver needs requests
            self.skipTest(f"dahua driver not importable here: {type(exc).__name__}")
        missing = {code: name for code, name in dd.EVENT_CODE_MAP.items()
                   if pb.DAHUA_EVENT_MAP.get(code) != name}
        self.assertFalse(
            missing,
            "bridge and driver disagree on these Dahua codes — the same alarm would "
            f"mean different things via push vs agent: {missing}")


class DispatchTests(unittest.TestCase):
    """parse_any must identify the vendor, and never guess."""

    HIK = (b'<?xml version="1.0" encoding="UTF-8"?>'
           b'<EventNotificationAlert xmlns="http://www.hikvision.com/ver20/XMLSchema">'
           b'<channelID>2</channelID><dateTime>2026-09-16T10:00:00+05:00</dateTime>'
           b'<eventType>VMD</eventType><eventState>active</eventState>'
           b'<activePostCount>1</activePostCount></EventNotificationAlert>')

    def test_hikvision_body_routes_to_hikvision(self):
        vendor, ev = pb.parse_any(self.HIK, "application/xml")
        self.assertEqual("hikvision", vendor)
        self.assertEqual("motion", ev["event_type"])

    def test_dahua_body_routes_to_dahua(self):
        vendor, ev = pb.parse_any(b"Code=VideoMotion;action=Start;index=0", "text/plain")
        self.assertEqual("dahua", vendor)
        self.assertEqual("motion", ev["event_type"])

    def test_neither_parser_claims_the_other_vendors_body(self):
        self.assertIsNone(pb.parse_dahua(self.HIK, "application/xml"))
        self.assertIsNone(
            pb.parse_hikvision(b"Code=VideoMotion;action=Start;index=0", "text/plain"))

    def test_unrecognised_push_yields_no_event_rather_than_a_guess(self):
        for junk in (b"", b"random bytes", b"<html>hi</html>", JPEG):
            vendor, ev = pb.parse_any(junk, "text/plain")
            self.assertIsNone(ev, junk[:20])
            self.assertIsNone(vendor)

    def test_a_parser_that_raises_cannot_take_the_bridge_down(self):
        def boom(body, content_type):
            raise RuntimeError("bad parser")

        original = pb.PARSERS
        pb.PARSERS = (("exploding", boom),) + original
        try:
            vendor, ev = pb.parse_any(b"Code=VideoMotion;action=Start;index=0", "text/plain")
        finally:
            pb.PARSERS = original
        self.assertEqual("dahua", vendor, "a raising parser must not block the others")
        self.assertIsNotNone(ev)


class DiagnosticTests(unittest.TestCase):
    """Field-validating a new model means seeing what it really sent."""

    def test_description_shows_the_text_and_flags_the_jpeg_without_dumping_it(self):
        body, ctype = multipart(b"Code=MysteryEvent;action=Start;index=0", JPEG)
        desc = pb.describe_unparsed(body, ctype)
        self.assertIn("MysteryEvent", desc)
        self.assertIn("jpeg=yes", desc)
        self.assertNotIn("fake-jpeg-body", desc, "image bytes must not reach the log")

    def test_description_is_bounded(self):
        desc = pb.describe_unparsed(b"A" * 100000, "text/plain")
        self.assertLess(len(desc), 1000, "an unbounded dump would flood the logs")
        self.assertIn("more bytes", desc)

    def test_binary_only_body_is_described_not_decoded(self):
        self.assertIn("binary jpeg", pb.describe_unparsed(JPEG, "image/jpeg"))


if __name__ == "__main__":
    unittest.main(verbosity=1)


class LivenessChatterTests(unittest.TestCase):
    """0.4.11 — recorder keep-alives are not alarms, but they ARE proof of life.

    A Dahua recorder cannot be made to emit a periodic heartbeat, so a PC-free site with
    a quiet night writes nothing and WatchLog cannot tell "quiet" from "unplugged". The
    bridge used to DROP the chatter that firmware does send, throwing away the only
    liveness signal available between alarms. It is now recorded as liveness only -- it
    must never become an event."""

    def test_keepalive_chatter_is_recognised_as_liveness(self):
        for code in ("Heartbeat", "KeepAlive", "TimeChange", "NTPAdjustTime"):
            body = f"Code={code};action=Start;index=0".encode()
            self.assertTrue(pb.is_liveness_chatter(body, "text/plain"), code)
            # and it must still NOT parse as an event
            self.assertIsNone(pb.parse_dahua(body, "text/plain"), code)

    def test_a_real_alarm_is_not_treated_as_mere_liveness(self):
        body = b"Code=VideoMotion;action=Start;index=0"
        self.assertFalse(pb.is_liveness_chatter(body, "text/plain"),
                         "a real alarm must take the event path, not the liveness path")

    def test_unrelated_junk_is_not_liveness(self):
        for junk in (b"", b"hello world", b"<html>login</html>", b'{"ok":true}'):
            self.assertFalse(pb.is_liveness_chatter(junk, "text/plain"), junk)

    def test_liveness_calls_the_liveness_rpc_not_the_ingest_rpc(self):
        """It must never insert an event from a keep-alive."""
        seen = {}

        def fake_rpc(fn, payload):
            seen["fn"] = fn
            return True, "ok"

        original = pb._rpc
        pb._rpc = fake_rpc
        try:
            pb.liveness("tok123")
        finally:
            pb._rpc = original
        self.assertEqual("wl_push_liveness", seen["fn"])


class RouteMatchingTests(unittest.TestCase):
    """The route regex matched the QUERY STRING, so firmware posting
    /push/<token>?action=alarm was 404'd and every alarm silently rejected."""

    def test_a_query_string_does_not_break_the_route(self):
        import re
        for target in ("/push/abc123", "/push/abc123/", "/push/abc123?action=alarm",
                       "/push/abc123?action=alarm&channel=0"):
            route = target.split("?", 1)[0]
            self.assertIsNotNone(re.match(r"/push/([A-Za-z0-9]+)/?$", route), target)

    def test_the_handler_strips_the_query_before_matching(self):
        src = (ROOT / "bridge" / "push_bridge.py").read_text(encoding="utf-8")
        body = src[src.find("def do_POST"):][:600]
        self.assertIn('self.path.split("?", 1)[0]', body,
                      "BaseHTTPRequestHandler.path includes the query string")
