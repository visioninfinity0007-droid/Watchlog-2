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

from .base import (Channel, DeviceInfo, DriverError, Event, NvrAuthFailed,
                   NvrDriver, NvrUnreachable, explain)

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
SNAPSHOT_TIMEOUT = 10
JPEG_MAGIC = bytes([0xFF, 0xD8])   # a JPEG always starts FF D8


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
            raise NvrUnreachable(f"{url}: {explain(e)}") from e
        # Digest is the norm; some Dahua-derived units answer Basic. Retry ONCE
        # with Basic before deciding the credentials are actually wrong.
        if r.status_code == 401:
            self.s.auth = HTTPBasicAuth(self.username, self.password)
            try:
                r = self.s.get(url, timeout=self.timeout, **kw)
            except requests.RequestException as e:
                raise NvrUnreachable(f"{url}: {explain(e)}") from e
        # Reachable but the recorder rejected the login: a credentials fault, not "offline".
        if r.status_code in (401, 403):
            raise NvrAuthFailed(
                f"{url}: HTTP {r.status_code} — recorder rejected the username or password")
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

    def capabilities(self) -> dict:
        """
        Read the recorder's analytics config and report it per channel.

        Queries the Dahua config endpoints for the analytics families that
        matter, marks each supported/active, and flags the ones that need a
        drawn line or zone. Every query is wrapped: a Dahua model that does
        not expose one of these simply leaves that analytic off the list
        rather than failing the whole probe.
        """
        try:
            chans = self.list_channels()
        except DriverError:
            return {"channels": []}   # device unreachable -> nothing to report
        # Pull each config block once; tolerate any that 404 / error.
        def _cfg(name):
            try:
                return _parse_kv(self._get(
                    f"/cgi-bin/configManager.cgi?action=getConfig&name={name}"))
            except DriverError:
                return {}

        motion = _cfg("MotionDetect")
        smd    = _cfg("SmartMotionDetect")
        cover  = _cfg("CoverDetect")
        ivs    = _cfg("VideoAnalyseRule")

        def _enabled(kv, prefix):   # table.<prefix>[i].Enable=true
            return str(kv.get(f"{prefix}.Enable", "")).lower() == "true"

        # IVS rules are table.VideoAnalyseRule[ch][rule].Class / .Enable
        ivs_by_ch: dict[int, set] = {}
        for key, val in ivs.items():
            m = re.match(r"table\.VideoAnalyseRule\[(\d+)\]\[\d+\]\.Class", key)
            if m:
                ch = int(m.group(1))
                # find the matching Enable
                en_key = key.rsplit(".", 1)[0] + ".Enable"
                if str(ivs.get(en_key, "")).lower() == "true":
                    ivs_by_ch.setdefault(ch, set()).add(val)

        out = []
        for c in chans:
            i = int(c.channel) - 1               # config is 0-based
            classes = ivs_by_ch.get(i, set())
            analytics = [
                {"key": "motion", "label": "Motion detection",
                 "supported": bool(motion),
                 "active": _enabled(motion, f"table.MotionDetect[{i}]"),
                 "geometry": False},
                {"key": "human_vehicle", "label": "Human/Vehicle (SMD)",
                 "supported": bool(smd),
                 "active": _enabled(smd, f"table.SmartMotionDetect[{i}]"),
                 "geometry": False},
                {"key": "tamper", "label": "Camera tamper",
                 "supported": bool(cover),
                 "active": _enabled(cover, f"table.CoverDetect[{i}]"),
                 "geometry": False},
                {"key": "line_crossing", "label": "Line crossing",
                 "supported": bool(ivs),
                 "active": "CrossLineDetection" in classes,
                 "geometry": True},
                {"key": "intrusion", "label": "Intrusion zone",
                 "supported": bool(ivs),
                 "active": "CrossRegionDetection" in classes,
                 "geometry": True},
            ]
            out.append({"channel": c.channel, "name": c.name,
                        "analytics": analytics})
        return {"channels": out}

    def storage_status(self) -> dict:
        """Dahua HDD/storage health via storageDevice.cgi. Conservative and honest:
          * a disk failure/missing/unformatted signal -> 'fault' (no usable recording storage);
          * a low-space / no-free-space signal -> 'degraded' (usually still recording by overwrite —
            NOT a blanket fault);
          * a clearly-normal signal -> 'ok';
          * anything else / unreadable -> None (UNKNOWN).
        NOT hardware-verified, so it fails safe to UNKNOWN rather than a false healthy."""
        try:
            kv = _parse_kv(self._get("/cgi-bin/storageDevice.cgi?action=getDeviceAllInfo"))
        except DriverError:
            return {"supported": False, "state": None}
        if not kv:
            return {"supported": True, "state": None}
        vals = " ".join(str(v).lower() for v in kv.values())
        if any(k in vals for k in ("error", "failed", "failure", "abnormal", "notexist",
                                   "no disk", "unformat")):
            return {"supported": True, "state": "fault"}       # no usable storage
        if any(k in vals for k in ("lowspace", "low space", "nospace", "full")):
            return {"supported": True, "state": "degraded"}    # low space — still usable, not a fault
        if any(k in vals for k in ("normal", "running", "sleeping", "good", "ok")):
            return {"supported": True, "state": "ok"}
        return {"supported": True, "state": None}

    def recording_status(self, channels=None) -> dict:
        """Per-channel recording state from Dahua RecordMode. Vendor-truth conservative:
          * Mode 2 (off) -> 'not_recording' (recorder explicitly says the channel is not recording);
          * Mode 0 (auto/schedule) or 1 (manual/always) -> None (UNKNOWN): this is CONFIGURATION, not
            proof that frames are being written to disk right now. We never claim 'recording' from a
            mode/schedule alone. Proof-of-active-recording (e.g. a recent-file check) is a future,
            hardware-validated refinement.
        Read-only; recording is never inferred from a snapshot."""
        try:
            kv = _parse_kv(self._get(
                "/cgi-bin/configManager.cgi?action=getConfig&name=RecordMode"))
        except DriverError:
            return {"supported": False, "channels": {}}
        out: dict[str, str | None] = {}
        for key, val in kv.items():
            m = re.match(r"table\.RecordMode\[(\d+)\]\.Mode", key)
            if m:
                ch = str(int(m.group(1)) + 1)          # config is 0-based; channels are 1-based
                # only an explicit OFF is a truthful state; a schedule/mode is not proof -> UNKNOWN
                out[ch] = "not_recording" if str(val).strip() == "2" else None
        if not out:
            return {"supported": False, "channels": {}}
        return {"supported": True, "channels": out}

    def get_snapshot(self, channel: str) -> bytes | None:
        """
        Dahua still image. The CGI is 1-based here, unlike the event
        stream's `index`, which is 0-based. Same device, two conventions.
        """
        try:
            ch = int(str(channel))
        except (TypeError, ValueError):
            return None
        url = f"{self.base_url}/cgi-bin/snapshot.cgi?channel={ch}"
        try:
            r = self.s.get(url, timeout=SNAPSHOT_TIMEOUT)
        except requests.RequestException:
            return None
        if r.status_code == 200 and r.content[:2] == JPEG_MAGIC:
            return r.content
        return None

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
