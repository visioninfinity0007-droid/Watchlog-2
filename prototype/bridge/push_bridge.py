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

It holds NO secret. The token in the URL is one recorder's credential: WatchLog
issues one token per recorder, and wl_ingest_push resolves the recorder from the
token BEFORE it reads any channel, then the camera by (recorder, channel). The
bridge therefore never names a site, recorder or camera itself (MNVR-011). The
Supabase publishable key is public by design.

What an alarm MEANS (event type, channel, keep-alive filtering, burst collapse,
which clock stamped it) comes from alarm_parsing.py, the Agent drivers' own parser
shipped here byte for byte, so an alarm that reaches WatchLog both through the
Agent and through push is one row with one meaning (MNVR-026). Deploy it on the same
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
import time
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import alarm_parsing

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")

# The shared vocabulary (kept under the names other code and tests use).
HIK_EVENT_MAP = alarm_parsing.HIK_EVENT_TYPE_MAP
DAHUA_EVENT_MAP = alarm_parsing.DAHUA_EVENT_CODE_MAP
DAHUA_NON_EVENTS = alarm_parsing.DAHUA_NON_EVENTS

# Which clock stamped an event whose recorder time could not be used: the bridge's.
RECEIVE_SOURCE = "push_receive"

# Repeats of one continuing alarm, per recorder token (see deliver()).
BurstFilter = alarm_parsing.BurstFilter
BURST = BurstFilter()


def _event(alarm, jpeg, received_at):
    """The wl_ingest_push event for one parsed alarm.

    device_event_id is NULL, exactly as the Agent sends it: neither vendor's alarm
    carries a stable id, and an invented one would key the push copy of an alarm
    differently from the Agent's copy. A naive recorder time is never read as UTC: the
    bridge cannot ask the recorder for its offset, so it uses its receive time and keeps
    the recorder's text. ``_burst`` is internal and removed by deliver()."""
    ts, clock = alarm_parsing.resolve_event_time(
        alarm.raw_time, received_at or datetime.now(timezone.utc),
        receive_source=RECEIVE_SOURCE)
    ev = {
        "channel": alarm.channel,
        "event_type": alarm.event_type,
        "device_ts": ts.astimezone(timezone.utc).isoformat(),
        "device_event_id": None,
        "payload": {**alarm.payload, **clock},
        "_burst": (alarm.vendor,) + tuple(alarm.burst_key),
    }
    if jpeg:
        ev["snapshot_b64"] = base64.b64encode(jpeg).decode("ascii")
    return ev


def parse_hikvision(body: bytes, content_type: str, received_at=None):
    """
    Turn a Hikvision alarm POST into a WatchLog event dict, or None.

    Handles the two shapes Hikvision sends: a bare EventNotificationAlert
    XML, and multipart/form-data with that XML in one part and a JPEG in
    another. Returns {channel, event_type, device_ts, device_event_id, payload,
    snapshot_b64?} or None if it is not an event (keep-alive, heartBeat, heartbeat
    videoloss with activePostCount 0, inactive, or not an alert at all).
    """
    xml_bytes, jpeg = _split_multipart(body, content_type)
    if xml_bytes is None:
        xml_bytes = body
    alarm = alarm_parsing.parse_hikvision_alert(xml_bytes)
    return _event(alarm, jpeg, received_at) if alarm is not None else None


def parse_dahua(body: bytes, content_type: str, received_at=None):
    """
    Turn a Dahua alarm POST into a WatchLog event dict, or None.

    Dahua posts the same ``Code=VideoMotion;action=Start;index=0;data={...}``
    vocabulary it streams over eventManager attach, and alarm_parsing is the very
    parser the Agent's attach path uses: action=Stop/State is ignored, the 0-based wire
    index becomes the 1-based channel, and a disk/alarm-input code or a missing index
    is never guessed onto a camera. Some firmware wraps the same fields in JSON, and
    some attaches a JPEG via multipart; both are handled.
    """
    text_bytes, jpeg = _split_multipart(body, content_type)
    raw = (text_bytes if text_bytes is not None else body)
    try:
        text = raw.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return None
    alarm = alarm_parsing.parse_dahua_block(text)
    return _event(alarm, jpeg, received_at) if alarm is not None else None


PARSERS = (("hikvision", parse_hikvision), ("dahua", parse_dahua))


def parse_any(body: bytes, content_type: str, received_at=None):
    """First parser that confidently understands this body wins.

    Order matters only for speed: the two formats are structurally disjoint (XML
    document vs Code=...;action=... / JSON), so neither can claim the other's.
    Returns (vendor, event) or (None, None) — never a guess.
    """
    received_at = received_at or datetime.now(timezone.utc)
    for vendor, parser in PARSERS:
        try:
            ev = parser(body, content_type, received_at)
        except Exception:  # noqa: BLE001 — a malformed push is not a crash
            ev = None
        if ev:
            return vendor, ev
    return None, None


def is_liveness_chatter(body: bytes, content_type: str) -> bool:
    """True for recorder keep-alive traffic: real, authenticated, but not an alarm."""
    text_bytes, _jpeg = _split_multipart(body, content_type)
    raw = text_bytes if text_bytes is not None else body
    try:
        text = raw.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return False
    lowered = text.lower()
    if "code=" not in lowered and '"code"' not in lowered:
        return False
    return any(code in lowered for code in DAHUA_NON_EVENTS)


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


def liveness(token: str) -> tuple:
    """Record that the recorder is alive WITHOUT inventing an event.

    Recorder chatter (Heartbeat/KeepAlive/TimeChange) is not an alarm and must never be
    stored as one -- but it does prove the recorder is powered, configured and able to
    reach us. Dropping it silently, as the bridge used to, threw away the only liveness
    signal a PC-free site can produce between alarms.
    """
    return _rpc("wl_push_liveness", {"p_token": token})


def push(token: str, events: list) -> tuple:
    """Call wl_ingest_push. Returns (ok, detail)."""
    return _rpc("wl_ingest_push", {"p_token": token, "p_events": events})


def deliver(token: str, vendor: str, ev: dict, mono: float | None = None) -> int:
    """Ingest one parsed event under its recorder token; returns the HTTP status.

    A continuing alarm repeats about once a second. The Agent collapses those repeats
    to one event per BURST_WINDOW_SECONDS; the bridge applies the same rule, per
    recorder token, on its own monotonic clock. An event WatchLog did not accept is not
    counted against the window, so the recorder's retry still gets through."""
    key = (token,) + tuple(ev.pop("_burst", None) or (vendor, ev.get("channel"),
                                                       ev.get("event_type")))
    now = time.monotonic() if mono is None else mono
    if not BURST.admit(key, now):
        return 200
    ok, _detail = push(token, [ev])
    if not ok:
        BURST.forget(key, now)
    return 200 if ok else 502


def _rpc(fn: str, payload: dict) -> tuple:
    if not (SUPABASE_URL and SUPABASE_KEY):
        return False, "SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY not set"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/rpc/{fn}", data=data, method="POST",
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
        # BaseHTTPRequestHandler.path carries the full request target INCLUDING the query
        # string. Dahua firmware that posts to `/push/<token>?action=alarm&channel=0` was
        # therefore 404'd and every alarm silently rejected. Match on the path only.
        route = self.path.split("?", 1)[0]
        m = re.match(r"/push/([A-Za-z0-9]+)/?$", route)
        if not m:
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(length) if length else b""
            self.log_message("unrecognised ROUTE %s %s", route[:60],
                             describe_unparsed(body, self.headers.get("Content-Type", "")))
            self.send_response(404); self.end_headers(); return
        token = m.group(1)
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        ctype = self.headers.get("Content-Type", "")

        # Every POST to a valid recorder token is recorder-originated liveness,
        # including Hikvision heartBeat and OEM/Dahua formats we do not yet parse.
        # Authenticate/update liveness FIRST; only then decide whether there is also
        # a security event worth ingesting.
        live_ok, live_detail = liveness(token)
        if not live_ok:
            self.log_message("liveness rejected for token %s... -> %s",
                             token[:6], live_detail[:80])
            self.send_response(502); self.end_headers(); return

        vendor, ev = parse_any(body, ctype)
        if ev is None and is_liveness_chatter(body, ctype):
            self.log_message("recorder liveness only -> %s", live_detail[:80])
            self.send_response(200); self.end_headers(); return
        if ev is None:
            # Not a recognisable alarm (keep-alive, a Stop, or a format we do not
            # confidently understand). Answer OK so the recorder does not retry
            # forever, and record NOTHING rather than invent an event. The
            # description is what makes a new model diagnosable from the logs.
            self.log_message("unrecognised push on token %s... %s",
                             token[:6], describe_unparsed(body, ctype))
            self.send_response(202); self.end_headers(); return
        status = deliver(token, vendor, ev)
        self.log_message("%s %s ch%s -> %s", vendor, ev.get("event_type"),
                         ev.get("channel") or "-", status)
        self.send_response(status); self.end_headers()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8620)
    a = ap.parse_args()
    print(f"WatchLog push bridge on {a.host}:{a.port} -> {SUPABASE_URL}")
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
