"""Recorder restart detection (5.1.2).

A recorder that reboots drops its event stream and loses whatever it was doing, and it
says nothing about it on the stream itself. The collector reads the recorder's uptime
each time it (re)opens the event stream, i.e. after every stream drop, and this module
compares it with the previous read.

Only POSITIVE evidence is reported: the uptime went down between two reads, which can
only happen if the recorder booted in between. No previous read (the Agent just
started), an unreadable uptime, or an uptime that grew are never a restart: unknown
stays unknown. The event is recorder-scoped (channel None) and timed at the estimated
boot (the time of the read minus the uptime), with the reads kept as evidence.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from drivers.base import Event

EVENT_TYPE = "recorder_restart"

# An uptime this much lower than the last read is a reboot. The slack absorbs a recorder
# that reports uptime rounded to whole seconds or minutes; a real reboot resets it to near 0.
DECREASE_SLACK_SECONDS = 5.0


class UptimeWatch:
    """Per-recorder uptime memory across driver re-opens. Thread-safe."""

    def __init__(self) -> None:
        self._last: float | None = None
        self._lock = threading.Lock()

    def observe(self, uptime: float | None, at: datetime | None = None) -> Event | None:
        """Fold one uptime read; return a recorder_restart Event on positive evidence."""
        if uptime is None:
            return None
        try:
            uptime = float(uptime)
        except (TypeError, ValueError):
            return None
        if uptime < 0:
            return None
        with self._lock:
            previous, self._last = self._last, uptime
        if previous is None or uptime + DECREASE_SLACK_SECONDS >= previous:
            return None
        at = at or datetime.now(timezone.utc)
        return Event(
            channel=None,
            event_type=EVENT_TYPE,
            device_ts=at - timedelta(seconds=uptime),
            device_event_id=None,
            payload={
                "recorder_scoped": True,
                "source": "uptime_probe",
                "evidence": "uptime_decreased",
                "previous_uptime_seconds": int(previous),
                "uptime_seconds": int(uptime),
                "detected_at": at.astimezone(timezone.utc).isoformat(),
                "clock_source": "agent_receive_minus_uptime",
            },
        )


def check(driver, watch: UptimeWatch, at: datetime | None = None) -> Event | None:
    """Read ``driver``'s uptime and fold it into ``watch``. Never raises."""
    reader = getattr(driver, "uptime_seconds", None)
    if reader is None:
        return None
    try:
        uptime = reader()
    except Exception:  # noqa: BLE001 - an unreadable uptime is no evidence of anything
        return None
    ev = watch.observe(uptime, at)
    if ev is not None:
        vendor = getattr(driver, "name", None)
        if vendor:
            ev.payload["driver"] = vendor
    return ev


__all__ = ["EVENT_TYPE", "UptimeWatch", "check"]
