"""Who owns scheduled periodic stills for a camera: the server or this Agent.

Owner decision (2026-10-06): the server owns scheduled periodic restaurant still capture; the
Agent is the bounded transport for the server's capture requests. A camera whose stills the
server is scheduling must not ALSO get the Agent's own periodic stills (two stills, two
reviews, double storage).

The current production contract does not tell the Agent which cameras the server schedules
(the analytics config carries no restaurant profile, a capture request carries no source).
It does tell it, request by request: the server's scheduler (0125/0156) keeps sending a
capture request for every scheduled camera, every 60-180 s, to Agents that advertise
``config_snapshot_requests``. So a camera the server sent a capture request for is
server-owned for SERVER_OWNED_SECONDS after that request; the Agent's own periodic samplers
skip it while it is, and resume on their own if the server stops. Cameras the server never
asks for keep the Agent's behaviour unchanged (nothing gains capture). Native recorder alarms
are not touched by any of this. An Agent that does not advertise the capability (5.0.17) never
receives scheduled requests, so it keeps its own stills exactly as before.

Keyed by (cloud recorder id, channel); a single-recorder runtime without a cloud recorder id
matches a request for its channel on any recorder of the site (it has only one).
"""
from __future__ import annotations

import threading
import time

# Longer than any restaurant interval (<= 180 s) plus a missed poll or two.
SERVER_OWNED_SECONDS = 900.0

_lock = threading.Lock()
_marks: dict[tuple[str, str], float] = {}


def _key(recorder_id, channel) -> tuple[str, str]:
    return (str(recorder_id or ""), str(channel or "").strip())


def note_requests(rows, now: float | None = None) -> int:
    """Record the server's capture requests from one poll. Returns how many were noted."""
    now = time.monotonic() if now is None else now
    noted = 0
    with _lock:
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            channel = str(row.get("channel") or "").strip()
            if not channel:
                continue
            _marks[_key(row.get("recorder_id"), channel)] = now
            noted += 1
    return noted


def owned_by_server(recorder_id, channel, now: float | None = None) -> bool:
    """True while the server is scheduling stills for this camera."""
    now = time.monotonic() if now is None else now
    channel = str(channel or "").strip()
    with _lock:
        if recorder_id:
            seen = _marks.get(_key(recorder_id, channel))
            return seen is not None and now - seen < SERVER_OWNED_SECONDS
        return any(ch == channel and now - seen < SERVER_OWNED_SECONDS
                   for (_rid, ch), seen in _marks.items())


def reset() -> None:
    with _lock:
        _marks.clear()


# --- the live recorder session, shared with server capture requests --------------------------

_live_lock = threading.Lock()
_live: dict[str, object] = {}


def _live_key(base_url) -> str:
    return str(base_url or "").rstrip("/").lower()


def register_live_driver(driver) -> None:
    """The collector's live driver for its recorder (Hikvision: one session per recorder)."""
    with _live_lock:
        _live[_live_key(getattr(driver, "base_url", ""))] = driver


def unregister_live_driver(driver) -> None:
    with _live_lock:
        key = _live_key(getattr(driver, "base_url", ""))
        if _live.get(key) is driver:
            del _live[key]


def live_driver_for(base_url):
    """The live session for this recorder address, if its driver takes stills on its own
    session (samples_in_stream); otherwise None and the caller opens its own."""
    with _live_lock:
        driver = _live.get(_live_key(base_url))
    return driver if getattr(driver, "samples_in_stream", False) else None
