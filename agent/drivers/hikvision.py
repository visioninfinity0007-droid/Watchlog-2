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

from .base import Channel, DeviceInfo, DriverError, Event, NvrDriver

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
            raise DriverError(f"{url}: {e}") from e
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
