"""
WatchLog — NVR driver interface.

Every driver is outbound-only: it opens connections to the NVR on the
local LAN and to Supabase over HTTPS, and never listens on a port. That
is the whole architectural point — nothing about a driver may require an
inbound connection, a port forward or a VPN.

A driver implements three things:

    probe()          — is this device mine? what is it?
    list_channels()  — which cameras exist
    stream_events()  — a long-lived generator yielding Event objects

stream_events() is a generator rather than a poll on purpose. Hikvision
(alertStream), Dahua (eventManager attach) and ONVIF (PullPoint) all
natively push over a client-initiated HTTP connection, which is both
lower latency and lighter on the device than repeated polling. A driver
whose device only supports polling simply polls inside the generator.

Events go into a local spool first and are drained to Supabase by the
agent, so a dropped internet link buffers instead of losing data.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Iterator


@dataclass(frozen=True)
class DeviceInfo:
    vendor: str
    model: str | None = None
    firmware: str | None = None
    serial: str | None = None
    channel_count: int | None = None
    device_time: datetime | None = None
    driver: str = ""
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Channel:
    channel: str
    name: str | None = None
    enabled: bool = True


@dataclass(frozen=True)
class Event:
    """
    One thing the device says happened.

    device_event_id is whatever stable identifier the device offers. Most
    devices offer none, in which case the server falls back to
    site+channel+timestamp+type — the dedupe key is computed in
    wl_ingest_events(), never here, so a stale agent build cannot weaken
    it.
    """
    channel: str
    event_type: str
    device_ts: datetime
    device_event_id: str | None = None
    payload: dict = field(default_factory=dict)
    # base64 JPEG, attached by the agent after the driver yields the
    # event. Kept off the driver so a slow or broken camera snapshot can
    # never delay or lose the event itself.
    snapshot_b64: str | None = None

    def to_json(self, agent_ts: datetime) -> dict:
        out = {
            "channel": str(self.channel),
            "event_type": self.event_type,
            "device_event_id": self.device_event_id,
            "device_ts": _iso(self.device_ts),
            "agent_ts": _iso(agent_ts),
            "payload": self.payload,
        }
        if self.snapshot_b64:
            out["snapshot_b64"] = self.snapshot_b64
        return out

    def with_snapshot(self, b64: str | None) -> "Event":
        return replace(self, snapshot_b64=b64) if b64 else self


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class DriverError(RuntimeError):
    """Device unreachable, refused credentials, or spoke an unexpected dialect."""


class NvrUnreachable(DriverError):
    """No usable response from the recorder at all — wrong address, offline, or a
    firewall in between. The recorder LAYER is down; we can make no claim about the
    cameras behind it (they become UNKNOWN, never OFFLINE)."""


class NvrAuthFailed(DriverError):
    """The recorder answered but rejected our credentials (HTTP 401/403). Distinct from
    unreachable: the box is there, the username/password is wrong — so this must read as
    a recorder-auth fault, never as a camera being offline or 'cameras could not be added'."""


def explain(e: Exception) -> str:
    """
    Turn a requests/urllib3 exception into something a person can act on.

    This distinction is the whole diagnosis in the field and the raw
    message buries it under a wall of connection-pool detail:

      refused  -> the host IS there, nothing is listening on that port.
                  Almost always the web interface is on another port.
      timeout  -> nothing answered at all. Wrong address, wrong subnet,
                  or a firewall in between.

    Those have completely different fixes, so they must not both read as
    "Max retries exceeded".
    """
    low = str(e).lower()
    if "refused" in low:
        return ("connection refused - something is at that address but "
                "nothing is listening on this port")
    if "no route to host" in low or "unreachable" in low:
        return ("no route to host - this PC cannot reach that network at "
                "all (different subnet?)")
    if "timed out" in low or "timeout" in low:
        return ("timed out - no reply at all (wrong address, different "
                "network, or a firewall)")
    if "getaddrinfo" in low or "name or service not known" in low             or "name resolution" in low:
        return "hostname could not be resolved"
    if "certificate" in low or "ssl" in low:
        return "TLS/SSL rejected - try http:// instead of https://"
    if "connection reset" in low:
        return "connection reset by the device"
    return str(e).splitlines()[0][:160]


class NvrDriver:
    """Base class. Subclasses must set `name` and implement the three methods."""

    name: str = "base"
    # Honest metadata, surfaced by `--probe` and stored on the agent row.
    # Flip to True only once a driver has run against real hardware.
    verified_against_hardware: bool = False

    def __init__(self, base_url: str, username: str = "", password: str = "",
                 timeout: int = 15) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout

    def probe(self) -> DeviceInfo:
        raise NotImplementedError

    def list_channels(self) -> list[Channel]:
        raise NotImplementedError

    def stream_events(self, stop: threading.Event) -> Iterator[Event]:
        raise NotImplementedError

    def capabilities(self) -> dict:
        """
        What analytics this recorder supports, and which are already on.

        Read-only. This never changes a setting on the customer's device -
        it reports what is there so the portal can show "your recorder
        supports motion, human/vehicle, line-crossing..." and mark what is
        already active. Enabling anything is a separate, opt-in, reversible
        step.

        Shape:
          {"channels": [
             {"channel": "1", "name": "Main Gate", "analytics": [
                {"key": "motion", "label": "Motion", "supported": true,
                 "active": true, "geometry": false},
                {"key": "human_vehicle", ..., "geometry": false},
                {"key": "line_crossing", ..., "geometry": true},
                ...]}]}

        `geometry: true` marks analytics that need a human to place a line
        or zone on the scene - the portal guides the user for those rather
        than pretending it can auto-configure them.

        Best-effort and FAIL SAFE: an endpoint that is missing or errors
        leaves that analytic 'unknown', never crashes the probe. Returns an
        empty channel list if the device exposes nothing we understand.
        """
        return {"channels": []}

    def get_snapshot(self, channel: str) -> bytes | None:
        """
        A still JPEG from `channel`, right now.

        This is what makes an incident report readable: a line saying
        "motion, Loading Bay, 02:14" is nearly useless on its own, and a
        still costs ~150 KB against a site uplink that a video clip would
        saturate. Return None if the device cannot produce one - a
        missing image must never cost us the event itself.
        """
        return None

    def get_clip(self, channel: str, start: datetime, end: datetime) -> bytes | None:
        """
        Recorded video covering an incident window.

        Deliberately NOT called automatically. Clips run 5-50 MB; pulling
        one per event would swamp a site's connection and the storage
        budget. This exists to be driven on demand by an operator asking
        for one specific incident.

        Unimplemented on every driver until it can be tested against real
        hardware - the playback and download APIs are the least
        consistent part of both vendors' interfaces, and guessing at them
        would produce code that looks finished and silently returns
        corrupt files.
        """
        return None

    def storage_status(self) -> dict:
        """Recorder HDD/storage health, read-only, from the vendor storage API.

        Returns {'supported': bool, 'state': 'ok'|'degraded'|'fault'|None}. A driver that cannot read
        storage MUST report supported=False / state=None — it must never fabricate 'ok'. "We don't
        know" is the honest answer and becomes UNKNOWN upstream (a JPEG proves an image, not that the
        NVR is recording it). Low space is a DEGRADED signal, not a blanket 'fault'.
        """
        return {"supported": False, "state": None}

    def recording_status(self, channels) -> dict:
        """Per-channel recording state from the vendor RECORD config, read-only.

        Returns {'supported': bool, 'channels': {channel: 'recording'|'not_recording'|None}}.
        Unread/unsupported channels are None -> UNKNOWN upstream. Recording is NEVER inferred from
        a snapshot or from the camera being reachable.
        """
        return {"supported": False, "channels": {}}

    def current_faults(self) -> dict:
        """Current, PRESENT-TENSE recorder fault state, read-only, from an active vendor API.

        Distinct from stream_events(), which only fires on a TRANSITION: this answers "which
        channels are in video loss / tamper RIGHT NOW", so an outage that began before the agent
        started (or before a reconnect/resume) is still seen without waiting for a fresh event.

        Returns {'supported': bool, 'video_loss': [channel...], 'video_blind': [channel...]}.
        A driver that cannot query current state MUST report supported=False and empty lists — it
        must never fabricate "no faults", because that would turn an unverifiable camera green.
        Channels are 1-based strings, matching list_channels().
        """
        return {"supported": False, "video_loss": [], "video_blind": []}

    def close(self) -> None:
        pass

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.base_url}>"
