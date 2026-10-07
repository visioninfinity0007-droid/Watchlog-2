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

import queue
import re
import threading
import time
from datetime import datetime, timezone
from typing import Iterator

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

from . import alarm_parsing
from .base import (Channel, DeviceInfo, DriverError, Event, NvrAuthFailed,
                   NvrDriver, NvrUnreachable, explain)

# One code map, channel rule and burst rule shared with the push bridge, so an alarm
# means the same thing on both paths (MNVR-026).
EVENT_CODE_MAP = alarm_parsing.DAHUA_EVENT_CODE_MAP

# Codes whose index names a disk or an alarm input, not a video channel. They are
# recorder-scoped: channel None plus a flag, never index+1 guessed onto a camera.
RECORDER_SCOPED_CODES = alarm_parsing.DAHUA_RECORDER_SCOPED_CODES

# Events we subscribe to (5.1.2): every code. A fixed list silently ignored anything the
# recorder raised outside it (network abort, login failure, analytics we do not map). Codes
# outside EVENT_CODE_MAP are stored raw (lowercased) like Hikvision's; the chatter that is
# not an occurrence (keep-alives, clock changes, per-file and metadata notices) is
# dropped by alarm_parsing.DAHUA_NON_EVENTS, never stored.
SUBSCRIBE_CODES = "All"

BURST_WINDOW_SECONDS = alarm_parsing.BURST_WINDOW_SECONDS
SNAPSHOT_TIMEOUT = 10
JPEG_MAGIC = bytes([0xFF, 0xD8])   # a JPEG always starts FF D8


_KV = re.compile(r"^([^=]+)=(.*)$")


def moves_to_basic(session, r) -> bool:
    """True when a 401 should move a Dahua session to Basic: the session is not on Basic yet
    and the challenge offers Basic and not Digest. Every request on that session (_get, the
    archive reader, the loadfile export) uses this one rule, so a Digest refusal never resends
    the password in the clear."""
    if r.status_code != 401 or isinstance(session.auth, HTTPBasicAuth):
        return False
    challenge = ((getattr(r, "headers", None) or {}).get("WWW-Authenticate") or "").lower()
    return "basic" in challenge and "digest" not in challenge


def _parse_kv(text: str) -> dict[str, str]:
    """Dahua replies are flat `key=value` lines."""
    out = {}
    for line in text.splitlines():
        m = _KV.match(line.strip())
        if m:
            out[m.group(1).strip()] = m.group(2).strip()
    return out


# Keys (the part after the last '.') that name seconds since the last boot. A "total"
# (cumulative running time across boots) never resets and is deliberately not one.
_UPTIME_KEYS = ("up", "uptime", "last", "result")


def parse_uptime_kv(kv: dict) -> "float | None":
    """Seconds since boot from a Dahua key=value reply, or None when none is stated."""
    for key, value in kv.items():
        name = key.rsplit(".", 1)[-1].strip().lower()
        if name in _UPTIME_KEYS:
            text = str(value).strip()
            if text.isdigit():
                return float(text)
    return None


class DahuaDriver(NvrDriver):
    name = "dahua-cgi"
    verified_against_hardware = False
    # Liveness comes from the attach stream itself: last_activity_monotonic and
    # event_stream are set only after attach answers 2xx and on every received line,
    # heartbeats included. A getSystemInfo probe says nothing about the event stream.
    reports_stream_activity = True

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.s = self.lan_session()   # no system proxy; self-signed HTTPS (Build 69)
        self.s.auth = HTTPDigestAuth(self.username, self.password)
        self._burst = alarm_parsing.BurstFilter(BURST_WINDOW_SECONDS)
        # (monotonic, wall) clock of the block being parsed, stamped by stream_events
        # when it arrived; None outside the stream (parse time is used then).
        self._received: tuple[float, datetime] | None = None
        # The collector may replace event_stream with its per-recorder state dict.
        self.last_activity_monotonic = 0.0
        self.event_stream: dict = {"connected": False, "connected_at": None,
                                   "last_frame_at": None, "last_error": None}
        # The activity stamp before the current stream's 2xx, while that stream has not
        # delivered a single chunk yet; None once it has (or outside a stream).
        self._activity_before_up: float | None = None

    # -- event-stream liveness (MNVR-008) -------------------------------

    def _stream_up(self) -> None:
        # The 2xx counts as activity only while this stream stays open: _stream_down takes
        # it back if the stream ends before a single chunk arrives, so a recorder whose
        # attach answers 200 and closes at once is never live, however often it is reopened.
        self._activity_before_up = self.last_activity_monotonic
        self.last_activity_monotonic = time.monotonic()
        self.event_stream.update(connected=True, last_error=None,
                                 connected_at=datetime.now(timezone.utc).isoformat())

    def _stream_frame(self) -> None:
        self._activity_before_up = None
        self.last_activity_monotonic = time.monotonic()
        self.event_stream["last_frame_at"] = datetime.now(timezone.utc).isoformat()

    def _stream_down(self, error: str | None) -> None:
        if self._activity_before_up is not None:    # ended without delivering anything
            self.last_activity_monotonic = self._activity_before_up
            self._activity_before_up = None
        self.event_stream["connected"] = False
        if error:
            self.event_stream["last_error"] = error

    # -- helpers --------------------------------------------------------

    def _receive_clock(self) -> tuple[float, datetime]:
        return self._received or (time.monotonic(), datetime.now(timezone.utc))

    def _get(self, path: str, **kw) -> str:
        url = self.base_url + path
        timeout = kw.pop("timeout", self.timeout)
        try:
            r = self.s.get(url, timeout=timeout, **kw)
        except requests.RequestException as e:
            raise NvrUnreachable(f"{url}: {explain(e)}") from e
        # Digest is the norm; some Dahua-derived units only do Basic. The session moves to
        # Basic only when the recorder's challenge offers Basic and not Digest: a wrong
        # password or a stray 401 from a Digest unit never sends the password in the clear,
        # costs no second login attempt and leaves the session on Digest. The switch is kept
        # for this recorder because snapshot.cgi and the attach stream use the same session
        # (a Basic-only unit would otherwise fail every still and every attach).
        if moves_to_basic(self.s, r):
            r.close()
            self.s.auth = HTTPBasicAuth(self.username, self.password)
            try:
                r = self.s.get(url, timeout=timeout, **kw)
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

    # -- focused reads + SAFE writes (Site Control managed tier; field-proven on DH-XVR1B08-I) --

    def get_channel_title(self, channel) -> "str | None":
        ch = int(str(channel)) - 1
        try:
            kv = _parse_kv(self._get(
                "/cgi-bin/configManager.cgi?action=getConfig&name=ChannelTitle"))
        except DriverError:
            return None
        return kv.get(f"table.ChannelTitle[{ch}].Name")

    def set_channel_title(self, channel, name) -> None:
        import urllib.parse
        ch = int(str(channel)) - 1
        self._get("/cgi-bin/configManager.cgi?action=setConfig&"
                  f"ChannelTitle[{ch}].Name={urllib.parse.quote(str(name), safe='')}")

    def get_smd(self, channel) -> dict:
        ch = int(str(channel)) - 1
        kv = _parse_kv(self._get(
            "/cgi-bin/configManager.cgi?action=getConfig&name=SmartMotionDetect"))
        def b(key):
            v = kv.get(f"table.SmartMotionDetect[{ch}].{key}")
            return None if v is None else (str(v).lower() == "true")
        return {"enable": b("Enable"),
                "human": b("ObjectTypes.Human"),
                "vehicle": b("ObjectTypes.Vehicle"),
                "sensitivity": kv.get(f"table.SmartMotionDetect[{ch}].Sensitivity")}

    def set_smd(self, channel, human=None, vehicle=None, sensitivity=None, enable=None) -> None:
        ch = int(str(channel)) - 1
        def tf(v):
            return "true" if v else "false"
        parts = []
        if enable is not None:
            parts.append(f"SmartMotionDetect[{ch}].Enable={tf(enable)}")
        if human is not None:
            parts.append(f"SmartMotionDetect[{ch}].ObjectTypes.Human={tf(human)}")
        if vehicle is not None:
            parts.append(f"SmartMotionDetect[{ch}].ObjectTypes.Vehicle={tf(vehicle)}")
        if sensitivity is not None:
            parts.append(f"SmartMotionDetect[{ch}].Sensitivity={sensitivity}")
        for p in parts:
            self._get(f"/cgi-bin/configManager.cgi?action=setConfig&{p}")

    def set_time_config(self, dst_enabled=None, ntp_enabled=None,
                        ntp_server=None, timezone_index=None) -> None:
        import urllib.parse
        def tf(v):
            return "true" if v else "false"
        parts = []
        if dst_enabled is not None:
            parts.append(f"Locales.DSTEnable={tf(dst_enabled)}")
        if ntp_enabled is not None:
            parts.append(f"NTP.Enable={tf(ntp_enabled)}")
        if ntp_server:
            parts.append(f"NTP.Address={urllib.parse.quote(str(ntp_server), safe='')}")
        if timezone_index is not None:
            parts.append(f"NTP.TimeZone={int(timezone_index)}")
        for p in parts:
            self._get(f"/cgi-bin/configManager.cgi?action=setConfig&{p}")

    def configure_push(self, url: str) -> dict:
        """Fail closed: never rewrite a Dahua recorder's AlarmServer (field W2 925885a4).

        Dahua's documented AlarmServer is a vendor alarm-centre protocol, not a generic
        HTTP/HTTPS webhook. Its protocol values are Dahua/Bosch/cloud families; writing an
        HTTPS WatchLog URL into AlarmServer.Address/Port can overwrite a customer's existing
        alarm-centre configuration without giving WatchLog a working callback. So the generic
        Dahua driver only READS AlarmServer and reports PC-free push as unsupported. Dahua
        coverage stays: native live eventManager monitoring while the Agent runs, durable
        local spooling through outages, and archive recovery when connectivity returns.

        A firmware-specific HTTP push adapter may override this only after real hardware
        proves its endpoint and payload. A rejected login still surfaces as an auth fault.
        Returns {"applied": False, "verified": False, "detail": str}; never writes.
        """
        try:
            kv = _parse_kv(self._get(
                "/cgi-bin/configManager.cgi?action=getConfig&name=AlarmServer"))
            proto = str(kv.get("table.AlarmServer.Protocol")
                        or kv.get("AlarmServer.Protocol") or "").strip()
        except NvrAuthFailed:
            raise
        except Exception:  # noqa: BLE001 - read-only capability hint only
            proto = ""
        detail = ("generic Dahua AlarmServer is a proprietary alarm-centre protocol, "
                  "not a WatchLog HTTP webhook; left unchanged")
        if proto:
            detail += f" (recorder protocol={proto})"
        return {"applied": False, "verified": False, "detail": detail}

    def get_clock(self) -> dict:
        """Recorder clock/timezone/DST/NTP, read-only (global.cgi + Locales + NTP config)."""
        def _cfg(name):
            try:
                return _parse_kv(self._get(
                    f"/cgi-bin/configManager.cgi?action=getConfig&name={name}"))
            except DriverError:
                return {}
        out: dict = {"supported": True, "current_time": None, "timezone": None,
                     "dst_enabled": None, "ntp_enabled": None, "ntp_server": None}
        try:
            ct = _parse_kv(self._get("/cgi-bin/global.cgi?action=getCurrentTime"))
            out["current_time"] = ct.get("result") or ct.get("time")
        except DriverError:
            pass
        loc = _cfg("Locales")
        if loc.get("table.Locales.DSTEnable") is not None:
            out["dst_enabled"] = str(loc.get("table.Locales.DSTEnable")).lower() == "true"
        ntp = _cfg("NTP")
        if ntp.get("table.NTP.Enable") is not None:
            out["ntp_enabled"] = str(ntp.get("table.NTP.Enable")).lower() == "true"
        out["ntp_server"] = ntp.get("table.NTP.Address")
        out["timezone"] = ntp.get("table.NTP.TimeZoneDesc")
        return out

    def current_faults(self) -> dict:
        """Current VideoLoss / VideoBlind channels from the recorder's live event INDEX.

        eventManager.cgi?action=getEventIndexes&code=VideoLoss returns the channels that are in
        that state *right now* (`channels[0]=4` -> 0-based index 4 -> channel "5"), unlike the
        event STREAM which only emits on a transition. This is what lets the first health cycle
        after startup/reconnect/resume see a camera that was already lost. A snapshot cannot be
        trusted for this: a video-loss channel still returns the recorder's black placeholder
        JPEG, which has a valid header and reads as 'live'.

        Read-only, digest-authenticated, outbound-only. Fail-safe: if a query errors we report
        supported=False rather than an empty (falsely-clean) fault set.
        """
        def _indexes(code):
            try:
                kv = _parse_kv(self._get(
                    f"/cgi-bin/eventManager.cgi?action=getEventIndexes&code={code}"))
            except DriverError:
                return None                       # could not query -> unknown, not "none"
            chans = []
            for key, val in kv.items():
                # channels[0]=4  (0-based index) -> channel "5"
                if key.startswith("channels[") and str(val).strip().lstrip("-").isdigit():
                    n = int(str(val).strip())
                    if n >= 0:
                        chans.append(str(n + 1))
            return sorted(set(chans), key=int)

        vl = _indexes("VideoLoss")
        vb = _indexes("VideoBlind")
        if vl is None and vb is None:
            return {"supported": False, "video_loss": [], "video_blind": []}
        return {"supported": True, "video_loss": vl or [], "video_blind": vb or []}

    # Paths tried, in order, for the recorder's uptime. IMPLEMENTED_UNVERIFIED: not read on
    # a field unit yet. A unit that answers none of them reports None (no restart claim).
    UPTIME_PATHS = ("/cgi-bin/magicBox.cgi?action=getUpTime",
                    "/cgi-bin/global.cgi?action=getUpTime")

    def uptime_seconds(self) -> "float | None":
        """Seconds since the recorder last booted, or None when it cannot say.

        Read-only. Only a plain number of seconds under a since-boot key is accepted
        (up / upTime / uptime / last / result); a cumulative total is never read as uptime.
        A path the recorder rejects is not asked again on this driver."""
        rejected = getattr(self, "_uptime_rejected", set())
        self._uptime_rejected = rejected
        for path in self.UPTIME_PATHS:
            if path in rejected:
                continue
            try:
                kv = _parse_kv(self._get(path))
            except NvrAuthFailed:
                return None
            except NvrUnreachable:
                return None
            except DriverError as error:
                status = re.search(r"HTTP (\d{3})", str(error))
                code = int(status.group(1)) if status else 0
                if 400 <= code < 500 or code == 501:
                    rejected.add(path)      # this firmware has no such call
                continue                    # anything else is transient: ask next time
            value = parse_uptime_kv(kv)
            if value is not None:
                return value
            rejected.add(path)
        return None

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

        The body is read on its own thread so every block keeps the time it
        ARRIVED. The collector fetches a still and runs AI for one event before
        asking for the next; a block read only after that work would carry a
        late device_ts (MNVR-024).
        """
        path = (f"/cgi-bin/eventManager.cgi?action=attach"
                f"&codes=[{SUBSCRIBE_CODES}]&heartbeat=5")
        url = self.base_url + path
        # A fresh open (start, or after a drop): the recorder restart check goes first.
        for ev in self._stream_open_events():
            yield ev
        try:
            r = self.s.get(url, stream=True, timeout=(self.timeout, 90))
        except requests.RequestException as e:
            self._stream_down(explain(e))
            raise DriverError(f"eventManager attach: {e}") from e
        if r.status_code >= 400:
            r.close()
            self._stream_down(f"HTTP {r.status_code}")
            raise DriverError(f"eventManager attach: HTTP {r.status_code}")
        self._stream_up()

        blocks: queue.Queue = queue.Queue()
        closed = threading.Event()

        def _read() -> None:
            try:
                for raw_line in r.iter_lines(chunk_size=512):
                    if closed.is_set():
                        return
                    self._stream_frame()            # heartbeats count: the stream is alive
                    if not raw_line:
                        continue
                    line = raw_line.decode("utf-8", "replace").strip()
                    if line.startswith("Code="):  # not boundary / Content-Length / heartbeat
                        blocks.put((time.monotonic(), datetime.now(timezone.utc), line))
                blocks.put(None)                    # the recorder ended the stream
            except Exception as e:                  # noqa: BLE001 - raised by the consumer
                blocks.put(e)

        threading.Thread(target=_read, name="dahua-attach", daemon=True).start()
        ended = None
        try:
            while not stop.is_set():
                try:
                    item = blocks.get(timeout=1.0)
                except queue.Empty:
                    continue
                if item is None:
                    ended = "event stream ended by the recorder"
                    break
                if isinstance(item, Exception):
                    ended = (explain(item) if isinstance(item, requests.RequestException)
                             else type(item).__name__)
                    raise item
                received_mono, received_at, line = item
                self._received = (received_mono, received_at)
                ev = self._parse_line(line)
                if ev:
                    yield ev
        finally:
            closed.set()
            self._received = None
            r.close()
            self._stream_down(ended)

    # -- parsing --------------------------------------------------------

    def _parse_line(self, line: str) -> Event | None:
        alarm = alarm_parsing.parse_dahua_block(line)
        if alarm is None:
            return None

        # attach is live and carries no device clock field: the event time is when the
        # block arrived. Repeats collapse on the monotonic receive clock, so a backward
        # PC clock step cannot drop every later event of this type (MNVR-023).
        received_mono, received_at = self._receive_clock()
        if not self._burst.admit(alarm.burst_key, received_mono,
                                 pair=alarm.pair_key, phase=alarm.phase):
            return None
        ts, clock = alarm_parsing.resolve_event_time(
            alarm.raw_time, received_at, receive_source="agent_receive")

        return Event(
            channel=alarm.channel,
            event_type=alarm.event_type,
            device_ts=ts,
            device_event_id=None,
            payload={**alarm.payload, **clock},
        )

    def close(self) -> None:
        self.s.close()
