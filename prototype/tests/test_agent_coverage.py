#!/usr/bin/env python3
"""Agent-side monitoring-coverage: suspend detection + truthful reporting (H3).

Pins that the Agent recognises a stretch where the site PC was not scheduling it
(sleep/hibernate/stall) from its own wall clock, records that as a coverage gap with a
cause, and reports it best-effort (retrying while the cloud is unreachable) — so WatchLog
never claims it observed the site while the machine was asleep.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from monitoring_coverage import (CoverageMonitor, CoverageGap, detect_suspend_gap,  # noqa: E402
                                 CAUSE_SUSPEND, DEFAULT_GAP_THRESHOLD)


def test_normal_iteration_is_no_gap():
    assert detect_suspend_gap(1000.0, 1001.0, 1.0, DEFAULT_GAP_THRESHOLD) == 0.0


def test_small_jump_below_threshold_is_no_gap():
    # a 30 s hiccup (GC pause, slow disk) is not a suspend
    assert detect_suspend_gap(1000.0, 1030.0, 1.0, DEFAULT_GAP_THRESHOLD) == 0.0


def test_long_freeze_is_a_gap():
    gap = detect_suspend_gap(1000.0, 1000.0 + 3600.0, 1.0, DEFAULT_GAP_THRESHOLD)
    assert abs(gap - 3599.0) < 0.001   # ~1 hour unobserved (minus the 1 s the loop owed)


def test_monitor_emits_gap_with_cause_and_span():
    mon = CoverageMonitor(loop_period=1.0, threshold=90.0)
    assert mon.tick(1000.0, 1001.0) is None            # normal
    g = mon.tick(1001.0, 1001.0 + 7200.0)              # 2 h suspend
    assert isinstance(g, CoverageGap) and g.cause == CAUSE_SUSPEND
    assert abs((g.ended_at - g.started_at) - 7199.0) < 0.001
    assert len(mon.pending) == 1


class _FakeCloud:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def call(self, fn, **kw):
        if self.fail:
            raise RuntimeError("cloud unreachable")
        self.calls.append((fn, kw))
        return {"ok": True}


def test_report_pending_sends_and_clears():
    mon = CoverageMonitor()
    mon.tick(0.0, 0.0 + 3600.0)
    cloud = _FakeCloud()
    n = mon.report_pending(cloud, {"agent_id": "a", "agent_key": "k"})
    assert n == 1 and mon.pending == []
    fn, kw = cloud.calls[0]
    assert fn == "wl_report_coverage_gap"
    assert kw["p_cause"] == CAUSE_SUSPEND
    assert kw["p_started_at"].endswith("+00:00") and kw["p_ended_at"].endswith("+00:00")


def test_report_pending_retries_while_cloud_down():
    mon = CoverageMonitor()
    mon.tick(0.0, 0.0 + 3600.0)
    down = _FakeCloud(fail=True)
    assert mon.report_pending(down, {"agent_id": "a", "agent_key": "k"}) == 0
    assert len(mon.pending) == 1                        # kept for retry, not lost
    up = _FakeCloud()
    assert mon.report_pending(up, {"agent_id": "a", "agent_key": "k"}) == 1
    assert mon.pending == []


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
