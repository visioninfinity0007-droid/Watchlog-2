#!/usr/bin/env python3
"""
WatchLog push bridge — translates a recorder's native alarm POST into a
wl_ingest_push call.

Why it exists: recorders can POST an event to an HTTP endpoint, but each
speaks its own format (Hikvision posts EventNotificationAlert XML, often
multipart with a JPEG part; Dahua/ONVIF differ). wl_ingest_push wants
clean JSON. This small service sits between them: it receives the NVR's
POST at /push/<token>, parses out (event type, channel, time, snapshot),
and calls wl_ingest_push with that token.

It holds NO secret. The token in the URL is the site's credential; the
Supabase publishable key is public by design. Deploy it on the same
Coolify host as everything else, behind TLS.

    python bridge/push_bridge.py --port 8620

Env: SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY.

STATUS: the Hikvision parser is validated by unit tests against real
EventNotificationAlert samples. Dahua and ONVIF push formats are
model-dependent and must be validated against a real unit before relying
on them — the parser returns None for anything it does not confidently
understand, and the bridge answers 202 without inventing an event, so an
unrecognised push is dropped loudly (logged) rather than mis-recorded.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")

# Hikvision event names -> the vocabulary the rest of WatchLog uses.
HIK_EVENT_MAP = {
    "VMD": "motion", "vmd": "motion", "motion": "motion",
    "linedetection": "line_crossing", "fielddetection": "intrusion",
    "intrusion": "intrusion", "regionEntrance": "intrusion",
    "regionExiting": "intrusion", "tamperdetection": "tamper",
    "shelteralarm": "tamper", "videoloss": "video_loss",
    "facedetection": "person", "humanDetection": "person",
    "vehicledetection": "vehicle",
}


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_hikvision(body: bytes, content_type: str):
    """
    Turn a Hikvision alarm POST into a WatchLog event dict, or None.

    Handles the two shapes Hikvision sends: a bare EventNotificationAlert
    XML, and multipart/form-data with that XML in one part and a JPEG in
    another. Returns {channel, event_type, device_ts, device_event_id,
    snapshot_b64?} or None if it is not a recognisable alarm.
    """
    xml_bytes, jpeg = _split_multipart(body, content_type)
    if xml_bytes is None:
        xml_bytes = body

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    if _strip_ns(root.tag) != "EventNotificationAlert":
        return None

    fields = {}
    for child in root.iter():
        fields[_strip_ns(child.tag)] = (child.text or "").strip()

    raw_type = fields.get("eventType") or fields.get("subEventType") or ""
    event_type = HIK_EVENT_MAP.get(raw_type, HIK_EVENT_MAP.get(raw_type.lower()))
    if not event_type:
        # An unknown but present eventType is still a real event; keep it
        # under its own name rather than dropping it.
        event_type = raw_type.lower() or None
    if not event_type:
        return None

    # Hikvision "videoloss"/"ipcOnline" keep-alives report inactive; skip.
    if fields.get("eventState", "active").lower() == "inactive":
        return None

    channel = (fields.get("channelID") or fields.get("dynChannelID")
               or fields.get("channelName") or "1")
    ts = _parse_ts(fields.get("dateTime"))
    ev = {
        "channel": str(channel),
        "event_type": event_type,
        "device_ts": ts,
        "device_event_id": fields.get("activePostCount") and
                           f"{channel}-{raw_type}-{ts}" or None,
    }
    if jpeg:
        ev["snapshot_b64"] = base64.b64encode(jpeg).decode("ascii")
    return ev


def _split_multipart(body: bytes, content_type: str):
    """Return (xml_bytes|None, jpeg_bytes|None) from a multipart body."""
    m = re.search(r"boundary=([^\s;]+)", content_type or "")
    if not m:
        return None, None
    boundary = ("--" + m.group(1).strip('"')).encode()
    xml_part = jpeg_part = None
    for part in body.split(boundary):
        if b"\r\n\r\n" not in part:
            continue
        head, _, payload = part.partition(b"\r\n\r\n")
        payload = payload.rstrip(b"\r\n")
        head_l = head.lower()
        if b"application/xml" in head_l or b"text/xml" in head_l or payload[:5] == b"<?xml" or b"<EventNotificationAlert" in payload[:200]:
            xml_part = payload
        elif b"image/jpeg" in head_l or payload[:2] == bytes([0xFF, 0xD8]):
            jpeg_part = payload
    return xml_part, jpeg_part


def _parse_ts(raw):
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(
                timezone.utc).isoformat()
        except ValueError:
            pass
    return datetime.now(timezone.utc).isoformat()


def push(token: str, events: list) -> tuple:
    """Call wl_ingest_push. Returns (ok, detail)."""
    if not (SUPABASE_URL and SUPABASE_KEY):
        return False, "SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY not set"
    data = json.dumps({"p_token": token, "p_events": events}).encode()
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/rpc/wl_ingest_push", data=data, method="POST",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return True, r.read().decode()
    except Exception as e:                              # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:160]}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        sys.stderr.write("  bridge: " + (fmt % a) + "\n")

    def do_GET(self):
        # Health/landing. Never reveals anything; POST /push/<token> is the
        # only functional route.
        self.send_response(200); self.send_header("Content-Type","text/plain")
        self.end_headers(); self.wfile.write(b"WatchLog push bridge: OK")

    def do_POST(self):
        m = re.match(r"/push/([A-Za-z0-9]+)/?$", self.path)
        if not m:
            self.send_response(404); self.end_headers(); return
        token = m.group(1)
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        ev = parse_hikvision(body, self.headers.get("Content-Type", ""))
        if ev is None:
            # Not a recognisable alarm (keep-alive, unknown format). Answer
            # OK so the recorder does not retry forever, but record nothing.
            self.log_message("unrecognised push on token %s... (%d bytes)",
                             token[:6], len(body))
            self.send_response(202); self.end_headers(); return
        ok, detail = push(token, [ev])
        self.log_message("%s -> %s", ev.get("event_type"), detail[:80])
        self.send_response(200 if ok else 502); self.end_headers()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8620)
    a = ap.parse_args()
    print(f"WatchLog push bridge on {a.host}:{a.port} -> {SUPABASE_URL}")
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
