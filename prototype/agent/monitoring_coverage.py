#!/usr/bin/env python3
"""Agent-side monitoring coverage — truthful "was WatchLog actually watching?".

The site PC can sleep, hibernate, be shut down, or be carried away from the recorder
LAN. During any of those the Agent process is not observing the site, and WatchLog must
never later imply it was. The server already infers agent-down from missing heartbeats
(prototype/server/coverage_model.py) but cannot know the CAUSE. This module lets the
Agent detect, from its own wall clock, a stretch where it was not scheduled
(suspend / hibernate / hard stall) and report that stretch WITH a cause, so the daily
report can say "Not monitored 02:21-06:28 (site PC asleep)" rather than a bare gap.

Distinctions kept explicit (design):
  * site PC asleep / off / stalled  -> NOT MONITORED (this module; a real coverage gap).
  * cloud outage, Agent still running -> still monitored: events buffer in the spool and
    drain on reconnect. NOT a coverage gap.
  * recorder-LAN outage, Agent running -> NOT VERIFIED (health UNKNOWN), handled by the
    health cycle, not here.

Pure logic + a tiny stateful monitor; no I/O of its own (the cloud call is injected).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

# Wall-clock excess (seconds) over the expected loop period beyond which we conclude the
# process was not scheduled. Well above any NTP nudge or normal scheduling jitter.
DEFAULT_GAP_THRESHOLD = 90.0

# A wall-clock jump proves only that THIS PROCESS was not scheduled for that span (the loop
# would otherwise have ticked). That is consistent with OS sleep/hibernate, the PC being off,
# or a hard stall — the Agent cannot tell which from the clock alone, so it reports the honest
# generic cause rather than asserting "sleep". (A cloud/ISP outage does NOT freeze the process,
# so it never produces this gap — it is handled by the spool, not here.)
CAUSE_OBSERVATION_GAP = "observation_gap"
CAUSE_SUSPEND = CAUSE_OBSERVATION_GAP        # back-compat alias; do not assert OS-sleep


def detect_suspend_gap(prev_wall: float, now_wall: float,
                       loop_period: float, threshold: float) -> float:
    """Unobserved duration (s) if the wall clock advanced far more than the loop should
    have taken (process frozen: sleep/hibernate/hard stall), else 0.0. Only positive
    excess beyond `threshold` counts, so clock nudges and jitter never register a gap."""
    excess = (now_wall - prev_wall) - loop_period
    return excess if excess > threshold else 0.0


@dataclass(frozen=True)
class CoverageGap:
    started_at: float   # epoch seconds
    ended_at: float
    cause: str


class CoverageMonitor:
    """Ticked once per main-loop iteration with the current wall clock. Emits a gap when
    the process was evidently suspended, and queues it for cloud reporting (retryable)."""

    def __init__(self, loop_period: float = 1.0, threshold: float = DEFAULT_GAP_THRESHOLD):
        self.loop_period = loop_period
        self.threshold = threshold
        self.pending: list[CoverageGap] = []

    def tick(self, prev_wall: float, now_wall: float) -> "CoverageGap | None":
        gap = detect_suspend_gap(prev_wall, now_wall, self.loop_period, self.threshold)
        if gap <= 0:
            return None
        g = CoverageGap(started_at=now_wall - gap, ended_at=now_wall, cause=CAUSE_OBSERVATION_GAP)
        self.pending.append(g)
        return g

    def report_pending(self, cloud, state: dict) -> int:
        """Best-effort: push queued gaps to the cloud; keep any that fail for retry (the
        cloud may itself be unreachable right after a resume). Returns the count reported.
        Never raises."""
        if not self.pending:
            return 0
        remaining: list[CoverageGap] = []
        reported = 0
        for g in self.pending:
            try:
                cloud.call("wl_report_coverage_gap",
                           p_agent_id=state["agent_id"], p_agent_key=state["agent_key"],
                           p_started_at=datetime.fromtimestamp(g.started_at, timezone.utc).isoformat(),
                           p_ended_at=datetime.fromtimestamp(g.ended_at, timezone.utc).isoformat(),
                           p_cause=g.cause)
                reported += 1
            except Exception:                      # noqa: BLE001 — retry on the next loop
                remaining.append(g)
        self.pending = remaining
        return reported


__all__ = ["DEFAULT_GAP_THRESHOLD", "CAUSE_OBSERVATION_GAP", "CAUSE_SUSPEND",
           "detect_suspend_gap", "CoverageGap", "CoverageMonitor"]
