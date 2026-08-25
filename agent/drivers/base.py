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
from dataclasses import dataclass, field
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

    def to_json(self, agent_ts: datetime) -> dict:
        return {
            "channel": str(self.channel),
            "event_type": self.event_type,
            "device_event_id": self.device_event_id,
            "device_ts": _iso(self.device_ts),
            "agent_ts": _iso(agent_ts),
            "payload": self.payload,
        }


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class DriverError(RuntimeError):
    """Device unreachable, refused credentials, or spoke an unexpected dialect."""


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

    def close(self) -> None:
        pass

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.base_url}>"
