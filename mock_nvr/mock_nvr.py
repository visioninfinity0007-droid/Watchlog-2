#!/usr/bin/env python3
"""
WatchLog prototype — mock NVR.

Stands in for a real Dahua or Hikvision NVR, which we do not have today.
It speaks the same request/response *shape* a driver consumes — device
info, channel list, recent motion events — so the whole pipeline is
provable end to end, leaving exactly one unproven component: the real
device driver.

Be honest about that in any demo. "The pipeline is proven, the device
driver is stubbed" is true and strong. "It works" is not.

Endpoints
    GET /healthz
    GET /api/device-info
    GET /api/cameras
    GET /api/events?since=<iso8601>&limit=<n>

Events are computed on demand from a fixed epoch rather than accumulated
in memory: one every EVENT_PERIOD seconds, with a stable device event id.
That means restarting the mock does not reshuffle history, so dedupe
stays testable across restarts.

    python mock_nvr.py                 # port 8420
    python mock_nvr.py --port 9000
    python mock_nvr.py --no-ids        # omit device_event_id entirely,
                                       # forcing the agent onto its
                                       # timestamp+type dedupe fallback
                                       # (the Cargo Max failure mode)

Stdlib only. No dependencies.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# --- device identity ---------------------------------------------------

DEVICE = {
    "deviceName": "WatchLog Mock NVR",
    "deviceType": "NVR",
    "model": "MOCK-NVR-16CH",
    "serialNumber": "MOCK0000000000001",
    "firmwareVersion": "0.0.1-mock",
    "channelCount": 4,
    "vendor": "mock",
    "note": "Synthetic device. Not a Dahua or Hikvision unit.",
}

CAMERAS = [
    {"channel": "1", "name": "Main Gate",      "enabled": True},
    {"channel": "2", "name": "Loading Bay",    "enabled": True},
    {"channel": "3", "name": "Rear Perimeter", "enabled": True},
    {"channel": "4", "name": "Server Room",    "enabled": True},
]

EVENT_TYPES = ["motion", "line_crossing", "video_loss", "tamper"]

# One synthetic event every 20s, numbered from a fixed anchor.
EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
EVENT_PERIOD = 20
MAX_HISTORY = timedelta(hours=24)
DEFAULT_LIMIT = 200

OMIT_IDS = False  # set by --no-ids


def _event(index: int) -> dict:
    """Deterministically build event number `index`."""
    ts = EPOCH + timedelta(seconds=index * EVENT_PERIOD)
    seed = hashlib.sha256(str(index).encode()).digest()
    cam = CAMERAS[seed[0] % len(CAMERAS)]
    # motion dominates, as it does on a real site
    etype = EVENT_TYPES[0] if seed[1] % 10 < 7 else EVENT_TYPES[1 + seed[1] % 3]

    ev = {
        "channel": cam["channel"],
        "cameraName": cam["name"],
        "eventType": etype,
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "confidence": round(0.55 + (seed[2] % 45) / 100.0, 2),
        "durationMs": 800 + seed[3] * 10,
    }
    if not OMIT_IDS:
        ev["eventId"] = f"EV{index:09d}"
    return ev


def _events_since(since: datetime, limit: int) -> list[dict]:
    now = datetime.now(timezone.utc)
    floor_ts = max(since, now - MAX_HISTORY)

    first = int((floor_ts - EPOCH).total_seconds() // EVENT_PERIOD) + 1
    last = int((now - EPOCH).total_seconds() // EVENT_PERIOD)
    if last < first:
        return []

    # ASCENDING from `since`, capped at `limit`. This is a contract, not a
    # detail: if a device returns the NEWEST n instead, a client that
    # advances its high-water mark to the newest row silently skips the
    # middle of any backlog longer than one page. A real Dahua/Hikvision
    # driver must page forward from `since` for the same reason.
    return [_event(i) for i in range(first, min(last, first + limit - 1) + 1)]


def _parse_since(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(timezone.utc) - timedelta(hours=1)
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc) - timedelta(hours=1)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Handler(BaseHTTPRequestHandler):
    server_version = "MockNVR/0.1"

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802  (stdlib naming)
        url = urlparse(self.path)
        qs = parse_qs(url.query)

        if url.path == "/healthz":
            self._send({"ok": True})

        elif url.path == "/api/device-info":
            self._send({
                **DEVICE,
                "deviceTime": datetime.now(timezone.utc)
                    .isoformat().replace("+00:00", "Z"),
            })

        elif url.path == "/api/cameras":
            self._send({"channels": CAMERAS})

        elif url.path == "/api/events":
            since = _parse_since(qs.get("since", [None])[0])
            try:
                limit = min(int(qs.get("limit", [DEFAULT_LIMIT])[0]), 1000)
            except ValueError:
                limit = DEFAULT_LIMIT
            events = _events_since(since, limit)
            self._send({
                "since": since.isoformat().replace("+00:00", "Z"),
                "count": len(events),
                "events": events,
            })

        else:
            self._send({"error": "not found", "path": url.path}, status=404)

    def log_message(self, fmt: str, *args) -> None:
        print(f"[mock-nvr] {self.address_string()} {fmt % args}", flush=True)


def main() -> None:
    global OMIT_IDS
    ap = argparse.ArgumentParser(description="WatchLog prototype mock NVR")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8420)
    ap.add_argument(
        "--no-ids", action="store_true",
        help="omit device_event_id, forcing the timestamp+type dedupe fallback",
    )
    args = ap.parse_args()
    OMIT_IDS = args.no_ids

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    mode = "WITHOUT device event ids" if OMIT_IDS else "with stable device event ids"
    print(f"[mock-nvr] listening on http://{args.host}:{args.port} ({mode})", flush=True)
    print(f"[mock-nvr] a new synthetic event every {EVENT_PERIOD}s across "
          f"{len(CAMERAS)} channels", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[mock-nvr] stopped", flush=True)


if __name__ == "__main__":
    main()
