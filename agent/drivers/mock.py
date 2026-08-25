"""
Mock NVR driver — the test fixture.

Talks to mock_nvr/mock_nvr.py. This is the only driver that has actually
been exercised end to end, which is why every gate in the README was run
through it. It polls rather than streams, so it also keeps the polling
code path alive: some budget devices offer nothing better.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Iterator

import requests

from .base import Channel, DeviceInfo, DriverError, Event, NvrDriver

POLL_SECONDS = 10
PAGE_SIZE = 200
MAX_PAGES_PER_CYCLE = 10
# Re-poll a window already seen. The server-side dedupe absorbs it, and it
# closes any gap left by a restart.
OVERLAP_SECONDS = 120
FIRST_RUN_LOOKBACK_SECONDS = 3600


def _parse(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class MockDriver(NvrDriver):
    name = "mock"
    verified_against_hardware = False   # by definition — it is not hardware

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.s = requests.Session()
        self.cursor: datetime | None = None

    def _get(self, path: str, **params) -> dict:
        try:
            r = self.s.get(self.base_url + path, params=params,
                           timeout=self.timeout)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            raise DriverError(f"{self.base_url}{path}: {e}") from e

    def probe(self) -> DeviceInfo:
        d = self._get("/api/device-info")
        if "deviceName" not in d:
            raise DriverError("not a WatchLog mock NVR")
        return DeviceInfo(
            vendor="mock", model=d.get("model"),
            firmware=d.get("firmwareVersion"), serial=d.get("serialNumber"),
            channel_count=d.get("channelCount"), driver=self.name, raw=d)

    def list_channels(self) -> list[Channel]:
        return [Channel(channel=str(c["channel"]), name=c.get("name"),
                        enabled=c.get("enabled", True))
                for c in self._get("/api/cameras").get("channels", [])]

    def get_snapshot(self, channel: str) -> bytes | None:
        try:
            r = self.s.get(self.base_url + "/api/snapshot",
                           params={"channel": str(channel)}, timeout=10)
        except requests.RequestException:
            return None
        if r.status_code == 200 and r.content[:2] == bytes([0xFF, 0xD8]):
            return r.content
        return None

    def stream_events(self, stop: threading.Event) -> Iterator[Event]:
        while not stop.is_set():
            # Drain the backlog by paging forward. A device that returns
            # the NEWEST n instead of the OLDEST n from `since` would make
            # a single-page read skip the middle of any long backlog.
            for _ in range(MAX_PAGES_PER_CYCLE):
                if stop.is_set():
                    return
                since = (self.cursor - timedelta(seconds=OVERLAP_SECONDS)
                         if self.cursor else
                         datetime.now(timezone.utc)
                         - timedelta(seconds=FIRST_RUN_LOOKBACK_SECONDS))
                batch = self._get("/api/events", since=_iso(since),
                                  limit=PAGE_SIZE).get("events", [])
                if not batch:
                    break

                newest = self.cursor
                for e in batch:
                    ts = _parse(e["timestamp"])
                    newest = ts if newest is None else max(newest, ts)
                    yield Event(
                        channel=str(e.get("channel", "1")),
                        event_type=e.get("eventType", "unknown"),
                        device_ts=ts,
                        device_event_id=e.get("eventId"),
                        payload=e)

                progressed = newest is not None and newest != self.cursor
                self.cursor = newest
                if not progressed or len(batch) < PAGE_SIZE:
                    break

            stop.wait(POLL_SECONDS)

    def close(self) -> None:
        self.s.close()
