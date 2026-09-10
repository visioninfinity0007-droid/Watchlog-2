#!/usr/bin/env python3
"""Historical backfill runtime — recovered intelligence, vendor-neutral (item 6).

Drives any driver's historical interface (drivers/base.py) to pull RECORDED events over a
BOUNDED time range, in cursored pages, and hand each new event to a sink (normally the spool).

Hard rules:
  * Recovered events are second-class. Every one is tagged source='recorder_archive',
    recovered=True, and a human-readable "Recovered from recorder archive" provenance line.
    Nothing here may ever be treated as a LIVE observation.
  * Duplicate-safe: a `seen` set of dedupe keys makes reprocessing (and restart) idempotent —
    the same archive window can be re-run without re-emitting events already recovered.
  * Bounded: an open-ended range is split into windows so one call can never ask a recorder for
    a month in a single request.
  * Honest status: if the driver does not support (or is unsure about) archive enumeration, we
    return that status verbatim and recover nothing. We never fabricate support.

Pure orchestration; the driver + sink are injected, so it is fully testable with a reference
driver and no hardware.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

SUPPORTED, UNSUPPORTED, UNKNOWN = "supported", "unsupported", "unknown"
RECOVERED_SOURCE = "recorder_archive"
PROVENANCE_LINE = "Recovered from recorder archive"


def _as_dt(v) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def _windows(start, end, window_seconds):
    start, end = _as_dt(start), _as_dt(end)
    step = timedelta(seconds=max(60, int(window_seconds)))
    cur = start
    while cur < end:
        nxt = min(cur + step, end)
        yield cur, nxt
        cur = nxt


def _dedupe_key(channel, raw) -> str:
    did = raw.get("device_event_id")
    if did:
        return f"dev:{did}"
    return f"{channel}:{raw.get('ts')}:{raw.get('type')}"


def backfill_events(driver, channel, start, end, *, window_seconds=3600, page_limit=500,
                    seen=None, on_event=None, max_events=None) -> dict:
    """Recover recorded events for `channel` over [start, end).

    seen:      a set of dedupe keys (pass the SAME set across restarts to stay idempotent).
    on_event:  called with each NEW recovered event dict (e.g. spool.add).
    Returns a summary {status, recovered, duplicates, windows, pages, provenance, bounded_range}.
    """
    cap = (driver.historical_capability() or {}).get("events", UNKNOWN)
    if cap != SUPPORTED:
        # Honest: report the driver's own status, recover nothing, fabricate nothing.
        return {"status": cap, "recovered": 0, "duplicates": 0, "windows": 0, "pages": 0,
                "provenance": RECOVERED_SOURCE, "reason": f"archive events {cap} on this recorder"}
    seen = seen if seen is not None else set()
    recovered = duplicates = windows = pages = 0
    for w_start, w_end in _windows(start, end, window_seconds):
        windows += 1
        cursor = None
        while True:
            res = driver.enumerate_historical_events(channel, w_start, w_end, cursor=cursor, limit=page_limit) or {}
            if res.get("status") != SUPPORTED:
                return {"status": res.get("status", UNKNOWN), "recovered": recovered,
                        "duplicates": duplicates, "windows": windows, "pages": pages,
                        "provenance": RECOVERED_SOURCE, "reason": "driver changed status mid-scan"}
            pages += 1
            for raw in res.get("events", []) or []:
                key = _dedupe_key(channel, raw)
                if key in seen:
                    duplicates += 1
                    continue
                seen.add(key)
                ev = dict(raw)
                ev.update(channel=str(channel), source=RECOVERED_SOURCE, recovered=True,
                          provenance=PROVENANCE_LINE)
                recovered += 1
                if on_event:
                    on_event(ev)
                if max_events is not None and recovered >= max_events:
                    return {"status": SUPPORTED, "recovered": recovered, "duplicates": duplicates,
                            "windows": windows, "pages": pages, "provenance": RECOVERED_SOURCE,
                            "stopped_at_limit": True}
            cursor = res.get("next_cursor")
            if not cursor:
                break
    return {"status": SUPPORTED, "recovered": recovered, "duplicates": duplicates,
            "windows": windows, "pages": pages, "provenance": RECOVERED_SOURCE}


class ReferenceArchiveDriver:
    """A deterministic, hardware-free reference implementation of the historical interface.

    Holds a fixed list of recorded events and serves them in cursored pages of `page_size`,
    honoring the requested [start, end) window. Used to prove the backfill runtime end-to-end
    (bounded retrieval, pagination, dedup, restart, provenance) without any recorder.
    """
    def __init__(self, events, page_size=2, events_status=SUPPORTED):
        # events: list of {ts, type, device_event_id?}
        self._events = sorted(events, key=lambda e: str(e["ts"]))
        self._page = int(page_size)
        self._status = events_status

    def historical_capability(self) -> dict:
        return {"events": self._status, "snapshots": UNSUPPORTED, "segments": UNSUPPORTED}

    def enumerate_historical_events(self, channel, start, end, cursor=None, limit=500) -> dict:
        if self._status != SUPPORTED:
            return {"status": self._status, "events": [], "next_cursor": None}
        s, e = _as_dt(start), _as_dt(end)
        in_win = [ev for ev in self._events if s <= _as_dt(ev["ts"]) < e]
        offset = int(cursor) if cursor else 0
        page = in_win[offset:offset + self._page]
        nxt = str(offset + self._page) if offset + self._page < len(in_win) else None
        return {"status": SUPPORTED, "events": page, "next_cursor": nxt}


__all__ = ["backfill_events", "ReferenceArchiveDriver", "SUPPORTED", "UNSUPPORTED", "UNKNOWN",
           "RECOVERED_SOURCE", "PROVENANCE_LINE"]
