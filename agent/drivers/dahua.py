"""
Dahua CGI driver.

Covers Dahua NVRs/DVRs and — importantly for Pakistan — the large family
of units that are Dahua hardware under another badge. CP Plus, which has
a strong dealer network here, is the significant one: its DVR/NVR line is
Dahua-derived and answers the same CGI endpoints. Imou is Dahua's own
consumer brand.

Dahua is HTTP CGI with Digest auth. The calls used:

    /cgi-bin/magicBox.cgi?action=getSystemInfo       identity
    /cgi-bin/magicBox.cgi?action=getDeviceType
    /cgi-bin/configManager.cgi?action=getConfig&name=ChannelTitle
    /cgi-bin/eventManager.cgi?action=attach&codes=[All]&heartbeat=5

`attach` is a long-lived multipart response — the device writes an event
block whenever something fires. Outbound only, like everything else here.

NOT YET VERIFIED AGAINST HARDWARE. Endpoint shapes are per Dahua's HTTP
API spec and the widely-used community integrations, but no real device
has been available. Run `watchlog_agent.py --probe` against a real unit
before trusting it.
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from typing import Iterator

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

from .base import Channel, DeviceInfo, DriverError, Event, NvrDriver

# Dahua event codes -> our vocabulary.
EVENT_CODE_MAP = {
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

# Events we subscribe to. "All" also works but floods the link with
# heartbeats and config chatter on a busy NVR.
SUBSCRIBE_CODES = ",".join(EVENT_CODE_MAP.keys())

BURST_WINDOW_SECONDS = 30

_KV = re.compile(r"^([^=]+)=(.*)$")


def _parse_kv(text: str) -> dict[str, str]:
    """Dahua replies are flat `key=value` lines."""
    out = {}
    for line in text.splitlines():
        m = _KV.match(line.strip())
        if m:
            out[m.group(1).strip()] = m.group(2).strip()
    return out


class DahuaDriver(NvrDriver):
    name = "dahua-cgi"
    verified_against_hardware = False

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.s = requests.Session()
        self.s.auth = HTTPDigestAuth(self.username, self.password)
        self._last_emitted: dict[tuple[str, str], datetime] = {}

    # -- helpers --------------------------------------------------------

    def _get(self, path: str, **kw) -> str:
        url = self.base_url + path
        try:
            r = self.s.get(url, timeout=kw.pop("timeout", self.timeout), **kw)
        except requests.RequestException as e:
            raise DriverError(f"{url}: {e}") from e
        if r.status_code == 401:
            self.s.auth = HTTPBasicAuth(self.username, self.password)
            r = self.s.get(url, timeout=self.timeout, **kw)
        if r.status_code >= 400:
            raise DriverError(f"{url}: HTTP {r.status_code} {r.text[:200]}")
        return r.text

    # -- interface ------------------------------------------------------

    def probe(self) -> DeviceInfo:
        info = _parse_kv(self._get("/cgi-bin/magicBox.cgi?action=getSystemInfo"))
        if not info:
            raise DriverError("getSystemInfo returned nothing parseable")

        model = info.get("deviceType") or info.get("model")
        try:
            dtype = _parse_kv(self._get("/cgi-bin/magicBox.cgi?action=getDeviceType"))
            model = dtype.get("type") or model
        except DriverError:
            pass

        count = info.get("videoInChannel") or info.get("VideoInChannel")
        return DeviceInfo(
            vendor="Dahua",
            model=model,
            firmware=info.get("version") or info.get("softwareVersion"),
            serial=info.get("serialNumber") or info.get("sn"),
            channel_count=int(count) if count and count.isdigit() else None,
            driver=self.name,
            raw=info,
        )

    def list_channels(self) -> list[Channel]:
        out: list[Channel] = []
        try:
            kv = _parse_kv(self._get(
                "/cgi-bin/configManager.cgi?action=getConfig&name=ChannelTitle"))
            # table.ChannelTitle[0].Name=Main Gate
            for key, val in kv.items():
                m = re.match(r"table\.ChannelTitle\[(\d+)\]\.Name", key)
                if m:
                    idx = int(m.group(1))
                    out.append(Channel(channel=str(idx + 1), name=val or None))
        except DriverError:
            pass

        if not out:
            n = self.probe().channel_count or 0
            out = [Channel(channel=str(i), name=f"Channel {i}")
                   for i in range(1, n + 1)]
        return sorted(out, key=lambda c: int(c.channel))

    def stream_events(self, stop: threading.Event) -> Iterator[Event]:
        """
        Consume /cgi-bin/eventManager.cgi?action=attach.

        Blocks are separated by a boundary and look like:

            Code=VideoMotion;action=Start;index=0;data={...}

        heartbeat=5 makes the device send a keep-alive every 5s, which is
        also how we detect a silently dead link.
        """
        path = (f"/cgi-bin/eventManager.cgi?action=attach"
                f"&codes=[{SUBSCRIBE_CODES}]&heartbeat=5")
        url = self.base_url + path
        try:
            r = self.s.get(url, stream=True, timeout=(self.timeout, 90))
        except requests.RequestException as e:
            raise DriverError(f"eventManager attach: {e}") from e
        if r.status_code >= 400:
            raise DriverError(f"eventManager attach: HTTP {r.status_code}")

        try:
            for raw_line in r.iter_lines(chunk_size=512):
                if stop.is_set():
                    break
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8", "replace").strip()
                if not line.startswith("Code="):
                    continue          # boundary / Content-Length / heartbeat
                ev = self._parse_line(line)
                if ev:
                    yield ev
        finally:
            r.close()

    # -- parsing --------------------------------------------------------

    def _parse_line(self, line: str) -> Event | None:
        fields: dict[str, str] = {}
        # data={...} may contain ';' so split only the leading key=value pairs
        head, sep, data = line.partition(";data=")
        for part in head.split(";"):
            k, _, v = part.partition("=")
            if k:
                fields[k.strip()] = v.strip()

        code = fields.get("Code", "")
        action = fields.get("action", "").lower()
        if action not in ("start", "pulse", ""):
            return None               # Stop / State — not an occurrence

        etype = EVENT_CODE_MAP.get(code)
        if etype is None:
            if code in ("Heartbeat", "KeepAlive", "TimeChange", "NTPAdjustTime"):
                return None
            etype = code.lower() or "unknown"

        # index is 0-based on the wire; channels are 1-based everywhere else.
        try:
            channel = str(int(fields.get("index", "0")) + 1)
        except ValueError:
            channel = "1"

        ts = datetime.now(timezone.utc)   # attach is live; no device clock field
        key = (channel, etype)
        last = self._last_emitted.get(key)
        if last and (ts - last).total_seconds() < BURST_WINDOW_SECONDS:
            return None
        self._last_emitted[key] = ts

        return Event(
            channel=channel,
            event_type=etype,
            device_ts=ts,
            device_event_id=None,
            payload={"vendor": "dahua", "code": code, "action": action,
                     "data": (data[:500] if sep else None)},
        )

    def close(self) -> None:
        self.s.close()
