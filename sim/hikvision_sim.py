#!/usr/bin/env python3
"""
Hikvision ISAPI simulator.

Speaks the ISAPI dialect the real driver expects: digest auth, XML with
namespaces, and a long-lived multipart/mixed alertStream that pushes
EventNotificationAlert documents.

WHAT THIS PROVES AND WHAT IT DOES NOT
    It proves the driver's auth handshake, XML parsing, namespace
    stripping, multipart splitting, burst collapsing and event mapping
    all work on realistic input. It does NOT prove the driver works on a
    real Hikvision NVR, because this simulator was written from the same
    understanding of ISAPI as the driver. A real device will differ —
    firmware variations, missing fields, unexpected event types. This
    closes the gap between "never executed" and "executed against
    spec-shaped traffic". Nothing more.

    python sim/hikvision_sim.py --port 8451
    python sim/hikvision_sim.py --port 8451 --user admin --password admin

Stdlib only.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from digest_auth import DigestMixin, make_nonce            # noqa: E402
from sample_frames import frame                            # noqa: E402

NS = 'xmlns="http://www.hikvision.com/ver20/XMLSchema"'

CHANNELS = [
    (1, "Main Gate"),
    (2, "Loading Bay"),
    (3, "Rear Perimeter"),
    (4, "Server Room"),
]

# Real ISAPI eventType values, as they appear on the wire.
EVENT_TYPES = ["VMD", "linedetection", "fielddetection",
               "tamperdetection", "facedetection"]

BOUNDARY = "--boundary"
EVENT_EVERY = 8          # seconds between real alarms
KEEPALIVE_EVERY = 2      # seconds between videoloss keep-alives


def device_info_xml() -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<DeviceInfo {NS}>
<deviceName>WatchLog Hik Sim</deviceName>
<deviceID>88888888-8888-8888-8888-888888888888</deviceID>
<deviceDescription>NVR</deviceDescription>
<deviceLocation>simulator</deviceLocation>
<systemContact>none</systemContact>
<model>DS-7608NI-K2/8P</model>
<serialNumber>DS-7608NI-K2/8P0820200101SIMULATOR</serialNumber>
<macAddress>00:11:22:33:44:55</macAddress>
<firmwareVersion>V4.30.085</firmwareVersion>
<firmwareReleasedDate>build 200925</firmwareReleasedDate>
<encoderVersion>V5.0</encoderVersion>
<deviceType>NVR</deviceType>
<telecontrolID>255</telecontrolID>
<videoInputPortNums>{len(CHANNELS)}</videoInputPortNums>
<alarmInPortNums>4</alarmInPortNums>
</DeviceInfo>"""


def channels_xml() -> str:
    items = "\n".join(
        f"""<InputProxyChannel {NS}>
<id>{cid}</id>
<name>{name}</name>
<sourceInputPortDescriptor>
<proxyProtocol>ONVIF</proxyProtocol>
<addressingFormatType>ipaddress</addressingFormatType>
<ipAddress>192.168.1.{100 + cid}</ipAddress>
<managePortNo>80</managePortNo>
<srcInputPort>1</srcInputPort>
</sourceInputPortDescriptor>
</InputProxyChannel>""" for cid, name in CHANNELS)
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<InputProxyChannelList {NS}>\n{items}\n</InputProxyChannelList>')


def alert_xml(channel: int, name: str, event_type: str, count: int) -> str:
    now = datetime.now(timezone.utc).astimezone()
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<EventNotificationAlert {NS}>
<ipAddress>192.168.1.64</ipAddress>
<portNo>80</portNo>
<protocol>HTTP</protocol>
<macAddress>00:11:22:33:44:55</macAddress>
<channelID>{channel}</channelID>
<dateTime>{now.isoformat(timespec='seconds')}</dateTime>
<activePostCount>{count}</activePostCount>
<eventType>{event_type}</eventType>
<eventState>active</eventState>
<eventDescription>{event_type} alarm</eventDescription>
<channelName>{name}</channelName>
</EventNotificationAlert>"""


class Handler(DigestMixin, BaseHTTPRequestHandler):
    server_version = "Hikvision-Webs/Sim"
    protocol_version = "HTTP/1.1"

    def _xml(self, body: str, status: int = 200) -> None:
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        if not self.authed():
            return
        path = urlparse(self.path).path

        if path == "/ISAPI/System/deviceInfo":
            self._xml(device_info_xml())

        elif path == "/ISAPI/ContentMgmt/InputProxy/channels":
            self._xml(channels_xml())

        elif path == "/ISAPI/System/Video/inputs/channels":
            # A real NVR answers this too; return an error so the driver's
            # NVR-first / DVR-fallback ordering is actually exercised.
            self._xml('<?xml version="1.0" encoding="UTF-8"?>'
                      f'<ResponseStatus {NS}><statusCode>4</statusCode>'
                      '<statusString>Invalid Operation</statusString>'
                      '</ResponseStatus>', status=403)

        elif path.startswith("/ISAPI/Streaming/channels/") and path.endswith("/picture"):
            # Real ISAPI numbers these <channel><stream>, e.g. 201 for
            # channel 2 main stream. Decode that back to a channel.
            raw = path.split("/")[4]
            ch = raw[:-2] if len(raw) > 2 and raw.endswith(("01", "02")) else raw
            self._jpeg(frame(ch))

        elif path == "/ISAPI/Event/notification/alertStream":
            self._alert_stream()

        else:
            self._xml('<?xml version="1.0" encoding="UTF-8"?>'
                      f'<ResponseStatus {NS}><statusCode>3</statusCode>'
                      '<statusString>Invalid URL</statusString>'
                      '</ResponseStatus>', status=404)

    def _jpeg(self, raw: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _alert_stream(self):
        """Long-lived multipart/mixed push, exactly as a real NVR does."""
        self.send_response(200)
        self.send_header("Content-Type",
                         f"multipart/mixed; boundary={BOUNDARY}")
        self.send_header("Connection", "close")
        self.end_headers()
        print("[hik-sim] alertStream opened", flush=True)

        last_event = 0.0
        # activePostCount rises while an alarm stays active — a real NVR
        # repeats the same alarm about once a second, which is what the
        # driver's burst collapsing has to survive.
        burst_left, burst_ch, burst_type, burst_n = 0, 1, "VMD", 0

        try:
            while True:
                now = time.monotonic()

                if burst_left > 0:
                    cid, name = next(c for c in CHANNELS if c[0] == burst_ch)
                    burst_n += 1
                    self._part(alert_xml(cid, name, burst_type, burst_n))
                    burst_left -= 1
                    time.sleep(1)
                    continue

                if now - last_event >= EVENT_EVERY:
                    last_event = now
                    burst_ch, name = random.choice(CHANNELS)
                    burst_type = random.choice(EVENT_TYPES)
                    burst_n = 1
                    burst_left = random.randint(2, 5)
                    self._part(alert_xml(burst_ch, name, burst_type, burst_n))
                    print(f"[hik-sim] {burst_type} ch{burst_ch} "
                          f"(x{burst_left + 1} repeats)", flush=True)
                    time.sleep(1)
                    continue

                # Idle keep-alive. Real units spam videoloss when nothing
                # is happening; the driver must ignore these.
                self._part(alert_xml(1, "Main Gate", "videoloss", 0))
                time.sleep(KEEPALIVE_EVERY)

        except (BrokenPipeError, ConnectionResetError, OSError):
            print("[hik-sim] alertStream closed", flush=True)

    def _part(self, xml: str) -> None:
        raw = xml.encode("utf-8")
        head = (f"\r\n{BOUNDARY}\r\n"
                f"Content-Type: application/xml; charset=\"UTF-8\"\r\n"
                f"Content-Length: {len(raw)}\r\n\r\n").encode()
        self.wfile.write(head + raw)
        self.wfile.flush()

    def log_message(self, fmt, *args):
        print(f"[hik-sim] {self.address_string()} {fmt % args}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Hikvision ISAPI simulator")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8451)
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="admin")
    ap.add_argument("--no-auth", action="store_true")
    args = ap.parse_args()

    Handler.users = {} if args.no_auth else {args.user: args.password}
    Handler.nonce = make_nonce()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[hik-sim] http://{args.host}:{args.port}  "
          f"auth={'off' if args.no_auth else 'digest ' + args.user}", flush=True)
    print("[hik-sim] SIMULATOR. Proves the driver parses ISAPI-shaped "
          "traffic; proves nothing about real hardware.", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[hik-sim] stopped", flush=True)


if __name__ == "__main__":
    main()
