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


# Dahua event codes -> our vocabulary. MUST stay a superset of
# agent/drivers/dahua.py:EVENT_CODE_MAP — the attach path and the push path have
# to agree or the same alarm means two different things depending on how it
# reached us. test_push_bridge pins that parity.
DAHUA_EVENT_MAP = {
    "VideoMotion": "motion",
    "SmartMotionHuman": "person",
    "SmartMotionVehicle": "vehicle",
    "CrossLineDetection": "line_crossing",
    "CrossRegionDetection": "intrusion",
    "LeftDetection": "object_left",
    "TakenAwayDetection": "object_removed",
    "VideoLoss": "video_loss",
    "VideoBlind": "tamper",
    "AlarmLocal": "alarm_input",
    "StorageNotExist": "disk_error",
    "StorageFailure": "disk_error",
    "StorageLowSpace": "disk_full",
    "FaceDetection": "face",
}

# Chatter a Dahua unit emits that is not an occurrence.
DAHUA_NON_EVENTS = {"heartbeat", "keepalive", "timechange", "ntpadjusttime"}


def parse_dahua(body: bytes, content_type: str):
    """
    Turn a Dahua alarm POST into a WatchLog event dict, or None.

    Dahua posts the same ``Code=VideoMotion;action=Start;index=0;data={...}``
    vocabulary it streams over eventManager attach, so this mirrors
    ``drivers/dahua.py::_parse_line`` deliberately — including ignoring
    action=Stop/State and converting the 0-based wire channel to the 1-based
    channel used everywhere else in WatchLog. Some firmware wraps the same
    fields in JSON, and some attaches a JPEG via multipart; both are handled.
    """
    text_bytes, jpeg = _split_multipart(body, content_type)
    raw = (text_bytes if text_bytes is not None else body)
    try:
        text = raw.decode("utf-8", "replace").strip()
    except Exception:  # noqa: BLE001
        return None
    if not text:
        return None

    fields = {}
    data = ""
    if text.startswith("{"):
        try:
            obj = json.loads(text)
        except ValueError:
            return None
        if not isinstance(obj, dict):
            return None
        fields = {str(k): ("" if v is None else str(v)) for k, v in obj.items()
                  if not isinstance(v, (dict, list))}
        data = json.dumps(obj.get("data")) if isinstance(obj.get("data"), (dict, list)) else ""
    else:
        # data={...} may itself contain ';', so only split the leading pairs.
        head, sep, data = text.partition(";data=")
        if "Code=" not in head:
            return None
        for part in head.split(";"):
            k, _, v = part.partition("=")
            if k:
                fields[k.strip()] = v.strip()
        data = data if sep else ""

    code = fields.get("Code") or fields.get("code") or ""
    if not code:
        return None
    action = (fields.get("action") or fields.get("Action") or "").lower()
    if action not in ("start", "pulse", ""):
        return None                       # Stop / State — not an occurrence
    if code.lower() in DAHUA_NON_EVENTS:
        return None

    event_type = DAHUA_EVENT_MAP.get(code) or code.lower()

    # index is 0-based on the wire; channels are 1-based everywhere else.
    try:
        channel = str(int(str(fields.get("index", fields.get("Index", "0"))).strip()) + 1)
    except ValueError:
        channel = "1"

    ts = _parse_ts(fields.get("dateTime") or fields.get("DateTime"))
    ev = {
        "channel": channel,
        "event_type": event_type,
        "device_ts": ts,
        "device_event_id": f"{channel}-{code}-{ts}",
    }
    if jpeg:
        ev["snapshot_b64"] = base64.b64encode(jpeg).decode("ascii")
    return ev


PARSERS = (("hikvision", parse_hikvision), ("dahua", parse_dahua))


def parse_any(body: bytes, content_type: str):
    """First parser that confidently understands this body wins.

    Order matters only for speed: the two formats are structurally disjoint (XML
    document vs Code=...;action=... / JSON), so neither can claim the other's.
    Returns (vendor, event) or (None, None) — never a guess.
    """
    for vendor, parser in PARSERS:
        try:
            ev = parser(body, content_type)
        except Exception:  # noqa: BLE001 — a malformed push is not a crash
            ev = None
        if ev:
            return vendor, ev
    return None, None


def describe_unparsed(body: bytes, content_type: str, limit: int = 400) -> str:
    """A bounded, log-safe description of a push we could not parse.

    Field-validating a new recorder model means seeing what it actually sent. This
    prints enough to write a parser against (content type, size, part headers, the
    leading text) while never dumping image bytes and never growing without bound.
    """
    parts = [f"content_type={(content_type or '-')[:80]} bytes={len(body)}"]
    text_bytes, jpeg = _split_multipart(body, content_type)
    parts.append(f"jpeg={'yes(' + str(len(jpeg)) + 'B)' if jpeg else 'no'}")
    sample = text_bytes if text_bytes is not None else body
    if sample[:2] == bytes([0xFF, 0xD8]):
        parts.append("text=<binary jpeg>")
    else:
        shown = " ".join(sample[:limit].decode("utf-8", "replace").split())
        parts.append(f"text={shown!r}")
        if len(sample) > limit:
            parts.append(f"(+{len(sample) - limit} more bytes)")
    return " ".join(parts)


def _split_multipart(body: bytes, content_type: str):
    """Return (text_bytes|None, jpeg_bytes|None) from a multipart body.

    Vendor-neutral: the JPEG part is identified positively (content type or the
    JFIF magic) and the FIRST remaining part is handed back as the text payload.
    Hikvision puts XML there, Dahua puts Code=...;action=... or JSON — the
    per-vendor parsers decide what it means; this only separates image from text.
    """
    m = re.search(r"boundary=([^\s;]+)", content_type or "")
    if not m:
        return None, None
    boundary = ("--" + m.group(1).strip('"')).encode()
    text_part = jpeg_part = None
    for part in body.split(boundary):
        if b"\r\n\r\n" not in part:
            continue
        head, _, payload = part.partition(b"\r\n\r\n")
        payload = payload.rstrip(b"\r\n")
        if not payload:
            continue
        head_l = head.lower()
        if b"image/jpeg" in head_l or payload[:2] == bytes([0xFF, 0xD8]):
            if jpeg_part is None:
                jpeg_part = payload
        elif text_part is None:
            text_part = payload
    return text_part, jpeg_part


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
        ctype = self.headers.get("Content-Type", "")
        vendor, ev = parse_any(body, ctype)
        if ev is None:
            # Not a recognisable alarm (keep-alive, a Stop, or a format we do not
            # confidently understand). Answer OK so the recorder does not retry
            # forever, and record NOTHING rather than invent an event. The
            # description is what makes a new model diagnosable from the logs.
            self.log_message("unrecognised push on token %s... %s",
                             token[:6], describe_unparsed(body, ctype))
            self.send_response(202); self.end_headers(); return
        ok, detail = push(token, [ev])
        self.log_message("%s %s ch%s -> %s", vendor, ev.get("event_type"),
                         ev.get("channel"), detail[:80])
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
