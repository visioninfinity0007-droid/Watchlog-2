"""
Hikvision ISAPI driver.

Covers Hikvision NVRs/DVRs and HiLook, Hikvision's own budget line, which
shares the ISAPI stack. Between them these are the most widely installed
units in Pakistan.

ISAPI is HTTP + XML with HTTP Digest auth. The three calls used:

    GET /ISAPI/System/deviceInfo                      identity
    GET /ISAPI/ContentMgmt/InputProxy/channels        IP channels (NVR)
    GET /ISAPI/System/Video/inputs/channels           analog channels (DVR)
    GET /ISAPI/Event/notification/alertStream         live events

alertStream is a long-lived multipart/mixed response the device pushes
into as things happen. The agent opens it outbound; the NVR never
initiates anything.

NOT YET VERIFIED AGAINST HARDWARE. The endpoints and payload shapes are
per Hikvision's ISAPI developer guide, but no real device has been
available. Run `watchlog_agent.py --probe` against a real unit before
trusting it.
"""

from __future__ import annotations

import base64
import ipaddress
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Iterator

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

from .base import (Channel, DeviceInfo, DriverError, Event, NvrDriver,
                   explain)

# ISAPI namespaces vary by firmware; strip them rather than guess.
_TAG = re.compile(r"\{.*?\}")


def _strip_ns(elem: ET.Element) -> ET.Element:
    for e in elem.iter():
        e.tag = _TAG.sub("", e.tag)
    return elem


def _text(node: ET.Element | None, path: str) -> str | None:
    if node is None:
        return None
    found = node.find(path)
    return found.text.strip() if found is not None and found.text else None


def _parse_ts(raw: str | None) -> datetime:
    """ISAPI emits ISO 8601, sometimes with a local offset, sometimes naive."""
    if not raw:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# Hikvision eventType values worth keeping, mapped to our vocabulary.
EVENT_TYPE_MAP = {
    "vmd": "motion",
    "motiondetection": "motion",
    "linedetection": "line_crossing",
    "fielddetection": "intrusion",
    "regionexiting": "region_exit",
    "regionentrance": "region_entry",
    "tamperdetection": "tamper",
    "shelteralarm": "tamper",
    "videoloss": "video_loss",
    "diskfull": "disk_full",
    "diskerror": "disk_error",
    "facedetection": "face",
    "peopledetection": "person",
    "vehicledetection": "vehicle",
}

# Hikvision repeats an active alarm every second for as long as it lasts.
# Collapsing a burst into one event is the difference between 5 rows and
# 500 for a single person walking past a camera.
BURST_WINDOW_SECONDS = 30

# A camera that will not produce a still must not stall the event loop.
SNAPSHOT_TIMEOUT = 10
JPEG_MAGIC = bytes([0xFF, 0xD8])   # a JPEG always starts FF D8

# Field DS-7608NI-Q1: setup/auth succeeded, but a permanently-open alertStream
# plus independent health/recovery logins could make the recorder's small web
# stack refuse later sessions. Slice the stream and capture ONE rotating still
# between slices on the SAME session. Eight channels => about one proof image
# per camera every six minutes, while native alarms continue to pass immediately.
HIKVISION_STREAM_SLICE_SECONDS = 30
HIKVISION_SAMPLE_MAX_BYTES = 2_000_000

# One Hikvision web stack, one authenticated HTTP operation at a time. Field evidence on
# DS-7608NI-Q1 showed that parallel Digest sessions (alert stream + health + recovery)
# can make a perfectly reachable recorder start returning timeouts. This lock is module-
# global so separate driver instances used by incident/recovery workers serialize too.
HIKVISION_HTTP_LOCK = threading.RLock()



class HikvisionDriver(NvrDriver):
    name = "hikvision-isapi"
    verified_against_hardware = False

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.s = requests.Session()
        # Recorder traffic is LAN-local. Never inherit a corporate/system HTTP proxy:
        # proxy auto-config can turn a 192.168.x.x login into a minute-long external
        # timeout even though the recorder is directly reachable.
        self.s.trust_env = False
        # Hikvision commonly redirects HTTP -> HTTPS and ships a self-signed certificate.
        # Disable CA verification for this LAN-only recorder session even when base_url
        # starts as http://, otherwise a redirect can fail after the browser works fine.
        self.s.verify = False
        self.s.auth = HTTPDigestAuth(self.username, self.password)
        self._last_emitted: dict[tuple[str, str], datetime] = {}
        self.last_activity_monotonic = 0.0

    # -- helpers --------------------------------------------------------

    def _get(self, path: str, **kw) -> requests.Response:
        url = self.base_url + path
        timeout = kw.pop("timeout", self.timeout)
        with HIKVISION_HTTP_LOCK:
            try:
                r = self.s.get(url, timeout=timeout, **kw)
            except requests.RequestException as e:
                raise DriverError(f"{url}: {explain(e)}") from e
            if r.status_code == 401:
                # HTTPDigestAuth already performed the Digest challenge/response. If the
                # final 401 still advertises Digest, the credentials were rejected; doing
                # another Basic request only doubles the field wait. Fall back to Basic
                # only when the recorder actually advertises Basic without Digest.
                challenge = (r.headers.get("WWW-Authenticate") or "").lower()
                if "basic" in challenge and "digest" not in challenge:
                    self.s.auth = HTTPBasicAuth(self.username, self.password)
                    r = self.s.get(url, timeout=timeout, **kw)
            if r.status_code >= 400:
                raise DriverError(f"{url}: HTTP {r.status_code} {r.text[:200]}")
            self.last_activity_monotonic = time.monotonic()
            return r

    def _xml(self, path: str) -> ET.Element:
        try:
            return _strip_ns(ET.fromstring(self._get(path).content))
        except ET.ParseError as e:
            raise DriverError(f"{path}: not XML ({e})") from e

    def _put(self, path: str, body: str) -> requests.Response:
        url = self.base_url + path
        with HIKVISION_HTTP_LOCK:
            try:
                r = self.s.put(url, data=body.encode(), timeout=self.timeout,
                               headers={"Content-Type": "application/xml"})
            except requests.RequestException as e:
                raise DriverError(f"{url}: {explain(e)}") from e
            if r.status_code == 401:
                challenge = (r.headers.get("WWW-Authenticate") or "").lower()
                if "basic" in challenge and "digest" not in challenge:
                    self.s.auth = HTTPBasicAuth(self.username, self.password)
                    r = self.s.put(url, data=body.encode(), timeout=self.timeout,
                                   headers={"Content-Type": "application/xml"})
            if r.status_code >= 400:
                raise DriverError(f"{url}: HTTP {r.status_code} {r.text[:200]}")
            self.last_activity_monotonic = time.monotonic()
            return r

    def configure_push(self, url: str, host_id: int = 1) -> dict:
        """
        Point this recorder's alarm notifications at `url` (the WatchLog
        push bridge, with the site token in the path). This is the "PC-free
        / recorder-push" setup: after this, the NVR POSTs every event to us
        on its own, no on-site agent needed.

        UNVALIDATED against real hardware. Hikvision's httpHosts schema
        varies across firmware families (ISAPI/2011 vs 2020), and some OEM
        units reject or silently ignore it. It must be proven on a real
        Hikvision unit before being offered. Written here so the path is
        complete in code and ready to test, not because it is trusted yet.

        On a recorder that supports it, WatchLog then receives events with
        NO PC on site. On one that does not, we fall back to an agent.
        """
        from urllib.parse import urlparse
        u = urlparse(url)
        host = u.hostname or ""
        port = u.port or (443 if u.scheme == "https" else 80)
        path = u.path or "/"
        proto = "HTTPS" if u.scheme == "https" else "HTTP"
        try:
            ipaddress.ip_address(host)
            addressing = "ipaddress"
            address_xml = f"<ipAddress>{host}</ipAddress>"
        except ValueError:
            # Production bridge uses a DNS/sslip hostname. Hikvision requires
            # addressingFormatType=hostname + hostName for this shape; putting a DNS
            # name into ipAddress is accepted by some simulators but rejected by NVRs.
            addressing = "hostname"
            address_xml = f"<hostName>{host}</hostName>"
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<HttpHostNotification xmlns="http://www.isapi.org/ver20/XMLSchema" version="2.0">'
            f'<id>{host_id}</id><url>{path}</url>'
            f'<protocolType>{proto}</protocolType>'
            '<parameterFormatType>XML</parameterFormatType>'
            f'<addressingFormatType>{addressing}</addressingFormatType>'
            f'{address_xml}<portNo>{port}</portNo>'
            '<httpAuthenticationMethod>none</httpAuthenticationMethod>'
            '<uploadImagesDataType>binary</uploadImagesDataType>'
            '<httpBroken>true</httpBroken>'
            '<SubscribeEvent>'
            '<heartbeat>30</heartbeat>'
            '<eventMode>all</eventMode>'
            '</SubscribeEvent>'
            '</HttpHostNotification>')
        # MUST return the same {applied, verified, detail} contract the Dahua driver
        # returns. Returning None made provision_recorder_push read `(out or {}).get(...)`
        # as all-False, so a Hikvision push that actually worked was always reported as
        # failed -- and the customer told to expect no PC-free reporting.
        try:
            self._put(f"/ISAPI/Event/notification/httpHosts/{host_id}", body)
        except DriverError as e:
            return {"applied": False, "verified": False,
                    "detail": f"recorder rejected httpHosts config: {str(e)[:120]}"}

        # Read back: the recorder is the source of truth, not our request.
        try:
            root = self._xml(f"/ISAPI/Event/notification/httpHosts/{host_id}")
        except Exception:  # noqa: BLE001 - written but unverifiable
            return {"applied": True, "verified": False,
                    "detail": "config written but could not be read back"}
        got_host = (_text(root, "ipAddress") or _text(root, "hostName") or "").strip()
        got_url = (_text(root, "url") or "").strip()
        if got_host == host and (not got_url or got_url == path):
            return {"applied": True, "verified": True,
                    "detail": f"recorder will POST alarms to {host}:{port}{path}"}
        return {"applied": True, "verified": False,
                "detail": ("recorder did not retain the httpHosts config "
                           f"(ipAddress={got_host!r} url={got_url!r}); this model likely "
                           "needs an on-site agent")}

    # -- interface ------------------------------------------------------

    def probe(self) -> DeviceInfo:
        root = self._xml("/ISAPI/System/deviceInfo")
        count = _text(root, "videoInputPortNums")
        return DeviceInfo(
            vendor="Hikvision",
            model=_text(root, "model"),
            firmware=_text(root, "firmwareVersion"),
            serial=_text(root, "serialNumber"),
            channel_count=int(count) if count and count.isdigit() else None,
            driver=self.name,
            raw={"deviceName": _text(root, "deviceName"),
                 "deviceType": _text(root, "deviceType")},
        )

    def list_channels(self) -> list[Channel]:
        out: list[Channel] = []

        # NVRs expose IP channels here.
        try:
            root = self._xml("/ISAPI/ContentMgmt/InputProxy/channels")
            for ch in root.findall(".//InputProxyChannel"):
                cid = _text(ch, "id")
                if cid:
                    out.append(Channel(channel=cid, name=_text(ch, "name")))
        except DriverError:
            pass

        # DVRs (and NVR analog inputs) expose them here.
        if not out:
            try:
                root = self._xml("/ISAPI/System/Video/inputs/channels")
                for ch in root.findall(".//VideoInputChannel"):
                    cid = _text(ch, "id")
                    if cid:
                        out.append(Channel(channel=cid, name=_text(ch, "name")))
            except DriverError:
                pass

        if not out:
            info = self.probe()
            n = info.channel_count or 0
            out = [Channel(channel=str(i), name=f"Channel {i}")
                   for i in range(1, n + 1)]
        return out

    def capabilities(self) -> dict:
        """
        Report per-channel analytics from ISAPI. Read-only.

        UNVALIDATED against real hardware. Hikvision's Smart endpoints vary
        across firmware (some units expose /ISAPI/Smart/<fn>/<ch>, others
        gate it behind AcuSense or a channel's IP-camera capabilities), so
        this is written to the documented schema but must be proven on a
        real unit. It fails safe: a channel whose config cannot be read
        reports motion only rather than crashing.
        """
        def _active(path: str) -> bool:
            try:
                el = self._xml(path)
            except DriverError:
                return None            # unknown / not supported
            en = el.find(".//enabled")
            return en is not None and (en.text or "").strip().lower() == "true"

        out = []
        for c in self.list_channels():
            ch = c.channel
            motion = _active(f"/ISAPI/System/Video/inputs/channels/{ch}/motionDetection")
            line = _active(f"/ISAPI/Smart/LineDetection/{ch}")
            field = _active(f"/ISAPI/Smart/FieldDetection/{ch}")
            analytics = [
                {"key": "motion", "label": "Motion detection",
                 "supported": motion is not None,
                 "active": bool(motion), "geometry": False},
                {"key": "line_crossing", "label": "Line crossing",
                 "supported": line is not None,
                 "active": bool(line), "geometry": True},
                {"key": "intrusion", "label": "Intrusion zone",
                 "supported": field is not None,
                 "active": bool(field), "geometry": True},
            ]
            out.append({"channel": ch, "name": c.name, "analytics": analytics})
        return {"channels": out}

    def get_snapshot(self, channel: str) -> bytes | None:
        """
        ISAPI still image.

        Channel numbering here is the awkward part: the streaming API
        uses <channel><stream> concatenated, so channel 2 main stream is
        201, not 2. Getting this wrong returns someone else's camera,
        which on a security system is worse than returning nothing.
        """
        try:
            ch = int(str(channel))
        except (TypeError, ValueError):
            return None
        for path in (f"/ISAPI/Streaming/channels/{ch}01/picture",
                     f"/ISAPI/Streaming/channels/{ch}/picture"):
            try:
                r = self._get(path, timeout=SNAPSHOT_TIMEOUT)
            except DriverError:
                continue
            if r.content[:2] == JPEG_MAGIC:     # JPEG magic
                return r.content
        return None

    def stream_events(self, stop: threading.Event) -> Iterator[Event]:
        """Consume native ISAPI alarms plus bounded rotating visual samples.

        A quiet recorder can legitimately emit no alarms for hours. WatchLog still needs
        recorder-backed camera data, so each bounded alert-stream slice is followed by
        ONE still from the next channel, using this same authenticated driver/session.
        """
        url = self.base_url + "/ISAPI/Event/notification/alertStream"
        try:
            sample_channels = [str(c.channel) for c in self.list_channels() if c.channel]
        except Exception:  # noqa: BLE001 — native events still work without sampling
            sample_channels = []
        sample_index = 0

        while not stop.is_set():
            r = None
            buf = b""
            started = time.monotonic()
            try:
                with HIKVISION_HTTP_LOCK:
                    r = self.s.get(
                        url,
                        stream=True,
                        timeout=(self.timeout, HIKVISION_STREAM_SLICE_SECONDS),
                    )
                    if r.status_code >= 400:
                        raise DriverError(f"alertStream: HTTP {r.status_code}")
                    self.last_activity_monotonic = time.monotonic()

                    try:
                        for chunk in r.iter_content(chunk_size=1024):
                            if stop.is_set():
                                break
                            if not chunk:
                                continue
                            self.last_activity_monotonic = time.monotonic()
                            buf += chunk
                            while b"</EventNotificationAlert>" in buf:
                                doc, _, buf = buf.partition(b"</EventNotificationAlert>")
                                xml_start = doc.find(b"<EventNotificationAlert")
                                if xml_start < 0:
                                    continue
                                raw = doc[xml_start:] + b"</EventNotificationAlert>"
                                ev = self._parse_alert(raw)
                                if ev:
                                    yield ev
                            if len(buf) > 1_000_000:
                                buf = b""
                            if time.monotonic() - started >= HIKVISION_STREAM_SLICE_SECONDS:
                                break
                    except requests.RequestException as exc:
                        low = str(exc).lower()
                        if "read timed out" not in low and "read timeout" not in low:
                            raise DriverError(f"alertStream: {exc}") from exc
            except requests.RequestException as exc:
                raise DriverError(f"alertStream: {exc}") from exc
            finally:
                if r is not None:
                    r.close()

            if stop.is_set():
                break

            if sample_channels:
                channel = sample_channels[sample_index % len(sample_channels)]
                sample_index += 1
                try:
                    raw = self.get_snapshot(channel)
                except Exception:  # noqa: BLE001 — sampling never kills native monitoring
                    raw = None
                if raw and len(raw) <= HIKVISION_SAMPLE_MAX_BYTES:
                    yield Event(
                        channel=channel,
                        event_type="visual_sample",
                        device_ts=datetime.now(timezone.utc),
                        device_event_id=(
                            f"hikvision-sample-{channel}-"
                            f"{int(time.time() // HIKVISION_STREAM_SLICE_SECONDS)}"
                        ),
                        payload={
                            "vendor": "hikvision",
                            "source": "periodic_snapshot",
                            "sample": True,
                        },
                        snapshot_b64=base64.b64encode(raw).decode("ascii"),
                    )

    # -- parsing --------------------------------------------------------

    def _parse_alert(self, raw: bytes) -> Event | None:
        try:
            root = _strip_ns(ET.fromstring(raw))
        except ET.ParseError:
            return None

        etype_raw = (_text(root, "eventType") or "").strip()
        state = (_text(root, "eventState") or "").lower()
        if state == "inactive":
            return None

        # Hikvision keeps alertStream warm by emitting videoloss with
        # activePostCount 0 whenever nothing is happening. A genuine
        # video-loss alarm carries a non-zero count.
        #
        # Both naive options are wrong: dropping all videoloss hides a
        # real fault on a security system, and keeping all of it fills
        # the database with heartbeats and makes every fault report
        # meaningless. The count is the discriminator.
        active_post = (_text(root, "activePostCount") or "").strip()
        if etype_raw.lower() == "videoloss" and active_post in ("0", ""):
            return None

        if not etype_raw:
            return None

        etype = EVENT_TYPE_MAP.get(etype_raw.lower()) or etype_raw.lower()

        channel = (_text(root, "channelID")
                   or _text(root, "dynChannelID")
                   or _text(root, "channelName") or "1")
        ts = _parse_ts(_text(root, "dateTime"))

        # Collapse the once-per-second repeat of a continuing alarm.
        key = (str(channel), etype)
        last = self._last_emitted.get(key)
        if last and (ts - last).total_seconds() < BURST_WINDOW_SECONDS:
            return None
        self._last_emitted[key] = ts

        return Event(
            channel=str(channel),
            event_type=etype,
            device_ts=ts,
            device_event_id=None,     # ISAPI alerts carry no stable id
            payload={"vendor": "hikvision", "eventType": etype_raw,
                     "eventDescription": _text(root, "eventDescription"),
                     "activePostCount": _text(root, "activePostCount")},
        )

    def close(self) -> None:
        self.s.close()
