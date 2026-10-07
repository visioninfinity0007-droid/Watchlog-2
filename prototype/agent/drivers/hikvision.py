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
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterator
from urllib.parse import urlparse

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

from . import alarm_parsing
from .base import (Channel, DeviceInfo, DriverError, Event, NvrAuthFailed,
                   NvrDriver, NvrUnreachable, explain)

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


def _parse_ts(raw: str | None) -> datetime | None:
    """ISAPI emits ISO 8601, sometimes with a local offset, sometimes naive.

    Returned as sent: aware with an offset, naive without one, None when absent or
    unparseable. A naive value is recorder-local time and is never assumed to be UTC.
    """
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


# POSIX TZ as ISAPI reports it, e.g. "CST-5:00:00" (the sign is inverted: UTC+5).
# Anything after the offset is a DST rule, which this deliberately does not resolve.
_POSIX_TZ = re.compile(r"[A-Za-z]{3,}([+-]?)(\d{1,2})(?::(\d{2}))?(?::(\d{2}))?")


def _stated_utc_offset(local_time: str | None, zone: str | None,
                       dst_enabled: bool = False) -> timedelta | None:
    """The recorder's own UTC offset from /ISAPI/System/time, or None when it does not
    state one unambiguously (no offset on localTime and no DST-free POSIX zone). With DST
    enabled a bare POSIX zone is only the standard offset, so it is not used."""
    stamped = _parse_ts(local_time)
    if stamped is not None and stamped.tzinfo is not None:
        return stamped.utcoffset()
    if dst_enabled:
        return None
    m = _POSIX_TZ.fullmatch((zone or "").strip())
    if not m:
        return None
    offset = timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0),
                       seconds=int(m.group(4) or 0))
    if offset > timedelta(hours=14):
        return None
    return offset if m.group(1) == "-" else -offset


# One event-type map, channel rule, keep-alive filter and burst rule shared with the
# push bridge, so an alarm means the same thing on both paths (MNVR-026).
EVENT_TYPE_MAP = alarm_parsing.HIK_EVENT_TYPE_MAP

# Alert types that describe the recorder itself (its disks, logins, network link and
# alarm inputs), not a camera. A channel field on these does not name a video input
# (MNVR-028). An alert that carries inputIOPortID is an alarm input whatever its type.
RECORDER_SCOPED_TYPES = alarm_parsing.HIK_RECORDER_SCOPED_TYPES

# Hikvision repeats an active alarm every second for as long as it lasts.
# Collapsing a burst into one event is the difference between 5 rows and
# 500 for a single person walking past a camera. The window is timed on the
# agent's monotonic receive clock, never on the recorder's dateTime.
BURST_WINDOW_SECONDS = alarm_parsing.BURST_WINDOW_SECONDS

# A recorder stamp further than this from the time the alert arrived is kept but
# flagged on the event (clock_skew_seconds) rather than trusted silently.
CLOCK_SKEW_FLAG_SECONDS = alarm_parsing.CLOCK_SKEW_FLAG_SECONDS
# How long a stated UTC offset is trusted before it is read again (a DST change moves it),
# and how long a recorder that did not state one waits before being asked again.
CLOCK_OFFSET_RETRY_SECONDS = 600

# A camera that will not produce a still must not stall the event loop.
SNAPSHOT_TIMEOUT = 10
JPEG_MAGIC = bytes([0xFF, 0xD8])   # a JPEG always starts FF D8

# Field DS-7608NI-Q1 (Chai Wala, Build 69/75 and shipped 5.0.26): setup/auth succeeded, but a
# permanently-open alertStream plus independent health/still/recovery logins made the
# recorder's small web stack refuse later sessions and time out while still reachable. The
# field fix, kept here in its per-recorder form:
#   * ONE authenticated HTTP operation at a time per recorder: every request of every driver
#     instance for that recorder (live stream, stills, health, archive) takes the recorder's
#     lock below; the live stream holds it for one bounded slice;
#   * the alert stream is cut into HIKVISION_STREAM_SLICE_SECONDS slices, and between slices
#     the stream may take ONE rotating camera still on the same session (between_slices),
#     so stills never open a competing session while native alarms still pass immediately.
HIKVISION_STREAM_SLICE_SECONDS = 30

# The lock is PER RECORDER (scheme, host, port), not module-global as in the single-recorder
# field build: two transports to the same recorder take turns, but recorder A's stream or slow
# export never holds recorder B (MNVR-025). Re-entrant, so a request made while the same
# thread holds the slice (an event still) does not deadlock.
# Between slices the stream hands its recorder to anyone waiting (health, archive, incident
# clips) for up to this long before it opens the next slice, so a waiter is never starved by
# the stream re-taking the lock first (Python locks are not fair).
SLICE_HANDOFF_SECONDS = 2.0


class _RecorderLock:
    """Re-entrant per-recorder lock that knows whether another thread is waiting for it."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._guard = threading.Lock()
        self._waiting = 0

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        with self._guard:
            self._waiting += 1
        try:
            return self._lock.acquire(blocking, timeout)
        finally:
            with self._guard:
                self._waiting -= 1

    def release(self) -> None:
        self._lock.release()

    def waiting(self) -> int:
        with self._guard:
            return self._waiting

    def __enter__(self) -> "_RecorderLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


_HTTP_LOCKS: dict[tuple, _RecorderLock] = {}
_HTTP_LOCKS_GUARD = threading.Lock()


def recorder_http_lock(base_url: str) -> _RecorderLock:
    """The HTTP lock of the recorder at ``base_url`` (every ISAPI request takes it)."""
    parsed = urlparse(str(base_url or ""))
    scheme = (parsed.scheme or "http").lower()
    try:
        port = parsed.port
    except ValueError:
        port = None
    key = (scheme, (parsed.hostname or "").lower(),
           port or (443 if scheme == "https" else 80))
    with _HTTP_LOCKS_GUARD:
        lock = _HTTP_LOCKS.get(key)
        if lock is None:
            lock = _HTTP_LOCKS[key] = _RecorderLock()
        return lock



class HikvisionDriver(NvrDriver):
    name = "hikvision-isapi"
    verified_against_hardware = False
    # Liveness comes from alertStream itself: last_activity_monotonic and event_stream are
    # set only after the stream answers 2xx and on every received chunk, keep-alive frames
    # included. A deviceInfo probe that answers says nothing about the event stream.
    reports_stream_activity = True

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.s = self.lan_session()   # no system proxy; self-signed HTTPS (Build 69)
        self.s.auth = HTTPDigestAuth(self.username, self.password)
        self._burst = alarm_parsing.BurstFilter(BURST_WINDOW_SECONDS)
        # (monotonic, wall) clock of the alert being parsed, stamped by stream_events
        # when its bytes arrived; None outside the stream (parse time is used then).
        self._received: tuple[float, datetime] | None = None
        # The recorder's stated UTC offset, for naive alert times (MNVR-024).
        self._utc_offset: timedelta | None = None
        self._utc_offset_asked: float | None = None
        # The collector may replace event_stream with its per-recorder state dict.
        self.last_activity_monotonic = 0.0
        self.event_stream: dict = {"connected": False, "connected_at": None,
                                   "last_frame_at": None, "last_error": None}
        # The activity stamp before the current stream's 2xx, while that stream has not
        # delivered a single chunk yet; None once it has (or outside a stream).
        self._activity_before_up: float | None = None
        # Set by the collector: called between alert-stream slices, outside the slice but on
        # this driver's session, and returns the events to yield (at most one camera still).
        # Any exception it raises is swallowed: a still never ends native monitoring.
        self.between_slices: Callable[[], list[Event]] | None = None

    # Stills come from the live stream (between_slices); a separate still worker must not
    # open a second session to this recorder (periodic_stills stands down for this driver).
    samples_in_stream = True

    def _lock(self) -> _RecorderLock:
        return recorder_http_lock(self.base_url)

    # -- event-stream liveness (MNVR-008) -------------------------------

    def _stream_up(self, resumed: bool = False) -> None:
        # The 2xx counts as activity only while this stream stays open: _stream_down takes
        # it back if the stream ends before a single chunk arrives, so a recorder whose
        # alertStream answers 200 and closes at once is never live, however often it is reopened.
        # ``resumed``: the next planned slice of a stream that was live; connected_at stays.
        self._activity_before_up = self.last_activity_monotonic
        self.last_activity_monotonic = time.monotonic()
        keep_since = resumed and self.event_stream.get("connected") and             self.event_stream.get("connected_at")
        self.event_stream.update(connected=True, last_error=None,
                                 connected_at=keep_since or datetime.now(timezone.utc).isoformat())

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

    def _recorder_utc_offset(self, now: float) -> timedelta | None:
        """UTC offset the recorder states for its local clock, read at most once per
        CLOCK_OFFSET_RETRY_SECONDS (``now`` is the monotonic receive clock).

        Only consulted for a naive alert dateTime. A stated offset is read again after the
        window, never trusted for the life of the driver: a reopened stream keeps the same
        driver for weeks and a DST change moves the offset. A recorder that does not state
        one, or whose clock cannot be read, leaves it unknown and is not asked on every
        alert."""
        if (self._utc_offset_asked is not None
                and now - self._utc_offset_asked < CLOCK_OFFSET_RETRY_SECONDS):
            return self._utc_offset
        self._utc_offset_asked = now
        self._utc_offset = None
        try:
            root = self._xml("/ISAPI/System/time")
        except DriverError:
            return None
        dst = (_text(root, "dstEnabled") or _text(root, "DSTEnabled") or "").strip().lower()
        self._utc_offset = _stated_utc_offset(
            _text(root, "localTime") or _text(root, "time") or _text(root, "currentTime"),
            _text(root, "timeZone") or _text(root, "timezone"),
            dst_enabled=dst in ("true", "1", "yes", "on"))
        return self._utc_offset

    def _send(self, method: str, url: str, **kw) -> requests.Response:
        """One ISAPI request on the Digest session.

        A few OEM firmwares only do Basic. Basic is used only when the recorder's challenge
        offers Basic and not Digest, and only for this one retry: the session keeps Digest,
        so a transient 401 can never leave every later request sending the password in the
        clear (or failing on a Digest-only unit). RequestException propagates to the caller.
        """
        with self._lock():
            r = self.s.request(method, url, **kw)
            if r.status_code == 401:
                challenge = (r.headers.get("WWW-Authenticate") or "").lower()
                if "basic" in challenge and "digest" not in challenge:
                    r.close()
                    r = self.s.request(method, url,
                                       auth=HTTPBasicAuth(self.username, self.password), **kw)
            return r

    def _get(self, path: str, **kw) -> requests.Response:
        url = self.base_url + path
        try:
            r = self._send("GET", url, timeout=kw.pop("timeout", self.timeout), **kw)
        except requests.RequestException as e:
            raise DriverError(f"{url}: {explain(e)}") from e
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
            r = self._send("PUT", url, data=body.encode(), timeout=self.timeout,
                           headers={"Content-Type": "application/xml"})
        except requests.RequestException as e:
            raise DriverError(f"{url}: {explain(e)}") from e
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

    def current_faults(self) -> dict:
        """Which IP channels are offline RIGHT NOW, from the NVR's own channel status.

        GET /ISAPI/ContentMgmt/InputProxy/channels/status lists each IP channel with
        ``<online>true|false</online>``. A channel the NVR states is offline has no video
        (an IP camera's video loss), so it is reported in ``video_loss`` with the same
        contract as the Dahua driver: the first health cycle after startup or a reconnect
        sees a camera that was already lost, which the event stream (transitions only)
        never would. Only an explicit ``false`` is a fault; a channel without the element
        is not judged. Hikvision has no present-tense tamper read, so ``video_blind`` is
        always empty. A recorder without this API (a DVR's analogue inputs) or a failed
        read is supported=False, never an empty, falsely clean fault set.
        IMPLEMENTED_UNVERIFIED on field hardware."""
        try:
            root = self._xml("/ISAPI/ContentMgmt/InputProxy/channels/status")
        except DriverError:
            return {"supported": False, "video_loss": [], "video_blind": []}
        rows = root.findall(".//InputProxyChannelStatus")
        if not rows:
            return {"supported": False, "video_loss": [], "video_blind": []}
        lost = set()
        for row in rows:
            cid = (_text(row, "id") or "").strip()
            online = (_text(row, "online") or "").strip().lower()
            if cid.isdigit() and online == "false":
                lost.add(cid)
        return {"supported": True, "video_loss": sorted(lost, key=int), "video_blind": []}

    def uptime_seconds(self) -> float | None:
        """Seconds since the recorder last booted (/ISAPI/System/status deviceUpTime), or
        None when it does not say. Read-only; used only as restart evidence."""
        try:
            root = self._xml("/ISAPI/System/status")
        except DriverError:
            return None
        for node in root.iter():
            if node.tag.lower() == "deviceuptime":
                text = (node.text or "").strip()
                return float(text) if text.isdigit() else None
        return None

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
        # None means the recorder affirmatively has no still at these paths (404 and the
        # like, or a body that is not a JPEG). A timeout, reset, 5xx or rejected login is
        # transient and raises, so callers retry instead of recording "unsupported".
        transient: DriverError | None = None
        for path in (f"/ISAPI/Streaming/channels/{ch}01/picture",
                     f"/ISAPI/Streaming/channels/{ch}/picture"):
            url = self.base_url + path
            try:
                r = self._send("GET", url, timeout=SNAPSHOT_TIMEOUT)
            except requests.RequestException as e:
                transient = NvrUnreachable(f"{url}: {explain(e)}")
                continue
            if r.status_code == 200 and r.content[:2] == JPEG_MAGIC:     # JPEG magic
                return r.content
            if r.status_code in (401, 403):
                transient = NvrAuthFailed(
                    f"{url}: HTTP {r.status_code} — recorder rejected the username or password")
            elif r.status_code in (408, 429) or r.status_code >= 500:
                transient = DriverError(f"{url}: HTTP {r.status_code}")
        if transient is not None:
            raise transient
        return None

    def stream_events(self, stop: threading.Event) -> Iterator[Event]:
        """
        Consume /ISAPI/Event/notification/alertStream in bounded slices (field Build 69).

        The device holds the connection open and writes a multipart body, one XML document
        per alarm, plus keep-alive videoloss/heartbeat frames (filtered out below). Each slice
        holds this recorder's HTTP lock for at most HIKVISION_STREAM_SLICE_SECONDS, then
        closes; between slices ``between_slices`` may take ONE camera still on this session,
        and the next slice opens at once. A planned slice end is not a dropped stream: the
        generator only ends when the recorder ends the stream or a request fails, so the
        collector's reopen/escalation logic sees exactly what it saw before.
        """
        url = self.base_url + "/ISAPI/Event/notification/alertStream"
        resumed = False
        while not stop.is_set():
            started = time.monotonic()
            planned_end = False
            with self._lock():
                if not resumed:
                    # A fresh open (start, or after a drop): the restart check reads the
                    # recorder's uptime inside this same lock hold, so it opens no gap.
                    for ev in self._stream_open_events():
                        yield ev
                try:
                    r = self._send("GET", url, stream=True,
                                   timeout=(self.timeout, HIKVISION_STREAM_SLICE_SECONDS))
                except requests.RequestException as e:
                    self._stream_down(explain(e))
                    raise DriverError(f"alertStream: {e}") from e
                if r.status_code >= 400:
                    r.close()
                    self._stream_down(f"HTTP {r.status_code}")
                    raise DriverError(f"alertStream: HTTP {r.status_code}")
                self._stream_up(resumed=resumed)

                buf = b""
                ended = "event stream ended by the recorder"
                try:
                    for chunk in r.iter_content(chunk_size=1024):
                        if stop.is_set():
                            ended = None
                            break
                        if chunk:
                            self._stream_frame()      # keep-alive frames count: the stream is alive
                            self._received = (time.monotonic(), datetime.now(timezone.utc))
                            buf += chunk
                            # Documents arrive back to back; split on the closing tag.
                            while b"</EventNotificationAlert>" in buf:
                                doc, _, buf = buf.partition(b"</EventNotificationAlert>")
                                begin = doc.find(b"<EventNotificationAlert")
                                if begin < 0:
                                    continue
                                raw = doc[begin:] + b"</EventNotificationAlert>"
                                ev = self._parse_alert(raw)
                                if ev:
                                    yield ev
                            if len(buf) > 1_000_000:  # runaway guard
                                buf = b""
                        if time.monotonic() - started >= HIKVISION_STREAM_SLICE_SECONDS:
                            planned_end = True
                            ended = None
                            break
                except GeneratorExit:                 # the collector stopped reading
                    ended = None
                    raise
                except requests.RequestException as e:
                    # Including a read timeout: the recorder sent nothing, not even a
                    # keep-alive, for the whole read window. That is a stale stream, recorded
                    # and handed to the collector's back-off, never a quiet re-open here.
                    ended = explain(e)
                    raise
                except Exception as e:
                    ended = type(e).__name__
                    raise
                finally:
                    self._received = None
                    r.close()
                    if not planned_end:
                        self._stream_down(ended)
            if not planned_end or stop.is_set():
                return
            resumed = bool(self.event_stream.get("connected"))
            # Hand the recorder to whoever waited during the slice before the next one.
            lock = self._lock()
            handoff = time.monotonic() + SLICE_HANDOFF_SECONDS
            while lock.waiting() and time.monotonic() < handoff and not stop.is_set():
                stop.wait(0.02)
            hook = self.between_slices
            if hook is not None:
                try:
                    extra = list(hook() or [])
                except Exception:                     # noqa: BLE001 — never ends monitoring
                    extra = []
                for ev in extra:
                    yield ev

    # -- parsing --------------------------------------------------------

    def _parse_alert(self, raw: bytes) -> Event | None:
        alarm = alarm_parsing.parse_hikvision_alert(raw)
        if alarm is None:
            return None

        # Collapse the once-per-second repeat of a continuing alarm on the agent's
        # monotonic receive clock. Comparing recorder dateTimes dropped every later event
        # of this (channel, type) after the recorder clock stepped backwards (MNVR-023).
        received_mono, received_at = self._receive_clock()
        if not self._burst.admit(alarm.burst_key, received_mono,
                                 pair=alarm.pair_key, phase=alarm.phase):
            return None

        # Which clock stamped this event is explicit (MNVR-024). A naive dateTime is the
        # recorder's local time: localise it with the offset the recorder states, or, when
        # it states none, use the time the alert arrived and keep the recorder's text.
        ts, clock = alarm_parsing.resolve_event_time(
            alarm.raw_time, received_at, receive_source="agent_receive",
            naive_offset=lambda: self._recorder_utc_offset(received_mono))

        return Event(
            channel=alarm.channel,
            event_type=alarm.event_type,
            device_ts=ts,
            device_event_id=None,     # ISAPI alerts carry no stable id
            payload={**alarm.payload, **clock},
        )

    def close(self) -> None:
        self.s.close()
