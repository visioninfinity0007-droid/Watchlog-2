#!/usr/bin/env python3
"""
Dahua CGI simulator.

Speaks the Dahua HTTP dialect the real driver expects: digest auth,
flat key=value responses, and a long-lived eventManager attach stream
with boundary-separated Code=...;action=... blocks.

Also stands in for CP Plus and the other Dahua-OEM recorders, which
answer the same CGI endpoints — relevant here because CP Plus has a real
dealer network in Pakistan.

WHAT THIS PROVES AND WHAT IT DOES NOT
    It proves the driver's auth, key=value parsing, channel-title index
    mapping, attach-stream line splitting, action filtering and burst
    collapsing work on realistic input. It does NOT prove the driver
    works on a real Dahua NVR — it was written from the same
    understanding of the CGI API as the driver itself. A real device will
    differ.

    python sim/dahua_sim.py --port 8452

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from digest_auth import DigestMixin, make_nonce            # noqa: E402

CHANNELS = ["Main Gate", "Loading Bay", "Rear Perimeter", "Server Room"]

EVENT_CODES = ["VideoMotion", "CrossLineDetection", "CrossRegionDetection",
               "VideoBlind", "SmartMotionHuman", "SmartMotionVehicle"]

BOUNDARY = "myboundary"
EVENT_EVERY = 8
HEARTBEAT_EVERY = 5

SYSTEM_INFO = {
    "appAutoStart": "true",
    "deviceType": "NVR4208-8P-4KS2",
    "hardwareVersion": "1.00",
    "processor": "ARM",
    "serialNumber": "DH0000000SIMULATOR",
    "updateSerial": "NVR4208-8P-4KS2",
    "version": "4.001.0000000.2",
    "videoInChannel": str(len(CHANNELS)),
    "videoOutChannel": "1",
}


class Handler(DigestMixin, BaseHTTPRequestHandler):
    server_version = "Webs/Sim"
    protocol_version = "HTTP/1.1"

    def _text(self, body: str, status: int = 200) -> None:
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        if not self.authed():
            return
        url = urlparse(self.path)
        qs = parse_qs(url.query)
        action = (qs.get("action") or [""])[0]

        if url.path == "/cgi-bin/magicBox.cgi" and action == "getSystemInfo":
            self._text("\r\n".join(f"{k}={v}" for k, v in SYSTEM_INFO.items()))

        elif url.path == "/cgi-bin/magicBox.cgi" and action == "getDeviceType":
            self._text(f"type={SYSTEM_INFO['deviceType']}")

        elif url.path == "/cgi-bin/configManager.cgi" and action == "getConfig":
            name = (qs.get("name") or [""])[0]
            if name == "ChannelTitle":
                # 0-based on the wire; the driver has to shift to 1-based.
                self._text("\r\n".join(
                    f"table.ChannelTitle[{i}].Name={n}"
                    for i, n in enumerate(CHANNELS)))
            else:
                self._text("Error", status=400)

        elif url.path == "/cgi-bin/eventManager.cgi" and action == "attach":
            self._attach(qs)

        else:
            self._text("Error", status=404)

    def _attach(self, qs: dict):
        codes = (qs.get("codes") or ["[All]"])[0].strip("[]").split(",")
        print(f"[dahua-sim] attach opened, {len(codes)} codes subscribed",
              flush=True)

        self.send_response(200)
        self.send_header("Content-Type",
                         f"multipart/x-mixed-replace; boundary={BOUNDARY}")
        self.send_header("Connection", "close")
        self.end_headers()

        last_event = 0.0
        burst_left, burst_code, burst_idx = 0, "", 0

        try:
            while True:
                now = time.monotonic()

                if burst_left > 0:
                    # action=Pulse is what a continuing alarm looks like.
                    self._block(burst_code, "Pulse", burst_idx)
                    burst_left -= 1
                    time.sleep(1)
                    continue

                if now - last_event >= EVENT_EVERY:
                    last_event = now
                    burst_code = random.choice(EVENT_CODES)
                    burst_idx = random.randrange(len(CHANNELS))
                    burst_left = random.randint(2, 4)
                    self._block(burst_code, "Start", burst_idx)
                    print(f"[dahua-sim] {burst_code} index={burst_idx} "
                          f"(x{burst_left + 1})", flush=True)
                    time.sleep(1)
                    continue

                # Real units send a heartbeat block when idle; the driver
                # must not turn these into events.
                self._raw("Heartbeat")
                time.sleep(HEARTBEAT_EVERY)

        except (BrokenPipeError, ConnectionResetError, OSError):
            print("[dahua-sim] attach closed", flush=True)

    def _block(self, code: str, action: str, index: int) -> None:
        data = json.dumps({"SmartMotionEnable": True,
                           "RegionName": ["Region1"],
                           "Channel": index})
        self._raw(f"Code={code};action={action};index={index};data={data}")

    def _raw(self, payload: str) -> None:
        body = (payload + "\r\n").encode("utf-8")
        head = (f"--{BOUNDARY}\r\n"
                f"Content-Type: text/plain\r\n"
                f"Content-Length: {len(body)}\r\n\r\n").encode()
        self.wfile.write(head + body)
        self.wfile.flush()

    def log_message(self, fmt, *args):
        print(f"[dahua-sim] {self.address_string()} {fmt % args}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Dahua CGI simulator")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8452)
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="admin")
    ap.add_argument("--no-auth", action="store_true")
    args = ap.parse_args()

    Handler.users = {} if args.no_auth else {args.user: args.password}
    Handler.nonce = make_nonce()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[dahua-sim] http://{args.host}:{args.port}  "
          f"auth={'off' if args.no_auth else 'digest ' + args.user}", flush=True)
    print("[dahua-sim] SIMULATOR. Also stands in for CP Plus and other "
          "Dahua-OEM recorders. Proves nothing about real hardware.", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[dahua-sim] stopped", flush=True)


if __name__ == "__main__":
    main()
