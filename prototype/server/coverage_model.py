#!/usr/bin/env python3
"""Phase A — Health Foundation: server-side watchdog + monitoring-coverage math.

Pure logic, no I/O. This is the authoritative SPEC for what migration 0043's SQL
RPCs compute (wl_agent_watchdog_sweep, wl_site_monitoring_coverage); the contract
test pins the SQL to these rules. Kept in prototype/server/ because — unlike the
agent's health_model.py — this logic is DERIVED IN THE CLOUD, from heartbeat gaps.

The load-bearing invariant (design §9): a period the cloud could not observe reduces
MONITORING COVERAGE only. It is never camera downtime and never camera uptime. So an
"unverified" window is removed from the availability denominator (monitored time) AND
from any operational claim, and therefore cannot move availability in either direction.

Intervals are (start, end) numbers; end None means "still open". The server passes
epoch seconds / timestamps; the math is unit-agnostic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

# 3 missed 60 s heartbeats before we declare the cloud blind to a site (anti-flap).
WATCHDOG_THRESHOLD_SECONDS = 180

Interval = Tuple[float, float]


# --- interval algebra ----------------------------------------------------------

def _finite(intervals, open_end: float) -> List[Interval]:
    out = []
    for s, e in intervals:
        ee = open_end if e is None else e
        if ee > s:
            out.append((s, ee))
    return out


def merge(intervals, open_end: Optional[float] = None) -> List[Interval]:
    """Union of overlapping/adjacent intervals. `open_end` bounds still-open (None) ends."""
    bound = open_end if open_end is not None else float("inf")
    ivals = sorted(_finite(intervals, bound))
    merged: List[Interval] = []
    for s, e in ivals:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def clip(intervals, lo: float, hi: float) -> List[Interval]:
    """Intersect each interval with [lo, hi]; drop empties."""
    out = []
    for s, e in intervals:
        s2, e2 = max(s, lo), min(e, hi)
        if e2 > s2:
            out.append((s2, e2))
    return out


def total(intervals) -> float:
    return sum(e - s for s, e in intervals)


def subtract(a, b) -> List[Interval]:
    """a minus b (both finite). Used to strip unverified time out of operational time."""
    b = merge(b)
    out: List[Interval] = []
    for s, e in a:
        cur = s
        for bs, be in b:
            if be <= cur or bs >= e:
                continue
            if bs > cur:
                out.append((cur, min(bs, e)))
            cur = max(cur, be)
            if cur >= e:
                break
        if cur < e:
            out.append((cur, e))
    return [(s, e) for s, e in out if e > s]


# --- server-side agent watchdog ------------------------------------------------

@dataclass(frozen=True)
class WatchdogDecision:
    action: str                       # 'open' | 'close' | 'none'
    started_at: Optional[float] = None
    ended_at: Optional[float] = None


def evaluate_watchdog(last_seen_at, now, has_open_interval: bool,
                      threshold: float = WATCHDOG_THRESHOLD_SECONDS) -> WatchdogDecision:
    """Decide the unreachable-interval action for one agent. Idempotent by design.

    - overdue (now - last_seen > threshold) and no interval open -> OPEN, backdated to
      last_seen_at (everything after the last confirmed contact is unverified).
    - back within threshold and an interval is open -> CLOSE at last_seen_at (the moment
      contact resumed).
    - otherwise -> NONE. Re-running while still unreachable never opens a duplicate.
    """
    if last_seen_at is None:
        return WatchdogDecision("none")
    unreachable = (now - last_seen_at) > threshold
    if unreachable and not has_open_interval:
        return WatchdogDecision("open", started_at=last_seen_at)
    if (not unreachable) and has_open_interval:
        return WatchdogDecision("close", ended_at=last_seen_at)
    return WatchdogDecision("none")


# --- monitoring coverage -------------------------------------------------------

@dataclass(frozen=True)
class Coverage:
    wall_seconds: float
    unverified_seconds: float
    monitored_seconds: float
    coverage_ratio: float             # monitored / wall, in [0, 1]; wall == 0 -> 1.0


def coverage(window_lo, window_hi, unverified_intervals) -> Coverage:
    wall = max(0.0, window_hi - window_lo)
    unv = total(clip(merge(unverified_intervals, open_end=window_hi), window_lo, window_hi))
    monitored = max(0.0, wall - unv)
    ratio = 1.0 if wall <= 0 else monitored / wall
    return Coverage(wall, unv, monitored, ratio)


# --- verified availability (the invariant lives here) --------------------------

@dataclass(frozen=True)
class Availability:
    monitored_seconds: float
    up_seconds: float
    availability_ratio: Optional[float]   # None when monitored == 0 (undefined, not 0/1)


def verified_availability(operational_intervals, unverified_intervals,
                          window_lo, window_hi) -> Availability:
    """Availability over MONITORED time only. Unverified windows are excluded from the
    denominator and from uptime, so they never count as up or down — only against coverage."""
    unv = merge(clip(merge(unverified_intervals, open_end=window_hi), window_lo, window_hi))
    wall = max(0.0, window_hi - window_lo)
    monitored = max(0.0, wall - total(unv))
    op = clip(merge(operational_intervals, open_end=window_hi), window_lo, window_hi)
    up = total(subtract(op, unv))     # operational AND monitored (never operational-but-blind)
    ratio = None if monitored <= 0 else min(1.0, up / monitored)
    return Availability(monitored, up, ratio)


__all__ = [
    "WATCHDOG_THRESHOLD_SECONDS", "WatchdogDecision", "evaluate_watchdog",
    "Coverage", "coverage", "Availability", "verified_availability",
    "merge", "clip", "total", "subtract",
]
