#!/usr/bin/env python3
"""Phase A — increment 2: server-side watchdog + monitoring-coverage MATH.

Pure logic (the SQL RPCs in 0043 mirror this; test_agent_watchdog_contract.py pins
the SQL to it). The one invariant these tests exist to protect, stated in the design
(OPERATIONAL_INTELLIGENCE_ARCHITECTURE.md §9) and by the increment-2 brief:

    If the cloud cannot observe the site, that period REDUCES MONITORING COVERAGE.
    It must NOT be counted as camera downtime, and NOT as camera uptime.

So an unverified window is subtracted from BOTH the availability denominator (monitored
time) and from any operational claim — it can never move availability up or down, only
coverage. These are red before coverage_model.py exists.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

import coverage_model as cov  # noqa: E402
from coverage_model import (  # noqa: E402
    evaluate_watchdog, coverage, verified_availability,
    merge, subtract, clip, total, WATCHDOG_THRESHOLD_SECONDS,
)

TH = WATCHDOG_THRESHOLD_SECONDS  # 180


# --- watchdog transition (open/close, idempotency, recovery) -------------------

def test_watchdog_opens_when_overdue():
    d = evaluate_watchdog(last_seen_at=0, now=TH + 1, has_open_interval=False)
    assert d.action == "open" and d.started_at == 0  # backdated to last confirmed contact


def test_watchdog_boundary_is_strict():
    # exactly at threshold is NOT yet unreachable (anti-flap on the boundary)
    d = evaluate_watchdog(last_seen_at=0, now=TH, has_open_interval=False)
    assert d.action == "none"


def test_watchdog_idempotent_while_still_unreachable():
    d = evaluate_watchdog(last_seen_at=0, now=10_000, has_open_interval=True)
    assert d.action == "none"  # already open -> no duplicate interval


def test_watchdog_closes_on_recovery():
    # heartbeat resumed: last_seen advanced to 250, now close by; close at the resumed time
    d = evaluate_watchdog(last_seen_at=250, now=260, has_open_interval=True)
    assert d.action == "close" and d.ended_at == 250


def test_watchdog_healthy_no_open_is_none():
    d = evaluate_watchdog(last_seen_at=100, now=120, has_open_interval=False)
    assert d.action == "none"


def test_watchdog_missing_last_seen_is_none():
    d = evaluate_watchdog(last_seen_at=None, now=500, has_open_interval=False)
    assert d.action == "none"


def test_watchdog_reopens_after_a_recovery():
    # open -> close -> overdue again opens a NEW interval at the new last_seen
    assert evaluate_watchdog(0, TH + 1, has_open_interval=False).action == "open"
    assert evaluate_watchdog(250, 260, has_open_interval=True).action == "close"
    reopen = evaluate_watchdog(last_seen_at=250, now=250 + TH + 1, has_open_interval=False)
    assert reopen.action == "open" and reopen.started_at == 250


# --- interval algebra ----------------------------------------------------------

def test_merge_unions_overlaps():
    assert merge([(100, 300), (200, 500)]) == [(100, 500)]


def test_merge_open_end_clips_to_supplied_bound():
    assert merge([(900, None)], open_end=1000) == [(900, 1000)]


def test_subtract_removes_the_overlap():
    assert subtract([(0, 1000)], [(200, 400)]) == [(0, 200), (400, 1000)]


def test_subtract_contained_span_splits():
    assert subtract([(0, 100)], [(40, 60)]) == [(0, 40), (60, 100)]


# --- monitoring coverage (wall vs monitored vs unverified) ---------------------

def test_coverage_full_when_no_gaps():
    c = coverage(0, 1000, [])
    assert c.wall_seconds == 1000 and c.unverified_seconds == 0
    assert c.monitored_seconds == 1000 and c.coverage_ratio == 1.0


def test_coverage_reduced_by_gap():
    c = coverage(0, 1000, [(200, 400)])
    assert c.unverified_seconds == 200 and c.monitored_seconds == 800
    assert c.coverage_ratio == 0.8


def test_coverage_open_interval_clipped_to_window():
    c = coverage(0, 1000, [(900, None)])  # still-open gap
    assert c.unverified_seconds == 100 and c.monitored_seconds == 900


def test_coverage_overlapping_gaps_not_double_counted():
    c = coverage(0, 1000, [(100, 300), (200, 500)])
    assert c.unverified_seconds == 400 and c.monitored_seconds == 600


def test_coverage_gap_outside_window_ignored():
    c = coverage(0, 1000, [(2000, 3000)])
    assert c.unverified_seconds == 0 and c.coverage_ratio == 1.0


def test_coverage_entirely_blind():
    c = coverage(0, 1000, [(0, 1000)])
    assert c.monitored_seconds == 0 and c.coverage_ratio == 0.0


# --- verified availability + THE invariant -------------------------------------

def test_availability_full_uptime():
    a = verified_availability([(0, 1000)], [], 0, 1000)
    assert a.monitored_seconds == 1000 and a.up_seconds == 1000 and a.availability_ratio == 1.0


def test_availability_offline_during_monitored_counts_as_down():
    a = verified_availability([(0, 500)], [], 0, 1000)  # down for the 2nd half, fully observed
    assert a.monitored_seconds == 1000 and a.up_seconds == 500 and a.availability_ratio == 0.5


def test_availability_entirely_unverified_is_undefined():
    a = verified_availability([(0, 1000)], [(0, 1000)], 0, 1000)
    assert a.monitored_seconds == 0 and a.availability_ratio is None  # not 0.0, not 1.0


def test_INVARIANT_unverified_reduces_coverage_not_availability():
    # operational the whole window, but the agent was unreachable for the 2nd half.
    op, unv = [(0, 1000)], [(500, 1000)]
    c = coverage(0, 1000, unv)
    a = verified_availability(op, unv, 0, 1000)
    assert c.coverage_ratio == 0.5                 # coverage drops to 50%
    assert a.availability_ratio == 1.0             # availability stays 100% over what we could see
    assert a.monitored_seconds == 500 and a.up_seconds == 500


def test_INVARIANT_downtime_during_unverified_is_not_counted_down():
    # camera looked "down" for the 2nd half, but we couldn't observe it (agent unreachable).
    op, unv = [(0, 500)], [(500, 1000)]
    a = verified_availability(op, unv, 0, 1000)
    assert a.monitored_seconds == 500 and a.up_seconds == 500
    assert a.availability_ratio == 1.0             # the unobservable "down" is excluded, not blamed


def test_availability_mixed_verified_down_and_unverified():
    # up [0,400]; monitored is [0,700] (gap [700,1000] unverified) -> down [400,700] counts.
    a = verified_availability([(0, 400)], [(700, 1000)], 0, 1000)
    assert a.monitored_seconds == 700 and a.up_seconds == 400
    assert abs(a.availability_ratio - (400 / 700)) < 1e-9


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
