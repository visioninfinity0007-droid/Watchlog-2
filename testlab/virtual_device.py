#!/usr/bin/env python3
"""WatchLog virtual CCTV device for installer/agent integration testing.

This is deliberately a protocol simulator, not a production CCTV server. It gives
Dahua, Hikvision and generic ONVIF test devices real LAN identities and implements
the exact read paths WatchLog exercises: discovery fingerprints, Digest auth,
inventory, snapshots, native event streams, storage/recording health and bounded
archive search/download.

Control API (port 9001, lab network only):
  GET  /state
  POST /scenario/<healthy|slow-login|storage-fault|archive-empty|camera-2-offline>
  POST /event/<motion|person|vehicle|video-loss|tamper>?channel=1
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import random
import socket
import socketserver
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

try:
    from PIL import Image, ImageDraw
except Exception:
    Image = None
    ImageDraw = None

KIND = os.environ.get("DEVICE_KIND", "dahua").strip().lower()
NAME = os.environ.get("DEVICE_NAME", f"WatchLog Lab {KIND.title()}")
MODEL = os.environ.get(
    "DEVICE_MODEL",
    {"dahua": "DH-XVR1B08-I", "hikvision": "DS-7608NI-Q1", "onvif": "WL-ONVIF-CAM"}.get(KIND, "WL-LAB")
)
SERIAL = os.environ.get("DEVICE_SERIAL", f"LAB-{KIND.upper()}-001")
USERNAME = os.environ.get("DEVICE_USERNAME", "admin")
PASSWORD = os.environ.get("DEVICE_PASSWORD", "WatchLog123!")
CHANNELS = max(1, int(os.environ.get("DEVICE_CHANNELS", "4")))
REALM = os.environ.get("DEVICE_REALM", MODEL)
HTTP_PORT = int(os.environ.get("HTTP_PORT", "80"))
CONTROL_PORT = int(os.environ.get("CONTROL_PORT", "9001"))
SDK_PORT = int(os.environ.get(
    "SDK_PORT",
    "37777" if KIND == "dahua" else ("8000" if KIND == "hikvision" else "0")
))
SDK_DELAY = float(os.environ.get("SDK_START_DELAY", "0"))
RTSP_PORT = int(os.environ.get("RTSP_PORT", "554"))
REAL_RTSP = os.environ.get("REAL_RTSP", "0") == "1"
CLIP_PATH = os.environ.get("LAB_CLIP_PATH", "/tmp/watchlog-lab.mp4")
NONCE = hashlib.md5(f"{SERIAL}:watchlog-lab".encode()).hexdigest()
OPAQUE = hashlib.md5(f"{REALM}:opaque".encode()).hexdigest()

STATE_LOCK = threading.RLock()
STATE = {
    "scenario": "healthy",
    "login_delay": 0.0,
    "storage": "normal",
    "archive": True,
    "offline_channels": [],
    "recording_off": [],
    "events": [],
    "push_config": {},
}

# Small but real JPEGs are generated at request time. If Pillow is unavailable,
# this 1x1 JPEG keeps snapshot contracts alive.
FALLBACK_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////"
    "2wBDAf//////////////////////////////////////////////////////////////////////////////////////"
    "wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/"
    "9oADAMBAAIQAxAAAAF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABBQJ//8QAFBEBAAAAAAAAAAAAAAAA"
    "AAAAAP/aAAgBAwEBPwF//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPwF//8QAFBABAAAAAAAAAAAAAAAA"
    "AAAAAP/aAAgBAQAGPwJ//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPyF//9oADAMBAAIAAwAAABB//8QA"
    "FBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPxB//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPxB//8QA"
    "FBABAQAAAAAAAAAAAAAAAAAAABH/2gAIAQEAAT8QH//Z"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def recorded_clip_bytes() -> bytes:
    try:
        with open(CLIP_PATH, "rb") as handle:
            data = handle.read(32 * 1024 * 1024 + 1)
        if data and len(data) <= 32 * 1024 * 1024:
            return data
    except OSError:
        pass
    return b"WL-LAB-RECORDED-CLIP" * 256


def jpeg_for(channel: int) -> bytes:
    if Image is None:
        return FALLBACK_JPEG
    img = Image.new("RGB", (640, 360), (25 + channel * 15, 45, 70))
    draw = ImageDraw.Draw(img)
    draw.text((24, 24), f"{NAME} / Camera {channel}", fill="white")
    draw.text((24, 60), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), fill="white")
    draw.rectangle((30 + channel * 30, 130, 180 + channel * 30, 280), outline="white", width=4)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85)
    return out.getvalue()


def _parse_digest(value: str) -> dict[str, str]:
    if not value.lower().startswith("digest "):
        return {}
    raw = value[7:]
    out: dict[str, str] = {}
    current = ""
    quoted = False
    parts = []
    for ch in raw:
        if ch == '"':
            quoted = not quoted
        if ch == "," and not quoted:
            parts.append(current)
            current = ""
        else:
            current += ch
    if current:
        parts.append(current)
    for part in parts:
        key, sep, val = part.strip().partition("=")
        if sep:
            out[key.strip()] = val.strip().strip('"')
    return out


def digest_ok(header: str | None, method: str) -> bool:
    if not header:
        return False
    if header.lower().startswith("basic "):
        try:
            userpass = base64.b64decode(header.split(None, 1)[1]).decode()
            return userpass == f"{USERNAME}:{PASSWORD}"
        except Exception:
            return False
    fields = _parse_digest(header)
    if not fields or fields.get("username") != USERNAME or fields.get("realm") != REALM:
        return False
    if fields.get("nonce") != NONCE:
        return False
    uri = fields.get("uri", "")
    ha1 = hashlib.md5(f"{USERNAME}:{REALM}:{PASSWORD}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
    qop = fields.get("qop")
    if qop:
        expected = hashlib.md5(
            f"{ha1}:{NONCE}:{fields.get('nc','')}:{fields.get('cnonce','')}:{qop}:{ha2}".encode()
        ).hexdigest()
    else:
        expected = hashlib.md5(f"{ha1}:{NONCE}:{ha2}".encode()).hexdigest()
    return fields.get("response", "").lower() == expected.lower()


def auth_challenge(handler: BaseHTTPRequestHandler) -> bool:
    with STATE_LOCK:
        delay = float(STATE["login_delay"])
    if delay:
        time.sleep(delay)
    if digest_ok(handler.headers.get("Authorization"), handler.command):
        return True
    handler.send_response(401)
    handler.send_header(
        "WWW-Authenticate",
        f'Digest realm="{REALM}", nonce="{NONCE}", opaque="{OPAQUE}", algorithm=MD5, qop="auth"'
    )
    handler.send_header("Content-Length", "0")
    handler.end_headers()
    return False


def xml(tag: str, body: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<{tag} version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema">{body}</{tag}>'
    ).encode()


def event_xml(kind: str, channel: int, active: bool = True) -> bytes:
    hik_type = {
        "motion": "VMD",
        "person": "PeopleDetection",
        "vehicle": "VehicleDetection",
        "video-loss": "videoloss",
        "tamper": "shelteralarm",
    }.get(kind, kind)
    return xml(
        "EventNotificationAlert",
        f"<ipAddress>10.77.0.21</ipAddress><portNo>80</portNo>"
        f"<protocol>HTTP</protocol><macAddress>00:11:22:33:44:55</macAddress>"
        f"<channelID>{channel}</channelID><dateTime>{now_iso()}</dateTime>"
        f"<activePostCount>1</activePostCount><eventType>{hik_type}</eventType>"
        f"<eventState>{'active' if active else 'inactive'}</eventState>"
        f"<eventDescription>{kind}</eventDescription>"
    )


class DeviceHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "App-webs" if KIND == "hikvision" else ("Dahua-Web" if KIND == "dahua" else "WatchLog-ONVIF")

    def log_message(self, fmt, *args):
        print(f"[{KIND}] {self.address_string()} {fmt % args}", flush=True)

    def _send(self, status=200, body=b"", ctype="text/plain", headers=None):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _snapshot(self, channel: int):
        with STATE_LOCK:
            offline = channel in STATE["offline_channels"]
        if offline:
            self._send(503, "camera offline")
        else:
            self._send(200, jpeg_for(channel), "image/jpeg")

    def do_GET(self):
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)

        if KIND == "router":
            self._send(200, "<html><title>Lab Router</title>router</html>", "text/html")
            return

        if path == "/":
            body = f"<html><title>{MODEL}</title>{NAME} {KIND}</html>"
            self._send(200, body, "text/html")
            return

        if not auth_challenge(self):
            return

        if KIND == "dahua":
            self._dahua_get(path, query)
            return
        if KIND == "hikvision":
            self._hik_get(path, query)
            return
        if KIND == "onvif":
            if path.startswith("/snapshot/"):
                try:
                    ch = int(path.rsplit("/", 1)[1])
                except Exception:
                    ch = 1
                self._snapshot(ch)
                return
            self._send(404, "not found")
            return
        self._send(404, "not found")

    def _dahua_get(self, path, q):
        action = (q.get("action") or [""])[0]
        name = (q.get("name") or [""])[0]

        if path == "/cgi-bin/magicBox.cgi" and action == "getSystemInfo":
            self._send(200, "\n".join([
                f"deviceType={MODEL}", "videoInChannel=%d" % CHANNELS,
                "version=V4.001.WL.LAB", f"serialNumber={SERIAL}", "processor=WatchLogLab"
            ]) + "\n")
            return
        if path == "/cgi-bin/magicBox.cgi" and action == "getDeviceType":
            self._send(200, f"type={MODEL}\n")
            return
        if path == "/cgi-bin/global.cgi" and action == "getCurrentTime":
            self._send(200, "result=" + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n")
            return
        if path == "/cgi-bin/configManager.cgi" and action == "getConfig":
            if name == "ChannelTitle":
                body = "".join(f"table.ChannelTitle[{i}].Name=Lab Camera {i+1}\n" for i in range(CHANNELS))
            elif name == "RecordMode":
                with STATE_LOCK:
                    off = set(STATE["recording_off"])
                body = "".join(f"table.RecordMode[{i}].Mode={'2' if i+1 in off else '0'}\n" for i in range(CHANNELS))
            elif name in ("MotionDetect", "SmartMotionDetect", "CoverDetect"):
                body = "".join(f"table.{name}[{i}].Enable=true\n" for i in range(CHANNELS))
            elif name == "VideoAnalyseRule":
                body = "".join(
                    f"table.VideoAnalyseRule[{i}][0].Class=CrossLineDetection\n"
                    f"table.VideoAnalyseRule[{i}][0].Enable=true\n" for i in range(CHANNELS)
                )
            elif name == "AlarmServer":
                body = "table.AlarmServer.Protocol=Dahua\n"
            elif name == "NTP":
                body = "table.NTP.Enable=true\ntable.NTP.Address=pool.ntp.org\n"
            elif name == "Locales":
                body = "table.Locales.DSTEnable=false\n"
            else:
                body = ""
            self._send(200, body)
            return
        if path == "/cgi-bin/storageDevice.cgi" and action == "getDeviceAllInfo":
            with STATE_LOCK:
                storage = STATE["storage"]
            self._send(200, f"table.Storage[0].State={storage}\n")
            return
        if path == "/cgi-bin/snapshot.cgi":
            ch = int((q.get("channel") or ["1"])[0])
            self._snapshot(ch)
            return
        if path == "/cgi-bin/eventManager.cgi" and action == "getEventIndexes":
            code = (q.get("code") or [""])[0]
            with STATE_LOCK:
                offline = list(STATE["offline_channels"])
            body = ""
            if code in ("VideoLoss", "VideoBlind"):
                body = "".join(f"channels[{i}]={ch-1}\n" for i, ch in enumerate(offline))
            self._send(200, body)
            return
        if path == "/cgi-bin/eventManager.cgi" and action == "attach":
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=watchlog")
            self.send_header("Connection", "close")
            self.end_headers()
            mapping = {
                "motion": "VideoMotion", "person": "SmartMotionHuman",
                "vehicle": "SmartMotionVehicle", "video-loss": "VideoLoss", "tamper": "VideoBlind"
            }
            try:
                for _ in range(60):
                    event = None
                    with STATE_LOCK:
                        if STATE["events"]:
                            event = STATE["events"].pop(0)
                    if event:
                        code = mapping.get(event["type"], "VideoMotion")
                        line = f"Code={code};action=Start;index={int(event['channel'])-1};data={{}}\r\n"
                    else:
                        line = "Code=Heartbeat;action=Pulse;index=0;data={}\r\n"
                    self.wfile.write(line.encode())
                    self.wfile.flush()
                    time.sleep(1)
            except Exception:
                pass
            return
        if path == "/cgi-bin/mediaFileFind.cgi":
            if action == "factory.create":
                self._send(200, "result=1\n")
            elif action == "findFile":
                self._send(200, "OK\n")
            elif action == "findNextFile":
                with STATE_LOCK:
                    archive = bool(STATE["archive"])
                if archive:
                    start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    body = (
                        f"items[0].StartTime={start}\n"
                        f"items[0].EndTime={start}\n"
                        "items[0].FilePath=/mnt/sd/2026/lab/clip.dav\n"
                        "items[0].Length=4096\n"
                    )
                else:
                    body = ""
                self._send(200, body)
            else:
                self._send(200, "OK\n")
            return
        if path == "/cgi-bin/loadfile.cgi" or path.startswith("/cgi-bin/RPC_Loadfile/"):
            with STATE_LOCK:
                archive = bool(STATE["archive"])
            self._send(200 if archive else 404, recorded_clip_bytes() if archive else b"",
                       "video/mp4")
            return
        self._send(404, "unsupported Dahua lab endpoint")

    def _hik_get(self, path, q):
        if path == "/ISAPI/System/deviceInfo":
            self._send(200, xml("DeviceInfo",
                f"<deviceName>{NAME}</deviceName><deviceID>{SERIAL}</deviceID>"
                f"<model>{MODEL}</model><serialNumber>{SERIAL}</serialNumber>"
                f"<firmwareVersion>V5.7.WL.LAB</firmwareVersion>"
                f"<deviceType>NVR</deviceType><videoInputPortNums>{CHANNELS}</videoInputPortNums>"),
                "application/xml")
            return
        if path == "/ISAPI/ContentMgmt/InputProxy/channels":
            body = "".join(
                f"<InputProxyChannel><id>{i}</id><name>Lab Camera {i}</name></InputProxyChannel>"
                for i in range(1, CHANNELS + 1)
            )
            self._send(200, xml("InputProxyChannelList", body), "application/xml")
            return
        if path.startswith("/ISAPI/Streaming/channels/") and path.endswith("/picture"):
            token = path.split("/")[4]
            digits = "".join(ch for ch in token if ch.isdigit())
            ch = int(digits[:-2] or digits or "1") if len(digits) > 2 else int(digits or "1")
            self._snapshot(max(1, ch))
            return
        if path.startswith("/ISAPI/System/Video/inputs/channels/") and path.endswith("/motionDetection"):
            self._send(200, xml("MotionDetection", "<enabled>true</enabled>"), "application/xml")
            return
        if path.startswith("/ISAPI/Smart/LineDetection/"):
            self._send(200, xml("LineDetection", "<enabled>true</enabled>"), "application/xml")
            return
        if path.startswith("/ISAPI/Smart/FieldDetection/"):
            self._send(200, xml("FieldDetection", "<enabled>true</enabled>"), "application/xml")
            return
        if path == "/ISAPI/Event/notification/alertStream":
            self.send_response(200)
            self.send_header("Content-Type", "multipart/mixed")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                for _ in range(60):
                    event = None
                    with STATE_LOCK:
                        if STATE["events"]:
                            event = STATE["events"].pop(0)
                    if event:
                        self.wfile.write(event_xml(event["type"], int(event["channel"])))
                        self.wfile.flush()
                    time.sleep(1)
            except Exception:
                pass
            return
        if path.startswith("/ISAPI/Event/notification/httpHosts/"):
            with STATE_LOCK:
                cfg = dict(STATE["push_config"])
            body = (
                f"<id>1</id><hostName>{cfg.get('host','')}</hostName>"
                f"<url>{cfg.get('url','')}</url><portNo>{cfg.get('port','80')}</portNo>"
            )
            self._send(200, xml("HttpHostNotification", body), "application/xml")
            return
        self._send(404, "unsupported Hikvision lab endpoint")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if KIND == "onvif":
            if not auth_challenge(self):
                return
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8", "replace")
            self._onvif_post(path, raw)
            return
        if KIND == "hikvision" and path == "/ISAPI/ContentMgmt/search":
            if not auth_challenge(self):
                return
            with STATE_LOCK:
                archive = bool(STATE["archive"])
            match = ""
            if archive:
                match = (
                    "<searchMatchItem><trackID>101</trackID>"
                    f"<startTime>{now_iso()}</startTime><endTime>{now_iso()}</endTime>"
                    "<mediaSegmentDescriptor><playbackURI>"
                    "rtsp://10.77.0.21/Streaming/tracks/101?starttime=20260927T000000Z&amp;endtime=20260927T000100Z"
                    "</playbackURI></mediaSegmentDescriptor></searchMatchItem>"
                )
            body = f"<responseStatusStrg>OK</responseStatusStrg>{match}"
            self._send(200, xml("CMSearchResult", body), "application/xml")
            return
        if KIND == "hikvision" and path == "/ISAPI/ContentMgmt/download":
            if not auth_challenge(self):
                return
            with STATE_LOCK:
                archive = bool(STATE["archive"])
            self._send(200 if archive else 404, recorded_clip_bytes() if archive else b"",
                       "video/mp4")
            return
        self._send(404, "unsupported POST")

    def _onvif_post(self, path: str, raw: str):
        host = self.headers.get("Host", "127.0.0.1").split(":")[0]
        if "GetDeviceInformation" in raw:
            body = (
                "<tds:GetDeviceInformationResponse>"
                "<tds:Manufacturer>WatchLog Lab</tds:Manufacturer>"
                f"<tds:Model>{MODEL}</tds:Model><tds:FirmwareVersion>1.0</tds:FirmwareVersion>"
                f"<tds:SerialNumber>{SERIAL}</tds:SerialNumber><tds:HardwareId>LAB</tds:HardwareId>"
                "</tds:GetDeviceInformationResponse>"
            )
        elif "GetCapabilities" in raw:
            body = (
                "<tds:GetCapabilitiesResponse><tds:Capabilities>"
                f"<tt:Media><tt:XAddr>http://{host}/onvif/media_service</tt:XAddr></tt:Media>"
                f"<tt:Events><tt:XAddr>http://{host}/onvif/events_service</tt:XAddr></tt:Events>"
                "</tds:Capabilities></tds:GetCapabilitiesResponse>"
            )
        elif "GetProfiles" in raw:
            profiles = "".join(
                f'<trt:Profiles token="profile{i}"><tt:Name>Lab Camera {i}</tt:Name>'
                f'<tt:VideoSourceConfiguration><tt:SourceToken>source{i}</tt:SourceToken>'
                "</tt:VideoSourceConfiguration></trt:Profiles>" for i in range(1, CHANNELS + 1)
            )
            body = f"<trt:GetProfilesResponse>{profiles}</trt:GetProfilesResponse>"
        elif "GetSnapshotUri" in raw:
            body = (
                "<trt:GetSnapshotUriResponse><trt:MediaUri>"
                f"<tt:Uri>http://{host}/snapshot/1</tt:Uri>"
                "</trt:MediaUri></trt:GetSnapshotUriResponse>"
            )
        elif "GetStreamUri" in raw:
            channel = 1
            for idx in range(1, CHANNELS + 1):
                if f"profile{idx}" in raw:
                    channel = idx
                    break
            body = (
                "<trt:GetStreamUriResponse><trt:MediaUri>"
                f"<tt:Uri>rtsp://{host}:{RTSP_PORT}/cam{channel}</tt:Uri>"
                "</trt:MediaUri></trt:GetStreamUriResponse>"
            )
        elif "CreatePullPointSubscription" in raw:
            body = (
                "<tev:CreatePullPointSubscriptionResponse><tev:SubscriptionReference>"
                f"<wsa:Address>http://{host}/onvif/pullpoint</wsa:Address>"
                "</tev:SubscriptionReference></tev:CreatePullPointSubscriptionResponse>"
            )
        elif "PullMessages" in raw:
            body = "<tev:PullMessagesResponse/>"
        else:
            body = "<tds:GetSystemDateAndTimeResponse/>"
        envelope = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
            'xmlns:wsa="http://www.w3.org/2005/08/addressing" '
            'xmlns:tds="http://www.onvif.org/ver10/device/wsdl" '
            'xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
            'xmlns:tev="http://www.onvif.org/ver10/events/wsdl" '
            'xmlns:tt="http://www.onvif.org/ver10/schema"><s:Body>'
            + body + "</s:Body></s:Envelope>"
        )
        self._send(200, envelope, "application/soap+xml")

    def do_PUT(self):
        if KIND != "hikvision":
            self._send(404, "unsupported PUT")
            return
        if not auth_challenge(self):
            return
        parsed = urlparse(self.path)
        if parsed.path.startswith("/ISAPI/Event/notification/httpHosts"):
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8", "replace")
            def between(tag):
                a, b = f"<{tag}>", f"</{tag}>"
                return raw.split(a, 1)[1].split(b, 1)[0] if a in raw and b in raw else ""
            with STATE_LOCK:
                STATE["push_config"] = {
                    "host": between("hostName") or between("ipAddress"),
                    "url": between("url"), "port": between("portNo") or "80"
                }
            self._send(200, xml("ResponseStatus", "<statusCode>1</statusCode><statusString>OK</statusString>"),
                       "application/xml")
            return
        self._send(404, "unsupported PUT")


class ControlHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, status, obj):
        raw = json.dumps(obj, indent=2, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path != "/state":
            self._send(404, {"error": "not found"})
            return
        with STATE_LOCK:
            self._send(200, dict(STATE))

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/scenario/"):
            name = parsed.path.rsplit("/", 1)[1]
            with STATE_LOCK:
                STATE.update({
                    "scenario": name, "login_delay": 0.0, "storage": "normal",
                    "archive": True, "offline_channels": [], "recording_off": []
                })
                if name == "slow-login":
                    STATE["login_delay"] = 8.0
                elif name == "storage-fault":
                    STATE["storage"] = "Abnormal"
                elif name == "archive-empty":
                    STATE["archive"] = False
                elif name == "camera-2-offline":
                    STATE["offline_channels"] = [2]
                elif name == "recording-3-off":
                    STATE["recording_off"] = [3]
                elif name != "healthy":
                    self._send(400, {"error": "unknown scenario", "scenario": name})
                    return
            self._send(200, {"ok": True, "scenario": name})
            return
        if parsed.path.startswith("/event/"):
            kind = parsed.path.rsplit("/", 1)[1]
            ch = int((parse_qs(parsed.query).get("channel") or ["1"])[0])
            if kind not in ("motion", "person", "vehicle", "video-loss", "tamper"):
                self._send(400, {"error": "unknown event"})
                return
            with STATE_LOCK:
                STATE["events"].append({"type": kind, "channel": ch, "ts": now_iso()})
                if kind == "video-loss" and ch not in STATE["offline_channels"]:
                    STATE["offline_channels"].append(ch)
            self._send(200, {"ok": True, "event": kind, "channel": ch})
            return
        self._send(404, {"error": "not found"})


class QuietTCP(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class NullTCP(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            self.request.settimeout(1.0)
            self.request.recv(64)
        except Exception:
            pass


def tcp_listener(port: int, delay: float = 0.0):
    if port <= 0:
        return
    if delay:
        time.sleep(delay)
    server = QuietTCP(("0.0.0.0", port), NullTCP)
    print(f"[{KIND}] TCP signature/listener ready on {port} after {delay:.1f}s", flush=True)
    server.serve_forever()


def ws_discovery_responder():
    if KIND != "onvif":
        return
    group = "239.255.255.250"
    port = 3702
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("", port))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(group) + socket.inet_aton("0.0.0.0"))
    except OSError as exc:
        print(f"[onvif] WS-Discovery disabled: {exc}", flush=True)
        return
    while True:
        data, addr = sock.recvfrom(65535)
        if b"Probe" not in data:
            continue
        msg_id = f"uuid:{uuid.uuid4()}"
        xaddr = f"http://{socket.gethostbyname(socket.gethostname())}/onvif/device_service"
        response = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
            'xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
            'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
            'xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
            '<s:Header><a:MessageID>' + msg_id + '</a:MessageID>'
            '<a:RelatesTo>uuid:watchlog-lab-probe</a:RelatesTo>'
            '<a:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/ProbeMatches</a:Action>'
            '</s:Header><s:Body><d:ProbeMatches><d:ProbeMatch>'
            f'<a:EndpointReference><a:Address>urn:uuid:{SERIAL}</a:Address></a:EndpointReference>'
            '<d:Types>dn:NetworkVideoTransmitter</d:Types>'
            f'<d:Scopes>onvif://www.onvif.org/name/{NAME.replace(" ","_")} '
            f'onvif://www.onvif.org/hardware/{MODEL}</d:Scopes>'
            f'<d:XAddrs>{xaddr}</d:XAddrs><d:MetadataVersion>1</d:MetadataVersion>'
            '</d:ProbeMatch></d:ProbeMatches></s:Body></s:Envelope>'
        ).encode()
        sock.sendto(response, addr)


def main():
    print(f"WatchLog lab device starting: kind={KIND} model={MODEL} channels={CHANNELS}", flush=True)
    threading.Thread(target=tcp_listener, args=(SDK_PORT, SDK_DELAY), daemon=True).start()
    if RTSP_PORT > 0 and not REAL_RTSP:
        threading.Thread(target=tcp_listener, args=(RTSP_PORT, 0), daemon=True).start()
    threading.Thread(target=ws_discovery_responder, daemon=True).start()

    control = ThreadingHTTPServer(("0.0.0.0", CONTROL_PORT), ControlHandler)
    threading.Thread(target=control.serve_forever, daemon=True).start()
    print(f"[{KIND}] control API on :{CONTROL_PORT}", flush=True)

    httpd = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), DeviceHandler)
    print(f"[{KIND}] HTTP API on :{HTTP_PORT}; user={USERNAME}; password={PASSWORD}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
