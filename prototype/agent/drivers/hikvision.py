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

import re
import threading
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

# Archive search/download helpers share the driver's requests.Session. Serialize
# those bounded HTTP operations so one session is never mutated concurrently.
HIKVISION_HTTP_LOCK = threading.RLock()



class HikvisionDriver(NvrDriver):
    name = "hikvision-isapi"
    verified_against_hardware = False

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.s = requests.Session()
        self.s.auth = HTTPDigestAuth(self.username, self.password)
        self._last_emitted: dict[tuple[str, str], datetime] = {}

    # -- helpers --------------------------------------------------------

    def _get(self, path: str, **kw) -> requests.Response:
        url = self.base_url + path
        try:
            r = self.s.get(url, timeout=kw.pop("timeout", self.timeout), **kw)
        except requests.RequestException as e:
            raise DriverError(f"{url}: {explain(e)}") from e
        if r.status_code == 401:
            # A few OEM firmwares only do Basic.
            self.s.auth = HTTPBasicAuth(self.username, self.password)
            r = self.s.get(url, timeout=self.timeout, **kw)
        if r.status_code >= 400:
            raise DriverError(f"{url}: HTTP {r.status_code} {r.text[:200]}")
        return r

    def _xml(self, path: str) -> ET.Element:
        try:
            return _strip_ns(ET.fromstring(self._get(path).content))
        except ET.ParseError as e:
            raise DriverError(f"{path}: not XML ({e})") from e

    def _put(self, path: str, body: str) -> requests.Response:
        url = self.base_url + path
        try:
            r = self.s.put(url, data=body.encode(), timeout=self.timeout,
                           headers={"Content-Type": "application/xml"})
        except requests.RequestException as e:
            raise DriverError(f"{url}: {explain(e)}") from e
        if r.status_code == 401:
            self.s.auth = HTTPBasicAuth(self.username, self.password)
            r = self.s.put(url, data=body.encode(), timeout=self.timeout,
                           headers={"Content-Type": "application/xml"})
        if r.status_code >= 400:
            raise DriverError(f"{url}: HTTP {r.status_code} {r.text[:200]}")
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
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<HttpHostNotification xmlns="http://www.hikvision.com/ver20/XMLSchema">'
            f'<id>{host_id}</id><url>{path}</url>'
            f'<protocolType>{proto}</protocolType>'
            '<parameterFormatType>XML</parameterFormatType>'
            f'<addressingFormatType>ipaddress</addressingFormatType>'
            f'<ipAddress>{host}</ipAddress><portNo>{port}</portNo>'
            '<httpAuthenticationMethod>none</httpAuthenticationMethod>'
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
        got_host = (_text(root, "ipAddress") or "").strip()
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
        """Read-only per-channel Hikvision analytic capability/config census.

        Endpoint support varies by recorder/camera firmware, so every probe is
        independent. A missing/rejected endpoint is UNKNOWN/unsupported; it
        never causes the whole recorder inspection to fail.
        """
        def _read(path: str):
            try:
                return self._xml(path)
            except DriverError:
                return None

        def _enabled(root) -> bool | None:
            if root is None:
                return None
            for el in root.iter():
                if el.tag.lower() in ("enabled", "enable"):
                    text = (el.text or "").strip().lower()
                    if text in ("true", "1", "yes", "on"):
                        return True
                    if text in ("false", "0", "no", "off"):
                        return False
            return None

        def _contains(root, *needles: str) -> bool:
            if root is None:
                return False
            blob = ET.tostring(root, encoding="unicode").lower()
            return all(n.lower() in blob for n in needles)

        out = []
        for c in self.list_channels():
            ch = c.channel
            motion_root = _read(f"/ISAPI/System/Video/inputs/channels/{ch}/motionDetection")
            motion_cap = _read(
                f"/ISAPI/System/Video/inputs/channels/{ch}/motionDetection/capabilities"
            )
            line_root = _read(f"/ISAPI/Smart/LineDetection/{ch}")
            field_root = _read(f"/ISAPI/Smart/FieldDetection/{ch}")
            entrance_root = _read(f"/ISAPI/Smart/RegionEntrance/{ch}")
            exit_root = _read(f"/ISAPI/Smart/RegionExiting/{ch}")

            motion = _enabled(motion_root)
            line = _enabled(line_root)
            field = _enabled(field_root)
            entrance = _enabled(entrance_root)
            region_exit = _enabled(exit_root)

            # Motion Detection 2.0 / target classification is exposed differently
            # across firmware. Treat human+vehicle as supported only when the
            # recorder's own capability/config XML explicitly names those targets.
            hv_supported = (
                _contains(motion_cap, "human", "vehicle")
                or _contains(motion_root, "human", "vehicle")
            )
            hv_active = False
            if hv_supported and motion_root is not None:
                blob = ET.tostring(motion_root, encoding="unicode").lower()
                hv_active = (
                    ("human" in blob or "pedestrian" in blob)
                    and "vehicle" in blob
                    and motion is True
                )

            analytics = [
                {"key": "motion", "label": "Motion detection",
                 "supported": motion_root is not None,
                 "active": motion is True, "geometry": False},
                {"key": "human_vehicle", "label": "Human / vehicle classification",
                 "supported": hv_supported,
                 "active": hv_active, "geometry": False},
                {"key": "line_crossing", "label": "Line crossing",
                 "supported": line_root is not None,
                 "active": line is True, "geometry": True},
                {"key": "intrusion", "label": "Intrusion zone",
                 "supported": field_root is not None,
                 "active": field is True, "geometry": True},
                {"key": "region_entry", "label": "Region entrance",
                 "supported": entrance_root is not None,
                 "active": entrance is True, "geometry": True},
                {"key": "region_exit", "label": "Region exit",
                 "supported": exit_root is not None,
                 "active": region_exit is True, "geometry": True},
            ]
            out.append({"channel": ch, "name": c.name, "analytics": analytics})
        return {"channels": out}

    def get_clock(self) -> dict:
        """Read recorder clock/timezone/NTP state through Hikvision ISAPI."""
        try:
            root = self._xml("/ISAPI/System/time")
        except DriverError:
            return {"supported": False}

        current = (_text(root, "localTime") or _text(root, "time") or
                   _text(root, "currentTime"))
        timezone = (_text(root, "timeZone") or _text(root, "timezone"))
        mode = (_text(root, "timeMode") or _text(root, "mode") or "").strip().lower()
        ntp_enabled = True if "ntp" in mode else (False if mode else None)
        ntp_server = None

        for path in ("/ISAPI/System/time/ntpServers",
                     "/ISAPI/System/time/ntpServers/1"):
            try:
                ntp = self._xml(path)
            except DriverError:
                continue
            ntp_server = (_text(ntp, ".//hostName") or _text(ntp, ".//ipAddress")
                          or _text(ntp, ".//serverName"))
            if ntp_server:
                break

        dst_text = (_text(root, "dstEnabled") or _text(root, "DSTEnabled") or "")
        dst_enabled = None
        if dst_text:
            dst_enabled = dst_text.strip().lower() in ("true", "1", "yes", "on")
        return {
            "supported": True,
            "current_time": current,
            "timezone": timezone,
            "dst_enabled": dst_enabled,
            "ntp_enabled": ntp_enabled,
            "ntp_server": ntp_server,
        }

    def storage_status(self) -> dict:
        """Read current storage-health state; never starts SMART/bad-sector tests."""
        try:
            root = self._xml("/ISAPI/Smart/storageDetection")
        except DriverError:
            return {"supported": False, "state": None}

        health = (_text(root, "healthState") or "").strip().lower()
        state = None
        if health == "good":
            state = "ok"
        elif health in ("bad", "damage", "damaged", "failed", "failure"):
            state = "fault"
        elif health in ("warning", "degraded"):
            state = "degraded"

        detail = {
            "health_state": health or None,
            "bad_blocks": _text(root, "badBlocks"),
        }
        return {
            "supported": True,
            "state": state,
            "native_fatal": state == "fault",
            "native_lowspace": False,
            "detail": detail,
        }

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
        """
        Consume /ISAPI/Event/notification/alertStream.

        The device holds the connection open and writes a multipart body,
        one XML document per alarm. It also emits keep-alive
        videoloss/heartbeat frames, which are filtered out below.
        """
        url = self.base_url + "/ISAPI/Event/notification/alertStream"
        try:
            r = self.s.get(url, stream=True, timeout=(self.timeout, 90))
        except requests.RequestException as e:
            raise DriverError(f"alertStream: {e}") from e
        if r.status_code >= 400:
            raise DriverError(f"alertStream: HTTP {r.status_code}")

        buf = b""
        try:
            for chunk in r.iter_content(chunk_size=1024):
                if stop.is_set():
                    break
                if not chunk:
                    continue
                buf += chunk
                # Documents arrive back to back; split on the closing tag.
                while b"</EventNotificationAlert>" in buf:
                    doc, _, buf = buf.partition(b"</EventNotificationAlert>")
                    start = doc.find(b"<EventNotificationAlert")
                    if start < 0:
                        continue
                    raw = doc[start:] + b"</EventNotificationAlert>"
                    ev = self._parse_alert(raw)
                    if ev:
                        yield ev
                if len(buf) > 1_000_000:      # runaway guard
                    buf = b""
        finally:
            r.close()

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

        targets = []
        for node in root.iter():
            tag = node.tag.lower()
            text = (node.text or "").strip().lower()
            if "targettype" in tag or tag in ("objecttype", "targetclass"):
                for raw_target in re.split(r"[,;|\\s]+", text):
                    if raw_target in ("human", "person", "pedestrian"):
                        targets.append("human")
                    elif raw_target in ("vehicle", "car", "motorvehicle"):
                        targets.append("vehicle")
        targets = sorted(set(targets))

        smart_native = etype_raw.lower() in {
            "linedetection", "fielddetection", "regionexiting", "regionentrance",
            "facedetection", "peopledetection", "vehicledetection"
        } or bool(targets)

        return Event(
            channel=str(channel),
            event_type=etype,
            device_ts=ts,
            device_event_id=None,     # ISAPI alerts carry no stable id
            payload={"vendor": "hikvision", "eventType": etype_raw,
                     "native_code": etype_raw,
                     "native_ai": smart_native,
                     "targets": targets,
                     "eventDescription": _text(root, "eventDescription"),
                     "activePostCount": _text(root, "activePostCount")},
        )

    def close(self) -> None:
        self.s.close()
